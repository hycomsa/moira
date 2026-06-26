"""End-to-end HTTP auth enforcement (must-fix #2) — real server, real tokens."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moira_api  # noqa: E402
from moira_core import authn  # noqa: E402


class TestApiAuthEnforcement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("MOIRA_AUTH_MODE", "MOIRA_AUTH_SECRET", "MOIRA_DB")}
        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); db.close()
        os.environ["MOIRA_AUTH_MODE"] = "local"
        os.environ["MOIRA_AUTH_SECRET"] = "test-secret-key"
        os.environ["MOIRA_DB"] = db.name
        moira_api.DB = db.name
        authn._LOCAL_SECRET = None  # re-read the secret we just set
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), moira_api.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        authn._LOCAL_SECRET = None

    def _req(self, method, path, token=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body)
        c.request(method, path, body=payload, headers=headers)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def _tok(self, *roles):
        return authn.mint_local_token("user", list(roles))

    def test_ready_is_public(self):
        self.assertEqual(self._req("GET", "/api/ready"), 200)

    def test_protected_read_requires_token(self):
        self.assertEqual(self._req("GET", "/api/runs"), 401)

    def test_viewer_can_read_but_not_launch(self):
        self.assertEqual(self._req("GET", "/api/runs", token=self._tok("viewer")), 200)
        self.assertEqual(self._req("POST", "/api/runs", token=self._tok("viewer"), body={}), 403)

    def test_developer_can_launch(self):
        # 403 would mean forbidden; anything else means the auth layer let it through
        self.assertNotEqual(self._req("POST", "/api/runs", token=self._tok("developer"), body={}), 403)
        self.assertNotEqual(self._req("POST", "/api/runs", token=self._tok("developer"), body={}), 401)

    def test_sensitive_read_forbidden_for_viewer(self):
        self.assertEqual(self._req("GET", "/api/logs", token=self._tok("viewer")), 403)
        self.assertEqual(self._req("GET", "/api/logs", token=self._tok("developer")), 200)

    def test_bad_token_is_401(self):
        self.assertEqual(self._req("GET", "/api/runs", token="garbage.token.here"), 401)

    def _seed_waiting_gate_run(self, persona):
        from moira_core import Pipeline, Node, NodeType, GateConfig, GateMode
        s = moira_api.open_store()
        pipe = Pipeline(id="p", name="P", nodes=[
            Node(id="g", name="Gate", type=NodeType.GATE,
                 gate=GateConfig(mode=GateMode.HUMAN, persona=persona))])
        rid = f"run-gate-{persona}"
        s.create_run(rid, "p", pipe.to_dict(), "owner", "waiting_gate")
        s.save_run_state(rid, {"g": "waiting_gate"})
        s.close()
        return rid

    def test_gate_approval_enforces_persona_and_ignores_body_spoof(self):
        rid = self._seed_waiting_gate_run("compliance")
        # a Developer must NOT be able to approve a compliance gate (separation of duties),
        # and a forged body `by` is irrelevant — identity comes from the token.
        self.assertEqual(
            self._req("POST", f"/api/runs/{rid}/approve",
                      token=self._tok("developer"), body={"by": "compliance-officer"}), 403)
        # a Compliance Officer can
        self.assertNotEqual(
            self._req("POST", f"/api/runs/{rid}/approve", token=self._tok("compliance"), body={}), 403)


if __name__ == "__main__":
    unittest.main()
