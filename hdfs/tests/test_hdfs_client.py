"""Tests für die Teile von hdfs.hdfs_client, die kein HDFS brauchen:
Ausgabe von getfattr/ls auswerten, Globs bauen, Shell-Skript für --generate."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from hdfs import hdfs_client
from hdfs.changes import Change
from hdfs.hdfs_client import (
    Hdfs,
    HdfsError,
    command_errors,
    decode_value,
    glob_escape,
    parse_getfattr,
    parse_ls_dirs,
    path_globs,
    write_script,
)


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class DecodeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(decode_value('"@a|@b=1"'), "@a|@b=1")
        self.assertEqual(decode_value("0x40617c"), "@a|")
        self.assertEqual(decode_value("0sQGF8"), "@a|")
        self.assertEqual(decode_value("roh"), "roh")

    def test_parse_getfattr(self):
        output = ("# file: /fs/a\nuser.acl=\"@a|@b=1\"\nuser.other=\"x\"\n\n"
                  "# file: /fs/b\n\n# file: /fs/c\nuser.acl=0x40637c\n")
        self.assertEqual(parse_getfattr(output, "user.acl"), {"/fs/a": "@a|@b=1", "/fs/b": None, "/fs/c": "@c|"})


    def test_parse_ls_dirs(self):
        output = ("drwxr-xr-x+  - hdfs supergroup          0 2026-11-13 10:00 /fs/a\n"
                  "-rw-r--r--   3 hdfs supergroup         12 2026-11-13 10:00 /fs/datei\n"
                  "drwxr-x---   - hdfs supergroup          0 2026-11-13 10:00 /fs/mit leer zeichen\n")
        self.assertEqual(parse_ls_dirs(output), {"/fs/a", "/fs/mit leer zeichen"})


class ReadAclsTest(unittest.TestCase):
    """read_acls mit gefälschtem "hdfs dfs": Dateien auf den Ebenen fallen weg."""

    GETFATTR: ClassVar[dict] = {
        "/fs/*": "# file: /fs/a\nuser.acl=\"@a|\"\n# file: /fs/datei\nuser.acl=\"@d|\"\n",
        "/fs/*/*": "# file: /fs/a/c1\n# file: /fs/a/datei\nuser.acl=\"@d|\"\n",
    }
    LS: ClassVar[dict] = {
        "/fs/*": ("drwxr-xr-x   - h g 0 2026-11-13 10:00 /fs/a\n"
                  "-rw-r--r--   3 h g 1 2026-11-13 10:00 /fs/datei\n"),
        "/fs/*/*": ("drwxr-xr-x   - h g 0 2026-11-13 10:00 /fs/a/c1\n"
                    "-rw-r--r--   3 h g 1 2026-11-13 10:00 /fs/a/datei\n"),
    }

    def hdfs(self, dfs) -> Hdfs:
        client = Hdfs.__new__(Hdfs)
        client.dfs = dfs
        return client

    def test_only_directories(self):
        def dfs(*args):
            if args[0] == "-getfattr":
                return completed(stdout=self.GETFATTR[args[-1]])
            return completed(stdout=self.LS[args[-1]])

        with mock.patch.object(hdfs_client, "info"):
            nodes = self.hdfs(dfs).read_acls("/fs", "user.acl", 2)
        self.assertEqual(nodes, {"/fs/a": "@a|", "/fs/a/c1": None})

    def test_ls_error_aborts(self):
        def dfs(*args):
            if args[0] == "-getfattr":
                return completed(stdout=self.GETFATTR[args[-1]])
            return completed(1, stderr='ls: Permission denied: user=x, access=READ_EXECUTE, inode="/fs/a"\n')

        with mock.patch.object(hdfs_client, "info"), self.assertRaises(HdfsError):
            self.hdfs(dfs).read_acls("/fs", "user.acl", 2)


class CommandErrorsTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(command_errors(completed(), "getfattr", "/fs/*"), [])

    def test_path_errors(self):
        stderr = ("WARN util.NativeCodeLoader: ...\n"
                  "getfattr: `/fs/a': No such file or directory\n"
                  'getfattr: Permission denied: user=x, access=READ, inode="/fs/b":hdfs:hdfs:drwx------\n')
        self.assertEqual([path for path, _ in command_errors(completed(1, stderr=stderr), "getfattr", "/fs/*")],
                         ["/fs/a", "/fs/b"])

    def test_empty_glob_is_no_error(self):
        proc = completed(1, stderr="getfattr: `/fs/*/x': No such file or directory\n")
        self.assertEqual(command_errors(proc, "getfattr", "/fs/*/x"), [])

    def test_error_without_path(self):
        errors = command_errors(completed(1, stderr="WARN NativeCodeLoader\nKerberos kaputt\n"), "getfattr", "/x")
        self.assertEqual(errors, [(None, "Kerberos kaputt")])
        self.assertEqual(command_errors(completed(255), "getfattr", "/x"), [(None, "Exit-Code 255")])


class GlobTest(unittest.TestCase):
    def test_glob_escape(self):
        self.assertEqual(glob_escape("/fs/a{b},c*d?[e]\\f"), "/fs/a\\{b\\}\\,c\\*d\\?\\[e\\]\\\\f")
        self.assertEqual(glob_escape("/fs/a b"), "/fs/a b")

    def test_path_globs_single_batch(self):
        self.assertEqual(list(path_globs(["/fs/a", "/fs/b,c"])), [("/{fs/a,fs/b\\,c}", 2)])

    def test_path_globs_split(self):
        with mock.patch.object(hdfs_client, "GLOB_BYTES", 12):
            globs = list(path_globs(["/fs/a1", "/fs/a2", "/fs/a3"]))
        self.assertEqual(globs, [("/{fs/a1,fs/a2}", 2), ("/{fs/a3}", 1)])

    def test_path_globs_empty(self):
        self.assertEqual(list(path_globs([])), [])


class WriteScriptTest(unittest.TestCase):
    changes: ClassVar[list[Change]] = [
        Change("/fs/account1", "@client3|@test1=1", "@client3|@test1=1,@newapp=19"),
        Change("/fs/acc 2/c*1", None, "@a|#ops=3,$x='y'"),
        Change("/fs/a3", "@x|", None),
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "skript.sh"
        write_script(self.script, self.changes, "user.acl", ["Kopf 1", "Kopf 2"])

    def run_script(self, fake_body: str) -> tuple:
        """Führt das Skript mit einem Fake-hadoop aus, der seine Argumente je Zeile in calls.txt schreibt."""
        fake = self.dir / "hadoop"
        calls = self.dir / "calls.txt"
        fake.write_text("#!/usr/bin/env bash\n"
                        f"printf '%s\\x1f' \"$@\" >> '{calls}'\necho >> '{calls}'\n{fake_body}\n")
        fake.chmod(0o755)
        proc = subprocess.run(["bash", str(self.script)], env={**os.environ, "HADOOP": str(fake)},
                              capture_output=True, text=True, check=False)
        lines = calls.read_text().splitlines() if calls.exists() else []
        return proc, [line.rstrip("\x1f").split("\x1f") for line in lines]

    def test_header_and_mode(self):
        text = self.script.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n# Kopf 1\n# Kopf 2\n"))
        self.assertIn("# alt: @client3|@test1=1", text)
        self.assertTrue(self.script.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(subprocess.run(["bash", "-n", str(self.script)], check=False).returncode, 0)

    def test_arguments_reach_hadoop_unchanged(self):
        proc, calls = self.run_script("exit 0")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK: 3 Pfade geschrieben", proc.stdout)
        self.assertEqual(calls, [
            ["fs", "-setfattr", "-n", "user.acl", "-v", '"@client3|@test1=1,@newapp=19"', "/fs/account1"],
            ["fs", "-setfattr", "-n", "user.acl", "-v", "\"@a|#ops=3,$x='y'\"", "/fs/acc 2/c\\*1"],
            ["fs", "-setfattr", "-x", "user.acl", "/fs/a3"],
        ])

    def test_failures_counted_and_continue(self):
        proc, calls = self.run_script("exit 1")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(calls), 3)
        self.assertIn("3 von 3 Aufrufen fehlgeschlagen", proc.stderr)


if __name__ == "__main__":
    unittest.main()
