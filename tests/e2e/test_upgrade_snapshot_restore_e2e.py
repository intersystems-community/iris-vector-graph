"""Restoring a real old archive onto a current install — the portability half.

This is the upgrade runbook most consumers will follow: snapshot on the version
they are running, install the new version, restore. Every archive here was
written by the release it is named for; see `tests/e2e/fixtures/old_releases.py`.

What the half does and does not cover is worth being exact about, because it is
easy to mistake for a migration test. `restore_snapshot` writes into the
*current* schema — it inserts old rows into today's tables and imports old global
nodes at today's coordinates. No migration runs. So this half answers "does old
content survive a shape change", and the in-place half
(`test_upgrade_migrate_globals_e2e.py`) answers "does old *layout* get moved".

Three consumer-visible losses are asserted here rather than fixed, because they
are properties of archives already in the field and no future code can put the
data back:

  1. Neither release's `save_snapshot` exports the temporal globals at all, so a
     snapshot-upgrade-restore runbook silently discards the entire temporal
     index.
  2. Both releases' global walkers seed `$Order` with `0`, and `0` collates after
     `""` — so v2.20, which keys the default graph with the integer `0`, cannot
     export its own default-graph adjacency.
  3. v2.16 stored NULL in `rdf_edges.graph_id` for default-graph edges. The
     column is still nullable, so those NULLs restore verbatim and stay.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/e2e/test_upgrade_snapshot_restore_e2e.py
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


@pytest.fixture(params=OLD_RELEASES, ids=RELEASE_IDS)
def restored(request, iris_connection, iris_master_cleanup):
    """A current-version engine holding one old release's restored archive.

    The dimension comes from the release, not from the test: restoring an
    archive into an engine configured for a different width would fail inside
    `TO_VECTOR` and the loss would look like a snapshot defect.
    """
    release = request.param
    engine = IRISGraphEngine(
        iris_connection, embedding_dimension=release.embedding_dimension
    )
    engine.initialize_schema()
    result = engine.restore_snapshot(str(release.zip_path))
    return release, engine, result


def _fetchall(engine, sql, params=None):
    cursor = engine.conn.cursor()
    cursor.execute(sql, params or [])
    return cursor.fetchall()


# ── the content that must survive ──────────────────────────────────────────


def test_an_old_archive_restores_at_all(restored):
    """The `layout` allowance is load-bearing: no `layout` key means restorable."""
    release, _engine, result = restored

    restored = result["restored_tables"]

    assert restored["Graph_KG.nodes"] == len(release.seeded["nodes"])
    assert restored["Graph_KG.rdf_edges"] == len(release.seeded["edges"]) + len(
        release.seeded["named_edges"]
    )


def test_every_seeded_node_comes_back(restored):
    release, engine, _result = restored

    for node in release.seeded["nodes"]:
        got = engine.get_node(node["node_id"])
        assert got is not None, f"{node['node_id']} did not survive the restore"
        assert got["id"] == node["node_id"]
        assert got["labels"] == node["labels"]


def test_every_seeded_node_property_comes_back(restored):
    """`get_nodes` flattens properties onto the node dict alongside id/labels."""
    release, engine, _result = restored

    for node in release.seeded["nodes"]:
        got = engine.get_node(node["node_id"])
        for key, value in node["properties"].items():
            assert got.get(key) == value, (node["node_id"], key)


def test_restored_nodes_carry_the_current_graph_id_spelling(restored):
    """v2.16's archive has no `nodes.graph_id` column; the default must supply it.

    `Graph_KG.nodes.graph_id` is NOT NULL DEFAULT '' on every current code path,
    so an archive that never mentions the column still lands with ADR-0003's
    `ForName()` spelling — not NULL, and not absent.
    """
    _release, engine, _result = restored

    rows = _fetchall(engine, "SELECT node_id, graph_id FROM Graph_KG.nodes")

    assert rows
    for node_id, graph_id in rows:
        assert graph_id == "", f"{node_id} restored with graph_id {graph_id!r}"


def test_the_named_graph_survives_on_its_edge(restored):
    """The one piece of graph identity both releases stored the same way."""
    release, engine, _result = restored

    rows = _fetchall(
        engine,
        "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?",
        [release.named_graph],
    )

    assert [tuple(r) for r in rows] == [
        (e["s"], e["p"], e["o_id"]) for e in release.seeded["named_edges"]
    ]


def test_the_embedding_vectors_come_back(restored):
    release, engine, _result = restored

    for item in release.seeded["embeddings"]:
        got = engine.get_embedding(item["node_id"])
        assert got is not None, f"{item['node_id']} lost its embedding"
        assert len(got["embedding"]) == release.embedding_dimension
        assert got["embedding"] == pytest.approx(item["embedding"], abs=1e-6)


def test_the_embedding_metadata_comes_back(restored):
    """The archive carries the column; the restore has to write it.

    `save_snapshot` exports `kg_NodeEmbeddings.metadata`, and both frozen
    archives hold it (see tests/unit/test_old_release_fixtures.py). The restore
    read it into `meta_val` and inserted only `(id, emb)`, so every consumer who
    upgraded by snapshot-and-restore lost their embedding provenance silently —
    the vectors came back, so nothing looked wrong.
    """
    release, engine, _result = restored

    for item in release.seeded["embeddings"]:
        got = engine.get_embedding(item["node_id"])
        assert got.get("metadata") == item["metadata"], (
            f"{item['node_id']}: the archive carries this metadata but the "
            "restore dropped it"
        )


# ── the losses, asserted so they are documented rather than discovered ─────


def test_the_archive_carries_no_temporal_index(restored):
    """Neither release lists the temporal globals in its own export.

    `GLOBALS_EXPORT` names `^KG("out")` and `^KG("in")` and stops. Every
    `^KG("tout")`, `("tin")`, `("bucket")` and `("tagg")` node is therefore
    absent from the archive, and `metadata["globals"]` simply does not mention
    them — so nothing warns the consumer.
    """
    release, _engine, _result = restored

    assert release.seeded["temporal"], "the fixture seeded no temporal edges"
    exported = {entry["k"][0] for entry in release.archive_global_nodes("^KG")}

    assert exported <= {"out", "in"}, exported
    assert "tout" not in exported


def test_a_restored_database_has_lost_its_temporal_edges(restored):
    """The consequence of the above, at the interface a consumer would use."""
    release, engine, _result = restored

    stamps = [e["timestamp"] for e in release.seeded["temporal"]]
    found = engine.get_edges_in_window("", "", min(stamps) - 1, max(stamps) + 1)

    assert found == [], (
        "the temporal index is expected to be empty after restoring one of these "
        f"archives, because neither exports it — got {found}"
    )


def test_the_archive_cannot_carry_the_default_graph_adjacency(restored):
    """The `$Order` seed, and why `""` is the only correct start value.

    Both releases walk their globals from a seed of `0`, which asks for the
    subscript *after* `0` and so can never emit `0` itself. In v2.20 the default
    graph key *is* the integer `0` (ADR-0001), so its archive holds only named
    graphs. v2.16 predates graph keys — its `0` is a shard subscript and every
    edge sits under it — so its archive is unaffected by the same bug.
    """
    release, _engine, _result = restored

    # v2.16's exporter recorded a numeric subscript as a JSON number and v2.20's
    # as a string, so the comparison is on the coordinate, not on its JSON type.
    keys = {str(entry["k"][1]) for entry in release.archive_global_nodes("^KG")}

    if release.tag == "v2.20.0":
        assert keys == {"acme"}, (
            "v2.20 is expected to export named graphs only; a `0` here means the "
            f"walker was fixed before this archive was written: {keys}"
        )
    else:
        assert "0" in keys


def test_sync_rebuilds_what_the_archive_could_not_carry(restored):
    """The recovery, and the reason this loss is survivable where temporal is not.

    Structural adjacency is derived from `rdf_edges`, which restored in full, so
    `sync()` puts `^KG("out", 0, ...)` back. The temporal index has no relational
    source to rebuild from, which is what makes its omission permanent.
    """
    release, engine, _result = restored

    engine.sync()
    iris_obj = engine._iris_obj()

    for edge in release.seeded["edges"]:
        val = iris_obj.get("^KG", "out", 0, edge["s"], edge["p"], edge["o_id"])
        assert val is not None, f"sync() did not rebuild {edge} in the default graph"


# ── the NULL spelling a shipped release wrote, and what restore owes it ────


@pytest.fixture()
def restored_v216(iris_connection, iris_master_cleanup):
    release = next(r for r in OLD_RELEASES if r.tag == "v2.16.0")
    engine = IRISGraphEngine(
        iris_connection, embedding_dimension=release.embedding_dimension
    )
    engine.initialize_schema()
    result = engine.restore_snapshot(str(release.zip_path))
    return release, engine, result


def test_v216_default_graph_edges_restore_at_all(restored_v216):
    """v2.16 wrote NULL for the default graph; the live column forbids NULL.

    `Graph.KG.Edge` declares `graph_id` Required, so inserting an archive row
    verbatim raises SQLCODE -108 — and the restore's per-row `except` logged it
    at debug level and moved on. The observable result was a database holding the
    one named-graph edge and none of the three default-graph edges, with no error
    raised and nothing above debug in the log.

    ADR-0003 fixes the default graph's SQL spelling as `''`. A pre-tightening
    archive spelling it NULL means the same graph, so the restore normalises it
    rather than dropping the row.
    """
    release, engine, _result = restored_v216

    rows = _fetchall(engine, "SELECT s, p, o_id, graph_id FROM Graph_KG.rdf_edges")

    assert len(rows) == len(release.seeded["edges"]) + len(
        release.seeded["named_edges"]
    ), f"restored {len(rows)} of 4 edges: {rows}"


def test_v216_default_graph_edges_land_on_the_canonical_spelling(restored_v216):
    """Normalised to `''` — not left NULL, and not moved to another graph."""
    release, engine, _result = restored_v216

    rows = _fetchall(
        engine,
        "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = '' ORDER BY s",
    )

    assert [tuple(r) for r in rows] == [
        (e["s"], e["p"], e["o_id"]) for e in release.seeded["edges"]
    ]


def test_a_restore_reports_rows_it_could_not_insert(restored_v216):
    """A count a consumer can check, instead of a debug line nobody reads.

    The restore keeps inserting after a row fails, which is the right call — one
    bad row should not abandon an upgrade. What was missing is any way to find
    out, so the count of what did not land is reported alongside what did.
    """
    _release, _engine, result = restored_v216

    assert "failed_rows" in result, (
        "restore_snapshot reports only successes, so a silently dropped row is "
        "indistinguishable from an archive that never held it"
    )
    assert result["failed_rows"] == {}
