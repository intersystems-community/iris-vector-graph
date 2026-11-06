"""4.6.0 R152, live: `BuildKG` returns `BuildNKG`'s failure, and `BetweennessNeighborhoodOS` the workers'.

- `Graph.KG.TraversalBuild.BuildKG` called `BuildNKG` inside a Try/Catch that wrote to messages.log, then answered
  `$$$OK`; a status `BuildNKG` returned was not read at all. `engine.sync()` reported success over a stale `^NKG`.
- `Graph.KG.NKGAccelCentrality.BetweennessNeighborhoodOS` ran `Do queue.Sync()`: a worker that failed left its
  sources unscored and the call answered `OK:` with the other workers' sums.

Each failure is forced through a throwaway subclass (tests/e2e/throwaway460.py) that overrides one method: `BuildNKG`
returning an error, `BuildNKG` throwing one (the real one does both), and `BetweennessWorker` returning an error.

The graph, by hand, in a namespace emptied first: the path a -S-> b -S-> c. From a, two hops out reach {a, b, c}.
`BetweennessNeighborhoodOS` runs directed Brandes from all 3 sources (sampleSize 200 >= 3, so scale 1): the pair
(a, c) passes through b, so b scores 1, a and c 0 (left out of the answer). Before 4.6.0 R050 it symmetrized the
edges and counted the pair from both ends: b 2.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt
from tests.e2e import throwaway460 as throwaway

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

EDGES = (("a", "S", "b"), ("b", "S", "c"))
CLASSES = {
    "IVGR152.FailingNKG": """
Class IVGR152.FailingNKG Extends Graph.KG.TraversalBuild
{
ClassMethod BuildNKG() As %Status
{
    Quit $$$ERROR($$$GeneralError, "r152 forced BuildNKG status")
}
}""",
    "IVGR152.ThrowingNKG": """
Class IVGR152.ThrowingNKG Extends Graph.KG.TraversalBuild
{
ClassMethod BuildNKG() As %Status
{
    $$$ThrowStatus($$$ERROR($$$GeneralError, "r152 forced BuildNKG throw"))
}
}""",
    "IVGR152.FailingWorker": """
Class IVGR152.FailingWorker Extends Graph.KG.NKGAccelCentrality
{
ClassMethod BetweennessWorker(parentPid As %Integer, workerN As %Integer, srcFirst As %Integer, srcLast As %Integer, scale As %Double) As %Status
{
    Quit $$$ERROR($$$GeneralError, "r152 forced worker failure")
}
}""",
}


def _id(n: str) -> str:
    return f"r152:{n}"


@pytest.fixture(scope="module")
def path():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for n in ("a", "b", "c"):
        engine.create_node(_id(n), labels=["R152"])
    for s, p, o in EDGES:
        engine.create_edge(_id(s), p, _id(o))
    conn.commit()
    engine.sync()
    for name, text in CLASSES.items():
        throwaway.load(conn, name, text)
    yield engine
    for name in CLASSES:
        throwaway.delete(conn, name)
    for n in ("a", "b", "c"):
        engine.delete_node(_id(n), graph=None)
    conn.commit()
    conn.close()


def _status(cls: str) -> str:
    out = alt.session(
        [f'set sc=##class({cls}).BuildKG() write "<<R152:",sc=1,"|",$System.Status.GetErrorText(sc),":R152>>",!']
    )
    return alt.marked(out, "R152")


def test_the_real_build_succeeds(path):
    assert _status("Graph.KG.TraversalBuild") == "1|"


@pytest.mark.parametrize(
    "cls,reason", [("IVGR152.FailingNKG", "r152 forced BuildNKG status"), ("IVGR152.ThrowingNKG", "r152 forced BuildNKG throw")]
)
def test_buildkg_returns_the_nkg_failure(path, cls, reason):
    ok, text = _status(cls).split("|", 1)
    assert ok == "0", text
    assert "BuildKG rebuilt ^KG but not ^NKG" in text and reason in text, text


def _neighborhood(cls: str) -> str:
    out = alt.session(
        [f'write "<<R152:",##class({cls}).BetweennessNeighborhood("{_id("a")}",2,200,20),":R152>>",!']
    )
    return alt.marked(out, "R152")


def test_the_neighborhood_answers_the_hand_worked_scores(path):
    import json

    raw = _neighborhood("Graph.KG.NKGAccelCentrality")
    assert raw.startswith("OK:"), raw
    assert {r["id"]: r["score"] for r in json.loads(raw[3:])} == {_id("b"): 1}


def test_a_failed_worker_fails_the_neighborhood(path):
    raw = _neighborhood("IVGR152.FailingWorker")
    assert raw.startswith("ERROR:Worker failed") and "r152 forced worker failure" in raw, raw
