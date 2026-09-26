#!/usr/bin/env bash
#
# install-rabbitmq.sh
#
# Installiert Erlang (eigener Build aus Nexus) und RabbitMQ (Generic-Unix-
# Tarball) auf einer Runtime-VM als normaler User und richtet eine systemd
# User Unit ein:
#   - lädt erlang-<OTP_VERSION>-el9-<arch>.tar.gz aus dem Nexus Raw-Hosted-Repo,
#     prüft SHA256, entpackt nach $RABBITMQ_BASE/erlang/ und passt ROOTDIR
#     per "Install -minimal" an den Zielpfad an
#   - prüft per ldd, ob alle Bibliotheken von beam.smp vorhanden sind
#   - lädt rabbitmq-server-generic-unix-<RABBITMQ_VERSION>.tar.xz über den
#     Nexus GitHub-Proxy, prüft optional SHA256 und GPG-Signatur
#   - Symlinks erlang/current und server/current auf die neuen Versionen
#     (alte Versionen bleiben liegen, Upgrade = Skript mit neuer Version)
#   - legt rabbitmq.conf und enabled_plugins an, falls nicht vorhanden
#   - schreibt etc/rabbitmq.env (PATH + RABBITMQ_*-Variablen) neu
#   - setzt ~/.erlang.cookie (ERLANG_COOKIE oder interaktiv, für Cluster
#     auf allen Nodes identisch)
#   - schreibt ~/.config/systemd/user/rabbitmq.service und bietet
#     daemon-reload + enable --now an (ohne Nachfrage: ENABLE=j bzw. ENABLE=n)
#
# Verzeichnis: $RABBITMQ_BASE (Default: ~/rabbitmq)
# Vorher prüfen: prepare-runtime.sh (als User, gibt fehlende root-Schritte aus)
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/versions.conf"

if [[ $EUID -eq 0 ]]; then
    echo "Fehler: Bitte als normaler User ausführen, nicht als root." >&2
    exit 1
fi

for cmd in curl tar xz sha256sum ldd systemctl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Fehler: '$cmd' nicht gefunden (Prüfung: prepare-runtime.sh)." >&2
        exit 1
    fi
done

if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]]; then
    echo "Warnung: Lingering ist für '$USER' nicht aktiv. RabbitMQ stoppt beim" >&2
    echo "         Logout und startet nicht beim Boot. Als root: loginctl enable-linger $USER" >&2
fi

BASE=${RABBITMQ_BASE:-$HOME/rabbitmq}
DL_DIR="${BASE}/downloads"
ERL_DIR="${BASE}/erlang/erlang-${OTP_VERSION}"
RMQ_DIR="${BASE}/server/rabbitmq_server-${RABBITMQ_VERSION}"
ETC_DIR="${BASE}/etc"

mkdir -p "$DL_DIR" "${BASE}/erlang" "${BASE}/server" "$ETC_DIR" "${BASE}/data" "${BASE}/log"

download() {
    local url="$1" target="$2"
    if [[ -f "$target" ]]; then
        echo "Bereits vorhanden: $target"
        return
    fi
    curl -fL --netrc-optional -o "${target}.part" "$url"
    mv "${target}.part" "$target"
}

# ---------------------------------------------------------------- Erlang

echo "== Erlang ${OTP_VERSION} =="
ERL_URL="${NEXUS_BUILDS}/erlang/${OTP_VERSION}/${ERLANG_TARBALL}"
download "$ERL_URL" "${DL_DIR}/${ERLANG_TARBALL}"
download "${ERL_URL}.sha256" "${DL_DIR}/${ERLANG_TARBALL}.sha256"
(cd "$DL_DIR" && sha256sum -c "${ERLANG_TARBALL}.sha256")

if [[ -d "$ERL_DIR" ]]; then
    echo "Bereits entpackt: $ERL_DIR"
else
    tar -C "${BASE}/erlang" -xzf "${DL_DIR}/${ERLANG_TARBALL}"
fi
(cd "$ERL_DIR" && ./Install -minimal "$ERL_DIR" >/dev/null)

