"""4.6.0 R308, live: comparisons across types, the math functions and size(), over every kind and read form, on every
route, against tests/oracle/crosstype.py's openCypher answers.

The cases (`crosstype.all_cases()`): `<`, `<=`, `>`, `>=`, `=` and `<>` of every pair of int, float, string, boolean,
null, list and map; each math function and size() of each kind; each read as a literal, a WITH column, a parameter, an
element of a WITH-held list, an UNWIND variable, an element of a collect()ed list and a stored property. Every route of
the R308 differential (`native`, `sql`, `edgescan`) answers each, and the answer must be the oracle's: the rows, or a
TypeError (raised, or in IVGResult.error) where the oracle raises one. A math answer compares as a number (an int, a
float or a Decimal, never text) within 1e-9; everything else keeps its type (the boolean false is not the integer 0).

The stored values are `ZZT308` nodes written by Cypher CREATE (so each keeps its form: crosstype.stored_values()),
removed before and after. The cached queries are purged before each statement, as for the R308 threshold matrix. In graph mode "two" a named graph `zzt308g` holds a relationship too (the all-graphs reads
then correlate graph_id: see the differential's GRAPH_MODES). Namespace, container and drivers as the R308 differential
(tests/e2e/test_460_r308_cypher_differential_e2e.py); disagreements go to `IVG_R308_REPORT` under `crosstype-<mode>`.

    IVG_NS_IVG460FXOC=IVG460FXOC pytest tests/e2e/test_460_r308_crosstype_e2e.py

Split over two namespaces (IVG_R308_CT_SHARD=0/2 in one, 1/2 in the other, each with its own IVG_R308_REPORT).
"""

from __future__ import annotations

import json
import math
import os
import time
from decimal import Decimal
from typing import List

import pytest

from tests.e2e import ns262
from tests.e2e.test_460_r308_cypher_differential_e2e import (  # noqa: F401 (engines is a fixture)
    DRIVER, GRAPH_MODES, REPORT, ROUTES, Answer, _write_report, engines, run)
from tests.oracle import crosstype as ct
from tests.oracle.cypher_gen import canon

pytestmark = [pytest.mark.e2e, ns262.requires_container]

#: "k/n": this process answers every n-th case from the k-th (0-based), so n processes over n namespaces (each purging
#: its own cached queries) split the family; their reports merge by key
SHARD = tuple(int(x) for x in os.environ.get("IVG_R308_CT_SHARD", "0/1").split("/"))
FAMILIES = ("comparison",)  # the math and size() families: R308 cmptypes, second part


def _clean(sql) -> None:
    sql.execute_cypher(f"MATCH (n:{ct.STORED_LABEL}) DETACH DELETE n", raise_on_error=True)
    sql.delete_nodes_by_prefix("zzt308:", graph="zzt308g")


@pytest.fixture(scope="module", params=GRAPH_MODES)
def stored(engines, request):
    sql = engines["sql"]
    _clean(sql)
    for k, v in ct.stored_values().items():
        prop = "" if v is None else f", v: {ct.cy_text(v)}"
        sql.execute_cypher(f"CREATE (:{ct.STORED_LABEL} {{k: '{k}'{prop}}})", raise_on_error=True)
    if request.param == "two":
        assert sql.create_node("zzt308:a", labels=["ZZG308"], graph="zzt308g")
        assert sql.create_node("zzt308:b", labels=["ZZG308"], graph="zzt308g")
        assert sql.create_edge("zzt308:a", "T", "zzt308:b", graph="zzt308g")
    yield request.param
    _clean(sql)


def _purge(engines) -> None:
    import iris

    iris.createIRIS(engines["conn"]).classMethodValue("%SYSTEM.SQL", "Purge")


def _same_value(got, want, floats: bool) -> bool:
    if floats and want is not None:
        if isinstance(got, bool) or not isinstance(got, (int, float, Decimal)):
            return False
        return math.isclose(float(got), want, rel_tol=1e-9, abs_tol=1e-12)
    return canon(got) == canon(want)


def agrees(a: Answer, case: ct.Case) -> bool:
    if case.expected == ct.TYPE_ERROR:
        return a.rows is None and "TypeError" in (a.error or "")
    if a.rows is None or len(a.rows) != len(case.expected):
        return False
    return all(len(g) == len(w) and all(_same_value(x, y, case.floats) for x, y in zip(g, w))
               for g, w in zip(a.rows, case.expected))


