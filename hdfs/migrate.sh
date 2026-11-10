#!/usr/bin/env bash
#
# migrate.sh [ALT] [NEU] [--two-step] [--keep]
#
# Spielt eine Migration ALT -> NEU (Default @client3 -> @newapp) mit
# hdfs-acl auf einem frischen NameNode durch und prüft das Ergebnis:
#   1. NameNode per Docker starten (wie test.sh --docker, Konfiguration in
#      namenode/), Testdaten mit test.sh laden
#   2. fsimage erzeugen und holen, damit hdfs-acl wie im echten Betrieb
#      aus dem fsimage plant und nur die betroffenen Pfade live nachliest
#   3. migrieren, erst als Dry-Run, dann mit --apply:
#        Standard     hdfs-acl replace ALT NEU
#        --two-step   hdfs-acl add ALT NEU, dann remove ALT --new-owner NEU
#   4. HDFS live einlesen und mit migrate_check.py gegen die Testdaten
#      prüfen: effektive Rechte wie vorher, nur ALT heißt jetzt NEU; ALT
#      steht in keiner ACL mehr; alle anderen Pfade sind unverändert
#   5. restore des Backups (bei --two-step beider, rückwärts) und prüfen,
#      dass HDFS wieder genau den Testdaten entspricht
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
TWO_STEP=0

usage() {
    sed -n '3,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case $arg in
        --two-step) TWO_STEP=1 ;;
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
export BASE_DIR ACL_ATTR PARALLEL HDFS_JAVA_HOME=$JAVA_HOME

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

if (( TWO_STEP )); then
    echo "Migriere $OLD -> $NEW in zwei Schritten (add, dann remove --new-owner) ..."
    step "add (Dry-Run)" acl_tool add "$OLD" "$NEW"
    echo "  add:     $(planned)"
    step "add --apply" acl_tool add "$OLD" "$NEW" --apply
    step "remove (Dry-Run)" acl_tool remove "$OLD" --new-owner "$NEW"
    echo "  remove:  $(planned)"
    step "remove --apply" acl_tool remove "$OLD" --new-owner "$NEW" --apply
    BACKUPS=("$(latest_backup "remove-${OLD#@}")" "$(latest_backup "add-${NEW#@}")")
else
    echo "Migriere $OLD -> $NEW mit replace ..."
    step "replace (Dry-Run)" acl_tool replace "$OLD" "$NEW"
    echo "  replace: $(planned)"
    step "replace --apply" acl_tool replace "$OLD" "$NEW" --apply
    BACKUPS=("$(latest_backup "replace-${OLD#@}-${NEW#@}")")
fi
echo "  Backup: ${BACKUPS[*]##*/}"

FAILED=0
check "die Migration" --old "$OLD" --new "$NEW" || FAILED=1

# Zurückdrehen in umgekehrter Reihenfolge; danach muss alles wie vorher sein
echo "Drehe die Migration zurück ..."
for backup in "${BACKUPS[@]}"; do
    step "restore $backup" acl_tool restore "$backup" --apply
    echo "  restore ${backup##*/}: $(planned)"
done
check "den Stand nach restore" --restored || FAILED=1
cd - > /dev/null

if (( FAILED )); then
    echo "Fehler: Migration $OLD -> $NEW hat nicht wie erwartet geklappt." >&2
    exit 1
fi
echo "OK: Migration $OLD -> $NEW geprüft und zurückgedreht (${SECONDS}s)."
