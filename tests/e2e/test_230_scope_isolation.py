"""Spec 230 US1 gate — a cross-graph read or delete cannot happen by accident.

Two guarantees, both about a *falsy* graph identifier meaning the default graph rather
than every graph:

- `retract_inference(graph="")` deletes the default graph's inferred edges and nothing
  else (SC-001). This is the release's only data-loss defect: the `else` branch carried
  no graph predicate, so both `retract_inference()` and `retract_inference(graph="")`
  emptied every graph in the namespace.
- `Graph.KG.EdgeScan.MatchEdges(..., graph=0)` returns the default graph's edges only,
  and the merged all-graphs view is reachable only through `MatchEdgesAllGraphs`
  (FR-002). `0` looked like "the default graph" to every caller and meant "iterate
  every graph" to the implementation.

Both live, because both are claims about what the server does with a predicate.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytestmark = [pytest.mark.e2e]

GRAPH_DEFAULT = ""
GRAPH_OTHER = "ivg230:scope:other"

SRC = "ivg230:scope:src"
DST = "ivg230:scope:dst"
PRED = "IVG230_INFERS"

INFERRED = {"inferred": "true"}


def _wipe(conn):
    from iris_vector_graph.schema import _call_classmethod

    # The adjacency globals first, while the rows that name the edge are still there.
    # `DeleteAdjacencyAllGraphs` is the only spelling that reaches every graph: a graph
    # key is a value, so "all of them" cannot be passed as one.
    with contextlib.suppress(Exception):
        _call_classmethod(
            conn, "Graph.KG.EdgeScan", "DeleteAdjacencyAllGraphs", SRC, PRED, DST
        )

    cursor = conn.cursor()
    try:
        for graph in (GRAPH_DEFAULT, GRAPH_OTHER):
            for table in ("rdf_edges", "rdf_props", "rdf_labels"):
                with contextlib.suppress(Exception):
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} "
                        "WHERE COALESCE(graph_id, '') = ? AND s IN (?, ?)",
                        (graph, SRC, DST),
                    )
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.nodes "
                    "WHERE COALESCE(graph_id, '') = ? AND node_id IN (?, ?)",
                    (graph, SRC, DST),
                )
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


@pytest.fixture
def two_graphs_with_inferred_edges(iris_connection):
    """One inferred edge in the default graph, one in a named graph.

    A missing container is a failure, not a skip: this file asserts what a `DELETE`
    predicate does on the server, and a skipped data-loss test reads exactly like a
    passing one.
    """
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 230 US1 asserts server-side delete scope. SKIP_IRIS_TESTS=true is "
            "not an acceptable outcome — start ivg-iris-enterprise with "
            "scripts/enterprise-container.sh up."
        )
    assert iris_connection is not None, "no live IRIS connection"

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    for graph in (GRAPH_DEFAULT, GRAPH_OTHER):
        engine.create_node(SRC, labels=["Ivg230Src"], graph=graph)
        engine.create_node(DST, labels=["Ivg230Dst"], graph=graph)
        engine.create_edge(SRC, PRED, DST, qualifiers=dict(INFERRED), graph=graph)

    yield engine
    _wipe(iris_connection)


def _inferred_count(conn, graph: str) -> int:
    """Count inferred edges in one graph, using the library's own marker.

    Deliberately imports `INFERRED_QUALIFIER_LIKE` rather than restating a pattern:
    a test that spells the predicate itself can agree with neither the writer nor the
    reader, which is how the writer and reader came to disagree in the first place.
    """
    from iris_vector_graph._engine.schema import INFERRED_QUALIFIER_LIKE

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges "
            f"WHERE qualifiers LIKE '{INFERRED_QUALIFIER_LIKE}' "
            "AND COALESCE(graph_id, '') = ?",
            (graph,),
        )
        return cursor.fetchone()[0]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def test_retract_inference_spares_other_graphs(
    two_graphs_with_inferred_edges, iris_connection
):
    """SC-001: `retract_inference(graph="")` touches the default graph only."""
    engine = two_graphs_with_inferred_edges
    before_other = _inferred_count(iris_connection, GRAPH_OTHER)
    assert before_other >= 1, "the fixture did not write the other graph's inferred edge"
    assert _inferred_count(iris_connection, GRAPH_DEFAULT) >= 1

    engine.retract_inference(graph="")

    assert _inferred_count(iris_connection, GRAPH_DEFAULT) == 0, (
        "the default graph's inferred edges survived, so the predicate is now too narrow"
    )
    assert _inferred_count(iris_connection, GRAPH_OTHER) == before_other, (
        f"'{GRAPH_OTHER}' lost inferred edges to a retraction scoped to the default "
        f"graph: {before_other} before, {_inferred_count(iris_connection, GRAPH_OTHER)} "
        "after. This is the data-loss defect spec 230 FR-001 closes."
    )


def test_retract_inference_with_no_argument_also_spares_other_graphs(
    two_graphs_with_inferred_edges, iris_connection
):
    """`graph=None` and `graph=""` are the same call and must be equally narrow."""
    engine = two_graphs_with_inferred_edges
    before_other = _inferred_count(iris_connection, GRAPH_OTHER)

    engine.retract_inference()

    assert _inferred_count(iris_connection, GRAPH_DEFAULT) == 0
    assert _inferred_count(iris_connection, GRAPH_OTHER) == before_other


def _match_edges(conn, method: str, *args):
    """Call an `EdgeScan` SqlProc and return its raw `%String` result."""
    cursor = conn.cursor()
    try:
        placeholders = ", ".join("?" for _ in args)
        cursor.execute(
            f"SELECT Graph_KG.{method}({placeholders})", list(args)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def test_match_edges_graph_zero_is_the_default_graph_only(
    two_graphs_with_inferred_edges, iris_connection
):
    """FR-002: `0` means the default graph; the merged view has its own name.

    The edges are identical in both graphs, so the only thing separating a scoped read
    from a merged one is how many the scan returns.

    No `sync()` here: `create_edge` writes `^KG` adjacency itself, under the edge's own
    graph key. A `BuildKG` would rebuild the whole namespace's tree, which is both slow
    and a much wider blast radius than this test needs.
    """
    two_graphs_with_inferred_edges  # the fixture's writes are the subject

    scoped = _match_edges(iris_connection, "MatchEdges", SRC, PRED, 0, 0)
    assert scoped is not None, "MatchEdges returned nothing at all"
    assert scoped.count(DST) == 1, (
        f"MatchEdges(..., graph=0) returned {scoped.count(DST)} entries for the target; "
        f"the default graph holds one. A falsy graph is reading every graph: {scoped!r}"
    )

    merged = _match_edges(iris_connection, "MatchEdgesAllGraphs", SRC, PRED, 0)
    assert merged is not None, (
        "MatchEdgesAllGraphs does not exist. The merged all-graphs view must be "
        "reachable by name so every cross-graph read is greppable (FR-002)."
    )
    assert merged.count(DST) == 2, (
        f"MatchEdgesAllGraphs should see both graphs' edges, saw: {merged!r}"
    )


def _node_ids(result) -> list:
    return [row[0] for row in (result.rows or [])]


def test_use_graph_with_the_default_graph_reads_one_graph(
    two_graphs_with_inferred_edges,
):
    """FR-001, on the server: `USE GRAPH ''` is a graph, not the absence of one.

    Both graphs hold a node with the ID `SRC` and the label `Ivg230Src` — the shape
    spec 227's `UNIQUE (graph_id, node_id)` made possible. So the scoped read and the
    unscoped one differ only in how many rows come back, which is exactly the
    difference the translator was not making: it tested `graph_context` for truth, and
    `''` is falsy, so the one graph whose name *is* the default graph's name got no
    predicate on any table and the query answered with both.

    Asserted live rather than on generated SQL alone because the predicate has to be
    one IRIS accepts at Prepare and applies to the right column; a statement that is
    syntactically scoped and refused at Prepare answers zero rows through the driver's
    error path, which reads like an empty graph.
    """
    engine = two_graphs_with_inferred_edges

    scoped = engine.execute_cypher(
        "USE GRAPH '' MATCH (n:Ivg230Src) RETURN n.node_id AS id"
    )
    assert len(_node_ids(scoped)) == 1, (
        f"USE GRAPH '' returned {len(_node_ids(scoped))} rows for a node ID that two "
        f"graphs hold. One graph was asked for: {scoped.rows!r}"
    )

    named = engine.execute_cypher(
        f"USE GRAPH '{GRAPH_OTHER}' MATCH (n:Ivg230Src) RETURN n.node_id AS id"
    )
    assert len(_node_ids(named)) == 1, named.rows

    unscoped = engine.execute_cypher("MATCH (n:Ivg230Src) RETURN n.node_id AS id")
    assert len(_node_ids(unscoped)) > 1, (
        "no USE GRAPH clause still means the whole namespace — the pre-214 default "
        f"every existing caller compiled against: {unscoped.rows!r}"
    )
    # Four, not two: with no predicate the `nodes`/`rdf_labels` join pairs every
    # graph's node row against every graph's label row for the same ID, so a node two
    # graphs hold comes back once per pair. The count is not asserted, because the
    # number is the fan-out rather than an answer anyone wants — what is asserted is
    # that it is not one graph's worth. Documented in docs/KNOWN_ISSUES.md; narrowing
    # the no-clause default is a deprecation, not this release.
    assert len(_node_ids(unscoped)) >= 2


def test_use_graph_with_the_default_graph_writes_one_graph(
    two_graphs_with_inferred_edges, iris_connection
):
    """The same value on a write. `SET` under `USE GRAPH ''` touches one graph's row.

    Unscoped, the UPDATE lands on whichever graph's property row the predicate reached
    first and the insert-if-absent guard then reports the property already present, so
    the graph the caller named never gets one.
    """
    engine = two_graphs_with_inferred_edges

    engine.execute_cypher(
        "USE GRAPH '' MATCH (n:Ivg230Src) SET n.ivg230mark = 'default'"
    )

    cursor = iris_connection.cursor()
    try:
        cursor.execute(
            "SELECT COALESCE(graph_id, ''), val FROM Graph_KG.rdf_props "
            'WHERE s = ? AND "key" = ?',
            (SRC, "ivg230mark"),
        )
        rows = [(r[0], r[1]) for r in (cursor.fetchall() or [])]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    assert rows == [("", "default")], (
        f"a SET scoped to the default graph wrote {rows!r}; one row in the default "
        "graph was asked for"
    )
