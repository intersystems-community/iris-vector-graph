"""Spec 234 Phase 2 gate — the multigraph storage model against IRIS (T006–T009).

Four things, each proven on the server because each is a claim about globals or
catalog state that no Python double can be wrong about in the same way:

- T006: the per-graph mode (``^IVG.GraphMode``) — off by default, settable,
  refusing to turn off while parallel edges exist, and cleared by an erase;
- T007: ``EdgeScan.WriteAdjacencyKeyed`` / ``DeleteAdjacencyKeyed`` walk one triple
  from one edge to three and back, and a triple that returns to one edge is
  byte-identical to one that never had two;
- T008: ``BuildKG`` rebuilds exactly what 200 random keyed writes produced
  incrementally (FR-006);
- T009: the ``ekey`` migration on a pre-234 table changes neither ``^KG`` nor the
  ledger head (SC-003), and a second run reports ``already at 234``.

The layout under test (research R3, option 1)::

    ^KG("out", g, s, p, o)          = weight of the lowest live ekey
    ^KG("out", g, s, p, o, ekey)    = that edge's weight     } only while the triple
    ^KG("out", g, s, p, o, "#")     = live edge count        } has two or more edges
    ^KG("in",  g, o, p, s[, ekey])  = mirror (children, no counter)
    ^KG("deg", g, s), ^KG("degp", g, s, p) count edges, not neighbours

Runs against ``ivg-iris-enterprise`` only. ``iris_master_cleanup`` erases the whole
namespace before and after each test, and the fixture brings ``rdf_edges`` to 234.
"""

from __future__ import annotations

import contextlib
import json
import os
import random

import pytest

pytestmark = [pytest.mark.e2e]

G_STORE = "ivg234:storage"
G_SINGLE = "ivg234:single"
G_RANDOM = "ivg234:random"
G_MODE = "ivg234:mode"

PRED = "IVG234_LINKS"
SRC = "ivg234:s"
DST = "ivg234:o"

ADJ_STORES = ("out", "in", "deg", "degp")


# --- helpers -------------------------------------------------------------------------


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 234 asserts ^KG layout on the server; SKIP_IRIS_TESTS=true is not an "
            "acceptable outcome — start ivg-iris-enterprise."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection")


def _iris(conn):
    import iris as _iris_mod

    return _iris_mod.createIRIS(conn)


def _key(iris_obj, graph):
    return iris_obj.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph)


def _walk(iris_obj, gname, prefix, depth=7):
    """Every defined node under ``^gname(*prefix)`` as ``{subscripts: value}``."""
    found = {}

    def walk(subs, remaining):
        if remaining <= 0:
            return
        cur = iris_obj.nextSubscript(False, gname, *subs, "")
        while cur is not None and cur != "":
            node = subs + (cur,)
            if iris_obj.isDefined(gname, *node) in (1, 11):
                found[node[len(prefix) :]] = iris_obj.getString(gname, *node)
            walk(node, remaining - 1)
            cur = iris_obj.nextSubscript(False, gname, *subs, cur)

    walk(tuple(prefix), depth)
    return found


def _dump(iris_obj, graph):
    """The four adjacency stores of one graph, keyed by store."""
    k = _key(iris_obj, graph)
    return {store: _walk(iris_obj, "^KG", (store, k)) for store in ADJ_STORES}


def _dump_all(iris_obj):
    """Every graph's adjacency stores — what a migration must leave untouched."""
    return {store: _walk(iris_obj, "^KG", (store,), depth=8) for store in ADJ_STORES}


def _ok(status):
    assert str(status) == "1", f"%Status not OK: {status!r}"


def _write(iris_obj, graph, ekey, weight, s=SRC, p=PRED, o=DST):
    _ok(
        iris_obj.classMethodValue(
            "Graph.KG.EdgeScan", "WriteAdjacencyKeyed", s, p, o, str(weight), _key(iris_obj, graph), ekey
        )
    )


def _delete(iris_obj, graph, ekey, s=SRC, p=PRED, o=DST):
    _ok(
        iris_obj.classMethodValue(
            "Graph.KG.EdgeScan", "DeleteAdjacencyKeyed", s, p, o, _key(iris_obj, graph), ekey
        )
    )


