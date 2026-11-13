"""Tests für die Kommandos über den echten Parser, mit einem Fake statt HDFS.

Der Fake ersetzt hdfs.commands.Hdfs; gelesen wird mit --live (read_acls),
nachgelesen mit read_paths, geschrieben mit write.
"""

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hdfs.commands import base_dir, owner_id
from hdfs.common import UsageError
from hdfs.main import build_parser, main
from hdfs.store import read_backup, save

TESTDATA = Path(__file__).resolve().parent.parent / "testdata"

NODES = {
    "/fs/account1": "@client3|@test1=1,@test2=2",
    "/fs/account1/container1": None,
    "/fs/account1/container2": "@o|@client3=1,.cn=ops,ou=groups,dc=firma=3",
    "/fs/account2": "@o|@x=1",
}


class FakeHdfs:
    def __init__(self, nodes: dict):
        self.nodes = dict(nodes)
        self.writes = 0

    def read_acls(self, base: str, attr: str, depth: int) -> dict:
        return dict(self.nodes)

    def read_paths(self, paths: list, attr: str, strict: bool = True) -> dict:
        return {path: self.nodes[path] for path in paths if path in self.nodes}

    def write(self, changes: list, attr: str) -> list:
        self.writes += 1
        for change in changes:
            self.nodes[change.path] = change.new
        return []


class CommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "hdfs-acl.db"
        self.hdfs = FakeHdfs(NODES)
        patcher = mock.patch("hdfs.commands.Hdfs", lambda parallel, version: self.hdfs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv: str) -> tuple:
        """main() mit --db im Testordner; liefert (exit-code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([*argv, "--db", str(self.db)])
        return rc, out.getvalue(), err.getvalue()

    def backups(self) -> list:
        return sorted(self.dir.glob("hdfs-acl-backup-*.csv"))

    # --- ändernde Kommandos ---------------------------------------------------

    def test_add_dry_run(self):
        rc, out, _ = self.run_main("add", "@client3", "@newapp", "--live", "--accounts")
        self.assertEqual(rc, 0)
        self.assertIn("neu: @client3|@test1=1,@test2=2,@newapp=19", out)
        self.assertIn("2 Pfade betroffen.", out)
        self.assertIn("Dry-Run", out)
        self.assertEqual((self.hdfs.writes, self.backups()), (0, []))

    def test_add_apply(self):
        rc, out, _ = self.run_main("add", "@client3", "@newapp", "--live", "--accounts", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK: 2 Pfade geändert und geprüft.", out)
        self.assertEqual(self.hdfs.nodes["/fs/account1/container2"], "@o|@client3=1,.cn=ops,ou=groups,dc=firma=3,@newapp=1")
        self.assertEqual(len(self.backups()), 1)
        # danach nichts mehr zu tun
        rc, out, _ = self.run_main("add", "@client3", "@newapp", "--live", "--accounts")
        self.assertIn("Keine Änderungen nötig.", out)

    def test_add_containers_only(self):
        # Default: nur Container, der Account mit @client3 als Besitzer bleibt
        rc, out, _ = self.run_main("add", "@client3", "@newapp", "--live", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("OK: 1 Pfade geändert und geprüft.", out)
        self.assertEqual(self.hdfs.nodes["/fs/account1"], NODES["/fs/account1"])
        self.assertEqual(self.hdfs.nodes["/fs/account1/container2"],
                         "@o|@client3=1,.cn=ops,ou=groups,dc=firma=3,@newapp=1")

    def test_replace_containers_only(self):
        rc, out, _ = self.run_main("replace", "@client3", "@newapp", "--live", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.hdfs.nodes["/fs/account1"], NODES["/fs/account1"])
        self.assertEqual(self.hdfs.nodes["/fs/account1/container2"],
                         "@o|@newapp=1,.cn=ops,ou=groups,dc=firma=3")

    def test_only_in_account(self):
        # @test1 steht nur in einer Account-ACL
        rc, out, _ = self.run_main("replace", "@test1", "@newapp", "--live")
        self.assertEqual(rc, 0)
        self.assertIn("@test1 steht in keiner ACL eines Containers (Accounts mit --accounts).", out)
        rc, out, _ = self.run_main("add", "@test1", "@newapp", "--live")
        self.assertIn("@test1 steht in keiner ACL eines Containers", out)

    def test_add_generate(self):
        script = self.dir / "add-newapp.sh"
        rc, out, _ = self.run_main("add", "@client3", "@newapp", "--live", "--accounts", "--generate", str(script))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.hdfs.writes, 0)
        self.assertEqual(self.hdfs.nodes, NODES)
        self.assertIn(f"Skript: {script}", out)

        text = script.read_text()
        self.assertIn("# hdfs-acl add-newapp: 2 Pfade", text)
        self.assertIn("-setfattr -n user.acl -v '\"@client3|@test1=1,@test2=2,@newapp=19\"' /fs/account1 ", text)
        self.assertIn("-setfattr -n user.acl -v '\"@o|@client3=1,.cn=ops,ou=groups,dc=firma=3,@newapp=1\"' /fs/account1/container2 ", text)
        self.assertEqual(text.count('"$HADOOP" fs'), 2)

        # das Backup passt zum Skript, restore dreht es nach dem Ausführen zurück
        (backup,) = self.backups()
        self.assertIn(f"restore {backup} --apply", text)
        self.assertEqual({c.path for c in read_backup(str(backup))},
                         {"/fs/account1", "/fs/account1/container2"})

    def test_generate_nothing_to_do(self):
        script = self.dir / "x.sh"
        # @client3 hat als Besitzer schon alle Bits von @test1
        rc, out, _ = self.run_main("add", "@test1", "@client3", "--live", "--accounts", "--generate", str(script))
        self.assertEqual(rc, 0)
        self.assertIn("Keine Änderungen nötig.", out)
        self.assertFalse(script.exists())

    def test_apply_and_generate_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["add", "@a", "@b", "--apply", "--generate", "x.sh"])

    def test_replace_and_restore(self):
        rc, _, _ = self.run_main("replace", "@client3", "@newapp", "--live", "--accounts", "--apply")
        self.assertEqual(rc, 0)
        self.assertEqual(self.hdfs.nodes["/fs/account1"], "@newapp|@test1=1,@test2=2")
        (backup,) = self.backups()
        rc, out, _ = self.run_main("restore", str(backup), "--live", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.hdfs.nodes, NODES)

    def test_restore_conflict(self):
        self.run_main("replace", "@client3", "@newapp", "--live", "--accounts", "--apply")
        (backup,) = self.backups()
        self.hdfs.nodes["/fs/account1"] = "@jemand|"
        rc, _, err = self.run_main("restore", str(backup), "--live", "--apply")
        self.assertEqual(rc, 2)
        self.assertIn("/fs/account1: wurde seit dem Backup geändert", err)
        self.assertEqual(self.hdfs.nodes["/fs/account1"], "@jemand|")

    def test_remove_owner_skipped(self):
        rc, _out, err = self.run_main("remove", "@client3", "--live", "--apply")
        self.assertEqual(rc, 2)
        self.assertIn("ist Besitzer von 1 Pfaden", err)
        self.assertEqual(self.hdfs.nodes["/fs/account1/container2"], "@o|.cn=ops,ou=groups,dc=firma=3")
        self.assertEqual(self.hdfs.nodes["/fs/account1"], NODES["/fs/account1"])

    def test_remove_with_new_owner(self):
        admins = ".cn=admins,ou=groups,dc=firma"
        rc, _, _ = self.run_main("remove", "@client3", "--new-owner", admins, "--live", "--apply")
        self.assertEqual(rc, 0)
        self.assertEqual(self.hdfs.nodes["/fs/account1"], f"{admins}|@test1=1,@test2=2")
        rc, _, err = self.run_main("remove", "@client3", "--new-owner", "#admins", "--live")
        self.assertEqual(rc, 1)
        self.assertIn("Gruppen beginnen mit '.'", err)

    def test_usage_errors(self):
        rc, _, err = self.run_main("add", "@a", "a", "--live")
        self.assertEqual(rc, 1)
        self.assertIn("QUELLE und NEU sind gleich", err)
        rc, _, err = self.run_main("remove", "@a", "--new-owner", "a", "--live")
        self.assertEqual(rc, 1)

    # --- lesende Kommandos ----------------------------------------------------

    def test_read_commands(self):
        save(self.db, "/fs", "user.acl", 2, "test", NODES)
        rc, out, _ = self.run_main("search", "@client3", "--format", "csv")
        self.assertEqual(rc, 0)
        rows = list(csv.reader(io.StringIO(out), delimiter=";"))
        # nur ACLs, die @client3 nennen; container1 erbt nur und fehlt deshalb
        self.assertEqual([r[0] for r in rows[1:]], ["/fs/account1", "/fs/account1/container2"])
        self.assertEqual(rows[1], ["/fs/account1", "@client3", "19", "ADMIN", "1", NODES["/fs/account1"]])

        _, out, _ = self.run_main("search", "@client*", "--format", "csv")
        self.assertEqual(len(out.splitlines()), 3)
        _, out, _ = self.run_main("search", "@client3", "--perm", "1", "--format", "csv")
        self.assertEqual(len(out.splitlines()), 2)

        _, out, _ = self.run_main("clients", "--format", "csv")
        self.assertIn("@client3;2;1:1 19:1;1", out)

        rc, out, _ = self.run_main("count", "--format", "csv")
        self.assertEqual(rc, 0)
        counts = {r[0]: int(r[1]) for r in list(csv.reader(io.StringIO(out), delimiter=";"))[1:]}
        self.assertEqual(counts["paths"], 4)
        self.assertEqual(counts["paths_with_acl"], 3)
        self.assertEqual(counts["entries_group"], 1)
        self.assertEqual(counts["clientids"], 5)

        # Pfad ohne ACL: nichts Geerbtes
        _, out, _ = self.run_main("show", "/fs/account1/container1/")
        self.assertEqual(out, "Pfad:    /fs/account1/container1\nACL:     (keine)\n")
        _, out, _ = self.run_main("show", "/fs/account1")
        self.assertIn("Rechte:  @client3=19 [ADMIN] (Besitzer), @test1=1 [READ], @test2=2 [2]", out)
        rc, _, err = self.run_main("show", "/fs/gibtsnicht")
        self.assertEqual(rc, 1)
        self.assertIn("nicht eingesammelt", err)

    def test_export_matches_testdata(self):
        # Golden-Test: Testdaten von test.sh -> DB -> export == expected.csv
        with (TESTDATA / "acls.csv").open(newline="") as file:
            nodes = {row["path"]: row["acl"] or None for row in csv.DictReader(file, delimiter=";")}
        save(self.db, "/fs", "user.acl", 2, "testdata", nodes)
        target = self.dir / "export.csv"
        rc, _, _ = self.run_main("export", "-o", str(target))
        self.assertEqual(rc, 0)
        self.assertEqual(target.read_text().splitlines(), (TESTDATA / "expected.csv").read_text().splitlines())


class HelpersTest(unittest.TestCase):
    def test_base_dir(self):
        self.assertEqual(base_dir("/fs/"), "/fs")
        with self.assertRaises(UsageError):
            base_dir("fs")

    def test_owner_id(self):
        self.assertEqual(owner_id("newapp"), "@newapp")
        self.assertEqual(owner_id(".cn=admins,ou=groups,dc=firma"), ".cn=admins,ou=groups,dc=firma")
        for bad in ("#admins", ".cn=ad|mins", ".admins"):
            with self.subTest(value=bad), self.assertRaises(UsageError):
                owner_id(bad)


if __name__ == "__main__":
    unittest.main()
