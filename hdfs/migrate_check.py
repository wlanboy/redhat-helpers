#!/usr/bin/env python3
"""Prüft eine Migration ALT -> NEU gegen die Testdaten (aufgerufen von migrate.sh).

Verglichen werden die Vergleichsdateien von test.sh (Stand vor der Migration)
mit einer DB, die per "collect --live" geschrieben wurde. Verglichen werden
nur die ACL-Einträge selbst (Tabelle rights: Rechte je clientid und ACL),
nichts Geerbtes. add und replace übertragen nur, was ALT in einer ACL
explizit stehen hat. --stage wählt, was gelten muss:

  added     nach Phase 1 (add ALT NEU): beide clientids greifen.
            - In jeder ACL, die ALT nennt, hat NEU die Bits von ALT dazu,
              wo ALT Besitzer ist plus Verwaltungsrecht (19). ALT und alle
              anderen haben genau die Rechte wie vorher.
            - Es gibt nur einen Besitzer, und der bleibt: War ALT Besitzer,
              ist es weiter ALT.
            - Außer den Einträgen von NEU ist jede ACL Wort für Wort gleich,
              NEU steht höchstens einmal da. ACLs ohne ALT sind unverändert.
  replaced  nach Phase 2 (replace ALT NEU) oder direktem replace:
            - Rechte genau wie vorher, nur heißt ALT jetzt NEU. Hatte NEU in
              einer ACL schon Rechte, gelten die Bits beider.
            - ALT steht in keiner ACL mehr, weder als Besitzer noch als Eintrag.
            - War ALT Besitzer, ist jetzt NEU Besitzer, sonst bleibt der
              Besitzer. NEU steht höchstens einmal als Eintrag da.
            - ACLs ohne ALT sind Wort für Wort unverändert.
            Mit --after-add (Phase 2 nach Phase 1) behält NEU den Eintrag
            aus Phase 1 dort, wo ALT Besitzer war.
  restored  nach dem Zurückdrehen: Rechte und ACLs genau wie in den Testdaten.

Ohne --accounts migrieren add und replace nur Container: die Accounts (erste
Ebene unter --base-dir) müssen dann bei added/replaced unverändert sein.

Exit-Code 0, wenn alles stimmt, sonst 1.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hdfs.acl import Acl, split_token
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


def want_rights(expected: list, owners: dict, stage: str, old: str, new: str,
                after_add: bool, migrated) -> dict:
    """Erwartete Rechte je ACL {(pfad, clientid): recht}.

    owners: {pfad: besitzer} aus acls.csv. Wo ALT Besitzer der ACL ist,
    bekommt NEU bei add zusätzlich das Verwaltungsrecht (active().admin).
    migrated(pfad): ob add/replace den Pfad anfassen durfte.
    """
    rights = {}

    def grant(path: str, clientid: str, perm: int) -> None:
        rights[(path, clientid)] = perm | rights.get((path, clientid), 0)

    for row in expected:
        path, clientid, perm = row["path"], row["clientid"], int(row["perm"])
        admin = active().admin if clientid == old and owners.get(path) == old else 0
        if not migrated(path):
            grant(path, clientid, perm)
        elif stage == "replaced" and clientid == old:
            # ALT heißt jetzt NEU; nach add bleibt das Verwaltungsrecht aus Phase 1
            grant(path, new, perm | (admin if after_add else 0))
        else:
            grant(path, clientid, perm)
            if stage == "added" and clientid == old:
                grant(path, new, perm | admin)  # NEU bekommt zusätzlich die Rechte von ALT
    return rights


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
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--stage", choices=["added", "replaced", "restored"], required=True)
    parser.add_argument("--old", help="clientid vor der Migration")
    parser.add_argument("--new", help="clientid nach der Migration")
    parser.add_argument("--after-add", action="store_true",
                        help="--stage replaced nach Phase 1 (add): NEU behält den Eintrag aus Phase 1")
    parser.add_argument("--accounts", action="store_true",
                        help="add/replace liefen mit --accounts: auch Accounts sind migriert")
    parser.add_argument("--base-dir", default=os.environ.get("BASE_DIR", "/fs"),
                        help="Basisordner der Testdaten (Default: $BASE_DIR oder /fs)")
    parser.add_argument("--acls", type=Path, required=True, help="acls.csv von test.sh")
    parser.add_argument("--expected", type=Path, required=True, help="expected.csv von test.sh")
    parser.add_argument("--db", type=Path, required=True, help="DB von collect --live")
    parser.add_argument("--permissions", metavar="DATEI",
                        help="Rechte-Tabelle wie bei hdfs-acl (Default: $HDFS_ACL_PERMISSIONS, "
                             "sonst permissions.json neben der DB, sonst eingebaut)")
    args = parser.parse_args()
    activate(find_permissions(args.permissions, args.db))
    if args.stage != "restored" and not (args.old and args.new):
        parser.error(f"--stage {args.stage} braucht --old und --new")

    con = sqlite3.connect(args.db)
    actual_acl = dict(con.execute("SELECT path, acl FROM node"))
    actual_rights = {(path, clientid): perm
                     for path, clientid, perm in con.execute("SELECT path, clientid, perm FROM rights")}

    errors = []
    prefix = args.base_dir.rstrip("/") + "/"

    def migrated(path: str) -> bool:
        """Account (erste Ebene) nur mit --accounts, Container immer."""
        return args.accounts or "/" in path.removeprefix(prefix)

    # Rechte je ACL
    acls = read_csv(args.acls)
    owners = {row["path"]: Acl.parse(row["acl"]).owner for row in acls if row["acl"]}
    wanted = want_rights(read_csv(args.expected), owners, args.stage, args.old, args.new, args.after_add,
                         migrated)
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
        if (args.stage == "restored" or before is None or args.old not in principals(before)
                or not migrated(path)):
            unchanged += 1
            if after != before:
                errors.append(f"{path}: hätte sich nicht ändern dürfen, {before!r} -> {after!r}")
            continue
        touched += 1
        if after is None or after == before:
            # bei add zulässig, wenn NEU dort schon alle Rechte von ALT hatte
            # (das prüft der Vergleich der Rechte oben)
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
              f"{unchanged} Pfade ohne {args.old} oder nicht migriert unverändert")
        print(f"  {args.old} steht in {paths_of(actual_rights, args.old)} ACLs "
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