def _exec(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
    finally:
        cur.close()
    with contextlib.suppress(Exception):
        conn.commit()


def _nodes(conn, graph, *ids):
    for nid in ids:
        _exec(conn, "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", (nid, graph))


def _row(conn, graph, ekey, weight, s=SRC, p=PRED, o=DST):
    _exec(
        conn,
        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey, qualifiers) VALUES (?, ?, ?, ?, ?, ?)",
        (s, p, o, graph, ekey, json.dumps({"weight": weight})),
    )


def _unrow(conn, graph, ekey, s=SRC, p=PRED, o=DST):
    _exec(
        conn,
        "DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ? AND ekey = ?",
        (s, p, o, graph, ekey),
    )


def _nkg_has(iris_obj, s=SRC, p=PRED, o=DST):
    s_idx = iris_obj.classMethodValue("Graph.KG.GraphIndex", "GetNodeIdx", s)
    o_idx = iris_obj.classMethodValue("Graph.KG.GraphIndex", "GetNodeIdx", o)
    p_idx = iris_obj.classMethodValue("Graph.KG.GraphIndex", "GetLabelIdx", p)
    if "" in (str(s_idx), str(o_idx), str(p_idx)):
        return False
    return iris_obj.isDefined("^NKG", -1, int(s_idx), -(int(p_idx) + 1), int(o_idx)) in (1, 11)


def _catalog(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_edges'"
        )
        constraints = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_edges' AND COLUMN_NAME = 'ekey'"
        )
        row = cur.fetchone()
        return constraints, (row[0] if row else None)
    finally:
        cur.close()


def _to_234(conn):
    from iris_vector_graph.schema import GraphSchema

    cur = conn.cursor()
    try:
        return GraphSchema.ensure_ekey(cur)
    finally:
        cur.close()
        with contextlib.suppress(Exception):
            conn.commit()


@pytest.fixture
def mg(iris_connection, iris_master_cleanup):
    _require_iris(iris_connection)
    from iris_vector_graph import IRISGraphEngine

    result = _to_234(iris_connection)
    assert result["status"] in ("migrated", "already at 234"), result
    iris_obj = _iris(iris_connection)
    iris_obj.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
    engine = IRISGraphEngine(iris_connection)
    yield engine, iris_obj, iris_connection
    with contextlib.suppress(Exception):
        iris_obj.kill("^IVG.GraphMode")


# --- T006: the mode ------------------------------------------------------------------


def test_mode_is_off_by_default_and_settable(mg):
    engine, iris_obj, _ = mg
    assert engine.is_multigraph(G_MODE) is False
    assert engine.is_multigraph(None) is False
    engine.set_multigraph(G_MODE, True)
    assert engine.is_multigraph(G_MODE) is True
    assert iris_obj.getString("^IVG.GraphMode", G_MODE) == "multi"
    assert engine.is_multigraph(None) is False, "the mode is per graph"
    engine.set_multigraph(G_MODE, False)
    assert engine.is_multigraph(G_MODE) is False
    assert iris_obj.isDefined("^IVG.GraphMode", G_MODE) == 0


def test_mode_cannot_be_turned_off_over_parallel_edges(mg):
    from iris_vector_graph.errors import ParallelEdgesPresentError

    engine, iris_obj, conn = mg
    engine.set_multigraph(G_MODE, True)
    _nodes(conn, G_MODE, SRC, DST, "ivg234:o2")
    _row(conn, G_MODE, 0, 1.0)
    _row(conn, G_MODE, 1, 2.0)
    _row(conn, G_MODE, 0, 1.0, o="ivg234:o2")
    _row(conn, G_MODE, 1, 1.0, o="ivg234:o2")
    _row(conn, G_MODE, 2, 1.0, o="ivg234:o2")

    with pytest.raises(ParallelEdgesPresentError) as exc:
        engine.set_multigraph(G_MODE, False)
    assert exc.value.count == 2, "two triples carry parallel edges"
    assert "parallel_edges_present" in str(exc.value)
    assert engine.is_multigraph(G_MODE) is True
    assert iris_obj.getString("^IVG.GraphMode", G_MODE) == "multi"


