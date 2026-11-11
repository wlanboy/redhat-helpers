"""Tests für hdfs.acl: Format, Gruppen, effektive Rechte, Vererbung, clientid-Prüfung."""

import unittest

from hdfs.acl import (
    Acl,
    acl_problems,
    client_id,
    covers,
    group_id,
    is_ldap,
    principal_kind,
    resolve,
    resolve_acl_from,
    rights,
    split_entries,
    split_token,
)
from hdfs.common import UsageError

OPS = ".cn=ops,ou=groups,dc=firma"


class SplitTokenTest(unittest.TestCase):
    def test_entry(self):
        self.assertEqual(split_token("@client1=3"), ("@client1", 3))

    def test_last_equals_wins(self):
        self.assertEqual(split_token("dc=firma=3"), ("dc=firma", 3))
        self.assertEqual(split_token(f"{OPS}=3"), (OPS, 3))

    def test_invalid(self):
        for token in ("@client1", "=3", "@client1=x", "@client1=", "cn=doc"):
            with self.subTest(token=token):
                self.assertEqual(split_token(token), (token, None))


class SplitEntriesTest(unittest.TestCase):
    def test_group_keeps_dn_together(self):
        self.assertEqual(split_entries(f"@a=1,{OPS}=3,peter=1"), ["@a=1", f"{OPS}=3", "peter=1"])

    def test_group_without_perm_stops_at_non_dn(self):
        self.assertEqual(split_entries(".cn=ops,ou=x,@a=1"), [".cn=ops,ou=x", "@a=1"])

    def test_ldap_without_dot_stays_split(self):
        self.assertEqual(split_entries("cn=doc,ou=hell,dc=firma=3"), ["cn=doc", "ou=hell", "dc=firma=3"])

    def test_spaces(self):
        self.assertEqual(split_entries(" .cn=a , dc=b=1 ,, @x=1 "), [".cn=a,dc=b=1", "@x=1"])


class LdapGroupTest(unittest.TestCase):
    def test_ldap_parts(self):
        for token in ("cn=doc", "ou=hell", "dc=firma=3", "CN=Doc", "uid=meier", "sn=meier"):
            with self.subTest(token=token):
                self.assertTrue(is_ldap(token))

    def test_not_ldap(self):
        # "cn=3" ist ein User "cn" mit Recht 3, kein DN-Teil; mit '.' ist es eine Gruppe
        for token in ("cn=3", "@client1=3", "svc_backup=1", "#ops=3", "cnx=doc", ".ou=x"):
            with self.subTest(token=token):
                self.assertFalse(is_ldap(token))

    def test_principal_kind(self):
        self.assertEqual(principal_kind("@client1"), "client")
        self.assertEqual(principal_kind(OPS), "group")
        self.assertEqual(principal_kind("#ops"), "user")  # '#' ist kein Gruppenpräfix mehr
        self.assertEqual(principal_kind("svc_backup"), "user")
        self.assertEqual(principal_kind("cn=doc"), "ldap")

    def test_group_id(self):
        self.assertEqual(group_id(" .cn=admins, ou=groups,dc=firma "), ".cn=admins,ou=groups,dc=firma")
        for bad in (".", "#admins", "cn=admins", ".cn=admins=3", ".cn=a|b", ".foo=x", ".cn=3"):
            with self.subTest(value=bad), self.assertRaises(UsageError):
                group_id(bad)


class AclTest(unittest.TestCase):
    def test_parse_render_roundtrip(self):
        raw = f"@client1|@client2=3,svc_backup=1,{OPS}=3,cn=doc,ou=hell,dc=firma=3"
        acl = Acl.parse(raw)
        self.assertEqual(acl.owner, "@client1")
        self.assertEqual(acl.tokens, ["@client2=3", "svc_backup=1", f"{OPS}=3", "cn=doc", "ou=hell", "dc=firma=3"])
        self.assertEqual(acl.render(), raw)

    def test_group_owner(self):
        acl = Acl.parse(f"{OPS}|@a=1")
        self.assertEqual((acl.owner, acl.tokens), (OPS, ["@a=1"]))
        self.assertEqual(acl.client_rights(), {"@a": (1, False)})

    def test_parse_strips_and_skips_empty(self):
        acl = Acl.parse(" @a | @b=1 ,, @c=3 ")
        self.assertEqual((acl.owner, acl.tokens), ("@a", ["@b=1", "@c=3"]))

    def test_owner_only(self):
        self.assertEqual(Acl.parse("@a|").tokens, [])
        self.assertEqual(Acl.parse("@a|").render(), "@a|")

    def test_readme_example(self):
        rights_ = Acl.parse(f"@client1|@client2=3,svc_backup=1,@client3=1,{OPS}=3").client_rights()
        self.assertEqual(rights_, {"@client1": (19, True), "@client2": (3, False), "@client3": (1, False)})

    def test_duplicate_entries_are_ored(self):
        self.assertEqual(Acl.parse("@o|@a=5,@a=16").client_rights()["@a"], (21, False))

    def test_owner_gets_at_least_owner_perm(self):
        self.assertEqual(Acl.parse("@o|@o=1").client_rights()["@o"], (19, True))
        self.assertEqual(Acl.parse("@o|@o=8").client_rights()["@o"], (27, True))

    def test_invalid_parts_give_no_rights(self):
        # wie acl_problems: ungültige Einträge und Besitzer zählen nicht
        self.assertEqual(Acl.parse("@a=3").client_rights(), {})
        self.assertEqual(Acl.parse("|@a=3").client_rights(), {"@a": (3, False)})
        self.assertEqual(Acl.parse(f"@o|@a=0,@b={2**31},@c={2**31 - 1},@d=x,@e,@f = 1").client_rights(),
                         {"@o": (19, True), "@c": (2**31 - 1, False)})


