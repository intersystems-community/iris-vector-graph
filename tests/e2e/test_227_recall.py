"""Spec 227 FR-021 / SC-007 — per-route recall, measured against an exact scan.

The design's reason for one physical table per `(graph, model)` is that a scoped search
then needs no predicate over the ANN index, so recall is not degraded by post-filtering
(plan.md, "One physical table per (graph, model)"). That is a claim about IRIS's
behaviour on real rows, and FR-021 will not take it on trust: this measures it per route
and records the number and the time it was taken on the route's registry row.

Why it cannot be a unit test: the thing under measurement is what the HNSW index
returns versus what an exhaustive scan returns, on the same rows, in the same build. A
mock returns whatever it was told to.

The exact side is a full scoring scan with no `TOP` and no `ORDER BY` (see
`measure_route_recall`): the `TOP k ORDER BY VECTOR_COSINE` shape is the one IRIS
answers from the index, so using it for ground truth would compare the index to itself.

Covers FR-021 and SC-007, and emits the per-route table `docs/OPERATIONS.md` publishes
(T076) so the published numbers come from a run rather than from an estimate.
"""

from __future__ import annotations

import contextlib
import os
import random

import pytest

pytestmark = [pytest.mark.e2e]

#: Two routes, two widths. Width matters here: recall is a property of the index over a
#: particular column, so a single width would measure one declaration twice.
ROUTES = (
    ("ivg227recall-a", "ivg227recall-m1", 8),
    ("ivg227recall-b", "ivg227recall-m2", 16),
)

#: Enough rows that a top-10 is a ranking rather than the whole table. Small enough that
#: the exact scan — which is O(rows) per probe, by design — stays a few seconds.
NODES_PER_ROUTE = 60
K = 10
PROBES = 5

#: The floor below which the "no post-filtering, so recall is not degraded" claim is
#: false and must be rewritten rather than published. It is not 1.0: HNSW is approximate
#: on its own terms, independent of graph scoping, and a hard 1.0 would assert something
#: about the index that spec 227 does not claim.
RECALL_FLOOR = 0.9


def _node(graph: str, i: int) -> str:
    return f"ivg227:recall:{graph}:{i:03d}"


def _vectors(seed: int, width: int, count: int) -> list:
    """Deterministic pseudo-random vectors, so a failure is reproducible.

    Random rather than a fill value: identical vectors make every ranking a tie, which
    any index answers perfectly, and the measurement would say nothing.
    """
    rng = random.Random(seed)
    return [[rng.uniform(-1.0, 1.0) for _ in range(width)] for _ in range(count)]


def _wipe(engine, cursor):
    for graph, _model, _width in ROUTES:
        with contextlib.suppress(Exception):
            engine.erase_graph(graph)
        with contextlib.suppress(Exception):
            cursor.execute(
                "DELETE FROM Graph_KG.embedding_registry "
                "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
                (graph,),
            )


def _registry_recall(cursor, table_name: str, graph_id: str):
    """`(recall_measured, recall_measured_at)` as the registry holds it."""
    cursor.execute(
        "SELECT recall_measured, recall_measured_at FROM Graph_KG.embedding_registry "
        "WHERE table_name = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
        (table_name, graph_id),
    )
    row = cursor.fetchone()
    return (None, None) if row is None else (row[0], row[1])


@pytest.fixture(scope="module")
def recall(iris_connection):
    """Build two populated routes, measure each, and hand back the measurements."""
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "FR-021 is a measurement against a live ANN index. SKIP_IRIS_TESTS=true "
            "would turn a published recall number into an assumption — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: recall cannot be measured against a mock")

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    cursor = iris_connection.cursor()
    _wipe(engine, cursor)

    for index, (graph, model, width) in enumerate(ROUTES):
        vectors = _vectors(1000 + index, width, NODES_PER_ROUTE)
        for i, vector in enumerate(vectors):
            node_id = _node(graph, i)
            engine.create_node(node_id, labels=["Recalled"], graph=graph)
            engine.store_embedding(node_id, vector, graph=graph, model_key=model)

    measured = {}
    for graph, model, _width in ROUTES:
        measured[(graph, model)] = engine.measure_route_recall(
            graph=graph, model_key=model, k=K, probes=PROBES
        )

    yield {"engine": engine, "cursor": cursor, "measured": measured}

    _wipe(engine, cursor)
    with contextlib.suppress(Exception):
        cursor.close()


