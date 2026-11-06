"""4.6.0 R310, live: an UNWIND over a list computed from a long collected node list (fixes64).

On 2026.3 the TCK setup of Match4 [4] failed SQLCODE -400 `<SUBSCRIPT>` in `^||%t`, keyed by the collected list's JSON
text, so nothing was written (the old harness dropped the error and ran the scenario on an empty graph). The answers
are worked by hand: 2 + 20 nodes in the order start, 1..20, end; `UNWIND range(0, 20)` pairs neighbours, so 21 `T`
relationships make one chain, whose only end reachable from `start` with var 'end' is `end`. Each node carries the
label L310 (its JSON in the collected list is ~140 characters, so the list is ~3000).
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

SETUP = """CREATE (a:L310 {var: 'start'}), (b:L310 {var: 'end'})
WITH *
UNWIND range(1, 20) AS i
CREATE (n:L310 {var: i})
WITH a, b, [a] + collect(n) + [b] AS nodeList
UNWIND range(0, size(nodeList) - 2, 1) AS i
WITH nodeList[i] AS n1, nodeList[i+1] AS n2
CREATE (n1)-[:T]->(n2)"""


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.execute_cypher("MATCH (n:L310) DETACH DELETE n", raise_on_error=True)
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:L310) DETACH DELETE n")


def _rows(e, q):
    r = e.execute_cypher(q, raise_on_error=True)
    assert not r.error, r.error
    return [list(row) for row in r.rows]


def test_the_match4_setup_builds_the_chain(eng):
    eng.execute_cypher(SETUP, raise_on_error=True)
    assert _rows(eng, "MATCH (n:L310) RETURN count(n) AS c") == [[22]]
    assert _rows(eng, "MATCH (:L310)-[r:T]->(:L310) RETURN count(r) AS c") == [[21]]
    # the chain's order: start -> 1 -> ... -> 20 -> end
    assert _rows(eng, "MATCH (a:L310 {var: 'start'})-[:T]->(b) RETURN b.var AS v") == [[1]]
    assert _rows(eng, "MATCH (a:L310 {var: 20})-[:T]->(b) RETURN b.var AS v") == [["end"]]


def test_the_match4_query_answers_the_end(eng):
    eng.execute_cypher(SETUP, raise_on_error=True)
    assert _rows(eng, "MATCH (n:L310 {var: 'start'})-[:T*]->(m:L310 {var: 'end'}) RETURN m.var AS v") == [["end"]]


def test_a_range_over_a_long_collected_list_counts_its_elements(eng):
    eng.execute_cypher(SETUP, raise_on_error=True)
    assert _rows(
        eng, "MATCH (n:L310) WITH collect(n) AS ns UNWIND range(0, size(ns) - 1) AS i RETURN count(i) AS c"
    ) == [[22]]


# Reads of a collected node held as its JSON (found beside Match4 [4]): each answered null for every node. By hand,
# three L310 nodes var 1, 2, 3 collected in var order, so element i has var i + 1.
ORDERED = "MATCH (n:L310) WITH n ORDER BY n.var WITH collect(n) AS ns "


@pytest.fixture()
def three(eng):
    eng.execute_cypher("UNWIND [1, 2, 3] AS i CREATE (:L310 {var: i})", raise_on_error=True)
    return eng


@pytest.mark.parametrize("query, expected", [
    (ORDERED + "RETURN ns[0].var AS v", [[1]]),
    (ORDERED + "RETURN ns[2].var AS v", [[3]]),
    (ORDERED + "UNWIND range(0, size(ns) - 1) AS i RETURN ns[i].var AS v ORDER BY v", [[1], [2], [3]]),
    (ORDERED + "UNWIND range(0, size(ns) - 1) AS i WITH ns[i] AS m RETURN m.var AS v ORDER BY v", [[1], [2], [3]]),
    (ORDERED + "UNWIND ns AS x WITH x RETURN x.var AS v ORDER BY v", [[1], [2], [3]]),
    (ORDERED + "UNWIND ns AS x WITH x WHERE x.var > 1 RETURN x.var AS v ORDER BY v DESC", [[3], [2]]),
])
def test_a_property_of_a_collected_node(three, query, expected):
    assert _rows(three, query) == expected


def test_the_node_id_of_an_indexed_collected_node_is_its_id(three):
    [[nid]] = _rows(three, "MATCH (n:L310 {var: 1}) RETURN n.node_id AS v")
    assert _rows(three, ORDERED + "RETURN ns[0].node_id AS v") == [[nid]]


@pytest.mark.parametrize("query, expected", [
    # 2026.3 pages with TOP / ROW_NUMBER over a derived table, which an UNWIND's column was read outside of: -23
    ("WITH [3, 1, 2] AS l UNWIND l AS i RETURN i ORDER BY i LIMIT 2", [[1], [2]]),
    ("WITH [3, 1, 2] AS l UNWIND l AS i RETURN i ORDER BY i DESC SKIP 1 LIMIT 1", [[2]]),
    ("WITH [3, 1, 2] AS l UNWIND l AS i RETURN i ORDER BY i SKIP 1", [[2], [3]]),
])
def test_an_unwound_value_sorted_and_paged(eng, query, expected):
    assert _rows(eng, query) == expected
