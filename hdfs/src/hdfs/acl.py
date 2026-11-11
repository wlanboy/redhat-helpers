"""ACL-Format: Parsen, Prüfen, effektive Rechte und Vererbung.

Format: <besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
  besitzer/eintrag: @clientid, user (ohne Präfix) oder Gruppe: '.' und voller
                    LDAP-DN, z. B. .cn=ops,ou=groups,dc=firma=3
  LDAP-Teile ohne '.' davor wie cn=doc, ou=hell oder dc=firma=3 werden ignoriert
  recht: Bitmaske aus der Spring-Security-ACL PermissionFactory; was welches
         Bit bedeutet und welches Recht der Besitzer immer hat, steht in der
         Rechte-Tabelle (permissions.py, konfigurierbar)

Vererbung: Eine ACL tiefer im Baum (Container) erweitert oder schränkt die
geerbte ein. Steht eine clientid in der Container-ACL, gilt dort deren Wert,
sonst der geerbte. Ein Besitzer im Container ersetzt den geerbten, ein leerer
Besitzer ("|@a=1") übernimmt ihn. Siehe inherit().

Dieses Modul kennt kein HDFS und keine Datenbank, es arbeitet nur auf Strings.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

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
        """Rechte der @clientids nur aus dieser ACL, ohne Vererbung:
        {clientid: (recht, ist_besitzer)}."""
        return {cid: (perm, owner) for cid, (perm, owner, _) in
                rights(inherit(None, "", self)).items()}


@dataclass(frozen=True)
class State:
    """Was an einem Pfad gilt, nachdem alle ACLs von oben zusammengeführt sind.

    owner: geltender Besitzer ('' = keiner), owner_from: Pfad, der ihn setzt.
    entries: {clientid: (maske, pfad)}; pfad = ACL, aus der der Wert stammt.
    Pfade ohne eigene ACL teilen sich das State-Objekt des Elternordners.
    """
    owner: str = ""
    owner_from: str | None = None
    entries: dict = field(default_factory=dict)


def inherit(parent: State | None, path: str, acl: Acl | None) -> State | None:
    """Führt die ACL eines Pfads mit dem geerbten Stand zusammen.

    - Ohne eigene ACL gilt der geerbte Stand unverändert (dasselbe Objekt).
    - Einträge: Steht eine clientid in der eigenen ACL, gilt deren Wert
      (erweitern oder einschränken), sonst der geerbte.
    - Besitzer: Es gibt nur einen. Ein eigener Besitzer ersetzt den
      geerbten, ein leerer ("|...") übernimmt ihn.
    """
    if acl is None:
        return parent
    base = parent or State()
    entries = dict(base.entries)
    for clientid, perm in acl.client_entries().items():
        entries[clientid] = (perm, path)
    if acl.owner:
        return State(acl.owner, path, entries)
    return State(base.owner, base.owner_from, entries)


def rights(state: State | None) -> dict:
    """Effektive Rechte der @clientids: {clientid: (recht, ist_besitzer, acl_von)}.

    Der Besitzer hat immer mindestens das Besitzer-Recht, auch wenn er
    zusätzlich mit =1 gelistet ist; weitere Bits seiner Einträge kommen dazu.
    Ein Besitzer, der keine gültige clientid ist (User, Gruppe oder "@a=3" bei
    fehlendem '|'), gibt keiner clientid Rechte.
    acl_von ist der tiefste Pfad, dessen ACL zum Recht beiträgt (Eintrag oder
    Besitzer).
    """
    if state is None:
        return {}
    result = {cid: (perm, False, origin) for cid, (perm, origin) in state.entries.items()}
    owner = state.owner
    if CLIENT_ID.fullmatch(owner) and state.owner_from is not None:
        perm, _, origin = result.get(owner, (0, False, state.owner_from))
        # beide Pfade liegen auf derselben Kette: der längere ist der tiefere
        deeper = max(origin, state.owner_from, key=len)
        result[owner] = (perm | active().owner, True, deeper)
    return result


def resolve(nodes: dict) -> dict:
    """{pfad: roh_acl oder None} -> {pfad: State oder None}, Vererbung aufgelöst.

    Die Pfade werden nach Tiefe sortiert abgearbeitet, damit der Elternordner
    immer schon aufgelöst ist. So erbt ein Pfad über beliebig viele Ebenen.
    Hat kein Vorfahr eine ACL (oder liegt er außerhalb der Sammlung), ist der
    Stand None: dort hat keine clientid Rechte.
    """
    states = {}
    for path in sorted(nodes, key=lambda p: p.count("/")):
        raw = nodes[path]
        parent = states.get(posixpath.dirname(path))
        states[path] = inherit(parent, path, Acl.parse(raw) if raw is not None else None)
    return states


def acl_problems(raw: str) -> list:
    """Formatfehler einer Roh-ACL als lesbare Liste (leer = in Ordnung).

    Ein leerer Besitzer ist hier kein Fehler, er übernimmt den geerbten. Ob es
    dann überhaupt einen gibt, hängt vom Baum ab und prüft store.save().
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


def resolve_acl_from(nodes: dict) -> dict:
    """Pfad -> nächster Pfad (selbst oder Vorfahr) mit eigener ACL, sonst None.

    nodes ist {pfad: roh_acl oder None}. Pfade mit demselben Ergebnis haben
    denselben geltenden Stand (resolve()), was zum Zwischenspeichern reicht.
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
