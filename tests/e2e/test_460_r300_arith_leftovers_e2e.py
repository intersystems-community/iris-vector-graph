"""4.6.0 R300, live: fixes48's arithmetic leftovers, each answered as openCypher answers it.

Measured on the release head (6f9e732f, ivg-iris-enterprise IVG460FX50A, driver 5.4.0, 2026-11-05), over the graph
below:

- `+` with a stored string: `n.id + 1` answered null, `n.id + n.id` 0, `n.id + 1.5` 1.5, and `WHERE n.id + 1 =
  'a3001'` matched nothing;
- a string starting with P went to the duration path: `n.name * 2` for 'Paris' answered null (for 'Bob' TypeError);
- `SET n.q = n.k % 2` failed SQLCODE -1, `SET n.q = n.j / 0.0` -400;
- `WHERE $a / n.k > 1` with a stored 0 failed -400 `<DIVIDE>`, naming no ArithmeticError;
- `CASE WHEN n.k > 9 THEN 1 / 0 END` raised ArithmeticError at Query Open, though no row takes the branch;
- `any(x IN [n.name] WHERE x * 2 = 0)` read the string as 0 (true);
- `OPTIONAL MATCH (n:A300 {id: 'zz'}) RETURN count(*) + 1` answered 1 and `RETURN b, count(*) + 1` null for the count;
- `WITH [1, 2] AS l RETURN l['a']` answered '1', `n.k ^ 'a'` and `2 ^ 'a'` 1.0.

What openCypher says, worked by hand:

- `+` of a string and a number, or of two strings, concatenates; the number is spelled as toString() spells it (the
  TCK has no scenario for string + number; openCypher 9 lists "+" as string concatenation, and Neo4j answers
  'a3001' for 'a300' + 1 and 'a3002.0' for 'a300' + 2.0). A stored number keeps its stored form: the API wrote 7 as
  the text '7' before 4.6.4, which is still the number 7, so `n.k + 1` is 8 and `n.k + 'x'` '7x'.
- `-`, `*`, `/` over a string raise TypeError whatever its first letter (R297's rule); a duration is not a string.
- SET computes what RETURN computes: `n.k % 2` is 0 for k 0 and 1 for k 3; `n.j / 0.0` is IEEE Infinity for j 5, 4.
- `$a / n.k` with $a 5 and k 0, two integers: ArithmeticError (R297's rule), in a WHERE too.
- CASE evaluates only the branch a row takes (openCypher 9, CASE; Neo4j): `CASE WHEN n.k > 9 THEN 1 / 0 END` is null
  for k 0 and 3, and `CASE WHEN n.k = 0 THEN 1 / 0 END` raises ArithmeticError.
- A quantifier's predicate over a string element: `x * 2` raises TypeError as it would outside the quantifier.
- OPTIONAL MATCH that matches nothing gives one row, so count(*) is 1 and count(*) + 1 is 2, beside b null (the TCK's
  OptionalMatch scenarios count the null row; none adds to the count).
- Indexing a list with a string raises TypeError (TCK List1 [8], 'string' example, through a WITH variable there).
- `^` over a string raises TypeError (R297's rule for the other arithmetic operators; the TCK has no scenario).

The graph: (:A300 {id: 'a300', name: 'Paris', k: 0, j: 5, f: 2.0}) and (:A300 {id: 'b300', name: 'Bob', k: 3, j: 4,
f: 1.5}) by Cypher CREATE; (:N300 {k: '7'}) by create_node (the text 4.6.3's create_node stored for 7); (:D300 {dur: duration('P1D')}).
Each read runs through an SQL-path engine and a native one (native_cypher=True), which must agree. The namespace is
`IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); this file's labels are removed before and after.
"""

from __future__ import annotations

import math

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

LABELS = ("A300", "N300", "D300", "S300")


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
        sql.delete_node("r300api")

    clean()
    sql.execute_cypher(
        "CREATE (:A300 {id: 'a300', name: 'Paris', k: 0, j: 5, f: 2.0}), "
        "(:A300 {id: 'b300', name: 'Bob', k: 3, j: 4, f: 1.5}), (:D300 {dur: duration('P1D')})",
        raise_on_error=True,
    )
    sql.create_node("r300api", labels=["N300"], properties={"k": "7"})
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
    assert repr(out[0]) == repr(out[1]), (q, out)
    return out[0]


def _raises(engines, q, kind, params=None):
    """Both engines refuse the query with `kind` (while translating, or in `error` with no row)."""
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


def test_plus_with_a_stored_string_concatenates(engines):
    assert _rows(engines, "MATCH (n:A300) RETURN n.id, n.id + 1, n.id + n.id, n.id + 1.5, 1 + n.id") == [
        ["a300", "a3001", "a300a300", "a3001.5", "1a300"],
        ["b300", "b3001", "b300b300", "b3001.5", "1b300"],
    ]
    assert _rows(engines, "MATCH (n:A300) RETURN n.name + 1, n.id + n.f") == [["Bob1", "b3001.5"], ["Paris1", "a3002.0"]]
    assert _rows(engines, "MATCH (n:A300) WHERE n.id + 1 = 'a3001' RETURN n.id") == [["a300"]]
    assert _rows(engines, "MATCH (n:A300) WITH n.id + 1 AS s RETURN s") == [["a3001"], ["b3001"]]


