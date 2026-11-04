"""4.6.0 R065: every `engine.algorithms.<name>` forwards to a method the engine has.

`execute_wcc`, `execute_scc`, `execute_k_core`, `execute_triangle_count` and `pagerank` forwarded to engine methods of
their own names, which do not exist. The answers are pinned live in tests/e2e/test_460_algorithms_namespace_e2e.py.
"""

from iris_vector_graph import engine as engine_module
from iris_vector_graph.engine import IRISGraphEngine, _AlgorithmsSubEngine
from tests.unit.test_460_cypher_forwarders import forwarded


def test_each_forwarder_names_an_engine_method():
    """Call each forwarder against a recording engine (R128: behaviour, not source text) and check the method it
    reached exists on IRISGraphEngine."""
    targets = {name: path for name, path in forwarded(_AlgorithmsSubEngine).items()}
    assert len(targets) >= 10, targets
    missing = {name: ".".join(path) for name, path in targets.items()
               if len(path) != 1 or not callable(getattr(IRISGraphEngine, path[0], None))}
    assert missing == {}, missing


def test_execute_wcc_returns_the_engine_s_wcc():
    class Engine:
        def kg_WCC(self, *a, **kw):
            return {"a": "a", "b": "a"}

    assert engine_module._AlgorithmsSubEngine(Engine()).execute_wcc() == {"a": "a", "b": "a"}
