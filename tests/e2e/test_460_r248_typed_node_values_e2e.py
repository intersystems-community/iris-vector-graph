"""4.6.0 R248: a value that holds a node's properties reads each property with the type it is stored with, the value
``RETURN n.<key>`` answers, under intersystems-irispython 5.4.0 and 5.4.1.

Run it under both drivers (the repo's .venv has 5.4.0; a fresh install gets 5.4.1). Before R248 a whole node's
``_props`` list carried every value as text on both drivers (``".94999999999999995559"`` for a Cypher-written 0.95,
``"29"``); a map literal spelled 0.95 ``.94999999999999995559``, which is not JSON; ``properties(n)`` spelled a
``$DOUBLE`` 5.0 ``5``; a variable-length path's property and ``get_property_pairs`` read the driver's type (0.95 under
5.4.0, ``'.94999999999999995559'`` under 5.4.1); ``percentileDisc`` read ``'20'`` under 5.4.1 (fixes30).

The graph, by hand (label ``T248``, ids unique to the run):
- ``c``, written by Cypher: ``CREATE {f: 0.95, i: 29, s: 'x', g: 5.0}``, then ``SET k = 2.5 * 2``;
- ``a``, written by ``create_node`` with the strings ``{f: '0.95', i: '29'}``: what 4.6.3 and earlier stored for the
  numbers 0.95 and 29 (4.6.4 stores an API-written number as a number: test_464_api_typed_props_e2e);
- ``a -[:R248]-> c``;
- ``z1``, ``z2``, ``z3``, written by Cypher: ``price`` 10.0, 20.0, 30.0.

So:
- ``c``'s properties are id, f 0.95, i 29, s 'x', g 5.0, k 5 (floats, the integer 29, the string, and 5: Cypher stores
  ``2.5 * 2`` as an IRIS decimal, which R218 reads as the integer) in ``RETURN n``, ``WITH n RETURN n``,
  ``collect(n)``, ``[n]``, ``{m: n}``, a node ``CALL ivg.degreeCentrality`` yields, ``properties(n)``, a
  variable-length path's node (``properties(x)`` of ``UNWIND nodes(p)``; a path value lists node ids since R291) and a
  variable-length target; ``a``'s are the strings '0.95' and '29' (the API's text);
- each of ``c``'s ``_props`` values is the value ``RETURN n.<key>`` answers on the SQL path;
- ``{f: n.f, g: n.g, i: n.i}`` over ``c`` is the JSON ``{"f": 0.95, "g": 5.0, "i": 29}``;
- ``percentileDisc(n.price, 0.5)`` over z1..z3 is 20.0, at 0.0 10.0;
- ``get_property_pairs`` of a key only c and a hold answers 0.95 for c and '0.95' for a.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r248_{run}_{k}" for k in ("c", "a", "z1", "z2", "z3")}
    key = f"pp{run}"
    # the text 4.6.3's create_node stored for 0.95 and 29: such rows stay in a database, and read as text
    assert e.create_node(ids["a"], labels=["T248"], properties={"f": "0.95", "i": "29", key: "0.95"})
    for q, params in (
        ("CREATE (:T248 {id: $id, f: 0.95, i: 29, s: 'x', g: 5.0})", {"id": ids["c"]}),
        ("MATCH (n:T248 {id: $id}) SET n.k = 2.5 * 2", {"id": ids["c"]}),
        (f"MATCH (n:T248 {{id: $id}}) SET n.{key} = 0.95", {"id": ids["c"]}),
        ("MATCH (a:T248 {id: $a}), (c:T248 {id: $c}) CREATE (a)-[:R248]->(c)", {"a": ids["a"], "c": ids["c"]}),
        ("CREATE (:T248Z {id: $id, price: 10.0})", {"id": ids["z1"]}),
        ("CREATE (:T248Z {id: $id, price: 20.0})", {"id": ids["z2"]}),
        ("CREATE (:T248Z {id: $id, price: 30.0})", {"id": ids["z3"]}),
    ):
        r = e.execute_cypher(q, params)
        assert r.error is None, (q, r.error)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids, key
    for nid in ids.values():
        with contextlib.suppress(Exception):
            e.delete_node(nid, graph=None)


def _c_props(ids, key):
    return {"id": ids["c"], "f": 0.95, "i": 29, "s": "x", "g": 5.0, "k": 5, key: 0.95}


def _a_props(ids, key):
    return {"id": ids["a"], "f": "0.95", "i": "29", key: "0.95"}


def _typed(d):
    return {k: (v, type(v).__name__) for k, v in d.items()}


def _run(e, query, params, native):
    old = e.native_cypher
    e.native_cypher = native
    try:
        r = e.execute_cypher(query, params)
    finally:
        e.native_cypher = old
    assert r.error is None, (native, query, r.error)
    return r.rows


def _both(e, query, params):
    return {p: _run(e, query, params, p == "native") for p in ("native", "sql")}


def _props(blob):
    """A node value's properties: its `_props` key/value list (each item JSON text or an object), or a map."""
    if isinstance(blob, str):
        blob = json.loads(blob)
    raw = blob["_props"]
    items = json.loads(raw) if isinstance(raw, str) else raw
    out = {}
    for item in items or []:
        item = json.loads(item) if isinstance(item, str) else item
        out[item["key"]] = item["value"]
    return out


