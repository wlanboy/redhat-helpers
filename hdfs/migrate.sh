#!/usr/bin/env bash
#
# migrate.sh [ALT] [NEU] [--direct] [--keep]
#
# Spielt eine Migration ALT -> NEU (Default @client3 -> @newapp) mit
# hdfs-acl auf einem frischen NameNode durch und prüft jede Phase:
#   1. NameNode per Docker starten (wie test.sh --docker, Konfiguration in
#      namenode/), Testdaten mit test.sh laden
#   2. fsimage erzeugen und holen, damit hdfs-acl wie im echten Betrieb
#      aus dem fsimage plant und nur die betroffenen Pfade live nachliest
#   3. Phase 1, "add ALT NEU": beide clientids greifen. Es gibt nur einen
#      Besitzer, ALT bleibt es; NEU bekommt dort einen Eintrag mit den
#      Rechten des Besitzers plus ADMINISTRATION (3 + 16 = 19), sonst die
#      Rechte von ALT. Prüfen.
#   4. Phase 2, "replace ALT NEU": ALT wird vollständig durch NEU ersetzt,
#      auch als Besitzer. Prüfen: effektive Rechte wie vorher, nur heißt
#      ALT jetzt NEU; ALT steht nirgends mehr; der Rest ist unverändert.
#      Geplant wird dabei auf dem fsimage von vor Phase 1, das Nachlesen
#      muss die Einträge aus Phase 1 bemerken.
#   5. Beide Backups rückwärts mit restore zurückspielen und prüfen, dass
#      HDFS wieder genau den Testdaten entspricht
#
# --direct  ohne Phase 1, nur "replace ALT NEU" (beide greifen nie zugleich)
#
# Container und Arbeitsordner werden am Ende gelöscht, mit --keep nicht.
# ALT und NEU ohne "@" bekommen es ergänzt. NEU darf schon Rechte haben
# (z. B. @client4), dann gelten an diesen Pfaden die Bits beider.
#
# Umgebungsvariablen wie bei test.sh (BASE_DIR, ACL_ATTR, PARALLEL,
# HADOOP_REPO, HADOOP_VERSION, HADOOP_HOME, HDFS_JAVA_HOME, DOCKER_IMAGE).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

BASE_DIR=${BASE_DIR:-/fs}
BASE_DIR=${BASE_DIR%/}
ACL_ATTR=${ACL_ATTR:-user.acl}
PARALLEL=${PARALLEL:-8}

OLD=""
NEW=""
DIRECT=0

usage() {
    sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case $arg in
        --direct) DIRECT=1 ;;
        --keep) KEEP=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "Fehler: Unbekannte Option '$arg' (siehe --help)." >&2; exit 1 ;;
        *)
            if [[ -z $OLD ]]; then OLD=$arg
            elif [[ -z $NEW ]]; then NEW=$arg
            else echo "Fehler: Zu viele Argumente (siehe --help)." >&2; exit 1
            fi ;;
    esac
done
OLD=${OLD:-@client3}
NEW=${NEW:-@newapp}
[[ $OLD == @* ]] || OLD=@$OLD
[[ $NEW == @* ]] || NEW=@$NEW
if [[ $OLD == "$NEW" ]]; then
    echo "Fehler: ALT und NEU sind gleich." >&2
    exit 1
fi

acl_tool() {
    python3 "$SCRIPT_DIR/src/hdfs/main.py" "$@"
}

# step <beschreibung> <kommando...>: Ausgabe ins Log, bei Fehler anzeigen
step() {
    local what=$1 rc=0
    shift
    "$@" > "$WORK_DIR/last.log" 2>&1 || rc=$?
    if (( rc != 0 )); then
        echo "Fehler: $what (Exit-Code $rc):" >&2
        sed 's/^/  /' "$WORK_DIR/last.log" >&2
        exit 1
    fi
}

