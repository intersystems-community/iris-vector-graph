"""4.6.0 R077: whether ^NKG is there is asked of the namespace every time, never remembered by the IRIS process.

`Graph.KG.NKGAccelLoader.UseNKG()` kept its first answer in `^||NKGAccel("useNKG")` for the life of the server
process. A process that asked before `^NKG` was built (a fresh namespace, or right after an erase dropped it) kept
reading the legacy no-shard `^KG("out", node, pred, dst)` layout after `sync()` built `^NKG`, so PPR and the k-hop
neighbourhood answered the seed alone, while a new connection (a new process) answered correctly. Process-private
globals also span namespaces, so the answer leaked across them too. Found in IVGALT
(`test_262_alt_schema_paths.py::test_pagerank_ppr_and_betweenness` failed in-process and passed from a fresh one).

IVG262ALT, schema `IVGALT`, built anew. Everything below runs on one connection, so in one IRIS process.
Scratch namespace: `IVG_NS_IVG262ALT` (default IVG262ALT). The file drops and rebuilds it (4.6.0 R191): point it at
a namespace no other run is using.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262, scratch_vars

pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG_NS_IVG262ALT")]

NS, SCHEMA = os.environ.get("IVG_NS_IVG262ALT", ""), "IVGALT"


@pytest.fixture(scope="module")
def engine():
    from iris_vector_graph import IRISGraphEngine

    ns262.recreate(NS)
    conn = ns262.connect(NS)
    e = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    e.initialize_schema()
    for n in ("c:1", "c:2", "c:3"):
        e.create_node(n)
    e.create_edge("c:1", "CHAIN", "c:2")
    e.create_edge("c:2", "CHAIN", "c:3")
    conn.commit()
    yield e
    conn.close()


def _nkg_built(engine) -> bool:
    return bool(int(engine._iris_obj().isDefined("^NKG", "$meta") or 0))


def test_the_process_sees_nkg_once_sync_builds_it(engine):
    native = engine._iris_obj()
    assert not _nkg_built(engine), "a fresh namespace's setup builds no ^NKG"
    # asked once with no ^NKG: the answer this process would have kept
    assert int(native.classMethodValue("Graph.KG.NKGAccelLoader", "UseNKG")) == 0
    engine.sync()
    assert _nkg_built(engine)
    # the same process, after the build
    assert int(native.classMethodValue("Graph.KG.NKGAccelLoader", "UseNKG")) == 1
    reached = native.classMethodValue("Graph.KG.NKGAccelTraversal", "KHopNeighbors", "c:1", 2, 100)
    assert all(f'"id":"{n}"' in str(reached) for n in ("c:1", "c:2", "c:3")), reached


def test_ppr_in_the_same_process_reaches_the_chain(engine):
    scores = {s["id"]: s["score"] for s in engine.ppr("c:1", top_k=3)["scores"]}
    assert set(scores) == {"c:1", "c:2", "c:3"}, scores
    assert max(scores, key=scores.get) == "c:1"
