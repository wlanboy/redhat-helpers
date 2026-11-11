"""Tests für hdfs.changes: add/replace/remove/restore planen (Beispiele aus hdfs.md)."""

import unittest
from typing import ClassVar

from hdfs.changes import (
    Change,
    merge_entries,
    plan_add,
    plan_remove,
    plan_replace,
    plan_restore,
)


def new_values(changes: list) -> dict:
    return {c.path: c.new for c in changes}


class MergeEntriesTest(unittest.TestCase):
    def test_docstring_example(self):
        self.assertEqual(merge_entries(["@a=1", "#ops=3", "@a=4"], "@a", owner=False), ["@a=5", "#ops=3"])

    def test_absent(self):
        tokens = ["@b=1", "#ops=3"]
        self.assertIs(merge_entries(tokens, "@a", owner=False), tokens)

    def test_owner_covered_drops_entries(self):
        self.assertEqual(merge_entries(["@a=1", "@b=1", "@a=2"], "@a", owner=True), ["@b=1"])

    def test_owner_extra_bits_stay(self):
        self.assertEqual(merge_entries(["@a=16", "@b=1"], "@a", owner=True), ["@a=16", "@b=1"])


class PlanAddTest(unittest.TestCase):
    def test_source_is_owner_gets_19(self):
        nodes = {"/fs/a": "@client3|@test1=1,@test2=2"}
        changes, found = plan_add(nodes, "@client3", "@newapp")
        self.assertEqual(found, 1)
        self.assertEqual(changes, [Change("/fs/a", "@client3|@test1=1,@test2=2",
                                          "@client3|@test1=1,@test2=2,@newapp=19")])

    def test_entry_appended(self):
        changes, _ = plan_add({"/fs/a": "@o|@client3=1,#ops=3"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@o|@client3=1,#ops=3,@newapp=1"})

    def test_missing_bits_added_in_place(self):
        changes, _ = plan_add({"/fs/a": "@o|@newapp=4,@client3=1"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@o|@newapp=5,@client3=1"})

    def test_already_covered(self):
        changes, found = plan_add({"/fs/a": "@o|@client3=1,@newapp=3"}, "@client3", "@newapp")
        self.assertEqual((changes, found), ([], 1))

    def test_idempotent(self):
        nodes = {"/fs/a": "@client3|@x=1", "/fs/b": "@o|@client3=3"}
        changes, _ = plan_add(nodes, "@client3", "@newapp")
        nodes.update(new_values(changes))
        self.assertEqual(plan_add(nodes, "@client3", "@newapp"), ([], 2))

    def test_inheriting_and_unrelated_paths_untouched(self):
        nodes = {"/fs/a": None, "/fs/b": "@o|@x=1"}
        self.assertEqual(plan_add(nodes, "@client3", "@newapp"), ([], 0))

    def test_ldap_and_users_kept(self):
        raw = "@o|cn=doc,ou=hell,dc=firma=3,svc=1,@client3=1"
        changes, _ = plan_add({"/fs/a": raw}, "@client3", "@newapp")
        self.assertEqual(changes[0].new, raw + ",@newapp=1")


class PlanReplaceTest(unittest.TestCase):
    def test_entry_renamed_in_place(self):
        changes = plan_replace({"/fs/a": "@o|@client3=1,#ops=3"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@o|@newapp=1,#ops=3"})

    def test_owner_replaced(self):
        changes = plan_replace({"/fs/a": "@client3|@x=1"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@newapp|@x=1"})

    def test_after_add_keeps_bit_16(self):
        changes = plan_replace({"/fs/a": "@client3|@test1=1,@test2=2,@newapp=19"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@newapp|@test1=1,@test2=2,@newapp=19"})

    def test_bits_of_both_merged(self):
        changes = plan_replace({"/fs/a": "@o|@client3=1,@newapp=4"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@o|@newapp=5"})

    def test_new_owner_drops_covered_entry(self):
        changes = plan_replace({"/fs/a": "@client3|@newapp=3"}, "@client3", "@newapp")
        self.assertEqual(new_values(changes), {"/fs/a": "@newapp|"})

    def test_unrelated_untouched(self):
        self.assertEqual(plan_replace({"/fs/a": "@o|@x=1", "/fs/b": None}, "@client3", "@newapp"), [])


class PlanRemoveTest(unittest.TestCase):
    def test_entry_removed(self):
        changes, owned = plan_remove({"/fs/a": "@o|@c=1,#g=3,@c=16"}, "@c", None)
        self.assertEqual((new_values(changes), owned), ({"/fs/a": "@o|#g=3"}, []))

    def test_owner_without_new_owner_skipped(self):
        changes, owned = plan_remove({"/fs/a": "@c|@x=1"}, "@c", None)
        self.assertEqual((changes, owned), ([], ["/fs/a"]))

    def test_owner_with_group(self):
        changes, _ = plan_remove({"/fs/a": "@c|@x=1"}, "@c", "#admins")
        self.assertEqual(new_values(changes), {"/fs/a": "#admins|@x=1"})

    def test_new_owner_drops_covered_entry(self):
        changes, _ = plan_remove({"/fs/a": "@c|@x=1,@n=3"}, "@c", "@n")
        self.assertEqual(new_values(changes), {"/fs/a": "@n|@x=1"})
        changes, _ = plan_remove({"/fs/a": "@c|@n=19"}, "@c", "@n")
        self.assertEqual(new_values(changes), {"/fs/a": "@n|@n=19"})

    def test_last_entry_leaves_owner_only(self):
        changes, _ = plan_remove({"/fs/a": "@o|@c=1"}, "@c", None)
        self.assertEqual(new_values(changes), {"/fs/a": "@o|"})

    def test_unrelated_untouched(self):
        self.assertEqual(plan_remove({"/fs/a": "@o|@x=1", "/fs/b": None}, "@c", None), ([], []))

    def test_new_owner_equals_clientid(self):
        with self.assertRaises(ValueError):
            plan_remove({}, "@c", "@c")


class PlanRestoreTest(unittest.TestCase):
    backup: ClassVar[list[Change]] = [Change("/fs/a", "@o|", "@o|@n=1"), Change("/fs/b", None, "@o|@n=1")]

    def test_restore(self):
        changes, conflicts = plan_restore({"/fs/a": "@o|@n=1", "/fs/b": "@o|@n=1"}, self.backup, False)
        self.assertEqual(changes, [Change("/fs/a", "@o|@n=1", "@o|"), Change("/fs/b", "@o|@n=1", None)])
        self.assertEqual(conflicts, [])

    def test_already_restored(self):
        self.assertEqual(plan_restore({"/fs/a": "@o|", "/fs/b": None}, self.backup, False), ([], []))

    def test_conflict_and_force(self):
        nodes = {"/fs/a": "@o|@other=1", "/fs/b": "@o|@n=1"}
        changes, conflicts = plan_restore(nodes, self.backup, False)
        self.assertEqual([c.path for c in changes], ["/fs/b"])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("/fs/a", conflicts[0])
        changes, conflicts = plan_restore(nodes, self.backup, True)
        self.assertEqual(([c.path for c in changes], conflicts), (["/fs/a", "/fs/b"], []))

    def test_missing_path_is_conflict(self):
        changes, conflicts = plan_restore({"/fs/b": "@o|@n=1"}, self.backup, True)
        self.assertEqual([c.path for c in changes], ["/fs/b"])
        self.assertIn("existiert nicht mehr", conflicts[0])


if __name__ == "__main__":
    unittest.main()
