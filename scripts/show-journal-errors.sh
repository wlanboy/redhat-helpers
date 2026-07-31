#!/usr/bin/env bash
#
# show-journal-errors.sh
#
# Zeigt Journal-Einträge mit Fehlern (Priorität err/crit/alert/emerg) aus
# der letzten Zeit an:
#   - fragt interaktiv nach dem Zeitraum (letzte Stunde/24h/7 Tage/seit
#     letztem Boot/eigene Angabe)
#   - zeigt zuerst eine Zusammenfassung: Anzahl Fehler pro Unit
#   - zeigt danach die vollständigen Log-Einträge für den Zeitraum
#
# Liest das System-Journal (journalctl). Ohne root/adm/systemd-journal
# Gruppenmitgliedschaft ggf. mit sudo ausführen.

set -euo pipefail

if ! command -v journalctl &>/dev/null; then
    echo "Fehler: journalctl wurde nicht gefunden." >&2
    exit 1
fi

echo "Zeitraum für die Fehlersuche:"
echo "  [1] letzte Stunde"
echo "  [2] letzte 24 Stunden"
echo "  [3] letzte 7 Tage"
echo "  [4] seit dem letzten Boot"
echo "  [5] eigene Angabe (journalctl --since Format)"
read -rp "Auswahl: " RANGE

JOURNAL_ARGS=(-p err --no-pager)

case "$RANGE" in
    1)
        JOURNAL_ARGS+=(--since "-1 hour")
        ;;
    2)
        JOURNAL_ARGS+=(--since "-24 hours")
        ;;
    3)
        JOURNAL_ARGS+=(--since "-7 days")
        ;;
    4)
        JOURNAL_ARGS+=(--boot)
        ;;
    5)
        read -rp "Since (z.B. '2026-07-30 08:00', '-2h'): " CUSTOM_SINCE
        if [[ -z "$CUSTOM_SINCE" ]]; then
            echo "Fehler: Angabe darf nicht leer sein." >&2
            exit 1
        fi
        JOURNAL_ARGS+=(--since "$CUSTOM_SINCE")
        ;;
    *)
        echo "Fehler: Ungültige Auswahl '$RANGE'." >&2
        exit 1
        ;;
esac

if ! journalctl "${JOURNAL_ARGS[@]}" -o cat &>/dev/null; then
    echo "Fehler: Zugriff auf das Journal fehlgeschlagen." >&2
    echo "Ggf. mit 'sudo $0' erneut ausführen." >&2
    exit 1
fi

echo
echo "Fehler pro Unit:"
journalctl "${JOURNAL_ARGS[@]}" -o json 2>/dev/null \
    | grep -o '"_SYSTEMD_UNIT":"[^"]*"' \
    | sed -E 's/"_SYSTEMD_UNIT":"([^"]*)"/\1/' \
    | sort | uniq -c | sort -rn \
    || echo "  (keine Unit-Zuordnung ermittelbar)"

echo
echo "Log-Einträge:"
journalctl "${JOURNAL_ARGS[@]}"
