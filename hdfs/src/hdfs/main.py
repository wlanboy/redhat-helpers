#!/usr/bin/env python3
"""ACL-Metadaten (xattr user.acl) in HDFS einsammeln, durchsuchen und ändern.

ACL-Format: <besitzer>|<eintrag>=<recht>,...  (Details in hdfs.md)

Kommandos:
  collect             ACLs aus HDFS lesen und in SQLite speichern
  search CLIENTID     Pfade, auf die eine clientid Rechte hat (Wildcards erlaubt)
  clients             alle clientids mit Anzahl Pfade
  show PFAD           ACL und effektive Rechte eines Pfads
  export              effektive Rechte als CSV (Format wie testdata/expected.csv)
  add QUELLE NEU      NEU überall mit den Rechten von QUELLE ergänzen (Migration)
  replace ALT NEU     ALT überall durch NEU ersetzen, auch als Besitzer
  remove CLIENTID     clientid aus allen ACLs entfernen
  restore BACKUP      Änderungen aus einer Backup-Datei zurückdrehen

add/replace/remove/restore lesen HDFS vorher frisch ein, zeigen den Plan und
schreiben erst mit --apply. Vor dem Schreiben wird ein Backup angelegt.

Nur Standardbibliothek, läuft ab Python 3.9.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import posixpath
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- alle eingesammelten Pfade; acl NULL = kein Attribut,
-- acl_from = Pfad, dessen ACL gilt (NULL = keine)
CREATE TABLE node (
    path     TEXT PRIMARY KEY,
    parent   TEXT,
    depth    INTEGER NOT NULL,
    acl      TEXT,
    acl_from TEXT,
    problems TEXT
);
-- eigene ACL-Einträge aller Arten (client/user/group), Besitzer mit owner = 1
CREATE TABLE entry (
    path      TEXT NOT NULL,
    principal TEXT NOT NULL,
    kind      TEXT NOT NULL,
    perm      INTEGER NOT NULL,
    owner     INTEGER NOT NULL
);
-- effektive Rechte je clientid inkl. Vererbung
CREATE TABLE effective (
    path     TEXT NOT NULL,
    clientid TEXT NOT NULL,
    perm     INTEGER NOT NULL,
    owner    INTEGER NOT NULL,
    acl_from TEXT NOT NULL,
    PRIMARY KEY (path, clientid)
);
CREATE INDEX entry_principal ON entry (principal);
CREATE INDEX effective_clientid ON effective (clientid);
"""


class UsageError(Exception):
    pass


class HdfsError(Exception):
    pass


def info(message: str) -> None:
    print(message, file=sys.stderr)


