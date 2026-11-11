"""Tests für hdfs.permissions: Rechte-Tabelle laden, Namen <-> Masken, Wirkung auf ACLs."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hdfs.acl import Acl
from hdfs.changes import plan_add
from hdfs.common import UsageError
from hdfs.main import main
from hdfs.permissions import DEFAULT, Permissions, activate, find, from_dict
from hdfs.store import save

# eigene PermissionFactory: 1 = lesen, 2 = schreiben+löschen, 4 = verwalten
CUSTOM = {
    "permissions": {"LESEN": 1, "AENDERN": 2, "VOLL": 3, "VERWALTEN": 4},
    "owner": "VOLL",
    "admin": "VERWALTEN",
}


class DescribeParseTest(unittest.TestCase):
    def test_default_describe(self):
        self.assertEqual(DEFAULT.describe(1), "READ")
        self.assertEqual(DEFAULT.describe(3), "READ_WRITE")
        self.assertEqual(DEFAULT.describe(19), "ADMIN")
        self.assertEqual(DEFAULT.describe(27), "ADMIN+8")
        self.assertEqual(DEFAULT.describe(35), "READ_WRITE+32")
        self.assertEqual(DEFAULT.describe(32), "32")

    def test_combined_name_wins(self):
        table = from_dict(CUSTOM, "test")
        self.assertEqual(table.describe(3), "VOLL")
        self.assertEqual(table.describe(7), "VOLL+VERWALTEN")

    def test_no_names(self):
        self.assertEqual(Permissions().describe(3), "")

    def test_parse(self):
        for value, mask in (("3", 3), ("READ", 1), ("read_write", 3), ("READ|16", 17),
                            ("READ_WRITE, 16", 19), ("admin", 19), (16, 16)):
            with self.subTest(value=value):
                self.assertEqual(DEFAULT.parse(value), mask)

    def test_parse_errors(self):
        for value in ("LOESCHEN", "0", "READ+", str(1 << 31)):
            with self.subTest(value=value), self.assertRaises(UsageError):
                DEFAULT.parse(value)


class LoadTest(unittest.TestCase):
    def test_from_dict(self):
        table = from_dict(CUSTOM, "test")
        self.assertEqual((table.owner, table.admin), (3, 4))

    def test_defaults_for_owner_admin(self):
        table = from_dict({"permissions": {"R": 1}}, "test")
        self.assertEqual((table.owner, table.admin), (19, 19))

    def test_invalid(self):
        for data in (
            [],
            {"permisions": {}},                      # Tippfehler im Schlüssel
            {"permissions": {"R": 0}},
            {"permissions": {"R": True}},
            {"permissions": {"R": "1"}},
            {"permissions": {"1R": 1}},
            {"permissions": {"read": 1, "READ": 2}},
            {"permissions": {"R": 1}, "owner": "X"},
            {"permissions": {"R": 1}, "admin": 0},
        ):
            with self.subTest(data=data), self.assertRaises(UsageError):
                from_dict(data, "test")

    def test_find_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hdfs-acl.db"
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertIs(find(None, db), DEFAULT)
                (Path(tmp) / "permissions.json").write_text(json.dumps(CUSTOM))
                self.assertEqual(find(None, db).owner, 3)
                self.assertEqual(find(None, db).admin, 4)
                other = Path(tmp) / "other.json"
                other.write_text(json.dumps({"admin": 8}))
                with mock.patch.dict("os.environ", {"HDFS_ACL_PERMISSIONS": str(other)}):
                    self.assertEqual(find(None, db).admin, 8)
                    # --permissions schlägt die Umgebung
                    self.assertEqual(find(str(Path(tmp) / "permissions.json"), db).admin, 4)

    def test_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text("{kaputt")
            with self.assertRaises(UsageError):
                find(str(path), None)
            with self.assertRaises(UsageError):
                find(str(Path(tmp) / "fehlt.json"), None)


class ActiveTableTest(unittest.TestCase):
    """Besitzer- und Verwaltungsrecht kommen aus der aktiven Tabelle."""

    def setUp(self):
        activate(Permissions(names={}, owner=7, admin=64, origin="test"))
        self.addCleanup(activate, DEFAULT)

    def test_owner_perm(self):
        self.assertEqual(Acl.parse("@a|@b=1").client_rights(), {"@b": (1, False), "@a": (7, True)})

    def test_add_uses_admin(self):
        changes, _ = plan_add({"/fs/a": "@old|@x=1"}, "@old", "@new")
        self.assertEqual(changes[0].new, "@old|@x=1,@new=71")


class CommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(activate, DEFAULT)
        self.dir = Path(tmp.name)
        self.db = self.dir / "hdfs-acl.db"
        save(self.db, "/fs", "user.acl", 2, "test",
             {"/fs/a": "@client1|@client2=1,@client3=19", "/fs/a/c": None})

    def run_main(self, *argv: str) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([*argv, "--db", str(self.db)])
        return rc, out.getvalue(), err.getvalue()

    def test_search_by_name(self):
        rc, out, _ = self.run_main("search", "@*", "--has-bits", "ADMIN", "--format", "csv")
        self.assertEqual(rc, 0)
        self.assertIn("/fs/a;@client3;19;ADMIN;0;", out)
        self.assertIn("/fs/a;@client1;19;ADMIN;1;", out)  # Besitzer hat 19
        self.assertNotIn(";@client2;", out)
        _, out, _ = self.run_main("search", "@*", "--perm", "read", "--format", "csv")
        self.assertEqual(len(out.splitlines()), 2)  # Kopf + /fs/a; /fs/a/c erbt nur

    def test_search_unknown_name(self):
        rc, _, err = self.run_main("search", "@*", "--perm", "LOESCHEN")
        self.assertEqual(rc, 1)
        self.assertIn("Unbekanntes Recht 'LOESCHEN'", err)

    def test_show_with_custom_table(self):
        table = self.dir / "custom.json"
        table.write_text(json.dumps({"permissions": {"LESEN": 1, "VOLL": 3}}))
        _, out, _ = self.run_main("show", "/fs/a", "--permissions", str(table))
        self.assertIn("@client1=19 [VOLL+16] (Besitzer)", out)
        self.assertIn("@client2=1 [LESEN]", out)
        self.assertIn("@client3=19 [VOLL+16]", out)

    def test_warns_on_other_owner_perm(self):
        table = self.dir / "permissions.json"  # neben der DB: wird automatisch genommen
        table.write_text(json.dumps({"owner": 7}))
        _, _, err = self.run_main("show", "/fs/a")
        self.assertIn("eingesammelt mit Besitzer-Recht 19", err)


if __name__ == "__main__":
    unittest.main()
