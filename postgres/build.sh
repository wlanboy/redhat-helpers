#!/usr/bin/env bash
#
# build.sh
#
# Erzeugt auf der Build-VM aus den fertigen PGDG-RPMs für RHEL 9 einen
# verschiebbaren Tarball für die Runtime-VMs (kein Compilieren):
#   - lädt postgresql<MAJOR>{,-libs,-server,-contrib}-<PG_VERSION>-<PG_RPM_RELEASE>
#     über den Nexus-Proxy auf download.postgresql.org und prüft die
#     RPM-Signatur gegen den PGDG-Key (User-eigene rpm-Datenbank, kein root)
#   - entpackt die RPMs per rpm2archive (ohne sie zu installieren) und
#     übernimmt nur /usr/pgsql-<MAJOR> als postgresql-<PG_VERSION>/
#   - schreibt BUILD_INFO (Version, RPM-Release, SHA256 der RPMs)
#   - packt postgresql-<PG_VERSION>-el9-<arch>.tar.gz inkl. .sha256
#   - bietet den Upload in das Nexus Raw-Hosted-Repo an
#     (ohne Nachfrage: UPLOAD=j bzw. UPLOAD=n)
#
# Arbeitsverzeichnis: $WORK_DIR (Default: ~/build/postgres)
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/versions.conf"

if [[ $EUID -eq 0 ]]; then
    echo "Fehler: Bitte als normaler User ausführen, nicht als root." >&2
    exit 1
fi

for cmd in curl tar gzip sha256sum rpm2archive rpmkeys; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Fehler: '$cmd' nicht gefunden." >&2
        exit 1
    fi
done

WORK_DIR=${WORK_DIR:-$HOME/build/postgres}
DL_DIR="${WORK_DIR}/downloads"
STAGE_DIR="${WORK_DIR}/release"
RELEASE_DIR="${STAGE_DIR}/${PG_NAME}"
DIST_DIR="${WORK_DIR}/dist"

mkdir -p "$DL_DIR" "$STAGE_DIR" "$DIST_DIR"

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

echo "== PostgreSQL ${PG_VERSION} (${PG_RPM_RELEASE}, ${PG_ARCH}) =="
RPMS=()
for pkg in "${PG_PACKAGES[@]}"; do
    rpm_file="${pkg}-${PG_VERSION}-${PG_RPM_RELEASE}.${PG_ARCH}.rpm"
    download "${NEXUS_PGDG}/${PG_RPM_PATH}/${rpm_file}" "${DL_DIR}/${rpm_file}"
    RPMS+=("${DL_DIR}/${rpm_file}")
done

if [[ "$PG_GPG_CHECK" =~ ^[JjYy]$ ]]; then
    KEY_FILE="${DL_DIR}/$(basename "$PG_GPG_KEY_PATH")"
    download "${NEXUS_PGDG}/${PG_GPG_KEY_PATH}" "$KEY_FILE"
    # Eigene rpm-Datenbank nur für die Prüfung, die System-DB bleibt unberührt
    KEY_DB="${DL_DIR}/rpmdb"
    rm -rf "$KEY_DB"
    mkdir -p "$KEY_DB"
    rpmkeys --dbpath "$KEY_DB" --import "$KEY_FILE"
    for f in "${RPMS[@]}"; do
        if ! rpmkeys --dbpath "$KEY_DB" -K "$f"; then
            echo "Fehler: Signatur von $(basename "$f") ungültig, Datei wird gelöscht." >&2
            rm -f "$f"
            exit 1
        fi
    done
    rm -rf "$KEY_DB"
    echo "Signaturen ok."
else
    echo "Warnung: PG_GPG_CHECK=n, keine Signaturprüfung." >&2
fi

# ---------------------------------------------------------------- Entpacken

echo "== Entpacke nach ${RELEASE_DIR} =="
rm -rf "$RELEASE_DIR"
TMP_DIR=$(mktemp -d "${STAGE_DIR}/.extract.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
for f in "${RPMS[@]}"; do
    rpm2archive - < "$f" | tar -C "$TMP_DIR" -xz
done
# Die RPMs installieren nach /usr/pgsql-<MAJOR>. PostgreSQL findet share/
# und lib/ relativ zum Binary, das Verzeichnis ist daher verschiebbar.
# Alles außerhalb (systemd-Units, /usr/bin-Links) wird verworfen.
mv "${TMP_DIR}/usr/pgsql-${PG_MAJOR}" "$RELEASE_DIR"
rm -rf "$TMP_DIR"
trap - EXIT

for f in bin/postgres bin/initdb bin/pg_ctl bin/psql bin/pg_upgrade; do
    if [[ ! -x "${RELEASE_DIR}/${f}" ]]; then
        echo "Fehler: ${RELEASE_DIR}/${f} fehlt." >&2
        exit 1
    fi
done

{
    echo "PG_VERSION=${PG_VERSION}"
    echo "PG_RPM_RELEASE=${PG_RPM_RELEASE}"
    echo "PG_ARCH=${PG_ARCH}"
    echo "PG_GPG_CHECK=${PG_GPG_CHECK}"
    echo "BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "BUILD_HOST=$(hostname -s)"
    echo "# SHA256 der Quell-RPMs"
    (cd "$DL_DIR" && sha256sum "${RPMS[@]##*/}")
} > "${RELEASE_DIR}/BUILD_INFO"

# ------------------------------------------------------------------ Packen

echo "== Packe Tarball =="
tar -C "$STAGE_DIR" -czf "${DIST_DIR}/${PG_TARBALL}" "$PG_NAME"
(cd "$DIST_DIR" && sha256sum "$PG_TARBALL" > "${PG_TARBALL}.sha256")

echo
echo "Erzeugt:"
echo "  ${DIST_DIR}/${PG_TARBALL}"
echo "  ${DIST_DIR}/${PG_TARBALL}.sha256"

UPLOAD_BASE="${NEXUS_BUILDS}/postgresql/${PG_VERSION}"
echo
DO_UPLOAD=${UPLOAD:-}
if [[ -z "$DO_UPLOAD" ]]; then
    read -rp "Nach '${UPLOAD_BASE}/' hochladen? [j/N]: " DO_UPLOAD || true
fi
if [[ "$DO_UPLOAD" =~ ^[JjYy]$ ]]; then
    AUTH=(--netrc-optional)
    if [[ -n "${NEXUS_USER:-}" ]]; then
        AUTH=(-u "$NEXUS_USER")
    fi
    for f in "$PG_TARBALL" "${PG_TARBALL}.sha256"; do
        echo "-> $f"
        curl -fsS "${AUTH[@]}" --upload-file "${DIST_DIR}/${f}" "${UPLOAD_BASE}/${f}"
    done
    echo "Upload fertig."
fi
