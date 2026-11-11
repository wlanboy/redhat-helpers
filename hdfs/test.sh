#!/usr/bin/env bash
#
# test.sh
#
# Legt in HDFS Testdaten für die Auswertung von ACL-Metadaten an:
#   - $BASE_DIR/account1..10, darunter je container1..52, unter container1,
#     container5 und container10 je ein Unterordner sub (dritte Ebene)
#   - $BASE_DIR/invalid mit fehlerhaften ACLs
#   - setzt das xattr $ACL_ATTR nach festem Schema (siehe unten) per
#     "hdfs dfs -setfattr", parallel über xargs
#   - schreibt die Vergleichsdateien nach $OUT_DIR:
#       acls.csv      pfad;acl            gesetzter Wert, leer = kein Attribut
#       expected.csv  pfad;clientid;recht
#                     Rechte je @clientid aus der ACL des Pfads, nichts
#                     Geerbtes; Pfade ohne ACL oder ohne @clientid fehlen
#   - liest am Ende alle Werte per "getfattr -R" zurück und vergleicht sie
#     mit acls.csv
#   - mit --docker: startet vorher einen frischen NameNode per "docker run"
#     (ohne DataNodes, es geht nur um Metadaten), testet danach hdfs-acl
#     durchgängig (fsimage, --live, Fehlerfälle, remove/add/restore) und
#     löscht den Container am Ende wieder
#
# ACL-Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
#   besitzer/eintrag: @clientid, user (ohne Präfix) oder Gruppe ('.' und
#   voller DN, z. B. .cn=ops,ou=groups,dc=firma=3);
#   LDAP-Teile ohne '.' (cn=..., ou=..., dc=..., sn=...) werden ignoriert
#   recht: Bitmaske, genutzt werden 1 = lesen, 3 = lesen+schreiben,
#          19 = verwalten
#   Der Besitzer hat immer mindestens 19, mehrere Einträge einer clientid
#   werden verodert. Ausgewertet werden nur @clientids.
#   Ausgewertet wird nur die ACL am Pfad selbst. Wie die Anwendung Account
#   und Container zusammenführt (erweitern/einschränken, leerer Besitzer
#   übernimmt den des Accounts), rechnet hdfs-acl nicht nach.
#
# Schema (n = Account-Nummer, cl(k) = @client<k>, k zyklisch über 1..10,
# OPS/ADMINS/READERS = Gruppen .cn=<name>,ou=groups,dc=firma):
#   account n:   cl(n)|cl(n+1)=3,svc_backup=1,cl(n+2)=1,OPS=3
#                account3 Besitzer "hans", account7 Besitzer ADMINS,
#                account10 ohne ACL
#   container c: c%5==0  ohne ACL
#                c%5==1  cl(n)|cl(n+3)=3,dev_user=3,cl(n+4)=1
#                c%5==2  cl(n+5)|READERS=1,cl(n+5)=1,cl(n)=1,cl(n+1)=1
#                        (Besitzer zusätzlich mit =1 gelistet, bleibt 19;
#                        cl(n+1) schränkt die Account-ACL ein)
#                c%5==3  maria|cl(n+6)=3,cl(n+7)=1,OPS=1
#                c%5==4  ADMINS|peter=3,OPS=1  (keine @clientid -> keine Rechte)
#                c==48   cl(n+9)|cl(n+1)=5,OPS=16,cl(n+1)=16,cl(n+9)=8,
#                                cn=doc,ou=hell,dc=firma=3,cn=testgroup
#                        (Masken: cl(n+1) 5|16=21, Besitzer 19|8=27;
#                        LDAP-Teile ohne '.' ohne Warnung ignoriert)
#                c==49   cl(n+8)|  (nur Besitzer, keine Einträge)
#                c==50   |cl(n+3)=1,cl(n+2)=3  (leerer Besitzer)
#                c==51   cl(n+1)|cl(n)=1,OPS=1  (Migration cl(n) -> cl(n+1):
#                        NEU ist schon Besitzer, ALT nur Eintrag)
#                c==52   cl(n)|cl(n+1)=1  (ALT Besitzer, NEU schon Eintrag)
#   sub (dritte Ebene, container<c>/sub):
#                c==1, c==5  ohne ACL
#                c==10   cl(n+2)|cl(n+3)=1,OPS=3  (ACL in dritter Ebene)
#   invalid/<name> (fehlerhafte ACLs, Rechte von Hand festgelegt, siehe
#   acl_invalid): kein '|', leerer Besitzer, ungültige Einträge, Recht 0
#   oder > 2^31-1, Leerzeichen, weitere LDAP-Teile, Gruppe ohne Recht;
#   invalid/noowner/sub ohne ACL
#   Fehlerhafte Teile geben keine Rechte, der Rest der ACL gilt.
#
# Optionen:
#   --clean        $BASE_DIR vorher komplett löschen (-skipTrash)
#   --dry-run      nur Vergleichsdateien schreiben, HDFS nicht anfassen
#   --verify-only  nichts anlegen, nur HDFS mit acls.csv vergleichen
#   --docker       frischen NameNode per Docker starten, hdfs-acl testen,
#                  Container danach löschen (HADOOP_CONF_DIR wird ignoriert)
#   --keep         mit --docker: Container und Arbeitsordner nicht löschen
#
# Umgebungsvariablen:
#   BASE_DIR      Basisordner in HDFS (Default /fs)
#   ACL_ATTR      Name des xattr (Default user.acl)
#   PARALLEL      parallele setfattr-Aufrufe (Default 8)
#   OUT_DIR       Ziel der Vergleichsdateien (Default <skriptordner>/testdata)
#   (hdfs-acl läuft hier mit DEPTH=3, wegen der Unterordner sub)
#   HADOOP_REPO   Checkout des Hadoop-Clusters (Default ~/git/hadoop)
#   HADOOP_VERSION   Version des Hadoop-Clients (Default 3.4.2)
#   HADOOP_HOME   Hadoop-Client (Default $HADOOP_REPO/downloads/hadoop-$HADOOP_VERSION)
#   HADOOP_CONF_DIR  Client-Konfiguration (Default $HADOOP_REPO/clientconfig)
#   HDFS_JAVA_HOME   Java 11/17 für den Client. Ohne Angabe wird gesucht in
#                    $HADOOP_REPO/downloads/jdk-11, sdkman (11*, 17*),
#                    /usr/lib/jvm/{jre,java}-{11,17}* (dnf), $JAVA_HOME.
#                    Java >= 23 geht nicht (Subject.getSubject entfernt).
#   DOCKER_IMAGE     Image für --docker (Default apache/hadoop:$HADOOP_VERSION)
#                    Konfiguration des NameNodes: namenode/ neben diesem Skript
#                    (localhost:19000, Web-UI localhost:19870)

