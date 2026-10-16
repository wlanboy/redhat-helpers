#!/usr/bin/env python3
"""ACL-Metadaten (xattr user.acl) in HDFS einsammeln, durchsuchen und ändern.

ACL-Format: <besitzer>|<eintrag>=<recht>,...  (Details in hdfs.md)

Kommandos:
  collect             ACLs aus dem fsimage (oder live) lesen und in SQLite speichern
  search CLIENTID     Pfade, auf die eine clientid Rechte hat (Wildcards erlaubt)
  clients             alle clientids mit Anzahl Pfade
  show PFAD           ACL und effektive Rechte eines Pfads
  export              effektive Rechte als CSV (Format wie testdata/expected.csv)
  add QUELLE NEU      NEU überall mit den Rechten von QUELLE ergänzen (Migration)
  replace ALT NEU     ALT überall durch NEU ersetzen, auch als Besitzer
  remove CLIENTID     clientid aus allen ACLs entfernen
  restore BACKUP      Änderungen aus einer Backup-Datei zurückdrehen

Eingelesen wird das neueste fsimage_<txid> im Ordner der DB (siehe hdfs.md),
ohne Last auf dem NameNode. --live liest stattdessen direkt aus HDFS.

add/replace/remove/restore lesen ein, lesen die betroffenen Pfade live nach,
zeigen den Plan und schreiben erst mit --apply. Vor dem Schreiben wird ein
Backup angelegt.

Nur Standardbibliothek, läuft ab Python 3.9.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

if __name__ == "__main__" and not __package__:
    # Direkt als Skript gestartet (python3 src/hdfs/main.py): Dann steht src/hdfs
    # vorne im Suchpfad statt src. Umstellen, damit "import hdfs.xxx" genauso
    # funktioniert wie bei "uv run hdfs-acl".
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from hdfs.commands import (  # noqa: E402  (erst nach der Pfad-Korrektur oben)
    cmd_add, cmd_clients, cmd_collect, cmd_export, cmd_remove, cmd_replace,
    cmd_restore, cmd_search, cmd_show,
)
from hdfs.common import HdfsError, UsageError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    # Optionsgruppen als Eltern-Parser: jedes Kommando bekommt nur die Optionen,
    # die es braucht (lesende z. B. kein --apply und keine HDFS-Optionen)
    db_opts = argparse.ArgumentParser(add_help=False)
    db_opts.add_argument("--db", default=os.environ.get("HDFS_ACL_DB", "hdfs-acl.db"),
                         help="SQLite-Datei (Default: $HDFS_ACL_DB oder ./hdfs-acl.db)")

    hdfs_opts = argparse.ArgumentParser(add_help=False)
    hdfs_opts.add_argument("--base-dir", default=os.environ.get("BASE_DIR", "/fs"),
                           help="Basisordner in HDFS (Default: $BASE_DIR oder /fs)")
    hdfs_opts.add_argument("--attr", default=os.environ.get("ACL_ATTR", "user.acl"),
                           help="Name des xattr (Default: $ACL_ATTR oder user.acl)")
    hdfs_opts.add_argument("--depth", type=int, default=2,
                           help="Ebenen unter dem Basisordner (Default 2: Account/Container)")
    hdfs_opts.add_argument("--fsimage", default=os.environ.get("HDFS_FSIMAGE"),
                           help="fsimage-Datei (Default: $HDFS_FSIMAGE oder das neueste "
                                "fsimage_<txid> im Ordner der DB)")
    hdfs_opts.add_argument("--live", action="store_true",
                           help="HDFS per getfattr lesen statt aus dem fsimage "
                                "(ein NameNode-RPC je Pfad, für kleine Bestände)")
    hdfs_opts.add_argument("--parallel", type=int, default=int(os.environ.get("PARALLEL", "8")),
                           help="parallele hdfs-Aufrufe beim Nachlesen/Schreiben "
                                "(Default: $PARALLEL oder 8)")

    write_opts = argparse.ArgumentParser(add_help=False)
    write_opts.add_argument("--apply", action="store_true", help="Änderungen wirklich schreiben")

    fmt_opts = argparse.ArgumentParser(add_help=False)
    fmt_opts.add_argument("--format", choices=["table", "csv", "json"], default="table")

    parser = argparse.ArgumentParser(
        prog="hdfs-acl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="KOMMANDO")

    # set_defaults(func=...): main() ruft später einfach args.func(args) auf
    p = sub.add_parser("collect", parents=[db_opts, hdfs_opts], help="ACLs einsammeln")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("search", parents=[db_opts, fmt_opts], help="nach clientid suchen")
    p.add_argument("clientid", help="z. B. @client3 oder '@client*' (Glob)")
    p.add_argument("--own", action="store_true", help="nur eigene Einträge, ohne Vererbung")
    p.add_argument("--perm", type=int, choices=[1, 3], help="nur dieses Recht")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("clients", parents=[db_opts, fmt_opts], help="alle clientids auflisten")
    p.set_defaults(func=cmd_clients)

    p = sub.add_parser("show", parents=[db_opts], help="ACL eines Pfads anzeigen")
    p.add_argument("path")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", parents=[db_opts], help="effektive Rechte als CSV")
    p.add_argument("-o", "--output", help="Datei statt stdout")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("add", parents=[db_opts, hdfs_opts, write_opts],
                       help="clientid ergänzen (Migration)")
    p.add_argument("source", metavar="QUELLE", help="vorhandene clientid")
    p.add_argument("new", metavar="NEU", help="clientid, die dieselben Rechte bekommt")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("replace", parents=[db_opts, hdfs_opts, write_opts],
                       help="clientid ersetzen, auch als Besitzer")
    p.add_argument("old", metavar="ALT", help="clientid, die verschwindet")
    p.add_argument("new", metavar="NEU", help="clientid, die ihre Stelle einnimmt")
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("remove", parents=[db_opts, hdfs_opts, write_opts], help="clientid entfernen")
    p.add_argument("clientid")
    p.add_argument("--new-owner", help="neuer Besitzer, wo CLIENTID Besitzer ist (sonst übersprungen); "
                                       "dessen eigene Einträge entfallen dann")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("restore", parents=[db_opts, hdfs_opts, write_opts],
                       help="Backup zurückspielen")
    p.add_argument("backup", help="hdfs-acl-backup-*.csv")
    p.add_argument("--force", action="store_true", help="auch seitdem geänderte Pfade zurücksetzen")
    p.set_defaults(func=cmd_restore)

    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (UsageError, HdfsError) as error:
        # erwartete Fehler nur als Meldung, alles andere mit Traceback
        print(f"Fehler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
