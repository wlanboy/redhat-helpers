#!/usr/bin/env bash
#
# install-valkey.sh
#
# Installiert Valkey (fertiger Ubuntu-22.04-Build "jammy" von
# download.valkey.io, über einen Nexus Raw-Proxy) als normaler User und
# richtet eine systemd User Unit ein:
#   - lädt valkey-<VALKEY_VERSION>-jammy-<arch>.tar.gz aus Nexus, prüft
#     SHA256 (gegen VALKEY_SHA256 oder die .sha256-Datei aus dem Proxy)
#   - entpackt nach $VALKEY_BASE/server/, Symlink server/current auf die
#     neue Version (alte Versionen bleiben liegen, Upgrade = Skript mit
#     neuer Version)
#   - prüft per ldd, ob glibc, OpenSSL 3 und libsystemd passen
#   - legt etc/valkey.conf einmalig an, danach eigene Pflege
#   - legt etc/auth.conf (requirepass, chmod 600) einmalig an:
#     VALKEY_PASSWORD, interaktiv oder zufällig erzeugt
#   - schreibt etc/valkey.env (PATH, VALKEY_BASE) neu
#   - schreibt ~/.config/systemd/user/valkey.service (Type=notify) und
#     bietet daemon-reload + enable --now an (ohne Nachfrage: ENABLE=j bzw.
#     ENABLE=n)
#
# Verzeichnis: $VALKEY_BASE (Default: ~/valkey)
# Port:        $VALKEY_PORT (Default: 6379, nur beim ersten Anlegen der Config)
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/versions.conf"

if [[ $EUID -eq 0 ]]; then
    echo "Fehler: Bitte als normaler User ausführen, nicht als root." >&2
    exit 1
fi

for cmd in curl tar sha256sum ldd systemctl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Fehler: '$cmd' nicht gefunden." >&2
        exit 1
    fi
done

if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]]; then
    echo "Warnung: Lingering ist für '$USER' nicht aktiv. Valkey stoppt beim" >&2
    echo "         Logout und startet nicht beim Boot. Als root: loginctl enable-linger $USER" >&2
fi

if ! systemctl --user show-environment &>/dev/null; then
    echo "Fehler: systemctl --user nicht erreichbar (per SSH direkt als '$USER'" >&2
    echo "        einloggen, nicht su/sudo -u)." >&2
    exit 1
fi

BASE=${VALKEY_BASE:-$HOME/valkey}
PORT=${VALKEY_PORT:-6379}
DL_DIR="${BASE}/downloads"
SRV_DIR="${BASE}/server/${VALKEY_NAME}"
ETC_DIR="${BASE}/etc"

mkdir -p "$DL_DIR" "${BASE}/server" "$ETC_DIR" "${BASE}/data" "${BASE}/log"

download() {
    local url="$1" target="$2"
    if [[ -f "$target" ]]; then
        echo "Bereits vorhanden: $target"
        return
    fi
    curl -fL --netrc-optional -o "${target}.part" "$url"
    mv "${target}.part" "$target"
}

# ---------------------------------------------------------------- Download

echo "== Valkey ${VALKEY_VERSION} (${VALKEY_DISTRO}, ${VALKEY_ARCH}) =="
URL="${NEXUS_VALKEY}/releases/${VALKEY_TARBALL}"
download "$URL" "${DL_DIR}/${VALKEY_TARBALL}"

ACTUAL_SHA=$(sha256sum "${DL_DIR}/${VALKEY_TARBALL}" | awk '{print $1}')
if [[ -n "$VALKEY_SHA256" ]]; then
    EXPECTED_SHA="$VALKEY_SHA256"
else
    download "${URL}.sha256" "${DL_DIR}/${VALKEY_TARBALL}.sha256"
    EXPECTED_SHA=$(awk '{print $1}' "${DL_DIR}/${VALKEY_TARBALL}.sha256")
    echo "Hinweis: VALKEY_SHA256 nicht gesetzt, Prüfung gegen .sha256 aus dem Proxy."
    echo "  In versions.conf eintragen: $ACTUAL_SHA"
fi
if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
    echo "Fehler: SHA256 stimmt nicht." >&2
    echo "  erwartet: $EXPECTED_SHA" >&2
    echo "  gefunden: $ACTUAL_SHA" >&2
    rm -f "${DL_DIR}/${VALKEY_TARBALL}" "${DL_DIR}/${VALKEY_TARBALL}.sha256"
    exit 1
fi
echo "SHA256 ok."

if [[ -d "$SRV_DIR" ]]; then
    echo "Bereits entpackt: $SRV_DIR"
else
    tar -C "${BASE}/server" -xzf "${DL_DIR}/${VALKEY_TARBALL}"
fi

# ------------------------------------------------------------- Bibliotheken