set -euo pipefail

readonly ACCOUNTS=10
readonly CONTAINERS=52
# Container, die einen Unterordner sub haben (siehe acl_sub)
readonly SUB_CONTAINERS=(1 5 10)
readonly INVALID_NAMES=(nopipe noowner badentry range spaces ldap group)
# Ebenen unter BASE_DIR: account/container/sub
readonly TEST_DEPTH=3
readonly CLIENTS=10
# Besitzer-Recht (owner in permissions.json)
readonly OWNER_PERM=19
readonly OPS=.cn=ops,ou=groups,dc=firma
readonly ADMINS=.cn=admins,ou=groups,dc=firma
readonly READERS=.cn=readers,ou=groups,dc=firma

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

BASE_DIR=${BASE_DIR:-/fs}
BASE_DIR=${BASE_DIR%/}
ACL_ATTR=${ACL_ATTR:-user.acl}
PARALLEL=${PARALLEL:-8}
OUT_DIR=${OUT_DIR:-$SCRIPT_DIR/testdata}

ACLS_CSV=$OUT_DIR/acls.csv
EXPECTED_CSV=$OUT_DIR/expected.csv

CLEAN=0
DRY_RUN=0
VERIFY_ONLY=0
DOCKER=0
KEEP=0

usage() {
    sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case $arg in
        --clean) CLEAN=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --verify-only) VERIFY_ONLY=1 ;;
        --docker) DOCKER=1 ;;
        --keep) KEEP=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Fehler: Unbekannte Option '$arg' (siehe --help)." >&2; exit 1 ;;
    esac
done

