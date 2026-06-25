"""Pipeline structural validation (quick-win A)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moira_core import (  # noqa: E402
    GateConfig, GateMode, Node, NodeType, Pipeline, available_pipelines,
    client_gated_pipeline, default_sdlc_pipeline, validate_pipeline,
)


def _producer(nid, deps=None, **kw):
    return Node(id=nid, name=nid, type=NodeType.PRODUCER, role="x",
                depends_on=deps or [], **kw)


class TestValidatePipeline(unittest.TestCase):
    def test_valid_linear_pipeline_has_no_errors(self):
        pipe = Pipeline(id="p", name="P", nodes=[_producer("a"), _producer("b", ["a"])])
        self.assertEqual(validate_pipeline(pipe), [])

    def test_empty_id_or_name_is_an_error(self):
        pipe = Pipeline(id="", name="", nodes=[_producer("a")])
        errs = validate_pipeline(pipe)
        self.assertTrue(any("id" in e.lower() for e in errs))
        self.assertTrue(any("name" in e.lower() for e in errs))

    def test_no_nodes_is_an_error(self):
        self.assertTrue(validate_pipeline(Pipeline(id="p", name="P", nodes=[])))

    def test_duplicate_node_ids_is_an_error(self):
        pipe = Pipeline(id="p", name="P", nodes=[_producer("a"), _producer("a")])
        self.assertTrue(any("duplicate" in e.lower() and "a" in e for e in validate_pipeline(pipe)))

    def test_dangling_depends_on_is_an_error(self):
        pipe = Pipeline(id="p", name="P", nodes=[_producer("a", ["ghost"])])
        self.assertTrue(any("ghost" in e for e in validate_pipeline(pipe)))

    def test_dangling_on_reject_goto_is_an_error(self):
        gate = Node(id="g", name="G", type=NodeType.GATE,
                    gate=GateConfig(mode=GateMode.HUMAN), on_reject_goto="ghost",
                    depends_on=["a"])
        pipe = Pipeline(id="p", name="P", nodes=[_producer("a"), gate])
        self.assertTrue(any("ghost" in e for e in validate_pipeline(pipe)))

    def test_gate_node_without_gate_config_is_an_error(self):
        gate = Node(id="g", name="G", type=NodeType.GATE, gate=None, depends_on=["a"])
        pipe = Pipeline(id="p", name="P", nodes=[_producer("a"), gate])
        self.assertTrue(any("gate" in e.lower() and "g" in e for e in validate_pipeline(pipe)))

    def test_builtin_pipelines_are_valid(self):
        """Regression guard: shipped pipelines must pass validation, or every run 400s."""
        self.assertEqual(validate_pipeline(default_sdlc_pipeline()), [])
        self.assertEqual(validate_pipeline(client_gated_pipeline()), [])
        for p in available_pipelines():
            self.assertEqual(validate_pipeline(p), [], f"built-in pipeline {p.id} invalid")

    def test_dependency_cycle_is_an_error(self):
        pipe = Pipeline(id="p", name="P", nodes=[
            _producer("a", ["b"]), _producer("b", ["a"]),
        ])
        self.assertTrue(any("cycle" in e.lower() for e in validate_pipeline(pipe)))


if __name__ == "__main__":
    unittest.main()
