"""R092: the predicate each networkx edge is loaded with, by `load_networkx` (engine) and `BulkLoader.load_networkx`.

obonet keeps an OBO relationship's type (`is_a`, `part_of`) in a MultiDiGraph's edge key, with no edge data;
`G.edges(data=True)` never yields the key, so both loaders stored every such edge as `is_a`. Expected predicates
are the ones each graph below was built with.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import networkx as nx

from iris_vector_graph.bulk_loader import networkx_edge_rows


def _rows(G):
    return sorted(networkx_edge_rows(G))


def test_a_multigraph_string_key_is_the_predicate():
    G = nx.MultiDiGraph()
    G.add_edge("b", "a", key="is_a")
    G.add_edge("c", "b", key="part_of")
    G.add_edge("c", "a", key="regulates")
    assert _rows(G) == [("b", "is_a", "a", None), ("c", "part_of", "b", None), ("c", "regulates", "a", None)]


def test_a_predicate_attribute_wins_over_the_key():
    G = nx.MultiDiGraph()
    G.add_edge("b", "a", key="k1", predicate="TREATS", score=3)
    assert _rows(G) == [("b", "TREATS", "a", {"score": 3})]


def test_an_integer_key_is_not_a_predicate():
    G = nx.MultiDiGraph()
    G.add_edge("b", "a")  # key 0
    G.add_edge("b", "a", label="KNOWS")  # key 1
    assert _rows(G) == [("b", "KNOWS", "a", None), ("b", "is_a", "a", None)]


def test_a_simple_graph_reads_its_attributes():
    G = nx.DiGraph()
    G.add_edge("x", "y", predicate="R", w=1)
    G.add_edge("y", "z", label="L")
    G.add_edge("z", "x", key="K")
    G.add_edge("x", "z")
    assert _rows(G) == [("x", "R", "y", {"w": 1}), ("x", "is_a", "z", None), ("y", "L", "z", None), ("z", "K", "x", None)]


def test_the_engine_loader_writes_the_key_as_the_predicate():
    from iris_vector_graph._engine.snapshot import SnapshotMixin

    eng = MagicMock()
    eng.create_node.return_value = True
    eng.create_edge.return_value = True
    G = nx.MultiDiGraph()
    G.add_node("a")
    G.add_node("b")
    G.add_edge("b", "a", key="part_of")
    SnapshotMixin.load_networkx(eng, G, auto_sync=False)
    eng.create_edge.assert_called_once_with(source_id="b", predicate="part_of", target_id="a", qualifiers=None)


def test_the_bulk_loader_writes_the_key_as_the_predicate():
    from iris_vector_graph.bulk_loader import BulkLoader

    loader = BulkLoader.__new__(BulkLoader)
    loader.load_nodes = MagicMock(return_value={"nodes": 2})
    loader.load_edges = MagicMock(return_value={"edges": 1})
    loader.conn = MagicMock()  # the closing row counts only
    loader.graph = ""
    loader._table = lambda name: f"S.{name}"
    G = nx.MultiDiGraph()
    G.add_edge("b", "a", key="part_of")
    loader.load_networkx(G, use_noindex=False, build_globals=False)
    (edge_list,), _ = loader.load_edges.call_args
    assert edge_list == [("b", "part_of", "a", None)]
