"""Die Kommandos der Kommandozeile, je eine Funktion cmd_<name>(args) -> Exit-Code.

Lesende Kommandos (search, clients, show, export) arbeiten nur auf der DB.
Ändernde Kommandos (add, replace, remove, restore) laufen alle gleich ab:
    1. refresh():  HDFS frisch einlesen, DB neu schreiben
    2. plan_*():   Liste Change berechnen (changes.py)
    3. execute():  Plan zeigen, mit --apply Backup + schreiben + nachprüfen
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from hdfs.acl import client_id
from hdfs.changes import plan_add, plan_remove, plan_replace, plan_restore
from hdfs.common import HdfsError, UsageError, info, natural_key
from hdfs.hdfs_client import Hdfs
from hdfs.output import output
from hdfs.store import open_db, read_backup, save, write_backup


def base_dir(value: str) -> str:
    value = value.rstrip("/")
    if not value.startswith("/"):
        raise UsageError("--base-dir muss ein absoluter Pfad unterhalb von / sein.")
    return value


def refresh(args: argparse.Namespace, hdfs: Hdfs) -> dict:
    """HDFS einlesen und die DB neu schreiben; liefert {pfad: roh_acl oder None}.

    Ändernde Kommandos planen immer auf diesem frischen Stand, nie auf einer
    alten DB, damit keine zwischenzeitlichen Änderungen überschrieben werden.
    """
    base = base_dir(args.base_dir)
    nodes = hdfs.read_acls(base, args.attr, args.depth)
    if not nodes:
        raise HdfsError(f"Unter {base} wurden keine Pfade gefunden.")
    save(Path(args.db), base, args.attr, args.depth, nodes)
    with_acl = sum(raw is not None for raw in nodes.values())
    info(f"{len(nodes)} Pfade, {with_acl} mit {args.attr}, gespeichert in {args.db}")
    return nodes


def execute(args: argparse.Namespace, hdfs: Hdfs, label: str, changes: list) -> int:
    """Gemeinsamer Ablauf aller ändernden Kommandos.

    Ohne --apply endet er nach der Anzeige (Dry-Run). Mit --apply:
    Backup schreiben, dann HDFS ändern, dann HDFS neu einlesen und jeden
    Pfad mit dem geplanten Wert vergleichen. Exit-Code 1, wenn ein Aufruf
    fehlschlug oder ein Wert nicht stimmt.
    """
    changes.sort(key=lambda c: natural_key(c.path))
    for change in changes:
        print(change.path)
        print(f"  alt: {change.old if change.old is not None else '(kein Attribut)'}")
        print(f"  neu: {change.new if change.new is not None else '(Attribut entfernen)'}")
    if not changes:
        print("Keine Änderungen nötig.")
        return 0
    print(f"{len(changes)} Pfade betroffen.")
    if not args.apply:
        print("Dry-Run: nichts geschrieben. Mit --apply ausführen.")
        return 0

    # Backup vor dem ersten Schreibzugriff, damit auch ein Abbruch mittendrin
    # mit restore zurückgedreht werden kann
    backup = write_backup(Path(args.db), label, changes)
    print(f"Backup: {backup}  (zurückdrehen mit: restore {backup})")
    failures = hdfs.write(changes, args.attr)
    for change, error in failures:
        print(f"Fehler: {change.path}: {error}", file=sys.stderr)

    # Nachprüfen: der Exit-Code von setfattr allein reicht nicht, gelesen wird
    # der tatsächliche Wert. Nebenbei ist die DB danach wieder aktuell.
    nodes = refresh(args, hdfs)
    mismatches = [c for c in changes if nodes.get(c.path) != c.new]
    for change in mismatches:
        print(f"Fehler: {change.path} hat nach dem Schreiben {nodes.get(change.path)!r}", file=sys.stderr)
    if failures or mismatches:
        return 1
    print(f"OK: {len(changes)} Pfade geändert und geprüft.")
    return 0


# --- lesende Kommandos --------------------------------------------------------


def cmd_collect(args: argparse.Namespace) -> int:
    refresh(args, Hdfs(args.parallel))
    con = sqlite3.connect(args.db)
    clients = con.execute("SELECT COUNT(DISTINCT clientid) FROM effective").fetchone()[0]
    print(f"{clients} clientids mit Rechten.")
    problems = con.execute("SELECT path, problems FROM node WHERE problems IS NOT NULL").fetchall()
    for path, problem in sorted(problems, key=lambda r: natural_key(r[0])):
        print(f"Warnung: {path}: {problem}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    pattern = client_id(args.clientid, allow_glob=True)
    con = open_db(Path(args.db))
    if args.own:
        # nur aktiv gesetzte Einträge, letzte Spalte ist die Roh-ACL des Pfads;
        # GROUP BY/MAX fasst Besitzer + zusätzlichen Eintrag zu einer Zeile zusammen
        columns = ["path", "clientid", "perm", "owner", "acl"]
        sql = """SELECT e.path, e.principal, MAX(e.perm), MAX(e.owner), n.acl
                 FROM entry e JOIN node n ON n.path = e.path
                 WHERE e.kind = 'client' AND e.principal GLOB ?
                 GROUP BY e.path, e.principal"""
    else:
        columns = ["path", "clientid", "perm", "owner", "acl_from"]
        sql = "SELECT path, clientid, perm, owner, acl_from FROM effective WHERE clientid GLOB ?"
    rows = [
        (path, clientid, perm, bool(owner), last)
        for path, clientid, perm, owner, last in con.execute(sql, (pattern,))
        if args.perm is None or perm == args.perm
    ]
    rows.sort(key=lambda r: (natural_key(r[1]), natural_key(r[0])))
    output(columns, rows, args.format)
    info(f"{len(rows)} Treffer")
    return 0


def cmd_clients(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    # own_acls: an wie vielen Pfaden die clientid selbst eingetragen ist (ohne Vererbung)
    own = dict(con.execute(
        "SELECT principal, COUNT(DISTINCT path) FROM entry WHERE kind = 'client' GROUP BY principal"))
    rows = [
        (clientid, paths, perm3, perm1, owner, own.get(clientid, 0))
        for clientid, paths, perm3, perm1, owner in con.execute(
            """SELECT clientid, COUNT(*), SUM(perm = 3), SUM(perm = 1), SUM(owner)
               FROM effective GROUP BY clientid""")
    ]
    rows.sort(key=lambda r: natural_key(r[0]))
    output(["clientid", "paths", "perm3", "perm1", "owner", "own_acls"], rows, args.format)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    path = args.path.rstrip("/")
    row = con.execute("SELECT acl, acl_from, problems FROM node WHERE path = ?", (path,)).fetchone()
    if row is None:
        raise UsageError(f"{path} ist nicht eingesammelt.")
    acl, acl_from, problems = row
    print(f"Pfad:    {path}")
    print(f"ACL:     {acl if acl is not None else '(keine)'}")
    if acl_from is None:
        print("Gilt:    keine ACL")
    elif acl_from != path:
        inherited = con.execute("SELECT acl FROM node WHERE path = ?", (acl_from,)).fetchone()[0]
        print(f"Gilt:    {inherited}  (geerbt von {acl_from})")
    if problems:
        print(f"Warnung: {problems}")
    rights = con.execute("SELECT clientid, perm, owner FROM effective WHERE path = ?", (path,)).fetchall()
    rights.sort(key=lambda r: natural_key(r[0]))
    print("Rechte:  " + (", ".join(
        f"{clientid}={perm}" + (" (Besitzer)" if owner else "") for clientid, perm, owner in rights
    ) or "keine clientid"))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    rows = con.execute("SELECT path, clientid, perm, acl_from FROM effective").fetchall()
    # gleiche Sortierung wie test.sh (sort -V), damit ein diff gegen expected.csv passt
    rows.sort(key=lambda r: (natural_key(r[0]), natural_key(r[1])))
    columns = ["path", "clientid", "perm", "acl_from"]
    if args.output:
        with open(args.output, "w", newline="") as file:
            output(columns, rows, "csv", file)
        info(f"{len(rows)} Zeilen nach {args.output}")
    else:
        output(columns, rows, "csv")
    return 0


# --- ändernde Kommandos -------------------------------------------------------


def cmd_add(args: argparse.Namespace) -> int:
    source, new = client_id(args.source), client_id(args.new)
    if source == new:
        raise UsageError("QUELLE und NEU sind gleich.")
    hdfs = Hdfs(args.parallel)
    changes, found = plan_add(refresh(args, hdfs), source, new)
    if not found:
        print(f"{source} steht in keiner ACL.")
        return 0
    return execute(args, hdfs, f"add-{new[1:]}", changes)


def cmd_replace(args: argparse.Namespace) -> int:
    old, new = client_id(args.old), client_id(args.new)
    if old == new:
        raise UsageError("ALT und NEU sind gleich.")
    hdfs = Hdfs(args.parallel)
    changes = plan_replace(refresh(args, hdfs), old, new)
    if not changes:
        print(f"{old} steht in keiner ACL.")
        return 0
    return execute(args, hdfs, f"replace-{old[1:]}-{new[1:]}", changes)


def cmd_remove(args: argparse.Namespace) -> int:
    clientid = client_id(args.clientid)
    new_owner = args.new_owner.strip() if args.new_owner else None
    # neuer Besitzer darf auch User oder #gruppe sein, nur keine Trennzeichen
    if new_owner is not None and not re.fullmatch(r"[^\s|,=]+", new_owner):
        raise UsageError(f"Ungültiger Besitzer '{new_owner}'.")
    hdfs = Hdfs(args.parallel)
    changes, owned = plan_remove(refresh(args, hdfs), clientid, new_owner)
    rc = execute(args, hdfs, f"remove-{clientid[1:]}", changes)
    if owned:
        print(f"Übersprungen: {clientid} ist Besitzer von {len(owned)} Pfaden (--new-owner angeben):",
              file=sys.stderr)
        for path in sorted(owned, key=natural_key):
            print(f"  {path}", file=sys.stderr)
        # Exit-Code 2 = teilweise erledigt; ein echter Fehler (1) hat Vorrang
        return rc or 2
    return rc


def cmd_restore(args: argparse.Namespace) -> int:
    backup = read_backup(args.backup)
    hdfs = Hdfs(args.parallel)
    changes, conflicts = plan_restore(refresh(args, hdfs), backup, args.force)
    rc = execute(args, hdfs, "restore", changes)
    if conflicts:
        print(f"Übersprungen ({len(conflicts)}, mit --force trotzdem zurücksetzen):", file=sys.stderr)
        for conflict in conflicts:
            print(f"  {conflict}", file=sys.stderr)
        return rc or 2
    return rc
