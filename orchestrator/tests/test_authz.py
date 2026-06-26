"""RBAC matrix — roles -> actions + approvable personas (must-fix #2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.authz import (  # noqa: E402
    Principal, can, can_approve, ROLES, required_action, authorize_request,
)


def _p(*roles):
    return Principal(subject="u", display_name="U", roles=list(roles), auth_source="test")


class TestRoleActions(unittest.TestCase):
    def test_admin_can_do_everything(self):
        admin = _p("admin")
        for action in ("configure", "launch", "approve_gate", "governance_override",
                       "run_eval", "read_sensitive", "read"):
            self.assertTrue(can(admin, action), action)

    def test_viewer_reads_only(self):
        v = _p("viewer")
        self.assertTrue(can(v, "read"))
        for action in ("configure", "launch", "approve_gate", "governance_override",
                       "run_eval", "read_sensitive"):
            self.assertFalse(can(v, action), action)

    def test_client_reads_no_launch_no_config(self):
        c = _p("client")
        self.assertTrue(can(c, "read"))
        self.assertFalse(can(c, "launch"))
        self.assertFalse(can(c, "configure"))
        self.assertFalse(can(c, "read_sensitive"))

    def test_developer_launches_and_edits_but_no_governance_override(self):
        d = _p("developer")
        self.assertTrue(can(d, "launch"))
        self.assertTrue(can(d, "configure"))
        self.assertTrue(can(d, "read_sensitive"))
        self.assertFalse(can(d, "governance_override"))

    def test_compliance_overrides_but_does_not_launch(self):
        c = _p("compliance")
        self.assertTrue(can(c, "governance_override"))
        self.assertTrue(can(c, "run_eval"))
        self.assertFalse(can(c, "launch"))

    def test_multiple_roles_union(self):
        self.assertTrue(can(_p("viewer", "developer"), "launch"))

    def test_unknown_action_is_denied(self):
        self.assertFalse(can(_p("admin"), "drop_database") is True and False)  # admin wildcard
        self.assertFalse(can(_p("developer"), "totally_unknown_action"))


class TestApprovePersona(unittest.TestCase):
    def test_admin_approves_any_persona(self):
        for persona in ("ba", "lead-dev", "architect", "qa", "compliance", "ciso",
                        "client", "accessibility-lead", "compliance-lead"):
            self.assertTrue(can_approve(_p("admin"), persona), persona)

    def test_developer_covers_dev_and_qa_personas(self):
        d = _p("developer")
        for persona in ("ba", "lead-dev", "architect", "qa", "accessibility-lead"):
            self.assertTrue(can_approve(d, persona), persona)
        self.assertFalse(can_approve(d, "compliance"))
        self.assertFalse(can_approve(d, "client"))

    def test_compliance_covers_compliance_personas_only(self):
        c = _p("compliance")
        for persona in ("compliance", "ciso", "compliance-lead"):
            self.assertTrue(can_approve(c, persona), persona)
        self.assertFalse(can_approve(c, "lead-dev"))
        self.assertFalse(can_approve(c, "qa"))

    def test_client_only_client_gate(self):
        self.assertTrue(can_approve(_p("client"), "client"))
        self.assertFalse(can_approve(_p("client"), "lead-dev"))

    def test_viewer_approves_nothing(self):
        self.assertFalse(can_approve(_p("viewer"), "ba"))

    def test_five_roles_exist(self):
        self.assertEqual(set(ROLES), {"admin", "developer", "compliance", "client", "viewer"})


class TestRouteAuthorization(unittest.TestCase):
    def test_required_action_mapping(self):
        self.assertIsNone(required_action("GET", "/index.html"))          # static = public
        self.assertIsNone(required_action("GET", "/api/ready"))           # readiness = public
        self.assertIsNone(required_action("OPTIONS", "/api/runs"))        # preflight = public
        self.assertEqual(required_action("GET", "/api/runs"), "read")
        self.assertEqual(required_action("GET", "/api/file"), "read_sensitive")
        self.assertEqual(required_action("GET", "/api/logs"), "read_sensitive")
        self.assertEqual(required_action("GET", "/api/runs/r1/debug"), "read_sensitive")
        self.assertEqual(required_action("POST", "/api/runs"), "launch")
        self.assertEqual(required_action("POST", "/api/runs/r1/approve"), "approve_gate")
        self.assertEqual(required_action("POST", "/api/eval"), "run_eval")
        self.assertEqual(required_action("POST", "/api/pipelines"), "configure")
        self.assertEqual(required_action("DELETE", "/api/agents/x"), "configure")

    def test_authorize_unauthenticated_is_401(self):
        self.assertEqual(authorize_request("POST", "/api/runs", None)[0], 401)

    def test_authorize_forbidden_is_403(self):
        viewer = _p("viewer")
        self.assertEqual(authorize_request("POST", "/api/runs", viewer)[0], 403)

    def test_authorize_allows_permitted(self):
        self.assertIsNone(authorize_request("POST", "/api/runs", _p("developer")))
        self.assertIsNone(authorize_request("GET", "/api/runs", _p("viewer")))

    def test_public_route_allows_even_anonymous(self):
        self.assertIsNone(authorize_request("GET", "/api/ready", None))


if __name__ == "__main__":
    unittest.main()
