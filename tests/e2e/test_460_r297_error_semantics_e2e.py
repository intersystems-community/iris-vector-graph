"""4.6.0 R297, live: an integer divided by zero raises ArithmeticError, and arithmetic on a string raises TypeError.

Found by fixes47 on the release head: `WITH n, n.k / 0 AS z RETURN z` answered `nan` for an integer k, `WITH n.k / 0
AS z CREATE (:KD2 {v: z})` wrote nodes holding NaN, and `n.id * 2` and `n.id - 1` over a string id answered null.
Measured here as well (IVG460C48, Community 2026.1, 2026-11-05): `RETURN 1 / 0` and `1 % 0` answered NaN, `WHERE n.k / 0
= 1` no rows and no error, `MATCH (n) SET n.q = n.k / 0` stored NaN, `RETURN 'a' * 2` answered 0 and `'a' - 1` -1, and
a float divided by zero (`1.5 / 0`, `n.f / 0` for f = 2.5) NaN.

What openCypher says. The TCK has no scenario for either: Mathematical5 (division) and Mathematical6 (modulo) hold no
scenario, and no feature divides an integer by zero or applies `-`, `*`, `/` or `%` to a string. Its README defines the
error kinds: ArithmeticError, "Invalid use of an arithmetic operation, such as dividing by zero", and TypeError, "...
operations on values with types that are not supported by the operation". The TCK does fix the float case: `0.0 / 0.0`
is NaN (Comparison1 [8], ReturnOrderBy1, WithOrderBy1). So, from the openCypher definitions:

- an integer divided by an integer zero, `/` or `%`, raises ArithmeticError;
- a float operand makes it IEEE 754 arithmetic: x / 0 is Infinity for x > 0, -Infinity for x < 0, NaN for 0 (and NaN
  for NaN); x % 0 is NaN;
- null propagates first: `null / 0` and a missing property over 0 are null;
- `-`, `*`, `/` and `%` over a string raise TypeError. A stored number keeps its stored form: a value the Python API
  wrote as the text '7' is the number 7, as it was before (the API stores numbers as text).

The graph, worked by hand: (:K297 {id: 'a', k: 2}), (:K297 {id: 'b', k: 3}), (:F297 {f: 2.5, g: -2.5, z: 0.0}),
(:N297 {id: 'n'}) by Cypher CREATE; (:A297 {k: 7, f: 2.5}) by create_node (stored as the text '7', '2.5').

- k / 2: a 1, b 1 (integer division truncates); k % 2: a 0, b 1; k * 2: 4, 6; k - 1: 1, 2. A297 k * 2: 14.
- every k / 0 and k % 0 (a literal zero, a computed one `n.k - n.k`, a parameter 0, a WITH column, an UNWIND
  integer): ArithmeticError. `RETURN 1 / 0`, `1 % 0`: ArithmeticError. A297 k / 0: ArithmeticError.
- f / 0: Infinity; g / 0: -Infinity; z / 0: NaN; f % 0: NaN; 1.5 / 0 and 1 / 0.0: Infinity; -1.5 / 0: -Infinity;
  0.0 / 0.0: NaN. A297 f / 0: Infinity.
- N297 k / 0 and null / 0: null.
- id * 2, id - 1, id / 2, id % 2, 2 - id over 'a' and 'b': TypeError; so do 'a' * 2, 'a' - 1, a WITH string times 2
  and 2 * $s with $s = 'a'.
- A duration is not a string here: duration('P1D') * 2 is P2D, as before.
- A write that computes such a value raises and writes nothing: no KD297 node, no property q.

Each read runs through an SQL-path engine and a native one (native_cypher=True), which must agree. The namespace is
`IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); this file's labels are removed before and after.
"""

from __future__ import annotations

import json
import math

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

LABELS = ("K297", "F297", "N297", "A297", "KD297", "O297", "D297")


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    sql = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)
    native = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=True)

    def clean():
        for label in LABELS:
            sql.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", raise_on_error=True)
        sql.delete_node("r297api")

    clean()
    sql.execute_cypher(
        "CREATE (:K297 {id: 'a', k: 2}), (:K297 {id: 'b', k: 3}), (:F297 {f: 2.5, g: -2.5, z: 0.0}), "
        "(:N297 {id: 'n'}), (:O297 {k: true}), (:O297 {k: 'abc'}), (:D297 {dur: duration({months: 1, days: -14})})",
        raise_on_error=True,
    )
    sql.create_node("r297api", labels=["A297"], properties={"k": 7, "f": 2.5})
    yield sql, native
    try:
        clean()
    finally:
        conn.close()


def _rows(engines, q, params=None):
    """The rows both engines answer, sorted; both must answer without error and agree."""
    out = []
    for eng in engines:
        r = eng.execute_cypher(q, params or {})
        assert getattr(r, "error", None) in (None, ""), (q, r.error)
        out.append(sorted((list(row) for row in r.rows), key=repr))
    assert repr(out[0]) == repr(out[1]), (q, out)  # repr: NaN is not equal to itself
    return out[0]


