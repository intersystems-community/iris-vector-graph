"""4.6.0 R252: ``bulk_create_edges_temporal`` stores a given weight of 0 as 0.

By hand: ``zed -PING->`` four targets at T+1 .. T+4 (T a multiple of 300, one bucket) with ``w`` 0, ``w`` 0.0,
``weight`` 0, and no weight. Read back with ``get_edges_in_window`` the weights are 0.0, 0.0, 0.0 and 1.0 (before:
1.0, 1.0, 0.0, 1.0, since ``e.get("w") or ...`` read a ``w`` of 0 as absent); the window's ``sum`` is 1.0 and its
``max`` 1.0, ``min`` 0.0 (before: sum 3.0, min 0.0).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]

T = 1_900_000_500 // 300 * 300


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    zed = f"r252_{run}_zed"
    targets = {k: f"r252_{run}_{k}" for k in ("w0", "w00", "weight0", "none")}
    written = e.bulk_create_edges_temporal(
        [
            {"s": zed, "p": "PING", "o": targets["w0"], "ts": T + 1, "w": 0},
            {"s": zed, "p": "PING", "o": targets["w00"], "ts": T + 2, "w": 0.0},
            {"s": zed, "p": "PING", "o": targets["weight0"], "ts": T + 3, "weight": 0},
            {"s": zed, "p": "PING", "o": targets["none"], "ts": T + 4},
        ]
    )
    assert int(written) == 4
    yield e, zed, targets


def test_a_zero_weight_is_stored_as_zero(g):
    e, zed, targets = g
    edges = e.get_edges_in_window(zed, "PING", T, T + 299)
    got = {x["o"]: x["w"] for x in edges}
    assert got == {targets["w0"]: 0.0, targets["w00"]: 0.0, targets["weight0"]: 0.0, targets["none"]: 1.0}


def test_the_window_aggregate_counts_the_zeros(g):
    e, zed, _ = g
    assert e.get_temporal_aggregate(zed, "PING", "count", T, T + 299) == 4
    assert e.get_temporal_aggregate(zed, "PING", "sum", T, T + 299) == 1.0
    assert e.get_temporal_aggregate(zed, "PING", "min", T, T + 299) == 0.0
    assert e.get_temporal_aggregate(zed, "PING", "max", T, T + 299) == 1.0