@pytest.mark.parametrize(
    "shape",
    ["MATCH (n:T248 {id: $id}) RETURN n", "MATCH (n:T248 {id: $id}) WITH n RETURN n"],
)
@pytest.mark.parametrize("who", ["c", "a"])
def test_a_returned_node_reads_its_stored_types(g, shape, who):
    e, ids, key = g
    want = _c_props(ids, key) if who == "c" else _a_props(ids, key)
    for path, rows in _both(e, shape, {"id": ids[who]}).items():
        assert len(rows) == 1 and rows[0][0] == ids[who], (path, rows)
        assert _typed(_props({"_props": rows[0][2]})) == _typed(want), path


@pytest.mark.parametrize(
    "shape, pick",
    [
        # 4.6.0 R311: the list and the map are answered as Python values
        ("MATCH (n:T248 {id: $id}) RETURN collect(n) AS v", lambda v: v[0]),
        ("MATCH (n:T248 {id: $id}) RETURN [n] AS v", lambda v: v[0]),
        ("MATCH (n:T248 {id: $id}) RETURN {m: n} AS v", lambda v: v["m"]),
    ],
)
def test_a_node_inside_a_list_or_map_reads_its_stored_types(g, shape, pick):
    e, ids, key = g
    for path, rows in _both(e, shape, {"id": ids["c"]}).items():
        assert len(rows) == 1, (path, rows)
        assert _typed(_props(pick(rows[0][0]))) == _typed(_c_props(ids, key)), path


def test_a_call_yielded_node_reads_its_stored_types(g):
    e, ids, key = g
    q = "CALL ivg.degreeCentrality({direction: 'in', predicate: 'R248'}) YIELD node, degree RETURN node, degree"
    for path, rows in _both(e, q, {}).items():
        mine = [r for r in rows if r[0] == ids["c"]]
        assert len(mine) == 1 and mine[0][3] == 1, (path, mine)
        assert _typed(_props({"_props": mine[0][2]})) == _typed(_c_props(ids, key)), path


def test_each_props_value_is_what_return_n_key_answers(g):
    e, ids, key = g
    keys = ["f", "i", "s", "g", "k", key]
    want = _run(e, "MATCH (n:T248 {id: $id}) RETURN " + ", ".join(f"n.{k}" for k in keys), {"id": ids["c"]}, False)
    got = _props({"_props": _run(e, "MATCH (n:T248 {id: $id}) RETURN n", {"id": ids["c"]}, False)[0][2]})
    assert [(got[k], type(got[k]).__name__) for k in keys] == [(v, type(v).__name__) for v in want[0]]


def test_properties_of_a_node_reads_its_stored_types(g):
    e, ids, key = g
    for who, want in (("c", _c_props(ids, key)), ("a", None)):
        for path, rows in _both(e, "MATCH (n:T248 {id: $id}) RETURN properties(n) AS p", {"id": ids[who]}).items():
            got = rows[0][0]  # 4.6.0 R311: properties(n) is the dict
            if want is None:  # an API-written number-shaped text reads as the number (KNOWN_ISSUES, R168)
                assert _typed(got) == _typed({"id": ids["a"], "f": 0.95, "i": 29, key: 0.95}), path
            else:
                assert _typed(got) == _typed(want), path


def test_a_map_literal_over_properties_is_json_with_the_stored_types(g):
    e, ids, _ = g
    for path, rows in _both(e, "MATCH (n:T248 {id: $id}) RETURN {f: n.f, g: n.g, i: n.i} AS m", {"id": ids["c"]}).items():
        assert _typed(rows[0][0]) == _typed({"f": 0.95, "g": 5.0, "i": 29}), path


