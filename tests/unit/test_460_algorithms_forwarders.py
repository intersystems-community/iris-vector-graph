"""4.6.0 R065: every `engine.algorithms.<name>` forwards to a method the engine has.

`execute_wcc`, `execute_scc`, `execute_k_core`, `execute_triangle_count` and `pagerank` forwarded to engine methods of
their own names, which do not exist. The answers are pinned live in tests/e2e/test_460_algorithms_namespace_e2e.py.
"""

import inspect
import re

from iris_vector_graph import engine as engine_module
from iris_vector_graph.engine import IRISGraphEngine, _AlgorithmsSubEngine


def test_each_forwarder_names_an_engine_method():
    source = inspect.getsource(_AlgorithmsSubEngine)
    targets = dict(re.findall(r"def (\w+)\(self, \*a, \*\*kw\): return self\._e\.(\w+)\(", source))
    assert targets, source
    missing = {name: target for name, target in targets.items() if not callable(getattr(IRISGraphEngine, target, None))}
    assert missing == {}, missing


def test_execute_wcc_returns_the_engine_s_wcc():
    class Engine:
        def kg_WCC(self, *a, **kw):
            return {"a": "a", "b": "a"}

    assert engine_module._AlgorithmsSubEngine(Engine()).execute_wcc() == {"a": "a", "b": "a"}