def test_every_route_produces_a_measurement(recall):
    for pair, one in sorted(recall["measured"].items()):
        assert one is not None, (
            f"{pair} holds {NODES_PER_ROUTE} vectors and measured nothing; an "
            f"unmeasurable route cannot have its recall published"
        )
        assert one.probes == PROBES, f"{pair} measured {one.probes} probes, not {PROBES}"
        assert one.rows_scanned >= NODES_PER_ROUTE - 1, (
            f"{pair} scanned {one.rows_scanned} rows across {one.probes} probes; the "
            f"exact side must see the whole route, not a top-k of it"
        )


def test_the_scoped_search_matches_the_exact_scan(recall):
    """SC-007's substance: the number, not the assumption."""
    for pair, one in sorted(recall["measured"].items()):
        print(
            f"[227 SC-007] {pair[0]}/{pair[1]} → recall@{one.k} = {one.recall:.4f} "
            f"over {one.probes} probes, index {one.index_state}"
        )
        assert one.recall >= RECALL_FLOOR, (
            f"recall@{one.k} on {one.table_name} is {one.recall:.4f}, below the "
            f"{RECALL_FLOOR} floor. One graph per table is supposed to mean the search "
            f"is not post-filtering an approximate index; at this number that sentence "
            f"is wrong and has to be rewritten, not published"
        )
        assert one.recall <= 1.0, f"recall above 1.0 is a counting bug: {one.recall}"


def test_the_number_and_its_timestamp_reach_the_registry_row(recall):
    """A measurement nobody can read back is not a recorded measurement (FR-021)."""
    cursor = recall["cursor"]
    for pair, one in sorted(recall["measured"].items()):
        assert one.recorded, (
            f"{pair} reports its measurement was not recorded; the number exists only "
            f"in this process and nothing else can read it"
        )
        stored, when = _registry_recall(cursor, one.table_name, one.graph_id)
        assert stored is not None, (
            f"{pair} was measured at {one.recall:.4f} and its registry row still reads "
            f"NULL, so an operator asking the database gets no number"
        )
        assert abs(float(stored) - one.recall) < 1e-6, (
            f"the registry holds {stored} for {pair} and the measurement was {one.recall}"
        )
        assert when, (
            "a recall with no timestamp keeps reading as current after the rows change"
        )


def test_the_inventory_reports_the_recall_it_recorded(recall):
    """SC-008: the operator's one read answers the recall question too."""
    engine = recall["engine"]
    wanted = {one.table_name: one for one in recall["measured"].values() if one}
    seen = {
        row.table_name: row
        for row in engine.embedding_inventory()
        if row.table_name in wanted
    }

    for table, one in sorted(wanted.items()):
        row = seen.get(table)
        assert row is not None, f"the inventory omits the measured route {table}"
        assert row.recall_measured is not None, (
            f"the inventory reports no recall for {table}, which was measured at "
            f"{one.recall:.4f}"
        )
        assert abs(float(row.recall_measured) - one.recall) < 1e-6
        assert row.recall_measured_at, f"the inventory drops {table}'s measurement time"


def test_no_published_recall_claim_lacks_a_measurement(recall):
    """The gate on T076: the table docs publish is generated from the registry.

    A row may be absent — a route nobody measured has nothing to publish. What it may
    not be is present with a recall and no measurement time, or with a measurement time
    and no recall: either half alone is a claim with nothing behind it.
    """
    engine = recall["engine"]
    rows = [r for r in engine.embedding_inventory() if r.table_name]

    half_claims = [
        r
        for r in rows
        if (r.recall_measured is None) != (not r.recall_measured_at)
    ]
    assert not half_claims, (
        "these routes publish half a recall claim — a number with no time, or a time "
        f"with no number: {[(r.table_name, r.recall_measured, r.recall_measured_at) for r in half_claims[:5]]}"
    )

    published = [r for r in rows if r.recall_measured is not None]
    lines = [
        "| graph | model | table | index | recall@k | measured |",
        "| ----- | ----- | ----- | ----- | -------- | -------- |",
    ]
    for r in sorted(published, key=lambda r: (r.graph_id or "", r.table_name)):
        lines.append(
            f"| `{r.graph_id or '(default)'}` | `{r.model_key or '(none)'}` | "
            f"`{r.table_name}` | {r.index_state} | {float(r.recall_measured):.4f} | "
            f"{r.recall_measured_at} |"
        )
    # Printed, not written: docs/OPERATIONS.md is edited by T076 from a run of this test,
    # so the numbers in it have a run behind them. `pytest -s` shows the table.
    print("\n[227 SC-007] per-route recall, as published:\n" + "\n".join(lines))

    ours = {one.table_name for one in recall["measured"].values() if one}
    assert ours <= {r.table_name for r in published}, (
        f"a route measured in this run is missing from the published table: "
        f"{sorted(ours - {r.table_name for r in published})}"
    )
