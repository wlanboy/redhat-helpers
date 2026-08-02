#!/usr/bin/env python3
"""
unithelper.py

Zeigt alle systemd User Units (systemctl --user) an und fuehrt einen
Befehl (start/stop/status/restart) auf einer Nummer, mehreren Nummern
(durch Komma oder Leerzeichen getrennt) oder allen Units aus.

Aufruf:
  unithelper.py start
  unithelper.py stop
  unithelper.py status
  unithelper.py restart

Laeuft im User-Kontext (kein root, kein sudo) gegen "systemctl --user".
Kompatibel zu Python 3.9.
"""

import argparse
import subprocess
import sys
from typing import List, Optional

ACTIONS = ("start", "stop", "status", "restart")


def error(message: str) -> None:
    print("Fehler: {0}".format(message), file=sys.stderr)


def die(message: str) -> None:
    error(message)
    sys.exit(1)


def list_units() -> List[str]:
    try:
        proc = subprocess.run(
            [
                "systemctl", "--user", "list-units", "--type=service", "--all",
                "--no-legend", "--plain",
            ],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        die("systemctl wurde nicht gefunden.")
    except subprocess.CalledProcessError as exc:
        die("Konnte User Units nicht auflisten ({0}).".format(exc))

    units = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        units.append(line.split()[0])
    return units


def print_units(units: List[str]) -> None:
    print()
    print("Verfuegbare User Units:")
    for i, unit in enumerate(units, start=1):
        print("  [{0:2d}] {1}".format(i, unit))
    print()


def parse_selection(raw: str, count: int) -> Optional[List[int]]:
    raw = raw.strip()
    if raw.lower() in ("a", "alle"):
        return list(range(1, count + 1))

    indices = []
    for token in raw.replace(",", " ").split():
        if not token.isdigit():
            error("Ungueltige Nummer '{0}'.".format(token))
            return None
        num = int(token)
        if not (1 <= num <= count):
            error("Nummer '{0}' liegt ausserhalb des gueltigen Bereichs.".format(num))
            return None
        if num not in indices:
            indices.append(num)

    if not indices:
        error("Keine gueltige Auswahl.")
        return None
    return indices


def run_action(action: str, unit: str) -> bool:
    print("-> {0}: {1}".format(action, unit))
    args = ["systemctl", "--user", action, unit]
    if action == "status":
        args.append("--no-pager")
    result = subprocess.run(args)
    if result.returncode != 0:
        error("{0} fehlgeschlagen fuer {1}.".format(action, unit))
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Zeigt systemd User Units an und fuehrt start/stop/status/restart "
            "auf einer, mehreren oder allen Units aus."
        )
    )
    parser.add_argument("action", choices=ACTIONS, help="Auszufuehrende Aktion")
    args = parser.parse_args()

    units = list_units()
    if not units:
        print("Keine User Units gefunden.")
        sys.exit(0)

    print_units(units)

    print("Nummer(n) angeben (z.B. '3' oder '1,4,7'), 'a' fuer alle, 'q' zum Abbrechen.")
    while True:
        raw = input("Auswahl: ").strip()
        if raw.lower() in ("q", "quit"):
            print("Abgebrochen.")
            sys.exit(0)
        indices = parse_selection(raw, len(units))
        if indices is not None:
            break

    selected = [units[i - 1] for i in indices]

    print()
    failures = 0
    for unit in selected:
        if not run_action(args.action, unit):
            failures += 1

    print()
    if failures:
        print("Fertig mit {0} Fehler(n).".format(failures))
        sys.exit(1)
    print("Fertig.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        die("Abgebrochen.")
