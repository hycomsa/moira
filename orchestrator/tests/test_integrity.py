"""Tamper-evidence: audit hash-chain seal + verify, and a store round-trip."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import Store  # noqa: E402
from moira_core.integrity import seal, verify_chain, verify_export, GENESIS  # noqa: E402
from moira_core.models import AuditRecord  # noqa: E402


class TestSealVerify(unittest.TestCase):
    def _chain(self, n):
        recs, prev = [], GENESIS
        for i in range(n):
            r = seal({"step_id": f"s{i}", "node_id": f"n{i}", "owner": "o", "status": "succeeded"}, prev)
            recs.append(r); prev = r["hash"]
        return recs

    def test_valid_chain_verifies(self):
        v = verify_chain(self._chain(5))
        self.assertTrue(v["ok"]); self.assertEqual(v["length"], 5); self.assertIsNone(v["broken_at"])

    def test_mutated_content_breaks(self):
        recs = self._chain(5)
        recs[2]["status"] = "failed"  # silent edit
        v = verify_chain(recs)
        self.assertFalse(v["ok"]); self.assertEqual(v["broken_at"], 2)

    def test_dropped_record_breaks(self):
        recs = self._chain(5)
        del recs[2]  # remove a link
        self.assertFalse(verify_chain(recs)["ok"])

    def test_reorder_breaks(self):
        recs = self._chain(5)
        recs[1], recs[2] = recs[2], recs[1]
        self.assertFalse(verify_chain(recs)["ok"])

    def test_empty_chain_ok(self):
        self.assertTrue(verify_chain([])["ok"])


class TestVerifyExport(unittest.TestCase):
    """verify_export reconstructs chain order from prev_hash links (the git mirror
    stores one unordered file per step)."""
    def _chain(self, n):
        recs, prev = [], GENESIS
        for i in range(n):
            r = seal({"step_id": f"s{i}", "node_id": f"n{i}", "owner": "o", "status": "succeeded"}, prev)
            recs.append(r); prev = r["hash"]
        return recs

    def test_shuffled_export_still_verifies(self):
        recs = self._chain(5)
        shuffled = [recs[3], recs[0], recs[4], recs[1], recs[2]]  # filesystem glob order
        v = verify_export(shuffled)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["length"], 5)
        self.assertEqual(v["head"], verify_chain(recs)["head"])  # agrees with ordered chain

    def test_tampered_export_detected(self):
        recs = self._chain(5)
        recs[2]["owner"] = "attacker"  # hash now stale -> next link won't thread
        self.assertFalse(verify_export(recs)["ok"])

    def test_dropped_export_record_detected(self):
        recs = self._chain(5)
        del recs[2]
        self.assertFalse(verify_export(recs)["ok"])

    def test_empty_and_unsealed(self):
        self.assertTrue(verify_export([])["ok"])
        self.assertFalse(verify_export([{"step_id": "x"}])["sealed"])  # legacy/no hash


class TestStoreChain(unittest.TestCase):
    def test_saved_audit_is_a_valid_chain(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False); tmp.close()
        store = Store(tmp.name)
        for i in range(4):
            store.save_audit(AuditRecord(step_id=f"step-{i}", run_id="run-x", node_id=f"n{i}",
                                         node_name=f"N{i}", owner="tester", status="succeeded"))
        recs = store.audit_records("run-x")
        self.assertEqual(len(recs), 4)
        self.assertTrue(all("hash" in r and "prev_hash" in r for r in recs))
        self.assertTrue(verify_chain(recs)["ok"])
        # tamper the stored JSON directly -> verify must fail
        store.conn.execute("UPDATE audit SET record=replace(record,'\"succeeded\"','\"failed\"') WHERE step_id='step-1'")
        store.conn.commit()
        self.assertFalse(verify_chain(store.audit_records("run-x"))["ok"])
        store.close()


if __name__ == "__main__":
    unittest.main()
