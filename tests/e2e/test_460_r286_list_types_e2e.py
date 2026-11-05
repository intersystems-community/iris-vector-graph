"""4.6.0 R286 (the list-value items), live: a row list's element and an UNWIND boolean keep their type through a WITH.

Found by fixes40 (R273's leftovers) on the release head, under intersystems-irispython 5.4.0 and 5.4.1:
* a string 'true' taken from a row list through a WITH read as the boolean (`WITH ['true', 'x'] AS l WITH l[0] AS s
  RETURN s` answered true): CY_LIST_VAL gives a boolean element and the string alike as the text 'true', and the
  RETURN read that text as a stored 'true' (the KNOWN_ISSUES rule for node properties);
* a JSON 1.0 in a row list came back from CY_LIST_VAL as the integer 1 (%DynamicArray's %Get reads 1.0 as 1);
* a flagged UNWIND boolean (R273's flag column, for a list mixing booleans with other values) read 1 again once a
  later WITH carried it (`UNWIND [true, 1] AS v WITH v RETURN v` answered 1, 1).

Worked by hand:
* `WITH ['true', 'x'] AS l WITH l[0] AS s` is the string 'true'; `l[1]` of ['true', true] is the boolean true;
  head(['false']) the string 'false'; carried by a second WITH, each stays what it is; collect() of s is ['true'].
* `WITH [1.0, 2] AS l WITH l[0] AS x` is the float 1.0: toString(x) '1.0', x * 2 the float 2.0; [2.50][0] is 2.5,
  [1e3][0] 1000.0, [3][0] the integer 3.
* UNWIND [true, 1] AS v, then `WITH v`: true, 1 (also after two WITHs, and renamed `WITH v AS w`, and beside another
  column); collect() of it after a WITH [true, 1]; `WITH DISTINCT v` keeps both (true is not 1); grouped over
  [true, 1, true]: true twice, 1 once.

Namespace: an IVG460F* scratch namespace (no graph data is read). Native on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

TB = "WITH ['true', true] AS l WITH l[0] AS s, l[1] AS b "
CASES = [
    ("'true' through WITH", "WITH ['true', 'x'] AS l WITH l[0] AS s RETURN s", [["true"]]),
    ("'true' of an UNWIND row", "UNWIND [['true', 1]] AS row WITH row[0] AS s RETURN s", [["true"]]),
    ("the string and the boolean", TB + "RETURN s, b", [["true", True]]),
    ("head() of ['false']", "WITH ['false'] AS l WITH head(l) AS s RETURN s", [["false"]]),
    ("last() of ['true']", "WITH ['true'] AS l WITH last(l) AS s RETURN s", [["true"]]),
    ("carried by a second WITH", TB + "WITH s, b RETURN s, b", [["true", True]]),
    ("collect() of the string", "WITH ['true', 'x'] AS l WITH l[0] AS s RETURN collect(s)", [[["true"]]]),
    ("1.0 through WITH", "WITH [1.0, 2] AS l WITH l[0] AS x RETURN x", [[1.0]]),
    ("toString() of it", "WITH [1.0, 2] AS l WITH l[0] AS x RETURN toString(x)", [["1.0"]]),
    ("1.0 * 2", "WITH [1.0, 2] AS l WITH l[0] AS x RETURN x * 2", [[2.0]]),
    ("1.0 of an UNWIND row", "UNWIND [[1.0, 2]] AS l WITH l[0] AS x RETURN x", [[1.0]]),
    ("2.50", "WITH [2.50] AS l WITH l[0] AS x RETURN x", [[2.5]]),
    ("1e3", "WITH [1e3] AS l WITH l[0] AS x RETURN x", [[1000.0]]),
    ("an integer stays one", "WITH [3] AS l WITH l[0] AS x RETURN x", [[3]]),
    ("an UNWIND boolean through WITH", "UNWIND [true, 1] AS v WITH v RETURN v", [[True], [1]]),
    ("through two WITHs", "UNWIND [true, 1] AS v WITH v WITH v RETURN v", [[True], [1]]),
    ("renamed", "UNWIND [true, 1, 'a'] AS v WITH v AS w RETURN w", [[True], [1], ["a"]]),
    ("beside another column", "UNWIND [false, 0] AS v WITH v, 5 AS k RETURN v, k", [[False, 5], [0, 5]]),
    ("collect() after a WITH", "UNWIND [true, 1] AS v WITH v WITH collect(v) AS c RETURN c", [[[True, 1]]]),
]
UNORDERED = [
    ("DISTINCT keeps true and 1", "UNWIND [true, 1] AS v WITH DISTINCT v RETURN v", [[True], [1]]),
    ("grouped", "UNWIND [true, 1, true] AS v WITH v, count(*) AS c RETURN v, c", [[True, 2], [1, 1]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        conn.close()


def _value(v):
    if isinstance(v, str) and v[:1] and v[:1] in "[{":
        return json.loads(v)
    return v


def _typed(v):
    if isinstance(v, list):
        return ["list", [_typed(x) for x in v]]
    return [type(v).__name__, v]


def _rows(engines, path, query):
    result = engines[path].execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    return _typed([[_value(v) for v in r] for r in result.rows])


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_value_keeps_its_type_through_with(engines, path, name, query, want):
    assert _rows(engines, path, query) == _typed(want)


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", UNORDERED, ids=[c[0] for c in UNORDERED])
def test_a_flagged_boolean_is_its_own_value(engines, path, name, query, want):
    def key(r):
        return json.dumps(r)

    assert sorted(_rows(engines, path, query)[1], key=key) == sorted(_typed(want)[1], key=key)
