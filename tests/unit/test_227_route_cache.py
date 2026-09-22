"""Spec 227 SC-010 — a resolved route is remembered, so one graph pays nothing for routing.

Measured on the live container before this cache existed: 300 vectors in the default
graph, `kg_KNN_VEC` at 1.159× the 3.2.0 statement on the same rows (SC-010's budget is
1.10×). The extra was one registry round trip per search, spent to learn a route that
had not changed — a cost a single-graph installation gets nothing for, which is exactly
what SC-010 forbids.

What may be cached is narrow, because the failure modes are not symmetric:

- **a row** is cached: a route's name and width do not change while it exists.
- **no row** is never cached: the next write creates the route, and a remembered miss
  would send that write's reader to nothing.
- **an unreadable registry** is never cached: it is a transient condition, and
  remembering it turns a moment's failure into an unrouted session.

Anything that changes a row — an index state, a recall, a dropped route — drops it.
"""

from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine

ROW = ("kg_emb_abc123", 8, "DOUBLE", "present", None)


def _engine(rows=(ROW,)):
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine._schema_prefix = "Graph_KG"
    engine._t = lambda name: f"Graph_KG.{name}"
    engine._registry_table = lambda: "Graph_KG.embedding_registry"
    cursor = engine.conn.cursor.return_value
    cursor.fetchall.return_value = [tuple(r) for r in rows]
    return engine, cursor


def _reads(cursor):
    """How many registry SELECTs were issued."""
    return sum(
        1
        for call in cursor.execute.call_args_list
        if "embedding_registry" in call[0][0] and call[0][0].upper().startswith("SELECT")
    )


def test_a_resolved_route_is_read_once():
    engine, cursor = _engine()

    first = engine._read_route("g1", "m1")
    second = engine._read_route("g1", "m1")

    assert first == second
    assert _reads(cursor) == 1, (
        f"the same route was read from the registry {_reads(cursor)} times; every search "
        f"on a single-graph install pays that round trip (SC-010)"
    )


def test_each_pair_is_cached_separately():
    """One cache entry per `(graph, model)`, or a second graph reads the first's table."""
    engine, cursor = _engine()

    engine._read_route("g1", "m1")
    engine._read_route("g2", "m1")
    engine._read_route("g1", "m2")

    assert _reads(cursor) == 3, (
        f"three distinct pairs produced {_reads(cursor)} registry reads, so two of them "
        f"share a cache key and one is answering with the other's route"
    )


def test_a_missing_route_is_not_remembered():
    """The write that follows a miss creates the route; a cached miss hides it."""
    engine, cursor = _engine(rows=())

    assert engine._read_route("g1", "m1") == (True, None)
    assert engine._read_route("g1", "m1") == (True, None)

    assert _reads(cursor) == 2, "a route that did not exist yet was cached as absent"


def test_an_unreadable_registry_is_not_remembered():
    engine, cursor = _engine()
    cursor.execute.side_effect = Exception("registry offline")

    assert engine._read_route("g1", "m1") == (False, None)
    cursor.execute.side_effect = None

    readable, route = engine._read_route("g1", "m1")
    assert readable, "one failed registry read turned the whole session unrouted"
    assert route is not None


def test_recording_an_index_state_drops_the_cached_route():
    engine, cursor = _engine()
    engine._read_route("g1", "m1")

    engine._record_route_index_state(cursor, "kg_emb_abc123", "g1", "refused", "no HNSW")

    engine._read_route("g1", "m1")
    assert _reads(cursor) == 2, (
        "the route's index state changed and the cache kept serving the old row, so "
        "`is_indexed` and the inventory disagree with the registry"
    )


def test_recording_a_recall_drops_the_cached_route():
    engine, cursor = _engine()
    engine._read_route("g1", "m1")

    engine._record_route_recall(cursor, "kg_emb_abc123", "g1", 0.97, "2026-09-20 12:00:00")

    engine._read_route("g1", "m1")
    assert _reads(cursor) == 2, "a measured route kept serving its pre-measurement row"


def test_invalidate_route_cache_clears_everything():
    """The blunt instrument, for callers that drop tables out from under the engine."""
    engine, cursor = _engine()
    engine._read_route("g1", "m1")
    engine._read_route("g2", "m1")

    engine.invalidate_route_cache()

    engine._read_route("g1", "m1")
    engine._read_route("g2", "m1")
    assert _reads(cursor) == 4


def test_a_cached_route_expires():
    """Another process can drop a route. The cache is per engine, so it must not be
    the last word for longer than it takes to notice."""
    engine, cursor = _engine()
    engine._route_cache_ttl_seconds = 0.0

    engine._read_route("g1", "m1")
    engine._read_route("g1", "m1")

    assert _reads(cursor) == 2, (
        "a zero TTL still served a cached route, so a route dropped elsewhere would be "
        "read from this engine until it restarts"
    )
