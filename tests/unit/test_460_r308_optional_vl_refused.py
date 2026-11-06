"""4.6.0 R308, offline: an OPTIONAL MATCH variable-length hop the statement cannot read raises instead of answering.

The translator leaves such a hop (a WHERE on its new end, a map on a node of it, a start the OPTIONAL MATCH binds, a
second hop) to the engine's labeled routes, which answered rows of the wrong width with the start nulled:
`MATCH (a:L {name: 'cat'}) OPTIONAL MATCH (a)-[:R*1..2]->(b) WHERE b.name = 'eve' RETURN a.name, b.name` answered
`[['dan'], ['eve'], [None, None], [None, None]]` (hand answer `[('cat', 'eve')]`, measured on release-4.6.0 and with
R308's other fixes, enterprise IVG460FXOC). The shapes that work (a lone hop from a bound start to a plain or labelled
new end, `OPTIONAL MATCH (a:L {name: 'x'})-[*]->(b)`) expand in SQL and are not refused.
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
def test_a_shape_the_statement_cannot_read_raises(q):
    sq, route = _route(q)
    assert sq.var_length_paths and sq.var_length_paths[0]["optional"], sq.sql
    with pytest.raises(ValueError, match="OPTIONAL MATCH"):
        route()


@pytest.mark.parametrize("q", EXPANDED)
def test_a_shape_the_statement_reads_expands_in_sql(q):
    assert not translate_to_sql(parse_query(q), {}).var_length_paths