def test_erase_graph_clears_the_mode(mg):
    from iris_vector_graph import IRISGraphEngine

    engine, iris_obj, conn = mg
    engine.set_multigraph(G_MODE, True)
    engine.set_multigraph(G_SINGLE, True)
    engine.erase_graph(G_MODE)
    assert engine.is_multigraph(G_MODE) is False
    assert IRISGraphEngine(conn).is_multigraph(G_MODE) is False
    assert engine.is_multigraph(G_SINGLE) is True, "an erase clears its own graph only"


def test_erase_all_clears_every_mode(mg):
    engine, iris_obj, _ = mg
    engine.set_multigraph(G_MODE, True)
    engine.set_multigraph(None, True)
    engine.erase_all()
    assert iris_obj.isDefined("^IVG.GraphMode") == 0
    assert engine.is_multigraph(G_MODE) is False
    assert engine.is_multigraph(None) is False


# --- T007: keyed transitions ----------------------------------------------------------


def _assert_state(iris_obj, graph, *, leaf, children, count):
    k = _key(iris_obj, graph)
    out = _walk(iris_obj, "^KG", ("out", k))
    inn = _walk(iris_obj, "^KG", ("in", k))
    if leaf is None:
        assert out == {} and inn == {}, (out, inn)
        assert _walk(iris_obj, "^KG", ("deg", k)) == {}
        assert _walk(iris_obj, "^KG", ("degp", k)) == {}
        return
    assert float(out[(SRC, PRED, DST)]) == leaf
    assert float(inn[(DST, PRED, SRC)]) == leaf
    # Native hands subscripts back as strings; ekeys are integers.
    got_children = {int(sub[3]): float(v) for sub, v in out.items() if len(sub) == 4 and sub[3] != "#"}
    got_in_children = {int(sub[3]): float(v) for sub, v in inn.items() if len(sub) == 4}
    assert got_children == children, out
    assert got_in_children == children, inn
    if count > 1:
        assert int(out[(SRC, PRED, DST, "#")]) == count
        assert iris_obj.isDefined("^KG", "out", k, SRC, PRED, DST) == 11
    else:
        assert (SRC, PRED, DST, "#") not in out
        assert iris_obj.isDefined("^KG", "out", k, SRC, PRED, DST) == 1
    assert int(iris_obj.getString("^KG", "deg", k, SRC)) == count
    assert int(iris_obj.getString("^KG", "degp", k, SRC, PRED)) == count


def test_one_to_three_and_back(mg):
    engine, iris_obj, conn = mg
    engine.set_multigraph(G_STORE, True)
    _nodes(conn, G_STORE, SRC, DST)

    _row(conn, G_STORE, 0, 1.5)
    _write(iris_obj, G_STORE, 0, 1.5)
    _assert_state(iris_obj, G_STORE, leaf=1.5, children={}, count=1)
    assert _nkg_has(iris_obj)

    _row(conn, G_STORE, 1, 2.5)
    _write(iris_obj, G_STORE, 1, 2.5)
    _assert_state(iris_obj, G_STORE, leaf=1.5, children={0: 1.5, 1: 2.5}, count=2)
    assert _nkg_has(iris_obj)

    _row(conn, G_STORE, 2, 3.5)
    _write(iris_obj, G_STORE, 2, 3.5)
    _assert_state(iris_obj, G_STORE, leaf=1.5, children={0: 1.5, 1: 2.5, 2: 3.5}, count=3)

    # A repeated write of a live ekey refreshes its weight and counts nothing.
    _write(iris_obj, G_STORE, 2, 3.5)
    _assert_state(iris_obj, G_STORE, leaf=1.5, children={0: 1.5, 1: 2.5, 2: 3.5}, count=3)

    # Deleting the lowest ekey: the leaf takes the new lowest's weight.
    _unrow(conn, G_STORE, 0)
    _delete(iris_obj, G_STORE, 0)
    _assert_state(iris_obj, G_STORE, leaf=2.5, children={1: 2.5, 2: 3.5}, count=2)
    assert _nkg_has(iris_obj)

    # A repeated delete of a gone ekey is a no-op.
    _delete(iris_obj, G_STORE, 0)
    _assert_state(iris_obj, G_STORE, leaf=2.5, children={1: 2.5, 2: 3.5}, count=2)

    # Two to one: children and counter go, the leaf holds the survivor's weight.
    _unrow(conn, G_STORE, 2)
    _delete(iris_obj, G_STORE, 2)
    _assert_state(iris_obj, G_STORE, leaf=2.5, children={}, count=1)
    assert _nkg_has(iris_obj)

    # Byte-identical to a triple that never had a second edge.
    _nodes(conn, G_SINGLE, SRC, DST)
    iris_obj.classMethodVoid(
        "Graph.KG.EdgeScan", "WriteAdjacency", SRC, PRED, DST, "2.5", _key(iris_obj, G_SINGLE)
    )
    assert _dump(iris_obj, G_STORE) == _dump(iris_obj, G_SINGLE)

    # The last edge: everything goes, ^NKG with it.
    iris_obj.classMethodVoid("Graph.KG.EdgeScan", "DeleteAdjacency", SRC, PRED, DST, _key(iris_obj, G_SINGLE))
    _unrow(conn, G_STORE, 1)
    _delete(iris_obj, G_STORE, 1)
    _assert_state(iris_obj, G_STORE, leaf=None, children={}, count=0)
    assert not _nkg_has(iris_obj)


