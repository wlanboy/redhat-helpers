"""Tests für hdfs.fsimage: XML von "hdfs oiv -p XML" lesen, Pfade zusammensetzen."""

import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Sequence

from hdfs.common import UsageError
from hdfs.fsimage import ROOT_ID, find_fsimage, parse_fsimage_xml, unmangle


def inode(inode_id: int, name: str, kind: str = "DIRECTORY", acl: str = "") -> str:
    xattrs = ""
    if acl:
        xattrs = (f"<xattrs><xattr><ns>USER</ns><name>acl</name><val>{acl}</val></xattr>"
                  "<xattr><ns>USER</ns><name>other</name><val>x</val></xattr></xattrs>")
    return f"<inode><id>{inode_id}</id><type>{kind}</type><name>{name}</name>{xattrs}</inode>"


def fsimage(inodes: list, dirs: dict, refs: Sequence[int] = (), ref_children: Optional[dict] = None,
            extra: str = "") -> bytes:
    """Minimales oiv-XML: dirs = {parent: [kinder]}, ref_children = {parent: [index in refs]}."""
    ref_children = ref_children or {}
    directories = "".join(
        f"<directory><parent>{parent}</parent>"
        + "".join(f"<child>{c}</child>" for c in dirs.get(parent, []))
        + "".join(f"<refChild>{r}</refChild>" for r in ref_children.get(parent, []))
        + "</directory>"
        for parent in sorted(set(dirs) | set(ref_children)))
    return (
        "<?xml version=\"1.0\"?>\n<fsimage><version><layoutVersion>-66</layoutVersion></version>\n"
        "<NameSection><namespaceId>1</namespaceId></NameSection>\n"
        f"<INodeSection><lastInodeId>99999</lastInodeId><numInodes>{len(inodes)}</numInodes>\n"
        + "\n".join(inodes) + extra + "\n</INodeSection>\n"
        "<INodeReferenceSection>"
        + "".join(f"<ref><referredId>{r}</referredId><name></name></ref>" for r in refs)
        + "</INodeReferenceSection>\n"
        f"<INodeDirectorySection>{directories}</INodeDirectorySection>\n"
        "<SnapshotDiffSection>kaputt, wird nicht mehr gelesen"
    ).encode()


# /fs/account1 (ACL) mit container1 (eigene ACL), container2 (erbt), einer Datei
# und einem Unterordner auf Ebene 3; /fs/account2 ohne ACL; /other
INODES = [
    inode(ROOT_ID, ""),
    inode(16386, "fs"),
    inode(16387, "account1", acl="@client1|@client2=3"),
    inode(16388, "container1", acl="@client4|cn=doc,ou=x"),
    inode(16389, "container2"),
    inode(16390, "datei.txt", kind="FILE", acl="@nope|"),
    inode(16391, "tief"),
    inode(16392, "account2"),
    inode(16393, "other", acl="@x|"),
]
DIRS = {ROOT_ID: [16386, 16393], 16386: [16387, 16392], 16387: [16388, 16389, 16390], 16388: [16391]}


def parse(data: bytes, base: str = "/fs", attr: str = "user.acl", depth: int = 2) -> dict:
    return parse_fsimage_xml(io.BytesIO(data), base, attr, depth)


class ParseFsimageTest(unittest.TestCase):
    def test_paths_and_acls(self):
        self.assertEqual(parse(fsimage(INODES, DIRS)), {
            "/fs/account1": "@client1|@client2=3",
            "/fs/account2": None,
            "/fs/account1/container1": "@client4|cn=doc,ou=x",
            "/fs/account1/container2": None,
        })

    def test_depth(self):
        self.assertEqual(set(parse(fsimage(INODES, DIRS), depth=1)), {"/fs/account1", "/fs/account2"})
        self.assertIn("/fs/account1/container1/tief", parse(fsimage(INODES, DIRS), depth=3))

    def test_other_attr(self):
        nodes = parse(fsimage(INODES, DIRS), attr="user.other")
        self.assertEqual(nodes["/fs/account1"], "x")
        self.assertIsNone(nodes["/fs/account2"])

    def test_missing_base(self):
        self.assertEqual(parse(fsimage(INODES, DIRS), base="/gibtsnicht"), {})

    def test_unknown_namespace(self):
        with self.assertRaises(UsageError):
            parse(fsimage(INODES, DIRS), attr="foo.acl")

    def test_ref_child(self):
        # account2 nur über eine Referenz eingehängt (umbenannt unter Snapshot)
        dirs = {ROOT_ID: [16386], 16386: [16387]}
        nodes = parse(fsimage(INODES, dirs, refs=[16392], ref_children={16386: [0]}), depth=1)
        self.assertEqual(set(nodes), {"/fs/account1", "/fs/account2"})

    def test_mangled_name_and_value(self):
        inodes = [inode(ROOT_ID, ""), inode(16386, "fs"), inode(16387, "a\\005c;b", acl="@c|\\0001;")]
        nodes = parse(fsimage(inodes, {ROOT_ID: [16386], 16386: [16387]}), depth=1)
        self.assertEqual(nodes, {"/fs/a\\b": "@c|\x01"})

    def test_stray_element(self):
        data = fsimage(INODES, DIRS, extra="<clinit>")
        with self.assertRaises(ET.ParseError) as ctx:
            parse(data)
        self.assertIn("<clinit>", str(ctx.exception))

    def test_broken_xml_shows_excerpt(self):
        data = fsimage(INODES, DIRS).replace(b"<numInodes>", b"<numInodes", 1)
        with self.assertRaises(ET.ParseError) as ctx:
            parse(data)
        self.assertIn("INodeSection", str(ctx.exception))
        self.assertIn("Zeile", str(ctx.exception))


class HelpersTest(unittest.TestCase):
    def test_unmangle(self):
        self.assertEqual(unmangle("a\\005c;b\\0041;"), "a\\bA")
        self.assertEqual(unmangle("ohne"), "ohne")

    def test_find_fsimage(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with self.assertRaises(UsageError):
                find_fsimage(folder)
            for name in ("fsimage_9", "fsimage_10", "fsimage_10.md5", "fsimage_11.ckpt", "fsimage_x"):
                (folder / name).touch()
            self.assertEqual(find_fsimage(folder).name, "fsimage_10")


if __name__ == "__main__":
    unittest.main()
