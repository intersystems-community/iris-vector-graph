"""The accelerated readers must read the keys Arno actually returns (spec 230, FR-017).

Measured against `ivg-iris-enterprise` with the callout loaded, the accelerated entry
points do not answer in the shape the store's columns promise:

```text
Graph.KG.ArnoAccel.WCCJson   -> {"components":1,"largest":[{"root":"a","size":3}]}
Graph.KG.Algorithms.WCCJson  -> {"a":"a","b":"a","c":"a"}
Graph.KG.ArnoAccel.CDLPJson  -> {"communities":2,"largest":[{"label":"a","size":2},...]}
Graph.KG.Algorithms.CDLPJson -> {"a":"a","b":"b","c":"a"}
Graph.KG.ArnoAccel.SubgraphJson -> {"nodes":[...],"edges":[{"src":..,"dst":..,"type":"REL"}]}
Graph.KG.Subgraph.SubgraphJson  -> {"nodes":[...],"edges":[{"s":..,"p":..,"o":..}],
                                    "properties":{...},"labels":{...}}
Graph.KG.ArnoAccel.PageRankGlobalJson -> [{"node":...,"score":...}]
Graph.KG.PageRank.PageRankGlobalJson  -> [{"id":...,"score":...}]
```

Arno's WCC and CDLP are *summaries*: a component count and the largest few. Read as a
node→component map they produce the rows `[["components",1],["largest",[...]]]` under
the columns `id, component_id`, which is not an error anyone sees — it is a plausible
answer to a question nobody asked. Arno's subgraph keeps the edge but loses the
predicate (`"type":"REL"`) and carries no properties or labels.

So a reader has three cases, and all three must be explicit: read it, fall back to the
ObjectScript owner that answers the promised question, or raise. What it must never do
is emit rows it cannot justify.

Unit-only: this is pure Python dispatch over a JSON string, so a fake store settles it.
"""

import json

import pytest

from iris_vector_graph.stores.iris_sql_store import (
    AcceleratedSummaryShape,
    IRISGraphStore,
    UnreadableResultShape,
    _scored_node_rows,
)


class _Recorder:
    """Records every classmethod call and answers from `handler`."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler

    def __call__(self, cls, meth, *args):
        self.calls.append((cls, meth, args))
        out = self.handler(cls, meth, *args) if self.handler else ""
        if isinstance(out, Exception):
            raise out
        return out

    def called(self, cls, meth):
        return any(c == cls and m == meth for c, m, _ in self.calls)


def _store(arno_algorithms=None, handler=None):
    st = object.__new__(IRISGraphStore)
    st.conn = None
    st._arno_available = bool(arno_algorithms)
    st._arno_capabilities = {"algorithms": list(arno_algorithms or []), "bfs": False}
    rec = _Recorder(handler)
    st._call_classmethod = rec
    st._arno_call = rec
    st._detect_arno = lambda: st._arno_available
    st.rec = rec
    return st


def _answers(mapping):
    """A handler answering `{(class, method): payload}`, JSON-encoding dicts/lists."""

    def handler(cls, meth, *args):
        payload = mapping[(cls, meth)]
        if isinstance(payload, (dict, list)):
            return json.dumps(payload)
        return payload

    return handler


# ---------------------------------------------------------------------------
# WCC
# ---------------------------------------------------------------------------

ARNO_WCC_SUMMARY = {"components": 1, "largest": [{"root": "a", "size": 3}]}
OS_WCC_MAP = {"a": "a", "b": "a", "c": "a"}


def test_arno_wcc_summary_does_not_become_rows():
    """The summary's own keys must never reach the caller as node IDs."""
    st = _store(
        ["wcc"],
        _answers(
            {
                ("Graph.KG.ArnoAccel", "WCCJson"): ARNO_WCC_SUMMARY,
                ("Graph.KG.Algorithms", "WCCJson"): OS_WCC_MAP,
            }
        ),
    )
    result = st.execute_wcc()
    ids = {r[0] for r in result.rows}
    assert "components" not in ids
    assert "largest" not in ids


