#!/usr/bin/env python3
"""Prüft eine Migration ALT -> NEU gegen die Testdaten (aufgerufen von migrate.sh).

Verglichen werden die Vergleichsdateien von test.sh (Stand vor der Migration)
mit einer DB, die per "collect --live" geschrieben wurde. --stage wählt, was
gelten muss:

  added     nach Phase 1 (add ALT NEU): beide clientids greifen.
            - ALT hat genau die Rechte wie vorher, NEU an jedem Pfad die von
              ALT verodert mit seinen eigenen, wo ALT Besitzer ist plus
              Verwaltungsrecht (19). Ausnahme: Pfade, an denen ALT und NEU
              nichts hatten, NEU aber den Eintrag im Account erbt (Container
              mit eigenem Besitzer); sie werden gezählt, nicht als Fehler.
            - Es gibt nur einen Besitzer, und der bleibt: War ALT Besitzer,
              ist es weiter ALT. NEU steht dort als Eintrag mit mindestens 19.
            - Außer den Einträgen von NEU ist jede ACL Wort für Wort gleich,
              NEU steht höchstens einmal da.
  replaced  nach Phase 2 (replace ALT NEU) oder direktem replace:
            - Effektive Rechte genau wie vorher, nur heißt ALT jetzt NEU. Hatte
              NEU an einem Pfad schon Rechte, gelten die Bits beider.
            - ALT steht in keiner ACL mehr, weder als Besitzer noch als Eintrag.
            - War ALT Besitzer, ist jetzt NEU Besitzer, sonst bleibt der
              Besitzer. NEU steht höchstens einmal als Eintrag da.
            - Außer den Einträgen von NEU sind Pfade ohne ALT Wort für Wort
              unverändert.
            Mit --after-add (Phase 2 nach Phase 1) behält NEU das Bit 16 aus
            Phase 1 dort, wo ALT Besitzer war.
  restored  nach dem Zurückdrehen: Rechte und ACLs genau wie in den Testdaten.

Verglichen werden bei added/replaced nur die Masken. acl_von hängt davon ab,
in welcher ACL der Eintrag von NEU steht, und der darf sich verschieben.

Exit-Code 0, wenn alles stimmt, sonst 1.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hdfs.acl import Acl, resolve, rights, split_token
from hdfs.permissions import activate, active
from hdfs.permissions import find as find_permissions


def principals(raw: str | None) -> set:
    """Besitzer und alle Einträge einer Roh-ACL (leer ohne ACL)."""
    if raw is None:
        return set()
    acl = Acl.parse(raw)
    return {acl.owner} | {split_token(token)[0] for token in acl.tokens}


def entries_of(acl: Acl, principal: str) -> list:
    """Bits aller Einträge von principal."""
    return [perm for p, perm in map(split_token, acl.tokens) if p == principal]


def without(acl: Acl, principal: str) -> Acl:
    """Die ACL ohne die Einträge von principal (Besitzer bleibt)."""
    return Acl(acl.owner, [t for t in acl.tokens if split_token(t)[0] != principal])


def read_csv(path: Path) -> list:
    with path.open(newline="") as file:
        return list(csv.DictReader(file, delimiter=";"))


def want_rights(expected: list, old_owns: set, stage: str, old: str, new: str,
                after_add: bool) -> dict:
    """Erwartete effektive Rechte {(pfad, clientid): (recht, acl_von)}.

    old_owns: Pfade, an denen ALT laut Testdaten effektiv Besitzer ist. Dort
    bekommt NEU bei add zusätzlich das Verwaltungsrecht (active().admin).
    Bei added/replaced ist acl_von None (es wird nur die Maske verglichen).
    """
    rights_ = {}

    def grant(path: str, clientid: str, perm: int, acl_from: str | None) -> None:
        rights_[(path, clientid)] = (perm | rights_.get((path, clientid), (0, None))[0], acl_from)

    for row in expected:
        path, clientid, perm = row["path"], row["clientid"], int(row["perm"])
        acl_from = row["acl_from"] if stage == "restored" else None
        admin = active().admin if clientid == old and path in old_owns else 0
        if stage == "replaced" and clientid == old:
            # ALT heißt jetzt NEU; nach add bleibt das Verwaltungsrecht aus Phase 1
            grant(path, new, perm | (admin if after_add else 0), acl_from)
        else:
            grant(path, clientid, perm, acl_from)
            if stage == "added" and clientid == old:
                grant(path, new, perm | admin, acl_from)  # NEU bekommt zusätzlich die Rechte von ALT
    return rights_


def check_added(before: Acl, after: Acl, old: str, new: str) -> list:
    errors = []
    if after.owner != before.owner:
        errors.append(f"Besitzer {after.owner}, muss {before.owner} bleiben")
    if without(after, new) != without(before, new):
        errors.append(f"außer den Einträgen von {new} hat sich etwas geändert")
    perms = entries_of(after, new)
    if len(perms) > 1:
        errors.append(f"{new} steht {len(perms)}-mal als Eintrag da, erwartet höchstens einmal")
    return errors


def check_replaced(before: Acl, after: Acl, old: str, new: str) -> list:
    errors = []
    if old in principals(after.render()):
        errors.append(f"{old} steht noch drin")
    want_owner = new if before.owner == old else before.owner
    if after.owner != want_owner:
        errors.append(f"Besitzer {after.owner}, erwartet {want_owner}")
    perms = entries_of(after, new)
    if len(perms) > 1:
        errors.append(f"{new} steht {len(perms)}-mal als Eintrag da")
    if old not in principals(before.render()) and without(after, new) != without(before, new):
        errors.append(f"ohne {old}, aber außer den Einträgen von {new} geändert")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--stage", choices=["added", "replaced", "restored"], required=True)
    parser.add_argument("--old", help="clientid vor der Migration")
    parser.add_argument("--new", help="clientid nach der Migration")
    parser.add_argument("--after-add", action="store_true",
                        help="--stage replaced nach Phase 1 (add): NEU behält das Bit 16")
    parser.add_argument("--acls", type=Path, required=True, help="acls.csv von test.sh")
    parser.add_argument("--expected", type=Path, required=True, help="expected.csv von test.sh")
    parser.add_argument("--db", type=Path, required=True, help="DB von collect --live")
    parser.add_argument("--permissions", metavar="DATEI",
                        help="Rechte-Tabelle wie bei hdfs-acl (Default: $HDFS_ACL_PERMISSIONS, "
                             "sonst permissions.json neben der DB, sonst Spring-Standard)")
    args = parser.parse_args()
    activate(find_permissions(args.permissions, args.db))
    if args.stage != "restored" and not (args.old and args.new):
        parser.error(f"--stage {args.stage} braucht --old und --new")

    con = sqlite3.connect(args.db)
    actual_acl = dict(con.execute("SELECT path, acl FROM node"))
    keep_from = args.stage == "restored"
    actual_rights = {(path, clientid): (perm, acl_from if keep_from else None)
                     for path, clientid, perm, acl_from
                     in con.execute("SELECT path, clientid, perm, acl_from FROM effective")}

    errors = []

    # effektive Rechte
    acls = read_csv(args.acls)
    states = resolve({row["path"]: row["acl"] or None for row in acls})
    old_owns = {path for path, state in states.items()
                if args.old and rights(state).get(args.old, (0, False))[1]}
    wanted = want_rights(read_csv(args.expected), old_owns, args.stage, args.old, args.new, args.after_add)
    inherited_extra = 0
    for key in sorted(wanted.keys() | actual_rights.keys()):
        want, got = wanted.get(key), actual_rights.get(key)
        if want == got:
            continue
        if args.stage == "added" and key[1] == args.new and want is None:
            # NEU erbt den Eintrag aus dem Account, wo ALT und NEU nichts hatten;
            # ohne Maske 0 nicht einschränkbar (hdfs-acl add warnt davor)
            inherited_extra += 1
            continue
        errors.append(f"Recht {key[0]} {key[1]}: erwartet {want}, ist {got}")

    # Roh-ACLs
    touched = unchanged = owned = 0
    for row in acls:
        path, before = row["path"], row["acl"] or None
        if path not in actual_acl:
            errors.append(f"{path}: fehlt")
            continue
        after = actual_acl[path]
        # before is None: ohne ACL kann ALT nicht drinstehen (so auch für pyright klar)
        if args.stage == "restored" or before is None or args.old not in principals(before):
            unchanged += 1
            if after == before:
                continue
            # nur Einträge von NEU dürfen dazukommen (Container, die erben würden)
            if args.stage == "restored" or before is None or after is None:
                errors.append(f"{path}: hätte sich nicht ändern dürfen, {before!r} -> {after!r}")
                continue
            check = check_added if args.stage == "added" else check_replaced
            errors += [f"{path}: {error}, {before!r} -> {after!r}"
                       for error in check(Acl.parse(before), Acl.parse(after), args.old, args.new)]
            continue
        touched += 1
        if after is None or after == before:
            # bei add zulässig: NEU erbt die Rechte von ALT schon von oben
            # (das prüft der Vergleich der effektiven Rechte oben)
            if args.stage != "added":
                errors.append(f"{path}: nicht geändert, {before!r}")
            continue
        acl_before, acl_after = Acl.parse(before), Acl.parse(after)
        owned += acl_before.owner == args.old
        check = check_added if args.stage == "added" else check_replaced
        errors += [f"{path}: {error}, {before!r} -> {after!r}"
                   for error in check(acl_before, acl_after, args.old, args.new)]

    def paths_of(rights: dict, clientid: str) -> int:
        return sum(c == clientid for _, c in rights)

    if args.stage == "restored":
        print(f"  {unchanged} Pfade, {len(actual_rights)} Rechte verglichen")
    else:
        print(f"  {touched} ACLs mit {args.old} (davon {owned}-mal Besitzer), "
              f"{unchanged} Pfade ohne {args.old} unverändert")
        print(f"  {args.old} hat Rechte an {paths_of(actual_rights, args.old)} Pfaden "
              f"(erwartet {paths_of(wanted, args.old)}), "
              f"{args.new} an {paths_of(actual_rights, args.new)} (erwartet {paths_of(wanted, args.new)})")
        owners = {clientid: sum(Acl.parse(a).owner == clientid for a in actual_acl.values() if a)
                  for clientid in (args.old, args.new)}
        print(f"  Besitzer: {args.old} an {owners[args.old]} ACLs, {args.new} an {owners[args.new]}")
        if inherited_extra:
            print(f"  {args.new} erbt an {inherited_extra} Pfaden Rechte, wo {args.old} keine hat "
                  "(eigener Besitzer im Container, nicht einschränkbar)")
    for error in errors[:50]:
        print(f"  FEHLER {error}", file=sys.stderr)
    if len(errors) > 50:
        print(f"  ... und {len(errors) - 50} weitere", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
