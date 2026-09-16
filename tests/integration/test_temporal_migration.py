"""`MigrateToGraphScoped` must move every flat temporal entry, or move none.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_temporal_migration.py

Spec-223 put the graph key first in all five temporal `^KG` globals and shipped a
migration for databases written before it. The migration decided what was flat by
guessing at subscript *values* — "a first subscript above 1000000 looks like a
Unix timestamp" — and stopped its `$Order` walk at the first subscript that did
not look like one. Integer 0 collates before every positive timestamp, so a
database holding even one graph-scoped default-graph edge made the walk stop on
its first step and migrate nothing, while reporting 0 the way an
already-migrated database does. It also skipped every entry whose source node id
was numeric, left `bucket` and `tagg` at their flat coordinates entirely, and
kept its flat source rows whenever a scoped entry already occupied the target.

What replaces the guessing is the shape of the tree: a flat `tout` timestamp has
`(s, p, o)` below it and a graph key has `(ts, s, p, o)`, so the two are told
apart by depth rather than by what their subscripts look like. The layout is the
fact; a value heuristic is a guess about it.

Everything here seeds the flat layout through the Native API, because that layout
has no writer left — it is what a pre-223 database already contains.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

# A bucket is 300s wide and starts on a multiple of 300 (TemporalIndex.BUCKET).
BUCKET_WIDTH = 300
TS = 1_700_000_100
BUCKET = TS // BUCKET_WIDTH


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


@pytest.fixture()
def iris_obj(engine):
    return engine._iris_obj()


def _migrate(engine) -> int:
    return int(
        str(
            engine._iris_obj().classMethodValue(
                "Graph.KG.TemporalIndex", "MigrateToGraphScoped"
            )
        )
    )


def _seed_flat(iris_obj, ts, s, p, o, weight=1.0):
    """One pre-223 temporal edge: raw, reverse, and its derived entries.

    Written at the coordinates the pre-223 code wrote — no graph subscript
    anywhere — so the migration has the same input a real upgrade would give it.
    """
    iris_obj.set(weight, "^KG", "tout", ts, s, p, o)
    iris_obj.set(weight, "^KG", "tin", ts, o, p, s)
    bucket = ts // BUCKET_WIDTH
    iris_obj.set(1, "^KG", "bucket", bucket, s)
    iris_obj.set(1, "^KG", "tagg", bucket, s, p, "count")
    iris_obj.set(weight, "^KG", "tagg", bucket, s, p, "sum")
    iris_obj.set(weight, "^KG", "tagg", bucket, s, p, "min")
    iris_obj.set(weight, "^KG", "tagg", bucket, s, p, "max")


def _scoped(iris_obj, *subs):
    return iris_obj.get("^KG", *subs)


# ---------------------------------------------------------------------------
# The raw trees
# ---------------------------------------------------------------------------


def test_a_flat_edge_lands_in_the_default_graph(engine, iris_obj):
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o", 2.5)

    assert _migrate(engine) == 1

    assert _scoped(iris_obj, "tout", 0, TS, "mig_s", "SAW", "mig_o") is not None
    assert _scoped(iris_obj, "tin", 0, TS, "mig_o", "SAW", "mig_s") is not None
    assert _scoped(iris_obj, "tout", TS, "mig_s", "SAW", "mig_o") is None, (
        "the flat entry survived the migration, so both layouts now answer for "
        "the same edge"
    )
    assert _scoped(iris_obj, "tin", TS, "mig_o", "SAW", "mig_s") is None


def test_a_flat_edge_migrates_when_scoped_data_already_exists(engine, iris_obj):
    """The headline defect: integer 0 collates first, so the walk stopped there.

    A database that has taken a single default-graph write since upgrading is
    exactly the database that most needs the migration, and is the one where it
    reported success having done nothing.
    """
    engine.create_edge_temporal("scoped_s", "SAW", "scoped_o", timestamp=TS)
    assert _scoped(iris_obj, "tout", 0, TS, "scoped_s", "SAW", "scoped_o") is not None
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o")

    assert _migrate(engine) == 1, (
        "the migration stopped at the graph key 0 that collates before every "
        "timestamp and reported the same 0 an already-migrated database reports"
    )

    assert _scoped(iris_obj, "tout", 0, TS, "mig_s", "SAW", "mig_o") is not None
    assert _scoped(iris_obj, "tout", 0, TS, "scoped_s", "SAW", "scoped_o") is not None


def test_a_flat_edge_with_a_numeric_source_migrates(engine, iris_obj):
    """Node ids are frequently integers, and those entries were skipped silently."""
    _seed_flat(iris_obj, TS, 4242, "SAW", 9999)

    assert _migrate(engine) == 1, (
        "an entry whose source node id is numeric was left at its flat "
        "coordinates and not counted"
    )
    assert _scoped(iris_obj, "tout", 0, TS, 4242, "SAW", 9999) is not None


def test_edge_properties_follow_their_edge(engine, iris_obj):
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o")
    iris_obj.set("clinic-3", "^KG", "edgeprop", TS, "mig_s", "SAW", "mig_o", "site")

    _migrate(engine)

    assert (
        _scoped(iris_obj, "edgeprop", 0, TS, "mig_s", "SAW", "mig_o", "site")
        == "clinic-3"
    )
    assert _scoped(iris_obj, "edgeprop", TS, "mig_s", "SAW", "mig_o", "site") is None


def test_a_flat_entry_whose_target_is_already_scoped_is_still_removed(engine, iris_obj):
    """A collision leaves one entry, not two at two layouts.

    The scoped entry wins because it is the one every reader can see; the flat
    duplicate is removed rather than left for the next run to trip over.
    """
    engine.create_edge_temporal("mig_s", "SAW", "mig_o", timestamp=TS, weight=7.0)
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o", 1.0)

    _migrate(engine)

    assert _scoped(iris_obj, "tout", TS, "mig_s", "SAW", "mig_o") is None
    assert float(_scoped(iris_obj, "tout", 0, TS, "mig_s", "SAW", "mig_o")) == 7.0


# ---------------------------------------------------------------------------
# The derived trees
# ---------------------------------------------------------------------------


def test_the_derived_entries_move_with_the_raw_ones(engine, iris_obj):
    """`bucket` and `tagg` were never touched, so migrated edges lost their windows."""
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o", 2.0)

    _migrate(engine)

    assert _scoped(iris_obj, "bucket", 0, BUCKET, "mig_s") is not None, (
        "the migrated edge's source is not active in any scoped bucket, so "
        "QueryWindowSources cannot find it"
    )
    assert int(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "count")) == 1
    assert float(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "sum")) == 2.0
    assert _scoped(iris_obj, "bucket", BUCKET, "mig_s") is None
    assert _scoped(iris_obj, "tagg", BUCKET, "mig_s", "SAW", "count") is None


def test_aggregates_whose_raw_edges_were_purged_survive(engine, iris_obj):
    """`PurgeRawBefore` keeps aggregates for 13 months after dropping raw edges.

    Recomputing every bucket from raw would delete exactly the history that
    retention policy exists to keep, so a bucket with no scoped counterpart is
    relocated rather than recomputed.
    """
    old_bucket = BUCKET - 1000
    iris_obj.set(5, "^KG", "bucket", old_bucket, "purged_s")
    iris_obj.set(5, "^KG", "tagg", old_bucket, "purged_s", "SAW", "count")
    iris_obj.set(12.5, "^KG", "tagg", old_bucket, "purged_s", "SAW", "sum")
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o")

    _migrate(engine)

    assert int(_scoped(iris_obj, "tagg", 0, old_bucket, "purged_s", "SAW", "count")) == 5, (
        "an aggregate whose raw edges were already purged was lost by the "
        "migration"
    )
    assert float(_scoped(iris_obj, "tagg", 0, old_bucket, "purged_s", "SAW", "sum")) == 12.5
    assert _scoped(iris_obj, "tagg", old_bucket, "purged_s", "SAW", "count") is None


def test_a_bucket_holding_both_layouts_is_recomputed_from_raw(engine, iris_obj):
    """Two aggregates for one bucket cannot be added together.

    `count` and `sum` would merge, `min` and `max` would merge, and the HLL
    sketch is an opaque register string that would not — so a bucket that exists
    at both layouts is rebuilt from the raw entries rather than merged, and the
    result counts each edge once.
    """
    engine.create_edge_temporal("mig_s", "SAW", "scoped_o", timestamp=TS, weight=1.0)
    _seed_flat(iris_obj, TS + 1, "mig_s", "SAW", "flat_o", 3.0)

    _migrate(engine)

    assert int(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "count")) == 2, (
        "the recomputed bucket does not count both edges exactly once"
    )
    assert float(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "sum")) == 4.0
    assert float(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "min")) == 1.0
    assert float(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "max")) == 3.0
    assert int(_scoped(iris_obj, "bucket", 0, BUCKET, "mig_s")) == 2


# ---------------------------------------------------------------------------
# The contract at the interface
# ---------------------------------------------------------------------------


def test_a_migrated_edge_answers_a_window_query(engine, iris_obj):
    """The reason to migrate at all: the reader is graph-scoped, the data was not."""
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o", 2.0)

    _migrate(engine)

    edges = engine.get_edges_in_window("mig_s", "SAW", TS - 10, TS + 10)
    assert len(edges) == 1, f"the migrated edge is invisible to the reader: {edges}"


def test_a_second_run_migrates_nothing(engine, iris_obj):
    _seed_flat(iris_obj, TS, "mig_s", "SAW", "mig_o")
    assert _migrate(engine) == 1

    assert _migrate(engine) == 0

    assert _scoped(iris_obj, "tout", 0, TS, "mig_s", "SAW", "mig_o") is not None
    assert int(_scoped(iris_obj, "tagg", 0, BUCKET, "mig_s", "SAW", "count")) == 1


def test_nothing_to_migrate_returns_zero(engine, iris_obj):
    engine.create_edge_temporal("scoped_s", "SAW", "scoped_o", timestamp=TS)

    assert _migrate(engine) == 0

    assert _scoped(iris_obj, "tout", 0, TS, "scoped_s", "SAW", "scoped_o") is not None
