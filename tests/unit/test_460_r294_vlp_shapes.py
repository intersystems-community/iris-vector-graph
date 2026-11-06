"""4.6.0 R294: the variable-length routes read the relationship each hop walked, and the RETURNs the engine's routes
cannot answer expand in SQL. The live checks are tests/e2e/test_460_r294_vlp_shapes_e2e.py."""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _engine(cursor):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._schema_prefix = "Graph_KG"
    store = MagicMock()
    store.conn.cursor.return_value = cursor
    store._schema_prefix = "Graph_KG"
    eng._store = store
    return eng


def test_the_walk_keeps_each_hops_edge_id_so_parallel_edges_are_two_paths():
    """a -knows-> b twice (edge 11 and 12), b -knows-> c (13)."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[("a", "b", "knows", 11), ("a", "b", "knows", 12)], [("b", "c", "knows", 13)]]
    paths = _engine(cursor)._bfs_with_paths("a", ["knows"], 2, "out", graph="", edge_ids=True)
    assert sorted(paths) == sorted([
        (["a", "b"], ["knows"], [11]), (["a", "b"], ["knows"], [12]),
        (["a", "b", "c"], ["knows", "knows"], [11, 13]), (["a", "b", "c"], ["knows", "knows"], [12, 13])])
    assert all("edge_id" in c.args[0] for c in cursor.execute.call_args_list)


def test_without_edge_ids_the_walk_answers_as_before():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[("a", "b", "knows")], []]
    assert _engine(cursor)._bfs_with_paths("a", [], 2, "out", graph="") == [(["a", "b"], ["knows"])]
    assert not any("edge_id" in c.args[0] for c in cursor.execute.call_args_list)


def test_the_qualifiers_of_a_walked_edge_are_read_by_its_id():
    cursor = MagicMock()
    cursor.fetchone.return_value = ('{"w": 7}',)
    assert _engine(cursor)._vlp_qualifiers("a", "knows", "b", "out", "", 12) == '{"w": 7}'
    (sql, args), = [c.args for c in cursor.execute.call_args_list]
    assert "WHERE edge_id = ?" in sql and args == [12]


def _tr(q):
    return translate_to_sql(parse_query(q), {})


def test_return_of_the_hops_relationship_variable_expands_in_sql():
    for q in ("MATCH p = (a {node_id: 'x'})-[r:knows*1..2]->(b) RETURN r",
              "MATCH p = (a {node_id: 'x'})-[r:knows*1..2]->(b) RETURN b.name AS n, r"):
        assert not _tr(q).var_length_paths, q
    # and so do the path route's own items since 4.6.0 R308 (the route listed a far end once per length)
    assert not _tr("MATCH p = (a {node_id: 'x'})-[:knows*1..2]->(b) RETURN b, length(p) AS l").var_length_paths
