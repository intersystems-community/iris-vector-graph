"""The frozen old-release fixtures are present and mean what the tests assume.

The upgrade tests are only as good as these artifacts. Two ways they rot without
anyone noticing: a file goes missing and the parametrized suite quietly collects
zero cases, or a regeneration changes the seed and every assertion downstream
starts agreeing with the new fixture instead of testing anything.

So this pins the properties the upgrade tests lean on, without a container:

  * both releases are present, and each has all three files;
  * the archives carry the old *column* shapes, including v2.16's missing
    `nodes.graph_id` and its NULL default-graph `rdf_edges.graph_id`;
  * the captured globals carry the old *subscript* shapes, including v2.16's
    unscoped `^KG("deg", node)` and both releases' flat `^KG("tout", ts, ...)`.

Regenerate with `scripts/fixtures/generate_old_snapshot.py`; see
`tests/e2e/fixtures/old_releases.py` for how the pieces fit together.
"""

from __future__ import annotations

import pytest

from tests.e2e.fixtures.old_releases import OLD_RELEASES, RELEASE_IDS

# Both halves of the upgrade story need a release from either side of spec-214
# (graph-scoped structural globals): v2.16 predates it, v2.20 follows it and
# predates spec-223 (graph-scoped temporal globals).
EXPECTED_TAGS = {"v2.16.0", "v2.20.0"}


def test_both_frozen_releases_are_present():
    """A missing fixture collects zero parametrized cases and passes silently."""
    assert {r.tag for r in OLD_RELEASES} == EXPECTED_TAGS


@pytest.mark.parametrize("release", OLD_RELEASES, ids=RELEASE_IDS)
def test_every_release_has_all_three_files(release):
    assert release.zip_path.exists(), release.zip_path
    assert release.globals_path.exists(), release.globals_path
    assert release.seeded["nodes"], "the manifest records no nodes"


@pytest.mark.parametrize("release", OLD_RELEASES, ids=RELEASE_IDS)
def test_the_seed_is_the_same_across_releases(release):
    """The two archives differ in shape only. Differing content confounds that."""
    assert [n["node_id"] for n in release.seeded["nodes"]] == [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]
    assert len(release.seeded["edges"]) == 3
    assert len(release.seeded["named_edges"]) == 1
    assert len(release.seeded["temporal"]) == 3
    assert len(release.seeded["embeddings"]) == 2
    assert release.named_graph == "acme"


@pytest.mark.parametrize("release", OLD_RELEASES, ids=RELEASE_IDS)
def test_no_release_declares_a_storage_layout(release):
    """`restore_snapshot` treats a missing `layout` key as restorable.

    That allowance is what lets these archives through at all, and it is reasoned
    about temporal globals only — see the pre-214 structural shapes below for why
    that reasoning is narrower than the archives are.
    """
    assert "layout" not in release.archive_metadata()


@pytest.mark.parametrize("release", OLD_RELEASES, ids=RELEASE_IDS)
def test_every_archive_carries_embedding_metadata(release):
    """The column the restore reads into a variable and never writes."""
    rows = release.archive_rows("Graph_KG.kg_NodeEmbeddings")

    assert len(rows) == 2
    for row in rows:
        assert row["metadata"], f"{release.tag} archive lost the metadata column"
        assert "fixture" in row["metadata"]


def _by_tag(tag):
    return next(r for r in OLD_RELEASES if r.tag == tag)


def test_v216_wrote_null_for_the_default_graph():
    """The NULL spelling is not hypothetical: a shipped release wrote it.

    `rdf_edges.graph_id` is still nullable, and v2.16 left it NULL for every
    default-graph edge. Any read predicate spelled `graph_id = ''` alone is false
    for these rows, because in SQL `NULL = ''` is unknown rather than true. This
    is the row shape the COALESCE guards across the writers exist for.
    """
    edges = _by_tag("v2.16.0").archive_rows("Graph_KG.rdf_edges")

    default_graph = [e for e in edges if e["s"] != "n5"]
    assert default_graph, "no default-graph edges in the v2.16 archive"
    assert all(e["graph_id"] is None for e in default_graph)
    assert [e["graph_id"] for e in edges if e["s"] == "n5"] == ["acme"]


