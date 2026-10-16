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
der Großteil, kosten nur Lesezeit.
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
    for event, elem in ET.iterparse(stream, events=("start", "end")):
        if event == "start":
            level += 1
            if level == 1:
                root = elem
            elif level == 2:
                section = elem
            continue
        level -= 1
        if level == 1:
            root.clear()  # Abschnitt fertig
        if level != 2:
            continue

        if section.tag == "INodeSection" and elem.tag == "inode":
            if elem.findtext("type") == "DIRECTORY":
                inode = int(elem.findtext("id"))
                # intern: Namen wie container1 wiederholen sich in jedem Account
                dirs[inode] = sys.intern(unmangle(elem.findtext("name") or ""))
                for xattr in elem.iterfind("xattrs/xattr"):
                    if xattr.findtext("ns") == ns and xattr.findtext("name") == name:
                        acls[inode] = unmangle(xattr.findtext("val") or "")
        elif section.tag == "INodeReferenceSection" and elem.tag == "ref":
            refs.append(int(elem.findtext("referredId")))
        elif section.tag == "INodeDirectorySection" and elem.tag == "directory":
            children = [int(c.text) for c in elem.iterfind("child")]
            children += [refs[int(c.text)] for c in elem.iterfind("refChild")]
            children = [c for c in children if c in dirs]
            if children:
                kids[int(elem.findtext("parent"))] = children
        section.clear()

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
