#!/usr/bin/env bash
#
# cluster.sh
#
# Verwaltet den Valkey-Cluster über valkey-cli --cluster. Passwort aus
# $VALKEY_BASE/etc/auth.conf (oder VALKEY_PASSWORD).
#
# Aufruf:
#   cluster.sh create             Cluster aus VALKEY_CLUSTER_NODES bilden
#                                 (einmalig, nachdem alle VMs installiert
#                                 sind; ohne Nachfrage: YES=j)
#   cluster.sh check [host:port]  Slots, Primaries/Replicas prüfen
#   cluster.sh info  [host:port]  Kurzübersicht (Keys, Slots pro Primary)
#   cluster.sh nodes [host:port]  CLUSTER NODES ausgeben
#
# Default für host:port ist 127.0.0.1:<erster Port aus VALKEY_PORTS>.
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/cluster.conf"

BASE=${VALKEY_BASE:-$HOME/valkeycluster}
CLI="${BASE}/server/current/bin/valkey-cli"

usage() {
    sed -n '/^# Aufruf:/,/^# Default/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 1
}

[[ $# -ge 1 ]] || usage
CMD="$1"
shift

if [[ ! -x "$CLI" ]]; then
    echo "Fehler: $CLI nicht gefunden (erst install-valkeycluster.sh ausführen)." >&2
    exit 1
fi

if [[ -z "${VALKEY_PASSWORD:-}" ]]; then
    if [[ ! -r "${BASE}/etc/auth.conf" ]]; then
        echo "Fehler: ${BASE}/etc/auth.conf nicht lesbar und VALKEY_PASSWORD nicht gesetzt." >&2
        exit 1
    fi
    VALKEY_PASSWORD=$(sed -n 's/^requirepass "\(.*\)"$/\1/p' "${BASE}/etc/auth.conf")
fi
# valkey-cli liest das Passwort aus der Umgebung, so taucht es nicht in ps auf
export VALKEYCLI_AUTH="$VALKEY_PASSWORD"

read -ra PORTS <<< "$VALKEY_PORTS"
ENTRY=${1:-127.0.0.1:${PORTS[0]}}

case "$CMD" in
    create)
        read -ra NODES <<< "$VALKEY_CLUSTER_NODES"
        MIN=$(( 3 * (VALKEY_CLUSTER_REPLICAS + 1) ))
        if [[ ${#NODES[@]} -lt $MIN ]]; then
            echo "Fehler: ${#NODES[@]} Instanzen in VALKEY_CLUSTER_NODES, für" >&2
            echo "        ${VALKEY_CLUSTER_REPLICAS} Replica(s) pro Primary sind mindestens ${MIN} nötig." >&2
            exit 1
        fi
        echo "== Erreichbarkeit =="
        FAILED=0
        for n in "${NODES[@]}"; do
            h=${n%:*}
            p=${n##*:}
            if [[ "$("$CLI" -h "$h" -p "$p" ping 2>&1)" == "PONG" ]]; then
                echo "  [OK]    $n"
            else
                echo "  [FEHLT] $n"
                FAILED=1
            fi
        done
        if [[ $FAILED -ne 0 ]]; then
            echo "Fehler: Nicht alle Instanzen erreichbar (Dienst, Firewall Port und" >&2
            echo "        Port+10000, identisches Passwort prüfen)." >&2
            exit 1
        fi
        EXTRA=()
        [[ "${YES:-}" =~ ^[JjYy]$ ]] && EXTRA+=(--cluster-yes)
        "$CLI" --cluster create "${NODES[@]}" \
            --cluster-replicas "$VALKEY_CLUSTER_REPLICAS" "${EXTRA[@]}"
        ;;
    check)
        "$CLI" --cluster check "$ENTRY"
        ;;
    info)
        "$CLI" --cluster info "$ENTRY"
        ;;
    nodes)
        "$CLI" -h "${ENTRY%:*}" -p "${ENTRY##*:}" cluster nodes
        ;;
    *)
        usage
        ;;
esac