def natural_key(value: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


# --- ACL ----------------------------------------------------------------------


def principal_kind(principal: str) -> str:
    if principal.startswith("@"):
        return "client"
    if principal.startswith("#"):
        return "group"
    return "user"


def split_token(token: str) -> tuple[str, Optional[int]]:
    """'@client1=3' -> ('@client1', 3); ungültige Einträge -> (token, None)."""
    principal, sep, perm = token.rpartition("=")
    if not sep or not principal or not perm.isdigit():
        return token, None
    return principal, int(perm)


@dataclass
class Acl:
    owner: str
    # Einträge im Originalwortlaut, damit unbekannte Formate beim Zurückschreiben
    # erhalten bleiben
    tokens: list

    @classmethod
    def parse(cls, raw: str) -> Acl:
        owner, _, rest = raw.partition("|")
        return cls(owner.strip(), [t.strip() for t in rest.split(",") if t.strip()])

    def render(self) -> str:
        return f"{self.owner}|{','.join(self.tokens)}"

    def client_rights(self) -> dict:
        """Effektive Rechte der @clientids: {clientid: (recht, ist_besitzer)}."""
        rights = {}
        for token in self.tokens:
            principal, perm = split_token(token)
            if perm is not None and principal.startswith("@"):
                if perm > rights.get(principal, (0, False))[0]:
                    rights[principal] = (perm, False)
        if self.owner.startswith("@"):
            rights[self.owner] = (3, True)
        return rights


def acl_problems(raw: str) -> list:
    problems = []
    owner, sep, rest = raw.partition("|")
    if not sep:
        problems.append("kein '|' zwischen Besitzer und Einträgen")
    if not owner.strip():
        problems.append("kein Besitzer")
    for token in (t.strip() for t in rest.split(",")):
        if not token:
            continue
        perm = split_token(token)[1]
        if perm is None:
            problems.append(f"ungültiger Eintrag '{token}'")
        elif perm not in (1, 3):
            problems.append(f"unbekanntes Recht in '{token}'")
    return problems


def resolve_acl_from(nodes: dict) -> dict:
    """Pfad -> Pfad, dessen ACL gilt. Ohne eigene ACL gilt die des Elternordners."""
    acl_from = {}
    for path in sorted(nodes, key=lambda p: p.count("/")):
        if nodes[path] is not None:
            acl_from[path] = path
        else:
            acl_from[path] = acl_from.get(posixpath.dirname(path))
    return acl_from


# --- HDFS ---------------------------------------------------------------------


def java_major(home: Path) -> Optional[int]:
    java = home / "bin" / "java"
    if not java.is_file():
        return None
    proc = subprocess.run([str(java), "-version"], capture_output=True, text=True)
    match = re.search(r'version "(\d+)(?:\.(\d+))?', proc.stderr)
    if not match:
        return None
    major = int(match[1])
    return int(match[2] or 0) if major == 1 else major


def find_java(repo: Path) -> Path:
    """Hadoop 3.4.2 ruft Subject.getSubject() auf, das ab JDK 23 entfernt ist."""
    explicit = os.environ.get("HDFS_JAVA_HOME")
    if explicit:
        home = Path(explicit).expanduser()
        if (java_major(home) or 99) >= 23:
            raise HdfsError(f"HDFS_JAVA_HOME={home} ist kein Java < 23.")
        return home
    sdkman = Path.home() / ".sdkman" / "candidates" / "java"
    candidates = [repo / "downloads" / "jdk-11"]
    candidates += sorted(sdkman.glob("11*")) + sorted(sdkman.glob("17*"))
    # dnf: java-{11,17}-openjdk-headless
    for pattern in ("jre-11*", "java-11*", "jre-17*", "java-17*"):
        candidates += sorted(Path("/usr/lib/jvm").glob(pattern))
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]))
    for home in candidates:
        major = java_major(home)
        if major is not None and major < 23:
            return home
    raise HdfsError("Kein Java 11/17 gefunden (HDFS_JAVA_HOME setzen).")


def stderr_text(proc: subprocess.CompletedProcess) -> str:
    lines = (line for line in proc.stderr.splitlines() if "NativeCodeLoader" not in line)
    return "\n".join(lines).strip()


