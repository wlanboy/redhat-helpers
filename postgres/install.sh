#!/usr/bin/env bash
#
# install.sh
#
# Installiert PostgreSQL auf einer Runtime-VM als normaler User und richtet
# eine systemd User Unit ein:
#   - lädt postgresql-<PG_VERSION>-el9-<arch>.tar.gz (erzeugt von build.sh)
#     aus dem Nexus Raw-Hosted-Repo und prüft den SHA256
#   - entpackt nach $PG_BASE/server/postgresql-<PG_VERSION>/, Symlink
#     server/current auf die neue Version (alte Versionen bleiben liegen,
#     Minor-Upgrade = Skript mit neuer Version)
#   - prüft per ldd, ob alle System-Bibliotheken vorhanden sind
#   - initdb einmalig nach data/<MAJOR> (Passwort: PG_PASSWORD, interaktiv
#     oder zufällig erzeugt, landet in ~/.pgpass)
#   - legt etc/postgresql.conf und etc/pg_hba.conf einmalig an, danach
#     eigene Pflege
#   - schreibt etc/postgres.env (PATH, LD_LIBRARY_PATH, PGHOST, ...) neu
#   - schreibt ~/.config/systemd/user/postgres.service (Type=notify) und
#     bietet daemon-reload + enable --now an (ohne Nachfrage: ENABLE=j bzw.
#     ENABLE=n)
#
# Verzeichnis: $PG_BASE (Default: ~/postgres)
# Port:        $PG_PORT (Default: 5432, nur beim ersten Anlegen der Config)
# Superuser:   $PG_SUPERUSER (Default: postgres, nur bei initdb)
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/versions.conf"

if [[ $EUID -eq 0 ]]; then
    echo "Fehler: Bitte als normaler User ausführen, nicht als root." >&2
    exit 1
fi

for cmd in curl tar gzip sha256sum ldd systemctl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Fehler: '$cmd' nicht gefunden." >&2
        exit 1
    fi
done

if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]]; then
    echo "Warnung: Lingering ist für '$USER' nicht aktiv. PostgreSQL stoppt beim" >&2
    echo "         Logout und startet nicht beim Boot. Als root: loginctl enable-linger $USER" >&2
fi

if ! systemctl --user show-environment &>/dev/null; then
    echo "Fehler: systemctl --user nicht erreichbar (per SSH direkt als '$USER'" >&2
    echo "        einloggen, nicht su/sudo -u)." >&2
    exit 1
fi

BASE=${PG_BASE:-$HOME/postgres}
PORT=${PG_PORT:-5432}
SUPERUSER=${PG_SUPERUSER:-postgres}
DL_DIR="${BASE}/downloads"
SRV_DIR="${BASE}/server/${PG_NAME}"
ETC_DIR="${BASE}/etc"
DATA_DIR="${BASE}/data/${PG_MAJOR}"
RUN_DIR="${BASE}/run"

mkdir -p "$DL_DIR" "${BASE}/server" "$ETC_DIR" "${BASE}/data" "${BASE}/log" "$RUN_DIR"
chmod 700 "${BASE}/data"

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

echo "== PostgreSQL ${PG_VERSION} (${PG_ARCH}) =="
PG_URL="${NEXUS_BUILDS}/postgresql/${PG_VERSION}/${PG_TARBALL}"
download "$PG_URL" "${DL_DIR}/${PG_TARBALL}"
download "${PG_URL}.sha256" "${DL_DIR}/${PG_TARBALL}.sha256"
if ! (cd "$DL_DIR" && sha256sum -c "${PG_TARBALL}.sha256"); then
    echo "Fehler: SHA256 stimmt nicht, Download wird gelöscht." >&2
    rm -f "${DL_DIR}/${PG_TARBALL}" "${DL_DIR}/${PG_TARBALL}.sha256"
    exit 1
fi

if [[ -d "$SRV_DIR" ]]; then
    echo "Bereits entpackt: $SRV_DIR"
