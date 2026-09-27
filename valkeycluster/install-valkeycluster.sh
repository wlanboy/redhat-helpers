#!/usr/bin/env bash
#
# install-valkeycluster.sh
#
# Installiert mehrere Valkey-Instanzen im Cluster-Modus auf dieser VM als
# normaler User und richtet eine systemd Template-Unit ein:
#   - lädt valkey-<VALKEY_VERSION>-jammy-<arch>.tar.gz aus Nexus (Version
#     und Nexus aus ../valkey/versions.conf), prüft SHA256
#   - entpackt nach $VALKEY_BASE/server/, Symlink server/current auf die
#     neue Version (alle Instanzen nutzen dieselben Binaries)
#   - prüft per ldd, ob glibc, OpenSSL 3 und libsystemd passen
#   - legt etc/auth.conf (requirepass + masterauth, chmod 600) einmalig an:
#     VALKEY_PASSWORD, interaktiv oder zufällig erzeugt. Muss auf allen
#     VMs des Clusters identisch sein.
#   - legt pro Port aus VALKEY_PORTS instances/<port>/valkey.conf
#     (cluster-enabled yes) einmalig an, danach eigene Pflege
#   - schreibt ~/.config/systemd/user/valkey-cluster@.service (Type=notify)
#   - aktiviert valkey-cluster@<port> und startet die Instanzen
#     nacheinander neu, jeweils erst nach PONG der vorherigen (ohne
#     Nachfrage: ENABLE=j bzw. ENABLE=n)
#
# Den Cluster selbst bildet danach einmalig: cluster.sh create
#
# Verzeichnis: $VALKEY_BASE (Default: /opt/local/valkey). Muss vorher als
# root angelegt werden und dem User gehören.
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/../valkey/versions.conf"
. "$SCRIPT_DIR/cluster.conf"

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