def decode_value(value: str) -> str:
    """Wert aus getfattr-Ausgabe: "text", 0x<hex> oder 0s<base64>."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    if value[:2].lower() == "0x":
        return bytes.fromhex(value[2:]).decode("utf-8")
    if value[:2].lower() == "0s":
        return base64.b64decode(value[2:]).decode("utf-8")
    return value


def parse_getfattr(output: str, attr: str) -> dict:
    nodes = {}
    path = None
    prefix = attr + "="
    for line in output.splitlines():
        if line.startswith("# file: "):
            path = line[len("# file: "):]
            nodes[path] = None
        elif path is not None and line.startswith(prefix):
            nodes[path] = decode_value(line[len(prefix):])
    return nodes


@dataclass
class Change:
    path: str
    old: Optional[str]
    new: Optional[str]  # None = Attribut entfernen


class Hdfs:
    def __init__(self, parallel: int):
        repo = Path(os.environ.get("HADOOP_REPO", "~/git/hadoop")).expanduser()
        home = Path(os.environ.get("HADOOP_HOME", repo / "downloads" / "hadoop-3.4.2")).expanduser()
        conf = Path(os.environ.get("HADOOP_CONF_DIR", repo / "clientconfig")).expanduser()
        self.bin = home / "bin" / "hdfs"
        if not self.bin.is_file():
            raise HdfsError(f"{self.bin} nicht gefunden (HADOOP_HOME setzen).")
        java_home = find_java(repo)
        self.env = {
            **os.environ,
            "JAVA_HOME": str(java_home),
            "HADOOP_HOME": str(home),
            "HADOOP_CONF_DIR": str(conf),
        }
        self.parallel = parallel
        info(f"HDFS-Client: {self.bin} (JAVA_HOME={java_home}, HADOOP_CONF_DIR={conf})")

    def dfs(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.bin), "dfs", *args], env=self.env, capture_output=True, text=True)

    def read_acls(self, base: str, attr: str, depth: int) -> dict:
        """Ein getfattr je Ebene über Globs (base/*, base/*/*, ...), nicht rekursiv,
        damit Dateien unterhalb der Container nicht mitgelesen werden."""
        patterns = [base + "/*" * level for level in range(1, depth + 1)]
        info(f"Lese {attr} aus {', '.join(patterns)} ...")
        with ThreadPoolExecutor(len(patterns)) as pool:
            procs = list(pool.map(lambda p: self.dfs("-getfattr", "-d", p), patterns))
        nodes = {}
        for pattern, proc in zip(patterns, procs):
            if proc.returncode != 0:
                error = stderr_text(proc)
                if "No such file or directory" in error:
                    continue  # Glob ohne Treffer
                raise HdfsError(f"getfattr {pattern} fehlgeschlagen:\n{error}")
            nodes.update(parse_getfattr(proc.stdout, attr))
        return nodes

    def write(self, changes: list, attr: str) -> list:
        """Setzt/entfernt das Attribut parallel; liefert [(Change, Fehlertext)]."""

        def run(change: Change) -> subprocess.CompletedProcess:
            if change.new is None:
                return self.dfs("-setfattr", "-x", attr, change.path)
            # Anführungszeichen: Wert wird immer als Text behandelt, auch bei 0x/0s
            return self.dfs("-setfattr", "-n", attr, "-v", f'"{change.new}"', change.path)

        failures = []
        with ThreadPoolExecutor(self.parallel) as pool:
            futures = {pool.submit(run, change): change for change in changes}
            for done, future in enumerate(as_completed(futures), 1):
                change, proc = futures[future], future.result()
                status = "ok" if proc.returncode == 0 else "FEHLER"
                info(f"  [{done}/{len(changes)}] {status} {change.path}")
                if proc.returncode != 0:
                    failures.append((change, stderr_text(proc)))
        return failures


# --- Speicher -----------------------------------------------------------------


def save(db: Path, base: str, attr: str, depth: int, nodes: dict) -> None:
    """Schreibt die DB komplett neu (über eine Temp-Datei, damit eine
    abgebrochene Sammlung den alten Stand nicht zerstört)."""
    tmp = db.with_name(db.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    acl_from = resolve_acl_from(nodes)
    rights_cache = {}
    for path, raw in nodes.items():
        parent = posixpath.dirname(path)
        problems = acl_problems(raw) if raw is not None else []
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?)",
            (path, parent if parent in nodes else None, path[len(base):].count("/"),
             raw, acl_from[path], "; ".join(problems) or None),
        )
        if raw is not None:
            acl = Acl.parse(raw)
            if acl.owner:
                con.execute("INSERT INTO entry VALUES (?, ?, ?, 3, 1)",
                            (path, acl.owner, principal_kind(acl.owner)))
            for token in acl.tokens:
                principal, perm = split_token(token)
                if perm is not None:
                    con.execute("INSERT INTO entry VALUES (?, ?, ?, ?, 0)",
                                (path, principal, principal_kind(principal), perm))
        source = acl_from[path]
        if source is not None:
            if source not in rights_cache:
                rights_cache[source] = Acl.parse(nodes[source]).client_rights()
            for clientid, (perm, owner) in rights_cache[source].items():
                con.execute("INSERT INTO effective VALUES (?, ?, ?, ?, ?)",
                            (path, clientid, perm, int(owner), source))

    meta = {
        "base_dir": base,
        "attr": attr,
        "depth": str(depth),
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    }
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    con.commit()
    con.close()
    os.replace(tmp, db)


def open_db(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise UsageError(f"{db} gibt es nicht, erst 'collect' ausführen.")
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    meta = dict(con.execute("SELECT key, value FROM meta"))
    info(f"Stand {meta['collected_at']}, {meta['base_dir']}, {meta['attr']} ({db})")
    return con


def write_backup(db: Path, label: str, changes: list) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db.resolve().parent / f"hdfs-acl-backup-{stamp}-{label}.csv"
    with backup.open("w", newline="") as file:
        writer = csv.writer(file, delimiter=";", lineterminator="\n")
        writer.writerow(["path", "old", "new"])
        for change in changes:
            writer.writerow([change.path, change.old or "", change.new or ""])
    return backup


# --- Ausgabe ------------------------------------------------------------------


def output(columns: list, rows: list, fmt: str, file=sys.stdout) -> None:
    if fmt == "json":
        json.dump([dict(zip(columns, row)) for row in rows], file, indent=2, ensure_ascii=False)
        file.write("\n")
        return
    if fmt == "csv":
        writer = csv.writer(file, delimiter=";", lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([[int(v) if isinstance(v, bool) else v for v in row] for row in rows])
        return
    cells = [[str(c).upper() for c in columns]]
    cells += [["ja" if v is True else "" if v is False else str(v) for v in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    for row in cells:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip(), file=file)


# --- Kommandos ----------------------------------------------------------------


def client_id(value: str, allow_glob: bool = False) -> str:
    value = value.strip()
    if not value.startswith("@"):
        value = "@" + value
    pattern = r"@[^\s|,=#@]+" if allow_glob else r"@[^\s|,=#@*?\[\]]+"
    if not re.fullmatch(pattern, value):
        raise UsageError(f"Ungültige clientid '{value}'.")
    return value


def base_dir(value: str) -> str:
    value = value.rstrip("/")
    if not value.startswith("/"):
        raise UsageError("--base-dir muss ein absoluter Pfad unterhalb von / sein.")
    return value


def refresh(args: argparse.Namespace, hdfs: Hdfs) -> dict:
    base = base_dir(args.base_dir)
    nodes = hdfs.read_acls(base, args.attr, args.depth)
    if not nodes:
        raise HdfsError(f"Unter {base} wurden keine Pfade gefunden.")
    save(Path(args.db), base, args.attr, args.depth, nodes)
    with_acl = sum(raw is not None for raw in nodes.values())
    info(f"{len(nodes)} Pfade, {with_acl} mit {args.attr}, gespeichert in {args.db}")
    return nodes


def cmd_collect(args: argparse.Namespace) -> int:
    nodes = refresh(args, Hdfs(args.parallel))
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
        # nur aktiv gesetzte Einträge, letzte Spalte ist die Roh-ACL des Pfads
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
    rows.sort(key=lambda r: (natural_key(r[0]), natural_key(r[1])))
    columns = ["path", "clientid", "perm", "acl_from"]
    if args.output:
        with open(args.output, "w", newline="") as file:
            output(columns, rows, "csv", file)
        info(f"{len(rows)} Zeilen nach {args.output}")
    else:
        output(columns, rows, "csv")
    return 0


def plan_add(nodes: dict, source: str, new: str) -> tuple:
    changes = []
    found = 0
    for path, raw in nodes.items():
        if raw is None:
            continue  # erbt; wird über den Elternordner mitgeändert
        acl = Acl.parse(raw)
        rights = acl.client_rights()
        if source not in rights:
            continue
        found += 1
        perm = rights[source][0]
        if rights.get(new, (0, False))[0] >= perm:
            continue
        # anhängen; vorhandene schwächere Einträge von NEU werden an ihrer Stelle hochgestuft
        tokens = merge_entries(acl.tokens + [f"{new}={perm}"], new, drop=False)
        changes.append(Change(path, raw, Acl(acl.owner, tokens).render()))
    return changes, found


def merge_entries(tokens: list, principal: str, drop: bool) -> list:
    """Fasst mehrere Einträge von principal zu einem mit dem höchsten Recht an der
    ersten Stelle zusammen; drop=True entfernt sie (principal ist Besitzer)."""
    perms = [perm for p, perm in map(split_token, tokens) if p == principal and perm is not None]
    if not perms:
        return tokens
    merged = []
    placed = drop
    for token in tokens:
        if split_token(token)[0] != principal:
            merged.append(token)
        elif not placed:
            merged.append(f"{principal}={max(perms)}")
            placed = True
    return merged


def plan_replace(nodes: dict, old: str, new: str) -> list:
    changes = []
    for path, raw in nodes.items():
        if raw is None:
            continue  # erbt; wird über den Elternordner mitgeändert
        acl = Acl.parse(raw)
        if acl.owner != old and all(split_token(t)[0] != old for t in acl.tokens):
            continue
        owner = new if acl.owner == old else acl.owner
        # ALT an derselben Stelle in NEU umbenennen; hatte NEU schon einen Eintrag,
        # bleibt einer mit dem höheren Recht, als Besitzer braucht NEU keinen
        tokens = []
        for token in acl.tokens:
            principal, perm = split_token(token)
            tokens.append(f"{new}={perm}" if principal == old and perm is not None else token)
        tokens = merge_entries(tokens, new, drop=owner == new)
        changes.append(Change(path, raw, Acl(owner, tokens).render()))
    return changes


def plan_remove(nodes: dict, clientid: str, new_owner: Optional[str]) -> tuple:
    changes = []
    owned = []
    for path, raw in nodes.items():
        if raw is None:
            continue
        acl = Acl.parse(raw)
        owner = acl.owner
        if owner == clientid:
            if new_owner is None:
                owned.append(path)
                continue
            owner = new_owner
        tokens = [t for t in acl.tokens if split_token(t)[0] != clientid]
        if owner != acl.owner:
            # z. B. nach add ALT NEU: NEU=3 ist als Besitzer überflüssig
            tokens = merge_entries(tokens, owner, drop=True)
        if owner != acl.owner or tokens != acl.tokens:
            changes.append(Change(path, raw, Acl(owner, tokens).render()))
    return changes, owned


def execute(args: argparse.Namespace, hdfs: Hdfs, label: str, changes: list) -> int:
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

    backup = write_backup(Path(args.db), label, changes)
    print(f"Backup: {backup}  (zurückdrehen mit: restore {backup})")
    failures = hdfs.write(changes, args.attr)
    for change, error in failures:
        print(f"Fehler: {change.path}: {error}", file=sys.stderr)

    nodes = refresh(args, hdfs)
    mismatches = [c for c in changes if nodes.get(c.path) != c.new]
    for change in mismatches:
        print(f"Fehler: {change.path} hat nach dem Schreiben {nodes.get(change.path)!r}", file=sys.stderr)
    if failures or mismatches:
        return 1
    print(f"OK: {len(changes)} Pfade geändert und geprüft.")
    return 0


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
        return rc or 2
    return rc


def cmd_restore(args: argparse.Namespace) -> int:
    with open(args.backup, newline="") as file:
        rows = list(csv.DictReader(file, delimiter=";"))
    hdfs = Hdfs(args.parallel)
    nodes = refresh(args, hdfs)
    changes = []
    conflicts = []
    for row in rows:
        path, old, new = row["path"], row["old"] or None, row["new"] or None
        if path not in nodes:
            conflicts.append(f"{path}: existiert nicht mehr")
            continue
        current = nodes[path]
        if current != new and not args.force:
            conflicts.append(f"{path}: wurde seit dem Backup geändert ({current!r})")
            continue
        if current != old:
            changes.append(Change(path, current, old))
    rc = execute(args, hdfs, "restore", changes)
    if conflicts:
        print(f"Übersprungen ({len(conflicts)}, mit --force trotzdem zurücksetzen):", file=sys.stderr)
        for conflict in conflicts:
            print(f"  {conflict}", file=sys.stderr)
        return rc or 2
    return rc


def build_parser() -> argparse.ArgumentParser:
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
    hdfs_opts.add_argument("--parallel", type=int, default=int(os.environ.get("PARALLEL", "8")),
                           help="parallele setfattr-Aufrufe (Default: $PARALLEL oder 8)")

    write_opts = argparse.ArgumentParser(add_help=False)
    write_opts.add_argument("--apply", action="store_true", help="Änderungen wirklich schreiben")

    fmt_opts = argparse.ArgumentParser(add_help=False)
    fmt_opts.add_argument("--format", choices=["table", "csv", "json"], default="table")

    parser = argparse.ArgumentParser(
        prog="hdfs-acl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="KOMMANDO")

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
        print(f"Fehler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
