#!/usr/bin/env python3
"""Prüft eine Migration ALT -> NEU gegen die Testdaten (aufgerufen von migrate.sh).

Verglichen werden die Vergleichsdateien von test.sh (Stand vor der Migration)
mit einer DB, die per "collect --live" geschrieben wurde. --stage wählt, was
gelten muss:

  added     nach Phase 1 (add ALT NEU): beide clientids greifen.
            - ALT hat genau die Rechte wie vorher, NEU an jedem Pfad von ALT
              mindestens dieselben (verodert mit eigenen Rechten von NEU).
            - Es gibt nur einen Besitzer, und der bleibt: War ALT Besitzer,
              ist es weiter ALT. NEU steht dort als Eintrag mit den Rechten
              des Besitzers plus ADMINISTRATION (mindestens 3 + 16 = 19).
            - Außer den Einträgen von NEU ist jede ACL Wort für Wort gleich,
              NEU steht genau einmal da.
  replaced  nach Phase 2 (replace ALT NEU) oder direktem replace:
            - Effektive Rechte genau wie vorher, nur heißt ALT jetzt NEU. Hatte
              NEU an einem Pfad schon Rechte, gelten die Bits beider.
            - ALT steht in keiner ACL mehr, weder als Besitzer noch als Eintrag.
            - War ALT Besitzer, ist jetzt NEU Besitzer, sonst bleibt der
              Besitzer. NEU steht höchstens einmal als Eintrag da, als
              Besitzer nur mit Bits über 3.
            - Pfade ohne ALT sind Wort für Wort unverändert.
            Mit --after-add (Phase 2 nach Phase 1) behält NEU das Bit 16 aus
            Phase 1 dort, wo ALT Besitzer war.
  restored  nach dem Zurückdrehen: Rechte und ACLs genau wie in den Testdaten.

Exit-Code 0, wenn alles stimmt, sonst 1.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hdfs.acl import Acl, covers, split_token  # noqa: E402
from hdfs.permissions import activate, active  # noqa: E402
from hdfs.permissions import find as find_permissions  # noqa: E402


def principals(raw: Optional[str]) -> set:
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


def want_rights(expected: list, owners: dict, stage: str, old: str, new: str,
                after_add: bool) -> dict:
    """Erwartete effektive Rechte {(pfad, clientid): (recht, acl_von)}.

    owners: {pfad: besitzer} aus acls.csv. Wo die gültige ACL (acl_von) ALT
    als Besitzer hat, bekommt NEU bei add zusätzlich das Verwaltungsrecht (active().admin).
    """
    rights = {}

    def grant(path: str, clientid: str, perm: int, acl_from: str) -> None:
        rights[(path, clientid)] = (perm | rights.get((path, clientid), (0, None))[0], acl_from)

    for row in expected:
        path, clientid, perm, acl_from = row["path"], row["clientid"], int(row["perm"]), row["acl_from"]
        admin = active().admin if clientid == old and owners.get(acl_from) == old else 0
        if stage == "replaced" and clientid == old:
            # ALT heißt jetzt NEU; nach add bleibt das Bit 16 aus Phase 1
            grant(path, new, perm | (admin if after_add else 0), acl_from)
        else:
            grant(path, clientid, perm, acl_from)
            if stage == "added" and clientid == old:
                grant(path, new, perm | admin, acl_from)  # NEU bekommt zusätzlich die Rechte von ALT
    return rights


def check_added(before: Acl, after: Acl, old: str, new: str) -> list:
    errors = []
    if after.owner != before.owner:
        errors.append(f"Besitzer {after.owner}, muss {before.owner} bleiben")
    if without(after, new) != without(before, new):
        errors.append(f"außer den Einträgen von {new} hat sich etwas geändert")
    perms = entries_of(after, new)
    if after.owner == new:
        # NEU war schon Besitzer: ein Eintrag nur für Bits über dem Besitzer-Recht
        if len(perms) > 1 or (perms and covers(active().owner, perms[0])):
            errors.append(f"Einträge {new}={perms} neben Besitzer {new}")
    elif len(perms) != 1:
        errors.append(f"{new} steht {len(perms)}-mal als Eintrag da, erwartet einmal")
    elif before.owner == old and not covers(perms[0], active().owner | active().admin):
        errors.append(f"{new}={perms[0]} hat nicht die Rechte des Besitzers {old} "
                      f"plus ADMINISTRATION ({active().owner | active().admin})")
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
    if after.owner == new and perms and covers(active().owner, perms[0]):
        errors.append(f"überflüssiger Eintrag {new}={perms[0]} neben Besitzer {new}")
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
    actual_rights = {(path, clientid): (perm, acl_from) for path, clientid, perm, acl_from
                     in con.execute("SELECT path, clientid, perm, acl_from FROM effective")}

    errors = []

    # effektive Rechte
    acls = read_csv(args.acls)
    owners = {row["path"]: Acl.parse(row["acl"]).owner for row in acls if row["acl"]}
    wanted = want_rights(read_csv(args.expected), owners, args.stage, args.old, args.new, args.after_add)
    for key in sorted(wanted.keys() | actual_rights.keys()):
        want, got = wanted.get(key), actual_rights.get(key)
        if want != got:
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
            if after != before:
                errors.append(f"{path}: hätte sich nicht ändern dürfen, {before!r} -> {after!r}")
            continue
        touched += 1
        if after is None or after == before:
            # bei add zulässig, wenn NEU dort schon alle Rechte von ALT hatte
            # (das prüft der Vergleich der effektiven Rechte oben)
            if not (args.stage == "added" and args.new in principals(before)):
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
    for error in errors[:50]:
        print(f"  FEHLER {error}", file=sys.stderr)
    if len(errors) > 50:
        print(f"  ... und {len(errors) - 50} weitere", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
