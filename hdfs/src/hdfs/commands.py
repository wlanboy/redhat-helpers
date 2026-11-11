"""Die Kommandos der Kommandozeile, je eine Funktion cmd_<name>(args) -> Exit-Code.

Lesende Kommandos (search, clients, count, show, export) arbeiten nur auf der DB.
Ändernde Kommandos (add, replace, remove, restore) laufen alle gleich ab:
    1. refresh():  Stand einlesen (fsimage oder live), DB neu schreiben
    2. plan_*():   Liste Change berechnen (changes.py)
    3. recheck():  die betroffenen Pfade live nachlesen, dann neu planen
    4. execute():  Plan zeigen, mit --apply Backup + schreiben + nachprüfen
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from hdfs.acl import client_id, covers, group_id
from hdfs.changes import plan_add, plan_remove, plan_replace, plan_restore
from hdfs.common import HdfsError, UsageError, info, natural_key
from hdfs.fsimage import find_fsimage
from hdfs.hdfs_client import Hdfs, write_script
from hdfs.output import output
from hdfs.permissions import active
from hdfs.store import open_db, read_backup, save, write_backup


def base_dir(value: str) -> str:
    value = value.rstrip("/")
    if not value.startswith("/"):
        raise UsageError("--base-dir muss ein absoluter Pfad unterhalb von / sein.")
    return value


def owner_id(value: str) -> str:
    """Neuer Besitzer von der Kommandozeile: Gruppe ('.' + voller DN) oder clientid.

    Ohne Präfix wird wie überall ein '@' ergänzt. "newapp" als User zum
    Besitzer zu machen wäre ein naheliegender Tippfehler, der die clientid
    aussperrt; User als neuer Besitzer gehen deshalb nicht.
    """
    value = value.strip()
    if value.startswith("."):
        return group_id(value)
    if value.startswith("#"):
        raise UsageError(f"'{value}': Gruppen beginnen mit '.' und vollem DN, "
                         "z. B. .cn=admins,ou=groups,dc=firma.")
    return client_id(value)


def refresh(args: argparse.Namespace, hdfs: Hdfs) -> tuple:
    """Stand einlesen und die DB neu schreiben; liefert ({pfad: roh_acl oder None}, quelle).

    Standard ist das fsimage neben der DB: kein Zugriff auf den NameNode, aber
    so alt wie der Checkpoint. Mit --live wird HDFS direkt gelesen.
    """
    base = base_dir(args.base_dir)
    if args.live:
        nodes = hdfs.read_acls(base, args.attr, args.depth)
        source = "live"
    else:
        image = Path(args.fsimage) if args.fsimage else find_fsimage(Path(args.db).resolve().parent)
        nodes = hdfs.read_fsimage(image, base, args.attr, args.depth)
        stamp = datetime.fromtimestamp(image.stat().st_mtime).isoformat(timespec="seconds")
        source = f"{image.name} (Datei vom {stamp})"
    if not nodes:
        raise HdfsError(f"Unter {base} wurden keine Pfade gefunden.")
    save(Path(args.db), base, args.attr, args.depth, source, nodes)
    with_acl = sum(raw is not None for raw in nodes.values())
    info(f"{len(nodes)} Pfade, {with_acl} mit {args.attr}, aus {source} gespeichert in {args.db}")
    return nodes, source


def recheck(args: argparse.Namespace, hdfs: Hdfs, nodes: dict, paths: list) -> dict:
    """Liest die betroffenen Pfade live nach und übernimmt ihre aktuellen Werte.

    Der eingelesene Stand (vor allem das fsimage) kann veraltet sein. Geplant
    wird deshalb ein zweites Mal auf den aktuellen Werten der Pfade, die der
    erste Plan ändern würde. Das kostet nur einen Lesezugriff je betroffenem
    Pfad. Pfade, die erst seit dem Einlesen betroffen sind, fehlen weiterhin.
    """
    if not paths:
        return nodes
    live = hdfs.read_paths(paths, args.attr)
    changed = sum(path in live and live[path] != nodes.get(path) for path in paths)
    for path in paths:
        if path in live:
            nodes[path] = live[path]
        else:
            nodes.pop(path, None)  # gelöscht: nicht anfassen (andere Fehler brechen ab)
    info(f"{len(paths)} betroffene Pfade live nachgelesen: {changed} seitdem geändert, "
         f"{len(paths) - len(live)} gelöscht")
    return nodes


def execute(args: argparse.Namespace, hdfs: Hdfs, label: str, changes: list,
            nodes: dict, source: str) -> int:
    """Gemeinsamer Ablauf aller ändernden Kommandos.

    Ohne --apply endet er nach der Anzeige (Dry-Run). Mit --generate DATEI:
    Backup schreiben und die Änderungen als Shell-Skript mit "hadoop fs"
    ausgeben, HDFS bleibt unverändert. Mit --apply:
    Backup schreiben, dann HDFS ändern, dann die geänderten Pfade neu lesen
    und mit dem geplanten Wert vergleichen. Die DB bekommt die gelesenen Werte.
    Exit-Code 1, wenn ein Aufruf fehlschlug oder ein Wert nicht stimmt.
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
    if args.generate:
        # Backup auch hier: wer das Skript ausführt, kann danach mit restore zurück
        backup = write_backup(Path(args.db), label, changes)
        script = Path(args.generate)
        write_script(script, changes, args.attr, [
            f"hdfs-acl {label}: {len(changes)} Pfade, erzeugt {datetime.now().isoformat(timespec='seconds')}",
            f"geplant auf: {source}",
            f"zurückdrehen: uv run main.py restore {backup} --apply",
        ])
        print(f"Backup: {backup}  (zurückdrehen mit: restore {backup})")
        print(f"Skript: {script}  (nichts geschrieben, Skript prüfen und ausführen)")
        return 0
    if not args.apply:
        print("Dry-Run: nichts geschrieben. Mit --apply ausführen.")
        return 0

    # Backup vor dem ersten Schreibzugriff, damit auch ein Abbruch mittendrin
    # mit restore zurückgedreht werden kann
    backup = write_backup(Path(args.db), label, changes)
    print(f"Backup: {backup}  (zurückdrehen mit: restore {backup})")
    errors = hdfs.write(changes, args.attr)
    for error in errors:
        print(f"Fehler: {error}", file=sys.stderr)

    # Nachprüfen: der Exit-Code von setfattr allein reicht nicht, gelesen wird
    # der tatsächliche Wert. Nebenbei kommt die DB auf den neuen Stand.
    # strict=False: nach dem Schreiben nicht abbrechen, ein nicht lesbarer Pfad
    # zählt unten als Fehler, und die DB bekommt trotzdem die gelesenen Werte
    live = hdfs.read_paths([c.path for c in changes], args.attr, strict=False)
    mismatches = [c for c in changes if c.path not in live or live[c.path] != c.new]
    for change in mismatches:
        actual = repr(live[change.path]) if change.path in live else "(nicht lesbar)"
        print(f"Fehler: {change.path} hat nach dem Schreiben {actual}", file=sys.stderr)
    nodes.update(live)
    save(Path(args.db), base_dir(args.base_dir), args.attr, args.depth, f"{source} + {label}", nodes)
    if errors or mismatches:
        return 1
    print(f"OK: {len(changes)} Pfade geändert und geprüft.")
    return 0


