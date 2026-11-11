"""Tests für hdfs.store, hdfs.output und hdfs.common: DB, Backup-CSV, Ausgabeformate."""

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import ClassVar

from hdfs.changes import Change
from hdfs.common import UsageError, natural_key
from hdfs.output import output
from hdfs.store import open_db, read_backup, save, write_backup

NODES = {
    "/fs/account1": "@client1|@client2=3,svc=1,.cn=ops,ou=groups,dc=firma=3,cn=doc,ou=x",
    "/fs/account1/container1": None,
    "/fs/account1/container2": "@client4|@client1=16,@client2=1",
    "/fs/account1/container3": "|@client5=1",
    "/fs/account2": None,
    "/fs/account2/container1": "|@client6=1",
    "/fs/account3": "kaputt",
}


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "hdfs-acl.db"

    def test_save_and_open(self):
        save(self.db, "/fs", "user.acl", 2, "test", NODES)
        self.assertFalse(self.db.with_name(self.db.name + ".tmp").exists())
        with redirect_stderr(io.StringIO()) as err:
            con = open_db(self.db)
        self.assertIn("aus test, /fs, user.acl", err.getvalue())

        node = {row[0]: row[1:] for row in con.execute("SELECT path, parent, depth, acl_from FROM node")}
        self.assertEqual(node["/fs/account1"], (None, 1, "/fs/account1"))
        self.assertEqual(node["/fs/account1/container1"], ("/fs/account1", 2, "/fs/account1"))
        self.assertEqual(node["/fs/account2"], (None, 1, None))
        problems = dict(con.execute("SELECT path, problems FROM node WHERE problems IS NOT NULL"))
        # leerer Besitzer ohne geerbten ist eine Warnung, mit geerbtem nicht
        self.assertEqual(problems, {"/fs/account2/container1": "kein Besitzer (auch nicht geerbt)",
                                    "/fs/account3": "kein '|' zwischen Besitzer und Einträgen"})

        entries = set(con.execute("SELECT principal, kind, perm, owner FROM entry WHERE path = '/fs/account1'"))
        self.assertEqual(entries, {("@client1", "client", 19, 1), ("@client2", "client", 3, 0),
                                   ("svc", "user", 1, 0), (".cn=ops,ou=groups,dc=firma", "group", 3, 0)})

        effective = set(con.execute("SELECT path, clientid, perm, owner, acl_from FROM effective"))
        self.assertEqual(effective, {
            ("/fs/account1", "@client1", 19, 1, "/fs/account1"),
            ("/fs/account1", "@client2", 3, 0, "/fs/account1"),
            ("/fs/account1/container1", "@client1", 19, 1, "/fs/account1"),
            ("/fs/account1/container1", "@client2", 3, 0, "/fs/account1"),
            # eigener Besitzer, @client2 eingeschränkt
            ("/fs/account1/container2", "@client4", 19, 1, "/fs/account1/container2"),
            ("/fs/account1/container2", "@client1", 16, 0, "/fs/account1/container2"),
            ("/fs/account1/container2", "@client2", 1, 0, "/fs/account1/container2"),
            # Besitzer geerbt, erweitert um @client5
            ("/fs/account1/container3", "@client1", 19, 1, "/fs/account1"),
            ("/fs/account1/container3", "@client2", 3, 0, "/fs/account1"),
            ("/fs/account1/container3", "@client5", 1, 0, "/fs/account1/container3"),
            ("/fs/account2/container1", "@client6", 1, 0, "/fs/account2/container1"),
        })

    def test_save_replaces_db(self):
        save(self.db, "/fs", "user.acl", 2, "alt", NODES)
        save(self.db, "/fs", "user.acl", 2, "neu", {"/fs/x": None})
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT path FROM node").fetchall(), [("/fs/x",)])
        self.assertEqual(dict(con.execute("SELECT key, value FROM meta"))["source"], "neu")

    def test_open_db_missing(self):
        with self.assertRaises(UsageError):
            open_db(self.dir / "fehlt.db")
        self.assertFalse((self.dir / "fehlt.db").exists())

    def test_backup_roundtrip(self):
        changes = [Change("/fs/a", "@o|@a=1,@b=2", "@o|@a=1,@b=2,@n=1"),
                   Change("/fs/b", None, "@o|"), Change("/fs/c;d", "@o|", None)]
        backup = write_backup(self.db, "add-n", changes)
        self.assertEqual(backup.parent, self.dir)
        self.assertRegex(backup.name, r"^hdfs-acl-backup-\d{8}-\d{6}-add-n\.csv$")
        self.assertEqual(read_backup(str(backup)), changes)


class OutputTest(unittest.TestCase):
    columns: ClassVar[list[str]] = ["path", "perm", "owner"]
    rows: ClassVar[list[tuple]] = [("/fs/a", 3, True), ("/fs/long/path", 19, False)]

    def render(self, fmt: str) -> str:
        buffer = io.StringIO()
        output(self.columns, self.rows, fmt, buffer)
        return buffer.getvalue()

    def test_csv(self):
        self.assertEqual(list(csv.reader(io.StringIO(self.render("csv")), delimiter=";")),
                         [["path", "perm", "owner"], ["/fs/a", "3", "1"], ["/fs/long/path", "19", "0"]])

    def test_json(self):
        self.assertEqual(json.loads(self.render("json")), [
            {"path": "/fs/a", "perm": 3, "owner": True},
            {"path": "/fs/long/path", "perm": 19, "owner": False},
        ])

    def test_table(self):
        self.assertEqual(self.render("table").splitlines(), [
            "PATH           PERM  OWNER",
            "/fs/a          3     ja",
            "/fs/long/path  19",
        ])


class NaturalKeyTest(unittest.TestCase):
    def test_sort(self):
        self.assertEqual(sorted(["c10", "c2", "c1", "b"], key=natural_key), ["b", "c1", "c2", "c10"])


if __name__ == "__main__":
    unittest.main()
