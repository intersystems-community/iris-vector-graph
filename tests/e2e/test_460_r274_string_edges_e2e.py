"""4.6.0 R274 (the string and percentile cases), live: split(), =~ and percentileCont() at the empty string, null
and zero rows.

The translator's own SQL functions read their arguments as IRIS hands them over: SQL's `''` arrives as `$Char(0)`
and SQL NULL as `""` (R269 fixed the CypherFn copies, not these). Found by fixes38 on the release head:

* `split('', ',')` answered `["\\u0000"]` and `split('a,b', '')` `["a,b"]` (Graph_KG.STR_SPLIT);
* `'' =~ 'a*'` answered false and `'' =~ '.+'` true (Graph_KG.REGEX_MATCH ran on "\\x00");
* `percentileCont` over zero rows answered 0.0 (IVG.Percentile.PCONT).

Found with them: a null held by a row (`WITH null AS s`) matched `s =~ 'a*'` (true), and `split(s, ',')` of it
answered `[""]`; `split('a', s)` `[]`. Once REGEX_MATCH answers NULL for it, IRIS reads `NOT (<function> = 1)` as true
for a NULL function value, so `NOT s =~ 'a*'` answered true and a `WHERE NOT` kept the null row.

Each answer worked by hand from openCypher:

* `split('', ',')` is `['']` (one piece, empty); `split('a,b', '')` the characters `['a', ',', 'b']`;
  `split('a,b', ',')` `['a', 'b']`; `split(',a,', ',')` `['', 'a', '']`; a null string or delimiter gives null.
* `'' =~ 'a*'` is true (zero a's), `'' =~ '.+'` false, `'' =~ ''` true, `'a' =~ ''` false, `'ab' =~ 'a*'` false
  (the whole string must match); null `=~` anything is null, and a null is no match in a WHERE.
* `percentileCont(x, 0.5)` and `percentileDisc(x, 0.5)` over no rows are null (as avg() is); over 1.0 and 3.0 they
  are 2.0 and 1.0.

Runs in an IVG460F* scratch namespace on the native and the SQL path (no graph data is read).
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

#: (name, query, rows by hand: a list is its parsed JSON)
CASES = [
    ("split of the empty string", "RETURN split('', ',')", [[[""]]]),
    ("split by the empty string", "RETURN split('a,b', '')", [[["a", ",", "b"]]]),
    ("split, control", "RETURN split('a,b', ','), split(',a,', ',')", [[["a", "b"], ["", "a", ""]]]),
    ("split of a row's empty string", "UNWIND ['', 'a,b'] AS s RETURN split(s, ',')", [[[""]], [["a", "b"]]]),
    ("split by a row's empty string", "UNWIND [''] AS d RETURN split('ab', d)", [[["a", "b"]]]),
    ("split of a row's null", "WITH null AS s RETURN split(s, ','), split('a', s)", [[None, None]]),
    ("the empty string matches a*", "RETURN '' =~ 'a*', '' =~ '.+', 'a' =~ 'a*'", [[True, False, True]]),
    ("the empty pattern", "RETURN '' =~ '', 'a' =~ ''", [[True, False]]),
    ("a row's empty string", "UNWIND ['', 'a', 'ab'] AS s RETURN s, s =~ 'a*'",
     [["", True], ["a", True], ["ab", False]]),
    ("a row's pattern", "WITH '' AS s, 'a*' AS p RETURN s =~ p, 'b' =~ p", [[True, False]]),
    ("a row's null", "WITH null AS s RETURN s =~ 'a*', NOT s =~ 'a*'", [[None, None]]),
    ("a match in a WHERE", "UNWIND ['', 'b', null] AS s WITH s WHERE s =~ 'a*' RETURN s", [[""]]),
    ("no match in a WHERE NOT", "UNWIND ['', 'b', null] AS s WITH s WHERE NOT s =~ 'a*' RETURN s", [["b"]]),
    ("percentileCont over no rows",
     "UNWIND [1.0, 2.0] AS x WITH x WHERE x > 5 RETURN percentileCont(x, 0.5), percentileDisc(x, 0.5), avg(x)",
     [[None, None, None]]),
    ("percentileCont over no rows, alone", "UNWIND [] AS x RETURN percentileCont(x, 0.5)", [[None]]),
    ("percentiles, control", "UNWIND [1.0, 3.0] AS x RETURN percentileCont(x, 0.5), percentileDisc(x, 0.5)",
     [[2.0, 1.0]]),
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
def test_strings_and_percentiles_at_the_edges(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert _typed([[_value(v) for v in r] for r in result.rows]) == _typed(want)
