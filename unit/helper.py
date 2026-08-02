#!/usr/bin/env python3
"""
helper.py

Gemeinsame Hilfsfunktionen fuer checkunits.py, generateunit.py und
unithelper.py: Fehlerausgabe/Abbruch sowie Auflisten von systemd User
Units (systemctl --user).

Kompatibel zu Python 3.9.
"""

import subprocess
import sys
from typing import Dict, List, NoReturn


def error(message: str) -> None:
    print("Fehler: {0}".format(message), file=sys.stderr)


def die(message: str, code: int = 1) -> NoReturn:
    error(message)
    sys.exit(code)


def list_units(die_code: int = 1) -> List[Dict[str, str]]:
    """Listet alle systemd User Units (Type=service) mit Name, Load-,
    Active- und Sub-State sowie Beschreibung auf.

    die_code steuert den Exit-Code, falls systemctl nicht gefunden wird
    bzw. fehlschlaegt (checkunits.py nutzt hier z.B. 2, da es
    nagios-artige Exit-Codes verwendet)."""
    try:
        proc = subprocess.run(
            [
                "systemctl", "--user", "list-units", "--type=service", "--all",
                "--no-legend", "--plain",
            ],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        die("systemctl wurde nicht gefunden.", code=die_code)
    except subprocess.CalledProcessError as exc:
        die("Konnte User Units nicht auflisten ({0}).".format(exc), code=die_code)

    units = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        while len(parts) < 5:
            parts.append("")
        name, load, active, sub, description = parts[:5]
        units.append(
            {
                "name": name,
                "load": load,
                "active": active,
                "sub": sub,
                "description": description,
            }
        )
    return units


def run_systemctl(args: List[str]) -> bool:
    """Fuehrt einen 'systemctl --user'-Befehl aus. Gibt True bei Erfolg
    (Exit-Code 0) zurueck, sonst False (inkl. Fehlermeldung)."""
    try:
        result = subprocess.run(["systemctl", "--user"] + args)
    except FileNotFoundError:
        error("systemctl wurde nicht gefunden.")
        return False
    if result.returncode != 0:
        error("systemctl {0} fehlgeschlagen.".format(" ".join(args)))
        return False
    return True
