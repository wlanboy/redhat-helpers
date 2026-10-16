"""ACL-Format: Parsen, Prüfen, effektive Rechte und Vererbung.

Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
  besitzer/eintrag: @clientid, user (ohne Präfix) oder #gruppe
  LDAP-Teile wie cn=doc, ou=hell, sn=meier oder dc=firma=3 werden ignoriert
  recht: Bitmaske aus der Spring-Security-ACL PermissionFactory, z. B.
         1 = nur lesen, 3 = lesen+schreiben+löschen; der Besitzer hat immer 3

Dieses Modul kennt kein HDFS und keine Datenbank, es arbeitet nur auf Strings.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Optional

from hdfs.common import UsageError

# Recht des Besitzers, auch wenn er gar nicht oder schwächer gelistet ist
OWNER_PERM = 3
# Spring speichert die Maske als Java-int, höchstes nutzbares Bit ist 1 << 30
MAX_PERM = (1 << 31) - 1

# Teil eines LDAP-DN: Attributtyp aus RFC 4514, '=', dann ein Wert, der nicht
# nur aus Ziffern besteht (sonst wäre "cn=3" ein User "cn" mit Recht 3).
# Ein DN zerfällt am ',' in mehrere Einträge, der letzte trägt evtl. "=<recht>".
LDAP_PART = re.compile(r"\.?(?:cn|ou|dc|o|c|l|st|street|uid|sn)=(?!\d*$)", re.IGNORECASE)


def is_ldap(token: str) -> bool:
    """True für LDAP-Teile wie 'cn=doc', 'ou=hell' oder 'dc=firma=3'."""
    return LDAP_PART.match(token) is not None


def principal_kind(principal: str) -> str:
    """Art eines Eintrags: @ = client, # = group, LDAP-DN = ldap, sonst user."""
    if is_ldap(principal):
        return "ldap"
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
        # Steht eine clientid mehrfach in der ACL, gelten alle ihre Bits zusammen
        for token in self.tokens:
            principal, perm = split_token(token)
            if perm is not None and principal.startswith("@"):
                rights[principal] = (rights.get(principal, (0, False))[0] | perm, False)
        # Der Besitzer hat immer mindestens OWNER_PERM, auch wenn er zusätzlich
        # mit =1 gelistet ist; weitere Bits aus seinen Einträgen kommen dazu
        if self.owner.startswith("@"):
            rights[self.owner] = (rights.get(self.owner, (0, False))[0] | OWNER_PERM, True)
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
        if not token or is_ldap(token):
            continue
        perm = split_token(token)[1]
        if perm is None:
            problems.append(f"ungültiger Eintrag '{token}'")
        elif not 0 < perm <= MAX_PERM:
            problems.append(f"Recht außerhalb 1..{MAX_PERM} in '{token}'")
    return problems


def covers(have: int, need: int) -> bool:
    """True, wenn die Maske have alle Bits von need enthält."""
    return have & need == need


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
