"""Spec 254 FR-002: a Cypher CREATE or MERGE of an edge between matched nodes adopts a temporal edge's marked row.

The live behaviour is in ``tests/e2e/test_254_purge_e2e.py``; this pins the statement's shape without IRIS.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.constants import TEMPORAL_MARKED_ROW
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

QUERIES = [
    "MATCH (a {node_id: 'a'}), (b {node_id: 'b'}) CREATE (a)-[:P]->(b)",
    "MATCH (a {node_id: 'a'}), (b {node_id: 'b'}) MERGE (a)-[r:P]->(b) ON MATCH SET r.seen = 1",
    "MATCH (a)-[:Q]->(b) WITH a, b CREATE (a)-[:P {w: 1}]->(b)",
    "UNWIND [1, 2] AS i MATCH (a:A), (b:B) CREATE (a)-[:R {i: i}]->(b)",
    "MATCH (a:A) WITH a, a.name AS nm MATCH (b:B) CREATE (a)-[:R {n: nm}]->(b)",
]


def _stmts(cypher):
    q = translate_to_sql(parse_query(cypher))
    return list(zip(q.sql, q.parameters))


def _markers(sql):
    return len(re.findall(r"\?", re.sub(r"'(?:[^']|'')*'", "''", sql)))


def _is_adopt(sql):
    return "UPDATE" in sql and TEMPORAL_MARKED_ROW in sql


def _adopt_index(stmts):
    return next(i for i, (s, _p) in enumerate(stmts) if _is_adopt(s))


@pytest.mark.parametrize("query", QUERIES)
def test_the_adopt_runs_before_the_edge_insert_and_every_other_edge_write(query):
    stmts = _stmts(query)
    i = _adopt_index(stmts)
    insert = next(j for j, (s, _p) in enumerate(stmts) if re.search(r"INSERT INTO (\w+\.)?rdf_edges", s))
    assert i < insert
    # a MERGE's ON MATCH SET of the relationship runs after the adopt, so the adopt cannot overwrite it
    assert all(j > i for j, (s, _p) in enumerate(stmts) if j != i and re.match(r"(WITH .*?\)\s*)?UPDATE (\w+\.)?rdf_edges", s, re.S))


@pytest.mark.parametrize("query", QUERIES)
def test_the_adopts_markers_and_parameters_agree(query):
    stmts = _stmts(query)
    sql, params = stmts[_adopt_index(stmts)]
    assert _markers(sql) == len(params)


def test_literal_properties_become_the_adopted_rows_qualifiers():
    stmts = _stmts("MATCH (a {node_id: 'a'}), (b {node_id: 'b'}) CREATE (a)-[:P {w: 1}]->(b)")
    sql, params = stmts[_adopt_index(stmts)]
    assert "SET qualifiers = ?" in sql and '{"w": "1"}' in params


def test_an_unwound_value_is_a_literal_of_the_adopted_row():
    adopts = [p for s, p in _stmts(QUERIES[3]) if _is_adopt(s)]
    assert [p[0] for p in adopts] == ['{"i": "1"}', '{"i": "2"}']


def test_a_with_bound_property_leaves_the_adopted_row_without_qualifiers():
    stmts = _stmts(QUERIES[4])
    sql, _params = stmts[_adopt_index(stmts)]
    assert "SET qualifiers = NULL" in sql