BEAM=$(find "$ERL_DIR" -path '*/erts-*/bin/beam.smp' | head -n1)
if [[ -z "$BEAM" ]]; then
    echo "Fehler: beam.smp nicht gefunden in $ERL_DIR" >&2
    exit 1
fi
if ldd "$BEAM" | grep -q 'not found'; then
    echo "Fehler: beam.smp vermisst Bibliotheken:" >&2
    ldd "$BEAM" | grep 'not found' >&2
    echo "Prüfung: prepare-runtime.sh" >&2
    exit 1
fi

"$ERL_DIR/bin/erl" -noshell -eval 'ok = crypto:start(), ok = ssl:start(), halt().' \
    || { echo "Fehler: crypto/ssl lassen sich nicht starten." >&2; exit 1; }
ln -sfn "$ERL_DIR" "${BASE}/erlang/current"

# -------------------------------------------------------------- RabbitMQ

echo "== RabbitMQ ${RABBITMQ_VERSION} =="
RMQ_TARBALL="rabbitmq-server-generic-unix-${RABBITMQ_VERSION}.tar.xz"
RMQ_URL="${NEXUS_GITHUB}/rabbitmq/rabbitmq-server/releases/download/v${RABBITMQ_VERSION}/${RMQ_TARBALL}"
download "$RMQ_URL" "${DL_DIR}/${RMQ_TARBALL}"

ACTUAL_SHA=$(sha256sum "${DL_DIR}/${RMQ_TARBALL}" | awk '{print $1}')
if [[ -n "$RABBITMQ_SHA256" ]]; then
    if [[ "$ACTUAL_SHA" != "$RABBITMQ_SHA256" ]]; then
        echo "Fehler: SHA256 stimmt nicht." >&2
        echo "  erwartet: $RABBITMQ_SHA256" >&2
        echo "  gefunden: $ACTUAL_SHA" >&2
        exit 1
    fi
    echo "SHA256 ok."
else
    echo "Warnung: RABBITMQ_SHA256 nicht gesetzt, keine Prüfung." >&2
    echo "  Berechnet: $ACTUAL_SHA  (in versions.conf eintragen)" >&2
fi

if [[ -n "$RABBITMQ_GPG_KEY_URL" ]]; then
    if ! command -v gpg &>/dev/null; then
        echo "Fehler: RABBITMQ_GPG_KEY_URL gesetzt, aber gpg nicht installiert." >&2
        exit 1
    fi
    download "${RMQ_URL}.asc" "${DL_DIR}/${RMQ_TARBALL}.asc"
    GNUPGHOME=$(mktemp -d)
    export GNUPGHOME
    trap 'rm -rf "$GNUPGHOME"' EXIT
    curl -fsSL --netrc-optional "$RABBITMQ_GPG_KEY_URL" | gpg --quiet --import
    gpg --verify "${DL_DIR}/${RMQ_TARBALL}.asc" "${DL_DIR}/${RMQ_TARBALL}"
    echo "GPG-Signatur ok."
fi

if [[ -d "$RMQ_DIR" ]]; then
    echo "Bereits entpackt: $RMQ_DIR"
else
    tar -C "${BASE}/server" -xJf "${DL_DIR}/${RMQ_TARBALL}"
fi
ln -sfn "$RMQ_DIR" "${BASE}/server/current"

# ------------------------------------------------------------ Konfiguration

echo "== Konfiguration in ${ETC_DIR} =="
if [[ ! -f "${ETC_DIR}/rabbitmq.conf" ]]; then
    cat > "${ETC_DIR}/rabbitmq.conf" <<'EOF'
# https://www.rabbitmq.com/docs/configure
listeners.tcp.default = 5672
management.tcp.port = 15672

log.file.level = info
log.file.rotation.size = 10485760
log.file.rotation.count = 5

# Cluster per Konfiguration statt manuellem join_cluster:
# cluster_formation.peer_discovery_backend = classic_config
# cluster_formation.classic_config.nodes.1 = rabbit@node1
# cluster_formation.classic_config.nodes.2 = rabbit@node2
# cluster_formation.classic_config.nodes.3 = rabbit@node3
EOF
    echo "Angelegt: ${ETC_DIR}/rabbitmq.conf"
