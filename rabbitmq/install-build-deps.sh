#!/usr/bin/env bash
#
# install-build-deps.sh
#
# Einmalige root-Vorbereitung der Build-VM: installiert die Pakete, die
# build-erlang.sh zum Übersetzen von Erlang/OTP braucht (aus den RHEL 9
# BaseOS/AppStream Repos über Nexus, siehe requirements.md).
#
# Der eigentliche Build läuft danach als normaler User.
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

echo "== Installiere Build-Abhängigkeiten für Erlang/OTP =="
dnf install -y \
    gcc gcc-c++ make perl \
    openssl-devel ncurses-devel \
    tar gzip xz

# curl nur nachinstallieren, wenn weder curl noch curl-minimal vorhanden ist
# (beide gleichzeitig kollidieren)
command -v curl &>/dev/null || dnf install -y curl-minimal

echo
echo "Fertig. Den Build als normaler User starten: rabbitmq/build-erlang.sh"
