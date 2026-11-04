"""4.6.0 R217: ``get_temporal_aggregate`` answers exactly the window ``[ts_start, ts_end]``.

Walkthrough 2 (W5): the aggregate counted every 300 s bucket the window touched whole, so an event outside the window
but in a touched bucket was counted. The report's events (T a multiple of 300): ``ann -CALLED->`` at T+5 (weight 2),
T+100 (4), T+299 (9), T+300 (1), T+650 (5), and ``ben -CALLED->`` at T+10 (7), which is never ann's.

By hand:
- ``[T+100, T+300]``: T+100, T+299, T+300 -> count 3, sum 14.0, avg 14/3, min 1.0, max 9.0 (it answered 4, 16.0, 4.0:
  the T+5 event shares a bucket with T+100);
- ``[T, T+299]``, one whole bucket: T+5, T+100, T+299 -> count 3, sum 15.0, min 2.0, max 9.0;
- ``[T, T+1000]``: all five -> count 5, sum 21.0, avg 4.2;
- ``[T+301, T+649]``, inside the second and third buckets with no event of ann's -> count 0;
- ``[T+650, T+650]``: the T+650 event alone -> count 1, sum 5.0.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]

T = 1_900_000_200


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    assert T % 300 == 0
    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ann, ben = f"r217_{run}_ann", f"r217_{run}_ben"
    ev = [
        (ann, f"r217_{run}_b", T + 5, 2.0),
        (ann, f"r217_{run}_b", T + 100, 4.0),
        (ann, f"r217_{run}_c", T + 299, 9.0),
        (ann, f"r217_{run}_d", T + 300, 1.0),
        (ann, f"r217_{run}_b", T + 650, 5.0),
        (ben, ann, T + 10, 7.0),
    ]
    written = e.bulk_create_edges_temporal([{"s": s, "p": "CALLED", "o": o, "ts": ts, "w": w} for s, o, ts, w in ev])
    assert int(written) == 6
    yield e, ann


@pytest.mark.parametrize(
    "lo, hi, expected",
    [
        (100, 300, {"count": 3, "sum": 14.0, "avg": 14.0 / 3, "min": 1.0, "max": 9.0}),
        (0, 299, {"count": 3, "sum": 15.0, "avg": 5.0, "min": 2.0, "max": 9.0}),
        (0, 1000, {"count": 5, "sum": 21.0, "avg": 4.2, "min": 1.0, "max": 9.0}),
        (650, 650, {"count": 1, "sum": 5.0, "avg": 5.0, "min": 5.0, "max": 5.0}),
    ],
)
def test_the_aggregate_is_the_window(g, lo, hi, expected):
    e, ann = g
    got = {m: e.get_temporal_aggregate(ann, "CALLED", m, T + lo, T + hi) for m in expected}
    assert got["count"] == expected["count"] and isinstance(got["count"], int)
    for m in ("sum", "avg", "min", "max"):
        assert got[m] == pytest.approx(expected[m], abs=1e-6), (m, got)


def test_a_window_with_no_event_counts_zero(g):
    e, ann = g
    assert e.get_temporal_aggregate(ann, "CALLED", "count", T + 301, T + 649) == 0
    assert e.get_temporal_aggregate(ann, "CALLED", "sum", T + 301, T + 649) == 0.0


def test_it_agrees_with_the_window_read(g):
    e, ann = g
    for lo, hi in ((100, 300), (6, 99), (0, 1000), (299, 300)):
        events = e.get_edges_in_window(ann, "CALLED", T + lo, T + hi)
        assert e.get_temporal_aggregate(ann, "CALLED", "count", T + lo, T + hi) == len(events), (lo, hi)
        total = sum(float(x["w"]) for x in events)
        assert e.get_temporal_aggregate(ann, "CALLED", "sum", T + lo, T + hi) == pytest.approx(total), (lo, hi)
