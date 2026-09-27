#!/usr/bin/env bash
#
# prepare-runtime.sh
#
# Prüft als normaler User, ob die Runtime-VM für RabbitMQ vorbereitet ist.
# Installiert nichts, ändert nichts:
#   - Laufzeit-Bibliotheken (openssl-libs, ncurses-libs, libstdc++, zlib)
#     und Tools (tar, xz, curl)
#   - Lingering für den aktuellen User (User Units laufen ohne Login
#     und starten beim Boot)
#   - systemctl --user erreichbar (echte Login-Session)
#   - $RABBITMQ_BASE (Default: /opt/local/rabbitmq) vorhanden und
#     beschreibbar
#   - Kurzname des Hosts auflösbar (für Cluster)
#
# Fehlt etwas, gibt das Skript die root-Befehle für den Admin aus und
# endet mit Exit-Code 1.
#
# Aufruf: prepare-runtime.sh (als der User, unter dem RabbitMQ laufen soll)

set -uo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Fehler: Bitte als normaler User ausführen, nicht als root." >&2
    exit 1
fi

FAILED=0
ROOT_CMDS=()

ok()   { echo "  [OK]   $*"; }
fail() { echo "  [FEHLT] $*"; FAILED=1; }
warn() { echo "  [WARN] $*"; }

echo "== Laufzeit-Pakete =="
MISSING=()
for pkg in openssl-libs ncurses-libs libstdc++ zlib tar xz; do
    if rpm -q "$pkg" &>/dev/null; then
        ok "$pkg"
    else
        fail "$pkg"
        MISSING+=("$pkg")
    fi
done
# curl oder curl-minimal, beide gleichzeitig kollidieren
if command -v curl &>/dev/null; then
    ok "curl"
else
    fail "curl"
    MISSING+=("curl-minimal")
fi
[[ ${#MISSING[@]} -gt 0 ]] && ROOT_CMDS+=("dnf install -y ${MISSING[*]}")

echo "== Lingering =="
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" == "yes" ]]; then
    ok "Lingering aktiv für '$USER'"
else
    fail "Lingering nicht aktiv für '$USER'"
    ROOT_CMDS+=("loginctl enable-linger $USER")
fi

echo "== systemd User-Instanz =="
if systemctl --user show-environment &>/dev/null; then
    ok "systemctl --user erreichbar"
else
    fail "systemctl --user nicht erreichbar (per SSH direkt als '$USER' einloggen, nicht su/sudo -u)"
fi

echo "== Verzeichnis =="
BASE=${RABBITMQ_BASE:-/opt/local/rabbitmq}
if [[ -d "$BASE" && -w "$BASE" ]]; then
    ok "$BASE beschreibbar"
else
    fail "$BASE fehlt oder ist nicht beschreibbar"
    ROOT_CMDS+=("mkdir -p $BASE && chown $USER: $BASE")
fi

echo "== Hostname =="
SHORT=$(hostname -s)
if getent hosts "$SHORT" &>/dev/null; then
    ok "'$SHORT' auflösbar"
else
    warn "'$SHORT' nicht auflösbar (für Cluster DNS oder /etc/hosts nötig)"
fi

# Benötigte Ports (firewalld, als root freigeben):
#   4369         epmd (Erlang Port Mapper, Cluster)
#   5672         AMQP
#   5671         AMQPS (AMQP über TLS)
#   15672        Management UI / HTTP API
#   15692        Prometheus-Metriken
#   25672        Inter-Node-Kommunikation (Cluster)
#   35672-35682  CLI-Tools (rabbitmqctl etc.)

echo
if [[ $FAILED -eq 0 ]]; then
    echo "Alles vorhanden. Weiter mit: rabbitmq/install-rabbitmq.sh"
    echo "Hinweis: Für einen Cluster Ports 4369, 25672 und 35672-35682 zwischen"
    echo "         den Nodes öffnen, für Clients 5672/5671, 15672, 15692."
    exit 0
fi

echo "Es fehlt etwas."
if [[ ${#ROOT_CMDS[@]} -gt 0 ]]; then
    echo "Ein Admin muss als root ausführen:"
    for c in "${ROOT_CMDS[@]}"; do
        echo "  $c"
    done
fi
exit 1