class InheritTest(unittest.TestCase):
    """Container-ACLs erweitern oder schränken die Account-ACL ein."""

    def effective(self, nodes: dict) -> dict:
        return {path: rights(state) for path, state in resolve(nodes).items()}

    def test_extend_and_restrict(self):
        eff = self.effective({"/fs/a": "@o|@b=3,@c=1", "/fs/a/c": "@o|@b=1,@d=3"})
        self.assertEqual(eff["/fs/a/c"], {
            "@o": (19, True, "/fs/a/c"),     # eigener Besitzer im Container
            "@b": (1, False, "/fs/a/c"),    # eingeschränkt
            "@c": (1, False, "/fs/a"),      # geerbt
            "@d": (3, False, "/fs/a/c"),    # erweitert
        })

    def test_container_owner_replaces(self):
        # @o hat im Container nur seine Einträge, das Besitzer-Recht nicht
        eff = self.effective({"/fs/a": "@o|@o=1,@b=1", "/fs/a/c": "@x|"})
        self.assertEqual(eff["/fs/a/c"], {"@o": (1, False, "/fs/a"), "@b": (1, False, "/fs/a"),
                                          "@x": (19, True, "/fs/a/c")})
        eff = self.effective({"/fs/a": "@o|", "/fs/a/c": f"{OPS}|"})
        self.assertEqual(eff["/fs/a/c"], {})

    def test_empty_owner_inherits(self):
        eff = self.effective({"/fs/a": "@o|", "/fs/a/c": "|@o=16,@b=1"})
        self.assertEqual(eff["/fs/a/c"], {"@o": (19, True, "/fs/a/c"), "@b": (1, False, "/fs/a/c")})
        self.assertEqual(eff["/fs/a"], {"@o": (19, True, "/fs/a")})

    def test_three_levels(self):
        eff = self.effective({"/fs/a": "@o|@b=3", "/fs/a/c": None, "/fs/a/c/s": "|@b=1",
                              "/fs/x": None, "/fs/x/c": "|@y=1"})
        self.assertEqual(eff["/fs/a/c"], {"@o": (19, True, "/fs/a"), "@b": (3, False, "/fs/a")})
        self.assertEqual(eff["/fs/a/c/s"], {"@o": (19, True, "/fs/a"), "@b": (1, False, "/fs/a/c/s")})
        self.assertEqual(eff["/fs/x"], {})
        self.assertEqual(eff["/fs/x/c"], {"@y": (1, False, "/fs/x/c")})

    def test_inheriting_paths_share_state(self):
        states = resolve({"/fs/a": "@o|", "/fs/a/c1": None, "/fs/a/c2": None})
        self.assertIs(states["/fs/a/c1"], states["/fs/a"])
        self.assertIs(states["/fs/a/c2"], states["/fs/a"])


class AclProblemsTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(acl_problems(f"@a|@b=1,{OPS}=3,user=2,cn=doc,ou=hell,dc=firma=3"), [])
        self.assertEqual(acl_problems("@a|"), [])
        self.assertEqual(acl_problems(f"@a|@b={2**31 - 1}"), [])
        # leerer Besitzer erbt, das prüft store.save() im Baum
        self.assertEqual(acl_problems("|@b=1"), [])

    def test_missing_separator(self):
        self.assertIn("kein '|' zwischen Besitzer und Einträgen", acl_problems("@a"))

    def test_invalid_entry(self):
        self.assertEqual(acl_problems("@a|@b"), ["ungültiger Eintrag '@b'"])
        self.assertEqual(acl_problems("@a|.cn=ops,ou=x"), ["ungültiger Eintrag '.cn=ops,ou=x'"])

    def test_perm_out_of_range(self):
        self.assertEqual(len(acl_problems("@a|@b=0")), 1)
        self.assertEqual(len(acl_problems(f"@a|@b={2**31}")), 1)


class HelpersTest(unittest.TestCase):
    def test_covers(self):
        self.assertTrue(covers(3, 1))
        self.assertTrue(covers(19, 16))
        self.assertFalse(covers(1, 3))
        self.assertFalse(covers(3, 16))

    def test_resolve_acl_from(self):
        nodes = {
            "/fs/a/c1": None,  # Reihenfolge egal, sortiert wird nach Tiefe
            "/fs/a": "@x|",
            "/fs/a/c2": "@y|",
            "/fs/b": None,
            "/fs/b/c": None,
        }
        self.assertEqual(resolve_acl_from(nodes), {
            "/fs/a": "/fs/a", "/fs/a/c1": "/fs/a", "/fs/a/c2": "/fs/a/c2",
            "/fs/b": None, "/fs/b/c": None,
        })

    def test_client_id(self):
        self.assertEqual(client_id("client1"), "@client1")
        self.assertEqual(client_id(" @client1 "), "@client1")
        self.assertEqual(client_id("client*", allow_glob=True), "@client*")
        for bad in ("@a,b", "@a|b", "@a=1", "#ops", "@", "@a b", "@client*"):
            with self.subTest(value=bad), self.assertRaises(UsageError):
                client_id(bad)


if __name__ == "__main__":
    unittest.main()
