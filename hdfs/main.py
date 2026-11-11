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

  # ACLs, in denen eine clientid steht (nur explizit, nichts Geerbtes)
  uv run main.py search @client3
  uv run main.py search '@client*' --format csv
  uv run main.py search @client2 --has-bits 16      # nur Rechte mit Bit 16
  uv run main.py search @client2 --perm ADMIN       # genau 19, über den Namen
  uv run main.py show /fs/account1 --permissions eigene-rechte.json  # andere Rechte-Tabelle

  # alle clientids mit Anzahl ACLs
  uv run main.py clients --format json

  # Anzahl der Einträge in der DB (Pfade, ACLs, Rechte, clientids)
  uv run main.py count

  # ACL eines Pfads und die Rechte daraus
  uv run main.py show /fs/account1/container1

  # Rechte je ACL als CSV
  uv run main.py export -o rechte.csv

  # @neu in jeder ACL, die @alt nennt, mit dessen Rechten ergänzen (erst Plan, dann schreiben)
  uv run main.py add @alt @neu
  uv run main.py add @alt @neu --apply
  export DEPTH=3 HADOOP_CONF_DIR=$PWD/namenode
  uv run main.py add @client1 @client2 --fsimage ./fsimage_0000000000000001017 --generate add-neu.sh
                                                      # statt --apply: Skript mit allen
                                                       # "hadoop fs"-Aufrufen, HDFS bleibt unverändert

  # @alt in jeder ACL durch @neu ersetzen, auch als Besitzer
  uv run main.py replace @alt @neu --apply

  # clientid entfernen; wo sie Besitzer ist, übernimmt die Gruppe ops
  uv run main.py remove @client3 --new-owner '.cn=ops,ou=groups,dc=firma' --apply

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
