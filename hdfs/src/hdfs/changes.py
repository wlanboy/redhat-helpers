"""Änderungen planen: aus dem aktuellen Stand und einem Kommando eine Liste Change.

Alle plan_*-Funktionen bekommen nodes = {pfad: roh_acl oder None}, frisch aus
HDFS gelesen, und schreiben selbst nichts. Geschrieben wird erst in
commands.execute(), und nur mit --apply.

Gemeinsame Regeln:
- Geändert werden nur Pfade mit eigener ACL. Pfade, die erben, ändern sich
  über ihren Elternordner automatisch mit.
- Einträge für User, Gruppen und andere clientids bleiben Wort für Wort
  und an ihrer Stelle erhalten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from hdfs.acl import Acl, split_token


@dataclass
class Change:
    path: str
    old: Optional[str]  # None = Pfad hatte kein Attribut
    new: Optional[str]  # None = Attribut entfernen


def merge_entries(tokens: list, principal: str, drop: bool) -> list:
    """Fasst mehrere Einträge von principal zu einem mit dem höchsten Recht an der
    ersten Stelle zusammen; drop=True entfernt sie (principal ist Besitzer).

    Beispiel: ['@a=1', '#ops=3', '@a=3'] -> ['@a=3', '#ops=3']
    """
    perms = [perm for p, perm in map(split_token, tokens) if p == principal and perm is not None]
    if not perms:
        return tokens
    merged = []
    # placed: der zusammengefasste Eintrag steht schon in merged; bei drop=True
    # wird so getan, als ob, damit alle Einträge von principal wegfallen
    placed = drop
    for token in tokens:
        if split_token(token)[0] != principal:
            merged.append(token)
        elif not placed:
            merged.append(f"{principal}={max(perms)}")
            placed = True
    return merged


def plan_add(nodes: dict, source: str, new: str) -> tuple:
    """NEU bekommt überall mindestens die Rechte von QUELLE (Migration).

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
        # effektives Recht verwenden: ist QUELLE Besitzer, bekommt NEU =3 als Eintrag
        perm = rights[source][0]
        if rights.get(new, (0, False))[0] >= perm:
            continue
        # anhängen; vorhandene schwächere Einträge von NEU werden an ihrer Stelle hochgestuft
        tokens = merge_entries(acl.tokens + [f"{new}={perm}"], new, drop=False)
        changes.append(Change(path, raw, Acl(acl.owner, tokens).render()))
    return changes, found


def plan_replace(nodes: dict, old: str, new: str) -> list:
    """ALT wird überall durch NEU ersetzt, als Eintrag und als Besitzer."""
    changes = []
    for path, raw in nodes.items():
        if raw is None:
            continue  # erbt; wird über den Elternordner mitgeändert
        acl = Acl.parse(raw)
        if acl.owner != old and all(split_token(t)[0] != old for t in acl.tokens):
            continue
        owner = new if acl.owner == old else acl.owner
        # ALT an derselben Stelle in NEU umbenennen; hatte NEU schon einen Eintrag,
        # bleibt einer mit dem höheren Recht, als Besitzer braucht NEU keinen
        tokens = []
        for token in acl.tokens:
            principal, perm = split_token(token)
            tokens.append(f"{new}={perm}" if principal == old and perm is not None else token)
        tokens = merge_entries(tokens, new, drop=owner == new)
        changes.append(Change(path, raw, Acl(owner, tokens).render()))
    return changes


def plan_remove(nodes: dict, clientid: str, new_owner: Optional[str]) -> tuple:
    """Entfernt clientid aus allen ACLs.

    Eine ACL braucht einen Besitzer. Ist clientid Besitzer, wird er durch
    new_owner ersetzt; ohne new_owner bleibt der Pfad unverändert und landet
    in owned, damit der Aufrufer ihn melden kann. Liefert (changes, owned).
    """
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
            # z. B. nach add ALT NEU: NEU=3 ist als Besitzer überflüssig
            tokens = merge_entries(tokens, owner, drop=True)
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
            conflicts.append(f"{entry.path}: existiert nicht mehr")
            continue
        current = nodes[entry.path]
        if current != entry.new and not force:
            conflicts.append(f"{entry.path}: wurde seit dem Backup geändert ({current!r})")
            continue
        if current != entry.old:
            changes.append(Change(entry.path, current, entry.old))
    return changes, conflicts
