"""ACL-Format: Parsen, Prüfen, effektive Rechte und Vererbung.

Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
  besitzer/eintrag: @clientid, user (ohne Präfix) oder Gruppe: '.' und voller
                    LDAP-DN, z. B. .cn=ops,ou=groups,dc=firma=3
  LDAP-Teile ohne '.' davor wie cn=doc, ou=hell oder dc=firma=3 werden ignoriert
  recht: Bitmaske aus der Spring-Security-ACL PermissionFactory; was welches
         Bit bedeutet und welches Recht der Besitzer immer hat, steht in der
         Rechte-Tabelle (permissions.py, konfigurierbar)

Ausgewertet werden nur die Einträge, die an einem Pfad selbst stehen. Wie
die Anwendung Account- und Container-ACL zusammenführt, rechnet das Tool
bewusst nicht nach (siehe hdfs.md).

Dieses Modul kennt kein HDFS und keine Datenbank, es arbeitet nur auf Strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hdfs.common import UsageError
from hdfs.permissions import MAX_PERM, active

# Recht des Besitzers (auch wenn er gar nicht oder schwächer gelistet ist) und
# Verwaltungsrecht (darf die ACL selbst ändern, was der Besitzer in Spring ohne
# dieses Bit darf) kommen aus der aktiven Rechte-Tabelle: active().owner und
# active().admin.

# Eine clientid: '@', dann keine Zeichen, die das ACL-Format zerlegen
CLIENT_ID = re.compile(r"@[^\s|,=#@]+")

# Attributtypen eines LDAP-DN (RFC 4514 und übliche Erweiterungen)
_DN_TYPES = r"(?:cn|ou|dc|o|c|l|st|street|uid|sn)"

# Teil eines LDAP-DN: Attributtyp, '=', dann ein Wert, der nicht nur aus
# Ziffern besteht (sonst wäre "cn=3" ein User "cn" mit Recht 3).
# Ein DN zerfällt am ',' in mehrere Teile, der letzte trägt evtl. "=<recht>".
LDAP_PART = re.compile(_DN_TYPES + r"=(?!\d*$)", re.IGNORECASE)

# Ein vollständiger DN-Teil ohne Recht dahinter, für die Prüfung von --new-owner
_DN_PART_FULL = re.compile(_DN_TYPES + r"=[^|,=]*[^|,=\d\s][^|,=]*", re.IGNORECASE)


def is_ldap(token: str) -> bool:
    """True für LDAP-Teile ohne '.' davor wie 'cn=doc', 'ou=hell' oder 'dc=firma=3'.

    Sie stammen aus Altbeständen und werden ignoriert. Mit '.' davor ist es
    eine Gruppe (siehe is_group).
    """
    return LDAP_PART.match(token) is not None


def is_group(principal: str) -> bool:
    """Gruppen beginnen mit '.' und haben einen vollen DN: .cn=ops,ou=groups,dc=firma"""
    return principal.startswith(".")


def principal_kind(principal: str) -> str:
    """Art eines Eintrags: @ = client, '.DN' = group, LDAP-Rest = ldap, sonst user."""
    if is_group(principal):
        return "group"
    if is_ldap(principal):
        return "ldap"
    if principal.startswith("@"):
        return "client"
    return "user"


def split_token(token: str) -> tuple[str, int | None]:
    """'@client1=3' -> ('@client1', 3); ungültige Einträge -> (token, None).

    Gruppen: '.cn=ops,ou=groups,dc=firma=3' -> ('.cn=ops,ou=groups,dc=firma', 3)
    """
    # rpartition: getrennt wird am letzten '=', die Zahl steht immer am Ende
    principal, sep, perm = token.rpartition("=")
    if not sep or not principal or not perm.isdigit():
        return token, None
    return principal, int(perm)


def split_entries(rest: str) -> list:
    """Teil hinter '|' in Einträge zerlegen.

    Grundsätzlich trennt ',', ein Gruppen-DN enthält aber selbst Kommas:
    '.cn=ops,ou=groups,dc=firma=3' ist ein Eintrag. Eine Gruppe beginnt mit
    '.' und sammelt die folgenden DN-Teile ein, bis ein Teil das Recht
    trägt ('dc=firma=3') oder der nächste Teil kein DN-Teil mehr ist.
    """
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    tokens = []
    i = 0
    while i < len(parts):
        token = parts[i]
        i += 1
        if is_group(token):
            while split_token(token)[1] is None and i < len(parts) and is_ldap(parts[i]):
                token += "," + parts[i]
                i += 1
        tokens.append(token)
    return tokens


@dataclass
class Acl:
    owner: str
    # Einträge im Originalwortlaut, damit unbekannte Formate beim Zurückschreiben
    # erhalten bleiben; ein Gruppen-DN ist ein Eintrag
    tokens: list

    @classmethod
    def parse(cls, raw: str) -> Acl:
        owner, _, rest = raw.partition("|")
        return cls(owner.strip(), split_entries(rest))

    def render(self) -> str:
        """Gegenstück zu parse(): ergibt den Wert, der ins xattr geschrieben wird."""
        return f"{self.owner}|{','.join(self.tokens)}"

    def client_entries(self) -> dict:
        """Gültige Einträge der @clientids dieser ACL: {clientid: maske}.

        Steht eine clientid mehrfach in der ACL, gelten alle ihre Bits
        zusammen. Was acl_problems() als Fehler meldet, gibt keine Rechte:
        Einträge mit Recht außerhalb 1..MAX_PERM.
        """
        entries = {}
        for token in self.tokens:
            principal, perm = split_token(token)
            if perm is not None and 0 < perm <= MAX_PERM and principal.startswith("@"):
                entries[principal] = entries.get(principal, 0) | perm
        return entries

    def client_rights(self) -> dict:
        """Rechte der @clientids aus dieser ACL: {clientid: (recht, ist_besitzer)}.

        Der Besitzer hat immer mindestens das Besitzer-Recht, auch wenn er
        zusätzlich mit =1 gelistet ist; weitere Bits seiner Einträge kommen
        dazu. Ein Besitzer, der keine gültige clientid ist (User, Gruppe, leer
        oder "@a=3" bei fehlendem '|'), gibt keiner clientid Rechte.
        """
        rights = {cid: (perm, False) for cid, perm in self.client_entries().items()}
        if CLIENT_ID.fullmatch(self.owner):
            rights[self.owner] = (rights.get(self.owner, (0, False))[0] | active().owner, True)
        return rights


def acl_problems(raw: str) -> list:
    """Formatfehler einer Roh-ACL als lesbare Liste (leer = in Ordnung).

    Ein leerer Besitzer ("|@a=1") ist kein Fehler, die Anwendung übernimmt
    dann den des Elternordners.
    """
    problems = []
    _, sep, rest = raw.partition("|")
    if not sep:
        problems.append("kein '|' zwischen Besitzer und Einträgen")
    for token in split_entries(rest):
        if is_ldap(token):
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


def group_id(value: str) -> str:
    """Prüft eine Gruppe von der Kommandozeile: '.' und voller DN ohne Recht.

    '.cn=admins,ou=groups,dc=firma' ist gültig, ',' trennt die DN-Teile.
    """
    value = value.strip()
    parts = value[1:].split(",") if value.startswith(".") else []
    if not parts or not all(_DN_PART_FULL.fullmatch(p.strip()) for p in parts):
        raise UsageError(f"Ungültige Gruppe '{value}' (erwartet '.' und vollen DN, "
                         "z. B. .cn=admins,ou=groups,dc=firma).")
    return "." + ",".join(p.strip() for p in parts)
