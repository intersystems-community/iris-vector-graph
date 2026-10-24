"""Which reads approx_count_distinct's HLL route answers, and which are counted exactly (KNOWN_ISSUES, 2026-10-24).

The route (`Graph.KG.NKGAccel.CountDistinctKHop` over `^NKG`) answers one count: the distinct far ends of one
variable-length hop, at least one hop away, from a start pinned by `node_id`, by types, maximum hops and direction, in
no graph, as the query's one row. `_approx_hll_read` takes that read from the parsed query, or names what else the
query asks for; anything named is counted exactly (tests/e2e/test_approx_count_distinct_checks_e2e.py holds the
answers on IRIS). The source comes from the pattern, never from a scan of the statement's bound values: that scan
took an UNWIND's string, a WHERE's property key or an unused parameter for the source.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

PLAIN = "MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n"


def _read(q, params=None):
    return IRISGraphEngine._approx_hll_read(parse_query(q), {"s": "x"} if params is None else params)


@pytest.mark.parametrize(
    "q,params,read",
    [
        (PLAIN, None, {"source": "x", "types": ["A"], "max_hops": 2, "direction": "out", "column": "n"}),
        (
            "MATCH (s {node_id: 'lit'})<-[:A|B*..3]-(m) RETURN approx_count_distinct(m) AS c",
            {},
            {"source": "lit", "types": ["A", "B"], "max_hops": 3, "direction": "in", "column": "c"},
        ),
        (
            "MATCH (s)-[*1..2]-(m) WHERE s.node_id = $s RETURN approx_count_distinct(m) AS n",
            None,
            {"source": "x", "types": [], "max_hops": 2, "direction": "both", "column": "n"},
        ),
    ],
    ids=["param-pin", "literal-pin-incoming", "where-pin-undirected"],
)
def test_the_plain_shape_is_read_from_the_pattern(q, params, read):
    assert _read(q, params) == (read, None)


@pytest.mark.parametrize(
    "q,params",
    [
        ("USE GRAPH 'g' " + PLAIN, None),
        ("MATCH (s {node_id: $s})-[:A*0..2]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*2..2]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s:L {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m:B) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) WHERE m.k = 1 RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN s.node_id AS id, approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(s) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(s) RETURN approx_count_distinct(s) AS n", None),
        ("MATCH (s {node_id: $s})-[:A]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A]->(x)-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) WITH m RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: 'x'})-[:A*1..2]->(m) UNWIND ['q'] AS z RETURN approx_count_distinct(m) AS n", {}),
        ("MATCH (s)-[:A*1..2]->(m) WHERE s.id = 'x' RETURN approx_count_distinct(m) AS n", {}),
        ("MATCH (s {id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s)-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", {"u": "foo"}),
        ("MATCH ({node_id: 'x'})-[:A*1..2]->(m) RETURN count(DISTINCT m) AS n", {}),
        ("MATCH (s {node_id: 5})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", {}),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", {}),
        (PLAIN + " LIMIT 0", None),
        (PLAIN + " SKIP 1", None),
        (PLAIN + " UNION " + PLAIN, None),
        ("MATCH (s {node_id: $s})-[:A*1..2 {w: 1}]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("OPTIONAL MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", None),
        ("MATCH (s {node_id: $s})-[:A*1..2]->(m) RETURN count(DISTINCT m) AS n", None),
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
        "self-loop",
        "fixed-hop",
        "two-hops",
        "with-stage",
        "unwind",
        "where-id-pin",
        "id-prop-pin",
        "unpinned-unused-param",
        "anonymous-start-no-aggregate",
        "integer-id",
        "unbound-param",
        "limit",
        "skip",
        "union",
        "rel-property",
        "optional",
        "not-the-aggregate",
    ],
)
def test_a_read_the_route_cannot_answer_is_named(q, params):
    read, reason = _read(q, params)
    assert read is None and reason


def _engine():
    e = IRISGraphEngine(MagicMock())
    e.execute_cypher = MagicMock(return_value=IVGResult(columns=["n"], rows=[[3]]))
    return e


def _dispatch(e, q, params=None):
    m = re.search(r"\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)", q, re.IGNORECASE)
    return e._execute_approx_count_distinct(q, {"s": "x"} if params is None else params, m)


def test_a_named_read_is_counted_exactly_without_calling_the_route():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod") as route:
        r = _dispatch(e, "MATCH (s {node_id: $s})-[:A]->(m) RETURN approx_count_distinct(m) AS n")
    route.assert_not_called()
    assert e.execute_cypher.call_args.args[0] == "MATCH (s {node_id: $s})-[:A]->(m) RETURN count(DISTINCT m) AS n"
    assert r.rows == [[3]] and any("counted exactly" in w for w in r.metadata.warnings)


def test_an_unresolved_source_is_counted_exactly_not_zeroed():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod") as route:
        r = _dispatch(e, "MATCH (s)-[:A*1..2]->(m) RETURN approx_count_distinct(m) AS n", {"u": "foo"})
    route.assert_not_called()
    assert r.rows == [[3]] and any("source" in w for w in r.metadata.warnings)


def test_a_query_that_does_not_parse_goes_to_the_exact_path():
    e = _engine()
    e.execute_cypher.return_value = IVGResult(columns=[], rows=[], error="parse fail")
    r = _dispatch(e, "MATCH (s {node_id: $s})-[:A*1..2->(m) RETURN approx_count_distinct(m) AS n")
    assert r.error == "parse fail"


def test_the_route_gets_the_read():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 5, "registers": 256}') as route:
        r = _dispatch(e, "MATCH (s {node_id: 'lit'})<-[:A|B*..3]-(m) RETURN approx_count_distinct(m) AS c", {})
    e.execute_cypher.assert_not_called()
    assert route.call_args.args[1:] == ("Graph.KG.NKGAccel", "CountDistinctKHop", "lit", '["A", "B"]', 3, "in")
    assert r.columns == ["c"] and r.rows == [[5]] and any("HLL" in w for w in r.metadata.warnings)


def test_no_types_send_no_predicates():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 5}') as route:
        _dispatch(e, "MATCH (s {node_id: $s})-[*1..2]-(m) RETURN approx_count_distinct(m) AS n")
    assert route.call_args.args[3:] == ("x", "", 2, "both")


def test_an_estimate_of_0_is_counted_exactly():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 0}'):
        r = _dispatch(e, PLAIN)
    assert r.rows == [[3]] and any("answered 0" in w for w in r.metadata.warnings)


def test_a_failed_route_is_counted_exactly():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("IRIS error")):
        r = _dispatch(e, PLAIN)
    assert r.rows == [[3]] and any("failed" in w for w in r.metadata.warnings)


def test_an_unnamed_column_takes_this_route_too():
    """Without `AS`, the query went to the translator's old route, which took the label for the source (4.4.0 gate)."""
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 5}') as route:
        r = IRISGraphEngine.execute_cypher(e, "MATCH (s {node_id: $s})-[*1..2]->(m) RETURN approx_count_distinct(m)", {"s": "x"})
    assert route.call_args.args[3] == "x"
    assert r.columns == ["approx_count_distinct(m)"] and r.rows == [[5]]


