"""SCCJson writes its rows as JSON text, at a42db224's speed, with R306's ids (4.6.0 speed gate).

R306 built each SCC row as a %DynamicObject so an id with a quote stays readable; at 20,000 rows that cost 100 ms of the
gate's nkg_first_read_after_erase (SCCJson 0.35 s against 0.45 s, +9% on the path). The rows are text again, the id
escaped by $ZConvert JSON (as Row escapes it): every id below comes back as it was stored.

Runs in the scratch namespace ``IVG_NAMESPACE`` names (an IVG460*/IVG262* one with IVG installed), erasing its graph.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith(("IVG460", "IVG262")),
                       reason="IVG_NAMESPACE must name an IVG460*/IVG262* scratch namespace: the case erases its graph"),
]

ODD = ['q"t', "b\\s", "sl/ash", "tab\tx", "ünï中", "10", "0.5", "-3", "x\u0001y"]


@pytest.fixture(scope="module")
def conn():
    c = ns262.connect(NS)
    yield c
    ns262.native(c).classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.close()


def test_scc_rows_carry_every_id_as_stored(conn, monkeypatch):
    from iris_vector_graph import IRISGraphEngine

    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    native = ns262.native(conn)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    e = IRISGraphEngine(conn)
    ring = list(ODD)
    for a, b in zip(ring, ring[1:] + ring[:1]):
        e.create_edge(a, "R", b)
    e.create_edge("lone_a", "R", "lone_b")
    e.sync()
    out = str(native.classMethodValue("Graph.KG.Communities", "SCCJson", 100))
    assert out.startswith("OK:"), out
    got = json.loads(out[3:])
    by_id = {r["id"]: (r["component"], r["size"]) for r in got}
    assert sorted(by_id) == sorted(ring + ["lone_a", "lone_b"])
    assert {by_id[s] for s in ring} == {(0, len(ring))}  # the ring, the largest component, first
    assert by_id["lone_a"][1] == by_id["lone_b"][1] == 1
    assert all(isinstance(r["component"], int) and isinstance(r["size"], int) for r in got)
    assert sorted((r["id"], r["size"]) for r in e.strongly_connected_components(top_k=0)) == sorted(
        [(s, len(ring)) for s in ring] + [("lone_a", 1), ("lone_b", 1)]
    )
