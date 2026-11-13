#!/usr/bin/env python3
"""ACL-Metadaten (xattr user.acl) in HDFS einsammeln, durchsuchen und ändern.

ACL-Format: <besitzer>|<eintrag>=<recht>,...  (recht = Bitmaske, Details in hdfs.md)
Was die Bits bedeuten, legt die Rechte-Tabelle fest (--permissions, hdfs.md).

Ausgewertet wird nur, was an einem Pfad selbst steht, nichts Geerbtes.

Kommandos:
  collect             ACLs aus dem fsimage (oder live) lesen und in SQLite speichern
  search CLIENTID     ACLs, die eine clientid nennen (Wildcards erlaubt)
  clients             alle clientids mit Anzahl ACLs
  count               Anzahl der Einträge in der DB (Pfade, ACLs, Rechte, clientids)
  show PFAD           ACL eines Pfads und die Rechte daraus
  export              Rechte je ACL als CSV (Format wie testdata/expected.csv)
  add QUELLE NEU      NEU mit den Rechten von QUELLE ergänzen (Migration),
                      mit --permission RECHT überall mit diesem Recht
  replace ALT NEU     ALT durch NEU ersetzen, auch als Besitzer
                      (add/replace: nur Container, mit --accounts auch Accounts,
                      mit --account NAME nur unter diesem Account)
  remove CLIENTID     clientid aus allen ACLs entfernen
  restore BACKUP      Änderungen aus einer Backup-Datei zurückdrehen

Eingelesen wird das neueste fsimage_<txid> im Ordner der DB (siehe hdfs.md),
ohne Last auf dem NameNode. --live liest stattdessen direkt aus HDFS.

add/replace/remove/restore lesen ein, lesen die betroffenen Pfade live nach,
zeigen den Plan und schreiben erst mit --apply. Vor dem Schreiben wird ein
Backup angelegt. --generate DATEI schreibt statt HDFS ein Shell-Skript mit
den "hadoop fs"-Aufrufen (ebenfalls mit Backup).

Nur Standardbibliothek, läuft ab Python 3.9.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    # Direkt als Skript gestartet (python3 src/hdfs/main.py): Dann steht src/hdfs
    # vorne im Suchpfad statt src. Umstellen, damit "import hdfs.xxx" genauso
    # funktioniert wie bei "uv run hdfs-acl".
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from hdfs.commands import (
    cmd_add,
    cmd_clients,
    cmd_collect,
    cmd_count,
    cmd_export,
    cmd_remove,
    cmd_replace,
    cmd_restore,
    cmd_search,
    cmd_show,
)
from hdfs.common import HdfsError, UsageError
from hdfs.permissions import activate
from hdfs.permissions import find as find_permissions


def positive_int(value: str) -> int:
    """argparse-Typ für --depth und --parallel: ganze Zahl ab 1."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"erwartet eine ganze Zahl ab 1, nicht '{value}'")
    return number