def test_a_variable_length_path_and_target_read_their_stored_types(g):
    e, ids, key = g
    # 4.6.0 R301: since R291 a path value is its nodes' ids and its relationships' types on every route (the SQL
    # route's shape, tests/e2e/test_460_r291_vlp_path_function_shape_e2e.py), so a path's nodes carry no properties
    # and this read them from objects that are no longer there (fixes48: TypeError, string indices). A path node's
    # properties are read through the node, with their stored types.
    for path, rows in _both(e, "MATCH p = (a:T248 {id: $a})-[:R248*1..2]->(t) RETURN p", {"a": ids["a"]}).items():
        assert rows[0][0] == {"nodes": [ids["a"], ids["c"]], "rels": ["R248"]}, path
    q = "MATCH p = (a:T248 {id: $a})-[:R248*1..2]->(t) UNWIND nodes(p) AS x RETURN x.id, properties(x)"
    for path, rows in _both(e, q, {"a": ids["a"]}).items():
        got = {r[0]: (json.loads(r[1]) if isinstance(r[1], str) else r[1]) for r in rows}
        assert _typed(got[ids["c"]]) == _typed(_c_props(ids, key)), path
        # properties() reads the API's text '0.95' and '29' as the numbers they are, as `RETURN properties(a)` does
        assert _typed(got[ids["a"]]) == _typed({"id": ids["a"], "f": 0.95, "i": 29, key: 0.95}), path
    for path, rows in _both(e, "MATCH (a:T248 {id: $a})-[:R248*1..2]->(t) RETURN t", {"a": ids["a"]}).items():
        assert rows[0][0] == ids["c"], (path, rows)
        assert _typed(_props({"_props": rows[0][2]})) == _typed(_c_props(ids, key)), path
    for path, rows in _both(e, "MATCH (a:T248 {id: $a})-[:R248*1..2]->(t) RETURN t.f, t.i", {"a": ids["a"]}).items():
        assert [(v, type(v).__name__) for v in rows[0]] == [(0.95, "float"), (29, "int")], path


@pytest.mark.parametrize("p, want", [(0.5, 20.0), (0.0, 10.0)])
def test_percentile_disc_of_a_node_value_is_a_float(g, p, want):
    e, _, _ = g
    for path, rows in _both(e, "MATCH (n:T248Z) RETURN percentileDisc(n.price, $p) AS v", {"p": p}).items():
        assert [(v, type(v).__name__) for v in rows[0]] == [(want, "float")], path


def test_property_pairs_read_the_stored_type(g):
    e, ids, key = g
    got = sorted(e.get_property_pairs(key))
    assert [(s, v, type(v).__name__) for s, v in got] == sorted(
        [(ids["a"], "0.95", "str"), (ids["c"], 0.95, "float")]
    )
    like = sorted(e.get_property_pairs_like(key, "%95%"))
    assert [(s, v, type(v).__name__) for s, v in like] == sorted(
        [(ids["a"], "0.95", "str"), (ids["c"], 0.95, "float")]
    )


@pytest.fixture(scope="module")
def ar(iris_connection):
    """Arithmetic, by hand: node ``x`` (Cypher) ``{g: 2.0, f: 1.5, i: 3}`` and ``x -[:A248 {w: 1.5, k: 3}]-> y``."""
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    x, y = f"r248a_{run}_x", f"r248a_{run}_y"
    for q in (
        "CREATE (:T248A {id: $x, g: 2.0, f: 1.5, i: 3})",
        "CREATE (:T248A {id: $y})",
        "MATCH (a:T248A {id: $x}), (b:T248A {id: $y}) CREATE (a)-[:A248 {w: 1.5, k: 3}]->(b)",
    ):
        r = e.execute_cypher(q, {"x": x, "y": y})
        assert r.error is None, (q, r.error)
    yield e, x
    for nid in (x, y):
        with contextlib.suppress(Exception):
            e.delete_node(nid, graph=None)


@pytest.mark.parametrize(
    "ret, want",
    [
        ("n.g * 2", (4.0, "float")),  # 5.4.1 read CY_ARITH's 4.0 as '4', which the mixed decode made the integer 4
        ("n.i * 2", (6, "int")),
        ("n.f * 3", (4.5, "float")),
        ("n.i / 2", (1, "int")),
        ("n.g / 4", (0.5, "float")),
        ("r.w * 3", (4.5, "float")),  # the int default answered 4
        ("r.k * 2", (6, "int")),
        ("n.g * r.w", (3.0, "float")),
    ],
)
def test_arithmetic_over_stored_values_keeps_their_type(ar, ret, want):
    e, x = ar
    q = f"MATCH (n:T248A {{id: $x}})-[r:A248]->() RETURN {ret} AS v"
    for path, rows in _both(e, q, {"x": x}).items():
        assert [(v, type(v).__name__) for v in rows[0]] == [want], path


def test_a_with_column_of_arithmetic_keeps_its_type(ar):
    e, x = ar
    q = "MATCH (n:T248A {id: $x}) WITH n.g * 2 AS d, n.i * 2 AS j RETURN d, j"
    for path, rows in _both(e, q, {"x": x}).items():
        assert [(v, type(v).__name__) for v in rows[0]] == [(4.0, "float"), (6, "int")], path
