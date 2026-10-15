"""ACL-Format: Parsen, Prüfen, effektive Rechte und Vererbung.

Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
  besitzer/eintrag: @clientid, user (ohne Präfix) oder #gruppe
  recht: 3 = lesen+schreiben+löschen, 1 = nur lesen; der Besitzer hat immer 3

Dieses Modul kennt kein HDFS und keine Datenbank, es arbeitet nur auf Strings.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Optional

from hdfs.common import UsageError


def principal_kind(principal: str) -> str:
    """Art eines Eintrags anhand des Präfixes: @ = client, # = group, sonst user."""
    if principal.startswith("@"):
        return "client"
    if principal.startswith("#"):
        return "group"
    return "user"


def split_token(token: str) -> tuple[str, Optional[int]]:
    """'@client1=3' -> ('@client1', 3); ungültige Einträge -> (token, None)."""
    # rpartition: getrennt wird am letzten '=', die Zahl steht immer am Ende
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
        """Gegenstück zu parse(): ergibt den Wert, der ins xattr geschrieben wird."""
        return f"{self.owner}|{','.join(self.tokens)}"

    def client_rights(self) -> dict:
        """Effektive Rechte der @clientids: {clientid: (recht, ist_besitzer)}."""
        rights = {}
        # Steht eine clientid mehrfach in der ACL, zählt das höchste Recht
        for token in self.tokens:
            principal, perm = split_token(token)
            if perm is not None and principal.startswith("@"):
                if perm > rights.get(principal, (0, False))[0]:
                    rights[principal] = (perm, False)
        # Der Besitzer hat immer 3, auch wenn er zusätzlich mit =1 gelistet ist.
        # Deshalb wird er erst nach den Einträgen gesetzt und überschreibt sie.
        if self.owner.startswith("@"):
            rights[self.owner] = (3, True)
        return rights


def acl_problems(raw: str) -> list:
    """Formatfehler einer Roh-ACL als lesbare Liste (leer = in Ordnung)."""
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
    """Pfad -> Pfad, dessen ACL gilt. Ohne eigene ACL gilt die des Elternordners.

    nodes ist {pfad: roh_acl oder None}. Die Pfade werden nach Tiefe sortiert
    abgearbeitet, damit der Elternordner immer schon aufgelöst ist. So erbt ein
    Pfad ohne ACL über beliebig viele Ebenen vom nächsten Vorfahren mit ACL.
    Hat kein Vorfahr eine ACL (oder liegt er außerhalb der Sammlung), ist das
    Ergebnis None: dort hat keine clientid Rechte.
    Eine eigene ACL ersetzt die geerbte komplett, es wird nichts zusammengeführt.
    """
    acl_from = {}
    for path in sorted(nodes, key=lambda p: p.count("/")):
        if nodes[path] is not None:
            acl_from[path] = path
        else:
            acl_from[path] = acl_from.get(posixpath.dirname(path))
    return acl_from


def client_id(value: str, allow_glob: bool = False) -> str:
    """Prüft eine clientid von der Kommandozeile und ergänzt ein fehlendes '@'.

    allow_glob=True lässt *, ? und [...] zu (für search, das SQLite-GLOB nutzt).
    Zeichen, die das ACL-Format zerlegen würden (|, ',', =), sind nie erlaubt.
    """
    value = value.strip()
    if not value.startswith("@"):
        value = "@" + value
    pattern = r"@[^\s|,=#@]+" if allow_glob else r"@[^\s|,=#@*?\[\]]+"
    if not re.fullmatch(pattern, value):
        raise UsageError(f"Ungültige clientid '{value}'.")
    return value
