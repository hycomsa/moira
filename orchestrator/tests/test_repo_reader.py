"""repo_reader artifact resolution for the lineage view (against the CSL repo)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core.repo_reader import AISdlcRepo  # noqa: E402

# Opt-in integration fixture: set MOIRA_CSL_REPO to a real AI SDLC repo to run
# these resolution tests; otherwise they skip (no machine paths in the tree).
CSL = os.environ.get("MOIRA_CSL_REPO", "")


@unittest.skipUnless(CSL and os.path.isdir(os.path.join(CSL, ".ai", "context")),
                     "set MOIRA_CSL_REPO to a real AI SDLC repo to run these")
class TestArtifactResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = AISdlcRepo(CSL)

    def test_read_requirement_section(self):
        txt = self.repo.read_requirement("REQ-APP-03")
        self.assertIsNotNone(txt)
        self.assertIn("REQ-APP-03", txt)
        self.assertIn("onboarding", txt.lower())
        # must stop at the next requirement (not bleed into REQ-APP-04)
        self.assertNotIn("REQ-APP-04", txt)

    def test_resolve_artifact_dispatch(self):
        req = self.repo.resolve_artifact("REQ-APP-03")
        self.assertEqual(req["type"], "REQ")
        self.assertIn("onboarding", req["title"].lower())

        intent = self.repo.resolve_artifact("INT-APP-driver-mobile-app")
        self.assertEqual(intent["type"], "INT")
        self.assertTrue(intent["text"])

        func = self.repo.resolve_artifact("FUNC-APP-onboarding")
        self.assertEqual(func["type"], "FUNC")

    def test_unknown_artifact_is_none(self):
        self.assertIsNone(self.repo.resolve_artifact("REQ-APP-999"))
        self.assertIsNone(self.repo.resolve_artifact("XYZ-1"))


if __name__ == "__main__":
    unittest.main()
