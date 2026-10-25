"""An erase that cannot have made ^NKG stale leaves it alone.

`Graph.KG.Eraser.EraseNodeIds` (and `EraseNodes`, which shares its body) used to
drop ^NKG whenever it removed an edge. opsreview's retention purge calls it once per
tenant every four hours for per-event nodes that were never interned in ^NKG, so each
purge threw away a derived index it had not damaged.

The argument for keeping it: ^NKG interns node ids with no graph dimension
(`^NKG("$NI", id)`), and every writer of an ^NKG edge (Adjacency.Put,
Traversal.BuildNKG, the Rust kg_build_nkg) interns BOTH endpoints. An edge with an
un-interned endpoint is therefore not in ^NKG, and removing it changes nothing there.

What these tests pin, against the server and not a double (the guarantee is the
state of a global after the call):

* ids never interned: ^NKG is byte-identical afterwards, apart from the version that
  `ArnoAccel.BumpVersion` still raises, and the BFS fast path answers as before;
* any interned id in the list, alone or mixed with un-interned ones: ^NKG is dropped
  as before;
* an edge between an interned node and an un-interned erased node: ^NKG intact, and
  still a subset of what ^KG holds (not stale);
* a graph erase still drops ^NKG, whatever was interned;
* the erase's effect on rows and ^KG is the same whether or not ^NKG was kept.

`SKIP_IRIS_TESTS=true` fails rather than skips: a skipped test that asserts what a
Kill left behind is indistinguishable from a passing one.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVGERASENKG_LINK"
SENTINEL = ("ivgtest", "sentinel")


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "the ^NKG-kept erase tests assert what a Kill leaves behind in a global. "
            "SKIP_IRIS_TESTS=true is not an acceptable outcome: start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: ^NKG cannot be observed from Python.")


# --- reading ^NKG and ^KG ------------------------------------------------------------


def _walk(iris_obj, root: str, prefix: tuple, out: dict, skip=()) -> None:
    """Every valued node under `root(*prefix)` as `{subscripts: value}`."""
    current = iris_obj.nextSubscript(False, root, *prefix, "")
    while current is not None and current != "":
        subs = prefix + (current,)
        if subs not in skip:
            state = iris_obj.isDefined(root, *subs)
            if state in (1, 11):
                out[subs] = str(iris_obj.getString(root, *subs))
            _walk(iris_obj, root, subs, out, skip)
        current = iris_obj.nextSubscript(False, root, *prefix, current)


def _nkg(iris_obj, without_version: bool = True) -> dict:
    """The whole of ^NKG. The version is left out by default: BumpVersion raises it
    on every erase, kept index or not, and that is not what is being asserted."""
    out: dict = {}
    skip = (("$meta", "version"),) if without_version else ()
    _walk(iris_obj, "^NKG", (), out, skip)
    return out


def _nkg_edges(iris_obj) -> set:
    """^NKG(-1) as a set of (s, p, o) id strings, through $ND / $LS."""
    edges = set()
    for subs, _ in _nkg_subtree(iris_obj, -1).items():
        s_idx, neg_p, o_idx = subs
        s = iris_obj.getString("^NKG", "$ND", int(s_idx))
        o = iris_obj.getString("^NKG", "$ND", int(o_idx))
        p = iris_obj.getString("^NKG", "$LS", -int(neg_p) - 1)
        edges.add((s, p, o))
    return edges


def _nkg_subtree(iris_obj, head) -> dict:
    out: dict = {}
    _walk(iris_obj, "^NKG", (head,), out)
    return {k[1:]: v for k, v in out.items()}


def _kg_edges(iris_obj) -> set:
    """Every ^KG("out", graph, s, p, o) of every graph, as (s, p, o)."""
    out: dict = {}
    _walk(iris_obj, "^KG", ("out",), out)
    return {(k[2], k[3], k[4]) for k in out if len(k) == 5}


def _kg_dump(iris_obj, *heads) -> dict:
    out: dict = {}
    for head in heads:
        _walk(iris_obj, "^KG", (head,), out)
    return out


def _bfs(iris_obj, seed: str) -> dict:
    iris_obj.classMethodVoid("Graph.KG.NKGAccelLoader", "ResetCache")
    raw = iris_obj.classMethodValue("Graph.KG.NKGAccelTraversal", "KHopNeighbors", seed, 2, 1000)
    parsed = json.loads(raw)
    if "error" in parsed:
        return {"error": parsed["error"]}
    return {n["id"]: n["dist"] for n in parsed["nodes"]}


def _interned(iris_obj, node_id: str) -> bool:
    return iris_obj.isDefined("^NKG", "$NI", node_id) in (1, 11)


def _rows(conn, ids) -> dict:
    """How many rows of each graph table name any of `ids`."""
    cursor = conn.cursor()
    marks = ",".join("?" * len(ids))
    counts = {}
    try:
        for table, col in (
            ("nodes", "node_id"),
            ("rdf_labels", "s"),
            ("rdf_props", "s"),
        ):
            cursor.execute(f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE {col} IN ({marks})", list(ids))
            counts[table] = int(cursor.fetchone()[0])
        cursor.execute(
            f"SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s IN ({marks}) OR o_id IN ({marks})",
            list(ids) + list(ids),
        )
        counts["rdf_edges"] = int(cursor.fetchone()[0])
    finally:
        cursor.close()
    return counts


# --- the scenario --------------------------------------------------------------------


class Scenario:
    """Un-interned nodes with edges inside a live ^NKG.

    The order is the point. ^NKG interns on write only once `nodeCount` exists, so
    the un-interned side is written first (^NKG absent: nothing interns), then the
    skeleton is initialised (what bulk_load_session does, and what is left after a
    purge dropped the index), then the interned side is written (Adjacency.Put now
    interns both endpoints).
    """

    def __init__(self, engine, iris_obj, conn):
        tag = uuid.uuid4().hex[:8]
        self.engine, self.iris, self.conn = engine, iris_obj, conn
        self.u1, self.u2, self.u3 = (f"ivgenk{tag}:u{i}" for i in (1, 2, 3))
        self.mixed = f"ivgenk{tag}:mixed"  # interned later, linked to an un-interned node
        self.i1, self.i2, self.i3 = (f"ivgenk{tag}:i{i}" for i in (1, 2, 3))
        self.uids = (self.u1, self.u2, self.u3)
        self.all_ids = self.uids + (self.mixed, self.i1, self.i2, self.i3)

    def build(self, intern_everything: bool = False):
        e = self.engine
        for node in self.all_ids:
            e.create_node(node, labels=["IvgEraseNkg"], properties={"k": node})
        # Never interned: written while ^NKG does not exist.
        e.create_edge(self.u1, PRED, self.u2)
        e.create_edge(self.u2, PRED, self.u3)
        e.create_edge(self.mixed, PRED, self.u3)
        e.conn.commit()
        if intern_everything:
            # Every endpoint interned, the U ones included.
            self.iris.classMethodValue("Graph.KG.Traversal", "BuildNKG")
        else:
            self.iris.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
        # Interned: written with ^NKG live, so Put interns both endpoints.
        e.create_edge(self.i1, PRED, self.i2)
        e.create_edge(self.i2, PRED, self.i3)
        e.create_edge(self.mixed, PRED, self.i1)
        e.conn.commit()
        self.iris.set("planted", "^NKG", *SENTINEL)
        return self

    def erase(self, ids, graph: str = ""):
        return int(
            self.iris.classMethodValue("Graph.KG.Eraser", "EraseNodeIds", graph, json.dumps(list(ids)))
        )


@pytest.fixture
def scn(iris_connection, iris_master_cleanup):
    _require_iris(iris_connection)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    return Scenario(engine, engine._iris_obj(), iris_connection)


def test_scenario_has_the_interning_it_claims(scn):
    """The other tests are only as good as this: who is interned and who is not."""
    scn.build()
    for node in scn.uids:
        assert not _interned(scn.iris, node), f"{node} was interned; the fixture proves nothing"
    for node in (scn.mixed, scn.i1, scn.i2, scn.i3):
        assert _interned(scn.iris, node), f"{node} was not interned"
    # The invariant the whole change rests on: no ^NKG edge has an un-interned end.
    for s, _p, o in _nkg_edges(scn.iris):
        assert _interned(scn.iris, s) and _interned(scn.iris, o)
    # ...and ^KG holds edges ^NKG does not (the U chain).
    assert (scn.u1, PRED, scn.u2) in _kg_edges(scn.iris)
    assert (scn.u1, PRED, scn.u2) not in _nkg_edges(scn.iris)


def test_erase_of_never_interned_ids_keeps_nkg(scn):
    scn.build()
    iris_obj = scn.iris
    before = _nkg(iris_obj)
    version_before = int(iris_obj.getString("^NKG", "$meta", "version"))
    bfs_before = _bfs(iris_obj, scn.i1)
    assert before[SENTINEL] == "planted"
    assert int(before[("$meta", "nodeCount")]) > 0
    assert bfs_before.get(scn.i2) == 1, bfs_before

    removed = scn.erase(scn.uids)

    assert removed == 3
    after = _nkg(iris_obj)
    assert after == before, "the erase changed ^NKG although it touched nothing ^NKG holds"
    assert after[SENTINEL] == "planted"
    assert after[("$meta", "nodeCount")] == before[("$meta", "nodeCount")]
    assert _bfs(iris_obj, scn.i1) == bfs_before
    # The other invalidation is unchanged: the version still moves, so a cache keyed on
    # it (nkg_adj, betweenness) is refilled rather than trusted.
    assert int(iris_obj.getString("^NKG", "$meta", "version")) > version_before
    # ...and the erase did its work in the rows and in ^KG.
    assert _rows(scn.conn, scn.uids) == {"nodes": 0, "rdf_labels": 0, "rdf_props": 0, "rdf_edges": 0}
    kg = _kg_edges(iris_obj)
    assert not {e for e in kg if scn.u1 in e or scn.u2 in e or scn.u3 in e}


def test_erase_of_an_interned_id_still_drops_nkg(scn):
    scn.build()
    iris_obj = scn.iris
    assert _interned(iris_obj, scn.i1)
    version_before = int(iris_obj.getString("^NKG", "$meta", "version"))

    removed = scn.erase([scn.i1])

    assert removed == 1
    assert not iris_obj.isDefined("^NKG", *SENTINEL), "an interned id was erased and ^NKG survived"
    assert not iris_obj.isDefined("^NKG", "$meta", "nodeCount")
    assert not iris_obj.isDefined("^NKG", "$NI", scn.i2)
    # DropNKG keeps the version rising (4.1.1).
    assert int(iris_obj.getString("^NKG", "$meta", "version")) > version_before
    assert _rows(scn.conn, [scn.i1]) == {"nodes": 0, "rdf_labels": 0, "rdf_props": 0, "rdf_edges": 0}


def test_mixed_list_drops_nkg(scn):
    scn.build()
    iris_obj = scn.iris

    removed = scn.erase([scn.u1, scn.i2])

    assert removed == 2
    assert not iris_obj.isDefined("^NKG", *SENTINEL), "one interned id in the list must drop ^NKG"
    assert not iris_obj.isDefined("^NKG", "$meta", "nodeCount")
    assert _rows(scn.conn, [scn.u1, scn.i2]) == {"nodes": 0, "rdf_labels": 0, "rdf_props": 0, "rdf_edges": 0}


def test_edge_between_interned_and_never_interned_keeps_nkg_correct(scn):
    scn.build()
    iris_obj = scn.iris
    # `mixed` is interned (it got an edge once ^NKG was live) and linked to U3, which is
    # not. Erase only U3: the (mixed -> U3) edge goes from ^KG, and was never in ^NKG.
    assert _interned(iris_obj, scn.mixed) and not _interned(iris_obj, scn.u3)
    assert (scn.mixed, PRED, scn.u3) in _kg_edges(iris_obj)
    assert (scn.mixed, PRED, scn.u3) not in _nkg_edges(iris_obj)
    before = _nkg(iris_obj)
    bfs_before = _bfs(iris_obj, scn.mixed)

    removed = scn.erase([scn.u3])

    assert removed == 1
    assert _nkg(iris_obj) == before
    assert _bfs(iris_obj, scn.mixed) == bfs_before
    # Not stale: every edge ^NKG holds is still in ^KG after the erase.
    stale = _nkg_edges(iris_obj) - _kg_edges(iris_obj)
    assert not stale, f"^NKG holds edges ^KG no longer has: {sorted(stale)}"
    assert (scn.mixed, PRED, scn.u3) not in _kg_edges(iris_obj)


def test_a_graph_erase_still_drops_nkg(scn):
    """EraseGraph is not changed: it still drops ^NKG when ^KG held the graph's edges.

    The edges are adjacency with no row behind them (written by EdgeScan, as an older
    install or a half-finished load leaves them) and between ids that were never
    interned, which is the case this change keeps ^NKG for in EraseNodeIds. A graph
    erase has no such exemption: ^NKG's edge count and sketches cannot be attributed to
    one graph.
    """
    graph = f"ivgenk:{uuid.uuid4().hex[:6]}"
    iris_obj = scn.iris
    iris_obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", f"{graph}:a", PRED, f"{graph}:b", "1.0", graph)
    iris_obj.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
    scn.engine.create_edge(f"{graph}:keep", PRED, f"{graph}:keep2")
    scn.engine.conn.commit()
    iris_obj.set("planted", "^NKG", *SENTINEL)
    assert not _interned(iris_obj, f"{graph}:a")
    assert iris_obj.isDefined("^KG", "out", graph)

    scn.engine.erase_graph(graph)

    assert not iris_obj.isDefined("^NKG", *SENTINEL), "a graph erase must still drop ^NKG"
    assert not iris_obj.isDefined("^NKG", "$meta", "nodeCount")


def test_rows_and_kg_are_the_same_whether_nkg_is_kept_or_dropped(scn):
    """The erase's own result must not depend on the ^NKG decision.

    The same data is erased twice: once with the U ids un-interned (^NKG kept) and once
    with every id interned by a full BuildNKG (^NKG dropped).
    """

    def run(intern_everything: bool):
        s = Scenario(scn.engine, scn.iris, scn.conn).build(intern_everything)
        kept_before = bool(scn.iris.isDefined("^NKG", *SENTINEL))
        removed = s.erase(s.uids)
        kept_after = bool(scn.iris.isDefined("^NKG", *SENTINEL))
        survivors = (s.mixed, s.i1, s.i2, s.i3)
        norm = lambda d: {  # noqa: E731 - ids carry a per-run tag; compare by suffix
            tuple(str(x).split(":")[-1] for x in k): v for k, v in d.items()
        }
        result = {
            "removed": removed,
            "rows": _rows(scn.conn, s.uids),
            "survivor_rows": _rows(scn.conn, survivors),
            "kg": norm(_kg_dump(scn.iris, "out", "in")),
            "labels": norm(_kg_dump(scn.iris, "label")),
        }
        return result, kept_before, kept_after

    kept_result, kb, ka = run(intern_everything=False)
    assert kb and ka, "setup: the U-only erase should have kept ^NKG"
    scn.iris.classMethodValue("Graph.KG.Eraser", "EraseAll")

    dropped_result, kb, ka = run(intern_everything=True)
    assert kb and not ka, "setup: the all-interned erase should have dropped ^NKG"

    assert kept_result == dropped_result


def test_prefix_erase_shares_the_rule(scn):
    """EraseNodes selects ids by prefix and runs the same body."""
    scn.build()
    iris_obj = scn.iris
    before = _nkg(iris_obj)
    # u1..u3 share the run's tag prefix with everything else, so select by their own
    # distinct "u" stem: ids are "<tag>:u1" and so on; "<tag>:u" matches only them.
    prefix = scn.u1[:-1]
    removed = int(iris_obj.classMethodValue("Graph.KG.Eraser", "EraseNodes", "", prefix))
    assert removed == 3
    assert _nkg(iris_obj) == before

    # An interned prefix drops it.
    removed = int(iris_obj.classMethodValue("Graph.KG.Eraser", "EraseNodes", "", scn.i1[:-1]))
    assert removed == 3
    assert not iris_obj.isDefined("^NKG", *SENTINEL)

