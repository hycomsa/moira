"""Identity / JWT (must-fix #2): local self-issued HS256 + claim->principal mapping."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import authn  # noqa: E402


class TestLocalJWT(unittest.TestCase):
    def test_mint_verify_roundtrip(self):
        tok = authn.mint_local_token("alice", ["admin"], secret="s3cret", ttl_seconds=60)
        claims = authn.verify_local(tok, "s3cret")
        self.assertEqual(claims["sub"], "alice")
        self.assertEqual(claims["roles"], ["admin"])

    def test_tampered_payload_fails(self):
        tok = authn.mint_local_token("alice", ["viewer"], secret="s3cret")
        h, p, s = tok.split(".")
        forged = authn.mint_local_token("alice", ["admin"], secret="other").split(".")[1]
        with self.assertRaises(ValueError):
            authn.verify_local(f"{h}.{forged}.{s}", "s3cret")

    def test_wrong_secret_fails(self):
        tok = authn.mint_local_token("alice", ["admin"], secret="s3cret")
        with self.assertRaises(ValueError):
            authn.verify_local(tok, "different")

    def test_expired_fails(self):
        tok = authn.mint_local_token("alice", ["admin"], secret="s3cret", ttl_seconds=-1)
        with self.assertRaises(ValueError):
            authn.verify_local(tok, "s3cret")


class TestClaimsMapping(unittest.TestCase):
    def test_claims_to_principal_uses_roles(self):
        p = authn.claims_to_principal({"sub": "bob", "name": "Bob", "roles": ["developer"]},
                                      auth_source="local")
        self.assertEqual(p.subject, "bob")
        self.assertEqual(p.roles, ["developer"])
        self.assertEqual(p.auth_source, "local")

    def test_oidc_groups_map_to_roles(self):
        gmap = {"moira-compliance": "compliance", "moira-devs": "developer"}
        p = authn.claims_to_principal(
            {"sub": "carol", "groups": ["moira-compliance", "other"]},
            auth_source="oidc", group_role_map=gmap)
        self.assertEqual(p.roles, ["compliance"])

    def test_map_groups_to_roles_dedup_sorted(self):
        gmap = {"g1": "developer", "g2": "developer", "g3": "viewer"}
        self.assertEqual(authn.map_groups_to_roles(["g1", "g2", "g3"], gmap),
                         ["developer", "viewer"])


if __name__ == "__main__":
    unittest.main()
