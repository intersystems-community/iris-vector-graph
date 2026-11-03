"""4.6.0 R067: every sub-engine forwarder (`engine.cypher.*`, `engine.temporal.*`, ...) names a method that exists.

`engine.cypher.execute_bfs` and `execute_knn_vec` forwarded to engine methods of their own names, which do not
exist; R065 found the same in `engine.algorithms`. They are the store's methods (the GraphStore protocol's), so they
forward to `engine._store`. The answers are pinned live in tests/e2e/test_460_cypher_namespace_e2e.py.
"""

import inspect
import re

from iris_vector_graph import engine as engine_module
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

_FORWARD = re.compile(r"def (\w+)\(self, \*a, \*\*kw\): return self\._e\.(_store\.)?(\w+)\(")


def _sub_engines():
    return [obj for name, obj in vars(engine_module).items()
            if inspect.isclass(obj) and name.startswith("_") and name.endswith("SubEngine")]


def test_each_sub_engine_forwarder_names_a_method_that_exists():
    missing = {}
    seen = 0
    for cls in _sub_engines():
        for name, store, target in _FORWARD.findall(inspect.getsource(cls)):
            seen += 1
            owner = IRISGraphStore if store else IRISGraphEngine
            if not callable(getattr(owner, target, None)):
                missing[f"{cls.__name__}.{name}"] = f"{owner.__name__}.{target}"
    assert seen > 20, seen
    assert missing == {}, missing


def test_cypher_execute_bfs_and_knn_forward_to_the_store():
    class Store:
        def execute_bfs(self, *a, **kw):
            return ("bfs", a, kw)

        def execute_knn_vec(self, *a, **kw):
            return ("knn", a, kw)

    class Engine:
        _store = Store()

    sub = engine_module._CypherSubEngine(Engine())
    assert sub.execute_bfs("a", ["P"], 2, "out", 10, graph="g") == ("bfs", ("a", ["P"], 2, "out", 10), {"graph": "g"})
    assert sub.execute_knn_vec([1.0], 3, None) == ("knn", ([1.0], 3, None), {})