if (( DOCKER && (DRY_RUN || VERIFY_ONLY) )); then
    echo "Fehler: --docker geht nicht mit --dry-run oder --verify-only." >&2
    exit 1
fi

if [[ $BASE_DIR != /?* ]]; then
    echo "Fehler: BASE_DIR muss ein absoluter Pfad unterhalb von / sein." >&2
    exit 1
fi

if [[ $ACL_ATTR != ?*.?* ]]; then
    echo "Fehler: ACL_ATTR braucht einen Namespace, z. B. user.acl." >&2
    exit 1
fi

# --- Schema -------------------------------------------------------------------

cl() {
    echo "@client$(( ($1 - 1) % CLIENTS + 1 ))"
}

# Setzen OWNER/ENTRIES; HAS_ACL=0 heißt: kein Attribut am Pfad.
acl_account() {
    local n=$1
    OWNER=""
    ENTRIES=()
    HAS_ACL=1
    if (( n == 10 )); then
        HAS_ACL=0
        return
    fi
    case $n in
        3) OWNER=hans ;;
        7) OWNER=$ADMINS ;;
        *) OWNER=$(cl "$n") ;;
    esac
    ENTRIES=("$(cl $((n + 1)))=3" "svc_backup=1" "$(cl $((n + 2)))=1" "$OPS=3")
}

acl_container() {
    local n=$1 c=$2
    OWNER=""
    ENTRIES=()
    HAS_ACL=1
    if (( c == 48 )); then
        OWNER=$(cl $((n + 9)))
        ENTRIES=("$(cl $((n + 1)))=5" "$OPS=16" "$(cl $((n + 1)))=16" "$(cl $((n + 9)))=8"
                 "cn=doc" "ou=hell" "dc=firma=3" "cn=testgroup")
        return
    fi
    if (( c == 49 )); then
        OWNER=$(cl $((n + 8)))
        return
    fi
    if (( c == 50 )); then
        ENTRIES=("$(cl $((n + 3)))=1" "$(cl $((n + 2)))=3")
        return
    fi
    if (( c == 51 )); then
        OWNER=$(cl $((n + 1)))
        ENTRIES=("$(cl "$n")=1" "$OPS=1")
        return
    fi
    if (( c == 52 )); then
        OWNER=$(cl "$n")
        ENTRIES=("$(cl $((n + 1)))=1")
        return
    fi
    case $(( c % 5 )) in
        0) HAS_ACL=0 ;;
        1) OWNER=$(cl "$n")
           ENTRIES=("$(cl $((n + 3)))=3" "dev_user=3" "$(cl $((n + 4)))=1") ;;
        2) OWNER=$(cl $((n + 5)))
           ENTRIES=("$READERS=1" "$(cl $((n + 5)))=1" "$(cl "$n")=1" "$(cl $((n + 1)))=1") ;;
        3) OWNER=maria
           ENTRIES=("$(cl $((n + 6)))=3" "$(cl $((n + 7)))=1" "$OPS=1") ;;
        4) OWNER=$ADMINS
           ENTRIES=("peter=3" "$OPS=1") ;;
    esac
}

acl_sub() {
    local n=$1 c=$2
    OWNER=""
    ENTRIES=()
    HAS_ACL=0
    if (( c == 10 )); then
        HAS_ACL=1
        OWNER=$(cl $((n + 2)))
        ENTRIES=("$(cl $((n + 3)))=1" "$OPS=3")
    fi
}

# acl_invalid <name>: RAW = gesetzter Wert, RIGHTS = erwartete Rechte als
# Zeilen "<clientid>;<recht>". Von Hand statt über grants(), denn hier wird
# gerade das Zerlegen geprüft. Ungültige Teile geben keine Rechte.
acl_invalid() {
    case $1 in
        nopipe)   RAW='@inv1=3'                    # Besitzer "@inv1=3" ist keine clientid
                  RIGHTS='' ;;
        noowner)  RAW="|@inv2=3,$OPS=1"            # leerer Besitzer
                  RIGHTS='@inv2;3' ;;
        badentry) RAW='@inv3|@inv4,@inv5=abc,=2,@inv6=1'
                  RIGHTS=$'@inv3;19\n@inv6;1' ;;
        range)    RAW='@inv3|@inv4=0,@inv5=2147483648,@inv6=2147483647'
                  RIGHTS=$'@inv3;19\n@inv6;2147483647' ;;
        spaces)   RAW='@inv3| @inv4=1 , @inv5 = 1'  # um ',' egal, um '=' ungültig
                  RIGHTS=$'@inv3;19\n@inv4;1' ;;
        ldap)     RAW='@inv3|uid=hans,CN=Doc,O=firma=1,cn=3,@inv4=1'  # cn=3: User "cn"
                  RIGHTS=$'@inv3;19\n@inv4;1' ;;
        group)    RAW='@inv3|.cn=ops,ou=groups,@inv4=1'  # Gruppe ohne Recht
                  RIGHTS=$'@inv3;19\n@inv4;1' ;;
    esac
}

