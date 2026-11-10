#!/usr/bin/env bash
#
# test.sh
#
# Legt in HDFS Testdaten für die Auswertung von ACL-Metadaten an:
#   - $BASE_DIR/account1..10, darunter je container1..50
#   - setzt das xattr $ACL_ATTR nach festem Schema (siehe unten) per
#     "hdfs dfs -setfattr", parallel über xargs
#   - schreibt die Vergleichsdateien nach $OUT_DIR:
#       acls.csv      pfad;acl            gesetzter Wert, leer = kein Attribut
#       expected.csv  pfad;clientid;recht;acl_von
#                     effektive Rechte je @clientid (inkl. Vererbung),
#                     Pfade ohne berechtigte @clientid fehlen
#   - liest am Ende alle Werte per "getfattr -R" zurück und vergleicht sie
#     mit acls.csv
#   - mit --docker: startet vorher einen frischen NameNode per "docker run"
#     (ohne DataNodes, es geht nur um Metadaten), testet danach hdfs-acl
#     durchgängig (fsimage, --live, Fehlerfälle, remove/add/restore) und
#     löscht den Container am Ende wieder
#
# ACL-Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
#   besitzer/eintrag: @clientid, user (ohne Präfix) oder #gruppe;
#   LDAP-Teile (cn=..., ou=..., dc=..., sn=...) werden ignoriert
#   recht: Bitmaske (Spring Security ACL PermissionFactory), z. B.
#          3 = lesen+schreiben+löschen, 1 = nur lesen
#   Der Besitzer hat immer mindestens 3, mehrere Einträge einer clientid
#   werden verodert. Ausgewertet werden nur @clientids.
#   Container ohne ACL erben vom Account, ein Container mit ACL ersetzt die
#   des Accounts komplett.
#
# Schema (n = Account-Nummer, cl(k) = @client<k>, k zyklisch über 1..10):
#   account n:   cl(n)|cl(n+1)=3,svc_backup=1,cl(n+2)=1,#ops=3
#                account3 Besitzer "hans", account7 Besitzer "#admins",
#                account10 ohne ACL
#   container c: c%5==0  ohne ACL -> erbt vom Account
#                c%5==1  cl(n)|cl(n+3)=3,dev_user=3,cl(n+4)=1
#                c%5==2  cl(n+5)|#readers=1,cl(n+5)=1,cl(n)=1
#                        (Besitzer zusätzlich mit =1 gelistet, bleibt 3)
#                c%5==3  maria|cl(n+6)=3,cl(n+7)=1,#ops=1
#                c%5==4  #admins|peter=3,#ops=1  (keine @clientid -> niemand)
#                c==48   cl(n+9)|cl(n+1)=5,#ops=16,cl(n+1)=16,cl(n+9)=8,
#                                cn=doc,ou=hell,dc=firma=3,cn=testgroup
#                        (Masken > 3: cl(n+1) 5|16=21, Besitzer 3|8=11;
#                        LDAP-Teile ohne Warnung ignoriert)
#                c==49   cl(n+8)|  (nur Besitzer, keine Einträge)
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
readonly CONTAINERS=50
readonly CLIENTS=10

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

BASE_DIR=${BASE_DIR:-/fs}
BASE_DIR=${BASE_DIR%/}
ACL_ATTR=${ACL_ATTR:-user.acl}
PARALLEL=${PARALLEL:-8}
OUT_DIR=${OUT_DIR:-$SCRIPT_DIR/testdata}
HADOOP_REPO=${HADOOP_REPO:-$HOME/git/hadoop}
HADOOP_VERSION=${HADOOP_VERSION:-3.4.2}
DOCKER_IMAGE=${DOCKER_IMAGE:-apache/hadoop:$HADOOP_VERSION}
NN_CONF_DIR=$SCRIPT_DIR/namenode

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

# --- HDFS-Client --------------------------------------------------------------

# Hadoop 3.4.2 ruft Subject.getSubject() auf, das ab JDK 23 entfernt ist.
java_usable() {
    local home=$1 version
    [[ -n $home && -x $home/bin/java ]] || return 1
    version=$("$home/bin/java" -version 2>&1 | awk -F'"' '/version/ {print $2; exit}')
    version=${version%%.*}
    [[ $version == 1 ]] && version=8
    [[ $version =~ ^[0-9]+$ ]] && (( version < 23 ))
}