def test_one_to_two_when_the_first_edge_is_not_ekey_zero(mg):
    """The 1→2 transition learns the existing edge's ekey from its row."""
    engine, iris_obj, conn = mg
    engine.set_multigraph(G_STORE, True)
    _nodes(conn, G_STORE, SRC, DST)
    _row(conn, G_STORE, 4, 4.0)
    _write(iris_obj, G_STORE, 4, 4.0)
    _row(conn, G_STORE, 7, 7.0)
    _write(iris_obj, G_STORE, 7, 7.0)
    _assert_state(iris_obj, G_STORE, leaf=4.0, children={4: 4.0, 7: 7.0}, count=2)
    # A new lowest ekey takes over the leaf.
    _row(conn, G_STORE, 2, 2.0)
    _write(iris_obj, G_STORE, 2, 2.0)
    _assert_state(iris_obj, G_STORE, leaf=2.0, children={2: 2.0, 4: 4.0, 7: 7.0}, count=3)


# --- T008: BuildKG equals the incremental result -------------------------------------


def test_build_kg_equals_200_random_keyed_ops(mg):
    engine, iris_obj, conn = mg
    engine.set_multigraph(G_RANDOM, True)
    rng = random.Random(234)
    ids = [f"ivg234:r{i}" for i in range(6)]
    _nodes(conn, G_RANDOM, *ids)
    triples = [(ids[i % 6], f"P{i % 2}", ids[(i * 5 + 1) % 6]) for i in range(20)]
    triples = list(dict.fromkeys(t for t in triples if t[0] != t[2]))
    while len(triples) < 20:
        s, o = rng.sample(ids, 2)
        t = (s, f"P{rng.randrange(3)}", o)
        if t not in triples:
            triples.append(t)
    live = {t: {} for t in triples}  # triple -> {ekey: weight}
    next_key = {t: 0 for t in triples}

    for _ in range(200):
        t = rng.choice(triples)
        s, p, o = t
        if live[t] and rng.random() < 0.4:
            ek = rng.choice(sorted(live[t]))
            _unrow(conn, G_RANDOM, ek, s=s, p=p, o=o)
            _delete(iris_obj, G_RANDOM, ek, s=s, p=p, o=o)
            del live[t][ek]
        else:
            ek = next_key[t]
            next_key[t] += 1
            w = rng.choice((0.5, 1.0, 1.5, 2.0, 3.25))
            _row(conn, G_RANDOM, ek, w, s=s, p=p, o=o)
            _write(iris_obj, G_RANDOM, ek, w, s=s, p=p, o=o)
            live[t][ek] = w

    assert any(len(v) >= 2 for v in live.values()), "the walk never made a parallel edge"
    incremental = _dump(iris_obj, G_RANDOM)
    k = _key(iris_obj, G_RANDOM)
    total = sum(len(v) for v in live.values())
    assert sum(int(v) for v in incremental["deg"].values()) == total

    _ok(iris_obj.classMethodValue("Graph.KG.Traversal", "BuildKG"))
    rebuilt = _dump(iris_obj, G_RANDOM)
    for store in ADJ_STORES:
        assert rebuilt[store] == incremental[store], (
            store,
            sorted(set(rebuilt[store].items()) ^ set(incremental[store].items()))[:10],
        )
    assert k is not None


