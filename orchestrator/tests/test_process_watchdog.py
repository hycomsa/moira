"""QW7 — escalating, VERIFIED process termination (SIGTERM → SIGKILL → unblock).

The old watchdog did a bare `proc.kill()` on the direct child: no graceful
SIGTERM, no verification the process actually exited, and — the real scar,
straight from Cezar's postmortems — no process-group kill, so a grandchild
that inherited the stdout pipe (e.g. a tool spawned by the CLI) kept it open
and the stream reader hung forever after the child died. These tests pin:

- a timeout terminates the WHOLE process group (grandchildren included) and
  the backend returns within the escalation window — never hangs on the pipe
- a TERM-ignoring process is escalated to SIGKILL and still dies
- a well-behaved process dies on SIGTERM (no gratuitous SIGKILL)
- cancel() kills the whole group too and returns promptly
"""
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moira_core.backends.claude_code as cc  # noqa: E402
from moira_core.models import Node, NodeType  # noqa: E402


def _script(body: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
    f.write("#!/bin/bash\n" + body + "\n")
    f.close()
    os.chmod(f.name, os.stat(f.name).st_mode | stat.S_IEXEC)
    return f.name


def _node(timeout: int = 1) -> Node:
    return Node(id="work", name="W", type=NodeType.PRODUCER,
                role="code-generator", backend="claude_code", timeout=timeout)


class TestWatchdog(unittest.TestCase):
    def setUp(self):
        self._patches = [mock.patch.object(cc, "TERM_GRACE", 1.0),
                         mock.patch.object(cc, "EOF_GRACE", 0.5)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_grandchild_holding_the_pipe_does_not_hang_the_reader(self):
        # the Cezar scar: a background grandchild inherits stdout; killing only
        # the direct child leaves the pipe open and the reader blocked forever
        script = _script("sleep 60 &\nsleep 60")
        be = cc.ClaudeCodeBackend(binary=script)
        t0 = time.time()
        res = be.run(_node(timeout=1), {"spec_text": "x"})
        elapsed = time.time() - t0
        self.assertFalse(res.ok)
        self.assertIn("timed out", res.error)
        self.assertLess(elapsed, 10.0, msg=f"reader hung for {elapsed:.1f}s")

    def test_term_ignoring_process_is_escalated_to_sigkill(self):
        # the loop survives a group SIGTERM: bash ignores it and respawns sleeps
        script = _script("trap '' TERM\nwhile true; do sleep 0.2; done")
        be = cc.ClaudeCodeBackend(binary=script)
        t0 = time.time()
        res = be.run(_node(timeout=1), {"spec_text": "x"})
        elapsed = time.time() - t0
        self.assertFalse(res.ok)
        self.assertIn("timed out", res.error)
        self.assertIn("SIGKILL", res.error)      # escalation is visible in the trail
        self.assertLess(elapsed, 10.0)

    def test_well_behaved_process_dies_on_sigterm_alone(self):
        script = _script("sleep 60")
        be = cc.ClaudeCodeBackend(binary=script)
        res = be.run(_node(timeout=1), {"spec_text": "x"})
        self.assertFalse(res.ok)
        self.assertIn("timed out", res.error)
        self.assertNotIn("SIGKILL", res.error)   # graceful exit was enough

    def test_cancel_kills_the_whole_group_promptly(self):
        import threading
        script = _script("sleep 60 &\nsleep 60")
        be = cc.ClaudeCodeBackend(binary=script)
        out: dict = {}

        def _run():
            out["res"] = be.run(_node(timeout=60), {"spec_text": "x"})
        t = threading.Thread(target=_run)
        t.start()
        time.sleep(0.6)                          # let the subprocess start
        t0 = time.time()
        be.cancel()
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), msg="run() still blocked after cancel()")
        self.assertLess(time.time() - t0, 10.0)
        self.assertFalse(out["res"].ok)
        self.assertIn("cancel", out["res"].error)


if __name__ == "__main__":
    unittest.main()