def _raises(engines, q, kind, params=None):
    """Both engines refuse the query with `kind`: an operand the translator sees raises as the query is translated (as
    any translator error does), one only the row has answers it in `error` with no row; raise_on_error raises it."""
    for eng in engines:
        try:
            r = eng.execute_cypher(q, params or {})
        except Exception as ex:  # noqa: BLE001 - which exception it is, is the assertion
            assert kind in str(ex), (q, kind, ex)
        else:
            assert r.error and kind in str(r.error), (q, kind, r.rows, r.error)
            assert not r.rows, (q, r.rows)
        with pytest.raises(Exception, match=kind):
            eng.execute_cypher(q, params or {}, raise_on_error=True)


def _one(engines, q):
    rows = _rows(engines, q)
    assert len(rows) == 1 and len(rows[0]) == 1, (q, rows)
    return rows[0][0]


def test_integer_arithmetic_that_does_not_divide_by_zero_is_unchanged(engines):
    assert _rows(engines, "MATCH (n:K297) RETURN n.id, n.k / 2, n.k % 2, n.k * 2, n.k - 1") == [
        ["a", 1, 0, 4, 1], ["b", 1, 1, 6, 2]]
    assert _rows(engines, "MATCH (n:K297) WITH n.k / 2 AS h RETURN h") == [[1], [1]]
    assert _rows(engines, "MATCH (n:A297) RETURN n.k * 2, n.k / 2") == [[14, 3]]
    # integers whose divisor is not a literal: no error unless one is zero
    assert _rows(engines, "UNWIND [4, 6] AS x UNWIND [2, 3] AS y RETURN x / y, x % y") == [
        [1, 1], [2, 0], [2, 0], [3, 0]]
    assert _rows(engines, "MATCH (n:K297) RETURN 6 / n.k, 7 % n.k") == [[2, 1], [3, 1]]
    assert _rows(engines, "MATCH (n:K297) WHERE 6 / n.k = 3 RETURN n.id") == [["a"]]


@pytest.mark.parametrize(
    "q, params",
    [
        ("MATCH (n:K297) WITH n, n.k / 0 AS z RETURN z", None),
        ("MATCH (n:K297) RETURN n.k / 0", None),
        ("MATCH (n:K297) RETURN n.k % 0", None),
        ("MATCH (n:K297) RETURN n.k / (n.k - n.k)", None),
        ("MATCH (n:K297) WITH n.k AS k RETURN k / 0", None),
        ("MATCH (n:K297) WHERE n.k / 0 = 1 RETURN n.id", None),
        ("MATCH (n:K297) WHERE n.k % 0 = 1 RETURN n.id", None),
        ("MATCH (n:K297) RETURN n.k / $d", {"d": 0}),
        ("MATCH (n:A297) RETURN n.k / 0", None),
        ("UNWIND [1, 2] AS x RETURN x / 0", None),
        ("UNWIND [4] AS x UNWIND [0] AS y RETURN x / y", None),
        ("UNWIND [4] AS x UNWIND [0] AS y RETURN x % y", None),
        ("RETURN 1 / 0", None),
        ("RETURN 1 % 0", None),
        ("RETURN 4 / $d", {"d": 0}),
    ],
)
def test_an_integer_divided_by_zero_raises_arithmetic_error(engines, q, params):
    _raises(engines, q, "ArithmeticError", params)


def test_a_float_divided_by_zero_is_ieee(engines):
    assert _rows(engines, "MATCH (n:F297) RETURN n.f / 0, n.g / 0") == [[math.inf, -math.inf]]
    assert math.isnan(_one(engines, "MATCH (n:F297) RETURN n.z / 0"))
    assert math.isnan(_one(engines, "MATCH (n:F297) RETURN n.f % 0"))
    assert _one(engines, "MATCH (n:A297) RETURN n.f / 0") == math.inf
    assert _one(engines, "RETURN 1.5 / 0") == math.inf
    assert _one(engines, "RETURN 1 / 0.0") == math.inf
    assert _one(engines, "RETURN -1.5 / 0") == -math.inf
    assert math.isnan(_one(engines, "RETURN 0.0 / 0.0"))
    assert math.isnan(_one(engines, "RETURN 1.5 % 0"))


def test_null_over_zero_is_null(engines):
    assert _rows(engines, "MATCH (n:N297) RETURN n.k / 0, n.k % 0") == [[None, None]]
    assert _one(engines, "RETURN null / 0") is None


@pytest.mark.parametrize(
    "q, params",
    [
        ("MATCH (n:K297) RETURN n.id * 2", None),
        ("MATCH (n:K297) RETURN n.id - 1", None),
        ("MATCH (n:K297) RETURN n.id / 2", None),
        ("MATCH (n:K297) RETURN n.id % 2", None),
        ("MATCH (n:K297) RETURN 2 - n.id", None),
        ("MATCH (n:K297) WITH n.id * 2 AS z RETURN z", None),
        ("MATCH (n:K297) WITH n.id AS s RETURN s * 2", None),
        ("MATCH (n:K297) WHERE n.id * 2 > 0 RETURN n.id", None),
        ("RETURN 'a' * 2", None),
        ("RETURN 'a' - 1", None),
        ("WITH 'a' AS s RETURN s * 2", None),
        ("RETURN 2 * $s", {"s": "a"}),
    ],
)
def test_arithmetic_on_a_string_raises_type_error(engines, q, params):
    _raises(engines, q, "TypeError", params)


