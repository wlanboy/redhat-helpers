"""Änderungen planen: aus dem aktuellen Stand und einem Kommando eine Liste Change.

Alle plan_*-Funktionen bekommen nodes = {pfad: roh_acl oder None}, frisch aus
HDFS gelesen, und schreiben selbst nichts. Geschrieben wird erst in
commands.execute(), und nur mit --apply.

Gemeinsame Regeln:
- Geändert werden nur Pfade mit eigener ACL. Pfade, die erben, ändern sich
  über ihren Elternordner automatisch mit.
- Einträge für User, Gruppen und andere clientids bleiben Wort für Wort
  und an ihrer Stelle erhalten.
- Container-ACLs erweitern oder schränken die geerbte ACL ein (acl.inherit).
  Ein Eintrag im Account wirkt deshalb in jedem Container, der die clientid
  nicht selbst nennt. add und replace planen darum über den ganzen Baum
  (align): Wo die Vererbung NEU zu viele oder zu wenige Rechte gäbe, bekommt
  die Container-ACL einen eigenen Eintrag für NEU.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from hdfs.acl import Acl, State, inherit, resolve, rights, split_token
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


def perm_of(state: State | None, clientid: str) -> int:
    """Effektives Recht von clientid in einem Stand (0 = keins)."""
    return rights(state).get(clientid, (0, False, None))[0]


def align(nodes: dict, planned: dict, clientid: str, want: dict) -> tuple:
    """Gleicht die Rechte von clientid nach dem Plan an want an.

    planned: {pfad: neue roh_acl} für Pfade mit eigener ACL (Ausgangspunkt:
    unverändert oder schon umgeschrieben). want: {pfad: recht}, das clientid
    am Ende effektiv haben soll. Von oben nach unten: Weicht das Recht nach
    Vererbung ab, bekommt die eigene ACL einen Eintrag clientid=want. Ist
    want 0, lässt sich nichts einschränken (eine Maske 0 gibt es nicht);
    solche Pfade landen in excess. Liefert (planned, excess).
    """
    states = {}
    excess = []
    for path in sorted(nodes, key=lambda p: p.count("/")):
        parent = states.get(posixpath.dirname(path))
        raw = planned.get(path)
        if raw is None:
            states[path] = parent
            continue
        acl = Acl.parse(raw)
        state = inherit(parent, path, acl)
        have, need = perm_of(state, clientid), want.get(path, 0)
        if have != need:
            if need:
                acl = Acl(acl.owner, set_entry(acl.tokens, clientid, need))
                planned[path] = acl.render()
                state = inherit(parent, path, acl)
            else:
                excess.append(path)
        states[path] = state
    return planned, excess


def to_changes(nodes: dict, planned: dict) -> list:
    return [Change(path, nodes[path], new) for path, new in planned.items() if new != nodes[path]]


def plan_add(nodes: dict, source: str, new: str) -> tuple:
    """NEU bekommt überall die Rechte (alle Bits) von QUELLE dazu (Migration).

    Phase 1 einer Migration: beide clientids greifen. Eine ACL hat nur einen
    Besitzer, ist QUELLE Besitzer, bleibt sie es. NEU bekommt dort einen
    Eintrag mit den Rechten des Besitzers plus dem Verwaltungsrecht
    (active().admin), damit NEU die ACL verwalten kann wie der Besitzer.
    Erst replace macht NEU zum Besitzer.

    Ziel je Pfad: NEU hat effektiv seine bisherigen Rechte | die von QUELLE.
    Meist reicht ein Eintrag im Account, Container erben ihn. Container, die
    QUELLE oder NEU selbst nennen oder einen anderen Besitzer haben, bekommen
    bei Bedarf einen eigenen Eintrag für NEU.

    Liefert (changes, found, excess). found = Anzahl ACLs, in denen QUELLE
    vorkommt, damit der Aufrufer "QUELLE steht nirgends" von "schon alles
    erledigt" unterscheiden kann. excess = Pfade, an denen NEU über die
    Vererbung mehr bekommt, als sich einschränken lässt: QUELLE und NEU hatten
    dort nichts (z. B. Container mit eigenem Besitzer unter einem Account, dessen
    Besitzer QUELLE ist). Herabgestuft wird nie, mehrfaches Ausführen ist
    unschädlich.
    """
    before = resolve(nodes)
    want = {}
    for path, state in before.items():
        r = rights(state)
        perm, source_owns, _ = r.get(source, (0, False, None))
        if source_owns:
            perm |= active().admin
        want[path] = perm | r.get(new, (0, False, None))[0]
    found = 0
    for raw in nodes.values():
        if raw is not None:
            acl = Acl.parse(raw)
            found += acl.owner == source or source in acl.client_entries()
    planned = {path: raw for path, raw in nodes.items() if raw is not None}
    planned, excess = align(nodes, planned, new, want)
    return to_changes(nodes, planned), found, excess


def plan_replace(nodes: dict, old: str, new: str) -> list:
    """ALT wird überall durch NEU ersetzt, als Eintrag und als Besitzer.

    NEU hat danach überall genau die Rechte von ALT plus seine eigenen.
    """
    before = resolve(nodes)
    want = {path: perm_of(state, old) | perm_of(state, new) for path, state in before.items()}
    planned = {}
    for path, raw in nodes.items():
        if raw is None:
            continue  # erbt; wird über den Elternordner mitgeändert
        acl = Acl.parse(raw)
        if acl.owner != old and all(split_token(t)[0] != old for t in acl.tokens):
            planned[path] = raw
            continue
        owner = new if acl.owner == old else acl.owner
        # ALT an derselben Stelle in NEU umbenennen; hatte NEU schon einen
        # Eintrag, bleibt einer mit den Bits beider
        tokens = []
        for token in acl.tokens:
            principal, perm = split_token(token)
            tokens.append(f"{new}={perm}" if principal == old and perm is not None else token)
        planned[path] = Acl(owner, merge_entries(tokens, new)).render()
    planned, _ = align(nodes, planned, new, want)
    return to_changes(nodes, planned)


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
