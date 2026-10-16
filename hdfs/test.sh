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
#
# ACL-Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
#   besitzer/eintrag: @clientid, user (ohne Präfix) oder #gruppe
#   recht: 3 = lesen+schreiben+löschen, 1 = nur lesen
#   Der Besitzer hat immer 3. Ausgewertet werden nur @clientids.
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
#                c==49   cl(n+8)|  (nur Besitzer, keine Einträge)
#
# Optionen:
#   --clean        $BASE_DIR vorher komplett löschen (-skipTrash)
#   --dry-run      nur Vergleichsdateien schreiben, HDFS nicht anfassen
#   --verify-only  nichts anlegen, nur HDFS mit acls.csv vergleichen
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

ACLS_CSV=$OUT_DIR/acls.csv
EXPECTED_CSV=$OUT_DIR/expected.csv

CLEAN=0
DRY_RUN=0
VERIFY_ONLY=0

usage() {
    sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case $arg in
        --clean) CLEAN=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --verify-only) VERIFY_ONLY=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Fehler: Unbekannte Option '$arg' (siehe --help)." >&2; exit 1 ;;
    esac
done

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
    if [[ $OWNER == @* ]]; then
        rights[$OWNER]=3
    fi
    for entry in "${ENTRIES[@]}"; do
        principal=${entry%=*}
        value=${entry##*=}
        if [[ $principal == @* ]] && (( value > ${rights[$principal]:-0} )); then
            rights[$principal]=$value
        fi
    done
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

# --- Ablauf -------------------------------------------------------------------

generate

if (( DRY_RUN )); then
    echo "Dry-Run: HDFS wurde nicht verändert."
    exit 0
fi

setup_hdfs

if (( ! VERIFY_ONLY )); then
    if (( CLEAN )); then
        echo "Lösche $BASE_DIR ..."
        hdfs_cmd dfs -rm -r -f -skipTrash "$BASE_DIR"
    fi
    create_dirs
    set_acls
fi

verify
echo "Fertig nach ${SECONDS}s."
