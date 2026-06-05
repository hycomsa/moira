import unittest

from moira_core.evals import (
    build_eval_prompt, normalize_scorecard, verdict_for, default_criteria,
    QUALITY_CRITERIA, CONFORMANCE_CRITERIA, COMPLIANCE_CRITERIA,
)


class TestEvalPrompt(unittest.TestCase):
    def test_quality_prompt_mentions_criteria_and_artifact(self):
        p = build_eval_prompt("quality", "some artifact text")
        self.assertIn("ARTIFACT UNDER REVIEW", p)
        self.assertIn("some artifact text", p)
        for c in QUALITY_CRITERIA:
            self.assertIn(c, p)
        self.assertIn('"criteria"', p)
        self.assertIn('"overall"', p)

    def test_conformance_prompt_targets_code_and_spec(self):
        p = build_eval_prompt("conformance", "FUNC-X spec body")
        self.assertIn("FUNCTIONAL SPEC", p)
        self.assertIn("working directory", p)
        self.assertIn("FUNC-X spec body", p)
        for c in CONFORMANCE_CRITERIA:
            self.assertIn(c, p)

    def test_custom_criteria_override_defaults(self):
        p = build_eval_prompt("quality", "x", criteria=["security", "a11y"])
        self.assertIn("security", p)
        self.assertIn("a11y", p)

    def test_default_criteria(self):
        self.assertEqual(default_criteria("conformance"), CONFORMANCE_CRITERIA)
        self.assertEqual(default_criteria("quality"), QUALITY_CRITERIA)
        self.assertEqual(default_criteria("compliance"), COMPLIANCE_CRITERIA)
        self.assertEqual(default_criteria("anything-else"), QUALITY_CRITERIA)

    def test_compliance_prompt_has_regulation_and_findings(self):
        p = build_eval_prompt("compliance", "REG-GDPR checklist body…")
        self.assertIn("REGULACJA", p)
        self.assertIn("REG-GDPR checklist body", p)
        self.assertIn('"findings"', p)
        self.assertIn("BLOCKER", p)
        self.assertIn("regulation", p)  # finding must map to a regulation


class TestComplianceFindings(unittest.TestCase):
    def test_findings_normalized_and_severity_coerced(self):
        out = {"criteria": [{"name": "pokrycie", "score": 0.9}],
               "findings": [
                   {"severity": "high", "title": "brak DPA", "regulation": "RODO art. 28",
                    "location": "svc/email.ts:12", "recommendation": "podpisz DPA"},
                   {"severity": "weird", "title": "x"}],
               "overall": 0.9, "summary": "ok"}
        sc = normalize_scorecard(out, "compliance")
        self.assertEqual(len(sc["findings"]), 2)
        self.assertEqual(sc["findings"][0]["severity"], "HIGH")
        self.assertEqual(sc["findings"][1]["severity"], "INFO")  # unknown → INFO
        # a HIGH finding caps overall at 0.5 even though model said 0.9
        self.assertEqual(sc["overall"], 0.5)

    def test_blocker_caps_overall_hard(self):
        out = {"overall": 0.95, "findings": [{"severity": "BLOCKER", "title": "plaintext hasło"}]}
        sc = normalize_scorecard(out, "compliance")
        self.assertEqual(sc["overall"], 0.2)
        self.assertTrue(sc["parsed"])

    def test_quality_scorecard_has_empty_findings(self):
        sc = normalize_scorecard({"overall": 0.8}, "quality")
        self.assertEqual(sc["findings"], [])


class TestVerdict(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(verdict_for(0.95), "pass")
        self.assertEqual(verdict_for(0.8), "pass")
        self.assertEqual(verdict_for(0.6), "warn")
        self.assertEqual(verdict_for(0.49), "fail")


class TestNormalizeScorecard(unittest.TestCase):
    def test_full_scorecard_passthrough(self):
        out = {"criteria": [{"name": "clarity", "score": 0.9, "verdict": "pass", "note": "ok"}],
               "overall": 0.85, "missing": ["edge case Z"], "summary": "solid"}
        sc = normalize_scorecard(out, "quality")
        self.assertTrue(sc["parsed"])
        self.assertEqual(sc["overall"], 0.85)
        self.assertEqual(sc["criteria"][0]["verdict"], "pass")
        self.assertEqual(sc["missing"], ["edge case Z"])
        self.assertEqual(sc["kind"], "quality")

    def test_clamps_scores_and_coerces_strings(self):
        out = {"criteria": [{"name": "x", "score": "1.7"}, {"name": "y", "score": -3}]}
        sc = normalize_scorecard(out)
        self.assertEqual(sc["criteria"][0]["score"], 1.0)
        self.assertEqual(sc["criteria"][1]["score"], 0.0)

    def test_derives_verdict_and_overall_when_missing(self):
        out = {"criteria": [{"name": "a", "score": 0.9}, {"name": "b", "score": 0.3}]}
        sc = normalize_scorecard(out)
        self.assertEqual(sc["criteria"][0]["verdict"], "pass")
        self.assertEqual(sc["criteria"][1]["verdict"], "fail")
        self.assertAlmostEqual(sc["overall"], 0.6, places=2)  # mean of 0.9, 0.3
        self.assertTrue(sc["parsed"])

    def test_non_scorecard_output_marked_unparsed(self):
        sc = normalize_scorecard({"result": "the model rambled"}, "quality")
        self.assertFalse(sc["parsed"])
        self.assertEqual(sc["criteria"], [])
        self.assertIn("rambled", sc["summary"])

    def test_non_dict_output(self):
        sc = normalize_scorecard("just a string")
        self.assertFalse(sc["parsed"])
        self.assertEqual(sc["overall"], 0.0)

    def test_explicit_overall_without_criteria_is_parsed(self):
        sc = normalize_scorecard({"overall": 0.7})
        self.assertTrue(sc["parsed"])
        self.assertEqual(sc["overall"], 0.7)


if __name__ == "__main__":
    unittest.main()
