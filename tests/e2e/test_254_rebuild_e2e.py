"""Spec 254 US3 / FR-004: a rebuild from zero lands the same graph.

opsreview's Enrichment process drops a tenant's graph and replays its event table, and its acceptance test 5 compares
the result by hash. Here: 100,000 one-node-per-event temporal edges plus identical colliding duplicates, written in
batches of 1,000; every store of the graph is digested (``Graph.KG.GraphStores.Digest``); the graph is erased and the
same writes replayed in batches of 777, then in a shuffled order. The digests must be equal, aggregates included.

What the test pins (US3 scenario 2): colliding writes are identical, and a triple's timestamps share one weight. The
first write at a coordinate wins, and a marked row takes its triple's first-written weight, so order cannot matter
only under those two conditions. Batch size never matters.

``IVG_254_REBUILD_EVENTS`` overrides the size. Run: ``IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise
IVG_PORT=31972``.
"""

from __future__ import annotations

import os
import random
import uuid

import iris
import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .graph_snapshot import CACHES, compare, digest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_database,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

N = int(os.environ.get("IVG_254_REBUILD_EVENTS", "100000"))
T0 = 1_760_000_000


@pytest.fixture
def env(iris_connection, iris_master_cleanup):
    engine = IRISGraphEngine(iris_connection)
    return engine, iris.createIRIS(iris_connection), f"g254r_{uuid.uuid4().hex[:6]}"


def _writes():
    rng = random.Random(254)
    out = []
    for i in range(N):
        out.append({"s": f"inst:{i % 80}", "p": "ACTION_AT", "o": f"action:{i}", "ts": T0 + i * 3,
                    "w": round(rng.uniform(0.5, 9.5), 2), "attrs": {"kind": rng.choice(["cfg", "deploy", "finding"]), "n": i}})
    # long-lived triples: many timestamps, one weight each
    for h in range(200):
        out.append({"s": f"inst:{h % 5}", "p": "HAS_CFG", "o": f"cfg:{h % 5}", "ts": T0 + h * 997, "w": 1.25})
    # identical duplicates (replayed events): reported as collisions, stored once
    dups = [dict(out[j]) for j in rng.sample(range(N), max(1, N // 100))]
    return out + dups, len(dups)


def _load(engine, writes, graph, batch):
    collisions = written = 0
    for k in range(0, len(writes), batch):
        n = engine.bulk_create_edges_temporal(writes[k:k + batch], graph=graph)
        written += int(n)
        collisions += n.collisions
    return written, collisions


def test_batch_size_and_order_do_not_change_any_store(env):
    engine, native, g = env
    writes, ndup = _writes()

    assert _load(engine, writes, g, 1000) == (len(writes) - ndup, ndup)
    first = digest(native, g)
    assert first["stores"]['^KG("tout")']["entries"] == len(writes) - ndup

    engine.erase_graph(g)
    assert _load(engine, writes, g, 777) == (len(writes) - ndup, ndup)
    assert compare(first, digest(native, g), exclude=CACHES) == []

    engine.erase_graph(g)
    shuffled = list(writes)
    random.Random(777).shuffle(shuffled)
    assert _load(engine, shuffled, g, 1000) == (len(writes) - ndup, ndup)
    assert compare(first, digest(native, g), exclude=CACHES) == []


# ------------------------------------------------------------------------------------- the digest itself


def test_the_digest_moves_when_one_entry_does(env):
    engine, native, g = env
    engine.bulk_create_edges_temporal([{"s": "a", "p": "P", "o": "b", "ts": T0, "w": 2.0, "attrs": {"k": 1}}], graph=g)
    before = digest(native, g)
    key = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", g)
    native.set("x", "KG", "edgeprop", key, T0, "a", "P", "b", "k")
    after = digest(native, g)
    assert [name for name, _, _ in compare(before, after)] == ['^KG("edgeprop")']
    native.set(3.0, "KG", "tout", key, T0, "a", "P", "b")
    assert '^KG("tout")' in [name for name, _, _ in compare(after, digest(native, g))]
    cur = engine.conn.cursor()
    cur.execute("UPDATE Graph_KG.rdf_edges SET qualifiers = '{}' WHERE graph_id = ?", [g])
    engine.conn.commit()
    assert "Graph_KG.rdf_edges" in [name for name, _, _ in compare(after, digest(native, g))]


def test_two_graphs_with_the_same_content_digest_the_same(env):
    engine, native, g = env
    edges = [{"s": "a", "p": "P", "o": "b", "ts": T0, "w": 2.0, "attrs": {"k": 1}}]
    engine.bulk_create_edges_temporal(edges, graph=g)
    engine.bulk_create_edges_temporal(edges, graph=g + "_twin")
    a, b = digest(native, g), digest(native, g + "_twin")
    assert compare(a, b) == []
    assert a["stores"]['^KG("tout")']["entries"] == 1 and a["stores"]["Graph_KG.rdf_edges"]["entries"] == 1
    assert digest(native, g + "_empty")["stores"]['^KG("tout")'] == {"entries": 0, "sha256": a["empty"]}
