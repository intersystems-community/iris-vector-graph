"""4.6.5 (round 8 walkthrough): a `$param` inside a list literal of a Cypher write, and a CREATE whose value is NaN, an
infinity or a runtime error (live: tests/e2e/test_465_cypher_writes_e2e.py).

4.6.4 raised `KeyError: 'b'` for `UNWIND ['w1', $b] AS x MERGE (:WT {id: x})` and `UNWIND [$b] AS x CREATE (:WT {id:
x})`, and `TypeError: Object of type Variable is not JSON serializable` for `CREATE (:WT {id:'w4', l:[1,$b]})` and
`SET n.l = [1,$b]`: the write bound the list's elements as AST objects. A value the query computes (`[1, 1+1]`, `[i,
i*2]` after an UNWIND) failed the same way. `CREATE (:WT {v: 0.0/0.0})` raised `<ARGUMENT ERROR> Unsupported argument
type` (the AST of the division was bound), where `SET` and `v: $v` store NaN; `UNWIND [1,0] AS x CREATE (n:Zh {node_id:
'zh' + toString(1/x)})` raised `<LIST ERROR>` where a read names `ArithmeticError: integer division by zero`.

Each expected value is worked by hand: the statement set a literal spelled in stores, [1, 2] as the text `[1, 2]`.
"""

from __future__ import annotations

import math

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.errors import GraphWriteError


def _statements(q, params=None):
    t = translate_to_sql(parse_query(q), params or {})
    sql = t.sql if isinstance(t.sql, list) else [t.sql]
    return list(zip(sql, t.parameters))


def _node_ids(q, params=None):
    """The node ids the statements insert, in order."""
    return [p[0] for s, p in _statements(q, params) if s.startswith("INSERT INTO Graph_KG.nodes ")]


def _props(q, params=None):
    """{(node, key): value} of the rdf_props rows the statements insert."""
    return {
        (p[0], p[1]): p[2] for s, p in _statements(q, params) if s.startswith("INSERT INTO Graph_KG.rdf_props ")
    }


def _set_values(q, params=None):
    """The values the UPDATE statements of a SET bind."""
    return [p[0] for s, p in _statements(q, params) if s.startswith("UPDATE Graph_KG.rdf_props SET val = Graph_KG.Prop_Set(%ID, val, ?)")]


# ── 1. a $param inside a list literal ────────────────────────────────────────


def test_merge_after_unwind_of_a_list_holding_a_param():
    assert _node_ids("UNWIND ['w1', $b] AS x MERGE (:WT {id: x})", {"b": "w2"}) == ["w1", "w2"]


def test_create_after_unwind_of_a_list_of_one_param():
    assert _node_ids("UNWIND [$b] AS x CREATE (:WT {id: x})", {"b": "w3"}) == ["w3"]


def test_the_param_spelled_in_stores_what_the_literal_does():
    with_param = _statements("UNWIND ['w1', $b] AS x MERGE (:WT {id: x})", {"b": "w2"})
    spelled = _statements("UNWIND ['w1', 'w2'] AS x MERGE (:WT {id: x})")
    assert with_param == spelled


def test_create_stores_a_list_property_holding_a_param():
    assert _props("CREATE (:WT {id:'w4', l:[1,$b]})", {"b": 2})[("w4", "l")] == "[1, 2]"


def test_a_param_holding_a_string_in_a_list_property():
    assert _props("CREATE (:WT {id:'w4', l:['a',$b]})", {"b": "z"})[("w4", "l")] == '["a", "z"]'


def test_set_stores_a_list_holding_a_param():
    assert _set_values("MATCH (n {node_id:'wt:a'}) SET n.l = [1,$b]", {"b": 2}) == ["[1, 2]"]


@pytest.mark.parametrize("action", ["ON CREATE", "ON MATCH"])
@pytest.mark.parametrize("value, params", [("[1, 2]", {}), ("[1, $b]", {"b": 2}), ("[1, 1 + 1]", {})])
def test_merge_action_set_stores_a_list_as_set_does(action, value, params):
    """`MERGE ... ON CREATE SET n.l = [1, 2]` bound the list of Literal objects (<ARGUMENT ERROR>) in 4.6.4, a
    parameter in it or not."""
    bound = [
        p[0] for s, p in _statements(f"MERGE (n:WT {{id:'m1'}}) {action} SET n.l = {value}", params)
        if s.startswith("UPDATE Graph_KG.rdf_props SET val = Graph_KG.Prop_Set(%ID, val, ?)")
    ]
    assert bound == ["[1, 2]"]


