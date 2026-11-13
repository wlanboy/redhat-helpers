"""Ablage: SQLite-Datenbank mit dem eingesammelten Stand und Backup-Dateien.

Die DB ist ein Abbild von HDFS zum Zeitpunkt des Einlesens (fsimage oder
live), nach ändernden Kommandos inklusive deren Änderungen. Sie wird jedes Mal
komplett neu geschrieben, nie fortgeschrieben. Lesende Kommandos (search,
clients, show, export) brauchen deshalb kein HDFS.
"""

from __future__ import annotations

import csv
import itertools
import os
import posixpath
import sqlite3
from datetime import datetime
from pathlib import Path

from hdfs.acl import Acl, acl_problems, is_ldap, principal_kind, split_token
from hdfs.changes import Change
from hdfs.common import UsageError, info
from hdfs.permissions import active

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- alle eingesammelten Pfade; acl NULL = kein Attribut
CREATE TABLE node (
    path     TEXT PRIMARY KEY,
    parent   TEXT,
    depth    INTEGER NOT NULL,
    acl      TEXT,
    problems TEXT
);
-- ACL-Einträge (client/user/group, ohne LDAP), Besitzer mit owner = 1
CREATE TABLE entry (
    path      TEXT NOT NULL,
    principal TEXT NOT NULL,
    kind      TEXT NOT NULL,
    perm      INTEGER NOT NULL,
    owner     INTEGER NOT NULL
);
-- Rechte je @clientid und ACL: Einträge verodert, Besitzer mit Besitzer-Recht;
-- nur was an einem Pfad selbst steht, nichts Geerbtes
CREATE TABLE rights (
    path     TEXT NOT NULL,
    clientid TEXT NOT NULL,
    perm     INTEGER NOT NULL,
    owner    INTEGER NOT NULL,
    PRIMARY KEY (path, clientid)
);
CREATE INDEX entry_principal ON entry (principal);
CREATE INDEX rights_clientid ON rights (clientid);
"""


def save(db: Path, base: str, attr: str, depth: int, origin: str, nodes: dict) -> None:
    """Schreibt die DB komplett neu (über eine Temp-Datei, damit eine
    abgebrochene Sammlung den alten Stand nicht zerstört).

    origin beschreibt die Herkunft (fsimage-Datei oder live) für die Anzeige.
    """
    tmp = db.with_name(db.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    for path, raw in nodes.items():
        parent = posixpath.dirname(path)
        problems = acl_problems(raw) if raw is not None else []
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?)",
            (path, parent if parent in nodes else None, path[len(base):].count("/"),
             raw, "; ".join(problems) or None),
        )
        if raw is None:
            continue
        acl = Acl.parse(raw)
        if acl.owner and not is_ldap(acl.owner):
            con.execute("INSERT INTO entry VALUES (?, ?, ?, ?, 1)",
                        (path, acl.owner, principal_kind(acl.owner), active().owner))
        for token in acl.tokens:
            principal, perm = split_token(token)
            if perm is not None and not is_ldap(token):
                con.execute("INSERT INTO entry VALUES (?, ?, ?, ?, 0)",
                            (path, principal, principal_kind(principal), perm))
        for clientid, (perm, owner) in acl.client_rights().items():
            con.execute("INSERT INTO rights VALUES (?, ?, ?, ?)", (path, clientid, perm, int(owner)))

    meta = {
        "base_dir": base,
        "attr": attr,
        "depth": str(depth),
        "source": origin,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        # rights und entry hängen am Besitzer-Recht; open_db() warnt, wenn
        # später mit einer anderen Tabelle gelesen wird
        "owner_perm": str(active().owner),
    }
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    con.commit()
    con.close()
    # os.replace ist atomar: Leser sehen entweder die alte oder die neue DB
    os.replace(tmp, db)


def open_db(db: Path) -> sqlite3.Connection:
    """Öffnet die DB nur lesend und meldet auf stderr, von wann der Stand ist."""
    if not db.is_file():
        raise UsageError(f"{db} gibt es nicht, erst 'collect' ausführen.")
    # mode=ro: sqlite3.connect würde eine fehlende Datei sonst still neu anlegen
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "rights" not in tables:
        raise UsageError(f"{db} hat das alte Format (mit Vererbung), erst 'collect' ausführen.")
    meta = dict(con.execute("SELECT key, value FROM meta"))
    info(f"Stand {meta['collected_at']} aus {meta.get('source', '?')}, {meta['base_dir']}, {meta['attr']} ({db})")
    if "owner_perm" in meta and int(meta["owner_perm"]) != active().owner:
        info(f"Warnung: eingesammelt mit Besitzer-Recht {meta['owner_perm']}, die Rechte-Tabelle "
             f"({active().origin}) sagt {active().owner}. Erneut 'collect' ausführen.")
    return con


def write_backup(db: Path, label: str, changes: list) -> Path:
    """Schreibt die geplanten Changes als CSV (path;old;new;empty) neben die DB.

    Ein leeres Feld steht für "kein Attribut". Ein vorhandenes, aber leeres
    Attribut ("") nennt die Spalte empty ("old", "new" oder "old,new"), denn
    in der CSV sähe es sonst genauso aus. read_backup() liest die Datei für
    restore wieder ein.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = db.resolve().parent
    # Zwei Backups derselben Aktion in derselben Sekunde: "x" legt nur neu an,
    # das zweite bekommt eine Nummer statt das erste zu überschreiben. Die
    # Nummer steht vor der Aktion, damit "hdfs-acl-backup-*-<aktion>.csv"
    # (latest_backup in test.sh/migrate.sh) weiter passt.
    for number in itertools.count(1):
        suffix = "" if number == 1 else f"-{number}"
        backup = folder / f"hdfs-acl-backup-{stamp}{suffix}-{label}.csv"
        try:
            file = backup.open("x", newline="")
        except FileExistsError:
            continue
        break
    with file:
        # ';' als Trenner, weil die ACL-Werte selbst ',' und '|' enthalten
        writer = csv.writer(file, delimiter=";", lineterminator="\n")
        writer.writerow(["path", "old", "new", "empty"])
        for change in changes:
            empty = ",".join(name for name, value in (("old", change.old), ("new", change.new))
                             if value == "")
            writer.writerow([change.path, change.old or "", change.new or "", empty])
    return backup


def read_backup(path: str) -> list:
    """Gegenstück zu write_backup(): CSV -> [Change].

    Backups ohne Spalte empty (ältere Versionen) kennen kein leeres Attribut,
    dort ist jedes leere Feld "kein Attribut".
    """
    def value(row: dict, name: str) -> str | None:
        if row[name]:
            return row[name]
        return "" if name in (row.get("empty") or "").split(",") else None

    with open(path, newline="") as file:
        return [Change(row["path"], value(row, "old"), value(row, "new"))
                for row in csv.DictReader(file, delimiter=";")]
