"""Gemeinsame Hilfen für alle Module: Fehlerklassen, Meldungen, Sortierung."""

from __future__ import annotations

import re
import sys


class UsageError(Exception):
    """Falsche Eingabe (Argument, fehlende DB, ...). main() gibt nur die Meldung aus."""


class HdfsError(Exception):
    """HDFS-Client nicht gefunden oder ein hdfs-Aufruf ist fehlgeschlagen."""


def info(message: str) -> None:
    # Statusmeldungen gehen nach stderr, damit stdout (CSV/JSON) sauber in
    # Pipes und Dateien weiterverarbeitet werden kann
    print(message, file=sys.stderr)


def natural_key(value: str) -> list:
    """Sortierschlüssel, der Zahlen als Zahlen vergleicht: container2 vor container10."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]
