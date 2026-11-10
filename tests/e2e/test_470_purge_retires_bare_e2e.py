"""4.7.0 row 6 (Tom's decision): a temporal purge retires the endpoint nodes it leaves bare, by default.

opsreview (4.4.x): every temporal event wrote a new target node, and `purge_raw_before` / `purge_before` deleted the
old edges and kept every endpoint, so the graph grew edgeless nodes without limit (941 nodes to 7.2 million).
`retire_bare_nodes=True` has deleted them since 4.4.1, but it was off by default. Now it is on: a purge with the
defaults leaves no edgeless endpoint that it made bare, and a node with another edge, a label or a property stays.
`retire_bare_nodes=False` keeps every endpoint, as before.

The known answer: sensor -EMITS-> r0, r1, r2, keeper, linked (temporal, all before t0 + 100); keeper has a
property; linked -KNOWS-> other (not temporal). Purged at t0 + 100: sensor, r0, r1 and r2 are bare and go (4);
keeper, linked and other stay.

    IVG_R470_WR_NS=IVG470WR2 pytest tests/e2e/test_470_purge_retires_bare_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
T0 = 1_700_000_000

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


@pytest.fixture
def eng():
    from iris_vector_graph import IRISGraphEngine

    conn = _connect()
    yield IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    conn.close()


def _build(eng, u, graph):
    names = {k: f"{u}{k}" for k in ("sensor", "r0", "r1", "r2", "keeper", "linked", "other")}
    assert eng.create_node(names["keeper"], properties={"site": "north"}, graph=graph)
    assert eng.create_edge(names["linked"], "KNOWS", names["other"], graph=graph)
    for i, target in enumerate(("r0", "r1", "r2", "keeper", "linked")):
        eng.create_edge_temporal(names["sensor"], "EMITS", names[target], timestamp=T0 + i, graph=graph)
    return names


def _nodes(eng, u, graph) -> set:
    cur = eng.conn.cursor()
    cur.execute("SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ? AND node_id %STARTSWITH ?", [graph or "", u])
    return {str(r[0])[len(u):] for r in cur.fetchall()}


def _edgeless(eng, u, graph) -> set:
    cur = eng.conn.cursor()
    cur.execute(
        "SELECT n.node_id FROM Graph_KG.nodes n WHERE n.graph_id = ? AND n.node_id %STARTSWITH ? AND NOT EXISTS "
        "(SELECT 1 FROM Graph_KG.rdf_edges e WHERE e.graph_id = n.graph_id AND (e.s = n.node_id OR e.o_id = n.node_id))",
        [graph or "", u])
    return {str(r[0])[len(u):] for r in cur.fetchall()}


ALL = {"sensor", "r0", "r1", "r2", "keeper", "linked", "other"}
KEPT = {"keeper", "linked", "other"}


def test_purge_raw_before_retires_the_endpoints_it_leaves_bare_by_default(eng):
    u, graph = "t470p" + uuid.uuid4().hex[:8], "g470purge"
    _build(eng, u, graph)
    assert _nodes(eng, u, graph) == ALL
    result = eng.purge_raw_before(T0 + 100, graph=graph)
    assert result.nodes_retired == 4, result
    assert _nodes(eng, u, graph) == KEPT
    assert _edgeless(eng, u, graph) == {"keeper"}, "an edgeless endpoint the purge made bare is still there"


def test_retire_bare_nodes_false_keeps_every_endpoint(eng):
    u, graph = "t470p" + uuid.uuid4().hex[:8], "g470purge"
    _build(eng, u, graph)
    result = eng.purge_raw_before(T0 + 100, graph=graph, retire_bare_nodes=False)
    assert result.nodes_retired == 0, result
    assert _nodes(eng, u, graph) == ALL
    assert _edgeless(eng, u, graph) == {"sensor", "r0", "r1", "r2", "keeper"}


def test_purge_before_retires_them_in_the_default_graph_by_default(eng):
    u = "t470p" + uuid.uuid4().hex[:8]
    _build(eng, u, None)
    assert eng.purge_before(T0 + 100) >= 4  # every bare endpoint of the default graph's purged edges, ours included
    assert _nodes(eng, u, None) == KEPT


@pytest.mark.parametrize("cls", ("Graph.KG.TemporalIndex", "Graph.KG.TemporalIndexMS"))
def test_the_three_argument_objectscript_call_keeps_the_nodes(eng, cls):
    """The class's own default stays off: opsreview calls ##class(Graph.KG.TemporalIndex).PurgeRawBefore(graph,
    tsEnd, tsStart) from ObjectScript and relies on it keeping shared nodes (they sweep orphans themselves). The
    Python API's default is on (row 6); the class's must not follow it by accident. Called through the Native API with
    the same three arguments: every node stays, and the answer is "deleted:skipped"."""
    import iris

    u, graph = "t470p" + uuid.uuid4().hex[:8], "g470purge"
    unit = 1000 if cls.endswith("MS") else 1
    names = {k: f"{u}{k}" for k in ("sensor", "r0", "r1", "r2", "keeper", "linked", "other")}
    assert eng.create_node(names["keeper"], properties={"site": "north"}, graph=graph)
    assert eng.create_edge(names["linked"], "KNOWS", names["other"], graph=graph)
    native = iris.createIRIS(eng.conn)
    for i, target in enumerate(("r0", "r1", "r2", "keeper", "linked")):
        native.classMethodVoid(cls, "InsertEdge", graph, names["sensor"], "EMITS", names[target], (T0 + i) * unit, 1.0)
    answer = str(native.classMethodValue(cls, "PurgeRawBefore", graph, (T0 + 100) * unit, 0))
    assert answer == "5:0", answer
    assert _nodes(eng, u, graph) == ALL
