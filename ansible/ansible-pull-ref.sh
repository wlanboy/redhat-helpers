#!/usr/bin/env bash
# Prüft ein Git-Repo auf einen neuen Tag bzw. Release-Branch und startet
# bei einer Änderung ansible-pull mit dem passenden Playbook.
#
# Konfiguration über Umgebungsvariablen (z.B. per EnvironmentFile der
# systemd Unit), siehe ansible/pull.md.
set -euo pipefail

REPO_URL="${REPO_URL:?REPO_URL muss gesetzt sein}"   # Repo, das beobachtet wird
MODE="${MODE:-tag}"                                  # tag | branch
REF_PATTERN="${REF_PATTERN:-}"                       # z.B. 'v*' oder 'release/*'
REF_EXCLUDE="${REF_EXCLUDE:-}"                       # Regex, z.B. '-(rc|beta)[0-9]*$'
PLAYBOOK="${PLAYBOOK:?PLAYBOOK muss gesetzt sein}"   # Pfad relativ zum Repo-Root
STATE_NAME="${STATE_NAME:-$MODE}"

# Optional: Playbooks liegen in einem eigenen Deploy-Repo. Dann wird dieses
# ausgecheckt und der gefundene Ref nur als Variable übergeben.
PULL_URL="${PULL_URL:-$REPO_URL}"
PULL_CHECKOUT="${PULL_CHECKOUT:-}"

STATE_DIR="${STATE_DIR:-$HOME/.local/state/ansible-pull}"
WORKDIR="${WORKDIR:-$HOME/.cache/ansible-pull/$STATE_NAME}"
ANSIBLE_PULL_ARGS="${ANSIBLE_PULL_ARGS:-}"

case "$MODE" in
    tag)    ls_flag=--tags;  prefix=refs/tags/;  REF_PATTERN="${REF_PATTERN:-v*}" ;;
    branch) ls_flag=--heads; prefix=refs/heads/; REF_PATTERN="${REF_PATTERN:-release/*}" ;;
    *)      echo "MODE muss 'tag' oder 'branch' sein, nicht '$MODE'" >&2; exit 2 ;;
esac

mkdir -p "$STATE_DIR" "$(dirname "$WORKDIR")"
state_file="$STATE_DIR/$STATE_NAME.last"

# Kein paralleler Lauf derselben Instanz
exec 9>"$STATE_DIR/$STATE_NAME.lock"
flock -n 9 || { echo "Läuft bereits, überspringe."; exit 0; }

# Alle passenden Refs holen, "<ref> <sha>" bauen, höchste Version nehmen
latest="$(
    git ls-remote --refs "$ls_flag" "$REPO_URL" "$REF_PATTERN" \
        | awk -v p="$prefix" -v ex="$REF_EXCLUDE" '
            { sub("^" p, "", $2) }
            ex != "" && $2 ~ ex { next }
            { print $2, $1 }' \
        | sort -V -k1,1 \
        | tail -n 1
)"

if [[ -z "$latest" ]]; then
    echo "Kein Ref gefunden für $MODE '$REF_PATTERN' in $REPO_URL."
    exit 0
fi

read -r ref sha <<<"$latest"
last="$(cat "$state_file" 2>/dev/null || true)"

if [[ "$latest" == "$last" ]]; then
    echo "Nichts zu tun, $ref ($sha) ist bereits ausgerollt."
    exit 0
fi

echo "Neuer Stand: $ref ($sha), vorher: ${last:-keiner}"

# shellcheck disable=SC2086  # ANSIBLE_PULL_ARGS soll aufgeteilt werden
ansible-pull \
    --url "$PULL_URL" \
    --checkout "${PULL_CHECKOUT:-$ref}" \
    --directory "$WORKDIR" \
    --inventory localhost, \
    --clean \
    --extra-vars "release_kind=$MODE release_ref=$ref release_sha=$sha" \
    $ANSIBLE_PULL_ARGS \
    "$PLAYBOOK"

# Nur nach erfolgreichem Lauf merken, sonst beim nächsten Timer erneut versuchen
echo "$latest" >"$state_file"
echo "Fertig: $ref ($sha)"
