#!/usr/bin/env python3
"""
checkunits.py

Prueft alle systemd User Units (systemctl --user) und zeigt eine
Uebersicht mit Fehlern (failed Units bzw. Journal-Eintraege mit
Prioritaet err/crit/alert/emerg) und Warnungen (Journal-Eintraege mit
Prioritaet warning) an.

Aufruf:
  checkunits.py                  # Zeitraum: letzte 24 Stunden
  checkunits.py --since "-1h"    # eigener Zeitraum (journalctl --since Format)
  checkunits.py --lines 10       # Anzahl Journal-Zeilen pro Unit (Default 5)

Exit-Codes (nagios-artig, fuer die Nutzung in Monitoring-Wrappern):
  0 = keine Fehler und keine Warnungen
  1 = nur Warnungen
  2 = mindestens ein Fehler

Laeuft im User-Kontext (kein root, kein sudo) gegen "systemctl --user"
und "journalctl --user". Kompatibel zu Python 3.9.
"""

import argparse
import subprocess
import sys
from typing import Any, Dict, List

from helper import list_units


def journal_lines(unit: str, priority: str, since: str, limit: int) -> List[str]:
    try:
        proc = subprocess.run(
            [
                "journalctl", "--user", "-u", unit, "-p", priority,
                "--since", since, "--no-pager", "-n", str(limit),
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        line for line in proc.stdout.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]


def print_section(title: str, entries: List[Dict[str, Any]]) -> None:
    print()
    print("== {0} ({1}) ==".format(title, len(entries)))
    if not entries:
        print("  (keine)")
        return
    for entry in entries:
        print(
            "  {0}  (active={1}, sub={2})".format(
                entry["name"], entry["active"], entry["sub"]
            )
        )
        for line in entry["lines"]:
            print("      {0}".format(line))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prueft alle User Units auf Fehler (failed / err-Journal) und "
            "Warnungen (warning-Journal) und zeigt eine Uebersicht."
        )
    )
    parser.add_argument(
        "--since", default="-24 hours",
        help="Zeitraum im journalctl --since Format (Default: '-24 hours')",
    )
    parser.add_argument(
        "--lines", type=int, default=5,
        help="Max. Journal-Zeilen pro Unit (Default: 5)",
    )
    args = parser.parse_args()

    units = list_units(die_code=2)
    if not units:
        print("Keine User Units gefunden.")
        sys.exit(0)

    failed_units = []
    warning_units = []

    for unit in units:
        err_lines = journal_lines(unit["name"], "err", args.since, args.lines)
        is_failed = unit["active"] == "failed" or bool(err_lines)

        if is_failed:
            failed_units.append({**unit, "lines": err_lines})
            continue

        warn_lines = journal_lines(unit["name"], "warning..warning", args.since, args.lines)
        if warn_lines:
            warning_units.append({**unit, "lines": warn_lines})

    print("Geprueft: {0} Units, Zeitraum: {1}".format(len(units), args.since))
    print(
        "  OK: {0}   Warnungen: {1}   Fehler: {2}".format(
            len(units) - len(failed_units) - len(warning_units),
            len(warning_units),
            len(failed_units),
        )
    )

    print_section("Fehler", failed_units)
    print_section("Warnungen", warning_units)

    print()
    if failed_units:
        print("Ergebnis: FEHLER gefunden.")
        sys.exit(2)
    if warning_units:
        print("Ergebnis: nur Warnungen gefunden.")
        sys.exit(1)
    print("Ergebnis: keine Fehler oder Warnungen.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(2)
