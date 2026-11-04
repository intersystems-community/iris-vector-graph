"""4.6.0 R228: reads of a path's elements, translated (fixes23). The answers are pinned live, on 2026.1 and 2026.3 with
the native planner on and off, in tests/e2e/test_460_r228_path_list_elements_e2e.py; this checks the SQL each shape
used to get wrong.

* ``[r IN relationships(p) | r.km]`` on a fixed-length path iterated the types (``JSON_ARRAY(e2.p, e4.p)``), so every
  property read a type string as JSON and the list came back ``[]``. The elements are now the relationships,
  ``{"type", "props"}`` objects, as a variable-length path's are, and ``type(r)`` reads ``$.type`` of one.
* ``UNWIND nodes(p) AS n RETURN n.name`` read the element (a node id) as JSON: null every row.
* ``head(nodes(p)).name`` rendered ``<list>.name`` (SQLCODE -25); ``[n IN nodes(p) | n {.name}]`` joined rdf_props on
  the comprehension's alias from outside it (-23); ``[n IN nodes(p) | id(n)]`` read a ``node_id`` column the
  comprehension's table does not have (-29).
* A comprehension whose projection is a property dropped null elements (``JSON_ARRAYAGG`` skips NULL).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

P = "MATCH p = (x {node_id:'a'})-[:ROAD]->()-[:ROAD]->(z) "


def _sql(cypher: str) -> str:
    return translate_to_sql(parse_query(cypher)).sql


def _select(cypher: str) -> str:
    return _sql(cypher).split("\nFROM ", 1)[0]


def test_fixed_path_relationships_iterate_relationship_objects():
    sql = _select(P + "RETURN [r IN relationships(p) | r.km] AS v")
    assert "JSON_ARRAY(e2.p, e4.p)" not in sql, sql
    assert "e2.qualifiers" in sql and "e4.qualifiers" in sql, sql
    assert "'$.props'" in sql, sql


def test_type_of_a_fixed_path_relationship_element_reads_its_type():
    sql = _select(P + "RETURN [r IN relationships(p) | type(r)] AS v")
    assert "'$.type'" in sql, sql
    assert ".p)" not in sql.split("JSON_ARRAYAGG(", 1)[1].split(" FROM ", 1)[0], sql


def test_unwound_path_node_reads_its_property_row():
    sql = _select(P + "UNWIND nodes(p) AS n RETURN n.name AS v")
    assert "\"key\" = 'name'" in sql, sql
    assert "'$.name'" not in sql, sql


def test_unwound_path_node_id_is_the_element():
    sql = _select(P + "UNWIND nodes(p) AS n RETURN n.node_id AS v")
    assert sql.startswith("SELECT u") and ".n AS v" in sql, sql


@pytest.mark.parametrize("fn", ["head", "last"])
def test_head_and_last_of_path_nodes_read_the_node(fn):
    sql = _select(P + f"RETURN {fn}(nodes(p)).name AS v")
    assert ").name" not in sql, sql
    assert "\"key\" = 'name'" in sql, sql


def test_map_projection_of_a_path_node_reads_inside_the_comprehension():
    sql = _sql(P + "RETURN [n IN nodes(p) | n {.name}] AS v")
    assert "LEFT JOIN rdf_props" not in sql, sql
    assert "s = lc5.n" in sql, sql


def test_id_of_a_path_node_element_is_the_element():
    sql = _select(P + "RETURN [n IN nodes(p) | id(n)] AS v")
    assert "lc5.node_id" not in sql and "JSON_ARRAYAGG(lc5.n)" in sql, sql


def test_a_property_projection_keeps_null_elements():
    sql = _select(P + "RETURN [n IN nodes(p) | n.missing] AS v")
    assert "CHAR(1)" in sql and "'null'" in sql, sql
