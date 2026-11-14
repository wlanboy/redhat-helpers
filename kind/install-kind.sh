#!/usr/bin/env bash
#
# install-kind.sh
#
# Richtet auf RHEL 9 mit Podman als Container-Runtime einen kind-Cluster
# (Kubernetes IN Docker) für den Einsatz mit einer internen Nexus/Artifactory
# Registry ein:
#   - prüft, ob Podman bereits installiert ist (siehe scripts/install-podman.sh)
#   - prüft cgroup v2 (Pflicht für den Podman-Provider von kind)
#   - prüft, ob kind/kubectl bereits vorhanden sind (PATH oder /usr/local/bin)
#   - fehlt kind, wird zuerst ein separat bereitgestelltes Binary
#     kind-linux-<arch> in diesem Ordner verwendet (Offline-Installation).
#     Es wird gegen den Hash in kind.sha256 geprüft und nur bei Übereinstimmung
#     installiert. Ohne lokales Binary wird kind per curl geladen und gegen die
#     .sha256sum des Releases geprüft.
#   - fehlt kubectl, wird es per curl geladen und gegen die .sha256 des
#     Releases geprüft
#   - erstellt optional direkt den Cluster (KIND_EXPERIMENTAL_PROVIDER=podman)
#     aus kind-cluster-config.yaml
#
# Umgebungsvariablen:
#   KIND_VERSION      kind-Version für den Download (Default: v0.32.0, passend
#                     zu kind.sha256)
#   KUBECTL_VERSION   kubectl-Version, z. B. v1.37.2 (Default: aktuelle stable)
#   KIND_BASE_URL     Basis-URL für kind (Default: https://kind.sigs.k8s.io/dl),
#                     z. B. ein Nexus/Artifactory Raw-Proxy
#   KUBECTL_BASE_URL  Basis-URL für kubectl (Default: https://dl.k8s.io/release)
#
# kind-cluster-config.yaml wird NICHT vom Skript generiert, sondern als
# eigene Datei in diesem Ordner gepflegt (Nexus/Artifactory registry-mirror
# Einträge dort direkt anpassen).
#
# Muss als root ausgeführt werden (Installation nach /usr/local/bin, falls
# nötig). Der Cluster selbst wird mit rootful Podman erstellt.

set -euo pipefail

KIND_VERSION="${KIND_VERSION:-v0.32.0}"
KUBECTL_VERSION="${KUBECTL_VERSION:-}"
KIND_BASE_URL="${KIND_BASE_URL:-https://kind.sigs.k8s.io/dl}"
KUBECTL_BASE_URL="${KUBECTL_BASE_URL:-https://dl.k8s.io/release}"
INSTALL_DIR="/usr/local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_OUT="${SCRIPT_DIR}/kind-cluster-config.yaml"
KIND_SHA256_FILE="${SCRIPT_DIR}/kind.sha256"

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

echo "== Prüfe Voraussetzungen =="

if ! command -v podman &>/dev/null; then
    echo "Fehler: Podman ist nicht installiert. Bitte zuerst scripts/install-podman.sh ausführen." >&2
    exit 1
fi
echo "Podman gefunden: $(podman --version)"

if [[ -r /sys/fs/cgroup/cgroup.controllers ]]; then
    echo "cgroup v2 aktiv."
else
    echo "Warnung: cgroup v2 wurde nicht erkannt. Der Podman-Provider von kind benötigt cgroup v2." >&2
fi

ARCH=$(uname -m)
case "$ARCH" in
    x86_64) KIND_ARCH="amd64" ;;
    aarch64) KIND_ARCH="arm64" ;;
    *)
        echo "Fehler: Nicht unterstützte Architektur '$ARCH'." >&2
        exit 1
        ;;
esac
LOCAL_KIND_BIN="${SCRIPT_DIR}/kind-linux-${KIND_ARCH}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Liefert den Pfad eines Programms aus PATH oder INSTALL_DIR. Unter sudo auf
# RHEL fehlt /usr/local/bin im secure_path, daher die zweite Prüfung.
find_bin() {
    local name="$1"
    if command -v "$name" 2>/dev/null; then
        return 0
    fi
    if [[ -x "${INSTALL_DIR}/${name}" ]]; then
        echo "${INSTALL_DIR}/${name}"
        return 0
    fi
    return 1
}

download() {
    local url="$1" dest="$2"
    if ! curl -fsSL --retry 3 -o "$dest" "$url"; then
        echo "Fehler: Download fehlgeschlagen: $url" >&2
        echo "Ohne Internetzugang: KIND_BASE_URL/KUBECTL_BASE_URL auf einen internen Mirror setzen" >&2
        echo "oder das Binary vorab manuell nach ${INSTALL_DIR} installieren." >&2
        exit 1
    fi
}

