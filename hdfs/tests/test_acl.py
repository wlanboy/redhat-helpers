"""Tests für hdfs.acl: Format, effektive Rechte, Vererbung, clientid-Prüfung."""

import unittest

from hdfs.acl import (
    Acl,
    acl_problems,
    client_id,
    covers,
    is_ldap,
    principal_kind,
    resolve_acl_from,
    split_token,
)
from hdfs.common import UsageError


class SplitTokenTest(unittest.TestCase):
    def test_entry(self):
        self.assertEqual(split_token("@client1=3"), ("@client1", 3))

    def test_last_equals_wins(self):
        self.assertEqual(split_token("dc=firma=3"), ("dc=firma", 3))

    def test_invalid(self):
        for token in ("@client1", "=3", "@client1=x", "@client1=", "cn=doc"):
            with self.subTest(token=token):
                self.assertEqual(split_token(token), (token, None))


class LdapTest(unittest.TestCase):
    def test_ldap_parts(self):
        for token in ("cn=doc", "ou=hell", "dc=firma=3", "CN=Doc", ".ou=x", "uid=meier", "sn=meier"):
            with self.subTest(token=token):
                self.assertTrue(is_ldap(token))

    def test_not_ldap(self):
        # "cn=3" ist ein User "cn" mit Recht 3, kein DN-Teil
        for token in ("cn=3", "@client1=3", "svc_backup=1", "#ops=3", "cnx=doc"):
            with self.subTest(token=token):
                self.assertFalse(is_ldap(token))

    def test_principal_kind(self):
        self.assertEqual(principal_kind("@client1"), "client")
        self.assertEqual(principal_kind("#ops"), "group")
        self.assertEqual(principal_kind("svc_backup"), "user")
        self.assertEqual(principal_kind("cn=doc"), "ldap")


class AclTest(unittest.TestCase):
    def test_parse_render_roundtrip(self):
        raw = "@client1|@client2=3,svc_backup=1,cn=doc,ou=hell,dc=firma=3"
        acl = Acl.parse(raw)
        self.assertEqual(acl.owner, "@client1")
        self.assertEqual(acl.tokens, ["@client2=3", "svc_backup=1", "cn=doc", "ou=hell", "dc=firma=3"])
        self.assertEqual(acl.render(), raw)

    def test_parse_strips_and_skips_empty(self):
        acl = Acl.parse(" @a | @b=1 ,, @c=3 ")
        self.assertEqual((acl.owner, acl.tokens), ("@a", ["@b=1", "@c=3"]))

    def test_owner_only(self):
        self.assertEqual(Acl.parse("@a|").tokens, [])
        self.assertEqual(Acl.parse("@a|").render(), "@a|")

    def test_readme_example(self):
        rights = Acl.parse("@client1|@client2=3,svc_backup=1,@client3=1,#ops=3").client_rights()
        self.assertEqual(rights, {"@client1": (3, True), "@client2": (3, False), "@client3": (1, False)})

    def test_duplicate_entries_are_ored(self):
        self.assertEqual(Acl.parse("@o|@a=5,@a=16").client_rights()["@a"], (21, False))

    def test_owner_gets_at_least_owner_perm(self):
        self.assertEqual(Acl.parse("@o|@o=1").client_rights()["@o"], (3, True))
        self.assertEqual(Acl.parse("@o|@o=8").client_rights()["@o"], (11, True))

    def test_non_client_owner_has_no_rights(self):
        self.assertEqual(Acl.parse("#ops|@a=1").client_rights(), {"@a": (1, False)})

    def test_invalid_parts_give_no_rights(self):
        # wie acl_problems: ungültige Einträge und Besitzer zählen nicht
        self.assertEqual(Acl.parse("@a=3").client_rights(), {})
        self.assertEqual(Acl.parse("|@a=3").client_rights(), {"@a": (3, False)})
        self.assertEqual(Acl.parse(f"@o|@a=0,@b={2**31},@c={2**31 - 1},@d=x,@e,@f = 1").client_rights(),
                         {"@o": (3, True), "@c": (2**31 - 1, False)})


class AclProblemsTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(acl_problems("@a|@b=1,#g=3,user=2,cn=doc,ou=hell,dc=firma=3"), [])
        self.assertEqual(acl_problems("@a|"), [])
        self.assertEqual(acl_problems(f"@a|@b={2**31 - 1}"), [])

    def test_missing_separator(self):
        self.assertIn("kein '|' zwischen Besitzer und Einträgen", acl_problems("@a"))

    def test_missing_owner(self):
        self.assertEqual(acl_problems("|@b=1"), ["kein Besitzer"])

    def test_invalid_entry(self):
        self.assertEqual(acl_problems("@a|@b"), ["ungültiger Eintrag '@b'"])

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
