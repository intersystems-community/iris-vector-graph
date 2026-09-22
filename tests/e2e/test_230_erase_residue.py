"""Spec 230 US3 gate — an erase leaves nothing behind (SC-006, FR-010, FR-012).

Three `^KG` stores were keyed by node, label and predicate with no graph:

    ^KG("prop",  nodeId, key)      = value
    ^KG("label", label,  nodeId)   = ""
    ^KG("deg2p", nodeId, pred)     = count

Before spec 227 a node ID existed in exactly one graph, so keying by node was
keying by graph transitively. Breaking `UNIQUE (node_id)` ended that: the same ID
now lives in two graphs, and `Graph.KG.Eraser` — which could only reach these
stores through the IDs it was deleting — took both graphs' entries. Its own
comment said so.

Proven against IRIS rather than a fake because the guarantee is the storage
layout: what survives an erase is whichever subscripts the `Kill` did not name,
and no Python-level double can be wrong about that in the same way the server is.

`SKIP_IRIS_TESTS=true` fails rather than skips, for the reason
`test_230_retrieval_scope.py` gives: a skipped residue test is indistinguishable
from a passing one.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytestmark = [pytest.mark.e2e]

GRAPH_A = "ivg230:erase:a"
GRAPH_B = "ivg230:erase:b"

GRAPHS = (GRAPH_A, GRAPH_B)

#: In both graphs, with properties and labels in both. The whole fixture: one
#: entity, two graphs. A per-graph erase that keys on the node ID alone cannot
#: tell these two apart.
SHARED = "ivg230:erase:shared"
#: The middle of the two-hop path, in both graphs, so `^KG("deg2p", ...)` has an
#: entry to lose.
MID = "ivg230:erase:mid"
#: The far end, in both graphs.
FAR = "ivg230:erase:far"
#: Graph B only. Every "did the erase overreach" assertion is about a store B
#: holds and A does not.
ONLY_B = "ivg230:erase:only-b"

#: The default graph's own copy of the two-hop path. `Graph.KG.TraversalKHop` is
#: default-graph-only by construction — every method opens `Set pGraph = 0` — so it
#: is the reader that proves the new subscript is addressed rather than assumed.
DEFAULT_SRC = "ivg230:erase:def:src"
DEFAULT_MID = "ivg230:erase:def:mid"
DEFAULT_FAR = "ivg230:erase:def:far"

PRED = "IVG230_ERASE_LINKS"

LABEL_SHARED = "Ivg230EraseShared"
LABEL_ONLY_B = "Ivg230EraseOnlyB"

#: The stores this story re-keys, in the order the Eraser kills them.
REKEYED = ("prop", "label", "deg2p", "deg2p_exact")

#: What may still hold data after `EraseAll`. `^KG("__version")` is the stamp
#: `^ArnoKG`'s cache is compared against — a version, not node data, and the
#: reason `EraseAll` names subtrees instead of killing `^KG` whole.
SURVIVES_ERASE_ALL = frozenset({"__version"})


# --- reading the globals ---------------------------------------------------------


def _iris(conn):
    import iris as _iris_mod

    return _iris_mod.createIRIS(conn)


def _graph_key(iris_obj, graph: str):
    """The `^KG` subscript for `graph`, from the one class that owns the derivation.

    Deriving it here would make the test agree with itself rather than with the
    server (ADR-0003).
    """
    return iris_obj.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph)


def _subtree(iris_obj, *path, depth: int = 6) -> dict:
    """Every valued node under `^KG(*path)`, as `{subscripts: value}`.

    Values are read as strings and valueless interior nodes are skipped, so the
    result is comparable with `==`: the question a residue test asks is whether
    one graph's entries are the same bytes before and after another graph's erase.
    """
    found: dict = {}

    def walk(prefix: tuple, remaining: int) -> None:
        if remaining <= 0:
            return
        current = iris_obj.nextSubscript(False, "^KG", *prefix, "")
        while current is not None and current != "":
            subs = prefix + (current,)
            with contextlib.suppress(Exception):
                value = iris_obj.getString("^KG", *subs)
                if value is not None:
                    found[subs[len(path) :]] = value
            walk(subs, remaining - 1)
            current = iris_obj.nextSubscript(False, "^KG", *prefix, current)

    walk(tuple(path), depth)
    return found


def _first_subscripts(iris_obj) -> list:
    """Every first-level subscript of `^KG`."""
    names = []
    current = iris_obj.nextSubscript(False, "^KG", "")
    while current is not None and current != "":
        names.append(current)
        current = iris_obj.nextSubscript(False, "^KG", current)
    return names


# --- fixture ---------------------------------------------------------------------


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 230 US3 asserts what a Kill leaves behind in ^KG. "
            "SKIP_IRIS_TESTS=true is not an acceptable outcome — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: residue in a global cannot be observed from "
            "Python."
        )


def _wipe(conn):
    cursor = conn.cursor()
    try:
        for graph in GRAPHS:
            for table in ("rdf_edges", "rdf_props", "rdf_labels", "nodes"):
                with contextlib.suppress(Exception):
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} "
                        "WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
        # The default graph's rows are named one by one: '' is shared with every
        # other fixture in the namespace, so a predicate on the graph alone would
        # delete rows this test never wrote.
        defaults = (DEFAULT_SRC, DEFAULT_MID, DEFAULT_FAR)
        for node_id in defaults:
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?",
                    (node_id, node_id),
                )
            for table in ("rdf_props", "rdf_labels"):
                with contextlib.suppress(Exception):
                    cursor.execute(f"DELETE FROM Graph_KG.{table} WHERE s = ?", (node_id,))
            with contextlib.suppress(Exception):
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", (node_id,))
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _populate(engine, iris_obj):
    """Two graphs with properties, labels and a two-hop path, then the derived
    stores built from them.

    Written through the engine rather than by direct INSERT so the rows carry the
    graph the way an application's rows do, and the derived stores are built by the
    server's own rebuild rather than by the test — a test that wrote `^KG` itself
    would prove the layout it assumed rather than the layout the code produces.
    """
    for graph in GRAPHS:
        engine.create_node(
            SHARED,
            labels=[LABEL_SHARED],
            properties={"name": f"shared in {graph}", "graph_note": graph},
            graph=graph,
        )
        engine.create_node(MID, labels=[LABEL_SHARED], properties={"name": "mid"}, graph=graph)
        engine.create_node(FAR, labels=[LABEL_SHARED], properties={"name": "far"}, graph=graph)
        engine.create_edge(SHARED, PRED, MID, graph=graph)
        engine.create_edge(MID, PRED, FAR, graph=graph)

    engine.create_node(
        ONLY_B,
        labels=[LABEL_ONLY_B],
        properties={"name": "only in b"},
        graph=GRAPH_B,
    )
    engine.create_edge(MID, PRED, ONLY_B, graph=GRAPH_B)

    for node_id in (DEFAULT_SRC, DEFAULT_MID, DEFAULT_FAR):
        engine.create_node(node_id, labels=[LABEL_SHARED], properties={"name": node_id})
    engine.create_edge(DEFAULT_SRC, PRED, DEFAULT_MID)
    engine.create_edge(DEFAULT_MID, PRED, DEFAULT_FAR)

    engine.rebuild_kg()
    iris_obj.classMethodValue("Graph.KG.Traversal", "Build2HopStats")


@pytest.fixture
def erase_env(iris_connection):
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    iris_obj = _iris(iris_connection)

    _wipe(iris_connection)
    _populate(engine, iris_obj)

    yield engine, iris_obj

    _wipe(iris_connection)
    with contextlib.suppress(Exception):
        engine.rebuild_kg()


# --- T042 / SC-006: one graph's erase spares the other ---------------------------


def test_erase_one_graph_spares_the_other(erase_env):
    engine, iris_obj = erase_env

    key_a = _graph_key(iris_obj, GRAPH_A)
    key_b = _graph_key(iris_obj, GRAPH_B)

    before = {store: _subtree(iris_obj, store, key_b) for store in ("prop", "label", "deg2p")}
    for store, entries in before.items():
        assert entries, (
            f'graph B holds no ^KG("{store}") entries before the erase, so this test '
            "cannot tell a spared store from an empty one. The rebuild did not write "
            "the graph-scoped layout."
        )

    had_a = {store: _subtree(iris_obj, store, key_a) for store in ("prop", "label", "deg2p")}
    for store, entries in had_a.items():
        assert entries, f'graph A holds no ^KG("{store}") entries to erase'

    engine.erase_graph(GRAPH_A)

    after = {store: _subtree(iris_obj, store, key_b) for store in ("prop", "label", "deg2p")}
    for store in ("prop", "label", "deg2p"):
        assert after[store] == before[store], (
            f'erasing graph A changed graph B\'s ^KG("{store}") entries.\n'
            f"  lost:  {sorted(set(before[store]) - set(after[store]))}\n"
            f"  gained:{sorted(set(after[store]) - set(before[store]))}"
        )

    for store in ("prop", "label", "deg2p"):
        residue = _subtree(iris_obj, store, key_a)
        assert not residue, (
            f'graph A\'s ^KG("{store}") entries survived its erase: '
            f"{sorted(residue)[:10]}"
        )


def test_the_default_graphs_two_hop_reader_still_finds_its_entry(erase_env):
    """An entry can be byte-identical and unreachable: a reader left on the flat
    layout does not error, it answers nothing, and `KHop2CountFast` reports that as
    a fall back to the walk. `Graph.KG.TraversalKHop` is default-graph-only, so the
    default graph's own path is what exercises it.
    """
    engine, iris_obj = erase_env

    before = int(
        iris_obj.classMethodValue("Graph.KG.Traversal", "KHop2CountFast", DEFAULT_SRC, PRED)
    )
    assert before >= 1, (
        "the default graph's two-hop count is 0 before any erase, so either the "
        "rebuild wrote no deg2p entry or the reader is not addressing the graph "
        "subscript"
    )

    engine.erase_graph(GRAPH_A)

    after = int(
        iris_obj.classMethodValue("Graph.KG.Traversal", "KHop2CountFast", DEFAULT_SRC, PRED)
    )
    assert after == before, (
        f"erasing graph A changed the default graph's two-hop count from {before} "
        f"to {after}"
    )


# --- T043 / SC-006: EraseAll means what it says ----------------------------------


def test_erase_all_leaves_no_node_data(erase_env):
    """`EraseAll` reported 32 rows removed and left `^KG("deg2p")` holding the
    previous ring, because its `Kill` list named neither `deg2p` nor
    `deg2p_exact`. The next traversal then answered two-hop counts for a database
    with no edges."""
    engine, iris_obj = erase_env

    removed = engine.erase_all()
    assert removed >= 0

    again = engine.erase_all()
    assert again == 0, (
        f"EraseAll reported {again} rows on an already-empty database, so the first "
        "call did not remove what it counted"
    )

    for store in REKEYED:
        residue = _subtree(iris_obj, store)
        assert not residue, (
            f'EraseAll left ^KG("{store}") holding {len(residue)} entries: '
            f"{sorted(residue)[:10]}"
        )

    for name in _first_subscripts(iris_obj):
        if name in SURVIVES_ERASE_ALL:
            continue
        residue = _subtree(iris_obj, name)
        assert not residue, (
            f'EraseAll left ^KG("{name}") holding {len(residue)} entries: '
            f"{sorted(residue)[:10]}. Either the store belongs in the Kill list or "
            "it belongs in this test's allowlist with a reason."
        )
