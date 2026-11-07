"""4.6.0 R227, live: an element of a list that comes from a row keeps its type, under either driver version.

R193 fixed constant lists (`head([1, 2, 3])` folds to 1). A list a row holds (a WITH column, an UNWIND variable, a
collect()) was read by JSON_ARRAYGET, which answers IRIS's value of the element as text: `WITH [1,'a',2.5,true] AS x
RETURN head(x), x[0], last(x)` answered ['1', '1', '1'] (true is 1 to IRIS). Such a RETURN column now reads
CY_LIST_ELEM, the element's JSON behind a mark, decoded to its type. A list or map element comes back as the Python
list or dict, as a list in a result row does (4.6.0 R314, CHANGELOG "A list, a map or a path in a result row answers
the Python list or dict, not its JSON text"; it was JSON text before). `collect(v)[3]` failed -19 (the subscript hid the aggregate).

Namespace: `IVG_NAMESPACE`, an IVG460FX* scratch namespace set up by `scripts/dev/make_scratch_namespace.py`
(IVG460FXX when this was written), with this checkout's routines (`initialize_schema()`, which the fixture runs). The
fixture writes graph r227g and erases it after.

By hand, graph r227g: nodes labelled R227 written by Cypher, {k: 1, v: 1}, {k: 2, v: 'a'}, {k: 3, v: 2.5},
{k: 4, v: '1'}; collected in k order the list is [1, 'a', 2.5, '1'].

Run it under the repo .venv (intersystems-irispython 5.4.0) and under 5.4.1, what a fresh install gets:

    IVG_NAMESPACE=IVG460FXX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/e2e/test_460_r227_row_list_types_e2e.py
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = "r227g"
STORED = [(1, 1), (2, "a"), (3, 2.5), (4, "1")]

#: (name, query, rows by hand)
CASES = [
    ("R227: head, [0] and last of a WITH list",
     "WITH [1, 'a', 2.5, true] AS x RETURN head(x), x[0], last(x)", [[1, 1, True]]),
    ("each index, negative and out of range",
     "WITH [1, 'a', 2.5, true] AS x RETURN x[1], x[2], x[3], x[-1], x[-4], x[4], x[-5]",
     [["a", 2.5, True, True, 1, None, None]]),
    ("a string of digits, null, false, a fraction, a list and a map",
     "WITH ['1', 2, null, false, 0.5, [1, 2], {k: 'v'}] AS x RETURN x[0], x[1], x[2], x[3], x[4], x[5], x[6]",
     [["1", 2, None, False, 0.5, [1, 2], {"k": "v"}]]),
    ("collect() of mixed values through WITH",
     "UNWIND [1, 'a', 2.5, '1'] AS v WITH collect(v) AS vs RETURN head(vs), vs[1], vs[2], last(vs)",
     [[1, "a", 2.5, "1"]]),
    ("collect() indexed in the RETURN",
     "UNWIND [1, 'a', 2.5, '1'] AS v RETURN head(collect(v)), collect(v)[3], collect(v)[-3]", [[1, "1", "a"]]),
    ("an UNWIND variable holding a list",
     "UNWIND [[1, 'a'], [2, 'b']] AS l RETURN l[0], last(l)", [[1, "a"], [2, "b"]]),
    ("collect() of stored values",
     f"USE GRAPH '{G}' MATCH (n:R227) WITH n ORDER BY n.k WITH collect(n.v) AS vs "
     "RETURN head(vs), vs[1], vs[2], vs[3], last(vs)", [[1, "a", 2.5, "1", "1"]]),
]

#: found here, fixed by 4.6.0 R273: the UNWIND row bound true as 1, so the collected list held no boolean
COLLECTED_BOOLEANS = [
    ("an UNWIND boolean collected", "UNWIND [1, true] AS v WITH collect(v) AS vs RETURN last(vs)", [[True]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    for k, v in STORED:
        nat.execute_cypher(f"USE GRAPH '{G}' CREATE (:R227 {{k: $k, v: $v}})", {"k": k, "v": v}, raise_on_error=True)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _rows(engine, query):
    result = engine.execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    return [list(r) for r in result.rows]


def _typed(rows):
    """Each value with its type: 1 is not True, 1 is not '1', 2.5 is not '2.5'."""
    return [[(type(v).__name__, v) for v in row] for row in rows]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_row_list_s_element_keeps_its_type(engines, path, name, query, want):
    assert _typed(_rows(engines[path], query)) == _typed(want)


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", COLLECTED_BOOLEANS, ids=[c[0] for c in COLLECTED_BOOLEANS])
def test_a_collected_boolean_stays_a_boolean(engines, path, name, query, want):
    assert _typed(_rows(engines[path], query)) == _typed(want)