SERVER_BIN="${SRV_DIR}/bin/valkey-server"
if [[ ! -x "$SERVER_BIN" ]]; then
    echo "Fehler: $SERVER_BIN nicht gefunden." >&2
    exit 1
fi
if ldd "$SERVER_BIN" 2>&1 | grep -q 'not found'; then
    echo "Fehler: valkey-server vermisst Bibliotheken:" >&2
    ldd "$SERVER_BIN" 2>&1 | grep 'not found' >&2
    echo "Benötigt: glibc >= 2.34, openssl-libs 3.x, systemd-libs, zlib, libzstd" >&2
    exit 1
fi
"$SERVER_BIN" --version
ln -sfn "$SRV_DIR" "${BASE}/server/current"

# ------------------------------------------------------------ Konfiguration

echo "== Konfiguration in ${ETC_DIR} =="
if [[ ! -f "${ETC_DIR}/auth.conf" ]]; then
    NEW_PASS=${VALKEY_PASSWORD:-}
    if [[ -z "$NEW_PASS" ]]; then
        read -rsp "Passwort für Valkey (leer = zufällig erzeugen): " NEW_PASS || true
        echo
    fi
    if [[ -z "$NEW_PASS" ]]; then
        NEW_PASS=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32 || true)
        echo "Neues Passwort erzeugt: grep requirepass ${ETC_DIR}/auth.conf"
    fi
    (umask 077; printf 'requirepass "%s"\n' "$NEW_PASS" > "${ETC_DIR}/auth.conf")
    echo "Angelegt: ${ETC_DIR}/auth.conf"
else
    echo "Vorhanden, unverändert: ${ETC_DIR}/auth.conf"
fi

if [[ ! -f "${ETC_DIR}/valkey.conf" ]]; then
    cat > "${ETC_DIR}/valkey.conf" <<EOF
# https://valkey.io/topics/valkey.conf/
bind 0.0.0.0 -::*
port ${PORT}
protected-mode yes

daemonize no
supervised systemd

dir ${BASE}/data
logfile ${BASE}/log/valkey.log
loglevel notice

# Persistenz: RDB-Snapshots + AOF
save 3600 1 300 100 60 10000
appendonly yes
appendfsync everysec

# Speicherlimit, bei Cache-Betrieb z.B. allkeys-lru
# maxmemory 2gb
# maxmemory-policy noeviction

include ${ETC_DIR}/auth.conf
EOF
    echo "Angelegt: ${ETC_DIR}/valkey.conf"
else
    echo "Vorhanden, unverändert: ${ETC_DIR}/valkey.conf"
fi

# Wird bei jedem Lauf neu geschrieben. Format ist sowohl für systemd
# (EnvironmentFile) als auch für "set -a; . valkey.env" in der Shell gültig.
cat > "${ETC_DIR}/valkey.env" <<EOF
PATH=${BASE}/server/current/bin:/usr/local/bin:/usr/bin:/bin
VALKEY_BASE=${BASE}
EOF
echo "Geschrieben: ${ETC_DIR}/valkey.env"

# ----------------------------------------------------------- systemd Unit

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/valkey.service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Valkey Server (User Unit)

[Service]
Type=notify
EnvironmentFile=${ETC_DIR}/valkey.env
ExecStart=${BASE}/server/current/bin/valkey-server ${ETC_DIR}/valkey.conf
TimeoutStartSec=120
TimeoutStopSec=120
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=default.target
EOF
echo "Geschrieben: $UNIT_FILE"

echo
DO_ENABLE=${ENABLE:-}
if [[ -z "$DO_ENABLE" ]]; then
    read -rp "daemon-reload ausführen und valkey.service aktivieren/(neu)starten? [J/n]: " DO_ENABLE || true
fi
DO_ENABLE=${DO_ENABLE:-J}
if [[ "$DO_ENABLE" =~ ^[JjYy]$ ]]; then
    systemctl --user daemon-reload
    systemctl --user enable valkey.service
    systemctl --user restart valkey.service
    systemctl --user --no-pager status valkey.service || true
fi

# Kernel-Einstellungen, die Valkey beim Start anmahnt (nur root kann sie setzen)
if [[ "$(cat /proc/sys/vm/overcommit_memory 2>/dev/null)" != "1" ]]; then
    echo
    echo "Hinweis: vm.overcommit_memory ist nicht 1 (Hintergrund-Saves können"
    echo "         fehlschlagen). Als root:"
    echo "  echo 'vm.overcommit_memory = 1' > /etc/sysctl.d/90-valkey.conf && sysctl --system"
fi

echo
echo "Fertig."
echo "  Valkey: $SRV_DIR"
echo "  Daten:  ${BASE}/data"
echo "  Logs:   ${BASE}/log/valkey.log"
echo
echo "CLI in der Shell nutzen (z.B. in ~/.bashrc):"
echo "  set -a; . ${ETC_DIR}/valkey.env; set +a"
echo "  valkey-cli -p ${PORT} --askpass ping"