# Zeile "<n> Pfade betroffen." bzw. "Keine Änderungen nötig." des letzten step
planned() {
    grep -E "Pfade betroffen|Keine Änderungen nötig" "$WORK_DIR/last.log" || echo "?"
}

latest_backup() {
    ls -t "$WORK_DIR"/work/hdfs-acl-backup-*-"$1".csv | head -1
}

# check <beschreibung>: HDFS live einlesen und gegen die Testdaten prüfen
check() {
    echo "Prüfe $1 ..."
    step "collect --live" acl_tool collect --live --db "$WORK_DIR/work/check.db"
    python3 "$SCRIPT_DIR/migrate_check.py" "${@:2}" \
        --acls "$TESTDATA/acls.csv" --expected "$TESTDATA/expected.csv" \
        --db "$WORK_DIR/work/check.db"
}

# --- Ablauf -------------------------------------------------------------------

setup_hdfs
start_namenode
TESTDATA=$WORK_DIR/testdata
# DEPTH=3: die Testdaten von test.sh haben Unterordner sub (dritte Ebene)
export BASE_DIR ACL_ATTR PARALLEL HDFS_JAVA_HOME=$JAVA_HOME DEPTH=3

# test.sh legt die Testdaten im eigenen NameNode an (HADOOP_CONF_DIR zeigt
# schon dorthin) und schreibt die Vergleichsdateien in den Arbeitsordner
OUT_DIR=$TESTDATA "$SCRIPT_DIR/test.sh"

echo "Erzeuge fsimage ..."
cd "$WORK_DIR/work"
HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -safemode enter > /dev/null
HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -saveNamespace > /dev/null
HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -safemode leave > /dev/null
HADOOP_USER_NAME=hadoop hdfs_cmd dfsadmin -fetchImage . > /dev/null 2>&1

step "collect" acl_tool collect
echo "Vorher: $OLD hat Rechte an $(acl_tool search "$OLD" --format csv 2>/dev/null | tail -n +2 | wc -l) Pfaden," \
     "$NEW an $(acl_tool search "$NEW" --format csv 2>/dev/null | tail -n +2 | wc -l)."

FAILED=0
BACKUPS=()

# apply <kommando> <backup-label> <argumente...>: Dry-Run, dann --apply
apply() {
    local command=$1 label=$2
    shift 2
    step "$command (Dry-Run)" acl_tool "$command" "$@"
    printf '  %-8s %s\n' "$command:" "$(planned)"
    step "$command --apply" acl_tool "$command" "$@" --apply
    BACKUPS=("$(latest_backup "$label")" "${BACKUPS[@]}")
}

if (( ! DIRECT )); then
    echo "Phase 1: $OLD und $NEW greifen beide (add) ..."
    apply add "add-${NEW#@}" "$OLD" "$NEW"
    check "Phase 1" --stage added --old "$OLD" --new "$NEW" || FAILED=1
fi
echo "Phase 2: $OLD vollständig durch $NEW ersetzen (replace) ..."
apply replace "replace-${OLD#@}-${NEW#@}" "$OLD" "$NEW"
PHASE2=(--stage replaced --old "$OLD" --new "$NEW")
(( DIRECT )) || PHASE2+=(--after-add)
check "Phase 2" "${PHASE2[@]}" || FAILED=1

# Zurückdrehen in umgekehrter Reihenfolge; danach muss alles wie vorher sein
echo "Drehe die Migration zurück ..."
for backup in "${BACKUPS[@]}"; do
    step "restore $backup" acl_tool restore "$backup" --apply
    echo "  restore ${backup##*/}: $(planned)"
done
check "den Stand nach restore" --stage restored || FAILED=1
cd - > /dev/null

if (( FAILED )); then
    echo "Fehler: Migration $OLD -> $NEW hat nicht wie erwartet geklappt." >&2
    exit 1
fi
echo "OK: Migration $OLD -> $NEW geprüft und zurückgedreht (${SECONDS}s)."