def build_parser() -> argparse.ArgumentParser:
    # Optionsgruppen als Eltern-Parser: jedes Kommando bekommt nur die Optionen,
    # die es braucht (lesende z. B. kein --apply und keine HDFS-Optionen)
    db_opts = argparse.ArgumentParser(add_help=False)
    db_opts.add_argument("--db", default=os.environ.get("HDFS_ACL_DB", "hdfs-acl.db"),
                         help="SQLite-Datei (Default: $HDFS_ACL_DB oder ./hdfs-acl.db)")
    db_opts.add_argument("--permissions", metavar="DATEI",
                         help="Rechte-Tabelle als JSON: Namen der Bits, Recht des Besitzers, "
                              "Verwaltungsrecht (Default: $HDFS_ACL_PERMISSIONS, sonst "
                              "permissions.json neben der DB, sonst READ=1, READ_WRITE=3, ADMIN=19)")

    hdfs_opts = argparse.ArgumentParser(add_help=False)
    hdfs_opts.add_argument("--base-dir", default=os.environ.get("BASE_DIR", "/fs"),
                           help="Basisordner in HDFS (Default: $BASE_DIR oder /fs)")
    hdfs_opts.add_argument("--attr", default=os.environ.get("ACL_ATTR", "user.acl"),
                           help="Name des xattr (Default: $ACL_ATTR oder user.acl)")
    hdfs_opts.add_argument("--depth", type=positive_int, default=os.environ.get("DEPTH", "2"),
                           help="Ebenen unter dem Basisordner (Default: $DEPTH oder 2, "
                                "Account/Container)")
    hdfs_opts.add_argument("--fsimage", default=os.environ.get("HDFS_FSIMAGE"),
                           help="fsimage-Datei (Default: $HDFS_FSIMAGE oder das neueste "
                                "fsimage_<txid> im Ordner der DB)")
    hdfs_opts.add_argument("--live", action="store_true",
                           help="HDFS per getfattr lesen statt aus dem fsimage "
                                "(ein NameNode-RPC je Pfad, für kleine Bestände)")
    hdfs_opts.add_argument("--parallel", type=positive_int, default=os.environ.get("PARALLEL", "8"),
                           help="parallele hdfs-Aufrufe beim Nachlesen/Schreiben "
                                "(Default: $PARALLEL oder 8)")
    hdfs_opts.add_argument("--hadoop-version", default=os.environ.get("HADOOP_VERSION", "3.4.2"),
                           help="Hadoop-Client unter $HADOOP_REPO/downloads/hadoop-<version>, "
                                "wenn HADOOP_HOME nicht gesetzt ist (Default: $HADOOP_VERSION oder 3.4.2)")

    scope_opts = argparse.ArgumentParser(add_help=False)
    scope_opts.add_argument("--accounts", action="store_true",
                            help="auch die Accounts (erste Ebene unter --base-dir) migrieren; "
                                 "ohne nur die Container darunter")
    scope_opts.add_argument("--account", action="append", metavar="ACCOUNT",
                            help="nur diesen Account: Name (account3) oder Pfad (/fs/account3); "
                                 "mehrfach angeben für mehrere")

    write_opts = argparse.ArgumentParser(add_help=False)
    write_mode = write_opts.add_mutually_exclusive_group()
    write_mode.add_argument("--apply", action="store_true", help="Änderungen wirklich schreiben")
    write_mode.add_argument("--generate", metavar="DATEI",
                            help="nichts schreiben, sondern ein Shell-Skript mit allen "
                                 "'hadoop fs -setfattr'-Aufrufen erzeugen (plus Backup)")

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
    p.add_argument("--perm", metavar="RECHT",
                   help="nur genau diese Maske, als Zahl oder Namen: 3, READ_WRITE")
    p.add_argument("--has-bits", metavar="RECHT",
                   help="nur Rechte, die alle Bits enthalten: 16, ADMIN")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("clients", parents=[db_opts, fmt_opts], help="alle clientids auflisten")
    p.set_defaults(func=cmd_clients)

    p = sub.add_parser("count", parents=[db_opts, fmt_opts], help="Einträge in der DB zählen")
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("show", parents=[db_opts], help="ACL eines Pfads anzeigen")
    p.add_argument("path")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export", parents=[db_opts], help="Rechte je ACL als CSV")
    p.add_argument("-o", "--output", help="Datei statt stdout")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("add", parents=[db_opts, hdfs_opts, scope_opts, write_opts],
                       help="clientid ergänzen (Migration)")
    p.add_argument("source", metavar="QUELLE", help="vorhandene clientid")
    p.add_argument("new", metavar="NEU", help="clientid, die dieselben Rechte bekommt")
    p.add_argument("--permission", metavar="RECHT",
                   help="NEU bekommt überall, wo QUELLE steht, dieses Recht statt der Bits "
                        "von QUELLE, als Zahl oder Namen: 3, READ_WRITE")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("replace", parents=[db_opts, hdfs_opts, scope_opts, write_opts],
                       help="clientid ersetzen, auch als Besitzer")
    p.add_argument("old", metavar="ALT", help="clientid, die verschwindet")
    p.add_argument("new", metavar="NEU", help="clientid, die ihre Stelle einnimmt")
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("remove", parents=[db_opts, hdfs_opts, write_opts], help="clientid entfernen")
    p.add_argument("clientid")
    p.add_argument("--new-owner", help="neuer Besitzer, wo CLIENTID Besitzer ist (sonst übersprungen): "
                                       "clientid (@ wird ergänzt) oder Gruppe als "
                                       "'.' und voller DN, z. B. .cn=admins,ou=groups,dc=firma")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("restore", parents=[db_opts, hdfs_opts, write_opts],
                       help="Backup zurückspielen")
    p.add_argument("backup", help="hdfs-acl-backup-*.csv")
    p.add_argument("--force", action="store_true", help="auch seitdem geänderte Pfade zurücksetzen")
    p.set_defaults(func=cmd_restore)

    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        activate(find_permissions(args.permissions, Path(args.db)))
        return args.func(args)
    except (UsageError, HdfsError) as error:
        # erwartete Fehler nur als Meldung, alles andere mit Traceback
        print(f"Fehler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
