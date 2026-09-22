"""Spec 227 SC-010 — a single-graph installation does not pay for routing.

4.0.0 puts two things in front of every search that 3.2.0 did not have: a registry read
to resolve the route, and a `COALESCE(graph_id, '') = COALESCE(?, '')` predicate on the
scan. A user with one graph gets nothing from either, and SC-010 is the promise that
they are not charged for them: the scoped search stays within 10% of the 3.2.0 scan on
the same rows.

The comparison is against the 3.2.0 statement itself, rebuilt here — `SELECT TOP k`
ordered by `VECTOR_COSINE` over the unscoped table, which is what `kg_KNN_VEC` was
before this spec — rather than against a number recorded in an earlier release, because
a number from another machine on another day measures the machine.

Not marked `perf`: the default `addopts` deselect that marker, and a phase gate that is
opt-in is not a gate. It costs a few seconds.

The two paths are run interleaved, and compared on medians. Interleaved because the
container is shared with whatever else is running and drifts over a run; medians because
a single slow call from a checkpoint or a journal flush is not a latency regression.
"""

from __future__ import annotations

import contextlib
import os
import random
import statistics
import time

import pytest

pytestmark = [pytest.mark.e2e]

GRAPH = ""  # the default graph: this is the single-graph installation
MODEL = None  # no model declared, so the route is 3.2.0's own table (FR-015)
NODES = 300
K = 10
WARMUP = 3
ROUNDS = 15

#: SC-010's budget. Applied to the median ratio, with no absolute slack: a slack term
#: large enough to cover a per-call registry round trip would cover the regression this
#: test exists to catch.
BUDGET = 1.10


def _node(i: int) -> str:
    return f"ivg227:latency:node:{i:04d}"


def _vector(rng, width: int) -> list:
    return [rng.uniform(-1.0, 1.0) for _ in range(width)]


def _query(vector) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def _declared_width(conn, table: str):
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        return GraphSchema.get_embedding_dimension(cursor, table)
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


@pytest.fixture(scope="module")
def latency(iris_connection):
    """Rows in the default graph, and the two ways of searching them."""
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "SC-010 compares two statements' latency on the same rows in the same "
            "build; SKIP_IRIS_TESTS=true cannot produce that. Start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: latency cannot be measured against a mock")

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    route_table = engine._route_for_read(GRAPH, MODEL)[0]
    assert route_table, (
        "the default pair must route to 3.2.0's own table (FR-015); with no route there "
        "is nothing to compare against"
    )
    qualified = engine._t(route_table)

    rng = random.Random(227)
    width = _declared_width(iris_connection, qualified)
    if not width:
        # Nothing has declared the column yet; the first write does, at this width.
        width = 64
    node_ids = [_node(i) for i in range(NODES)]
    for node_id in node_ids:
        engine.create_node(node_id, graph=GRAPH)
        engine.store_embedding(
            node_id, _vector(rng, width), graph=GRAPH, model_key=MODEL
        )

    probe = _query(_vector(rng, width))
    cursor = iris_connection.cursor()

    def scan_320():
        """The statement 3.2.0 ran: no graph predicate, no route lookup."""
        cursor.execute(
            f"SELECT TOP {K} node_id, VECTOR_COSINE(emb, TO_VECTOR(?, DOUBLE)) AS score"
            f" FROM {qualified} ORDER BY score DESC",
            [probe],
        )
        return cursor.fetchall()

    def search_400():
        return engine.kg_KNN_VEC(probe, k=K, graph=None, model_key=MODEL)

    for _ in range(WARMUP):
        scan_320()
        search_400()

    legacy, scoped = [], []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        scan_320()
        legacy.append(time.perf_counter() - started)
        started = time.perf_counter()
        search_400()
        scoped.append(time.perf_counter() - started)

    report = {
        "engine": engine,
        "table": route_table,
        "width": width,
        "legacy": legacy,
        "scoped": scoped,
        "legacy_median": statistics.median(legacy),
        "scoped_median": statistics.median(scoped),
    }
    print(
        f"\n[227 SC-010] {NODES} vectors of width {width} in {route_table}: "
        f"3.2.0 scan median {report['legacy_median'] * 1000:.2f}ms, "
        f"4.0.0 scoped median {report['scoped_median'] * 1000:.2f}ms, "
        f"ratio {report['scoped_median'] / report['legacy_median']:.3f}"
    )

    yield report

    for node_id in node_ids:
        with contextlib.suppress(Exception):
            engine.delete_node(node_id, graph=GRAPH)
    with contextlib.suppress(Exception):
        cursor.execute(
            f"DELETE FROM {qualified} WHERE node_id LIKE 'ivg227:latency:%'"
        )
        iris_connection.commit()
    with contextlib.suppress(Exception):
        cursor.close()


def test_the_scoped_search_stays_within_the_budget(latency):
    ratio = latency["scoped_median"] / latency["legacy_median"]
    assert ratio <= BUDGET, (
        f"the scoped search costs {ratio:.3f}× the 3.2.0 scan on the same "
        f"{NODES} rows ({latency['scoped_median'] * 1000:.2f}ms vs "
        f"{latency['legacy_median'] * 1000:.2f}ms), over SC-010's {BUDGET}× budget. A "
        f"single-graph installation is paying for a route it does not use — the "
        f"per-call registry read is the first thing to look at"
    )


def test_both_paths_answer_the_same_question(latency):
    """A faster path that answers differently is not the same measurement."""
    engine = latency["engine"]
    rows = engine.kg_KNN_VEC(
        _query([0.1] * latency["width"]), k=K, graph=None, model_key=MODEL
    )
    assert len(rows) == K, (
        f"the scoped search returned {len(rows)} of {K} neighbours over {NODES} rows, "
        f"so the latency above was measured on a query that answers nothing"
    )
