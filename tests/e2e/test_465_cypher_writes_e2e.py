"""4.6.5, live: a `$param` in a list literal of a Cypher write, and a CREATE or MERGE of NaN, an infinity or a failing
value (round 8 walkthrough; unit: tests/unit/test_465_cypher_write_literals.py).

4.6.4: `UNWIND ['w1', $b] AS x MERGE (:W465 {id: x})` and `UNWIND [$b] AS x CREATE (...)` raised `KeyError: 'b'`;
`CREATE (:W465 {id: 'w4', l: [1, $b]})` and `SET n.l = [1, $b]` raised `TypeError: Object of type Variable is not JSON
serializable`; `CREATE (:W465 {v: 0.0 / 0.0})` and `{v: 1.0 / 0.0}` raised `<ARGUMENT ERROR>` (`<LIST ERROR>` under
driver 5.4.1) where `SET n.w = 0.0 / 0.0` and `v: $v` store NaN; `UNWIND [1, 0] AS x CREATE (n:W465Z {node_id: 'zh' +
toString(1 / x)})` raised GraphWriteError with `<LIST ERROR>`, where `RETURN 1 / 0` names `ArithmeticError`.

Each answer is the database's state after the call, worked by hand: the nodes the query makes, `n.l` read back as the
list `[1, 2]`, NaN read back as NaN, the failing write leaving no node. Each case runs on the default and the SQL route.

    IVG_NAMESPACE=IVG463WA pytest tests/e2e/test_465_cypher_writes_e2e.py
"""

from __future__ import annotations

import math

import pytest

from iris_vector_graph.errors import GraphWriteError
from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]


@pytest.fixture(scope="module")
def conn():
    c = ns463.connect()
    yield c
    c.close()


@pytest.fixture(params=ns463.ROUTES)
def eng(conn, request):
    """An engine on the route, with this file's nodes (labels W465, W465Z) gone before and after the case."""
    engine = ns463.engines(conn)[request.param]
    for label in ("W465", "W465Z"):
        engine.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True)
    yield engine
    for label in ("W465", "W465Z"):
        engine.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True)


def _ids(eng, label="W465"):
    return sorted(r[0] for r in ns463.rows(eng, f"MATCH (n:{label}) RETURN n.node_id"))


def _write(eng, q, params=None):
    eng.execute_cypher(q, dict(params or {}), raise_on_error=True)


def _raw(conn, node, key):
    """The stored value of a property, as the driver returns it, whatever the engine would make of it."""
    cur = conn.cursor()
    cur.execute('SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?', [node, key])
    rows = cur.fetchall()
    assert len(rows) == 1, (node, key, rows)
    return rows[0][0]


# ── 1. a $param inside a list literal ────────────────────────────────────────


def test_merge_after_unwind_of_a_list_holding_a_param(eng):
    _write(eng, "UNWIND ['w1', $b] AS x MERGE (:W465 {id: x})", {"b": "w2"})
    assert _ids(eng) == ["w1", "w2"]


def test_create_after_unwind_of_a_list_of_one_param(eng):
    _write(eng, "UNWIND [$b] AS x CREATE (:W465 {id: x})", {"b": "w3"})
    assert _ids(eng) == ["w3"]


def test_a_param_in_the_unwound_list_makes_the_nodes_the_spelled_out_literal_does(eng):
    _write(eng, "UNWIND ['w1', 'w2', $b, 7] AS x CREATE (:W465 {node_id: toString(x)})", {"b": "w3"})
    assert _ids(eng) == ["7", "w1", "w2", "w3"]


def test_create_stores_a_list_property_holding_a_param(eng):
    _write(eng, "CREATE (:W465 {id: 'w4', l: [1, $b]})", {"b": 2})
    assert ns463.rows(eng, "MATCH (n:W465 {node_id: 'w4'}) RETURN n.l") == [[[1, 2]]]


def test_set_stores_a_list_holding_a_param(eng):
    _write(eng, "CREATE (:W465 {id: 'w5'})")
    _write(eng, "MATCH (n:W465 {node_id: 'w5'}) SET n.l = [1, $b]", {"b": 2})
    assert ns463.rows(eng, "MATCH (n:W465 {node_id: 'w5'}) RETURN n.l") == [[[1, 2]]]


def test_a_list_of_strings_holding_a_param(eng):
    _write(eng, "CREATE (:W465 {id: 'w4', l: ['a', $b, 'c']})", {"b": "b"})
    assert ns463.rows(eng, "MATCH (n:W465 {node_id: 'w4'}) RETURN n.l") == [[["a", "b", "c"]]]