acl_string() {
    local IFS=,
    echo "$OWNER|${ENTRIES[*]}"
}

# grants: Rechte der @clientids aus OWNER/ENTRIES als "<clientid>;<recht>",
# nur aus dieser ACL (nichts Geerbtes). Mehrere Einträge einer clientid
# werden verodert, der Besitzer hat mindestens OWNER_PERM.
grants() {
    local -A rights=()
    local entry principal value
    for entry in "${ENTRIES[@]}"; do
        principal=${entry%=*}
        value=${entry##*=}
        if [[ $principal == @* ]]; then
            rights[$principal]=$(( ${rights[$principal]:-0} | value ))
        fi
    done
    if [[ $OWNER == @* ]]; then
        rights[$OWNER]=$(( ${rights[$OWNER]:-0} | OWNER_PERM ))
    fi
    for principal in "${!rights[@]}"; do
        echo "$principal;${rights[$principal]}"
    done
}

# Nach Pfad (natürliche Reihenfolge), dann clientid.
sort_rows() {
    LC_ALL=C sort -t';' -k1,1V -k2,2V
}

# record <pfad>: schreibt die Zeile für acls.csv aus OWNER/ENTRIES/HAS_ACL
# und die Zeilen für expected.csv (Rechte dieser ACL, nichts Geerbtes)
record() {
    local grant
    if (( HAS_ACL )); then
        echo "$1;$(acl_string)" >> "$acls_body"
        while IFS= read -r grant; do
            [[ -z $grant ]] || echo "$1;$grant" >> "$expected_body"
        done <<< "$(grants)"
    else
        echo "$1;" >> "$acls_body"
    fi
}

generate() {
    local n c s name account container path acls_body expected_body grant
    PATHS=()
    acls_body=$(mktemp)
    expected_body=$(mktemp)

    for (( n = 1; n <= ACCOUNTS; n++ )); do
        account=$BASE_DIR/account$n
        PATHS+=("$account")
        acl_account "$n"
        record "$account"

        for (( c = 1; c <= CONTAINERS; c++ )); do
            container=$account/container$c
            PATHS+=("$container")
            acl_container "$n" "$c"
            record "$container"

            for s in "${SUB_CONTAINERS[@]}"; do
                (( s == c )) || continue
                path=$container/sub
                PATHS+=("$path")
                acl_sub "$n" "$c"
                record "$path"
            done
        done
    done

    PATHS+=("$BASE_DIR/invalid")
    echo "$BASE_DIR/invalid;" >> "$acls_body"
    for name in "${INVALID_NAMES[@]}"; do
        path=$BASE_DIR/invalid/$name
        PATHS+=("$path")
        acl_invalid "$name"
        echo "$path;$RAW" >> "$acls_body"
        while IFS= read -r grant; do
            [[ -z $grant ]] || echo "$path;$grant" >> "$expected_body"
        done <<< "$RIGHTS"
    done
    path=$BASE_DIR/invalid/noowner/sub
    PATHS+=("$path")
    echo "$path;" >> "$acls_body"

    mkdir -p "$OUT_DIR"
    { echo "path;acl"; sort_rows < "$acls_body"; } > "$ACLS_CSV"
    { echo "path;clientid;perm"; sort_rows < "$expected_body"; } > "$EXPECTED_CSV"
    rm -f "$acls_body" "$expected_body"

    echo "Vergleichsdateien: $ACLS_CSV ($(( $(wc -l < "$ACLS_CSV") - 1 )) Pfade, $(awk -F';' 'NR > 1 && $2 != ""' "$ACLS_CSV" | wc -l) mit ACL)"
    echo "                   $EXPECTED_CSV ($(( $(wc -l < "$EXPECTED_CSV") - 1 )) Rechte)"
}

# --- HDFS ---------------------------------------------------------------------

create_dirs() {
    echo "Lege ${#PATHS[@]} Ordner unter $BASE_DIR an ..."
    hdfs_cmd dfs -mkdir -p "${PATHS[@]}"
}

set_acls() {
    local count
    count=$(awk -F';' 'NR > 1 && $2 != ""' "$ACLS_CSV" | wc -l)
    echo "Setze $ACL_ATTR an $count Pfaden ($PARALLEL parallel) ..."
    # Wert in Anführungszeichen: setfattr behandelt ihn dann immer als Text,
    # auch wenn er mit 0x/0s beginnt.
    if ! awk -F';' 'NR > 1 && $2 != "" {print $1 "\t" $2}' "$ACLS_CSV" \
        | tr '\t\n' '\0\0' \
        | xargs -0 -n 2 -P "$PARALLEL" bash -c 'hdfs_cmd dfs -setfattr -n "$ACL_ATTR" -v "\"$2\"" "$1"' _; then
        echo "Fehler: Mindestens ein setfattr ist fehlgeschlagen." >&2
        exit 1
    fi
}

verify() {
    local actual
    echo "Lese $ACL_ATTR unter $BASE_DIR zurück und vergleiche mit $ACLS_CSV ..."
    actual=$(hdfs_cmd dfs -getfattr -R -d "$BASE_DIR" | awk -v attr="$ACL_ATTR" '
        /^# file: / { if (path != "") print path ";" value; path = substr($0, 9); value = ""; next }
        index($0, attr "=") == 1 { value = substr($0, length(attr) + 2); gsub(/^"|"$/, "", value) }
        END { if (path != "") print path ";" value }')

    if diff -u --label erwartet --label hdfs \
        <(tail -n +2 "$ACLS_CSV") \
        <(awk -F';' 'NR == FNR {want[$1]; next} ($1 in want)' <(tail -n +2 "$ACLS_CSV") <(printf '%s\n' "$actual") | sort_rows); then
        echo "OK: Alle Pfade und $ACL_ATTR-Werte stimmen mit $ACLS_CSV überein."
    else
        echo "Fehler: HDFS weicht von $ACLS_CSV ab (Diff oben). Mit --clean neu anlegen." >&2
        exit 1
    fi
}

# --- hdfs-acl (nur mit --docker) ----------------------------------------------

FAILED=0

# expect <exit-code> <beschreibung> <kommando...>; Ausgabe nach $WORK_DIR/last.log
expect() {
    local want=$1 what=$2 rc=0
    shift 2
    "$@" > "$WORK_DIR/last.log" 2>&1 || rc=$?
    if (( rc == want )); then
        echo "  ok     $what"
    else
        echo "  FEHLER $what: Exit-Code $rc statt $want" >&2
        sed 's/^/         /' "$WORK_DIR/last.log" >&2
        FAILED=1
    fi
}

# expect_output <muster> <beschreibung>: sucht in der Ausgabe des letzten expect
expect_output() {
    if grep -q -- "$1" "$WORK_DIR/last.log"; then
        echo "  ok     $2"
    else
        echo "  FEHLER $2: '$1' nicht in der Ausgabe" >&2
        sed 's/^/         /' "$WORK_DIR/last.log" >&2
        FAILED=1
    fi
}

acl_tool() {
    python3 "$SCRIPT_DIR/src/hdfs/main.py" "$@"
}

# neuestes Backup einer Aktion, z. B. latest_backup add-newapp
latest_backup() {
    ls -t "$WORK_DIR"/work/hdfs-acl-backup-*-"$1".csv | head -1
}

test_tool() {
    local value
    echo "Teste hdfs-acl gegen den NameNode ..."
    export BASE_DIR ACL_ATTR HDFS_JAVA_HOME=$JAVA_HOME DEPTH=$TEST_DEPTH
    cd "$WORK_DIR/work"

    # fsimage: Checkpoint erzwingen und holen. Das geht nur hier, im echten
    # Cluster sperrt der Safemode alle Schreibzugriffe (siehe hdfs.md).
    HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -safemode enter > /dev/null
    HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -saveNamespace > /dev/null
    HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -safemode leave > /dev/null
    HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -fetchImage . > /dev/null 2>&1

    expect 0 "collect aus dem fsimage" acl_tool collect
    expect 0 "export (fsimage) = expected.csv" diff <(acl_tool export 2>/dev/null) "$EXPECTED_CSV"
    expect 0 "collect --live" acl_tool collect --live --db live.db
    expect 0 "export (live) = expected.csv" diff <(acl_tool export --db live.db 2>/dev/null) "$EXPECTED_CSV"

    # Nachlesen scheitert (keine Berechtigung): Abbruch statt "keine Änderungen"
    hdfs_cmd dfs -chmod 700 "$BASE_DIR/account5/container1"
    expect 1 "add bricht ab, wenn das Nachlesen scheitert" \
        env HADOOP_USER_NAME=other python3 "$SCRIPT_DIR/src/hdfs/main.py" add @client5 @newapp
    expect_output "Nachlesen von" "Meldung nennt den Lesefehler"
    hdfs_cmd dfs -chmod 755 "$BASE_DIR/account5/container1"
    expect 1 "Abbruch ohne core-site.xml" \
        env HADOOP_CONF_DIR="$WORK_DIR/gibtsnicht" python3 "$SCRIPT_DIR/src/hdfs/main.py" add @client5 @newapp
    expect_output "core-site.xml nicht gefunden" "Meldung nennt core-site.xml"

    # --new-owner: die eigene clientid wird abgelehnt, ein fehlendes @ ergänzt
    expect 1 "remove @client2 --new-owner @client2 abgelehnt" acl_tool remove @client2 --new-owner @client2
    expect 0 "remove @client10 --new-owner newapp --apply" acl_tool remove @client10 --new-owner newapp --apply
    value=$(hdfs_cmd dfs -getfattr -n "$ACL_ATTR" "$BASE_DIR/account1/container48" 2>/dev/null | grep "^$ACL_ATTR=" || true)
    if [[ $value == "$ACL_ATTR=\"@newapp|@client2=5,$OPS=16,@client2=16,cn=doc,ou=hell,dc=firma=3,cn=testgroup\"" ]]; then
        echo "  ok     newapp wird zu @newapp (account1/container48)"
    else
        echo "  FEHLER newapp als Besitzer: $value" >&2
        FAILED=1
    fi

    # restore ist wiederholbar, auch auf veraltetem fsimage
    expect 0 "restore (remove)" acl_tool restore "$(latest_backup remove-client10)" --apply
    expect 0 "zweites restore (remove) ohne Konflikte" acl_tool restore "$(latest_backup remove-client10)" --apply
    expect_output "Keine Änderungen nötig" "zweites restore ändert nichts"
    expect 0 "add @client3 @newapp --apply" acl_tool add @client3 @newapp --apply
    expect 0 "restore (add)" acl_tool restore "$(latest_backup add-newapp)" --apply
    expect 0 "zweites restore (add) ohne Konflikte" acl_tool restore "$(latest_backup add-newapp)" --apply

    cd - > /dev/null
    if (( FAILED )); then
        echo "Fehler: Mindestens ein hdfs-acl-Test ist fehlgeschlagen." >&2
        exit 1
    fi
    echo "OK: hdfs-acl-Tests bestanden."
}

# --- Ablauf -------------------------------------------------------------------

generate

if (( DRY_RUN )); then
    echo "Dry-Run: HDFS wurde nicht verändert."
    exit 0
fi

setup_hdfs
if (( DOCKER )); then
    start_namenode
fi

if (( ! VERIFY_ONLY )); then
    if (( CLEAN )); then
        echo "Lösche $BASE_DIR ..."
        hdfs_cmd dfs -rm -r -f -skipTrash "$BASE_DIR"
    fi
    create_dirs
    set_acls
fi

verify
if (( DOCKER )); then
    test_tool
    # nach allen restores muss HDFS wieder genau acls.csv entsprechen
    verify
fi
echo "Fertig nach ${SECONDS}s."