@pytest.mark.parametrize("family", FAMILIES)
def test_every_route_gives_opencyphers_answer(engines, stored, family):
    started = time.time()
    cases = [c for c in ct.all_cases() if c.family == family][SHARD[0]::SHARD[1]]
    found: List[dict] = []
    for case in cases:
        _purge(engines)  # each case from its own plans: a cached one keeps an earlier case's types (R308)
        answers = {route: run(engines, route, case.text, case.params) for route in ROUTES}
        wrong = {k: a.show() for k, a in answers.items() if not agrees(a, case)}
        if wrong:
            found.append({"query": case.text, "params": case.params, "form": case.form, "label": case.label,
                          "expected": case.expected, "answers": wrong})
    _write_report(f"crosstype-{family}-{stored}-{SHARD[0]}of{SHARD[1]}", {"driver": DRIVER, "cases": len(cases),
                                                    "seconds": round(time.time() - started, 1),
                                                    "disagreements": found})
    assert not found, f"{len(found)} of {len(cases)} {family} cases disagree (driver {DRIVER}, report {REPORT}):\n" + \
        "\n".join(f"  [{d['form']}] {d['label']}: {d['query']} {d['params'] or ''}\n    expected {d['expected']}\n"
                  + "\n".join(f"    {k}: {v['error'] or v['rows']}" for k, v in d["answers"].items())
                  for d in found[:30])


# ---------------------------------------------------------------- elements of a WITH-held list read by position

POSITIONAL = [1, 3.14, "a", "b", False, True, None, [1], [1, 2], {"k": 1}]
_LIST = "[" + ", ".join(ct.cy_text(v) for v in POSITIONAL) + "]"
_PAIRS = (f"WITH {_LIST} AS types UNWIND range(0, size(types) - 1) AS i UNWIND range(0, size(types) - 1) AS j ")


def _decoded(v):
    if isinstance(v, str) and v[:1] in "[{":
        return json.loads(v)
    return v


@pytest.mark.parametrize("op", ct.OPS)
@pytest.mark.parametrize("shape", ["alias", "direct"])
def test_elements_read_by_position_compare_by_type(engines, op, shape):
    """TCK Comparison2 [3]'s statement over a literal list (4.6.0 R308: `1 <= 'a'` held, and the elements came back as
    text, '1' for 1 and '0' for false). Only two values of one kind order: of the pairs here, (1, 3.14), ('a', 'b'),
    (false, true) and ([1], [1, 2]) under `<` (worked by hand); every operator's pairs from crosstype.compare."""
    if shape == "alias":
        q = (_PAIRS + f"WITH types[i] AS lhs, types[j] AS rhs WHERE i <> j WITH lhs, rhs, lhs {op} rhs AS result "
             "WHERE result RETURN lhs, rhs")
    else:
        q = _PAIRS + f"WITH types, i, j WHERE i <> j AND types[i] {op} types[j] RETURN types[i] AS lhs, types[j] AS rhs"
    pairs = [(a, b) for i, a in enumerate(POSITIONAL) for j, b in enumerate(POSITIONAL)
             if i != j and ct.compare(a, op, b)]
    if op == "<":
        assert sorted(map(repr, pairs)) == sorted(map(repr, [(1, 3.14), ("a", "b"), (False, True), ([1], [1, 2])]))
    want = sorted(canon_row(p) for p in pairs)
    for route in ROUTES:
        a = run(engines, route, q, {})
        assert a.rows is not None, (route, a.error)
        assert sorted(canon_row(tuple(_decoded(v) for v in row)) for row in a.rows) == want, (route, a.rows)


def canon_row(row):
    return repr(tuple(canon(v) for v in row))


@pytest.mark.parametrize("q, want", [
    # fixes64's cases, worked by hand (release d7e6c340 and 4.5.x answered '0' for false and [1] for the string '[1]')
    ("UNWIND [1, ['a'], false] AS v RETURN v", [1, ["a"], False]),
    ("UNWIND ['[1]', ['a'], 1] AS v RETURN v", ["[1]", ["a"], 1]),
    ("UNWIND ['text', '7', '[1]', 1.5, ['list'], false] AS v RETURN v", ["text", "7", "[1]", 1.5, ["list"], False]),
    ("UNWIND [1, ['a'], false, {k: 1}] AS v RETURN collect(v) AS l", [[1, ["a"], False, {"k": 1}]]),
    ("UNWIND [false, true] AS v RETURN collect(v) AS l", [[False, True]]),
])
def test_an_unwound_known_list_answers_each_element_as_its_type(engines, q, want):
    for route in ROUTES:
        _purge(engines)
        a = run(engines, route, q, {})
        assert a.rows is not None, (route, a.error)
        got = [_decoded(r[0]) if isinstance(want[0], list) and len(want) == 1 else r[0] for r in a.rows]
        assert sorted(canon_row((g,)) for g in got) == sorted(canon_row((w,)) for w in want), (route, a.rows)
