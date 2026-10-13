"""A bulk load ends with the degree cache warm (ADR-0012).

The bulk path writes through Graph.KG.Adjacency, which bumps generations but fills no cache. Without a warm-up the first
reader after the load recounts every node it touches (the head-to-head benchmark loads and reads once, so it always paid
that), and Build2HopStats validates each middle node one by one. The session therefore calls Graph.KG.Adj.WarmCache once,
before Build2HopStats, in the incremental branch; the full-sync branch already warms inside BuildKG.
"""

from unittest.mock import MagicMock, patch

from iris_vector_graph.engine import IRISGraphEngine


def _engine(calls):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._connection_params = {"hostname": "h", "port": 1972, "namespace": "USER", "username": "u", "password": "p"}
    iris_obj = MagicMock()
    iris_obj.classMethodValue.side_effect = lambda cls, method, *a: calls.append((cls, method)) or "0"
    eng._iris_obj = lambda: iris_obj
    return eng


def test_the_incremental_finish_warms_the_cache_before_the_two_hop_stats():
    calls = []
    eng = _engine(calls)
    with patch.object(IRISGraphEngine, "_bulk_load_drifted", return_value=False), patch(
        "iris_vector_graph._engine.nodes_edges.GraphSchema"
    ):
        with eng.bulk_load_session(rebuild_indexes=False, incremental=True):
            pass
    names = [m for _, m in calls]
    assert "WarmCache" in names and "Build2HopStats" in names
    assert names.index("WarmCache") < names.index("Build2HopStats")
    assert ("Graph.KG.Adj", "WarmCache") in calls


def test_a_failed_warm_up_does_not_stop_the_session_and_still_builds_the_stats():
    calls = []
    eng = _engine(calls)
    real = eng._iris_obj().classMethodValue.side_effect

    def flaky(cls, method, *a):
        if method == "WarmCache":
            raise RuntimeError("cache warm-up failed")
        return real(cls, method, *a)

    eng._iris_obj().classMethodValue.side_effect = flaky
    with patch.object(IRISGraphEngine, "_bulk_load_drifted", return_value=False), patch(
        "iris_vector_graph._engine.nodes_edges.GraphSchema"
    ):
        with eng.bulk_load_session(rebuild_indexes=False, incremental=True):
            pass
    assert ("Graph.KG.Traversal", "Build2HopStats") in calls
