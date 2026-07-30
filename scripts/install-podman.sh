#!/usr/bin/env bash
#
# install-podman.sh
#
# Installiert Podman auf RHEL 9 und richtet ihn für den Betrieb mit einer
# internen Nexus/Artifactory Registry ein:
#   - installiert podman + policycoreutils-python-utils (für SELinux-Labeling)
#   - fragt interaktiv nach der Nexus/Artifactory Registry-URL
#   - fragt, ob insecure SSL (HTTP / selbstsigniertes Zertifikat) für diese
#     Registry aktiviert werden soll
#   - fragt, ob die Registry als unqualified-search-registry eingetragen wird
#     (damit "podman pull image:tag" ohne vollen Pfad funktioniert)
#   - fragt nach einem Basisverzeichnis für Container/Images/Volumes
#     (Default: /opt/local/podman) und setzt graphroot in storage.conf
#   - setzt SELinux-Kontext (container_var_lib_t) auf das Storage-Verzeichnis
#
# Muss als root ausgeführt werden.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Fehler: Dieses Skript muss als root ausgeführt werden." >&2
    exit 1
fi

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    if [[ "${PLATFORM_ID:-}" != "platform:el9" ]]; then
        echo "Warnung: Es wurde kein RHEL 9 erkannt (gefunden: ${PRETTY_NAME:-unbekannt}). Fahre trotzdem fort." >&2
    fi
fi

echo "== Installiere Podman =="
dnf install -y podman policycoreutils-python-utils

echo "== Installiere podman-compose =="
if ! dnf install -y podman-compose 2>/dev/null; then
    echo "podman-compose nicht in aktivierten Repos gefunden (RHEL 9 Base/AppStream enthält es nicht), installiere per pip3."
    dnf install -y python3-pip
    pip3 install podman-compose
fi

REGISTRIES_CONF_D="/etc/containers/registries.conf.d"
REGISTRIES_CONF="/etc/containers/registries.conf"
STORAGE_CONF="/etc/containers/storage.conf"

mkdir -p "$REGISTRIES_CONF_D"

echo
read -rp "Nexus/Artifactory Registry-URL (z.B. nexus.example.com:5000): " REGISTRY_URL

if [[ -z "$REGISTRY_URL" ]]; then
    echo "Keine Registry-URL angegeben, überspringe Registry-Konfiguration."
else
    read -rp "Insecure SSL (HTTP/selbstsigniertes Zertifikat) für '$REGISTRY_URL' aktivieren? [J/n]: " INSECURE
    INSECURE=${INSECURE:-J}

    SAFE_NAME=$(echo "$REGISTRY_URL" | tr -c '[:alnum:]' '_')
    REGISTRY_CONF_FILE="${REGISTRIES_CONF_D}/990-${SAFE_NAME}.conf"

    {
        echo "[[registry]]"
        echo "location = \"${REGISTRY_URL}\""
        if [[ "$INSECURE" =~ ^[JjYy]$ ]]; then
            echo "insecure = true"
        fi
    } > "$REGISTRY_CONF_FILE"

    echo "Registry-Konfiguration geschrieben nach: $REGISTRY_CONF_FILE"

    read -rp "'$REGISTRY_URL' als unqualified-search-registry eintragen (Nutzung ohne vollen Pfad, z.B. 'podman pull image:tag')? [j/N]: " ADD_SEARCH
    if [[ "$ADD_SEARCH" =~ ^[JjYy]$ ]]; then
        if [[ ! -f "$REGISTRIES_CONF" ]]; then
            echo 'unqualified-search-registries = []' > "$REGISTRIES_CONF"
        fi
        if grep -q '^unqualified-search-registries' "$REGISTRIES_CONF"; then
            if ! grep -q "\"${REGISTRY_URL}\"" "$REGISTRIES_CONF"; then
                sed -i "s|^unqualified-search-registries = \[\(.*\)\]|unqualified-search-registries = [\"${REGISTRY_URL}\"\1]|" "$REGISTRIES_CONF"
            fi
        else
            echo "unqualified-search-registries = [\"${REGISTRY_URL}\"]" >> "$REGISTRIES_CONF"
        fi
        echo "'${REGISTRY_URL}' als unqualified-search-registry eingetragen."
    fi
fi

echo
read -rp "Basisverzeichnis für Container/Images/Volumes [/opt/local/podman]: " STORAGE_BASE
STORAGE_BASE=${STORAGE_BASE:-/opt/local/podman}
GRAPHROOT="${STORAGE_BASE}/storage"

mkdir -p "$GRAPHROOT"

FSTYPE=$(findmnt -no FSTYPE --target "$GRAPHROOT" 2>/dev/null || echo "unbekannt")
case "$FSTYPE" in
    nfs|nfs4|cifs|smb3)
        echo "Warnung: '$STORAGE_BASE' liegt auf einem Netzwerk-Dateisystem (${FSTYPE})." >&2
        echo "Der overlay-Storage-Treiber von Podman unterstützt kein NFS/CIFS (fehlende d_type/xattr-Semantik)." >&2
        read -rp "Trotzdem fortfahren? [j/N]: " NFS_CONFIRM
        if ! [[ "$NFS_CONFIRM" =~ ^[JjYy]$ ]]; then
            echo "Abgebrochen." >&2
            exit 1
        fi
        ;;
esac

if [[ -f "$STORAGE_CONF" ]]; then
    cp "$STORAGE_CONF" "${STORAGE_CONF}.bak.$(date +%Y%m%d%H%M%S)"
fi

if grep -q '^graphroot' "$STORAGE_CONF" 2>/dev/null; then
    sed -i "s|^graphroot.*|graphroot = \"${GRAPHROOT}\"|" "$STORAGE_CONF"
elif grep -q '^#.*graphroot' "$STORAGE_CONF" 2>/dev/null; then
    sed -i "s|^#.*graphroot.*|graphroot = \"${GRAPHROOT}\"|" "$STORAGE_CONF"
elif grep -q '^\[storage\]' "$STORAGE_CONF" 2>/dev/null; then
    sed -i "/^\[storage\]/a graphroot = \"${GRAPHROOT}\"" "$STORAGE_CONF"
else
    printf '[storage]\ndriver = "overlay"\ngraphroot = "%s"\n' "$GRAPHROOT" >> "$STORAGE_CONF"
fi

echo "graphroot gesetzt auf: $GRAPHROOT"

if command -v semanage &>/dev/null; then
    semanage fcontext -a -t container_var_lib_t "${STORAGE_BASE}(/.*)?" 2>/dev/null || true
    restorecon -Rv "$STORAGE_BASE"
else
    echo "Warnung: semanage nicht gefunden, SELinux-Kontext wurde nicht gesetzt." >&2
fi

echo
echo "Fertig."
echo "  Podman Version:      $(podman --version)"
echo "  Compose Version:     $(podman-compose --version 2>/dev/null || echo 'nicht gefunden')"
if [[ -n "${REGISTRY_URL:-}" ]]; then
    echo "  Registry:            $REGISTRY_URL (insecure: ${INSECURE:-nein})"
fi
echo "  Storage (graphroot): $GRAPHROOT"
echo
echo "Test: podman info --format '{{.Store.GraphRoot}}'"
