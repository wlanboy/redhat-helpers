"""Änderungen planen: aus dem aktuellen Stand und einem Kommando eine Liste Change.

Alle plan_*-Funktionen bekommen nodes = {pfad: roh_acl oder None}, frisch aus
HDFS gelesen, und schreiben selbst nichts. Geschrieben wird erst in
commands.execute(), und nur mit --apply.

Gemeinsame Regeln:
- Geändert werden nur Pfade mit eigener ACL. Pfade, die erben, ändern sich
  über ihren Elternordner automatisch mit.
- Einträge für User, Gruppen und andere clientids bleiben Wort für Wort
  und an ihrer Stelle erhalten.
- add und replace übertragen nur explizite Rechte: was ALT in einer ACL
  selbst stehen hat (Besitzer oder Eintrag). Geerbte Rechte lösen nichts
  aus, jede ACL wird für sich umgeschrieben. Container, die ALT nicht
  nennen, erben NEU vom Account wie zuvor ALT.
"""

from __future__ import annotations

from dataclasses import dataclass

from hdfs.acl import Acl, covers, split_token
from hdfs.permissions import active


@dataclass
class Change:
    path: str
    old: str | None  # None = Pfad hatte kein Attribut
    new: str | None  # None = Attribut entfernen


def merge_entries(tokens: list, principal: str) -> list:
    """Fasst mehrere Einträge von principal zu einem an der ersten Stelle zusammen,
    die Bits werden verodert.

    Auch wenn principal Besitzer ist, bleibt der Eintrag stehen: Einträge
    erben in Container, deren ACL einen anderen Besitzer hat, das
    Besitzer-Recht nicht.

    Beispiel: ['@a=1', '.cn=ops,dc=x=3', '@a=4'] -> ['@a=5', '.cn=ops,dc=x=3']
    """
    perms = [perm for p, perm in map(split_token, tokens) if p == principal and perm is not None]
    if not perms:
        return tokens
    mask = 0
    for perm in perms:
        mask |= perm
    return set_entry(tokens, principal, mask)


def set_entry(tokens: list, principal: str, mask: int) -> list:
    """Genau ein Eintrag principal=mask: an der Stelle des ersten vorhandenen,
    weitere entfallen; ohne vorhandenen wird angehängt."""
    result = []
    placed = False
    for token in tokens:
        if split_token(token)[0] != principal:
            result.append(token)
        elif not placed:
            result.append(f"{principal}={mask}")
            placed = True
    if not placed:
        result.append(f"{principal}={mask}")
    return result


def plan_add(nodes: dict, source: str, new: str) -> tuple:
    """NEU bekommt in jeder ACL, die QUELLE explizit nennt, deren Bits dazu (Migration).

    Phase 1 einer Migration: beide clientids greifen. Eine ACL hat nur einen
    Besitzer, ist QUELLE Besitzer, bleibt sie es. NEU bekommt dort einen
    Eintrag mit dem Recht des Besitzers plus dem Verwaltungsrecht
    (active().admin), damit NEU die ACL verwalten kann wie der Besitzer.
    Erst replace macht NEU zum Besitzer.

    Nur explizite Rechte: Gezählt werden der Besitzer und die Einträge dieser
    ACL, nichts Geerbtes. Schränkt ein Container QUELLE ein (@quelle=1),
    bekommt NEU dort denselben Eintrag.

    Liefert (changes, found); found = Anzahl ACLs, in denen QUELLE vorkommt,
    damit der Aufrufer "QUELLE steht nirgends" von "schon alles erledigt"
    unterscheiden kann. Herabgestuft wird nie, mehrfaches Ausführen ist
    unschädlich.
    """
    changes = []
    found = 0
    for path, raw in nodes.items():
        if raw is None:
            continue  # erbt; wird über den Elternordner mitgeändert
        acl = Acl.parse(raw)
        rights = acl.client_rights()
        if source not in rights:
            continue
        found += 1
        # explizites Recht in dieser ACL: ist QUELLE Besitzer, bekommt NEU das
        # Besitzer-Recht plus Verwaltungsrecht (=19)
        perm, source_owns = rights[source]
        if source_owns:
            perm |= active().admin
        if covers(rights.get(new, (0, False))[0], perm):
            continue
        # anhängen; vorhandene Einträge von NEU werden an ihrer Stelle um die
        # fehlenden Bits ergänzt. Ist NEU Besitzer, bleibt der Eintrag trotzdem,
        # denn er trägt Bits, die das Besitzer-Recht nicht hat.
        tokens = merge_entries(acl.tokens + [f"{new}={perm}"], new)
        changes.append(Change(path, raw, Acl(acl.owner, tokens).render()))
    return changes, found


