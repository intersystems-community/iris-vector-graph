"""Which ObjectScript class each algorithm entry point talks to.

`tests/unit/test_store_dispatch_targets.py` proves the pairs exist; these tests
pin down *which* pair each method sends, because a wrong-but-existing target is
just as silent: every method here catches `Exception` and answers with an empty
result, so `kg_SUBGRAPH` returned `{"nodes": [], "edges": []}` in every
deployment while `Graph.KG.Subgraph.SubgraphJson` sat there working.

Native-API `classMethodValue` does not resolve inherited ClassMethods
(`NKGAccel.cls:7`: "Sibling (not nested) names required"), so naming a subclass
that merely extends the owner is not good enough either.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class _Recorder:
    """Stands in for `_call_classmethod` / `_arno_call`, recording (cls, meth)."""

    def __init__(self, handler=None):
        self.calls = []
        self._handler = handler or (lambda cls, meth, args: "[]")

    def __call__(self, cls, meth, *args):
        self.calls.append((cls, meth, args))
        out = self._handler(cls, meth, args)
        if isinstance(out, Exception):
            raise out
        return out

    @property
    def pairs(self):
        return [(c, m) for c, m, _ in self.calls]


def _store(arno_algorithms=None, handler=None, bfs=False):
    st = object.__new__(IRISGraphStore)
    st.conn = MagicMock()
    st._arno_available = bool(arno_algorithms) or bfs
    st._arno_capabilities = {"algorithms": list(arno_algorithms or []), "bfs": bfs}
    rec = _Recorder(handler)
    st._call_classmethod = rec
    st._arno_call = rec
    st._detect_arno = lambda: st._arno_available
    st._iris_obj = lambda: MagicMock()
    st.rec = rec
    return st


# `Graph.KG.Subgraph.SubgraphJson` emits {"s","p","o"} edge objects plus properties
# and labels (Subgraph.cls:152-162). The list-triple this fixture used to hold is a
# shape no shipped method emits, and spec 230's reader now refuses it rather than
# reading a predicate out of position 2.
SUBGRAPH_JSON = json.dumps(
    {
        "nodes": ["A", "B"],
        "edges": [{"s": "A", "p": "REL", "o": "B"}],
        "properties": {},
        "labels": {},
    }
)


# ── Subgraph ──────────────────────────────────────────────────────────────────


def test_subgraph_objectscript_path_calls_subgraph_class():
    st = _store(handler=lambda c, m, a: SUBGRAPH_JSON)
    result = st.execute_subgraph(["A"], 2, None, 100)
    assert ("Graph.KG.Subgraph", "SubgraphJson") in st.rec.pairs
    assert json.loads(result.rows[0][0]) == ["A", "B"]


def test_subgraph_arno_path_calls_arnoaccel():
    st = _store(arno_algorithms=["subgraph"], handler=lambda c, m, a: SUBGRAPH_JSON)
    st.execute_subgraph(["A"], 2, None, 100)
    assert ("Graph.KG.ArnoAccel", "SubgraphJson") in st.rec.pairs


def test_subgraph_failure_reports_the_error():
    """An empty subgraph and a failed subgraph must not look the same."""
    st = _store(handler=lambda c, m, a: RuntimeError("METHOD DOES NOT EXIST"))
    result = st.execute_subgraph(["A"], 2, None, 100)
    assert result.error
    assert "METHOD DOES NOT EXIST" in result.error


# ── PageRank / PPR ────────────────────────────────────────────────────────────


def test_ppr_objectscript_path_calls_run_json():
    st = _store(handler=lambda c, m, a: json.dumps([{"id": "A", "score": 1.0}]))
    result = st.execute_ppr(["A"], 0.85, 20)
    assert ("Graph.KG.PageRank", "RunJson") in st.rec.pairs
    assert result.rows == [["A", 1.0]]


def test_ppr_arno_path_calls_arnoaccel():
    st = _store(arno_algorithms=["ppr"], handler=lambda c, m, a: "[]")
    st.execute_ppr(["A"], 0.85, 20)
    assert ("Graph.KG.ArnoAccel", "PPRJson") in st.rec.pairs


def test_pagerank_arno_path_calls_arnoaccel():
    st = _store(arno_algorithms=["pagerank"], handler=lambda c, m, a: "[]")
    st.execute_pagerank(0.85, 20)
    assert ("Graph.KG.ArnoAccel", "PageRankGlobalJson") in st.rec.pairs


def test_wcc_arno_path_calls_arnoaccel():
    st = _store(arno_algorithms=["wcc"], handler=lambda c, m, a: "{}")
    st.execute_wcc()
    assert ("Graph.KG.ArnoAccel", "WCCJson") in st.rec.pairs


def test_cdlp_arno_path_calls_arnoaccel():
    st = _store(arno_algorithms=["cdlp"], handler=lambda c, m, a: "{}")
    st.execute_cdlp(10)
    assert ("Graph.KG.ArnoAccel", "CDLPJson") in st.rec.pairs


# ── Arno BFS paging ───────────────────────────────────────────────────────────


def test_arno_bfs_pages_through_traversal_with_a_cursor():
    """`SORTED:tag:n` stages ^ArnoKG("bfs_r"); only the cursor API reads it.

    The old loop called `NKGAccel.ReadBFSPage(tag, i)` with a page *number* and
    concatenated the returned strings, but `ReadBFSPage` lives on
    `Graph.KG.Traversal`, takes `(tag, cursorStep, cursorO, pageSize)` and returns
    an `{"items": …, "done": …}` envelope — so the concatenation was never valid
    JSON and the rows came back empty.
    """
    st = _store(bfs=True, handler=lambda c, m, a: "SORTED:tag123:2")
    page = json.dumps(
        {
            "items": [
                {"s": "A", "p": "REL", "o": "B", "w": 1, "step": 1},
                {"s": "B", "p": "REL", "o": "C", "w": 1, "step": 2},
            ],
            "next_step": -1,
            "next_o": "",
            "done": True,
        }
    )
    seen = []

    def fake_call(conn, cls, meth, *args):
        seen.append((cls, meth, args))
        return page

    with patch("iris_vector_graph.engine._call_classmethod", side_effect=fake_call):
        result = st._run_arno_bfs("A", [], 3, "out", 0)

    assert [(c, m) for c, m, _ in seen] == [("Graph.KG.Traversal", "ReadBFSPage")]
    assert seen[0][2][0] == "tag123"
    assert result.rows == [["B", 1, "REL"], ["C", 2, "REL"]]


# ── KNN ───────────────────────────────────────────────────────────────────────


def test_knn_vec_uses_scoped_sql_and_no_objectscript():
    """There is no `kg_KNN_VEC` ClassMethod anywhere; the scoped SQL is the path."""
    st = _store()
    st.conn.cursor.return_value.fetchall.return_value = [("n1", 0.9)]
    result = st.execute_knn_vec([0.1, 0.2], 5, None, graph="g1")
    assert st.rec.calls == []
    assert result.rows == [["n1", 0.9]]
    sql, params = st.conn.cursor.return_value.execute.call_args[0]
    assert "graph_id" in sql
    assert "g1" in params


def test_knn_vec_declares_the_query_vector_dtype_and_width():
    """A bare `TO_VECTOR(?)` is `SQLCODE -259` against a VECTOR(DOUBLE, n) column.

    IRIS infers the dtype of an untyped `TO_VECTOR` from the literal and then
    refuses to compare two vectors of different datatypes — it fails at query
    *open*, so even an empty table errors. The declared width comes from the query
    vector, so a wrong-width vector errors (ADR-0005) instead of being reshaped.
    """
    st = _store()
    st.conn.cursor.return_value.fetchall.return_value = []
    st.execute_knn_vec([0.1, 0.2, 0.3], 5, None)
    sql = st.conn.cursor.return_value.execute.call_args[0][0]
    assert "TO_VECTOR(?, DOUBLE, 3)" in sql


def test_knn_vec_skips_rows_with_no_vector():
    """`VECTOR_COSINE` of a NULL `emb` is NULL, and `float(None)` raises.

    A node row can exist with no embedding yet, and `TOP k … ORDER BY score DESC`
    happily returns those NULL-scored rows — the whole search then failed inside
    the result loop and was reported as an empty answer.
    """
    st = _store()
    st.conn.cursor.return_value.fetchall.return_value = [("n1", None), ("n2", 0.5)]
    result = st.execute_knn_vec([0.1, 0.2], 5, None)
    sql = st.conn.cursor.return_value.execute.call_args[0][0]
    assert "emb IS NOT NULL" in sql
    assert result.rows == [["n2", 0.5]]
    assert result.error is None


def test_knn_vec_label_filter_reaches_the_sql():
    """The label was handed to the dead ObjectScript call and dropped by the SQL."""
    st = _store()
    st.conn.cursor.return_value.fetchall.return_value = []
    st.execute_knn_vec([0.1, 0.2], 5, "Gene")
    sql, params = st.conn.cursor.return_value.execute.call_args[0]
    assert "rdf_labels" in sql
    assert "Gene" in params


# ── Temporal Cypher window BFS ────────────────────────────────────────────────


def _window_handler(edges_by_source):
    """Answer QueryWindow/QueryWindowInbound out of a dict, echoing the graph."""

    def handler(cls, meth, args):
        assert cls == "Graph.KG.TemporalIndex"
        if meth == "QueryWindow":
            graph, source = args[0], args[1]
        elif meth == "QueryWindowInbound":
            graph, source = args[0], args[1]
        else:  # pragma: no cover - a new target would be a defect
            raise AssertionError(f"unexpected method {meth}")
        handler.graphs.add(graph)
        return json.dumps(edges_by_source.get((meth, source), []))

    handler.graphs = set()
    return handler


def _edge(s, p, o, ts):
    return {"s": s, "p": p, "o": o, "ts": ts, "w": 1.0}


def test_temporal_cypher_walks_outbound_windows_hop_by_hop():
    handler = _window_handler(
        {
            ("QueryWindow", "A"): [_edge("A", "REL", "B", 1500)],
            ("QueryWindow", "B"): [_edge("B", "REL", "C", 1600)],
        }
    )
    st = _store(handler=handler)
    result = st.execute_temporal_cypher("A", [], 1000, 2000, "out", 2)
    assert result.columns == ["id", "hops", "pred", "ts"]
    assert result.rows == [["B", 1, "REL", 1500], ["C", 2, "REL", 1600]]


def test_temporal_cypher_respects_max_hops():
    handler = _window_handler(
        {
            ("QueryWindow", "A"): [_edge("A", "REL", "B", 1500)],
            ("QueryWindow", "B"): [_edge("B", "REL", "C", 1600)],
        }
    )
    st = _store(handler=handler)
    result = st.execute_temporal_cypher("A", [], 1000, 2000, "out", 1)
    assert [r[0] for r in result.rows] == ["B"]


def test_temporal_cypher_uses_inbound_reader_for_in_direction():
    handler = _window_handler({("QueryWindowInbound", "A"): [_edge("Z", "REL", "A", 1200)]})
    st = _store(handler=handler)
    result = st.execute_temporal_cypher("A", [], 1000, 2000, "in", 1)
    assert result.rows == [["Z", 1, "REL", 1200]]


def test_temporal_cypher_threads_the_graph():
    handler = _window_handler({("QueryWindow", "A"): []})
    st = _store(handler=handler)
    st.execute_temporal_cypher("A", [], 1000, 2000, "out", 1, graph="tenantA")
    assert handler.graphs == {"tenantA"}


def test_temporal_cypher_default_graph_is_the_default_not_every_graph():
    handler = _window_handler({("QueryWindow", "A"): []})
    st = _store(handler=handler)
    st.execute_temporal_cypher("A", [], 1000, 2000, "out", 1)
    assert handler.graphs == {""}


def test_temporal_cypher_filters_by_predicate():
    calls = []

    def handler(cls, meth, args):
        calls.append(args[2])  # predicate
        return "[]"

    st = _store(handler=handler)
    st.execute_temporal_cypher("A", ["CITED", "WROTE"], 1000, 2000, "out", 1)
    assert set(calls) == {"CITED", "WROTE"}


def test_temporal_cypher_does_not_revisit_a_cycle():
    handler = _window_handler(
        {
            ("QueryWindow", "A"): [_edge("A", "REL", "B", 1500)],
            ("QueryWindow", "B"): [_edge("B", "REL", "A", 1600)],
        }
    )
    st = _store(handler=handler)
    result = st.execute_temporal_cypher("A", [], 1000, 2000, "out", 5)
    assert [r[0] for r in result.rows] == ["B"]


def test_temporal_cypher_failure_reports_the_error():
    st = _store(handler=lambda c, m, a: RuntimeError("no such method"))
    result = st.execute_temporal_cypher("A", [], 1000, 2000, "out", 2)
    assert result.rows == []
    assert result.error and "no such method" in result.error
