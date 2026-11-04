"""4.6.0 R265: `Graph.KG.NKGAccel.BFSJson` outside USER answers a chunked Arno reply and leaves the caller where it was.

Measured on HEAD (2026-11-04, IVG460FXZ, Arno 0.1.0): the library's `kg_bfs_global` writes a large reply into USER's
`^ArnoKG("bfs_result", 1..n)`, answers `CHUNKED:BFS:<n>`, and returns with the process in USER. BFSJson read the
chunks there (so its answer was right), staged the hits in USER's `^ArnoKG("bfs_r")`, and returned in USER: the
connection's next statement ran against USER's tables. `SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = hub` then
answered 0 for a hub with 900 edges. A reply small enough to come back whole (400 hits) leaves the process where it was.

The graph, by hand: hub q<run>h -R265C-> q<run>1 .. q<run>900 in this namespace's default graph (900
one-hop hits, a reply of 6 chunks). One hop from the hub answers the 900 leaves; the process is still in this
namespace afterwards, and the SQL count of the hub's edges is 900.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

NS = os.environ.get("IVG_NAMESPACE", "USER")
N = 900


@pytest.fixture(scope="module")
def star(iris_connection):
    import iris

    from iris_vector_graph import IRISGraphEngine

    if NS.upper() == "USER":
        pytest.skip("the defect is outside USER: run with IVG_NAMESPACE naming a scratch namespace")
    engine = IRISGraphEngine(iris_connection)
    native = iris.createIRIS(iris_connection)
    if not native.classMethodValue("Graph.KG.ArnoAccel", "Load"):
        pytest.skip("the Arno library does not load in this container")
    native.classMethodValue("%SYSTEM.Process", "SetNamespace", NS)
    # short ids: the whole adjacency is one $ZF argument (at most 34,808 characters), so long ones would take the
    # ObjectScript path ("size-gate") and never reach the library
    run = uuid.uuid4().hex[:4]
    hub = f"q{run}h"
    leaves = [f"q{run}{i}" for i in range(1, N + 1)]
    assert engine.bulk_create_edges([{"source_id": hub, "predicate": "R265C", "target_id": t} for t in leaves]) == N
    iris_connection.commit()
    assert engine.sync()
    try:
        yield engine, native, hub, set(leaves)
    finally:
        native.classMethodValue("%SYSTEM.Process", "SetNamespace", NS)
        cur = iris_connection.cursor()
        with contextlib.suppress(Exception):
            cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'R265C'", [hub])
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ? OR node_id %STARTSWITH ?", [hub, f"q{run}"])
            iris_connection.commit()
        engine.sync()


def test_a_chunked_reply_is_read_and_the_caller_stays_in_its_namespace(star, iris_connection):
    from iris_vector_graph.engine import _bfs_stream_pages

    engine, native, hub, leaves = star
    raw = str(native.classMethodValue("Graph.KG.NKGAccel", "BFSJson", hub, "[]", 1, 0))
    here = str(native.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    assert here == NS, f"BFSJson left the process in {here}"
    path = str(native.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsPath"))
    chunks = int(native.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsChunks"))
    assert path == "native" and chunks > 1, (path, chunks)
    assert raw.startswith("SORTED:") and raw != "SORTED:0", raw
    hits = list(_bfs_stream_pages(iris_connection, raw.split(":")[1]))
    assert {h["o"] for h in hits} == leaves and {int(h["step"]) for h in hits} == {1}
    cur = iris_connection.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ?", [hub])
    assert cur.fetchone()[0] == N