def test_arno_wcc_summary_falls_back_to_the_objectscript_owner():
    """`Graph.KG.Algorithms.WCCJson` answers the question the columns promise."""
    st = _store(
        ["wcc"],
        _answers(
            {
                ("Graph.KG.ArnoAccel", "WCCJson"): ARNO_WCC_SUMMARY,
                ("Graph.KG.Algorithms", "WCCJson"): OS_WCC_MAP,
            }
        ),
    )
    result = st.execute_wcc()
    assert result.error is None
    assert st.rec.called("Graph.KG.Algorithms", "WCCJson")
    assert sorted(result.rows) == [["a", "a"], ["b", "a"], ["c", "a"]]


def test_wcc_reads_the_per_node_map_without_arno():
    st = _store(handler=_answers({("Graph.KG.Algorithms", "WCCJson"): OS_WCC_MAP}))
    result = st.execute_wcc()
    assert result.error is None
    assert sorted(result.rows) == [["a", "a"], ["b", "a"], ["c", "a"]]


def test_wcc_reads_a_row_list_that_names_the_node_either_way():
    """`id` is the ObjectScript spelling, `node` the callout's."""
    rows = [{"id": "a", "component_id": 1}, {"node": "b", "component_id": 2}]
    st = _store(handler=_answers({("Graph.KG.Algorithms", "WCCJson"): rows}))
    result = st.execute_wcc()
    assert result.error is None
    assert sorted(result.rows) == [["a", 1], ["b", 2]]


def test_wcc_on_an_unreadable_shape_reports_an_error_and_no_rows():
    st = _store(handler=_answers({("Graph.KG.Algorithms", "WCCJson"): [1, 2, 3]}))
    result = st.execute_wcc()
    assert result.rows == []
    assert result.error


def test_wcc_does_not_loop_when_the_fallback_is_also_a_summary():
    """Two summaries is an error, not a third attempt."""
    st = _store(
        ["wcc"],
        _answers(
            {
                ("Graph.KG.ArnoAccel", "WCCJson"): ARNO_WCC_SUMMARY,
                ("Graph.KG.Algorithms", "WCCJson"): ARNO_WCC_SUMMARY,
            }
        ),
    )
    result = st.execute_wcc()
    assert result.rows == []
    assert result.error
    assert sum(1 for c, m, _ in st.rec.calls if m == "WCCJson") == 2


# ---------------------------------------------------------------------------
# CDLP
# ---------------------------------------------------------------------------

ARNO_CDLP_SUMMARY = {
    "communities": 2,
    "largest": [{"label": "a", "size": 2}, {"label": "b", "size": 1}],
}
OS_CDLP_MAP = {"a": "a", "b": "b", "c": "a"}


def test_arno_cdlp_summary_falls_back_to_the_objectscript_owner():
    st = _store(
        ["cdlp"],
        _answers(
            {
                ("Graph.KG.ArnoAccel", "CDLPJson"): ARNO_CDLP_SUMMARY,
                ("Graph.KG.Algorithms", "CDLPJson"): OS_CDLP_MAP,
            }
        ),
    )
    result = st.execute_cdlp(10)
    assert result.error is None
    ids = {r[0] for r in result.rows}
    assert "communities" not in ids and "largest" not in ids
    assert sorted(result.rows) == [["a", "a"], ["b", "b"], ["c", "a"]]


def test_cdlp_on_an_unreadable_shape_reports_an_error_and_no_rows():
    st = _store(handler=_answers({("Graph.KG.Algorithms", "CDLPJson"): "42"}))
    result = st.execute_cdlp(10)
    assert result.rows == []
    assert result.error


# ---------------------------------------------------------------------------
# Subgraph
# ---------------------------------------------------------------------------

ARNO_SUBGRAPH = {
    "nodes": ["a", "b", "c"],
    "edges": [{"src": "a", "dst": "b", "type": "REL"}, {"src": "b", "dst": "c", "type": "REL"}],
}
OS_SUBGRAPH = {
    "nodes": ["a", "b", "c"],
    "edges": [{"s": "a", "p": "KNOWS", "o": "b"}, {"s": "b", "p": "LIKES", "o": "c"}],
    "properties": {"a": {"name": "A"}},
    "labels": {"a": ["Person"]},
}