def test_plus_over_numbers_is_unchanged(engines):
    rows = _rows(engines, "MATCH (n:A300) RETURN n.id, n.j + 1, n.f + 1, n.j + n.k")
    assert rows == [["a300", 6, 3.0, 5], ["b300", 5, 2.5, 7]]
    assert [type(v).__name__ for v in rows[0][1:]] == ["int", "float", "int"]
    assert _rows(engines, "MATCH (n:A300) WHERE n.j + 1 > 5 RETURN n.id") == [["a300"]]
    # the API's text '7' is the number 7
    assert _rows(engines, "MATCH (n:N300) RETURN n.k + 1, n.k + n.k, n.k + 'x'") == [[8, 14, "7x"]]
    assert _rows(engines, "MATCH (n:D300) RETURN n.dur + duration('P1D')") == [["P2D"]]


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:A300 {id: 'a300'}) RETURN n.name * 2",
        "MATCH (n:A300 {id: 'a300'}) RETURN n.name - 1",
        "MATCH (n:A300 {id: 'a300'}) RETURN n.name / 2",
        "MATCH (n:A300 {id: 'a300'}) RETURN 2 * n.name",
    ],
)
def test_a_string_starting_with_p_raises_type_error(engines, q):
    _raises(engines, q, "TypeError")


def test_a_duration_still_multiplies(engines):
    assert _rows(engines, "MATCH (n:D300) RETURN n.dur * 2") == [["P2D"]]


def test_set_computes_modulo_and_ieee_division(engines):
    sql = engines[0]
    sql.execute_cypher("MATCH (n:A300) SET n.q = n.k % 2", raise_on_error=True)
    assert _rows(engines, "MATCH (n:A300) RETURN n.id, n.q") == [["a300", 0], ["b300", 1]]
    sql.execute_cypher("MATCH (n:A300) SET n.q = n.j / 0.0", raise_on_error=True)
    assert _rows(engines, "MATCH (n:A300) RETURN n.id, n.q") == [["a300", math.inf], ["b300", math.inf]]
    sql.execute_cypher("MATCH (n:A300) REMOVE n.q", raise_on_error=True)


def test_a_where_dividing_by_a_stored_zero_raises_arithmetic_error(engines):
    _raises(engines, "MATCH (n:A300) WHERE $a / n.k > 1 RETURN n.id", "ArithmeticError", {"a": 5})
    # no zero divisor among the rows read: 5 / 3 = 1, not > 1
    assert _rows(engines, "MATCH (n:A300) WHERE n.k > 0 AND $a / n.k > 0 RETURN n.id", {"a": 5}) == [["b300"]]


def test_a_branch_no_row_takes_does_not_raise(engines):
    assert _rows(engines, "MATCH (n:A300) RETURN n.id, CASE WHEN n.k > 9 THEN 1 / 0 END") == [
        ["a300", None], ["b300", None]]
    _raises(engines, "MATCH (n:A300) RETURN CASE WHEN n.k = 0 THEN 1 / 0 END", "ArithmeticError")


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:A300) RETURN any(x IN [n.name] WHERE x * 2 = 0)",
        "MATCH (n:A300) WITH [n.name] AS l RETURN any(x IN l WHERE x % 2 = 0)",
    ],
)
def test_a_string_element_of_a_list_predicate_raises_type_error(engines, q):
    _raises(engines, q, "TypeError")


def test_a_list_predicate_over_numbers_is_unchanged(engines):
    # a: 5 is odd and > 4; b: 4 and 3, neither both (the answer alone: beside n.id it comes back as the text '1',
    # on the release head too, a defect R300 leaves)
    assert _rows(engines, "MATCH (n:A300) RETURN any(x IN [n.j, n.k] WHERE x % 2 = 1 AND x > 4)") == [[False], [True]]


def test_the_null_row_counts_itself(engines):
    assert _rows(engines, "OPTIONAL MATCH (n:A300 {id: 'zz'}) RETURN count(*)") == [[1]]
    assert _rows(engines, "OPTIONAL MATCH (n:A300 {id: 'zz'}) RETURN count(*) + 1") == [[2]]
    # a node is three columns of execute_cypher's row (id, labels, properties), all null on the null row
    assert _rows(engines, "OPTIONAL MATCH (n:A300 {id: 'a300'})-[:NONE300]->(b) RETURN b, count(*) + 1") == [
        [None, None, None, 2]]
    assert _rows(engines, "OPTIONAL MATCH (n:A300 {id: 'a300'})-[:NONE300]->(b) RETURN b.k, count(*) * 3") == [
        [None, 3]]


@pytest.mark.parametrize(
    "q",
    [
        "WITH [1, 2] AS l RETURN l['a']",
        "MATCH (n:A300) RETURN n.k ^ 'a'",
        "RETURN 2 ^ 'a'",
    ],
)
def test_a_string_index_or_exponent_raises_type_error(engines, q):
    _raises(engines, q, "TypeError")


def test_exponent_over_numbers_is_unchanged(engines):
    assert _rows(engines, "MATCH (n:A300) RETURN n.id, n.j ^ 2") == [["a300", 25.0], ["b300", 16.0]]
