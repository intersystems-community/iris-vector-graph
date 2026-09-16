"""Upgrading the package against an existing database — the in-place half.

The other upgrade path, and the one no archive can describe: a consumer installs
the new version over the database they already have. Nothing is re-shaped for
them. Their `^KG` tree sits exactly where the old release left it until a
migration moves it, and every current reader is graph-scoped — so until the
migration runs, their temporal history is present on disk and invisible through
the interface.

That starting condition is what `<tag>.globals.ndjson` carries: every `^KG` node
in the old release's own layout, captured from a scratch namespace running that
tag's compiled ObjectScript (see `tests/e2e/fixtures/old_releases.py`). The
archive cannot stand in for it, because `restore_snapshot` imports globals at
today's coordinates and neither release exported its temporal index at all.

Both frozen releases predate spec-223, so both hold the flat temporal layout:
`^KG("tout", ts, s, p, o)` with no graph key. `MigrateToGraphScoped` is what moves
it, and the assertions below are about the layout *and* about the interface,
because the layout is only interesting insofar as a reader can then see it.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/e2e/test_upgrade_migrate_globals_e2e.py
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.e2e.fixtures.old_releases import OLD_RELEASES, RELEASE_IDS

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

# TemporalIndex.BUCKET — a bucket is 300s wide and starts on a multiple of 300.
BUCKET_WIDTH = 300


@pytest.fixture(params=OLD_RELEASES, ids=RELEASE_IDS)
def old_database(request, iris_connection, iris_master_cleanup):
    """A current install sitting on one old release's untouched globals.

    `^KG` is emptied first so the tree under test is the old release's and nothing
    else: a leftover scoped entry from another test would be indistinguishable
    from work the migration did.
    """
    release = request.param
    engine = IRISGraphEngine(
        iris_connection, embedding_dimension=release.embedding_dimension
    )
    engine.initialize_schema()

    iris_obj = engine._iris_obj()
    iris_obj.kill("^KG")
    assert release.write_globals(iris_obj) > 0

    return release, engine, iris_obj


def _migrate(engine) -> int:
    return int(
        str(
            engine._iris_obj().classMethodValue(
                "Graph.KG.TemporalIndex", "MigrateToGraphScoped"
            )
        )
    )


def _window(release):
    """A span covering every seeded temporal edge, inclusive."""
    stamps = [e["timestamp"] for e in release.seeded["temporal"]]
    return min(stamps) - 1, max(stamps) + 1


# ── before the migration ───────────────────────────────────────────────────


def test_the_old_layout_arrives_flat(old_database):
    """The starting condition, stated rather than assumed.

    Depth is the tell, as it is in the migration itself: a flat `tout` timestamp
    has `(s, p, o)` below it, a graph key has `(ts, s, p, o)`.
    """
    release, _engine, iris_obj = old_database

    for edge in release.seeded["temporal"]:
        flat = iris_obj.get(
            "^KG", "tout", edge["timestamp"], edge["source"], edge["predicate"], edge["target"]
        )
        assert flat is not None, f"{edge} did not load at its old coordinates"
        scoped = iris_obj.get(
            "^KG", "tout", 0, edge["timestamp"], edge["source"], edge["predicate"], edge["target"]
        )
        assert scoped is None, "the fixture already holds scoped temporal data"


def test_the_temporal_history_is_invisible_until_the_migration_runs(old_database):
    """The consumer-visible symptom of an un-migrated upgrade.

    Every reader takes a graph key as its first subscript, so a flat tree answers
    no query. The data is on disk and intact; a consumer who upgrades and does not
    migrate sees an empty temporal index and no error.
    """
    release, engine, _iris_obj = old_database
    start, end = _window(release)

    assert engine.get_edges_in_window("", "", start, end) == []


def test_the_aggregates_are_invisible_too(old_database):
    """`bucket` and `tagg` are graph-scoped by the same subscript rule."""
    release, engine, _iris_obj = old_database
    start, end = _window(release)

    assert engine.get_temporal_aggregate("n1", "calls", "count", start, end) == 0


# ── the migration ──────────────────────────────────────────────────────────


def test_every_temporal_edge_moves_into_the_default_graph(old_database):
    """A flat entry can only have come from the default graph (ADR-0001)."""
    release, engine, iris_obj = old_database

    moved = _migrate(engine)

    assert moved == len(release.seeded["temporal"])
    for edge in release.seeded["temporal"]:
        coords = (edge["timestamp"], edge["source"], edge["predicate"], edge["target"])
        assert iris_obj.get("^KG", "tout", 0, *coords) is not None, edge
        assert iris_obj.get("^KG", "tout", *coords) is None, (
            f"{edge} exists at both layouts, so two trees now answer for one edge"
        )


def test_the_reverse_index_moves_with_it(old_database):
    """`tin` is a separate tree and a separate `MigrateOrphans` pass."""
    release, engine, iris_obj = old_database

    _migrate(engine)

    for edge in release.seeded["temporal"]:
        coords = (edge["timestamp"], edge["target"], edge["predicate"], edge["source"])
        assert iris_obj.get("^KG", "tin", 0, *coords) is not None, edge
        assert iris_obj.get("^KG", "tin", *coords) is None, edge


def test_the_migration_makes_the_window_readable(old_database):
    """The point of the whole exercise, at the interface a consumer calls."""
    release, engine, _iris_obj = old_database
    start, end = _window(release)

    _migrate(engine)

    found = engine.get_edges_in_window("", "", start, end)

    assert {(e["s"], e["p"], e["o"], int(e["ts"])) for e in found} == {
        (e["source"], e["predicate"], e["target"], e["timestamp"])
        for e in release.seeded["temporal"]
    }


def test_the_migrated_edges_keep_their_weights(old_database):
    """A relocation, not a rebuild: the old values come across unchanged."""
    release, engine, _iris_obj = old_database
    start, end = _window(release)

    _migrate(engine)

    by_coord = {
        (e["s"], e["p"], e["o"]): float(e["w"])
        for e in engine.get_edges_in_window("", "", start, end)
    }
    for edge in release.seeded["temporal"]:
        key = (edge["source"], edge["predicate"], edge["target"])
        assert by_coord[key] == pytest.approx(edge["weight"])


def test_the_derived_trees_move_so_aggregates_answer_again(old_database):
    """`bucket` and `tagg` are the half a raw-only migration would leave behind.

    They have no relational source, so anything not moved is gone: purged raw
    edges leave their aggregates behind deliberately (13-month retention), and a
    rebuild from raw could not put those contributions back.
    """
    release, engine, iris_obj = old_database
    start, end = _window(release)

    _migrate(engine)

    n1_edges = [e for e in release.seeded["temporal"] if e["source"] == "n1"]
    assert engine.get_temporal_aggregate("n1", "calls", "count", start, end) == len(
        n1_edges
    )
    assert engine.get_temporal_aggregate(
        "n1", "calls", "sum", start, end
    ) == pytest.approx(sum(e["weight"] for e in n1_edges))

    for edge in release.seeded["temporal"]:
        bucket = edge["timestamp"] // BUCKET_WIDTH
        assert iris_obj.get("^KG", "bucket", 0, bucket, edge["source"]) is not None
        assert iris_obj.get("^KG", "bucket", bucket, edge["source"]) is None


def test_the_hll_sketch_survives_the_move(old_database):
    """The one value in `tagg` that cannot be recomputed or merged here.

    An HLL register string is opaque to the migration, so a bucket that is
    relocated keeps its sketch and a bucket that has to be recomputed loses
    whatever raw it no longer has. These buckets exist at one layout only, so
    relocation is what must happen — and the sketch must arrive byte for byte.
    """
    _release, engine, iris_obj = old_database
    bucket = 1700000000 // BUCKET_WIDTH
    before = iris_obj.get("^KG", "tagg", bucket, "n1", "calls", "hll")
    assert before, "the fixture carries no HLL sketch to move"

    _migrate(engine)

    assert iris_obj.get("^KG", "tagg", 0, bucket, "n1", "calls", "hll") == before


def test_a_second_migration_reports_zero_and_changes_nothing(old_database):
    """Idempotent, and honest about it.

    A migration that reports 0 must mean "nothing left to move" — the earlier
    version returned 0 both when it was finished and when its walk stopped on the
    first subscript, which made the two indistinguishable.
    """
    release, engine, _iris_obj = old_database
    start, end = _window(release)

    first = _migrate(engine)
    after_first = engine.get_edges_in_window("", "", start, end)

    assert _migrate(engine) == 0
    assert first == len(release.seeded["temporal"])
    assert engine.get_edges_in_window("", "", start, end) == after_first


# ── what this migration deliberately does not touch ────────────────────────


def test_the_structural_adjacency_is_left_exactly_where_it_was(old_database):
    """A temporal migration moves temporal globals. Structural is spec-214's.

    Worth pinning because the two upgrades are independent and a consumer coming
    from a pre-214 release needs both: running only this one leaves their
    adjacency at coordinates the current readers do not use.
    """
    release, engine, iris_obj = old_database
    before = {
        tuple(entry["k"]): entry["v"] for entry in release.globals_under("^KG", "out")
    }
    assert before

    _migrate(engine)

    for key, value in before.items():
        subs = [int(s) if str(s).isdigit() else s for s in key]
        assert iris_obj.get("^KG", *subs) == value, key


def test_the_pre_214_degree_cache_stays_unscoped(old_database):
    """v2.16 wrote `^KG("deg", node)`; current readers look one subscript deeper.

    v2.20 already carries the graph key, so the same test states a different fact
    for each release: the migration changed neither.
    """
    release, engine, iris_obj = old_database
    depth = len(release.globals_under("^KG", "deg")[0]["k"])

    _migrate(engine)

    if release.tag == "v2.16.0":
        assert depth == 2
        assert iris_obj.get("^KG", "deg", "n1") is not None
        assert iris_obj.get("^KG", "deg", 0, "n1") is None
    else:
        assert depth == 3
        assert iris_obj.get("^KG", "deg", 0, "n1") is not None


def test_the_v216_shard_subscript_reads_as_the_default_graph(old_database):
    """The silent one: same depth, different meaning, and no migration for it.

    `^KG("out", 0, s, p, o)` is a graph key in v2.20 and a pre-214 *shard* in
    v2.16 — and v2.16's BuildKG ignored `graph_id`, so the named graph's edge sits
    under that `0` as well. A current reader scoped to the default graph therefore
    reports `acme` content as its own, with nothing anywhere to flag it.

    Nothing here fixes that; a structural migration would have to consult
    `rdf_edges` to know which graph each edge belongs to. It is asserted so the
    hazard is a known property of pre-214 upgrades rather than a surprise at a
    consumer site.
    """
    release, engine, iris_obj = old_database
    if release.tag != "v2.16.0":
        pytest.skip("the shard subscript is a pre-214 shape")

    _migrate(engine)

    named = release.seeded["named_edges"][0]
    assert (
        iris_obj.get("^KG", "out", 0, named["s"], named["p"], named["o_id"]) is not None
    ), "the acme edge is expected under the shard 0, which is what makes this silent"
    assert iris_obj.get("^KG", "out", named["graph_id"], named["s"]) is None
