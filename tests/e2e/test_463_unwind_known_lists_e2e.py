"""4.6.3, live: an UNWIND of a list known before the query runs answers each '' element as '', and a `+` of a
parameter's list concatenates.

Expected answers by openCypher (UNWIND answers one row per element, each element as it is; `+` of a list and a list
concatenates, of a list and a value appends it):

- `UNWIND ['', 'a'] + [''] AS x RETURN x`: '', 'a', ''. Before 4.6.3: null, 'a', null (the concatenation went to
  JSON_TABLE as JSON text, which reads "" as null).
- The same through two WITH-bound literals, and through two parameters (before: one row, '0', the two JSON texts
  added as numbers).
- `RETURN $l1 + $l2`: the one list ['', 'a', '']. Before: 0.
- `UNWIND $l AS x` of 1,001 strings, '' first (past the 900 elements read as UNION ALL rows): before, null for ''.
- `UNWIND $l AS x` of ['', [1, '']]: '' and the list [1, ''] (before: null and the list).

Each case runs on the default route and the SQL route (native_cypher=False). The namespace is `IVG_NAMESPACE`
(tests/e2e/ns463.py).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    yield ns463.engines(conn)
    conn.close()


S = ("", "str")

CASES = [
    ("UNWIND ['', 'a'] + [''] AS x RETURN x", {}, [[S], [("a", "str")], [S]]),
    ("WITH ['', 'a'] AS l1, [''] AS l2 UNWIND l1 + l2 AS x RETURN x", {}, [[S], [("a", "str")], [S]]),
    ("UNWIND $l1 + $l2 AS x RETURN x", {"l1": ["", "a"], "l2": [""]}, [[S], [("a", "str")], [S]]),
    ("UNWIND $l1 + 'b' AS x RETURN x", {"l1": ["", "a"]}, [[S], [("a", "str")], [("b", "str")]]),
    ("UNWIND $l1 + $n AS x RETURN x", {"l1": [""], "n": [1, 2.5]}, [[S], [(1, "int")], [(2.5, "float")]]),
    ("UNWIND $l AS x RETURN x", {"l": ["", [1, ""]]}, [[S], [[(1, "int"), S]]]),
    ("RETURN $l1 + $l2 AS c", {"l1": ["", "a"], "l2": [""]}, [[[S, ("a", "str"), S]]]),
    ("RETURN size($l1 + $l2) AS c", {"l1": ["", "a"], "l2": [""]}, [[(3, "int")]]),
]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("query,params,want", CASES, ids=[c[0] for c in CASES])
def test_a_known_list_keeps_its_empty_strings(engines, route, query, params, want):
    assert ns463.typed(ns463.rows(engines[route], query, params)) == want


BIG = [""] + [f"s{i}" for i in range(1000)]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_long_parameter_list_keeps_its_empty_string(engines, route):
    got = ns463.rows(engines[route], "UNWIND $l AS x RETURN x", {"l": BIG})
    assert sorted(r[0] for r in got) == sorted(BIG)
    assert ns463.rows(engines[route], "UNWIND $l AS x WITH x WHERE x = '' RETURN count(*)", {"l": BIG}) == [[1]]
    assert ns463.rows(engines[route], "UNWIND $l AS x WITH x WHERE x IS NULL RETURN count(*)", {"l": BIG}) == [[0]]
