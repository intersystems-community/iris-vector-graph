"""4.7.0 row 22, live: `UNWIND $p AS e` over a parameter list holding a list or map beside a scalar reads each element
as itself, on both routes, through the clauses that follow it.

`UNWIND $p AS e RETURN e` with `$p = ['[2]', [2], true, '1']` answered `[2], [2], '1', '1'`: the parameter's elements
were matched by their JSON_TABLE text, which a nested list shares with the string that spells it, and a boolean beside
a list read as '1'. The translator now reads such a list as `WITH $p AS __l0 UNWIND __l0 AS e`
(tests/unit/test_470_unwind_param_rewrite.py). The answers are openCypher's: an element is the value the list holds,
in the list's order.

    IVG_NAMESPACE=IVG470TIME2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_unwind_param_rewrite_e2e.py
"""

from __future__ import annotations

import time

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

AMBIGUOUS = ["[2]", [2], True, "1"]


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    yield engs
    engs["sql"].execute_cypher("MATCH (n) WHERE n:PRW OR n:PRW2 DETACH DELETE n", {}, raise_on_error=True)
    conn.close()


@pytest.fixture()
def nodes(engs):
    q = engs["sql"]
    q.execute_cypher("MATCH (n) WHERE n:PRW OR n:PRW2 DETACH DELETE n", {}, raise_on_error=True)
    q.execute_cypher("CREATE (:PRW {id: 'x'}), (:PRW {id: '1'})", {}, raise_on_error=True)
    return engs


def _rows(eng, q, params):
    return eng.execute_cypher(q, params, raise_on_error=True).rows


def _t(v):
    """A value beside its type, inside lists and maps too: True and 1 are told apart (1 == True in Python)."""
    if isinstance(v, list):
        return [_t(e) for e in v]
    if isinstance(v, dict):
        return {k: _t(e) for k, e in v.items()}
    return (v, type(v).__name__)


def _typed(rows):
    return [[_t(v) for v in r] for r in rows]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("q, params, want", [
    ("UNWIND $p AS e RETURN e", {"p": AMBIGUOUS}, [["[2]"], [[2]], [True], ["1"]]),
    ("UNWIND $p AS e RETURN e", {"p": ["x", {"k": [1]}, False]}, [["x"], [{"k": [1]}], [False]]),
    # a later WITH and WHERE: true and [2] differ from '1' (two kinds are never equal)
    ("UNWIND $p AS e WITH e WHERE e <> '1' RETURN e", {"p": AMBIGUOUS}, [["[2]"], [[2]], [True]]),
    # another parameter beside it
    ("UNWIND $p AS e RETURN e, $q AS q", {"p": AMBIGUOUS, "q": 7}, [["[2]", 7], [[2], 7], [True, 7], ["1", 7]]),
    ("UNWIND $p AS e RETURN collect(e) AS l", {"p": AMBIGUOUS}, [[AMBIGUOUS]]),
])
def test_each_element_reads_as_itself(engs, route, q, params, want):
    assert _typed(_rows(engs[route], q, params)) == _typed(want)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_later_match_carries_the_element(nodes, route):
    q = "UNWIND $p AS e MATCH (n:PRW {id: 'x'}) RETURN e, n.id AS id"
    assert _typed(_rows(nodes[route], q, {"p": AMBIGUOUS})) == _typed([[v, "x"] for v in AMBIGUOUS])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_later_create_stores_the_element(nodes, route):
    """A write is not rewritten: this stored each element already, and the WITH form's CREATE fails -119."""
    _rows(nodes[route], "UNWIND $p AS e CREATE (:PRW2 {v: e, k: $k})", {"p": [True, [1], "x"], "k": route})
    got = _rows(nodes["sql"], "MATCH (n:PRW2 {k: $k}) RETURN n.v AS v", {"k": route})
    assert sorted(map(repr, _typed(got))) == sorted(map(repr, _typed([[True], [[1]], ["x"]])))


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_long_list_reads_whole_and_in_order(engs, route):
    p = ["a", [1], True] * 1000
    t = time.perf_counter()
    got = _rows(engs[route], "UNWIND $p AS e RETURN e", {"p": p})
    print(f"{route}: 3000 elements in {time.perf_counter() - t:.2f} s")
    assert _typed(got) == _typed([[v] for v in p])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_list_of_one_kind_reads_as_before(engs, route):
    p = [{"id": f"n{i}", "v": i} for i in range(3)]
    assert _rows(engs[route], "UNWIND $p AS m RETURN m.id AS id, m.v AS v", {"p": p}) == [["n0", 0], ["n1", 1], ["n2", 2]]
