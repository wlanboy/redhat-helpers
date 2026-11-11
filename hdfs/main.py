#!/usr/bin/env python3
"""Kurzer Einstieg für hdfs-acl aus dem Projektordner.

    uv run main.py <kommando> ...
    python3 main.py <kommando> ...

Reicht alle Argumente an hdfs.main.main() weiter (Kommandos siehe hdfs.md
bzw. "uv run main.py --help" / "uv run main.py <kommando> --help").

Beispiele:

  # ACLs aus dem neuesten fsimage_<txid> im DB-Ordner einsammeln
  uv run main.py collect
  uv run main.py collect --live --base-dir /fs      # direkt aus HDFS (kleine Bestände)

  # Pfade, auf die eine clientid Rechte hat
  uv run main.py search @client3
  uv run main.py search '@client*' --own --format csv
  uv run main.py search @client2 --has-bits 16      # nur Pfade mit ADMINISTRATION
  uv run main.py search @client2 --has-bits ADMINISTRATION   # dasselbe über den Namen
  uv run main.py show /fs/account1 --permissions eigene-rechte.json  # andere Rechte-Tabelle

  # alle clientids mit Anzahl Pfade
  uv run main.py clients --format json

  # ACL und effektive Rechte eines Pfads
  uv run main.py show /fs/account1/container1

  # effektive Rechte als CSV
  uv run main.py export -o rechte.csv

  # @neu überall mit den Rechten von @alt ergänzen (erst Plan, dann schreiben)
  uv run main.py add @alt @neu
  uv run main.py add @alt @neu --apply
  uv run main.py add @client1 @client2 --fsimage ./fsimage_0000000000000001017 --generate add-neu.sh
                                                      # statt --apply: Skript mit allen
                                                       # "hadoop fs"-Aufrufen, HDFS bleibt unverändert

  # @alt überall durch @neu ersetzen, auch als Besitzer
  uv run main.py replace @alt @neu --apply

  # clientid entfernen; wo sie Besitzer ist, übernimmt #ops
  uv run main.py remove @client3 --new-owner '#ops' --apply

  # Änderungen aus einem Backup zurückdrehen. Das Backup entsteht automatisch
  # bei jedem --apply und --generate neben der DB (Name steht in der Ausgabe).
  uv run main.py restore hdfs-acl-backup-20261111-120000-add-neu.csv --apply
"""

import sys
from pathlib import Path

# src/ vorne in den Suchpfad, damit es auch ohne installiertes Paket
# (python3 main.py) den Code aus src/hdfs nimmt.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hdfs.main import main

if __name__ == "__main__":
    sys.exit(main())
