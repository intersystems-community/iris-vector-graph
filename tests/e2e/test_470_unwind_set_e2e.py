"""4.7.0, live: SET of an UNWIND variable over a parameter list writes the row's value, on both routes.

On 4.6.5 (f1a442e7) and on release-4.7.0 before this, measured 2026-11-10 on both routes:
- `UNWIND $p AS e MATCH (n {id: 'x'}) SET n.f = e` and `UNWIND $p AS e CREATE (n:X) SET n.f = e` raised
  SyntaxError "UndefinedVariable: Variable `e` not defined", for `$p = [1, 2]` too;
- `UNWIND $rows AS r MATCH (n {id: r.id}) SET n += r`, the batch idiom, raised "SET n += takes a map literal or a
  map parameter".
`SET n.v = r.v` worked, and stores true, [1] and '1' each as itself (the guard below). The answers are openCypher's:
the property holds the value the row holds, read back type-strict (true is not 1).

    IVG_NAMESPACE=IVG470TIME2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_unwind_set_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    yield engs
    engs["sql"].execute_cypher("MATCH (n) WHERE n:US OR n:USX DETACH DELETE n", {}, raise_on_error=True)
    conn.close()


@pytest.fixture()
def engs(engines):
    q = engines["sql"]
    q.execute_cypher("MATCH (n) WHERE n:US OR n:USX DETACH DELETE n", {}, raise_on_error=True)
    q.execute_cypher("CREATE (:US {id: 'x'}), (:US {id: 'a'}), (:US {id: 'b'})", {}, raise_on_error=True)
    q.execute_cypher("MATCH (a:US {id: 'a'}), (b:US {id: 'b'}) CREATE (a)-[:UR {w: 0}]->(b)", {}, raise_on_error=True)
    return engines


def _write(eng, q, params):
    eng.execute_cypher(q, params, raise_on_error=True)


def _read(engs, q):
    return engs["sql"].execute_cypher(q, {}, raise_on_error=True).rows


def _t(v):
    if isinstance(v, list):
        return [_t(e) for e in v]
    if isinstance(v, dict):
        return {k: _t(e) for k, e in v.items()}
    return (v, type(v).__name__)


def _typed(rows):
    return [[_t(v) for v in r] for r in rows]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("value", [7, 2.5, "s", True, False, [1, 2], ["a", True]])
def test_set_of_the_unwound_value(engs, route, value):
    _write(engs[route], "UNWIND $p AS e MATCH (n:US {id: 'x'}) SET n.f = e", {"p": [value]})
    assert _typed(_read(engs, "MATCH (n:US {id: 'x'}) RETURN n.f")) == _typed([[value]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_set_of_each_row_on_its_own_node(engs, route):
    _write(engs[route], "UNWIND $p AS e MATCH (n:US {id: e}) SET n.f = e", {"p": ["a", "b"]})
    assert _read(engs, "MATCH (n:US) RETURN n.id, n.f ORDER BY n.id") == [["a", "a"], ["b", "b"], ["x", None]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_create_then_set(engs, route):
    _write(engs[route], "UNWIND $p AS e CREATE (n:USX) SET n.f = e", {"p": [1, 2]})
    assert _read(engs, "MATCH (n:USX) RETURN n.f ORDER BY n.f") == [[1], [2]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_set_of_an_expression_of_the_unwound_value(engs, route):
    _write(engs[route], "UNWIND $p AS e MATCH (n:US {id: 'x'}) SET n.f = e * 2, n.g = toString(e)", {"p": [3]})
    assert _typed(_read(engs, "MATCH (n:US {id: 'x'}) RETURN n.f, n.g")) == _typed([[6, "3"]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_batch_idiom_merges_each_row_into_its_node(engs, route):
    rows = [{"id": "a", "v": 1, "t": True}, {"id": "b", "v": [2], "t": "1"}]
    _write(engs[route], "UNWIND $rows AS r MATCH (n:US {id: r.id}) SET n += r", {"rows": rows})
    got = _read(engs, "MATCH (n:US) RETURN n.id, n.v, n.t ORDER BY n.id")
    assert _typed(got) == _typed([["a", 1, True], ["b", [2], "1"], ["x", None, None]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_set_equals_a_row_replaces_the_properties(engs, route):
    _write(engs[route], "MATCH (n:US {id: 'a'}) SET n.old = 1", {})
    _write(engs[route], "UNWIND $rows AS r MATCH (n:US {id: r.id}) SET n = r", {"rows": [{"id": "a", "v": 5}]})
    assert _read(engs, "MATCH (n:US {id: 'a'}) RETURN n.id, n.v, n.old") == [["a", 5, None]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_batch_idiom_on_a_relationship(engs, route):
    rows = [{"a": "a", "b": "b", "w": 9}]
    _write(engs[route], "UNWIND $rows AS r MATCH (:US {id: r.a})-[x:UR]->(:US {id: r.b}) SET x += r", {"rows": rows})
    assert _read(engs, "MATCH (:US)-[x:UR]->(:US) RETURN x.w, x.a, x.b") == [[9, "a", "b"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_row_property_already_set_as_itself(engs, route):
    """The guard: this answered on 4.6.5 and must keep answering."""
    rows = [{"id": "a", "v": True}, {"id": "b", "v": [1]}, {"id": "x", "v": "1"}]
    _write(engs[route], "UNWIND $rows AS r MATCH (n:US {id: r.id}) SET n.v = r.v", {"rows": rows})
    got = _read(engs, "MATCH (n:US) RETURN n.id, n.v ORDER BY n.id")
    assert _typed(got) == _typed([["a", True], ["b", [1]], ["x", "1"]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_boolean_carried_by_a_with_is_set_as_a_boolean(engs, route):
    """`WITH e WHERE e = true ... SET n.f = e` wrote the string '1' (the element read by its JSON_TABLE text)."""
    q = "UNWIND $p AS e WITH e WHERE e = true MATCH (n:US {id: 'x'}) SET n.f = e"
    _write(engs[route], q, {"p": ["1", [1], True]})
    assert _typed(_read(engs, "MATCH (n:US {id: 'x'}) RETURN n.f")) == _typed([[True]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_merge_on_create_set_writes_the_row(engs, route):
    """It bound the parser's Variable object as the value (the row was never read)."""
    _write(engs[route], "UNWIND $p AS e MERGE (n:USX {id: e}) ON CREATE SET n.f = e", {"p": ["m1", "m2"]})
    assert _read(engs, "MATCH (n:USX) RETURN n.id, n.f ORDER BY n.id") == [["m1", "m1"], ["m2", "m2"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_merge_on_match_set_writes_the_row(engs, route):
    _write(engs[route], "UNWIND $p AS e MERGE (n:US {id: e}) ON MATCH SET n.seen = e", {"p": ["a"]})
    assert _read(engs, "MATCH (n:US {id: 'a'}) RETURN n.seen") == [["a"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_merge_rows_that_match_and_rows_that_create(engs, route):
    """ON MATCH wrote nothing after an UNWIND, a literal value too (`n.m = 'lit'`): its node read as the alias."""
    q = "UNWIND $p AS e MERGE (n:US {id: e}) ON CREATE SET n.c = e ON MATCH SET n.m = e"
    _write(engs[route], q, {"p": ["a", "new"]})
    got = _read(engs, "MATCH (n:US) WHERE n.id IN ['a', 'new'] RETURN n.id, n.c, n.m ORDER BY n.id")
    assert got == [["a", None, "a"], ["new", "new", None]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_batch_merge_idiom(engs, route):
    rows = [{"id": "a", "v": 1}, {"id": "n2", "v": 2}]
    q = "UNWIND $rows AS r MERGE (n:US {id: r.id}) ON CREATE SET n.made = true SET n += r"
    _write(engs[route], q, {"rows": rows})
    got = _read(engs, "MATCH (n:US) WHERE n.id IN ['a', 'n2'] RETURN n.id, n.v, n.made ORDER BY n.id")
    assert _typed(got) == _typed([["a", 1, None], ["n2", 2, True]])
