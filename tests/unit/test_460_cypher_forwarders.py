"""4.6.0 R067: every sub-engine forwarder (`engine.cypher.*`, `engine.temporal.*`, ...) names a method that exists.

`engine.cypher.execute_bfs` and `execute_knn_vec` forwarded to engine methods of their own names, which do not
exist; R065 found the same in `engine.algorithms`. They are the store's methods (the GraphStore protocol's), so they
forward to `engine._store`. The answers are pinned live in tests/e2e/test_460_cypher_namespace_e2e.py.
"""

import inspect

from iris_vector_graph import engine as engine_module
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class _Recorder:
    """Stands in for the engine: records the attribute path a forwarder walks and the call it makes at the end."""

    def __init__(self, log, path=()):
        self._log, self._path = log, path

    def __getattr__(self, name):
        return _Recorder(self._log, self._path + (name,))

    def __call__(self, *a, **kw):
        self._log.append((self._path, a, kw))
        return ("forwarded", self._path)


def forwarded(cls) -> dict:
    """{method: attribute path on the engine} for every method of a sub-engine, found by calling it (R128: the
    behaviour, not the source text). Each must pass its arguments through unchanged."""
    out = {}
    for name, fn in vars(cls).items():
        if name.startswith("__") or not inspect.isfunction(fn):
            continue
        log: list = []
        result = getattr(cls(_Recorder(log)), name)(1, "x", k=2)
        assert len(log) == 1, (cls.__name__, name, log)
        path, a, kw = log[0]
        assert (a, kw) == ((1, "x"), {"k": 2}), (cls.__name__, name, a, kw)
        assert result == ("forwarded", path), (cls.__name__, name, result)
        out[name] = path
    return out


def _sub_engines():
    return [obj for name, obj in vars(engine_module).items()
            if inspect.isclass(obj) and name.startswith("_") and name.endswith("SubEngine")]


def test_each_sub_engine_forwarder_names_a_method_that_exists():
    missing = {}
    seen = 0
    for cls in _sub_engines():
        for name, path in forwarded(cls).items():
            seen += 1
            owner = IRISGraphStore if path[:1] == ("_store",) else IRISGraphEngine
            target = path[-1]
            if len(path) > 2 or (len(path) == 2 and path[0] != "_store") or not callable(getattr(owner, target, None)):
                missing[f"{cls.__name__}.{name}"] = f"{owner.__name__}.{'.'.join(path)}"
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
