"""4.1.1 — directed betweenness is answered by the exact ObjectScript Brandes.

`kg_betweenness_global_v` (libarno_callout.so of 2026-07-04) scores the graph as
undirected: on a directed ER(20, 0.25) its Pearson against networkx is 0.741 directed
and 1.000 undirected, while `BetweennessGlobalParallel` is 1.000 directed. The engine's
default is direction="out", so Rust answered it wrong whenever the library was loaded.
Both ObjectScript tiers walk out-edges only, which is right for "out" and "in"
(reversing every edge leaves betweenness unchanged) and wrong for "both", so "both"
goes to LazyKG, which honours direction. The live proof is
tests/e2e/test_centrality_e2e.py::TestBetweennessCentrality::test_betweenness_exact_on_small_graph.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/NKGAccelCentrality.cls"


def test_betweenness_global_does_not_call_rust():
    body = re.search(r"^ClassMethod BetweennessGlobal\(.*?^\}", CLS.read_text(), re.S | re.M).group(0)
    assert "$ZF(" not in body
    assert "..BetweennessGlobalParallel(" in body


def _store(calls):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    iris_obj = mock.Mock()

    def cmv(cls, method, *args):
        calls.append(method)
        if method == "IsLoaded":
            return 1
        return 'OK:[{"id":"a","score":1.0}]'

    iris_obj.classMethodValue.side_effect = cmv
    store._iris_obj = lambda: iris_obj
    store.conn = mock.Mock()
    return store


def test_out_is_answered_by_objectscript():
    calls = []
    res = _store(calls)._betweenness_gref(0, "out", 0, 10, 256, None)
    assert "BetweennessGlobal" in calls
    assert res.rows == [["a", 1.0]]


def test_both_is_not_answered_by_the_out_edge_walk():
    calls = []
    store = _store(calls)
    lkg = mock.Mock()
    lkg.iter_nodes.return_value = iter([])
    with mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg) as made:
        store._betweenness_gref(0, "both", 0, 10, 256, None)
    assert "BetweennessGlobal" not in calls
    made.assert_called_once()
