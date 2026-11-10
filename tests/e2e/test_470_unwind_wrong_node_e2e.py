"""4.7.0, live: a write that finds its nodes by an UNWIND element of a parameter list compares the element as the same
literal would, on both routes.

`UNWIND $p AS e WITH e MATCH (n) WHERE n.f = e DETACH DELETE n` with `$p` holding true compared true as its JSON_TABLE
text, 1 (measured 2026-11-10, 4.6.5 and release-4.7.0): it deleted a node whose f is the string '1' and kept the one
whose f is the boolean true. Row by row (no WITH), true was inlined as 1 and `[1]` as raw SQL text (SQLCODE -12).

The answer is the literal's (the lead's decision, option A): `n.f = true` matches a stored true and the spellings older
writers stored, '1' and 'True' (`_stored_bool_comparison`; KNOWN_ISSUES, "A boolean in an equality also matches a
stored '1' or 'True'"); `n.f = [1]` matches a stored list [1], never the string '[2]'; a string matches itself. So
over `['zz', [1], true]` every form deletes b (f true), one (f '1') and l (f [1]), and keeps ls (f '[2]'), x and 1.
`MATCH (n:UW {id: true}) DETACH DELETE n` (no UNWIND) answers the same, the reference the UNWIND forms must equal.

    IVG_NAMESPACE=IVG470TIME2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_unwind_wrong_node_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

MIXED = ["zz", [1], True]
#: every form's answer over MIXED: the nodes kept
KEPT = ["1", "ls", "x"]


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    yield engs
    engs["sql"].execute_cypher("MATCH (n:UW) DETACH DELETE n", {}, raise_on_error=True)
    conn.close()


@pytest.fixture()
def engs(engines):
    q = engines["sql"]
    q.execute_cypher("MATCH (n:UW) DETACH DELETE n", {}, raise_on_error=True)
    q.execute_cypher("CREATE (:UW {id: '1'}), (:UW {id: 'x'}), (:UW {id: 'b', f: true}), (:UW {id: 'one', f: '1'}), "
                     "(:UW {id: 'l', f: [1]}), (:UW {id: 'ls', f: '[2]'})", {}, raise_on_error=True)
    q.execute_cypher("MATCH (a:UW {id: 'b'}), (b:UW {id: 'x'}) CREATE (a)-[:UWR]->(b)", {}, raise_on_error=True)
    return engines


def _write(eng, q, params):
    eng.execute_cypher(q, params, raise_on_error=True)


def _rows(engs, q):
    """Sorted here: ORDER BY of a string that spells a number sorts it as one (KNOWN_ISSUES), not this file's matter."""
    return sorted(engs["sql"].execute_cypher(q, {}, raise_on_error=True).rows)


def _ids(engs):
    return [r[0] for r in _rows(engs, "MATCH (n:UW) RETURN n.id")]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_literals_answer(engs, route):
    """The reference: each value as a literal, no UNWIND."""
    for v in ("true", "[1]", "'zz'"):
        _write(engs[route], f"MATCH (n:UW) WHERE n.f = {v} DETACH DELETE n", {})
    assert _ids(engs) == KEPT


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("q", [
    "UNWIND $p AS e WITH e MATCH (n:UW) WHERE n.f = e DETACH DELETE n",
    "UNWIND $p AS e MATCH (n:UW) WHERE n.f = e DETACH DELETE n",
    "UNWIND $p AS e MATCH (n:UW {f: e}) DETACH DELETE n",
    "UNWIND $p AS e WITH e MATCH (n:UW {f: e}) DETACH DELETE n",
])
def test_a_delete_removes_the_nodes_the_literals_find(engs, route, q):
    _write(engs[route], q, {"p": MIXED})
    assert _ids(engs) == KEPT


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("q", [
    "UNWIND $p AS e WITH e MATCH (n:UW) WHERE n.f = e SET n.hit = 1",
    "UNWIND $p AS e MATCH (n:UW) WHERE n.f = e SET n.hit = 1",
    "UNWIND $p AS e MATCH (n:UW {f: e}) SET n.hit = 1",
])
def test_a_set_writes_the_nodes_the_literals_find(engs, route, q):
    _write(engs[route], q, {"p": MIXED})
    got = _rows(engs, "MATCH (n:UW) WHERE n.hit = 1 RETURN n.id")
    assert got == [["b"], ["l"], ["one"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("q", [
    "UNWIND $p AS e WITH e MATCH (n:UW) WHERE n.f = e REMOVE n.f",
    "UNWIND $p AS e MATCH (n:UW) WHERE n.f = e REMOVE n.f",
])
def test_a_remove_changes_the_nodes_the_literals_find(engs, route, q):
    _write(engs[route], q, {"p": MIXED})
    got = _rows(engs, "MATCH (n:UW) WHERE n.f IS NOT NULL RETURN n.id")
    assert got == [["ls"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_read_finds_the_nodes_the_literals_find(engs, route):
    q = "UNWIND $p AS e MATCH (n:UW) WHERE n.f = e RETURN n.id"
    assert sorted(engs[route].execute_cypher(q, {"p": MIXED}, raise_on_error=True).rows) == [["b"], ["l"], ["one"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("q", [
    "UNWIND $p AS e WITH e MATCH (n:UW {id: e}) DETACH DELETE n",
    "UNWIND $p AS e MATCH (n:UW {id: e}) DETACH DELETE n",
    "UNWIND $p AS e WITH e MATCH (n:UW) WHERE n.id = e DETACH DELETE n",
])
def test_an_id_element_deletes_as_the_literal_would(engs, route, q):
    """`{id: 'x'}` names the node; `{id: true}` is the id property, so it finds the node whose id is '1' too
    (`MATCH (n:UW {id: true}) DETACH DELETE n` answers so), and `[1]` finds none."""
    _write(engs[route], q, {"p": ["x", [1], True]})
    assert _ids(engs) == ["b", "l", "ls", "one"]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_id_literals_answer(engs, route):
    for v in ("'x'", "[1]", "true"):
        _write(engs[route], f"MATCH (n:UW {{id: {v}}}) DETACH DELETE n", {})
    assert _ids(engs) == ["b", "l", "ls", "one"]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_list_of_one_kind_deletes_each_of_its_nodes(engs, route):
    _write(engs[route], "UNWIND $p AS e WITH e MATCH (n:UW) WHERE n.id = e DETACH DELETE n", {"p": ["x", "1"]})
    assert _ids(engs) == ["b", "l", "ls", "one"]