def test_merge_pattern_property_list_holding_a_param(eng):
    _write(eng, "MERGE (:W465 {id: 'm1', l: [1, $b]})", {"b": 2})
    assert ns463.rows(eng, "MATCH (n:W465 {node_id: 'm1'}) RETURN n.l") == [[[1, 2]]]


@pytest.mark.parametrize("action", ["ON CREATE", "ON MATCH"])
@pytest.mark.parametrize("value", ["[1, 2]", "[1, $b]", "[1, 1 + 1]"])
def test_merge_action_set_stores_a_list(eng, action, value):
    """`MERGE ... ON CREATE SET n.l = [1, 2]` raised `<ARGUMENT ERROR>` in 4.6.4 with or without a parameter in it."""
    if action == "ON MATCH":
        _write(eng, "CREATE (:W465 {id: 'm2'})")
    _write(eng, f"MERGE (n:W465 {{id: 'm2'}}) {action} SET n.l = {value}", {"b": 2})
    assert _ids(eng) == ["m2"]
    assert ns463.rows(eng, "MATCH (n:W465 {node_id: 'm2'}) RETURN n.l") == [[[1, 2]]]


def test_a_matched_row_creates_a_list_holding_a_param(eng):
    _write(eng, "CREATE (:W465 {id: 'a'})")
    _write(eng, "MATCH (n:W465 {node_id: 'a'}) CREATE (:W465 {id: 'c1', l: [1, $b]})", {"b": 2})
    assert ns463.rows(eng, "MATCH (n:W465 {node_id: 'c1'}) RETURN n.l") == [[[1, 2]]]


def test_a_relationship_property_list_holding_a_param(eng):
    _write(eng, "CREATE (:W465 {id: 'a'}), (:W465 {id: 'b'})")
    _write(eng, "MATCH (a:W465 {node_id: 'a'}), (b:W465 {node_id: 'b'}) CREATE (a)-[:R465 {l: [1, $p]}]->(b)", {"p": 2})
    assert ns463.rows(eng, "MATCH (:W465)-[r:R465]->(:W465) RETURN r.l") == [[[1, 2]]]


def test_unwind_of_map_literals_holding_a_param_stores_each_entry(eng):
    _write(
        eng,
        "UNWIND [{id: 'u1', n: $p, l: [1, $p]}, {id: 'u2', n: 7, l: []}] AS m "
        "CREATE (:W465 {id: m.id, n: m.n, l: m.l})",
        {"p": 2},
    )
    got = ns463.rows(eng, "MATCH (n:W465) RETURN n.node_id, n.n, n.l ORDER BY n.node_id")
    assert ns463.typed(got) == [
        [("u1", "str"), (2, "int"), [(1, "int"), (2, "int")]],
        [("u2", "str"), (7, "int"), []],
    ]


def test_unwind_of_a_nested_list_holding_a_param(eng):
    _write(eng, "UNWIND [[1, $b]] AS l CREATE (:W465 {id: 'n1', l: l})", {"b": 2})
    assert ns463.rows(eng, "MATCH (n:W465) RETURN n.node_id, n.l") == [["n1", [1, 2]]]


def test_foreach_over_a_list_holding_a_param(eng):
    _write(eng, "FOREACH (x IN ['f1', $b] | CREATE (:W465 {id: x}))", {"b": "f2"})
    assert _ids(eng) == ["f1", "f2"]


def test_a_nested_list_property_holding_a_param_is_refused_and_writes_nothing(eng):
    with pytest.raises(TypeError, match="InvalidPropertyType: property `l`"):
        _write(eng, "CREATE (:W465 {id: 'x', l: [1, $b]})", {"b": [2, 3]})
    assert _ids(eng) == []


def test_a_map_property_holding_a_param_is_refused_and_writes_nothing(eng):
    with pytest.raises(TypeError, match="InvalidPropertyType: property `m`"):
        _write(eng, "CREATE (:W465 {id: 'x', m: {a: $p}})", {"p": 1})
    assert _ids(eng) == []


def test_list_elements_the_query_computes(eng):
    _write(eng, "CREATE (:W465 {id: 'k1', l: [1, 1 + 1, 2 * 1.5]})")
    _write(eng, "UNWIND [1, 2] AS i CREATE (:W465 {node_id: 'u' + toString(i), l: [i, i * 2]})")
    _write(eng, "MATCH (n:W465 {node_id: 'k1'}) SET n.s = [1, 1 + 1]")
    got = ns463.rows(eng, "MATCH (n:W465) RETURN n.node_id, n.l, n.s ORDER BY n.node_id")
    assert ns463.typed(got) == [
        [("k1", "str"), [(1, "int"), (2, "int"), (3.0, "float")], [(1, "int"), (2, "int")]],
        [("u1", "str"), [(1, "int"), (2, "int")], (None, "NoneType")],
        [("u2", "str"), [(2, "int"), (4, "int")], (None, "NoneType")],
    ]


