"""Spec 227 SC-012 — 50 graphs across 2 models, 100 routed tables.

The layout's cost is per `(graph, model)` pair: one physical table and one index
attempt each. Spec 227 documents 50 graphs × 2 models as the *tested* ceiling, and a
tested ceiling nobody tests is a claim. Phase 0 measured the bare tables at
`created 100/100 in 5.9s, dropped in 1.2s` (research R7) — without registry rows, and
without the ANN index attempt that a real route makes — so this run's numbers are
expected to be larger. What they must not be is super-linear: the failure this catches
is a per-route cost that grows with the number of routes already present, which is
invisible at the two routes every other 227 test creates.

Everything is measured inside one module-scoped fixture, in order: create, then observe
the live layout, then erase. Tests read the recording. That is not indirection for its
own sake — the drop cost is part of what SC-012 asks for, and a drop can only be timed
after the observations that need the routes alive, which means it cannot happen in a
test that also asserts on them.

Covers FR-042 and SC-012.
"""

from __future__ import annotations

import contextlib
import os
import time

import pytest

from iris_vector_graph.routing import route_table_name

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: 50 × 2 = 100 routes, the ceiling spec 227 publishes as tested.
GRAPHS = 50
MODELS = ("ivg227scale-m1", "ivg227scale-m2")

#: Two widths, because a route exists to hold a width. 8 and 16 keep the vectors
#: small: this measures the cost of the *layout*, and a 768-wide vector would spend
#: the run's time in the driver instead.
WIDTHS = {MODELS[0]: 8, MODELS[1]: 16}

#: Graphs seeded with nodes and vectors. A search needs rows, and seeding all 50
#: would measure the writer rather than the layout. Five is enough for the assertion
#: that matters: every sampled graph holds the *same* node IDs, so a search that
#: crosses graphs returns more rows than one graph has.
SAMPLED = 5
PER_GRAPH_NODES = 3

#: Phase 0's bare-table baseline (research R7), for the report.
BASELINE_CREATE_S = 5.9
BASELINE_DROP_S = 1.2

#: Ceilings, deliberately an order of magnitude above the baseline. A real route adds
#: a registry insert and an index attempt per table, and the container is shared with
#: whatever else is running, so anything tighter would fail on jitter rather than on a
#: regression. What these do catch: a per-route cost that scales with the number of
#: routes already present — 100 routes at 30× the baseline is not slow, it is quadratic.
CREATE_CEILING_S = 120.0
DROP_CEILING_S = 90.0


def _graph(i: int) -> str:
    return f"ivg227scale-{i:02d}"


def _node(i: int) -> str:
    """A node ID every sampled graph holds, so a leak is a row count, not an ID."""
    return f"ivg227:scale:node:{i}"


def _vector(width: int, fill: float) -> list:
    return [fill] * width


def _query(vector) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def _existing_tables(cursor, names) -> set:
    """Which of ``names`` IRIS actually holds, read in one statement.

    One `IN` list rather than 100 round trips: at this size the round trips are a
    measurable part of the run, and the question is about the whole layout.
    """
    if not names:
        return set()
    placeholders = ", ".join("?" for _ in names)
    cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME IN ({placeholders})",
        list(names),
    )
    return {str(r[0]) for r in cursor.fetchall() or []}


