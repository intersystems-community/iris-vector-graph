"""4.1.1 — BFS on a graph too big for the Arno callout answers, and Arno stays on.

See tests/unit/test_411_bfs_zf_limit.py.
"""

from __future__ import annotations

import json
import random
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVG411_ZF"


@pytest.fixture
def big(iris_connection, iris_master_cleanup):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    o = engine._iris_obj()
    p = f"zf{uuid.uuid4().hex[:6]}_"
    rnd = random.Random(1)
    # 1,000 nodes, 3,000 edges: a 100 KB adjacency, past the callout's limit.
    for i in range(1000):
        for _ in range(3):
            d = rnd.randrange(1000)
            o.set(1, "^KG", "out", 0, f"{p}{i:04d}", PRED, f"{p}{d:04d}")
            o.set(1, "^KG", "in", 0, f"{p}{d:04d}", PRED, f"{p}{i:04d}")
    o.classMethodValue("Graph.KG.Traversal", "BuildNKG")
    return engine, o, f"{p}0000"


def _hits(o, raw):
    tag = raw.split(":")[1]
    return {r["o"] for r in json.loads(str(o.classMethodValue("Graph.KG.Traversal", "ReadBFSResults", tag)))}


def test_bfs_json_matches_objectscript_on_a_graph_past_the_limit(big):
    engine, o, seed = big
    o.classMethodValue("Graph.KG.ArnoAccel", "Load")
    arno = str(o.classMethodValue("Graph.KG.NKGAccel", "BFSJson", seed, f'["{PRED}"]', 2, 0))
    arno_hits = _hits(o, arno)
    plain = str(
        o.classMethodValue("Graph.KG.Traversal", "BFSFastJsonSorted", seed, f'["{PRED}"]', 2, "", "out", 0)
    )
    assert arno_hits and arno_hits == _hits(o, plain)


def test_the_store_probe_keeps_arno_on(big):
    engine, _, _ = big
    engine._store._arno_available = None
    assert engine._store._detect_arno() is True
