"""4.6.0 R273 (the UNWIND, collect() and list-element cases), live: an UNWIND row, a collected list and a list's
element keep each value's type.

Found by fixes35 (R227's and R262's leftovers) on the release head, under intersystems-irispython 5.4.0 and 5.4.1:
* `UNWIND [1, true, 'a'] AS v RETURN v` answered 1 for true: the UNWIND row binds true as IRIS's 1, so collect() of
  it held no boolean either (R227's strict xfail, `UNWIND [1, true] AS v WITH collect(v) AS vs RETURN last(vs)`);
* `collect()` of a list-valued WITH column listed its JSON as a string (`["[1, 2]"]`);
* a row list's element read through a WITH was JSON_ARRAYGET's text (`WITH x[0] AS y RETURN y` answered '3'), and
  `x[0] + 1` the text '4' under 5.4.1 (R262's strict xfails);
* `size($p)` answered the length of the parameter's JSON text (7 for [1, 2, 3]).

Found with them: `UNWIND $p AS v RETURN v` answered every element as text under both drivers ('1', '2' for
[1, 2], '1', '0' for [true, false]) and an empty string as null.

Worked by hand:
* UNWIND [1, true, 'a']: the rows 1, true, 'a'; collect() of it [1, true, 'a']; of [1, true], last() is true;
  UNWIND [false, 0]: false, 0 (0 is not false).
* UNWIND $p for p = [1, 2]: 1, 2; for b = [true, false]: true, false; for f = [1.5, 2.0]: 1.5, 2.0; for
  s = ['', 'a']: '', 'a'.
* `WITH [1, 2] AS l WITH collect(l) AS c RETURN c` is [[1, 2]]; UNWIND c is one row, [1, 2]; of
  UNWIND [[1, 2], [3]] AS l, collect(l) is [[1, 2], [3]] and its size 2.
* x = collect() of UNWIND [3, 4]: through `WITH x[0] AS y`, y is 3; `WITH x[1] AS y RETURN y * 2` 8;
  `WITH last(x) AS y` 4; `RETURN x[0] + 1` 4. For x = ['a', 2.5]: x[0] through WITH 'a', x[1] 2.5.
* size($p) for p = [1, 2, 3] is 3; size($s) for s = 'abcd' 4.

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

PARAMS = {"p": [1, 2], "b": [True, False], "f": [1.5, 2.0], "s": ["", "a"], "q": [1, 2, 3], "t": "abcd"}
X = "UNWIND [3, 4] AS v WITH collect(v) AS x "

CASES = [
    ("UNWIND of a boolean among other values", "UNWIND [1, true, 'a'] AS v RETURN v", [[1], [True], ["a"]]),
    ("collect() of it", "UNWIND [1, true, 'a'] AS v RETURN collect(v)", [[[1, True, "a"]]]),
    ("R227: last() of a collected boolean", "UNWIND [1, true] AS v WITH collect(v) AS vs RETURN last(vs)", [[True]]),
    ("false is not 0", "UNWIND [false, 0] AS v RETURN v", [[False], [0]]),
    ("UNWIND of an integer parameter", "UNWIND $p AS v RETURN v", [[1], [2]]),
    ("UNWIND of a boolean parameter", "UNWIND $b AS v RETURN v", [[True], [False]]),
    ("UNWIND of a float parameter", "UNWIND $f AS v RETURN v", [[1.5], [2.0]]),
    ("UNWIND of a parameter holding ''", "UNWIND $s AS v RETURN v", [[""], ["a"]]),
    ("collect() of a list-valued WITH column", "WITH [1, 2] AS l WITH collect(l) AS c RETURN c", [[[[1, 2]]]]),
    ("UNWIND of that collected list", "WITH [1, 2] AS l WITH collect(l) AS c UNWIND c AS x RETURN x", [[[1, 2]]]),
    ("collect() of two lists", "UNWIND [[1, 2], [3]] AS l WITH collect(l) AS c RETURN c, size(c)",
     [[[[1, 2], [3]], 2]]),
    ("x[0] through WITH", X + "WITH x[0] AS y RETURN y", [[3]]),
    ("x[1] through WITH, doubled", X + "WITH x[1] AS y RETURN y * 2", [[8]]),
    ("last(x) through WITH", X + "WITH last(x) AS y RETURN y", [[4]]),
    ("x[0] + 1", X + "RETURN x[0] + 1", [[4]]),
    ("a string and a float through WITH", "WITH ['a', 2.5] AS x WITH x[0] AS a, x[1] AS f RETURN a, f",
     [["a", 2.5]]),
    ("size() of a list parameter", "RETURN size($q), size($t)", [[3, 4]]),
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


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_list_value_keeps_its_type(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, dict(PARAMS), raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert _typed([[_value(v) for v in r] for r in result.rows]) == _typed(want)
