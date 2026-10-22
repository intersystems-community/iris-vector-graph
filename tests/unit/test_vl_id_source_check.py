"""The BFS route of a variable-length read from `{id: $x}`: it starts only from the node the MATCH names, and counts
each reached node once.

DRKG's D_Q2 (`MATCH (n:Entity {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m)`) answered 61,331, the sum of the
distinct nodes at each depth, where 45,993 are within two hops: the count route counted BFS's rows, one per node per
depth. It also answered for x whatever the pattern said of x: the translated MATCH reads `{id: x}` as the node x
with the pattern's labels and an `id` property equal to x (create_node writes one; a bulk load does not), and the
route took the id and read neither. The translator now marks a source named by `{id: ..}` (`src_id_prop`), and the
route checks that node before walking from it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult


def _translate(cypher, params=None):
    return translate_to_sql(parse_query(cypher), params or {"x": "a"})


class _Store:
    _schema_prefix = "Graph_KG"

    def __init__(self, labels=("L",), id_prop="a"):
        self.conn = MagicMock()
        self.labels, self.id_prop = list(labels), id_prop
        self.walked = []
        cursor = self.conn.cursor.return_value
        cursor.execute.side_effect = self._execute

    def _execute(self, sql, args):
        # id_holders' lookup: the `id` value, the node, then the labels it must carry
        value, node, labels = args[0], args[1], args[2:]
        held = self.id_prop == value and set(labels) <= set(self.labels)
        self.conn.cursor.return_value.fetchall.return_value = [(node,)] if held else []

    def execute_bfs(self, source_id, predicates, max_hops, direction, max_results, *, graph=None):
        self.walked.append(source_id)
        # c is one hop and two hops out, d two hops out by two paths
        rows = [["b", 1, "R"], ["c", 1, "R"], ["c", 2, "R"], ["d", 2, "R"], ["d", 2, "R"]]
        return IVGResult(columns=["id", "hops", "pred"], rows=rows)


def _engine(store):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = store
    engine._nkg_dirty = False
    engine._edge_graphs = lambda: []
    return engine


def test_the_translator_marks_a_source_named_by_its_id_property():
    assert _translate("MATCH (s:L {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt").var_length_paths[0]["src_id_prop"]
    # a variable named like a parameter binds the source too, with no property behind it
    vl = _translate("MATCH (x)-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt").var_length_paths[0]
    assert not vl.get("src_id_prop")


def test_a_count_counts_each_reached_node_once():
    store = _Store()
    q = _translate("MATCH (s:L {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt")
    r = _engine(store)._route_var_length(q, {"x": "a"})
    assert r.rows == [[3]] and r.columns == ["cnt"], (r.columns, r.rows)


def test_a_start_without_the_label_walks_nowhere():
    store = _Store(labels=("Other",))
    q = _translate("MATCH (s:L {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt")
    r = _engine(store)._route_var_length(q, {"x": "a"})
    assert r.rows == [[0]] and r.columns == ["cnt"] and store.walked == [], (r.columns, r.rows, store.walked)


def test_a_start_without_its_id_property_walks_nowhere():
    store = _Store(id_prop=None)
    for cypher in (
        "MATCH (s:L {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt",
        "MATCH (s {id: $x})-[*1..2]->(m) RETURN DISTINCT m.name",  # aliased, it expands in SQL instead
    ):
        r = _engine(store)._route_var_length(_translate(cypher), {"x": "a"})
        assert r.rows in ([[0]], []) and store.walked == [], (cypher, r.rows, store.walked)


def test_a_start_that_holds_both_is_walked():
    store = _Store()
    q = _translate("MATCH (s:L {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt")
    _engine(store)._route_var_length(q, {"x": "a"})
    assert store.walked == ["a"]


class _IdPropStore(_Store):
    """rdf_props answers `id` = x with the nodes holding it (not x itself), where they carry the labels asked for."""

    def __init__(self, holders, **kw):
        super().__init__(**kw)
        self.holders = holders
        self.cursor = self.conn.cursor.return_value

    def _execute(self, sql, args):
        held = set(args[1:]) <= set(self.labels)
        self.conn.cursor.return_value.fetchall.return_value = [(h,) for h in self.holders] if held else []


def test_a_where_on_the_id_property_starts_from_its_holders():
    # `WHERE s.id = $x` pins the start (the translator routes it to BFS), and the statement reads it as the `id`
    # property: the start is the node holding it, not every node with the label
    from iris_vector_graph._engine.query import extract_vlp_source_ids

    q = _translate("MATCH (s:L)-[*1..2]->(m) WHERE s.id = $x RETURN count(DISTINCT m) AS c")
    vl = q.var_length_paths[0]
    store = _IdPropStore(["h1"])
    ids = extract_vlp_source_ids(
        sql_query=q, source_labels=vl["source_labels"], source_alias=vl["source_alias"],
        target_alias=vl["target_alias"], store=store,
    )
    assert ids == ["h1"], ids
    sql, args = store.cursor.execute.call_args[0]
    assert '"key" = \'id\'' in sql and list(args) == ["a", "L"], (sql, args)
    # a holder without the label is no start
    store = _IdPropStore(["h1"], labels=("Other",))
    assert extract_vlp_source_ids(
        sql_query=q, source_labels=vl["source_labels"], source_alias=vl["source_alias"],
        target_alias=vl["target_alias"], store=store,
    ) == []


def test_a_number_held_by_the_id_property_is_its_text():
    from iris_vector_graph._engine.query import bound_id_property

    for cypher, params in (("MATCH (s:L)-[*1..2]->(m) WHERE s.id = $x RETURN count(DISTINCT m) AS c", {"x": 5}),
                           ("MATCH (s:L)-[*1..2]->(m) WHERE s.id = 5 RETURN count(DISTINCT m) AS c", {})):
        q = _translate(cypher, params)
        vl = q.var_length_paths[0]
        assert bound_id_property(q.sql, q.parameters[0] if q.parameters else [], vl["source_alias"]) == "5", q.sql


def test_a_count_is_named_as_the_sql_path_names_it():
    # the labeled route named it "count" (or the column map's first key) where the statement says `AS cnt`
    from iris_vector_graph._engine.query import _count_column

    aliased = _translate("MATCH (s:L)-[:R*1..2]->(t:L {id: $x}) RETURN count(DISTINCT s) AS cnt")
    assert _count_column(aliased) == "cnt"
    # unaliased, the SQL path renames its alias (count_DISTINCT_s_) through the column map
    bare = _translate("MATCH (s:L)-[:R*1..2]->(t:L {id: $x}) RETURN count(DISTINCT s)")
    assert _count_column(bare) == "count(DISTINCT s)"