else
    # Erst in ein temporäres Verzeichnis, damit ein abgebrochenes Entpacken
    # kein halbes server/postgresql-<VERSION> hinterlässt.
    TMP_DIR=$(mktemp -d "${BASE}/server/.extract.XXXXXX")
    trap 'rm -rf "$TMP_DIR"' EXIT
    tar -C "$TMP_DIR" -xzf "${DL_DIR}/${PG_TARBALL}"
    mv "${TMP_DIR}/${PG_NAME}" "$SRV_DIR"
    rm -rf "$TMP_DIR"
    trap - EXIT
fi
[[ -f "${SRV_DIR}/BUILD_INFO" ]] && grep -E '^(PG_RPM_RELEASE|BUILD_DATE)=' "${SRV_DIR}/BUILD_INFO"

# ------------------------------------------------------------- Bibliotheken

SERVER_BIN="${SRV_DIR}/bin/postgres"
if [[ ! -x "$SERVER_BIN" ]]; then
    echo "Fehler: $SERVER_BIN nicht gefunden." >&2
    exit 1
fi
# Ohne RPATH: libpq kommt über LD_LIBRARY_PATH aus dem eigenen lib/.
# Ausgenommen sind contrib-Module, die nur mit plperl/plpython nutzbar sind
# (nicht installiert), und xml2 (braucht libxslt).
MISSING_LIBS=$(
    for f in "${SRV_DIR}"/bin/* "${SRV_DIR}"/lib/*.so*; do
        case "$(basename "$f")" in
            *plperl*|*plpython*|pgxml.so) continue ;;
        esac
        LD_LIBRARY_PATH="${SRV_DIR}/lib" ldd "$f" 2>/dev/null | awk '/not found/{print $1}'
    done | sort -u
)
if [[ -n "$MISSING_LIBS" ]]; then
    echo "Fehler: Es fehlen System-Bibliotheken:" >&2
    echo "$MISSING_LIBS" | sed 's/^/  /' >&2
    echo "Als root: dnf install -y libicu numactl-libs liburing" >&2
    exit 1
fi
"$SERVER_BIN" --version

# ------------------------------------------------------------ Major-Version

# Liegt nur ein Cluster einer anderen Major-Version vor, ist das ein
# Major-Upgrade. Das braucht pg_upgrade und wird nicht automatisch gemacht.
if [[ ! -f "${DATA_DIR}/PG_VERSION" ]]; then
    for v in "${BASE}"/data/*/PG_VERSION; do
        [[ -f "$v" ]] || continue
        OLD_MAJOR=$(cat "$v")
        echo "Fehler: Vorhandener Cluster in ${BASE}/data/${OLD_MAJOR} (PostgreSQL ${OLD_MAJOR})." >&2
        echo "        Wechsel auf ${PG_MAJOR} ist ein Major-Upgrade mit pg_upgrade," >&2
        echo "        Ablauf siehe readme.md, Abschnitt \"Major-Upgrade\"." >&2
        echo "        Neue Version ist bereits entpackt: $SRV_DIR" >&2
        exit 1
    done
fi

ln -sfn "$SRV_DIR" "${BASE}/server/current"

# ------------------------------------------------------------ Konfiguration

echo "== Konfiguration in ${ETC_DIR} =="

# Wird bei jedem Lauf neu geschrieben. Format ist sowohl für systemd
# (EnvironmentFile) als auch für "set -a; . postgres.env" in der Shell gültig.
cat > "${ETC_DIR}/postgres.env" <<EOF
PATH=${BASE}/server/current/bin:/usr/local/bin:/usr/bin:/bin
LD_LIBRARY_PATH=${BASE}/server/current/lib
PGDATA=${DATA_DIR}
PGHOST=${RUN_DIR}
PGPORT=${PORT}
PGUSER=${SUPERUSER}
PGDATABASE=postgres
PG_BASE=${BASE}
EOF
echo "Geschrieben: ${ETC_DIR}/postgres.env"

if [[ ! -f "${ETC_DIR}/postgresql.conf" ]]; then
    cat > "${ETC_DIR}/postgresql.conf" <<EOF
# Eigene Einstellungen, eingebunden am Ende von data/<MAJOR>/postgresql.conf.
# Die Datei von initdb bleibt die Basis (max_connections, shared_buffers,
# Zeitzone), Werte hier überschreiben sie.
# https://www.postgresql.org/docs/current/runtime-config.html

listen_addresses = '*'
port = ${PORT}
unix_socket_directories = '${RUN_DIR}'

hba_file = '${ETC_DIR}/pg_hba.conf'
ident_file = '${ETC_DIR}/pg_ident.conf'
password_encryption = scram-sha-256

logging_collector = on
log_directory = '${BASE}/log'
log_filename = 'postgresql-%a.log'
log_rotation_age = 1d
log_rotation_size = 0
log_truncate_on_rotation = on
log_line_prefix = '%m [%p] %q%u@%d '

shared_preload_libraries = 'pg_stat_statements'

# Speicher, Richtwerte: shared_buffers ~25 % RAM, effective_cache_size ~75 %
# shared_buffers = 2GB
# effective_cache_size = 6GB
# maintenance_work_mem = 512MB
# work_mem = 16MB
EOF
    echo "Angelegt: ${ETC_DIR}/postgresql.conf"
else
    echo "Vorhanden, unverändert: ${ETC_DIR}/postgresql.conf"
fi

if [[ ! -f "${ETC_DIR}/pg_hba.conf" ]]; then
    cat > "${ETC_DIR}/pg_hba.conf" <<'EOF'
# https://www.postgresql.org/docs/current/auth-pg-hba-conf.html
# TYPE  DATABASE     USER  ADDRESS       METHOD
local   all          all                 scram-sha-256
host    all          all   127.0.0.1/32  scram-sha-256
host    all          all   ::1/128       scram-sha-256
host    all          all   0.0.0.0/0     scram-sha-256
host    all          all   ::/0          scram-sha-256

local   replication  all                 scram-sha-256
host    replication  all   0.0.0.0/0     scram-sha-256
host    replication  all   ::/0          scram-sha-256
EOF
    echo "Angelegt: ${ETC_DIR}/pg_hba.conf"
else
    echo "Vorhanden, unverändert: ${ETC_DIR}/pg_hba.conf"
fi

if [[ ! -f "${ETC_DIR}/pg_ident.conf" ]]; then
    cat > "${ETC_DIR}/pg_ident.conf" <<'EOF'
# https://www.postgresql.org/docs/current/auth-username-maps.html
# MAPNAME       SYSTEM-USERNAME         PG-USERNAME
EOF
    echo "Angelegt: ${ETC_DIR}/pg_ident.conf"
fi

# ------------------------------------------------------------------ initdb

if [[ ! -f "${DATA_DIR}/PG_VERSION" ]]; then
    echo "== initdb ${DATA_DIR} =="
    NEW_PASS=${PG_PASSWORD:-}
    if [[ -z "$NEW_PASS" ]]; then
        read -rsp "Passwort für DB-User '${SUPERUSER}' (leer = zufällig erzeugen): " NEW_PASS || true
        echo
    fi
    if [[ -z "$NEW_PASS" ]]; then
        NEW_PASS=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32 || true)
        echo "Neues Passwort erzeugt, steht in ~/.pgpass."
    fi
    PW_FILE=$(mktemp)
    trap 'rm -f "$PW_FILE"' EXIT
    (umask 077; printf '%s\n' "$NEW_PASS" > "$PW_FILE")

    # builtin-Provider (ab PostgreSQL 17): Sortierung hängt nicht von glibc-
    # oder ICU-Updates ab, Indizes bleiben nach OS-Updates gültig.
    # --data-checksums explizit (Default erst ab 18), pg_upgrade verlangt
    # bei altem und neuem Cluster denselben Stand.
    LD_LIBRARY_PATH="${SRV_DIR}/lib" "${SRV_DIR}/bin/initdb" \
        -D "$DATA_DIR" \
        -U "$SUPERUSER" \
        --pwfile="$PW_FILE" \
        --auth=scram-sha-256 \
        --data-checksums \
        -E UTF8 \
        --locale-provider=builtin \
        --locale=C.UTF-8
    rm -f "$PW_FILE"
    trap - EXIT

    # ~/.pgpass: eine Zeile für den Socket (Host = Socket-Verzeichnis, weil
    # es nicht der Default ist) und eine für TCP auf localhost.
    # ':' und '\' müssen escaped werden.
    PGPASS="$HOME/.pgpass"
    esc() { printf '%s' "$1" | sed 's/[\\:]/\\&/g'; }
    touch "$PGPASS"
    chmod 600 "$PGPASS"
    grep -vF -e "$(esc "$RUN_DIR"):${PORT}:*:${SUPERUSER}:" \
             -e "localhost:${PORT}:*:${SUPERUSER}:" "$PGPASS" > "${PGPASS}.new" || true
    for h in "$RUN_DIR" localhost; do
        printf '%s:%s:*:%s:%s\n' "$(esc "$h")" "$PORT" "$SUPERUSER" "$(esc "$NEW_PASS")" >> "${PGPASS}.new"
    done
    chmod 600 "${PGPASS}.new"
    mv "${PGPASS}.new" "$PGPASS"
    echo "Eingetragen: $PGPASS"