def test_merge_action_set_of_a_nested_list_is_refused_as_set_is():
    with pytest.raises(TypeError, match="InvalidPropertyType: property `l`"):
        translate_to_sql(parse_query("MERGE (n:WT {id:'m1'}) ON CREATE SET n.l = [[1], $b]"), {"b": 2})


def test_merge_pattern_property_list_holding_a_param():
    assert _props("MERGE (:WT {id:'m2', l:[1,$b]})", {"b": 2})[("m2", "l")] == "[1, 2]"


def test_a_list_param_held_in_a_list_is_a_nested_list_and_refused_as_the_literal_is():
    with pytest.raises(TypeError, match="InvalidPropertyType: property `l`"):
        translate_to_sql(parse_query("CREATE (:WT {id:'w4', l:[1,$b]})"), {"b": [2, 3]})
    with pytest.raises(TypeError, match="InvalidPropertyType: property `l`"):
        translate_to_sql(parse_query("CREATE (:WT {id:'w4', l:[1,[2,3]]})"), {})


def test_a_map_literal_holding_a_param_is_refused_as_a_property_as_the_literal_is():
    with pytest.raises(TypeError, match="InvalidPropertyType: property `p`"):
        translate_to_sql(parse_query("CREATE (:WT {id:'w4', p:{a:$v}})"), {"v": 1})


def test_unwind_of_a_map_literal_holding_a_param():
    assert _node_ids("UNWIND [{k: $p}, {k: 'q'}] AS m CREATE (:WT {id: m.k})", {"p": "from-param"}) == [
        "from-param",
        "q",
    ]


def test_unwind_of_a_nested_list_holding_a_param():
    props = _props("UNWIND [[1, $b]] AS l CREATE (:WT {id: 'n1', l: l})", {"b": 2})
    assert props[("n1", "l")] == "[1, 2]"


def test_foreach_over_a_list_holding_a_param():
    assert _node_ids("FOREACH (x IN ['f1', $b] | CREATE (:WT {id: x}))", {"b": "f2"}) == ["f1", "f2"]


def test_an_absent_param_is_not_made_up():
    with pytest.raises(Exception) as err:
        translate_to_sql(parse_query("CREATE (:WT {id:'w4', l:[1,$b]})"), {})
    assert "b" in str(err.value)


# ── computed values in a list literal ────────────────────────────────────────


def test_a_list_property_holding_arithmetic():
    assert _props("CREATE (:WT {id:'c1', l:[1, 1+1, 2*1.5]})")[("c1", "l")] == "[1, 2, 3.0]"


def test_a_list_property_holding_the_unwind_row():
    props = _props("UNWIND [1, 2] AS i CREATE (:WT {id: 'u' + toString(i), l: [i, i * 2]})")
    assert props[("u1", "l")] == "[1, 2]" and props[("u2", "l")] == "[2, 4]"


def test_set_a_list_holding_arithmetic():
    assert _set_values("MATCH (n {node_id:'a'}) SET n.l = [1, 1+1]") == ["[1, 2]"]


# ── 2. NaN and the infinities in a CREATE or MERGE ───────────────────────────


def _stored(q, key="v", params=None):
    (value,) = [v for (s, k), v in _props(q, params).items() if k == key]
    return value


@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
def test_zero_over_zero_is_nan(verb):
    assert math.isnan(_stored(f"{verb} (:WT {{id:'w6', v: 0.0/0.0}})"))


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("1.0/0.0", math.inf),
        ("-1.0/0.0", -math.inf),
        ("1/0.0", math.inf),
        ("1.5/0", math.inf),
        ("-2.5/0", -math.inf),
    ],
)
@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
def test_a_float_over_zero_is_an_infinity(verb, expr, expected):
    assert _stored(f"{verb} (:WT {{id:'w7', v: {expr}}})") == expected


@pytest.mark.parametrize("expr", ["5.0 % 0", "5 % 0.0", "0.0 / 0"])
def test_a_float_modulo_zero_is_nan(expr):
    assert math.isnan(_stored(f"CREATE (:WT {{id:'w8', v: {expr}}})"))


def test_the_value_is_the_float_the_param_form_binds():
    param = _stored("CREATE (:WT {id:'w8', v: $v})", params={"v": float("inf")})
    folded = _stored("CREATE (:WT {id:'w8', v: 1.0/0.0})")
    assert param == folded == math.inf and type(param) is type(folded) is float


