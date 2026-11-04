"""4.6.0 R064, live: `LIST_HEAD` and `LIST_LAST`, called directly in SQL.

4.6.0 setup creates neither (spec 262), in the IVG Schema or in SQLUser: a call prepares to SQLCODE -359. The bodies
4.4.0 to 4.5.1 created in SQLUser (frozen below from v4.5.1's schema.py) are one line each, and ObjectScript's
legacy `If` without braces skips the rest of its line, so the first test that is false ends the function with no
value: `<COMMAND>` "Function must return a value" for every non-empty list; `LIST_LAST('[]')` reads index -1 and
fails with `<RANGE>`. `head()` and `last()` in Cypher, which the translator answers inline, give 1 and 3 for
[1, 2, 3].

The frozen functions are created in the scratch namespace's SQLUser for the test and dropped after it.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

#: an IVG460FX* scratch namespace set up for `Graph_KG` (scripts/dev/make_scratch_namespace.py IVG460FXI)
NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

#: v4.5.1 iris_vector_graph/schema.py, lines 1937 and 1940
FROZEN_451 = (
    'CREATE OR REPLACE FUNCTION SQLUser.LIST_HEAD(j VARCHAR(32000)) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT '
    '{ Set arr = ##class(%Library.DynamicArray).%FromJSON(j), t = arr.%GetTypeOf(0) If t = "unassigned" Quit "" '
    'If t = "number" Quit arr.%Get(0) If t = "boolean" Quit arr.%Get(0) If t = "string" Quit arr.%Get(0) '
    'Set obj = arr.%Get(0) Quit obj.%ToJSON() }',
    'CREATE OR REPLACE FUNCTION SQLUser.LIST_LAST(j VARCHAR(32000)) RETURNS VARCHAR(4000) LANGUAGE OBJECTSCRIPT '
    '{ Set arr = ##class(%Library.DynamicArray).%FromJSON(j), n = arr.%Size()-1, t = arr.%GetTypeOf(n) '
    'If t = "unassigned" Quit "" If t = "number" Quit arr.%Get(n) If t = "boolean" Quit arr.%Get(n) '
    'If t = "string" Quit arr.%Get(n) Set obj = arr.%Get(n) Quit obj.%ToJSON() }',
)


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    yield eng
    conn.close()


def _select(conn, sql, args=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return cur.fetchall()
    finally:
        cur.close()


@pytest.mark.parametrize("schema", ["Graph_KG", "SQLUser"])
@pytest.mark.parametrize("fn", ["LIST_HEAD", "LIST_LAST"])
def test_setup_creates_neither(engine, schema, fn):
    with pytest.raises(Exception, match="-359"):
        _select(engine.conn, f"SELECT {schema}.{fn}('[1,2,3]')")


@pytest.fixture
def frozen(engine):
    cur = engine.conn.cursor()
    for ddl in FROZEN_451:
        cur.execute(ddl)
    engine.conn.commit()
    yield engine.conn
    for fn in ("LIST_HEAD", "LIST_LAST"):
        cur.execute(f"DROP FUNCTION SQLUser.{fn}")
    engine.conn.commit()
    cur.close()


@pytest.mark.parametrize("fn", ["LIST_HEAD", "LIST_LAST"])
@pytest.mark.parametrize("arg", ["[1,2,3]", '["x","y"]', '[{"k":1}]'])
def test_the_451_bodies_fail_on_a_list(frozen, fn, arg):
    with pytest.raises(Exception, match="Function must return a value"):
        _select(frozen, f"SELECT SQLUser.{fn}(?)", [arg])


def test_the_451_list_last_of_an_empty_list_is_a_range_error(frozen):
    with pytest.raises(Exception, match="<RANGE>"):
        _select(frozen, "SELECT SQLUser.LIST_LAST('[]')")


def test_cypher_head_and_last_pick_the_first_and_last_element(engine):
    r = engine.execute_cypher("RETURN head([1, 2, 3]) AS h, last([1, 2, 3]) AS l", raise_on_error=True)
    assert [[str(v) for v in row] for row in r.rows] == [["1", "3"]]


@pytest.mark.xfail(strict=True, reason="head() and last() answer an integer element as text ('1'), where [1,2,3][0] "
                                       "answers 1 (found by R064, not fixed there)")
def test_cypher_head_and_last_keep_the_element_type(engine):
    r = engine.execute_cypher("RETURN head([1, 2, 3]) AS h, last([1, 2, 3]) AS l", raise_on_error=True)
    assert r.rows == [[1, 3]]
