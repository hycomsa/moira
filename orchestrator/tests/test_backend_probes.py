"""QW4 — backend install/login probes + launch gating.

Before this change `/api/health` reported only `shutil.which(binary)` and a
launch happily enqueued work for a missing or logged-out CLI — the failure
surfaced minutes later as a failed node after retries. These tests pin:

- `probes.probe("claude_code")`: install check, `--version` banner parse,
  `auth status` JSON parse (loggedIn/email), copy-paste fix hints
- unknown probe outcomes (auth command fails) never claim a state
- an asymmetric-TTL cache: healthy results live long, bad results re-probe fast
- `launch_blockers()`: blocks definitely-unusable backends only; unknown or
  unprobeable backends (mock) never block
- HTTP: POST /api/runs against a not-installed backend fails fast with 503
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.backends import probes  # noqa: E402


class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _runner_map(version="2.1.233 (Claude Code)\n",
                auth='{"loggedIn": true, "email": "dev@hycom.pl"}\n',
                calls=None):
    """Fake subprocess.run keyed by the sub-command."""
    def run(cmd, **kw):
        if calls is not None:
            calls.append(list(cmd))
        if "--version" in cmd:
            return _FakeProc(version)
        if "auth" in cmd:
            if isinstance(auth, Exception):
                raise auth
            return _FakeProc(auth)
        return _FakeProc("", 1)
    return run


class TestClaudeProbe(unittest.TestCase):
    def setUp(self):
        probes._cache.clear()

    def test_not_installed(self):
        r = probes.probe("claude_code", which=lambda b: None, force=True)
        self.assertFalse(r.installed)
        self.assertIsNone(r.authenticated)
        self.assertIn("npm install", r.hint)

    def test_installed_and_logged_in(self):
        r = probes.probe("claude_code", which=lambda b: "/usr/bin/claude",
                         runner=_runner_map(), force=True)
        self.assertTrue(r.installed)
        self.assertEqual(r.version, "2.1.233")
        self.assertIs(r.authenticated, True)
        self.assertIn("dev@hycom.pl", r.detail)

    def test_logged_out_gets_login_hint(self):
        r = probes.probe("claude_code", which=lambda b: "/usr/bin/claude",
                         runner=_runner_map(auth='{"loggedIn": false}'), force=True)
        self.assertTrue(r.installed)
        self.assertIs(r.authenticated, False)
        self.assertIn("claude auth login", r.hint)

    def test_auth_probe_failure_is_unknown_not_false(self):
        r = probes.probe("claude_code", which=lambda b: "/usr/bin/claude",
                         runner=_runner_map(auth=OSError("boom")), force=True)
        self.assertTrue(r.installed)
        self.assertIsNone(r.authenticated)

    def test_cache_healthy_long_bad_short(self):
        calls: list = []
        t = {"now": 1000.0}
        kw = dict(which=lambda b: "/usr/bin/claude", now=lambda: t["now"])
        probes.probe("claude_code", runner=_runner_map(calls=calls), **kw)
        n_healthy = len(calls)
        t["now"] += probes.TTL_OK - 1          # still cached
        probes.probe("claude_code", runner=_runner_map(calls=calls), **kw)
        self.assertEqual(len(calls), n_healthy)
        t["now"] += 2                          # healthy TTL expired -> re-probe
        probes.probe("claude_code", runner=_runner_map(calls=calls), **kw)
        self.assertGreater(len(calls), n_healthy)

        probes._cache.clear()
        calls.clear()
        bad = _runner_map(auth='{"loggedIn": false}', calls=calls)
        probes.probe("claude_code", runner=bad, **kw)
        n_bad = len(calls)
        t["now"] += probes.TTL_BAD - 1         # bad result still cached
        probes.probe("claude_code", runner=bad, **kw)
        self.assertEqual(len(calls), n_bad)
        t["now"] += 2                          # bad TTL expired -> re-probe fast
        probes.probe("claude_code", runner=bad, **kw)
        self.assertGreater(len(calls), n_bad)

    def test_litellm_probe_reports_import_only(self):
        r = probes.probe("litellm", force=True)
        self.assertIsInstance(r.installed, bool)
        self.assertIsNone(r.authenticated)  # per-provider keys: never claim login state

    def test_mock_and_unknown_backends_are_benign(self):
        self.assertTrue(probes.probe("mock", force=True).installed)
        self.assertTrue(probes.probe("someday_backend", force=True).installed)


class TestLaunchBlockers(unittest.TestCase):
    def setUp(self):
        probes._cache.clear()

    def _seed(self, name, **kw):
        probes._cache[name] = probes.ProbeResult(backend=name, ts=9e18, **kw)

    def test_not_installed_blocks_with_hint(self):
        self._seed("claude_code", installed=False, hint="npm install -g x")
        blockers = probes.launch_blockers({"claude_code"})
        self.assertEqual(len(blockers), 1)
        self.assertIn("not installed", blockers[0])
        self.assertIn("npm install", blockers[0])

    def test_logged_out_blocks(self):
        self._seed("claude_code", installed=True, authenticated=False,
                   hint="claude auth login")
        blockers = probes.launch_blockers({"claude_code"})
        self.assertEqual(len(blockers), 1)
        self.assertIn("claude auth login", blockers[0])

    def test_unknown_auth_and_mock_never_block(self):
        self._seed("claude_code", installed=True, authenticated=None)
        self.assertEqual(probes.launch_blockers({"claude_code", "mock"}), [])

    def test_healthy_backend_does_not_block(self):
        self._seed("claude_code", installed=True, authenticated=True)
        self.assertEqual(probes.launch_blockers({"claude_code"}), [])


class TestLaunchGateHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._prev = {k: os.environ.get(k) for k in ("MOIRA_AUTH_MODE", "MOIRA_DB")}
        db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); db.close()
        os.environ["MOIRA_AUTH_MODE"] = "off"
        os.environ["MOIRA_DB"] = db.name
        import moira_api
        moira_api.DB = db.name
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

    def _post_run(self, backend):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/runs", body=json.dumps({"backend": backend}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def test_launch_on_unusable_backend_fails_fast_with_hint(self):
        probes._cache["claude_code"] = probes.ProbeResult(
            backend="claude_code", installed=False,
            hint="npm install -g @anthropic-ai/claude-code", ts=9e18)
        status, data = self._post_run("claude_code")
        self.assertEqual(status, 503)
        self.assertIn("blockers", data)
        self.assertIn("npm install", data["blockers"][0])
        probes._cache.clear()

    def test_launch_on_mock_still_works(self):
        status, data = self._post_run("mock")
        self.assertEqual(status, 201, msg=str(data))

    def test_health_reports_probes(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/api/health")
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("probes", data)
        self.assertIn("claude_code", data["probes"])
        for key in ("installed", "authenticated", "hint", "version"):
            self.assertIn(key, data["probes"]["claude_code"])


if __name__ == "__main__":
    unittest.main()