@pytest.fixture(scope="module")
def scale(iris_connection):
    """Build 100 routes, record what they look like, erase them, report the cost.

    A missing container is a FAILURE, never a skip (constitution VIII gate 1): a
    skipped ceiling test reads exactly like a verified ceiling.
    """
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "SC-012 is a claim about a live layout's cost. Start ivg-iris-enterprise "
            "with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: 100 routed tables cannot be mocked")

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    graphs = [_graph(i) for i in range(GRAPHS)]
    expected = {
        (g, m): route_table_name(g, m) for g in graphs for m in MODELS
    }

    # Anything left by an interrupted earlier run would be counted as this run's.
    for graph in graphs:
        with contextlib.suppress(Exception):
            engine.erase_graph(graph)

    created = {}
    started = time.time()
    for graph in graphs:
        for model in MODELS:
            route = engine.resolve_route(
                graph, model, create=True, dimension=WIDTHS[model], dtype="DOUBLE"
            )
            created[(graph, model)] = route
    create_seconds = time.time() - started

    # Seed a sample, so a scoped search has something to return and something to
    # wrongly return. Same node IDs in every sampled graph.
    sampled = graphs[:SAMPLED]
    for position, graph in enumerate(sampled):
        for i in range(PER_GRAPH_NODES):
            node_id = _node(i)
            engine.create_node(node_id, labels=["Scaled"], graph=graph)
            for model in MODELS:
                engine.store_embedding(
                    node_id,
                    _vector(WIDTHS[model], 0.1 + position / 10 + i / 100),
                    graph=graph,
                    model_key=model,
                )

    searched = {}
    for graph in sampled:
        for model in MODELS:
            searched[(graph, model)] = engine.kg_KNN_VEC(
                _query(_vector(WIDTHS[model], 0.15)),
                k=PER_GRAPH_NODES * SAMPLED * 2,
                graph=graph,
                model_key=model,
            )

    inventory_started = time.time()
    inventory = engine.embedding_inventory()
    inventory_seconds = time.time() - inventory_started

    cursor = iris_connection.cursor()
    try:
        present = _existing_tables(cursor, sorted(expected.values()))
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    drop_started = time.time()
    for graph in graphs:
        engine.erase_graph(graph)
    drop_seconds = time.time() - drop_started

    cursor = iris_connection.cursor()
    try:
        surviving = _existing_tables(cursor, sorted(expected.values()))
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    report = {
        "expected": expected,
        "created": created,
        "present": present,
        "surviving": surviving,
        "sampled": sampled,
        "searched": searched,
        "inventory": inventory,
        "create_seconds": create_seconds,
        "drop_seconds": drop_seconds,
        "inventory_seconds": inventory_seconds,
    }
    # Printed so the numbers docs/OPERATIONS.md publishes (T076) come from a run
    # rather than from an estimate. `pytest -s` shows them.
    print(
        f"\n[227 SC-012] {len(expected)} routes: created in {create_seconds:.1f}s "
        f"({create_seconds / len(expected):.3f}s each, baseline {BASELINE_CREATE_S}s "
        f"for bare tables), inventory read in {inventory_seconds:.2f}s, erased in "
        f"{drop_seconds:.1f}s (baseline {BASELINE_DROP_S}s)"
    )

    yield report

    for graph in graphs:
        with contextlib.suppress(Exception):
            engine.erase_graph(graph)


def test_every_route_in_the_ceiling_is_created(scale):
    """100 pairs, 100 routes, 100 distinct tables."""
    expected, created = scale["expected"], scale["created"]

    missing = sorted(pair for pair, route in created.items() if route is None)
    assert not missing, f"{len(missing)} of {len(expected)} routes were not created: {missing[:5]}"

    names = {pair: route.table_name for pair, route in created.items()}
    assert names == expected, (
        "a route landed in a table other than the one its (graph, model) hashes to; "
        "the registry and `route_table_name` disagree, so a later lookup misses"
    )
    assert len(set(names.values())) == len(expected), (
        "two pairs share one physical table, which can hold one declared width: "
        f"{len(expected)} pairs, {len(set(names.values()))} tables"
    )


def test_every_created_route_exists_as_a_table(scale):
    """The registry row and the table are separate facts; SC-012 needs both."""
    expected, present = scale["expected"], scale["present"]
    absent = sorted(set(expected.values()) - present)
    assert not absent, (
        f"{len(absent)} routes have a registry row and no table, so the first write "
        f"to each fails: {absent[:5]}"
    )


def test_every_route_declares_its_own_width(scale):
    created = scale["created"]
    wrong = {
        pair: route.dimension
        for pair, route in created.items()
        if route is not None and route.dimension != WIDTHS[pair[1]]
    }
    assert not wrong, f"routes declaring the wrong model's width: {list(wrong.items())[:5]}"


def test_every_route_is_indexed_or_honestly_refused(scale):
    """FR-019: `absent` is the one answer a freshly created route may not give.

    A refusal is fine and recorded — the build may have no HNSW support. Silence is
    not: a route reported as having no index, with no attempt and no reason, is
    indistinguishable from an index that was never tried, and the operator has
    nothing to act on.
    """
    created = scale["created"]
    silent = sorted(
        pair
        for pair, route in created.items()
        if route is not None
        and not (
            route.index_state == "present"
            or (route.index_state == "refused" and route.index_error)
        )
    )
    assert not silent, (
        f"{len(silent)} routes report neither an index nor a reason for not having "
        f"one: {[(p, created[p].index_state, created[p].index_error) for p in silent[:3]]}"
    )

    states = sorted({r.index_state for r in created.values() if r is not None})
    print(f"[227 SC-012] index states across {len(created)} routes: {states}")