else
    echo "Vorhanden, unverändert: ${ETC_DIR}/rabbitmq.conf"
fi

if [[ ! -f "${ETC_DIR}/enabled_plugins" ]]; then
    echo '[rabbitmq_management,rabbitmq_prometheus].' > "${ETC_DIR}/enabled_plugins"
    echo "Angelegt: ${ETC_DIR}/enabled_plugins"
fi

# Wird bei jedem Lauf neu geschrieben. Format ist sowohl für systemd
# (EnvironmentFile) als auch für "set -a; . rabbitmq.env" in der Shell gültig.
cat > "${ETC_DIR}/rabbitmq.env" <<EOF
PATH=${BASE}/erlang/current/bin:${BASE}/server/current/sbin:/usr/local/bin:/usr/bin:/bin
RABBITMQ_CONFIG_FILE=${ETC_DIR}/rabbitmq.conf
RABBITMQ_ENABLED_PLUGINS_FILE=${ETC_DIR}/enabled_plugins
RABBITMQ_MNESIA_BASE=${BASE}/data
RABBITMQ_LOG_BASE=${BASE}/log
EOF
echo "Geschrieben: ${ETC_DIR}/rabbitmq.env"

# ---------------------------------------------------------------- Cookie

COOKIE_FILE="$HOME/.erlang.cookie"
if [[ -n "${ERLANG_COOKIE:-}" ]]; then
    NEW_COOKIE="$ERLANG_COOKIE"
elif [[ -f "$COOKIE_FILE" ]]; then
    NEW_COOKIE=""
    echo "Erlang-Cookie vorhanden, unverändert: $COOKIE_FILE"
else
    echo
    echo "Für einen Cluster muss der Cookie auf allen Nodes identisch sein."
    read -rsp "Erlang-Cookie eingeben (leer = neuen erzeugen): " NEW_COOKIE || true
    echo
    if [[ -z "$NEW_COOKIE" ]]; then
        NEW_COOKIE=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32 || true)
        echo "Neuer Cookie erzeugt. Für weitere Nodes: cat $COOKIE_FILE"
    fi
fi
if [[ -n "$NEW_COOKIE" ]]; then
    [[ -f "$COOKIE_FILE" ]] && chmod 600 "$COOKIE_FILE"
    printf '%s' "$NEW_COOKIE" > "$COOKIE_FILE"
    chmod 400 "$COOKIE_FILE"
    echo "Geschrieben: $COOKIE_FILE"
fi

# ----------------------------------------------------------- systemd Unit

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/rabbitmq.service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=RabbitMQ Broker (User Unit)

[Service]
Type=notify
NotifyAccess=all
EnvironmentFile=${ETC_DIR}/rabbitmq.env
ExecStart=${BASE}/server/current/sbin/rabbitmq-server
ExecStop=${BASE}/server/current/sbin/rabbitmqctl shutdown
SuccessExitStatus=69
TimeoutStartSec=600
TimeoutStopSec=120
Restart=on-failure
RestartSec=10
LimitNOFILE=65536

[Install]
WantedBy=default.target
EOF
echo "Geschrieben: $UNIT_FILE"

echo
DO_ENABLE=${ENABLE:-}
if [[ -z "$DO_ENABLE" ]]; then
    read -rp "daemon-reload ausführen und rabbitmq.service aktivieren/(neu)starten? [J/n]: " DO_ENABLE || true
fi
DO_ENABLE=${DO_ENABLE:-J}
if [[ "$DO_ENABLE" =~ ^[JjYy]$ ]]; then
    systemctl --user daemon-reload
    systemctl --user enable rabbitmq.service
    systemctl --user restart rabbitmq.service
    systemctl --user --no-pager status rabbitmq.service || true
fi

echo
echo "Fertig."
echo "  Erlang:   $ERL_DIR"
echo "  RabbitMQ: $RMQ_DIR"
echo "  Daten:    ${BASE}/data"
echo "  Logs:     ${BASE}/log"
echo
echo "CLI in der Shell nutzen (z.B. in ~/.bashrc):"
echo "  set -a; . ${ETC_DIR}/rabbitmq.env; set +a"
echo "  rabbitmq-diagnostics status"