def test_nan_after_an_unwind():
    props = _props("UNWIND [0.0, 1.0] AS d CREATE (:WT {id: 'z' + toString(d), v: 1.0 / d})")
    assert props[("z1.0", "v")] == 1.0 and props[("z0.0", "v")] == math.inf


def test_a_nan_in_a_list_property_is_what_the_param_form_stores():
    folded = _stored("CREATE (:WT {id:'w9', v: [1, 0.0/0.0]})")
    param = _stored("CREATE (:WT {id:'w9', v: [1, $n]})", params={"n": float("nan")})
    assert folded == param == "[1, NaN]"


# ── a runtime error in a CREATE names its cause ──────────────────────────────


@pytest.mark.parametrize(
    "q, text",
    [
        ("UNWIND [1,0] AS x CREATE (n:Zh {node_id: 'zh' + toString(1/x)})", "ArithmeticError: integer division by zero"),
        ("CREATE (:Zh {node_id: 'z', v: 1/0})", "ArithmeticError: integer division by zero"),
        ("CREATE (:Zh {node_id: 'z', v: 7 % 0})", "ArithmeticError: integer modulo by zero"),
        ("UNWIND [0] AS x MERGE (:Zh {node_id: 'z', v: 4 / x})", "ArithmeticError: integer division by zero"),
        ("CREATE (:Zh {node_id: 'z', v: [1, 2 / 0]})", "ArithmeticError: integer division by zero"),
        ("CREATE (:Zh {node_id: 'z', v: 1 + 1/0})", "ArithmeticError: integer division by zero"),
    ],
)
def test_an_integer_division_by_zero_in_a_write_raises_graph_write_error_naming_it(q, text):
    with pytest.raises(GraphWriteError) as err:
        translate_to_sql(parse_query(q), {})
    assert text in str(err.value) and "nothing written" in str(err.value), str(err.value)
    assert "LIST ERROR" not in str(err.value) and "ARGUMENT ERROR" not in str(err.value)


def test_a_zero_divisor_in_a_branch_not_taken_raises_nothing():
    q = "UNWIND [1, 0] AS x CREATE (:Zh {node_id: 'zh' + toString(x), v: CASE WHEN x = 0 THEN null ELSE 6 / x END})"
    props = _props(q)
    assert props[("zh1", "v")] == 6 and ("zh0", "v") not in props
    assert _node_ids(q) == ["zh1", "zh0"]


def test_arithmetic_on_a_string_in_a_write_raises_graph_write_error_naming_the_type_error():
    with pytest.raises(GraphWriteError) as err:
        translate_to_sql(parse_query("CREATE (:Zh {node_id: 'z', v: 'a' * 2})"), {})
    assert "TypeError: InvalidArgumentType: * needs two numbers" in str(err.value)
    assert "nothing written" in str(err.value) and "LIST ERROR" not in str(err.value)


def test_a_string_plus_a_number_is_still_the_concatenation():
    assert _stored("CREATE (:WT {id: 'c', v: 'a' + 1})") == "a1"


# ── a literal map element of an UNWIND, read by key ─────────────────────────


def test_unwind_of_map_literals_stores_the_entries_read_by_key():
    props = _props(
        "UNWIND [{id: 'ua', l: [1, $b], n: 5, s: 'x'}] AS m CREATE (:WT {id: m.id, l: m.l, n: m.n, s: m.s})", {"b": 2}
    )
    assert props == {("ua", "id"): "ua", ("ua", "l"): "[1, 2]", ("ua", "n"): 5, ("ua", "s"): "x"}


def test_unwind_of_a_map_param_still_stores_the_entries_read_by_key():
    props = _props("UNWIND $rows AS m CREATE (:WT {id: m.id, l: m.l})", {"rows": [{"id": "ub", "l": [1, 2]}]})
    assert props == {("ub", "id"): "ub", ("ub", "l"): "[1, 2]"}


# ── MERGE ... ON CREATE SET of NaN or an infinity ───────────────────────────


@pytest.mark.parametrize("action", ["ON CREATE", "ON MATCH"])
@pytest.mark.parametrize("expr, check", [("0.0/0.0", math.isnan), ("1.0/0.0", lambda x: x == math.inf)])
def test_merge_action_set_stores_the_float_that_set_and_the_param_store(action, expr, check):
    sets = [
        p
        for s, p in _statements(f"MERGE (n:WT {{id:'m1'}}) {action} SET n.v = {expr}")
        if s.startswith("UPDATE Graph_KG.rdf_props SET val = Graph_KG.Prop_Set(%ID, val, ?)")
    ]
    assert len(sets) == 1 and type(sets[0][0]) is float and check(sets[0][0]), sets
