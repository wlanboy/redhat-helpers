#!/usr/bin/env bash
#
# manage-user-units.sh
#
# Zeigt alle systemd User Units (systemctl --user) an und bietet an:
#   - alle Units zu starten/stoppen/neu zu starten
#   - eine einzelne Unit per Nummer zu starten/stoppen/neu zu starten
#
# Läuft im User-Kontext (kein root, kein sudo) gegen "systemctl --user".

set -euo pipefail

if ! command -v systemctl &>/dev/null; then
    echo "Fehler: systemctl wurde nicht gefunden." >&2
    exit 1
fi

mapfile -t UNITS < <(systemctl --user list-units --type=service --all --no-legend --plain | awk '{print $1}')

if [[ ${#UNITS[@]} -eq 0 ]]; then
    echo "Keine User Units gefunden."
    exit 0
fi

print_units() {
    echo
    echo "Verfügbare User Units:"
    local i
    for i in "${!UNITS[@]}"; do
        printf '  [%2d] %s\n' "$((i + 1))" "${UNITS[$i]}"
    done
    echo
}

do_action() {
    local action="$1"
    shift
    local unit
    for unit in "$@"; do
        echo "-> $action: $unit"
        systemctl --user "$action" "$unit" || echo "   Fehler bei $unit" >&2
    done
}

print_units

echo "Was möchtest du tun?"
echo "  [a] Alle Units starten/stoppen/neustarten"
echo "  [n] Einzelne Unit per Nummer starten/stoppen/neustarten"
echo "  [q] Abbrechen"
read -rp "Auswahl: " MODE

case "$MODE" in
    a|A)
        read -rp "Aktion für ALLE Units (start/stop/restart): " ACTION
        if [[ ! "$ACTION" =~ ^(start|stop|restart)$ ]]; then
            echo "Fehler: Ungültige Aktion '$ACTION'." >&2
            exit 1
        fi
        do_action "$ACTION" "${UNITS[@]}"
        ;;
    n|N)
        read -rp "Nummer der Unit: " NUM
        if ! [[ "$NUM" =~ ^[0-9]+$ ]] || (( NUM < 1 || NUM > ${#UNITS[@]} )); then
            echo "Fehler: Ungültige Nummer '$NUM'." >&2
            exit 1
        fi
        read -rp "Aktion (start/stop/restart): " ACTION
        if [[ ! "$ACTION" =~ ^(start|stop|restart)$ ]]; then
            echo "Fehler: Ungültige Aktion '$ACTION'." >&2
            exit 1
        fi
        do_action "$ACTION" "${UNITS[$((NUM - 1))]}"
        ;;
    q|Q)
        echo "Abgebrochen."
        exit 0
        ;;
    *)
        echo "Fehler: Ungültige Auswahl '$MODE'." >&2
        exit 1
        ;;
esac

echo "Fertig."
