"""4.6.0 R310, live: a grouping read over an UNWIND of a long collected list answers on 2026.3 (fixes64).

On IRIS 2026.3 such a read failed `-400 <SUBSCRIPT>` once the list's text passed 511 characters (30 nodes); the engine
now runs it once more with the list read by position (tests/unit/test_460_r310_unwind_by_index.py). The answers are
worked by hand from the graph each test builds; none is read from IVG:

- `grouped`: nodes `L310D` with `v` 1..N and `k = v % 3`; `p = v` except where `v % 5 == 0`, where `p` is unset;
  three more nodes with `k = 9` and no `p`, so group 9 collects an empty list and unwinds to no row.
- the value-set test stores each tests/oracle/values.py value the API can write on its own node, and asks the same
  UNWIND with the list read the usual way and by position: the elements must be the same values with the same types.

Run under both drivers (5.4.0 in the repo's .venv, 5.4.1 a fresh install).
"""

from __future__ import annotations

import math
import os

import pytest

from iris_vector_graph.cypher.translator import unwind_by_index
from iris_vector_graph.engine import IRISGraphEngine
from tests.oracle.values import VALUES

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for label in ("L310D", "V310"):
        e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", raise_on_error=True)
    try:
        yield e
    finally:
        for label in ("L310D", "V310"):
            e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n")


def _rows(e, q):
    r = e.execute_cypher(q, raise_on_error=True)
    assert not r.error, r.error
    return [list(row) for row in r.rows]


def _grouped(e, n):
    # Cypher writes v, k and p as integers (the API's bulk_create_nodes stores its properties as text)
    e.execute_cypher(f"UNWIND range(1, {n}) AS v CREATE (:L310D {{v: v, k: v % 3, p: v}})", raise_on_error=True)
    e.execute_cypher("MATCH (n:L310D) WHERE n.v % 5 = 0 REMOVE n.p", raise_on_error=True)
    e.execute_cypher("UNWIND range(1, 3) AS j CREATE (:L310D {k: 9})", raise_on_error=True)
    return _grouped_sums(n)


@pytest.mark.parametrize("n", [30, 2000])
def test_count_and_sum_per_key_of_an_unwound_property_list(eng, n):
    expected = _grouped(eng, n)
    got = _rows(eng, "MATCH (n:L310D) WITH n.k AS k, collect(n.p) AS ps UNWIND ps AS x "
                     "RETURN k, count(x) AS c, sum(x) AS s ORDER BY k")
    assert got == expected


@pytest.mark.parametrize("n", [30, 2000])
def test_count_per_key_of_an_unwound_node_list(eng, n):
    expected = [[k, c] for k, c, _ in _grouped(eng, n)]
    got = _rows(eng, "MATCH (n:L310D) WHERE n.p IS NOT NULL WITH n.k AS k, collect(n) AS ns UNWIND ns AS x "
                     "RETURN k, count(x) AS c ORDER BY k")
    assert got == expected
    # each unwound node is the node: its property reads back
    got = _rows(eng, "MATCH (n:L310D) WHERE n.p IS NOT NULL WITH n.k AS k, collect(n) AS ns UNWIND ns AS x "
                     "RETURN k, sum(x.p) AS s ORDER BY k")
    assert got == [[k, s] for k, _, s in _grouped_sums(n)]


def _grouped_sums(n):
    """By hand: per k = v % 3, the count and the sum of the v in 1..n with v % 5 != 0."""
    out = {}
    for v in range(1, n + 1):
        if v % 5:
            c, s = out.get(v % 3, (0, 0))
            out[v % 3] = (c + 1, s + v)
    return [[k, c, s] for k, (c, s) in sorted(out.items())]


def test_distinct_and_a_later_aggregating_with(eng):
    _grouped(eng, 2000)
    assert _rows(eng, "MATCH (n:L310D) WITH n.k AS k, collect(n) AS ns UNWIND ns AS x RETURN DISTINCT k ORDER BY k") \
        == [[0], [1], [2], [9]]  # group 9's nodes have no p, but collect(n) keeps them
    got = _rows(eng, "MATCH (n:L310D) WITH collect(n.p) AS ps UNWIND ps AS x WITH x % 2 AS odd, count(*) AS c "
                     "RETURN odd, c ORDER BY odd")
    evens = sum(1 for v in range(1, 2001) if v % 5 and v % 2 == 0)
    odds = sum(1 for v in range(1, 2001) if v % 5 and v % 2 == 1)
    assert got == [[0, evens], [1, odds]]


def test_the_fallback_ran_and_logged_once(eng, caplog):
    import logging

    _grouped(eng, 2000)
    q = "MATCH (n:L310D) WITH n.k AS k, collect(n.p) AS ps UNWIND ps AS x RETURN k, count(x) AS c ORDER BY k"
    with caplog.at_level(logging.INFO, logger="iris_vector_graph._engine.query"):
        first = _rows(eng, q)
        second = _rows(eng, q)
    assert first == second
    notes = [r for r in caplog.records if "reading it by position" in r.getMessage()]
    assert len(notes) == 1


def _typed(x):
    if isinstance(x, float) and math.isnan(x):
        return ("float", "nan")
    return (type(x).__name__, repr(x))


def test_elements_read_by_position_have_json_tables_types(eng):
    writable = [v for v in VALUES if v.api_writable]
    eng.bulk_create_nodes([
        {"id": f"r310v:{i:02d}", "labels": ["V310"], "properties": {"i": i, "p": v.py}}
        for i, v in enumerate(writable)
    ])
    q = "MATCH (n:V310) WITH n ORDER BY n.i WITH collect(n.p) AS ps UNWIND ps AS x RETURN x"
    usual = [_typed(r[0]) for r in _rows(eng, q)]
    used: list = []
    with unwind_by_index(used):
        by_position = [_typed(r[0]) for r in _rows(eng, q)]
    assert used, "the read was rewritten"
    assert by_position == usual


def test_literal_elements_read_by_position_have_json_tables_types(eng):
    # without suni: a list literal this long holding it fails at Query Open on either path (<PROPERTY DOES NOT EXIST>,
    # reported apart); alone it reads back on both
    lits = ", ".join(v.cypher for v in VALUES if v.literal_writable and v.key != "suni")
    q = f"WITH [{lits}] AS l UNWIND l AS x RETURN x"
    usual = [_typed(r[0]) for r in _rows(eng, q)]
    used: list = []
    with unwind_by_index(used):
        by_position = [_typed(r[0]) for r in _rows(eng, q)]
    assert used and len(usual) == sum(1 for v in VALUES if v.literal_writable and v.key != "suni")
    assert by_position == usual
