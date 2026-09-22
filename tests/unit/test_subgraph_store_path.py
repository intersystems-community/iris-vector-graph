"""`kg_SUBGRAPH` must answer the same shape whichever path served it.

`Graph.KG.Subgraph.SubgraphJson` returns `{"nodes","edges","properties","labels"}`
(Subgraph.cls:153-156). The store parsed that envelope and forwarded only two of
the four keys, and the store path in `kg_SUBGRAPH` then built a `SubgraphData`
with `node_properties={}`, `node_labels={}` and `node_embeddings={}` and left the
edges as raw `{"s","p","o"}` dicts instead of the declared
`List[Tuple[str, str, str]]`.

Nothing raised: `include_properties=True` and `include_embeddings=True` were
accepted and silently ignored, so a caller read "this subgraph has no properties,
no labels and no embeddings" — and a caller unpacking `for s, p, o in sg.edges`
got the dict *keys*. Only the ObjectScript fallback (reached when the store
reports no `subgraph` capability) was ever right, which is why the shape survived.
"""

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.result import IVGResult

ENVELOPE = {
    "nodes": ["A", "B"],
    "edges": [{"s": "A", "p": "REL", "o": "B"}],
    "properties": {"A": {"name": "NodeA"}},
    "labels": {"A": ["Gene"]},
}


def _store():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    st = object.__new__(IRISGraphStore)
    st.conn = MagicMock()
    st._arno_available = False
    st._arno_capabilities = {"algorithms": []}
    st._detect_arno = lambda: False
    st._call_classmethod = lambda *a: json.dumps(ENVELOPE)
    return st


def _engine(result, emb_rows=()):
    """An engine whose store answers `result`, with a cursor for the emb read."""
    from iris_vector_graph.engine import IRISGraphEngine

    eng = object.__new__(IRISGraphEngine)
    eng._store = MagicMock()
    eng._store.execute_subgraph.return_value = result
    eng._store_capabilities = {"subgraph": True}
    eng.conn = MagicMock()
    eng.conn.cursor.return_value.fetchall.return_value = list(emb_rows)
    eng._t = lambda name: f"Graph_KG.{name}"
    return eng


def test_store_carries_properties_and_labels():
    result = _store().execute_subgraph(["A"], 2, [], 100)
    assert result.columns == ["nodes", "edges", "properties", "labels"]
    row = result.rows[0]
    assert json.loads(row[2]) == {"A": {"name": "NodeA"}}
    assert json.loads(row[3]) == {"A": ["Gene"]}


def test_store_path_fills_properties_and_labels():
    eng = _engine(_store().execute_subgraph(["A"], 2, [], 100))
    sg = eng.kg_SUBGRAPH(["A"], k_hops=2)
    assert sg.node_properties == {"A": {"name": "NodeA"}}
    assert sg.node_labels == {"A": ["Gene"]}


def test_store_path_edges_are_triples():
    """`SubgraphData.edges` is declared `List[Tuple[str, str, str]]`."""
    eng = _engine(_store().execute_subgraph(["A"], 2, [], 100))
    sg = eng.kg_SUBGRAPH(["A"], k_hops=2)
    assert sg.edges == [("A", "REL", "B")]
    for s, p, o in sg.edges:  # the documented way to read them
        assert (s, p, o) == ("A", "REL", "B")


def test_store_path_honours_include_properties_false():
    eng = _engine(_store().execute_subgraph(["A"], 2, [], 100))
    sg = eng.kg_SUBGRAPH(["A"], k_hops=2, include_properties=False)
    assert sg.node_properties == {} and sg.node_labels == {}


def test_store_path_reads_embeddings_when_asked():
    eng = _engine(
        _store().execute_subgraph(["A"], 2, [], 100), emb_rows=[("A", "0.1,0.2")]
    )
    sg = eng.kg_SUBGRAPH(["A"], k_hops=2, include_embeddings=True)
    assert sg.node_embeddings == {"A": [0.1, 0.2]}
    sql = eng.conn.cursor.return_value.execute.call_args[0][0]
    assert "node_id" in sql and " id " not in sql


def test_store_path_skips_the_embedding_read_by_default():
    eng = _engine(_store().execute_subgraph(["A"], 2, [], 100))
    sg = eng.kg_SUBGRAPH(["A"], k_hops=2)
    assert sg.node_embeddings == {}
    eng.conn.cursor.assert_not_called()


def test_a_two_column_answer_still_works():
    """An older store (or a mock) answering only nodes/edges must not break."""
    eng = _engine(
        IVGResult(
            columns=["nodes", "edges"],
            rows=[[json.dumps(["A"]), json.dumps([{"s": "A", "p": "REL", "o": "B"}])]],
        )
    )
    sg = eng.kg_SUBGRAPH(["A"], k_hops=2)
    assert sg.nodes == ["A"] and sg.edges == [("A", "REL", "B")]
    assert sg.node_properties == {}
