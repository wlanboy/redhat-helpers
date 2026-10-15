"""Ausgabe von Ergebniszeilen als Tabelle, CSV oder JSON."""

from __future__ import annotations

import csv
import json
import sys


def output(columns: list, rows: list, fmt: str, file=sys.stdout) -> None:
    """rows sind Tupel in der Reihenfolge von columns; fmt: table, csv oder json.

    bool-Werte (z. B. owner) werden je Format passend dargestellt:
    JSON true/false, CSV 1/0, Tabelle "ja"/leer.
    """
    if fmt == "json":
        json.dump([dict(zip(columns, row)) for row in rows], file, indent=2, ensure_ascii=False)
        file.write("\n")
        return
    if fmt == "csv":
        writer = csv.writer(file, delimiter=";", lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([[int(v) if isinstance(v, bool) else v for v in row] for row in rows])
        return
    # Tabelle: jede Spalte so breit wie ihr längster Wert (inkl. Überschrift)
    cells = [[str(c).upper() for c in columns]]
    cells += [["ja" if v is True else "" if v is False else str(v) for v in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    for row in cells:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip(), file=file)
