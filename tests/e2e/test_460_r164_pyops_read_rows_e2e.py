"""4.6.0 R164, live: `PyOps.ReadRows` answers across the `iris.cls` boundary as its callers read it.

The four PyOps reads call `iris.cls("Graph.KG.PyOps").ReadRows(...)` from embedded Python, so its answer crosses
IRIS and back: the rows must come back a Python list, the absent-table answer `None` (not ""), and a failure a
RuntimeError with ReadFailure's text (R146's file covers PermissionError for -99). A throwaway class
(tests/e2e/throwaway460.py) makes the calls. VectorSearch then answers through it, by hand: r164:a holds e1 and
r164:b holds e2 (8 dimensions), so a search for e1 ranks a first at cosine similarity 1 and b at 0.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt
from tests.e2e import throwaway460 as throwaway

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

PROBE = "IVGR164.Probe"
PROBE_CLASS = f"""
Class {PROBE} Extends %RegisteredObject
{{
ClassMethod Rows() As %String [ Language = python ]
{{
    import iris
    rows = iris.cls("Graph.KG.PyOps").ReadRows("probe", "SELECT 1, 'x'", (), "", "", 0)
    return type(rows).__name__ + ":" + repr([tuple(r) for r in rows])
}}
ClassMethod Absent() As %String [ Language = python ]
{{
    import iris
    return repr(iris.cls("Graph.KG.PyOps").ReadRows("probe", "SELECT 1 FROM {alt.SCHEMA}.r164_none", (), "", "", 1))
}}
ClassMethod Failed() As %String [ Language = python ]
{{
    import iris
    try:
        iris.cls("Graph.KG.PyOps").ReadRows("probe", "SELECT 1 FROM {alt.SCHEMA}.r164_none", (), "", "", 0)
    except Exception as exc:
        return type(exc).__name__ + ":" + str(exc)
    return "no exception"
}}
}}
"""


def _e(i: int) -> list:
    return [1.0 if j == i else 0.0 for j in range(alt.DIM)]


@pytest.fixture(scope="module")
def probe():
    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    for n, vec in (("a", _e(0)), ("b", _e(1))):
        engine.create_node(f"r164:{n}", labels=["R164"])
        engine.store_embedding(f"r164:{n}", vec)
    conn.commit()
    throwaway.load(conn, PROBE, PROBE_CLASS)
    yield engine
    throwaway.delete(conn, PROBE)
    for n in ("a", "b"):
        engine.delete_node(f"r164:{n}", graph=None)
    conn.commit()
    conn.close()


def _call(method: str) -> str:
    return alt.marked(alt.session([f'write "<<R164:",##class({PROBE}).{method}(),":R164>>",!']), "R164")


def test_the_rows_come_back_a_list(probe):
    assert _call("Rows") == "list:[(1, 'x')]"


def test_an_absent_table_comes_back_none(probe):
    assert _call("Absent") == "None"


def test_a_failure_comes_back_a_runtime_error_naming_it(probe):
    out = _call("Failed")
    assert out.startswith("RuntimeError:probe failed: SQLCODE -30"), out


def test_vector_search_answers_through_it(probe):
    vec = json.dumps(_e(0))
    out = alt.session([
        f"set v=##class(%DynamicArray).%FromJSON(\"{vec}\")",
        'write "<<R164:",##class(Graph.KG.PyOps).VectorSearch(v,5,"R164","").%ToJSON(),":R164>>",!',
    ])
    hits = [(h["id"], round(h["score"], 6)) for h in json.loads(alt.marked(out, "R164"))]
    assert hits == [("r164:a", 1.0), ("r164:b", 0.0)], hits