setup_hdfs() {
    local candidate java_home=""
    if [[ -n ${HDFS_JAVA_HOME:-} ]]; then
        # auch .../bin/java oder .../bin angegeben? Dann das JDK darüber nehmen
        java_home=${HDFS_JAVA_HOME%/}
        java_home=${java_home%/bin/java}
        java_home=${java_home%/bin}
        if [[ ! -x $java_home/bin/java ]]; then
            echo "Fehler: HDFS_JAVA_HOME=$HDFS_JAVA_HOME: kein $java_home/bin/java gefunden." >&2
            exit 1
        fi
        if ! java_usable "$java_home"; then
            echo "Fehler: HDFS_JAVA_HOME=$HDFS_JAVA_HOME ist kein Java < 23." >&2
            exit 1
        fi
    else
        for candidate in "$HADOOP_REPO/downloads/jdk-11" \
                         "$HOME"/.sdkman/candidates/java/11* \
                         "$HOME"/.sdkman/candidates/java/17* \
                         /usr/lib/jvm/jre-11* /usr/lib/jvm/java-11* \
                         /usr/lib/jvm/jre-17* /usr/lib/jvm/java-17* \
                         "${JAVA_HOME:-}"; do
            if java_usable "$candidate"; then
                java_home=$candidate
                break
            fi
        done
    fi
    if [[ -z $java_home ]]; then
        echo "Fehler: Kein Java 11/17 gefunden (HDFS_JAVA_HOME setzen)." >&2
        exit 1
    fi

    export JAVA_HOME=$java_home
    export HADOOP_HOME=${HADOOP_HOME:-$HADOOP_REPO/downloads/hadoop-$HADOOP_VERSION}
    export HADOOP_CONF_DIR=${HADOOP_CONF_DIR:-$HADOOP_REPO/clientconfig}
    export ACL_ATTR

    if [[ ! -x $HADOOP_HOME/bin/hdfs ]]; then
        echo "Fehler: $HADOOP_HOME/bin/hdfs nicht gefunden (HADOOP_HOME oder HADOOP_VERSION setzen)." >&2
        exit 1
    fi
    echo "HDFS-Client: $HADOOP_HOME/bin/hdfs"
    echo "  JAVA_HOME=$JAVA_HOME"
    echo "  HADOOP_CONF_DIR=$HADOOP_CONF_DIR"
}

hdfs_cmd() {
    "$HADOOP_HOME/bin/hdfs" "$@"
}
export -f hdfs_cmd

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
        7) OWNER='#admins' ;;
        *) OWNER=$(cl "$n") ;;
    esac
    ENTRIES=("$(cl $((n + 1)))=3" "svc_backup=1" "$(cl $((n + 2)))=1" "#ops=3")
}

acl_container() {
    local n=$1 c=$2
    OWNER=""
    ENTRIES=()
    HAS_ACL=1
    if (( c == 48 )); then
        OWNER=$(cl $((n + 9)))
        ENTRIES=("$(cl $((n + 1)))=5" "#ops=16" "$(cl $((n + 1)))=16" "$(cl $((n + 9)))=8"
                 "cn=doc" "ou=hell" "dc=firma=3" "cn=testgroup")
        return
    fi
    if (( c == 49 )); then
        OWNER=$(cl $((n + 8)))
        return
    fi
    case $(( c % 5 )) in
        0) HAS_ACL=0 ;;
        1) OWNER=$(cl "$n")
           ENTRIES=("$(cl $((n + 3)))=3" "dev_user=3" "$(cl $((n + 4)))=1") ;;
        2) OWNER=$(cl $((n + 5)))
           ENTRIES=("#readers=1" "$(cl $((n + 5)))=1" "$(cl "$n")=1") ;;
        3) OWNER=maria
           ENTRIES=("$(cl $((n + 6)))=3" "$(cl $((n + 7)))=1" "#ops=1") ;;
        4) OWNER='#admins'
           ENTRIES=("peter=3" "#ops=1") ;;
    esac
}

acl_string() {
    local IFS=,
    echo "$OWNER|${ENTRIES[*]}"
}

# Effektive Rechte der @clientids aus OWNER/ENTRIES als "<clientid>;<recht>".
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
        rights[$OWNER]=$(( ${rights[$OWNER]:-0} | 3 ))
    fi
    for principal in "${!rights[@]}"; do
        echo "$principal;${rights[$principal]}"
    done
}

# expected_rows <pfad> <acl_von>: Zeilen für die aktuell gesetzte OWNER/ENTRIES
expected_rows() {
    local grant
    (( HAS_ACL )) || return 0
    while IFS= read -r grant; do
        echo "$1;$grant;$2"
    done < <(grants)
}