def test_v220_wrote_the_empty_string_for_the_default_graph():
    """ADR-0003's `ForName()` spelling, as the later release stored it."""
    edges = _by_tag("v2.20.0").archive_rows("Graph_KG.rdf_edges")

    assert [e["graph_id"] for e in edges if e["s"] != "n5"] == ["", "", ""]


def test_v216_nodes_have_no_graph_id_column_at_all():
    """Pre-214. The restore has to let the current NOT NULL DEFAULT '' supply it."""
    rows = _by_tag("v2.16.0").archive_rows("Graph_KG.nodes")

    assert rows
    assert all("graph_id" not in row for row in rows)


def test_v220_nodes_carry_a_graph_id_column():
    rows = _by_tag("v2.20.0").archive_rows("Graph_KG.nodes")

    assert rows
    assert all("graph_id" in row for row in rows)


@pytest.mark.parametrize("release", OLD_RELEASES, ids=RELEASE_IDS)
def test_the_captured_temporal_index_is_flat(release):
    """Both releases predate spec-223: `tout` is keyed by timestamp, not by graph.

    Depth is the tell. A flat entry is `("tout", ts, s, p, o)` — five subscripts.
    A graph-scoped one is `("tout", gkey, ts, s, p, o)` — six.
    """
    tout = release.globals_under("^KG", "tout")

    assert len(tout) == 3
    for entry in tout:
        assert len(entry["k"]) == 5, entry
        assert entry["k"][1].isdigit() and int(entry["k"][1]) > 1_000_000, entry


def test_v216_degree_caches_have_no_graph_key():
    """Pre-214 `^KG("deg", node)`. Current readers look one subscript deeper."""
    deg = _by_tag("v2.16.0").globals_under("^KG", "deg")

    assert len(deg) == 4
    assert all(len(entry["k"]) == 2 for entry in deg), deg
    assert {entry["k"][1] for entry in deg} == {"n1", "n2", "n3", "n5"}


def test_v220_degree_caches_carry_a_graph_key():
    deg = _by_tag("v2.20.0").globals_under("^KG", "deg")

    assert all(len(entry["k"]) == 3 for entry in deg), deg
    assert {entry["k"][1] for entry in deg} == {"0", "acme"}


def test_a_captured_numeric_subscript_goes_back_as_a_number():
    """The in-place half loads these globals verbatim, and collation depends on it.

    IRIS collates numeric subscripts before strings. Integer `0` keys the default
    graph and sorts before every timestamp; the string `"0"` sorts among the
    strings. Writing a captured `"1700000000"` back as text would put the temporal
    tree where no `$Order` walk over numbers reaches it, and the migration would
    report 0 having found nothing to move.
    """
    from tests.e2e.fixtures.old_releases import _canonical

    assert _canonical("0") == 0
    assert _canonical("1700000000") == 1700000000
    assert _canonical(0) == 0
    assert _canonical("n1") == "n1"
    assert _canonical("acme") == "acme"
    # Not canonical as a number, so it stays the string IRIS would hold.
    assert _canonical("007") == "007"


def test_the_zero_under_out_means_different_things_in_the_two_releases():
    """The nastiest shape collision in the whole upgrade, and it is silent.

    `^KG("out", 0, s, p, o)` has the same depth in both releases. In v2.20 that
    `0` is the default graph key (ADR-0001). In v2.16 it is a pre-214 shard
    subscript, and v2.16's BuildKG ignored `graph_id` entirely — so the *named*
    graph's edge sits under it too. Read as a graph key, a v2.16 tree silently
    reports `acme` content as default-graph content.
    """
    v216 = _by_tag("v2.16.0").globals_under("^KG", "out", 0)
    v220 = _by_tag("v2.20.0").globals_under("^KG", "out", 0)

    assert {e["k"][2] for e in v216} == {"n1", "n2", "n3", "n5"}
    assert {e["k"][2] for e in v220} == {"n1", "n2", "n3"}
    assert _by_tag("v2.20.0").globals_under("^KG", "out", "acme")
    assert not _by_tag("v2.16.0").globals_under("^KG", "out", "acme")