def _subgraph_columns(result):
    row = result.rows[0]
    return [json.loads(cell) for cell in row]


def test_arno_subgraph_losing_the_predicate_falls_back_to_the_owner():
    """`"type":"REL"` is not a predicate, and `p="REL"` would be a fabricated one."""
    st = _store(
        ["subgraph"],
        _answers(
            {
                ("Graph.KG.ArnoAccel", "SubgraphJson"): ARNO_SUBGRAPH,
                ("Graph.KG.Subgraph", "SubgraphJson"): OS_SUBGRAPH,
            }
        ),
    )
    result = st.execute_subgraph(["a"], 2, [], 100)
    assert result.error is None
    assert st.rec.called("Graph.KG.Subgraph", "SubgraphJson")
    nodes, edges, properties, labels = _subgraph_columns(result)
    assert nodes == ["a", "b", "c"]
    assert edges == OS_SUBGRAPH["edges"]
    assert properties == {"a": {"name": "A"}}
    assert labels == {"a": ["Person"]}
    assert not any("REL" in json.dumps(e) for e in edges)


def test_subgraph_keeps_the_owners_spo_edges_properties_and_labels():
    st = _store(handler=_answers({("Graph.KG.Subgraph", "SubgraphJson"): OS_SUBGRAPH}))
    result = st.execute_subgraph(["a"], 2, [], 100)
    assert result.error is None
    nodes, edges, properties, labels = _subgraph_columns(result)
    assert edges == OS_SUBGRAPH["edges"]
    assert properties and labels


def test_subgraph_on_an_unreadable_edge_shape_reports_an_error():
    st = _store(
        handler=_answers(
            {("Graph.KG.Subgraph", "SubgraphJson"): {"nodes": ["a"], "edges": [{"from": "a"}]}}
        )
    )
    result = st.execute_subgraph(["a"], 2, [], 100)
    assert result.error
    nodes, edges, _, _ = _subgraph_columns(result)
    assert edges == []


# ---------------------------------------------------------------------------
# Scored node rows (PageRank, PPR)
# ---------------------------------------------------------------------------


def test_scored_rows_read_both_spellings_of_the_node():
    rows = _scored_node_rows([{"id": "a", "score": 1.0}, {"node": "b", "score": 0.5}])
    assert rows == [["a", 1.0], ["b", 0.5]]


def test_scored_rows_raise_on_a_row_naming_no_node():
    """Dropping the row lost a score silently; the count was the only clue."""
    with pytest.raises(UnreadableResultShape) as excinfo:
        _scored_node_rows([{"id": "a", "score": 1.0}, {"vertex": "b", "score": 0.5}])
    assert "vertex" in str(excinfo.value)


def test_scored_rows_raise_on_a_row_that_is_not_a_mapping():
    with pytest.raises(UnreadableResultShape):
        _scored_node_rows([["a", 1.0]])


def test_pagerank_surfaces_an_unreadable_row_as_an_error():
    st = _store(
        handler=_answers(
            {("Graph.KG.PageRank", "PageRankGlobalJson"): [{"vertex": "a", "score": 1.0}]}
        )
    )
    result = st.execute_pagerank(0.85, 20)
    assert result.rows == []
    assert result.error


def test_pagerank_reads_the_callouts_node_key():
    st = _store(
        ["pagerank"],
        _answers(
            {("Graph.KG.ArnoAccel", "PageRankGlobalJson"): [{"node": "a", "score": 0.47}]}
        ),
    )
    result = st.execute_pagerank(0.85, 20)
    assert result.error is None
    assert result.rows == [["a", 0.47]]


# ---------------------------------------------------------------------------
# The exception hierarchy itself
# ---------------------------------------------------------------------------


def test_a_summary_shape_is_a_kind_of_unreadable_shape():
    """Callers that only care "this cannot be read" need one except clause."""
    assert issubclass(AcceleratedSummaryShape, UnreadableResultShape)
