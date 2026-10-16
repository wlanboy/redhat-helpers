"""ACLs aus einem fsimage lesen, ohne den NameNode zu fragen.

Das fsimage ist der Checkpoint des NameNodes: der komplette Namensraum mit
allen xattrs. "hdfs oiv -p XML" wandelt es lokal in XML um, das hier als
Stream gelesen wird. Benutzt werden nur diese Abschnitte (in dieser
Reihenfolge in der Datei):

    <INodeSection>           <inode> mit id, type, name und xattrs
    <INodeReferenceSection>  <ref> mit referredId (umbenannt unter Snapshots)
    <INodeDirectorySection>  <directory> mit parent und child/refChild

Pfade stehen im fsimage nicht, sie werden aus den Eltern-Kind-Beziehungen
zusammengesetzt. Gemerkt werden dafür nur Ordner; Dateien, und das ist meist
der Großteil, kosten nur Lesezeit. Nach <INodeDirectorySection> wird nicht
weitergelesen, die übrigen Abschnitte (Snapshot-Diffs, Tokens, Cache) werden
nicht gebraucht.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import IO

from hdfs.common import UsageError

# INodeId.ROOT_INODE_ID: der Wurzelordner hat in jedem HDFS diese id
ROOT_ID = 16385

# Was oiv in den ausgewerteten Abschnitten auf Ebene 3 schreibt. Alles andere
# ist fremder Text im Stream, z. B. "<clinit>" aus einer Logzeile. Der Parser selbst merkt das erst am Ende des Abschnitts (das neue
# Element wird nie geschlossen), mit einer nutzlosen Zeilennummer.
ENTRIES = {
    "INodeSection": {"lastInodeId", "numInodes", "inode"},
    "INodeReferenceSection": {"ref"},
    "INodeDirectorySection": {"directory"},
}

# Präfix im Attributnamen -> <ns> im XML
NAMESPACES = {"user": "USER", "trusted": "TRUSTED", "system": "SYSTEM", "security": "SECURITY", "raw": "RAW"}


def find_fsimage(directory: Path) -> Path:
    """Neuestes fsimage_<txid> im Ordner; die .md5-Dateien daneben zählen nicht."""
    images = [p for p in directory.glob("fsimage_*") if re.fullmatch(r"fsimage_\d+", p.name)]
    if not images:
        raise UsageError(f"Kein fsimage_<txid> in {directory} (siehe hdfs.md: fsimage besorgen).")
    return max(images, key=lambda p: int(p.name[len("fsimage_"):]))


def unmangle(text: str) -> str:
    """oiv schreibt Backslash und in XML ungültige Zeichen als \\XXXX; (hex)."""
    return re.sub(r"\\([0-9a-fA-F]{4});", lambda m: chr(int(m[1], 16)), text)


class _Tail:
    """Reicht den Stream an den Parser durch und behält die letzten zwei
    Blöcke, damit ein Parse-Fehler die Stelle im XML zeigen kann."""

    def __init__(self, stream: IO[bytes]):
        self.stream = stream
        self.lines = 0  # Zeilenumbrüche vor den behaltenen Blöcken
        self.blocks = [b"", b""]

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.lines += self.blocks[0].count(b"\n")
        self.blocks = [self.blocks[1], data]
        return data

    def excerpt(self, line: int, column: int) -> str:
        """Ende der drei Zeilen davor und die Zeile line um column herum."""
        lines = b"".join(self.blocks).split(b"\n")
        i = line - 1 - self.lines
        if not 0 <= i < len(lines):
            return ""
        shown = [(line - n, lines[i - n], len(lines[i - n]) - 300) for n in range(min(i, 3), 0, -1)]
        shown.append((line, lines[i][:column + 100], column - 300))
        return "\n".join(f"  Zeile {n}: {'...' if start > 0 else ''}{text[max(0, start):].decode(errors='replace')}"
                         for n, text, start in shown)

    def find(self, text: str) -> str:
        """Wie excerpt, für das erste Vorkommen von text in den Blöcken."""
        data = b"".join(self.blocks)
        pos = data.find(text.encode())
        if pos < 0:
            return ""
        line = self.lines + data.count(b"\n", 0, pos) + 1
        return self.excerpt(line, pos - data.rfind(b"\n", 0, pos) - 1)


def parse_fsimage_xml(stream: IO[bytes], base: str, attr: str, depth: int) -> dict:
    """XML von "hdfs oiv -p XML" -> {pfad: wert oder None}.

    Liefert dieselben Pfade wie das Einlesen per getfattr: die Ordner auf den
    Ebenen 1..depth unter base, auch ohne Attribut (None, wichtig für die
    Vererbung).
    """
    prefix, _, name = attr.partition(".")
    ns = NAMESPACES.get(prefix.lower())
    if ns is None or not name:
        raise UsageError(f"Unbekannter xattr-Namespace in '{attr}'.")

    dirs = {}  # id -> name, nur Ordner
    acls = {}  # id -> wert, nur Ordner mit dem Attribut
    refs = []  # Position in INodeReferenceSection -> referredId
    kids = {}  # id -> [ids der Unterordner]

    # Abschnitte (Ebene 2) werden nach jedem fertigen Eintrag (Ebene 3)
    # geleert, sonst hielte ElementTree den ganzen Baum im Speicher
    level = 0
    root = section = ET.Element("")
    tail = _Tail(stream)
    stray = None  # (element, umgebendes element)
    try:
        for event, elem in ET.iterparse(tail, events=("start", "end")):
            if event == "start":
                level += 1
                if level == 1:
                    root = elem
                elif level == 2:
                    section = elem
                    if elem.tag != "version" and not elem.tag.endswith("Section"):
                        stray = (elem.tag, root.tag)
                        break
                elif level == 3 and elem.tag not in ENTRIES.get(section.tag, {elem.tag}):
                    stray = (elem.tag, section.tag)
                    break
                continue
            level -= 1
            if level == 1:
                root.clear()  # Abschnitt fertig
                if elem.tag == "INodeDirectorySection":
                    break
            if level != 2:
                continue
            _collect(section.tag, elem, ns, name, dirs, acls, refs, kids)
            section.clear()
    except ET.ParseError as error:
        line, column = error.position
        where = f"<{section.tag}>" if level >= 2 else f"<{root.tag}>" if level else "Dateianfang"
        new = ET.ParseError(f"{error} (in {where})\n{tail.excerpt(line, column)}".rstrip())
        new.code, new.position = error.code, error.position
        raise new from None
    if stray:
        tag, parent = stray
        raise ET.ParseError(f"unerwartetes Element <{tag}> in <{parent}>, fremder Text in der Ausgabe von oiv?\n"
                            + tail.find(f"<{tag}"))

    # Von der Wurzel zu base, dann Ebene für Ebene nach unten
    node = ROOT_ID
    for part in base.strip("/").split("/"):
        node = next((k for k in kids.get(node, ()) if dirs[k] == part), None)
        if node is None:
            return {}
    nodes = {}
    current = [(base, node)]
    for _ in range(depth):
        current = [(f"{path}/{dirs[k]}", k) for path, parent in current for k in kids.get(parent, ())]
        for path, inode in current:
            nodes[path] = acls.get(inode)
    return nodes


def _collect(section: str, elem: ET.Element, ns: str, name: str,
             dirs: dict, acls: dict, refs: list, kids: dict) -> None:
    """Einen fertigen Eintrag (Ebene 3) auswerten."""
    if section == "INodeSection" and elem.tag == "inode":
        if elem.findtext("type") == "DIRECTORY":
            inode = int(elem.findtext("id"))
            # intern: Namen wie container1 wiederholen sich in jedem Account
            dirs[inode] = sys.intern(unmangle(elem.findtext("name") or ""))
            for xattr in elem.iterfind("xattrs/xattr"):
                if xattr.findtext("ns") == ns and xattr.findtext("name") == name:
                    acls[inode] = unmangle(xattr.findtext("val") or "")
    elif section == "INodeReferenceSection" and elem.tag == "ref":
        refs.append(int(elem.findtext("referredId")))
    elif section == "INodeDirectorySection" and elem.tag == "directory":
        children = [int(c.text) for c in elem.iterfind("child")]
        children += [refs[int(c.text)] for c in elem.iterfind("refChild")]
        children = [c for c in children if c in dirs]
        if children:
            kids[int(elem.findtext("parent"))] = children
