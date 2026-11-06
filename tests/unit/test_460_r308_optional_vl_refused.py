"""4.6.0 R308, offline: an OPTIONAL MATCH variable-length hop the statement cannot read in one pass goes to the walk
from its start's rows, never to the engine's labeled routes.

The translator leaves such a hop (a WHERE on its new end, a map on a node of it, a second hop) to the engine, whose
labeled routes answered rows of the wrong width with the start nulled:
`MATCH (a:L {name: 'cat'}) OPTIONAL MATCH (a)-[:R*1..2]->(b) WHERE b.name = 'eve' RETURN a.name, b.name` answered
`[['dan'], ['eve'], [None, None], [None, None]]` (hand answer `[('cat', 'eve')]`, measured on release-4.6.0 and with
R308's other fixes, enterprise IVG460FXOC). The shapes that work (a lone hop from a bound start to a plain or labelled
new end, `OPTIONAL MATCH (a:L {name: 'x'})-[*]->(b)`) expand in SQL. The walked shapes' answers are
tests/unit/test_460_r308_optional_var_length.py's and tests/e2e/test_460_r308_optional_var_length_e2e.py's.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import QueryMixin
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

REFUSED = [
    "MATCH (a:L {name: 'cat'}) OPTIONAL MATCH (a)-[:R*1..2]->(b) WHERE b.name = 'eve' RETURN a.name, b.name",
    "MATCH (a:L {name: 'cat'}) OPTIONAL MATCH (a)-[:R*1..2]->(b {name: 'dan'}) RETURN a.name, b.name",
]
EXPANDED = [
    "MATCH (a:L {name: 'cat'}) OPTIONAL MATCH (a)-[:R*1..2]->(b) RETURN a.name, b.name",
    "MATCH (a:L) OPTIONAL MATCH (a)-[:R*1..2]->(b:M) RETURN a.name, b.name",
    "OPTIONAL MATCH (a:L {name: 'x'})-[:R*1..2]->(b) RETURN a.name, b.name",
]


def _route(q):
    sq = translate_to_sql(parse_query(q), {})
    stub = SimpleNamespace(_nkg_dirty=False)
    return sq, (lambda: QueryMixin._route_var_length(stub, sq, {}))


@pytest.mark.parametrize("q", REFUSED)
def test_a_shape_the_statement_cannot_read_is_walked_from_its_starts(q):
    sq, _route_ = _route(q)
    assert sq.var_length_paths and sq.var_length_paths[0]["optional"], sq.sql
    assert sq.var_length_paths[0]["optional_paths_sql"]


@pytest.mark.parametrize("q", EXPANDED)
def test_a_shape_the_statement_reads_expands_in_sql(q):
    assert not translate_to_sql(parse_query(q), {}).var_length_paths
