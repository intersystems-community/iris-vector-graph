"""Spec 227 FR-021 — `measure_route_recall` compares a route's search to an exact scan.

The registry has held `recall_measured` / `recall_measured_at` since T020 and nothing
wrote them, so every recall number the project could publish was an assumption. FR-021
refuses that: one route is one graph, which is the reason the design claims no
post-filtering and therefore exact recall — a claim that is only worth making with a
measured number behind it.

The exact side is deliberately *not* `ORDER BY ... TOP k`: that is the shape IRIS answers
from the ANN index, which would compare the index to itself. It scores every row in the
route and ranks in Python. Scores, never vectors — a route's vectors stay in IRIS.
"""

from unittest.mock import MagicMock

from iris_vector_graph._engine.embeddings import EmbeddingRoute
from iris_vector_graph.engine import IRISGraphEngine


def _route(table="kg_emb_abc123", graph="g1", model="m1"):
    return EmbeddingRoute(
        graph_id=graph,
        model_key=model,
        table_name=table,
        dimension=8,
        dtype="DOUBLE",
        index_state="present",
    )


#: `route=UNROUTED` is a pair with no route; the default builds one.
UNROUTED = object()


def _engine(exact_rows, ann_rows, route=UNROUTED, probes=("p1",)):
    """An engine whose route, seeds, exact scan and ANN search are all fixed.

    `exact_rows` is what the scoring scan returns — `(node_id, score)` pairs in table
    order, unranked, as IRIS would hand them back without an ORDER BY.
    """
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine.vector_dtype = "DOUBLE"
    engine._t = lambda name: f"Graph_KG.{name}"
    resolved = _route() if route is UNROUTED else route
    engine._route_for_read = MagicMock(
        return_value=((resolved.table_name, resolved) if resolved else (None, None))
    )
    engine.kg_KNN_VEC = MagicMock(return_value=list(ann_rows))
    engine._record_route_recall = MagicMock()

    cursor = engine.conn.cursor.return_value
    seeds = [(p,) for p in probes]
    # First statement picks the probe seeds; every later one is a scoring scan.
    cursor.fetchall.side_effect = [seeds] + [list(exact_rows)] * 8
    return engine, cursor


def test_recall_is_one_when_the_search_finds_the_exact_neighbours():
    exact = [("a", 0.9), ("b", 0.8), ("c", 0.1)]
    engine, _ = _engine(exact, [("a", 0.9), ("b", 0.8)])

    measured = engine.measure_route_recall(graph="g1", model_key="m1", k=2)

    assert measured is not None
    assert measured.recall == 1.0, (
        f"the search returned the exact top-2 and recall came back {measured.recall}"
    )
    assert measured.k == 2
    assert measured.table_name == "kg_emb_abc123"


def test_recall_reports_the_fraction_the_search_missed():
    exact = [("a", 0.9), ("b", 0.8), ("c", 0.1)]
    engine, _ = _engine(exact, [("a", 0.9), ("c", 0.1)])

    measured = engine.measure_route_recall(graph="g1", model_key="m1", k=2)

    assert measured.recall == 0.5, (
        f"one of two exact neighbours was missed; recall came back {measured.recall}"
    )


def test_a_tied_score_counts_as_a_hit():
    """Equal-width fixture vectors tie constantly, and a tie is not a miss.

    `b` and `c` score identically, so either is a correct answer for the second slot.
    Comparing ID sets would report 0.5 for a search that returned a right answer.
    """
    exact = [("a", 0.9), ("b", 0.8), ("c", 0.8)]
    engine, _ = _engine(exact, [("a", 0.9), ("c", 0.8)])

    measured = engine.measure_route_recall(graph="g1", model_key="m1", k=2)

    assert measured.recall == 1.0, (
        f"a tie with the k-th exact score was counted as a miss: {measured.recall}"
    )


def test_the_exact_scan_is_scoped_and_reads_no_vector():
    engine, cursor = _engine([("a", 0.9), ("b", 0.8)], [("a", 0.9)])

    engine.measure_route_recall(graph="g1", model_key="m1", k=1)

    scans = [c[0][0] for c in cursor.execute.call_args_list]
    assert scans, "nothing was executed, so nothing was measured"
    for sql in scans:
        assert "graph_id" in sql, f"a recall statement reads every graph: {sql}"
        assert "kg_emb_abc123" in sql, f"a recall statement left the route: {sql}"
    scoring = [s for s in scans if "VECTOR_COSINE" in s]
    assert scoring, f"no exact scan was run: {scans}"
    for sql in scoring:
        assert "TOP" not in sql.upper(), (
            "the exact scan uses the TOP + ORDER BY shape IRIS answers from the ANN "
            f"index, so it would compare the index to itself: {sql}"
        )
        assert sql.upper().startswith("SELECT NODE_ID, VECTOR_COSINE"), (
            "the exact scan must project an ID and a score only; the seed vector stays "
            f"inside IRIS as a subquery, because a vector fetched and re-bound is "
            f"reshaped by the driver (ADR-0005): {sql}"
        )


def test_the_measurement_is_written_to_the_registry_row():
    engine, _ = _engine([("a", 0.9)], [("a", 0.9)])

    measured = engine.measure_route_recall(graph="g1", model_key="m1", k=1)

    assert engine._record_route_recall.called, (
        "the number was computed and not recorded, which is the state FR-021 rejects"
    )
    args = engine._record_route_recall.call_args
    assert args[0][1] == "kg_emb_abc123", f"recorded against the wrong table: {args}"
    assert args[0][2] == "g1", f"recorded against the wrong graph: {args}"
    assert args[0][3] == measured.recall
    assert measured.measured_at, "a recall with no timestamp cannot be shown to be current"
    assert measured.recorded, "the write succeeded and the measurement says otherwise"


def test_record_false_measures_without_writing():
    """A caller comparing two runs needs the number without overwriting the row."""
    engine, _ = _engine([("a", 0.9)], [("a", 0.9)])

    measured = engine.measure_route_recall(graph="g1", model_key="m1", k=1, record=False)

    assert measured.recall == 1.0
    assert not engine._record_route_recall.called
    assert not measured.recorded, "nothing was written and the measurement claims it was"


def test_a_failed_registry_write_is_reported_not_swallowed():
    """A lost write must not read like a route nobody measured."""
    engine, _ = _engine([("a", 0.9)], [("a", 0.9)])
    engine._record_route_recall = MagicMock(return_value=False)

    measured = engine.measure_route_recall(graph="g1", model_key="m1", k=1)

    assert measured.recall == 1.0
    assert not measured.recorded, (
        "the registry refused the row and the measurement reports itself as recorded, "
        "which is how an unrecorded number gets published as a recorded one"
    )


def test_a_pair_with_no_route_measures_nothing():
    engine, cursor = _engine([], [], route=None)

    assert engine.measure_route_recall(graph="g9", model_key="m9") is None
    assert not cursor.execute.called, "a route that does not exist was queried anyway"
    assert not engine._record_route_recall.called


def test_an_empty_route_measures_nothing_rather_than_claiming_one():
    """Recall over zero rows is not 1.0. It is unmeasured, and must read as unmeasured."""
    engine, _ = _engine([], [], probes=())

    assert engine.measure_route_recall(graph="g1", model_key="m1") is None
    assert not engine._record_route_recall.called, (
        "an empty route recorded a recall number, which publishes a claim about no data"
    )