def test_build_kg_without_any_multigraph_is_the_pre_234_layout(mg):
    """With no graph in multigraph mode BuildKG writes leaves only, as before."""
    engine, iris_obj, conn = mg
    _nodes(conn, G_SINGLE, SRC, DST)
    _row(conn, G_SINGLE, 0, 1.25)
    _ok(iris_obj.classMethodValue("Graph.KG.Traversal", "BuildKG"))
    dump = _dump(iris_obj, G_SINGLE)
    assert dump["out"] == {(SRC, PRED, DST): "1.25"}
    assert dump["in"] == {(DST, PRED, SRC): "1.25"}
    assert dump["deg"] == {(SRC,): "1"}
    assert dump["degp"] == {(SRC, PRED): "1"}


# --- T009: upgrade from pre-234 -------------------------------------------------------


def _downgrade(conn):
    """Put rdf_edges back the way a pre-234 install has it."""
    for sql in (
        "ALTER TABLE Graph_KG.rdf_edges ADD CONSTRAINT u_spo_graph UNIQUE (s, p, o_id, graph_id)",
        "ALTER TABLE Graph_KG.rdf_edges DROP CONSTRAINT u_spo_graph_ekey",
        "ALTER TABLE Graph_KG.rdf_edges DROP COLUMN ekey",
    ):
        with contextlib.suppress(Exception):
            _exec(conn, sql)
    constraints, column = _catalog(conn)
    assert column is None, "could not drop ekey to build the pre-234 fixture"
    assert "u_spo_graph" in constraints and "u_spo_graph_ekey" not in constraints


def test_upgrade_from_pre_234(iris_connection, ledger_reset):
    _require_iris(iris_connection)
    from tests.integration._ledger_helpers import make_engine, seed_graph

    conn = iris_connection
    _to_234(conn)
    try:
        _downgrade(conn)
        engine = make_engine(conn)
        iris_obj = _iris(conn)
        seed_graph(engine, 5, 4, prefix="ivg234:u")
        engine.create_node("ivg234:a", graph=G_STORE)
        engine.create_node("ivg234:b", graph=G_STORE)
        engine.create_edge("ivg234:a", PRED, "ivg234:b", graph=G_STORE)
        engine.ledger.enable()
        engine.rebuild_kg()

        before_kg = _dump_all(iris_obj)
        before_head = engine.ledger.head().revision_id
        # The fixture writes before ledger.enable(), so verify reports those writes
        # as unrecorded (baseline.txt, pre-change). The migration must not change it.
        v = engine.ledger.verify()
        before_verify = (v.result, len(v.differences), v.classification)
        assert before_kg["out"], "the fixture wrote no adjacency to compare"

        result = _to_234(conn)
        assert result["status"] == "migrated", result

        constraints, column = _catalog(conn)
        assert column == "NO"
        assert "u_spo_graph_ekey" in constraints and "u_spo_graph" not in constraints
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            n = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE ekey = 0")
            zero = int(cur.fetchone()[0])
        finally:
            cur.close()
        assert n == 5 and zero == n, (n, zero)

        assert _dump_all(iris_obj) == before_kg
        assert engine.ledger.head().revision_id == before_head
        v = engine.ledger.verify()
        assert (v.result, len(v.differences), v.classification) == before_verify

        assert _to_234(conn)["status"] == "already at 234"

        # And the widened key still refuses a duplicate ekey-0 edge, as u_spo_graph did.
        with pytest.raises(Exception):
            _exec(
                conn,
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)",
                ("ivg234:a", PRED, "ivg234:b", G_STORE),
            )
    finally:
        _to_234(conn)
