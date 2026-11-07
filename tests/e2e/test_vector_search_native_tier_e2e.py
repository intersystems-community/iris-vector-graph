"""`search_nodes_by_vector` on the native VECTOR_COSINE tier answers fast on a live namespace.

Moved from tests/unit/test_vector_search_routing.py (4.6.0): it needs the test container, and in the unit suite an
"offline" run (`SKIP_IRIS_TESTS=true` with the container up) reached it (tests/unit/test_460_vector_routing_unit_is_offline.py).
A timing check: under load, rerun it alone before believing a failure (docs/dev/TESTING_POLICY.md, Environment).
"""

from __future__ import annotations

import math
import random
import time

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.requires_database]


def test_native_tier_faster_than_ivf_on_live_db(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine(iris_connection, embedding_dimension=768)
    if not e._probe_native_vec():
        pytest.skip("native VECTOR_COSINE not available")

    def rand_vec(d):
        v = [random.gauss(0, 1) for _ in range(d)]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    q = rand_vec(768)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        e.search_nodes_by_vector(q, k=10)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    assert times[2] < 200, f"vector_search p50={times[2]:.1f}ms — native HNSW should be fast"
