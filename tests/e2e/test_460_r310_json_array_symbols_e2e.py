"""4.6.0 R310 follow-up, live (fixes66): a list holding a string with a symbol answers on 2026.3, both drivers.

`JSON_ARRAY('☕')` fails at Query Open on 2026.3 (tests/unit/test_460_r310_json_array_symbols.py). The answers are the
literals themselves, written by hand.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.oracle.values import VALUES

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

CAFE = "café ☕ 日本"


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.execute_cypher("MATCH (n:S310) DETACH DELETE n", raise_on_error=True)
    e.execute_cypher("CREATE (:S310 {x: 1})", raise_on_error=True)
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:S310) DETACH DELETE n")


def _rows(e, q):
    r = e.execute_cypher(q, raise_on_error=True)
    assert not r.error, r.error
    return [list(row) for row in r.rows]


def test_a_with_list_of_a_symbol_and_a_duration(eng):
    assert _rows(eng, f"WITH ['{CAFE}', duration('P1D')] AS l UNWIND l AS x RETURN x") == [[CAFE], ["P1D"]]


def test_every_oracle_literal_in_one_list_reads_back(eng):
    lits = [v for v in VALUES if v.literal_writable]
    got = _rows(eng, f"WITH [{', '.join(v.cypher for v in lits)}] AS l RETURN size(l) AS n, l[{lits.index(next(v for v in lits if v.key == 'suni'))}] AS u")
    assert got == [[len(lits), CAFE]]


def test_a_list_of_a_node_and_a_symbol(eng):
    [[n, s]] = _rows(eng, "MATCH (n:S310) WITH [n, '☕'] AS l RETURN l[0].x AS n, l[1] AS s")
    assert (n, s) == (1, "☕")


def _list(cell):
    # a RETURNed list expression comes back as its JSON text on release too (`RETURN [n.x, 'a']` answers '[1,"a"]',
    # reported apart): decoded here, so what this pins is the elements
    return json.loads(cell) if isinstance(cell, str) else cell


def test_an_expression_with_a_symbol_inside_a_list(eng):
    [[cell]] = _rows(eng, "MATCH (n:S310) RETURN [n.x, 'a' + '☕'] AS l")
    assert _list(cell) == [1, "a☕"]


def test_keys_of_a_map_literal_with_a_symbol(eng):
    [[cell]] = _rows(eng, "MATCH (n:S310) RETURN keys({a: 1, `☕`: 2}) AS k")
    assert _list(cell) == ["a", "☕"]