verify_sha256() {
    local file="$1" expected="$2" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "Fehler: Prüfsumme von $file stimmt nicht." >&2
        echo "  erwartet: $expected" >&2
        echo "  gefunden: $actual" >&2
        exit 1
    fi
    echo "Prüfsumme ok: $actual"
}

echo
if KIND_BIN="$(find_bin kind)"; then
    echo "kind bereits vorhanden: $("$KIND_BIN" version)"
elif [[ -f "$LOCAL_KIND_BIN" ]]; then
    echo "== Installiere kind aus lokalem Binary (${LOCAL_KIND_BIN}) =="
    if [[ ! -f "$KIND_SHA256_FILE" ]]; then
        echo "Fehler: $KIND_SHA256_FILE fehlt, das lokale Binary kann nicht geprüft werden." >&2
        exit 1
    fi
    EXPECTED="$(awk -v f="kind-linux-${KIND_ARCH}" '!/^#/ && $2 == f {print $1}' "$KIND_SHA256_FILE")"
    if [[ -z "$EXPECTED" ]]; then
        echo "Fehler: Kein Hash für kind-linux-${KIND_ARCH} in $KIND_SHA256_FILE." >&2
        exit 1
    fi
    verify_sha256 "$LOCAL_KIND_BIN" "$EXPECTED"
    install -m 0755 "$LOCAL_KIND_BIN" "${INSTALL_DIR}/kind"
    KIND_BIN="${INSTALL_DIR}/kind"
    echo "kind installiert: $("$KIND_BIN" version)"
else
    echo "== kind nicht gefunden, lade ${KIND_VERSION} herunter =="
    KIND_URL="${KIND_BASE_URL}/${KIND_VERSION}/kind-linux-${KIND_ARCH}"
    download "$KIND_URL" "${TMP_DIR}/kind"
    download "${KIND_URL}.sha256sum" "${TMP_DIR}/kind.sha256sum"
    verify_sha256 "${TMP_DIR}/kind" "$(awk '{print $1}' "${TMP_DIR}/kind.sha256sum")"
    install -m 0755 "${TMP_DIR}/kind" "${INSTALL_DIR}/kind"
    KIND_BIN="${INSTALL_DIR}/kind"
    echo "kind installiert: $("$KIND_BIN" version)"
fi

echo
if KUBECTL_BIN="$(find_bin kubectl)"; then
    echo "kubectl bereits vorhanden: $("$KUBECTL_BIN" version --client)"
else
    if [[ -z "$KUBECTL_VERSION" ]]; then
        download "${KUBECTL_BASE_URL}/stable.txt" "${TMP_DIR}/stable.txt"
        KUBECTL_VERSION="$(tr -d '[:space:]' < "${TMP_DIR}/stable.txt")"
    fi
    echo "== kubectl nicht gefunden, lade ${KUBECTL_VERSION} herunter =="
    KUBECTL_URL="${KUBECTL_BASE_URL}/${KUBECTL_VERSION}/bin/linux/${KIND_ARCH}/kubectl"
    download "$KUBECTL_URL" "${TMP_DIR}/kubectl"
    download "${KUBECTL_URL}.sha256" "${TMP_DIR}/kubectl.sha256"
    verify_sha256 "${TMP_DIR}/kubectl" "$(awk '{print $1}' "${TMP_DIR}/kubectl.sha256")"
    install -m 0755 "${TMP_DIR}/kubectl" "${INSTALL_DIR}/kubectl"
    KUBECTL_BIN="${INSTALL_DIR}/kubectl"
    echo "kubectl installiert: $("$KUBECTL_BIN" version --client)"
fi

echo
if [[ ! -f "$CONFIG_OUT" ]]; then
    echo "Fehler: $CONFIG_OUT nicht gefunden." >&2
    echo "Diese Datei wird nicht generiert, sondern im Ordner gepflegt (Nexus/Artifactory registry-mirror Einträge dort eintragen)." >&2
    exit 1
fi
echo "Verwende Cluster-Config: $CONFIG_OUT"

echo
read -rp "Cluster jetzt mit Podman erstellen ('kind create cluster --config ...')? [j/N]: " CREATE_NOW
if [[ "$CREATE_NOW" =~ ^[JjYy]$ ]]; then
    export KIND_EXPERIMENTAL_PROVIDER=podman
    "$KIND_BIN" create cluster --config "$CONFIG_OUT"
fi

echo
echo "Fertig."
echo "  kind Version:    $("$KIND_BIN" version)"
echo "  Cluster-Config:  $CONFIG_OUT"
echo "  Cluster starten: KIND_EXPERIMENTAL_PROVIDER=podman kind create cluster --config $CONFIG_OUT"
echo "  Cluster löschen: KIND_EXPERIMENTAL_PROVIDER=podman kind delete cluster --name \$(kind get clusters)"
