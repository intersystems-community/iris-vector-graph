"""Spec 230 — a variable-length pattern answers the direction and the cap it was given.

Both defects live on the *default* graph with Arno loaded, which is the configuration
every single-tenant install runs, and both are invisible from Python:

* **FR-031, direction.** `Graph.KG.NKGAccel.BFSJson` — the Rust accelerator — takes
  `(srcId, preds, maxHops, maxResults)` and walks `^NKG` outbound. The store chose it
  for any default-graph BFS, so `(x)<-[r*1..1]-(y)` and `(x)-[r*1..1]-(y)` came back
  with x's *successors*. Real edges, wrong direction, no error. That it needs IRIS to
  prove is the whole point: the ObjectScript adapter answers all three directions
  correctly on the same `^KG` rows, so only the live adapter choice is wrong, and
  which adapter runs depends on whether Arno is actually loaded in the container.
* **FR-032, the row cap.** With the source bound by a property (`x.id = $id`) instead
  of `node_id`, the query is answered by `_execute_var_length_labeled`, which never
  read the translated statement's `LIMIT`. A `LIMIT 5` over a twenty-neighbour hub
  returned twenty rows.

`SKIP_IRIS_TESTS=true` fails rather than skips: this is an assertion about which
server-side traversal runs, and a skip reads exactly like a pass.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVG230_DIR_LINKS"


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 230 FR-031/FR-032 assert which server-side BFS runs and what it "
            "returns. SKIP_IRIS_TESTS=true is not an acceptable outcome — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: the direction defect is a choice between two "
            "server-side traversals and cannot be observed without them."
        )


class _Fixture:
    """A hub with one outbound neighbour, one inbound neighbour, and twenty leaves."""

    def __init__(self, engine, conn):
        self.engine = engine
        self.conn = conn
        self.run = uuid.uuid4().hex[:8]
        self.ids: list = []

    def node(self, suffix: str) -> str:
        nid = f"ivg230dir_{self.run}_{suffix}"
        self.ids.append(nid)
        self.engine.create_node(nid, labels=["Ivg230Dir"])
        return nid

    def wipe(self):
        cursor = self.conn.cursor()
        try:
            for nid in self.ids:
                for sql, params in (
                    ("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", (nid, nid)),
                    ("DELETE FROM Graph_KG.rdf_props WHERE s=?", (nid,)),
                    ("DELETE FROM Graph_KG.rdf_labels WHERE s=?", (nid,)),
                    ("DELETE FROM Graph_KG.nodes WHERE node_id=?", (nid,)),
                ):
                    with contextlib.suppress(Exception):
                        cursor.execute(sql, params)
            with contextlib.suppress(Exception):
                self.conn.commit()
        finally:
            with contextlib.suppress(Exception):
                cursor.close()


@pytest.fixture
def hub(iris_connection):
    """`hub` with `out` downstream and `in` upstream — the asymmetry is the test."""
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    # No embedding_dimension and no initialize_schema: this file asserts nothing about
    # vectors, and declaring a width here would ALTER the shared kg_NodeEmbeddings.emb
    # column for every other test in the session. A narrow default route rejects every
    # wider write afterwards (SQLCODE -104), which is how one 4-dimension fixture took
    # out eighty unrelated E2Es.
    engine = IRISGraphEngine(iris_connection)
    fixture = _Fixture(engine, iris_connection)

    fixture.hub = fixture.node("hub")
    fixture.out = fixture.node("out")
    fixture.inbound = fixture.node("in")
    engine.create_edge(fixture.hub, PRED, fixture.out)
    engine.create_edge(fixture.inbound, PRED, fixture.hub)
    with contextlib.suppress(Exception):
        iris_connection.commit()

    yield fixture
    fixture.wipe()


def _ids(result) -> set:
    return {row[0] for row in result["rows"]}


# --- FR-031: the direction the pattern asked for --------------------------------


def test_an_inbound_pattern_returns_the_predecessor(hub):
    """`(x)<-[r*1..1]-(y)` asks for edges *into* x."""
    result = hub.engine.execute_cypher(
        "MATCH (x)<-[r*1..1]-(y) WHERE x.id = $id RETURN y.id", {"id": hub.hub}
    )
    ids = _ids(result)
    assert hub.inbound in ids, f"inbound neighbour missing: {ids}"
    assert hub.out not in ids, (
        f"an inbound pattern returned the outbound neighbour: {ids} — the traversal "
        "answered a direction nobody asked for"
    )


def test_an_undirected_pattern_returns_both_sides(hub):
    result = hub.engine.execute_cypher(
        "MATCH (x)-[r*1..1]-(y) WHERE x.id = $id RETURN y.id", {"id": hub.hub}
    )
    ids = _ids(result)
    assert hub.out in ids, f"outbound neighbour missing: {ids}"
    assert hub.inbound in ids, f"inbound neighbour missing: {ids}"


def test_an_outbound_pattern_is_unchanged(hub):
    """The accelerator still serves the direction it can express."""
    result = hub.engine.execute_cypher(
        "MATCH (x)-[r*1..1]->(y) WHERE x.id = $id RETURN y.id", {"id": hub.hub}
    )
    ids = _ids(result)
    assert hub.out in ids
    assert hub.inbound not in ids


def test_the_store_declines_the_accelerator_for_the_directions_it_cannot_answer(hub):
    """Named at the seam, so the reason survives a later refactor of the routes.

    Checked live because the choice only exists when Arno is loaded: on a container
    without it both branches return the ObjectScript adapter and the assertion is
    vacuous.
    """
    from iris_vector_graph.stores.iris_sql_store import (
        _ArnoBfsAdapter,
        _ObjectScriptBfsAdapter,
    )

    store = hub.engine._store
    if not isinstance(store._select_bfs_strategy(graph=None, direction="out"), _ArnoBfsAdapter):
        pytest.skip("Arno not loaded in this container — nothing to decline")
    for direction in ("in", "both"):
        assert isinstance(
            store._select_bfs_strategy(graph=None, direction=direction),
            _ObjectScriptBfsAdapter,
        ), f"direction={direction} was routed to the outbound-only accelerator"


# --- FR-032: the row cap on a property-bound source ------------------------------


def test_a_property_bound_source_obeys_the_limit(hub):
    """The route that answers `x.id = $id` never ran the statement carrying the cap."""
    for i in range(20):
        leaf = hub.node(f"leaf{i}")
        hub.engine.create_edge(hub.hub, PRED, leaf)
    with contextlib.suppress(Exception):
        hub.conn.commit()

    result = hub.engine.execute_cypher(
        "MATCH (x)-[r*1..1]->(y) WHERE x.id = $id RETURN y.id LIMIT 5",
        {"id": hub.hub},
    )
    assert (
        len(result["rows"]) <= 5
    ), f"LIMIT 5 returned {len(result['rows'])} rows from a 21-neighbour hub"


def test_without_a_limit_every_neighbour_comes_back(hub):
    """The cap must not become a default: an uncapped query still returns them all."""
    leaves = [hub.node(f"nolimit{i}") for i in range(7)]
    for leaf in leaves:
        hub.engine.create_edge(hub.hub, PRED, leaf)
    with contextlib.suppress(Exception):
        hub.conn.commit()

    result = hub.engine.execute_cypher(
        "MATCH (x)-[r*1..1]->(y) WHERE x.id = $id RETURN y.id", {"id": hub.hub}
    )
    ids = _ids(result)
    for leaf in leaves:
        assert leaf in ids, f"uncapped query dropped {leaf}: {sorted(ids)}"
