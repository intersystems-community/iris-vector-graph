"""Spec 227 T039 — two creators, one route (FR-014).

Route creation is two statements that have to land together: a `CREATE TABLE` and
a registry row. Unserialised, two writers produce three bad outcomes, all silent:

- two `CREATE TABLE`s, the second failing, and a caller holding a route whose
  table it thinks it created;
- one table and two registry rows, so the pair resolves to two different names
  depending on which row is read first;
- one table, one row, and a loser that raises where it should simply use the
  winner's route.

Two arbitrations, and both are tested here because they fail differently:

**In process** — a lock on the route key. Both threads call `resolve_route(...,
create=True)`; one creates, the other re-reads inside the lock and returns the
same route. The lock is on the key, not global, so an unrelated pair is not
blocked.

**Across processes** — the registry primary key `(table_name, graph_id)`. No
Python lock spans processes, so the loser's INSERT raises, and the correct
response is to roll back, re-read, and return the winner's row. Simulated by a
registry that grows a row behind the caller's back, which is what a second
process looks like from here.
"""

import threading

import pytest

from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeCursor, FakeRegistry, engine_with

GRAPH_A = "ivg227-conc-a"
GRAPH_B = "ivg227-conc-b"
MODEL_A = "model-a"


def test_two_threads_creating_one_route_produce_one_table_and_one_row():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    results = {}
    errors = {}
    start = threading.Barrier(2)

    def create(tag):
        try:
            start.wait(timeout=5)
            results[tag] = engine.resolve_route(
                graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=384
            )
        except Exception as exc:  # recorded, not swallowed: asserted below
            errors[tag] = exc

    threads = [threading.Thread(target=create, args=(tag,)) for tag in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"a creator raised instead of using the winner's route: {errors}"
    assert len(results) == 2
    assert results["first"].table_name == results["second"].table_name
    assert len(registry.created_tables) == 1, registry.created_tables
    assert len(registry.rows) == 1, registry.rows


def test_the_loser_gets_the_winners_dimension_not_its_own_request():
    """Both asked to create; only one declared the column. The other must report
    the width that is actually declared, or its next write is refused by IRIS at
    INSERT with SQLCODE -104 and no explanation in the route it was holding."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    first = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=384)
    second = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=768)

    assert first.table_name == second.table_name
    assert second.dimension == 384
    assert len(registry.created_tables) == 1


def test_a_lock_on_one_route_does_not_block_another():
    """The lock is per route key. A namespace with 100 routes (FR-042) that
    serialised every creation would serialise every first write in it."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    held = threading.Event()
    release = threading.Event()
    other_done = threading.Event()

    original = FakeCursor.execute

    def slow_execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        if "CREATE TABLE" in flat and route_table_name(GRAPH_A, MODEL_A) in flat:
            held.set()
            # Blocks graph A's creation while graph B's runs to completion.
            release.wait(timeout=5)
        return original(self, sql, params)

    def create_b():
        held.wait(timeout=5)
        engine.resolve_route(graph=GRAPH_B, model_key=MODEL_A, create=True, dimension=384)
        other_done.set()
        release.set()

    FakeCursor.execute = slow_execute
    try:
        helper = threading.Thread(target=create_b)
        helper.start()
        engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=384)
        helper.join(timeout=10)
    finally:
        FakeCursor.execute = original
        release.set()

    assert other_done.is_set(), "graph B's route creation was blocked by graph A's"
    assert len(registry.created_tables) == 2


def test_a_row_that_appears_behind_our_back_is_used_not_raised_over():
    """What a second *process* looks like from here: the INSERT raises on the
    primary key, and the winner's row is already there to be read."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)
    winner = route_table_name(GRAPH_A, MODEL_A)

    original = FakeCursor.execute
    planted = {"done": False}

    def plant_then_execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        if not planted["done"] and "CREATE TABLE" in flat and winner in flat:
            planted["done"] = True
            registry.rows.append(
                {
                    "table_name": winner,
                    "graph_id": GRAPH_A,
                    "mechanism": "iris-embedding-config",
                    "model_key": MODEL_A,
                    "declared_config": MODEL_A,
                    "dimension": 384,
                    "dtype": "DOUBLE",
                    "index_state": "present",
                    "index_error": None,
                }
            )
        return original(self, sql, params)

    FakeCursor.execute = plant_then_execute
    try:
        route = engine.resolve_route(
            graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=384
        )
    finally:
        FakeCursor.execute = original

    assert route is not None
    assert route.table_name == winner
    assert len(registry.rows) == 1, "the loser inserted a second row"


def test_a_create_table_that_lost_the_race_is_not_an_error():
    """IRIS reports "table already exists" with SQLCODE -201. The other process
    got there first, which is the expected outcome, not a failure."""
    existing = route_table_name(GRAPH_A, MODEL_A)
    # The table is there; the registry row is not. Exactly the window between the
    # winner's two statements.
    registry = FakeRegistry(tables=[existing])
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=384)

    assert route.table_name == existing
    assert registry.row_for(existing, GRAPH_A) is not None


def test_a_registry_that_cannot_be_read_does_not_yield_a_route():
    """A resolver that answers from the hash when the read fails is the exact
    failure FR-011 forbids: it would create a second table for a route that
    exists and split one graph's vectors across both."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    original = FakeCursor.execute

    def refuse(self, sql, params=None):
        flat = " ".join(str(sql).split())
        if "embedding_registry" in flat and flat.upper().startswith("SELECT"):
            raise RuntimeError("[SQLCODE: <-30>:<Table does not exist>] embedding_registry")
        return original(self, sql, params)

    FakeCursor.execute = refuse
    try:
        assert engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A) is None
    finally:
        FakeCursor.execute = original
