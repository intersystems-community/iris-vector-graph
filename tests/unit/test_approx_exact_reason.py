"""Which reads approx_count_distinct's HLL route answers, and which are counted exactly (KNOWN_ISSUES, 2026-10-24).

The route (`Graph.KG.NKGAccel.CountDistinctKHop` over `^NKG`) answers one count: the distinct ends of one
variable-length hop from one id-pinned start, by types, maximum hops and direction, from at least one hop away, in no
graph. `_approx_exact_reason` names what else a query asks for; anything named is counted exactly
(tests/e2e/test_approx_count_distinct_checks_e2e.py holds the answers on IRIS).
"""

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

PLAIN = "MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n"


def _reason(q, var="m"):
    return IRISGraphEngine._approx_exact_reason(parse_query(q), var)


def test_the_plain_shape_takes_the_hll_route():
    assert _reason(PLAIN) is None
    assert _reason("MATCH (s {node_id: $s})-[:A|B*..3]-(m) RETURN approx_count_distinct(m) AS n") is None


@pytest.mark.parametrize(
    "q,var",
    [
        ("USE GRAPH 'g' " + PLAIN, "m"),
        ("MATCH (s {node_id: $s})-[:A*0..2]->(m) RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A*2..2]->(m) RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s:L {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m:B) RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) WHERE m.k = 1 RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN s.node_id AS id, approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(s) AS n", "s"),
        ("MATCH (s {node_id: $s})-[:A]->(m) RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A]->(x)-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", "m"),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) WITH m RETURN approx_count_distinct(m) AS n", "m"),
    ],
    ids=[
        "use-graph",
        "minimum-0",
        "minimum-2",
        "start-label",
        "far-label",
        "far-where",
        "another-column",
        "counts-the-start",
        "fixed-hop",
        "two-hops",
        "with-stage",
    ],
)
def test_a_read_the_route_cannot_answer_is_named(q, var):
    assert _reason(q, var)


def _engine():
    e = IRISGraphEngine(MagicMock())
    e.execute_cypher = MagicMock(return_value=IVGResult(columns=["n"], rows=[[3]]))
    return e


def _dispatch(e, q):
    import re

    m = re.search(r"\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)", q, re.IGNORECASE)
    return e._execute_approx_count_distinct(q, {"s": "x"}, m)


def test_a_named_read_is_counted_exactly_without_calling_the_route():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod") as route:
        r = _dispatch(e, "MATCH (s {node_id: $s})-[:A]->(m) RETURN approx_count_distinct(m) AS n")
    route.assert_not_called()
    assert e.execute_cypher.call_args.args[0] == "MATCH (s {node_id: $s})-[:A]->(m) RETURN count(DISTINCT m) AS n"
    assert r.rows == [[3]] and any("counted exactly" in w for w in r.metadata.warnings)


def test_an_estimate_of_0_is_counted_exactly():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 0}'):
        r = _dispatch(e, PLAIN)
    assert r.rows == [[3]] and any("holds no count" in w for w in r.metadata.warnings)


def test_an_estimate_keeps_the_hll_route():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 5, "registers": 256}'):
        r = _dispatch(e, PLAIN)
    e.execute_cypher.assert_not_called()
    assert r.rows == [[5]] and any("HLL" in w for w in r.metadata.warnings)