else
    echo "Cluster vorhanden: ${DATA_DIR}"
fi

# etc/postgresql.conf einbinden (idempotent, auch für Cluster aus pg_upgrade).
# pg_hba.conf/pg_ident.conf von initdb entfernen, gelesen wird etc/.
INCLUDE_LINE="include '${ETC_DIR}/postgresql.conf'"
if ! grep -qxF "$INCLUDE_LINE" "${DATA_DIR}/postgresql.conf"; then
    printf '\n# install.sh: eigene Einstellungen\n%s\n' "$INCLUDE_LINE" \
        >> "${DATA_DIR}/postgresql.conf"
    echo "Include ergänzt: ${DATA_DIR}/postgresql.conf"
fi
rm -f "${DATA_DIR}/pg_hba.conf" "${DATA_DIR}/pg_ident.conf"

# ----------------------------------------------------------- systemd Unit

UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/postgres.service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=PostgreSQL ${PG_MAJOR} (User Unit)

[Service]
Type=notify
EnvironmentFile=${ETC_DIR}/postgres.env
ExecStart=${BASE}/server/current/bin/postgres -D ${DATA_DIR}
ExecReload=/bin/kill -HUP \$MAINPID
# SIGINT = Fast Shutdown, laufende Transaktionen werden abgebrochen.
# mixed: nur der Postmaster bekommt das Signal, er beendet die Backends.
KillMode=mixed
KillSignal=SIGINT
# Crash-Recovery und Checkpoint beim Stoppen können lange dauern
TimeoutSec=0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
echo "Geschrieben: $UNIT_FILE"

echo
DO_ENABLE=${ENABLE:-}
if [[ -z "$DO_ENABLE" ]]; then
    read -rp "daemon-reload ausführen und postgres.service aktivieren/(neu)starten? [J/n]: " DO_ENABLE || true
fi
DO_ENABLE=${DO_ENABLE:-J}
if [[ "$DO_ENABLE" =~ ^[JjYy]$ ]]; then
    systemctl --user daemon-reload
    systemctl --user enable postgres.service
    systemctl --user restart postgres.service
    systemctl --user --no-pager status postgres.service || true
fi

echo
echo "Fertig."
echo "  PostgreSQL: $SRV_DIR"
echo "  Daten:      $DATA_DIR"
echo "  Logs:       ${BASE}/log/"
echo
echo "psql in der Shell nutzen (z.B. in ~/.bashrc):"
echo "  set -a; . ${ETC_DIR}/postgres.env; set +a"
echo "  psql -c 'select version()'"