def test_a_scoped_search_at_the_ceiling_returns_only_its_own_graph(scale):
    """Every sampled graph holds the same node IDs, so a leak is a row count.

    With 5 graphs × 3 nodes in play and `k` set above the total, a search that
    crossed graphs would come back with up to 15 rows and look healthier than the
    correct answer of 3.
    """
    searched = scale["searched"]
    for (graph, model), rows in sorted(searched.items()):
        assert rows, f"{graph}/{model} returned nothing; its route holds {PER_GRAPH_NODES} rows"
        assert len(rows) <= PER_GRAPH_NODES, (
            f"{graph}/{model} returned {len(rows)} rows from a graph holding "
            f"{PER_GRAPH_NODES}: the search crossed graphs at scale"
        )
        ids = {r[0] for r in rows}
        assert ids <= {_node(i) for i in range(PER_GRAPH_NODES)}, (
            f"{graph}/{model} returned IDs no sampled graph holds: {ids}"
        )


def test_each_model_answers_from_its_own_route(scale):
    """Two models over the same node IDs at two widths, in the same graph.

    The IDs are identical by construction, so this compares what a leak would make
    identical too: the route each answer came out of.
    """
    created, searched = scale["created"], scale["searched"]
    for graph in scale["sampled"]:
        first, second = MODELS
        assert created[(graph, first)].table_name != created[(graph, second)].table_name
        assert searched[(graph, first)], f"{graph}/{first} answered nothing"
        assert searched[(graph, second)], f"{graph}/{second} answered nothing"


def test_one_inventory_read_reports_the_whole_layout(scale):
    """SC-008 and SC-012: the operator asks once, for all 100 routes."""
    expected, inventory = scale["expected"], scale["inventory"]
    reported = {(r.graph_id, r.model_key): r for r in inventory if r.table_name}

    for pair, table in sorted(expected.items()):
        row = reported.get(pair)
        assert row is not None, f"the inventory omits {pair}, one of {len(expected)} routes"
        assert row.table_name == table
        assert row.dimension == WIDTHS[pair[1]], (
            f"the inventory reports {row.dimension} for {pair}, which declares "
            f"{WIDTHS[pair[1]]}; a caller sizing a query vector from this gets -104"
        )


def test_the_inventory_never_claims_an_index_the_dictionary_does_not_hold(scale):
    """SC-009. The registry records what one `CREATE INDEX` replied; the class
    dictionary is what the namespace holds now. `present` with no index name is the
    synthesized row spec 226 removed, and 100 routes is where it would hide."""
    tables = set(scale["expected"].values())
    rows = [r for r in scale["inventory"] if r.table_name in tables]
    synthesized = [r for r in rows if r.index_state == "present" and not r.index_name]
    assert not synthesized, (
        f"{len(synthesized)} routes claim an index with no name behind it: "
        f"{[r.table_name for r in synthesized[:5]]}"
    )


def test_erasing_the_graphs_leaves_no_routed_table(scale):
    surviving = scale["surviving"]
    assert not surviving, (
        f"{len(surviving)} routed tables outlived the erase of their graphs: "
        f"{sorted(surviving)[:5]}. Each one answers a later route lookup with a "
        f"stale width."
    )


def test_the_measured_cost_of_the_ceiling_is_linear(scale):
    """FR-042: publish the cost, and fail when it stops being per-route.

    The ceilings are loose on purpose (see the module constants). What they catch is
    a cost that grows with the number of routes already present — the shape no
    two-route test can see.
    """
    create_seconds = scale["create_seconds"]
    drop_seconds = scale["drop_seconds"]
    count = len(scale["expected"])

    assert create_seconds < CREATE_CEILING_S, (
        f"creating {count} routes took {create_seconds:.1f}s "
        f"({create_seconds / count:.3f}s each) against a {BASELINE_CREATE_S}s "
        f"bare-table baseline; that is not a per-route cost"
    )
    assert drop_seconds < DROP_CEILING_S, (
        f"erasing {GRAPHS} graphs holding {count} routes took {drop_seconds:.1f}s "
        f"against a {BASELINE_DROP_S}s bare-table baseline"
    )
    assert scale["inventory_seconds"] < 30.0, (
        f"one inventory read over {count} routes took "
        f"{scale['inventory_seconds']:.1f}s; an operator's status command cannot "
        f"cost that, and FR-020 promises one read"
    )