def test_a_duration_is_not_a_string_to_arithmetic(engines):
    assert _one(engines, "RETURN duration('P1D') * 2") == "P2D"
    assert _one(engines, "WITH duration('P1D') AS d RETURN d * 2") == "P2D"
    # a WITH's date (a constant in its stage) beside a stored duration: Temporal8 [1]'s second example, by hand
    # 1984-10-11 + P1M-14D = 1984-10-28, - = 1984-09-25 (it raised TypeError during the first R297 TCK run)
    assert _rows(engines, "WITH date({year: 1984, month: 10, day: 11}) AS x MATCH (d:D297) "
                          "RETURN x + d.dur AS sum, x - d.dur AS diff") == [["1984-10-28", "1984-09-25"]]


def test_the_number_forms_the_tck_reads_are_as_before(engines):
    # WithOrderBy4 [9]: `x % 3` of a WITH column of an integer property is an integer (k 2 and 3: 2 and 0)
    rows = _rows(engines, "MATCH (n:K297) WITH n.k AS x WITH x % 3 AS x RETURN x")
    assert rows == [[0], [2]] and all(type(r[0]) is int for r in rows), rows
    # Quantifier7 [3] (it failed -202 at Prepare)
    assert _one(engines, "RETURN (single(x IN [1, 2, 3, 4, 5, 6, 7, 8, 9] WHERE x % 2 = 0) OR all(x IN [1, 2, 3, 4, 5, "
                         "6, 7, 8, 9] WHERE x % 2 = 0)) <= any(x IN [1, 2, 3, 4, 5, 6, 7, 8, 9] WHERE x % 2 = 0) AS r") is True


def _count(eng, q):
    return eng.execute_cypher(q, raise_on_error=True).rows[0][0]


@pytest.mark.parametrize(
    "q, kind",
    [
        ("MATCH (n:K297) WITH n.k / 0 AS z CREATE (:KD297 {v: z})", "ArithmeticError"),
        ("MATCH (n:K297) CREATE (:KD297 {v: n.k / 0})", "ArithmeticError"),
        ("MATCH (n:K297) WITH n CREATE (:KD297 {v: n.k % 0})", "ArithmeticError"),
        ("MATCH (n:K297) CREATE (:KD297 {v: n.id * 2})", "TypeError"),
        ("MATCH (n:K297) WITH n.id * 2 AS z CREATE (:KD297 {v: z})", "TypeError"),
    ],
)
def test_a_create_from_a_failing_value_raises_and_writes_nothing(engines, q, kind):
    sql = engines[0]
    with pytest.raises(Exception, match=kind):
        sql.execute_cypher(q, raise_on_error=True)
    assert _count(sql, "MATCH (m:KD297) RETURN count(m)") == 0


@pytest.mark.parametrize(
    "q, kind",
    [
        ("MATCH (n:K297) SET n.q = n.k / 0", "ArithmeticError"),
        ("MATCH (n:K297) SET n.q = n.id * 2", "TypeError"),
        ("MATCH (n:K297) SET n.q = n.id - 1", "TypeError"),
        ("MATCH (n:K297) SET n.q = n.id * 1.5", "TypeError"),
        ("MATCH (n:K297) SET n.q = 'a' * 2", "TypeError"),
    ],
)
def test_a_set_from_a_failing_value_raises_and_writes_nothing(engines, q, kind):
    sql = engines[0]
    with pytest.raises(Exception, match=kind):
        sql.execute_cypher(q, raise_on_error=True)
    assert _count(sql, "MATCH (n:K297) WHERE n.q IS NOT NULL RETURN count(n)") == 0


def test_a_create_raises_only_for_the_rows_its_match_reads(engines):
    # (:O297 {k: true}) and (:O297 {k: 'abc'}) hold k values no arithmetic takes. On 2026.3 IRIS pushed the property
    # INSERT's `WHERE _cg._v IS NOT NULL` below the MATCH's joins and computed n.k * 2 for them too: TypeError for a
    # CREATE that reads only K297 (test_460_r292_create_rows failed so on ivg-iris-enterprise). A null is not written.
    sql = engines[0]
    try:
        sql.execute_cypher("MATCH (n:K297) CREATE (:KD297 {v: n.k * 2, w: n.k - 1, x: n.missing * 2})",
                           raise_on_error=True)
        got = sql.execute_cypher("MATCH (m:KD297) RETURN m.v, m.w, keys(m)", raise_on_error=True).rows
        assert sorted([v, w, sorted(json.loads(k))] for v, w, k in got) == [[4, 1, ["v", "w"]], [6, 2, ["v", "w"]]]
    finally:
        sql.execute_cypher("MATCH (m:KD297) DETACH DELETE m", raise_on_error=True)