# Nach Pfad (natürliche Reihenfolge), dann clientid.
sort_rows() {
    LC_ALL=C sort -t';' -k1,1V -k2,2V
}

generate() {
    local n c account path acls_body expected_body
    PATHS=()
    acls_body=$(mktemp)
    expected_body=$(mktemp)

    for (( n = 1; n <= ACCOUNTS; n++ )); do
        account=$BASE_DIR/account$n
        PATHS+=("$account")
        acl_account "$n"
        if (( HAS_ACL )); then
            echo "$account;$(acl_string)" >> "$acls_body"
        else
            echo "$account;" >> "$acls_body"
        fi
        expected_rows "$account" "$account" >> "$expected_body"

        for (( c = 1; c <= CONTAINERS; c++ )); do
            path=$account/container$c
            PATHS+=("$path")
            acl_container "$n" "$c"
            if (( HAS_ACL )); then
                echo "$path;$(acl_string)" >> "$acls_body"
                expected_rows "$path" "$path" >> "$expected_body"
            else
                echo "$path;" >> "$acls_body"
                acl_account "$n"
                expected_rows "$path" "$account" >> "$expected_body"
            fi
        done
    done

    mkdir -p "$OUT_DIR"
    { echo "path;acl"; sort_rows < "$acls_body"; } > "$ACLS_CSV"
    { echo "path;clientid;perm;acl_from"; sort_rows < "$expected_body"; } > "$EXPECTED_CSV"
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

# --- Docker-NameNode ----------------------------------------------------------

# Ein NameNode ohne DataNodes reicht: Ordner, xattrs und fsimage sind reine
# Metadaten. Die Konfiguration in $NN_CONF_DIR dient dem NameNode im
# Container und dem Client auf dem Host (Ports 19000/19870, siehe dort).
start_namenode() {
    local i
    WORK_DIR=$(mktemp -d /tmp/hdfs-acl-test.XXXXXX)
    NN_CONTAINER=hdfs-acl-test-$$
    trap cleanup EXIT
    mkdir -p "$WORK_DIR/work"
    export HADOOP_CONF_DIR=$NN_CONF_DIR

    echo "Starte NameNode $NN_CONTAINER ($DOCKER_IMAGE, Konfiguration $NN_CONF_DIR) ..."
    # Der User "hadoop" im Container muss die Dateien lesen können
    docker run -d --name "$NN_CONTAINER" \
        -p 127.0.0.1:19000:19000 -p 127.0.0.1:19870:19870 \
        -v "$NN_CONF_DIR:/conf:ro" -e HADOOP_CONF_DIR=/conf \
        --entrypoint bash "$DOCKER_IMAGE" \
        -c 'hdfs namenode -format -nonInteractive && exec hdfs namenode' > /dev/null

    for (( i = 0; i < 90; i++ )); do
        if [[ $(docker inspect -f '{{.State.Running}}' "$NN_CONTAINER" 2>/dev/null) != true ]]; then
            break
        fi
        if hdfs_cmd dfs -ls / > /dev/null 2>&1; then
            # Superuser ist der User, der den NameNode startet ("hadoop").
            # Ohne Kerberos glaubt der NameNode HADOOP_USER_NAME einfach.
            HADOOP_USER_NAME=hadoop hdfs_cmd dfs -chown "$(id -un)" /
            echo "NameNode antwortet nach ${i}s."
            return
        fi
        sleep 1
    done
    echo "Fehler: NameNode antwortet nicht. Log:" >&2
    docker logs --tail 30 "$NN_CONTAINER" >&2 || true
    exit 1
}

cleanup() {
    local rc=$?
    if (( KEEP )); then
        echo "Behalten: Container $NN_CONTAINER, Arbeitsordner $WORK_DIR"
        echo "  export HADOOP_CONF_DIR=$NN_CONF_DIR"
        echo "  aufräumen: docker rm -f $NN_CONTAINER && rm -rf $WORK_DIR"
        return $rc
    fi
    echo "Lösche NameNode $NN_CONTAINER und $WORK_DIR ..."
    docker rm -f "$NN_CONTAINER" > /dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
    return $rc
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
    export BASE_DIR ACL_ATTR HDFS_JAVA_HOME=$JAVA_HOME
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
    if [[ $value == "$ACL_ATTR=\"@newapp|@client2=5,#ops=16,@client2=16,cn=doc,ou=hell,dc=firma=3,cn=testgroup\"" ]]; then
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