def plan_replace(nodes: dict, old: str, new: str) -> list:
    """ALT wird in jeder ACL, die ALT explizit nennt, durch NEU ersetzt,
    als Eintrag und als Besitzer. Geerbtes bleibt unberührt."""
    changes = []
    for path, raw in nodes.items():
        if raw is None:
            continue  # erbt; wird über den Elternordner mitgeändert
        acl = Acl.parse(raw)
        if acl.owner != old and all(split_token(t)[0] != old for t in acl.tokens):
            continue
        owner = new if acl.owner == old else acl.owner
        # ALT an derselben Stelle in NEU umbenennen; hatte NEU schon einen
        # Eintrag, bleibt einer mit den Bits beider
        tokens = []
        for token in acl.tokens:
            principal, perm = split_token(token)
            tokens.append(f"{new}={perm}" if principal == old and perm is not None else token)
        changes.append(Change(path, raw, Acl(owner, merge_entries(tokens, new)).render()))
    return changes


def plan_remove(nodes: dict, clientid: str, new_owner: str | None) -> tuple:
    """Entfernt clientid aus allen ACLs.

    Eine ACL braucht einen Besitzer. Ist clientid Besitzer, wird er durch
    new_owner ersetzt; ohne new_owner bleibt der Pfad unverändert und landet
    in owned, damit der Aufrufer ihn melden kann. Liefert (changes, owned).
    new_owner == clientid ist sinnlos (die Bits über 3 gingen verloren) und
    wird abgelehnt.
    """
    if new_owner == clientid:
        raise ValueError(f"neuer Besitzer {new_owner} ist die zu entfernende clientid")
    changes = []
    owned = []
    for path, raw in nodes.items():
        if raw is None:
            continue
        acl = Acl.parse(raw)
        owner = acl.owner
        if owner == clientid:
            if new_owner is None:
                owned.append(path)
                continue
            owner = new_owner
        tokens = [t for t in acl.tokens if split_token(t)[0] != clientid]
        if owner != acl.owner:
            # mehrere Einträge des neuen Besitzers zu einem zusammenfassen
            tokens = merge_entries(tokens, owner)
        if owner != acl.owner or tokens != acl.tokens:
            changes.append(Change(path, raw, Acl(owner, tokens).render()))
    return changes, owned


def plan_restore(nodes: dict, backup: list, force: bool) -> tuple:
    """Dreht die Changes eines Backups zurück: new -> old.

    Zurückgesetzt wird nur, wo HDFS noch den Wert hat, den wir damals
    geschrieben haben (new). Hat jemand den Pfad seitdem geändert, würde
    restore dessen Änderung stillschweigend überschreiben; solche Pfade
    landen in conflicts, außer mit force. Liefert (changes, conflicts).
    """
    changes = []
    conflicts = []
    for entry in backup:
        if entry.path not in nodes:
            conflicts.append(f"{entry.path}: existiert nicht mehr oder ist nicht lesbar")
            continue
        current = nodes[entry.path]
        if current == entry.old:
            # schon zurückgesetzt (zweites restore) oder nie geschrieben
            # (Teilfehler beim Schreiben): nichts zu tun, kein Konflikt
            continue
        if current != entry.new and not force:
            conflicts.append(f"{entry.path}: wurde seit dem Backup geändert ({current!r})")
            continue
        changes.append(Change(entry.path, current, entry.old))
    return changes, conflicts