# ── 2. NaN and the infinities in a CREATE or MERGE ───────────────────────────


@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
@pytest.mark.parametrize(
    "expr, value",
    [
        ("0.0 / 0.0", math.nan),
        ("1.0 / 0.0", math.inf),
        ("-1.0 / 0.0", -math.inf),
        ("1 / 0.0", math.inf),
        ("2.5 / 0", math.inf),
        ("5.0 % 0", math.nan),
    ],
)
def test_a_create_or_merge_stores_what_set_and_the_param_store(eng, conn, verb, expr, value):
    """The expression, SET to it and the parameter holding the float: all three read back, and are stored, alike."""
    _write(eng, f"{verb} (:W465 {{id: 'viaexpr', v: {expr}}})")
    _write(eng, "CREATE (:W465 {id: 'viaset'})")
    _write(eng, f"MATCH (n:W465 {{node_id: 'viaset'}}) SET n.v = {expr}")
    _write(eng, "CREATE (:W465 {id: 'viaparam', v: $v})", {"v": value})
    for node in ("viaexpr", "viaset", "viaparam"):
        read = ns463.rows(eng, f"MATCH (n:W465 {{node_id: '{node}'}}) RETURN n.v")[0][0]
        raw = _raw(conn, node, "v")
        for got in (read, raw):
            assert isinstance(got, float), (node, got)
            assert math.isnan(got) if math.isnan(value) else got == value, (node, got)


def test_nan_after_an_unwind(eng):
    _write(eng, "UNWIND [0.0, 2.0] AS d CREATE (:W465 {node_id: 'z' + toString(d), v: 1.0 / d})")
    got = ns463.rows(eng, "MATCH (n:W465) RETURN n.node_id, n.v ORDER BY n.node_id")
    assert got == [["z0.0", math.inf], ["z2.0", 0.5]]


def test_merge_action_set_stores_nan_and_an_infinity(eng, conn):
    _write(eng, "MERGE (n:W465 {id: 'm1'}) ON CREATE SET n.a = 0.0 / 0.0, n.b = 1.0 / 0.0")
    assert math.isnan(_raw(conn, "m1", "a")) and _raw(conn, "m1", "a") is not None
    assert _raw(conn, "m1", "b") == math.inf
    assert isinstance(_raw(conn, "m1", "a"), float) and isinstance(_raw(conn, "m1", "b"), float)


# ── a failing value names its cause and writes nothing ───────────────────────


@pytest.mark.parametrize(
    "q, text",
    [
        ("UNWIND [1, 0] AS x CREATE (n:W465Z {node_id: 'zh' + toString(1 / x)})", "ArithmeticError: integer division by zero"),
        ("CREATE (:W465Z {node_id: 'z', v: 1 / 0})", "ArithmeticError: integer division by zero"),
        ("CREATE (:W465Z {node_id: 'z', v: 7 % 0})", "ArithmeticError: integer modulo by zero"),
        ("MERGE (:W465Z {node_id: 'z', v: 1 / 0})", "ArithmeticError: integer division by zero"),
        ("UNWIND [2, 0] AS x CREATE (:W465Z {node_id: 'z' + toString(x), l: [1, 6 / x]})", "ArithmeticError: integer division by zero"),
        ("CREATE (:W465Z {node_id: 'z', v: 'a' * 2})", "TypeError: InvalidArgumentType: * needs two numbers"),
    ],
)
def test_a_failing_value_raises_graph_write_error_naming_it_and_writes_nothing(eng, q, text):
    with pytest.raises(GraphWriteError) as err:
        _write(eng, q)
    message = str(err.value)
    assert text in message and "nothing written" in message, message
    assert "LIST ERROR" not in message and "ARGUMENT ERROR" not in message, message
    assert _ids(eng, "W465Z") == []


def test_a_zero_divisor_in_a_branch_not_taken_writes_the_other_rows(eng):
    _write(eng, "UNWIND [1, 0] AS x CREATE (:W465 {node_id: 'zh' + toString(x), v: CASE WHEN x = 0 THEN null ELSE 6 / x END})")
    assert ns463.rows(eng, "MATCH (n:W465) RETURN n.node_id, n.v ORDER BY n.node_id") == [["zh0", None], ["zh1", 6]]


def test_a_read_of_the_same_division_names_the_same_error(eng):
    with pytest.raises(RuntimeError, match="ArithmeticError: integer division by zero"):
        ns463.rows(eng, "RETURN 1 / 0")