def test_an_unnamed_column_counted_exactly_keeps_its_name():
    e = _engine()
    e.execute_cypher.return_value = IVGResult(columns=["count(DISTINCT m)"], rows=[[2]])
    with patch("iris_vector_graph.schema._call_classmethod") as route:
        r = IRISGraphEngine.execute_cypher(e, "MATCH (s {node_id: $s})-[*1..2]->(m:B) RETURN approx_count_distinct(m)", {"s": "x"})
    route.assert_not_called()
    assert e.execute_cypher.call_args.args[0] == "MATCH (s {node_id: $s})-[*1..2]->(m:B) RETURN count(DISTINCT m)"
    assert r.columns == ["approx_count_distinct(m)"] and r.rows == [[2]]


def test_only_the_unnamed_approx_column_is_renamed():
    e = _engine()
    e.execute_cypher.return_value = IVGResult(columns=["a", "count(DISTINCT x)"], rows=[[2, 1]])
    q = "MATCH (s {node_id: $s})-[*1..2]->(m:B), (s)-->(x) RETURN approx_count_distinct(m) AS a, count(DISTINCT x)"
    r = IRISGraphEngine.execute_cypher(e, q, {"s": "x"})
    assert r.columns == ["a", "count(DISTINCT x)"]


@pytest.mark.parametrize(
    "call,column",
    [
        ("approx_count_distinct( m )", "count(DISTINCT  m )"),
        ("APPROX_COUNT_DISTINCT(m)", "count(DISTINCT m)"),
        ("approx_count_distinct (m)", "count(DISTINCT m)"),
    ],
)
def test_an_unnamed_column_keeps_the_name_the_query_wrote(call, column):
    """An unnamed column is named by its text as written; the exact path's column is renamed back to that text."""
    e = _engine()
    e.execute_cypher.return_value = IVGResult(columns=[column], rows=[[2]])
    with patch("iris_vector_graph.schema._call_classmethod"):
        r = IRISGraphEngine.execute_cypher(e, f"MATCH (s {{node_id: $s}})-[*1..2]->(m:B) RETURN {call}", {"s": "x"})
    assert r.columns == [call]


def test_the_hll_route_names_an_unnamed_column_by_its_text():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", return_value='{"estimate": 5}'):
        r = IRISGraphEngine.execute_cypher(e, "MATCH (s {node_id: $s})-[*1..2]->(m) RETURN APPROX_COUNT_DISTINCT( m )", {"s": "x"})
    assert r.columns == ["APPROX_COUNT_DISTINCT( m )"]


def test_a_property_argument_is_counted_exactly():
    e = _engine()
    with patch("iris_vector_graph.schema._call_classmethod") as route:
        IRISGraphEngine.execute_cypher(e, "MATCH (s {node_id: $s})-[*1..2]->(m) RETURN approx_count_distinct(m.name) AS n", {"s": "x"})
    route.assert_not_called()
    assert e.execute_cypher.call_args.args[0] == "MATCH (s {node_id: $s})-[*1..2]->(m) RETURN count(DISTINCT m.name) AS n"