read -ra PORTS <<< "$VALKEY_PORTS"
if [[ ${#PORTS[@]} -eq 0 ]]; then
    echo "Fehler: VALKEY_PORTS ist leer." >&2
    exit 1
fi
for p in "${PORTS[@]}"; do
    if [[ ! "$p" =~ ^[0-9]+$ ]] || (( p < 1024 || p > 55535 )); then
        echo "Fehler: Ungültiger Port '$p' (1024-55535, Cluster-Bus = Port + 10000)." >&2
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

BASE=${VALKEY_BASE:-/opt/local/valkey}
if [[ ! -d "$BASE" || ! -w "$BASE" ]]; then
    echo "Fehler: $BASE fehlt oder ist nicht beschreibbar. Als root:" >&2
    echo "  mkdir -p $BASE && chown $USER: $BASE" >&2
    exit 1
fi

DL_DIR="${BASE}/downloads"
SRV_DIR="${BASE}/server/${VALKEY_NAME}"
ETC_DIR="${BASE}/etc"

mkdir -p "$DL_DIR" "${BASE}/server" "$ETC_DIR" "${BASE}/instances" "${BASE}/log"

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
    echo "  In valkey/versions.conf eintragen: $ACTUAL_SHA"
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

echo "== Konfiguration in ${BASE} =="
AUTH_FILE="${ETC_DIR}/auth.conf"
if [[ ! -f "$AUTH_FILE" ]]; then
    NEW_PASS=${VALKEY_PASSWORD:-}
    if [[ -z "$NEW_PASS" ]]; then
        echo "Das Passwort muss auf allen VMs des Clusters identisch sein."
        read -rsp "Cluster-Passwort (leer = zufällig erzeugen): " NEW_PASS || true
        echo
    fi
    if [[ -z "$NEW_PASS" ]]; then
        NEW_PASS=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32 || true)
        echo "Neues Passwort erzeugt. Für weitere VMs: grep requirepass $AUTH_FILE"
    fi
    (umask 077; printf 'requirepass "%s"\nmasterauth "%s"\n' "$NEW_PASS" "$NEW_PASS" > "$AUTH_FILE")
    echo "Angelegt: $AUTH_FILE"
else
    echo "Vorhanden, unverändert: $AUTH_FILE"
fi

for p in "${PORTS[@]}"; do
    INST_DIR="${BASE}/instances/${p}"
    mkdir -p "${INST_DIR}/data"
    if [[ -f "${INST_DIR}/valkey.conf" ]]; then
        echo "Vorhanden, unverändert: ${INST_DIR}/valkey.conf"
        continue
    fi
    ANNOUNCE="# cluster-announce-ip 10.0.0.1"
    [[ -n "$VALKEY_ANNOUNCE_IP" ]] && ANNOUNCE="cluster-announce-ip ${VALKEY_ANNOUNCE_IP}"
    cat > "${INST_DIR}/valkey.conf" <<EOF
# https://valkey.io/topics/cluster-tutorial/
bind 0.0.0.0 -::*
port ${p}
protected-mode yes

daemonize no
supervised systemd

dir ${INST_DIR}/data
logfile ${BASE}/log/valkey-${p}.log
loglevel notice

cluster-enabled yes
# relativ zu dir, wird von Valkey selbst gepflegt, nicht von Hand ändern
cluster-config-file nodes.conf
cluster-node-timeout ${VALKEY_NODE_TIMEOUT}
${ANNOUNCE}

# Persistenz: RDB-Snapshots + AOF
save 3600 1 300 100 60 10000
appendonly yes
appendfsync everysec

# Speicherlimit pro Instanz
# maxmemory 1gb
# maxmemory-policy noeviction

include ${AUTH_FILE}
EOF
    echo "Angelegt: ${INST_DIR}/valkey.conf"
done

# Wird bei jedem Lauf neu geschrieben. Format ist sowohl für systemd
# (EnvironmentFile) als auch für "set -a; . valkey.env" in der Shell gültig.
cat > "${ETC_DIR}/valkey.env" <<EOF
PATH=${BASE}/server/current/bin:/usr/local/bin:/usr/bin:/bin
VALKEY_BASE=${BASE}
EOF
echo "Geschrieben: ${ETC_DIR}/valkey.env"

# ----------------------------------------------------------- systemd Unit

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/valkey-cluster@.service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Valkey Cluster Instanz Port %i (User Unit)

[Service]
Type=notify
EnvironmentFile=${ETC_DIR}/valkey.env
ExecStart=${BASE}/server/current/bin/valkey-server ${BASE}/instances/%i/valkey.conf
TimeoutStartSec=300
TimeoutStopSec=120
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=default.target
EOF
echo "Geschrieben: $UNIT_FILE"

# Wartet, bis die Instanz auf PING antwortet und nicht mehr lädt
wait_ready() {
    local port="$1"
    for _ in $(seq 1 60); do
        if [[ "$(VALKEYCLI_AUTH="$PASS" "${BASE}/server/current/bin/valkey-cli" \
                 -p "$port" ping 2>/dev/null)" == "PONG" ]] \
           && VALKEYCLI_AUTH="$PASS" "${BASE}/server/current/bin/valkey-cli" \
                 -p "$port" info persistence 2>/dev/null | grep -q '^loading:0'; then
            return 0
        fi
        sleep 2
    done
    return 1
}

echo
DO_ENABLE=${ENABLE:-}
if [[ -z "$DO_ENABLE" ]]; then
    read -rp "daemon-reload ausführen und Instanzen ${PORTS[*]} aktivieren/(neu)starten? [J/n]: " DO_ENABLE || true
fi
DO_ENABLE=${DO_ENABLE:-J}
if [[ "$DO_ENABLE" =~ ^[JjYy]$ ]]; then
    PASS=$(sed -n 's/^requirepass "\(.*\)"$/\1/p' "$AUTH_FILE")
    systemctl --user daemon-reload
    for p in "${PORTS[@]}"; do
        echo "-> valkey-cluster@${p}"
        systemctl --user enable "valkey-cluster@${p}.service"
        systemctl --user restart "valkey-cluster@${p}.service"
        if ! wait_ready "$p"; then
            echo "Fehler: Instanz ${p} antwortet nicht. Log: ${BASE}/log/valkey-${p}.log" >&2
            exit 1
        fi
    done
    systemctl --user --no-pager list-units 'valkey-cluster@*' || true
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
echo "  Valkey:     $SRV_DIR"
echo "  Instanzen:  ${BASE}/instances/{$(IFS=,; echo "${PORTS[*]}")}"
echo "  Logs:       ${BASE}/log/"
echo
echo "Wenn alle VMs installiert sind, Cluster einmalig bilden:"
echo "  VALKEY_CLUSTER_NODES=\"vm1:7001 vm2:7001 ...\" valkeycluster/cluster.sh create"