# --- lesende Kommandos --------------------------------------------------------


def cmd_collect(args: argparse.Namespace) -> int:
    refresh(args, Hdfs(args.parallel, args.hadoop_version))
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
        # Besitzer + zusätzliche Einträge werden zu einer Zeile verodert (SQLite
        # hat kein bitweises OR als Aggregat)
        columns = ["path", "clientid", "perm", "rights", "owner", "acl"]
        merged = {}
        for path, clientid, perm, owner, acl in con.execute(
                """SELECT e.path, e.principal, e.perm, e.owner, n.acl
                   FROM entry e JOIN node n ON n.path = e.path
                   WHERE e.kind = 'client' AND e.principal GLOB ?""", (pattern,)):
            old_perm, old_owner, _ = merged.get((path, clientid), (0, 0, acl))
            merged[(path, clientid)] = (old_perm | perm, old_owner | owner, acl)
        found = [(path, clientid, *rest) for (path, clientid), rest in merged.items()]
    else:
        columns = ["path", "clientid", "perm", "rights", "owner", "acl_from"]
        found = con.execute(
            "SELECT path, clientid, perm, owner, acl_from FROM effective WHERE clientid GLOB ?",
            (pattern,))
    # --perm/--has-bits als Zahl oder Namen aus der Rechte-Tabelle
    table = active()
    exact = table.parse(args.perm) if args.perm is not None else None
    bits = table.parse(args.has_bits) if args.has_bits is not None else None
    rows = [
        (path, clientid, perm, table.describe(perm), bool(owner), last)
        for path, clientid, perm, owner, last in found
        if (exact is None or perm == exact) and (bits is None or covers(perm, bits))
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
    # perms: wie oft welche Maske vorkommt, z. B. "1:12 3:40 19:2"
    perms = {}
    for clientid, perm, count in con.execute(
            "SELECT clientid, perm, COUNT(*) FROM effective GROUP BY clientid, perm ORDER BY perm"):
        perms.setdefault(clientid, []).append(f"{perm}:{count}")
    rows = [
        (clientid, paths, " ".join(perms[clientid]), owner, own.get(clientid, 0))
        for clientid, paths, owner in con.execute(
            "SELECT clientid, COUNT(*), SUM(owner) FROM effective GROUP BY clientid")
    ]
    rows.sort(key=lambda r: natural_key(r[0]))
    output(["clientid", "paths", "perms", "owner", "own_acls"], rows, args.format)
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    queries = [
        ("paths", "alle eingesammelten Pfade", "SELECT COUNT(*) FROM node"),
        ("paths_with_acl", "Pfade mit eigener ACL", "SELECT COUNT(*) FROM node WHERE acl IS NOT NULL"),
        ("paths_inherited", "Pfade, die eine ACL erben",
         "SELECT COUNT(*) FROM node WHERE acl_from IS NOT NULL AND acl_from != path"),
        ("paths_without_acl", "Pfade ohne geltende ACL", "SELECT COUNT(*) FROM node WHERE acl_from IS NULL"),
        ("paths_with_problems", "ACLs mit Formatfehlern",
         "SELECT COUNT(*) FROM node WHERE problems IS NOT NULL"),
        ("entries", "eigene ACL-Einträge (Tabelle entry)", "SELECT COUNT(*) FROM entry"),
        ("entries_client", "davon clientids", "SELECT COUNT(*) FROM entry WHERE kind = 'client'"),
        ("entries_owner", "davon Besitzer", "SELECT COUNT(*) FROM entry WHERE owner = 1"),
        ("effective", "effektive Rechte Pfad/clientid (Tabelle effective)",
         "SELECT COUNT(*) FROM effective"),
        ("clientids", "clientids mit Rechten", "SELECT COUNT(DISTINCT clientid) FROM effective"),
    ]
    rows = [(name, con.execute(sql).fetchone()[0], text) for name, text, sql in queries]
    output(["name", "count", "description"], rows, args.format)
    return 0


def rights_suffix(perm: int) -> str:
    """' [READ_WRITE]' für show; leer, wenn die Rechte-Tabelle keine Namen hat."""
    names = active().describe(perm)
    return f" [{names}]" if names else ""


def cmd_show(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    path = args.path.rstrip("/")
    row = con.execute("SELECT acl, parent, problems FROM node WHERE path = ?", (path,)).fetchone()
    if row is None:
        raise UsageError(f"{path} ist nicht eingesammelt.")
    acl, parent, problems = row
    # Vorfahren mit ACL, von oben nach unten: sie werden zusammengeführt
    inherited = []
    while parent is not None:
        parent_acl, grandparent = con.execute(
            "SELECT acl, parent FROM node WHERE path = ?", (parent,)).fetchone()
        if parent_acl is not None:
            inherited.insert(0, (parent, parent_acl))
        parent = grandparent
    print(f"Pfad:    {path}")
    print(f"ACL:     {acl if acl is not None else '(keine)'}")
    for ancestor, ancestor_acl in inherited:
        print(f"Erbt:    {ancestor_acl}  (von {ancestor})")
    if acl is None and not inherited:
        print("Gilt:    keine ACL")
    if problems:
        print(f"Warnung: {problems}")
    rights = con.execute("SELECT clientid, perm, owner, acl_from FROM effective WHERE path = ?",
                         (path,)).fetchall()
    rights.sort(key=lambda r: natural_key(r[0]))
    print("Rechte:  " + (", ".join(
        f"{clientid}={perm}{rights_suffix(perm)}" + (" (Besitzer)" if owner else "")
        + (f" (aus {acl_from})" if acl_from != path else "")
        for clientid, perm, owner, acl_from in rights
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
    hdfs = Hdfs(args.parallel, args.hadoop_version)
    nodes, origin = refresh(args, hdfs)
    changes, _, _ = plan_add(nodes, source, new)
    nodes = recheck(args, hdfs, nodes, [c.path for c in changes])
    changes, found, excess = plan_add(nodes, source, new)
    if not found:
        print(f"{source} steht in keiner ACL.")
        return 0
    rc = execute(args, hdfs, f"add-{new[1:]}", changes, nodes, origin)
    if excess:
        # Container-ACLs können nur Werte >= 1 setzen: wo QUELLE und NEU nichts
        # hatten, lässt sich ein geerbter Eintrag von NEU nicht wegnehmen
        print(f"Warnung: {new} bekommt an {len(excess)} Pfaden über die Vererbung Rechte, "
              f"die {source} dort nicht hat (eigener Besitzer im Container, {source} "
              "nicht gelistet). Einschränken geht nicht, eine Maske 0 gibt es nicht:",
              file=sys.stderr)
        for path in sorted(excess, key=natural_key):
            print(f"  {path}", file=sys.stderr)
    return rc


def cmd_replace(args: argparse.Namespace) -> int:
    old, new = client_id(args.old), client_id(args.new)
    if old == new:
        raise UsageError("ALT und NEU sind gleich.")
    hdfs = Hdfs(args.parallel, args.hadoop_version)
    nodes, origin = refresh(args, hdfs)
    nodes = recheck(args, hdfs, nodes, [c.path for c in plan_replace(nodes, old, new)])
    changes = plan_replace(nodes, old, new)
    if not changes:
        print(f"{old} steht in keiner ACL.")
        return 0
    return execute(args, hdfs, f"replace-{old[1:]}-{new[1:]}", changes, nodes, origin)


def cmd_remove(args: argparse.Namespace) -> int:
    clientid = client_id(args.clientid)
    new_owner = owner_id(args.new_owner) if args.new_owner else None
    if new_owner == clientid:
        raise UsageError(f"--new-owner {new_owner} ist die clientid, die entfernt werden soll.")
    hdfs = Hdfs(args.parallel, args.hadoop_version)
    nodes, origin = refresh(args, hdfs)
    changes, owned = plan_remove(nodes, clientid, new_owner)
    nodes = recheck(args, hdfs, nodes, [c.path for c in changes] + owned)
    changes, owned = plan_remove(nodes, clientid, new_owner)
    rc = execute(args, hdfs, f"remove-{clientid[1:]}", changes, nodes, origin)
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
    hdfs = Hdfs(args.parallel, args.hadoop_version)
    nodes, origin = refresh(args, hdfs)
    # die Pfade des Backups immer live lesen: das fsimage kennt die
    # Änderungen, die zurückgedreht werden sollen, meist noch gar nicht
    nodes = recheck(args, hdfs, nodes, [entry.path for entry in backup])
    changes, conflicts = plan_restore(nodes, backup, args.force)
    rc = execute(args, hdfs, "restore", changes, nodes, origin)
    if conflicts:
        print(f"Übersprungen ({len(conflicts)}, mit --force trotzdem zurücksetzen):", file=sys.stderr)
        for conflict in conflicts:
            print(f"  {conflict}", file=sys.stderr)
        return rc or 2
    return rc
