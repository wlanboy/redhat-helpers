#!/usr/bin/env bash
#
# install-kind.sh
#
# Richtet auf RHEL 9 mit Podman als Container-Runtime einen kind-Cluster
# (Kubernetes IN Docker) für den Einsatz mit einer internen Nexus/Artifactory
# Registry ein. kind und kubectl werden als bereits installiert vorausgesetzt
# (siehe /usr/local/bin/kind, /usr/local/bin/kubectl bzw. das kind-linux-*
# Binary in diesem Ordner) und nur bei Bedarf nachinstalliert:
#   - prüft, ob Podman bereits installiert ist (siehe scripts/install-podman.sh)
#   - prüft cgroup v2 (Pflicht für den Podman-Provider von kind)
#   - prüft, ob kind/kubectl bereits vorhanden sind; falls nicht, wird zuerst
#     ein lokales kind-linux-<arch> Binary in diesem Ordner verwendet
#     (Offline-Installation), sonst per curl heruntergeladen
#   - erstellt optional direkt den Cluster (KIND_EXPERIMENTAL_PROVIDER=podman)
#     aus kind-cluster-config.yaml
#
# kind-cluster-config.yaml wird NICHT vom Skript generiert, sondern als
# eigene Datei in diesem Ordner gepflegt (Nexus/Artifactory registry-mirror
# Einträge dort direkt anpassen).
#
# Muss als root ausgeführt werden (Installation nach /usr/local/bin, falls
# nötig). Der Cluster selbst wird mit rootful Podman erstellt.

set -euo pipefail

KIND_VERSION="${KIND_VERSION:-v0.29.0}"
KUBECTL_VERSION="${KUBECTL_VERSION:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_OUT="${SCRIPT_DIR}/kind-cluster-config.yaml"

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

echo
if command -v kind &>/dev/null; then
    echo "kind bereits vorhanden: $(kind version)"
elif [[ -f "$LOCAL_KIND_BIN" ]]; then
    echo "== Installiere kind aus lokalem Binary (${LOCAL_KIND_BIN}) =="
    install -m 0755 "$LOCAL_KIND_BIN" /usr/local/bin/kind
    echo "kind installiert: $(/usr/local/bin/kind version)"
else
    echo "== kind nicht gefunden, lade ${KIND_VERSION} herunter =="
fi

echo
if command -v kubectl &>/dev/null; then
    echo "kubectl bereits vorhanden: $(kubectl version --client)"
else
    echo "== kubectl nicht gefunden, lade herunter =="
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
    kind create cluster --config "$CONFIG_OUT"
fi

echo
echo "Fertig."
echo "  kind Version:    $(kind version)"
echo "  Cluster-Config:  $CONFIG_OUT"
echo "  Cluster starten: KIND_EXPERIMENTAL_PROVIDER=podman kind create cluster --config $CONFIG_OUT"
echo "  Cluster löschen: KIND_EXPERIMENTAL_PROVIDER=podman kind delete cluster --name \$(kind get clusters)"
