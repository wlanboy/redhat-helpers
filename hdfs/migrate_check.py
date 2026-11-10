#!/usr/bin/env python3
"""Prüft eine Migration ALT -> NEU gegen die Testdaten (aufgerufen von migrate.sh).

Verglichen werden die Vergleichsdateien von test.sh (Stand vor der Migration)
mit einer DB, die nach der Migration per "collect --live" geschrieben wurde:

1. Effektive Rechte: genau expected.csv, nur dass ALT jetzt NEU heißt. Hatte
   NEU an einem Pfad schon Rechte, gelten die Bits beider (verodert).
2. ALT steht in keiner ACL mehr, weder als Besitzer noch als Eintrag.
3. Jede ACL, in der ALT vorkam, wurde geändert und enthält NEU.
4. Alle anderen Pfade sind Wort für Wort unverändert.

Mit --restored (nach dem Zurückdrehen) gilt stattdessen: Rechte und ACLs
sind genau wie in den Testdaten.

Exit-Code 0, wenn alles stimmt, sonst 1.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from hdfs.acl import Acl, split_token  # noqa: E402


def principals(raw: str) -> set:
    """Besitzer und alle Einträge einer Roh-ACL."""
    acl = Acl.parse(raw)
    return {acl.owner} | {split_token(token)[0] for token in acl.tokens}


def read_csv(path: Path) -> list:
    with path.open(newline="") as file:
        return list(csv.DictReader(file, delimiter=";"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old", help="clientid vor der Migration")
    parser.add_argument("--new", help="clientid nach der Migration")
    parser.add_argument("--restored", action="store_true",
                        help="Stand muss genau den Testdaten entsprechen")
    parser.add_argument("--acls", type=Path, required=True, help="acls.csv von test.sh")
    parser.add_argument("--expected", type=Path, required=True, help="expected.csv von test.sh")
    parser.add_argument("--db", type=Path, required=True, help="DB nach der Migration (collect --live)")
    args = parser.parse_args()
    if args.restored:
        args.old = args.new = None
    elif not (args.old and args.new):
        parser.error("--old und --new angeben, oder --restored")

    con = sqlite3.connect(args.db)
    actual_acl = dict(con.execute("SELECT path, acl FROM node"))
    actual_rights = {(path, clientid): (perm, acl_from) for path, clientid, perm, acl_from
                     in con.execute("SELECT path, clientid, perm, acl_from FROM effective")}

    errors = []

    # 1. effektive Rechte: ALT in NEU umbenennen, doppelte Einträge verodern
    want_rights = {}
    for row in read_csv(args.expected):
        clientid = args.new if row["clientid"] == args.old else row["clientid"]
        key = (row["path"], clientid)
        perm = int(row["perm"]) | want_rights.get(key, (0, None))[0]
        want_rights[key] = (perm, row["acl_from"])
    for key in sorted(want_rights.keys() | actual_rights.keys()):
        want, got = want_rights.get(key), actual_rights.get(key)
        if want != got:
            errors.append(f"Recht {key[0]} {key[1]}: erwartet {want}, ist {got}")

    # 2.-4. Roh-ACLs
    migrated = unchanged = 0
    for row in read_csv(args.acls):
        path, before = row["path"], row["acl"] or None
        if path not in actual_acl:
            errors.append(f"{path}: fehlt nach der Migration")
            continue
        after = actual_acl[path]
        if args.old and after is not None and args.old in principals(after):
            errors.append(f"{path}: {args.old} steht noch in {after!r}")
        if args.old and before is not None and args.old in principals(before):
            migrated += 1
            if after is None or after == before or args.new not in principals(after):
                errors.append(f"{path}: nicht migriert, {before!r} -> {after!r}")
        else:
            unchanged += 1
            if after != before:
                errors.append(f"{path}: hätte sich nicht ändern dürfen, {before!r} -> {after!r}")

    def paths_of(rights: dict, clientid: str) -> int:
        return sum(c == clientid for _, c in rights)

    if args.restored:
        print(f"  {unchanged} Pfade, {len(actual_rights)} Rechte verglichen")
    else:
        print(f"  {migrated} ACLs enthielten {args.old}, {unchanged} Pfade ohne {args.old}")
        print(f"  {args.new} hat Rechte an {paths_of(actual_rights, args.new)} Pfaden "
              f"(erwartet {paths_of(want_rights, args.new)}), "
              f"{args.old} an {paths_of(actual_rights, args.old)}")
    for error in errors[:50]:
        print(f"  FEHLER {error}", file=sys.stderr)
    if len(errors) > 50:
        print(f"  ... und {len(errors) - 50} weitere", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
