"""Snapshots stop carrying the degree counters, and a restore leaves no stale cache (spec 249).

`^KG("deg")` and `^KG("degp")` are no longer stores. `^KG("gen")` and `^KG("degc")` (the generation and the cache behind
`Graph.KG.Adj.Degree`) are derived: never exported, and cleared for every graph a restore wrote, because the restore sets
`out`/`in` entries without bumping a generation and a cache entry stamped with the old generation would otherwise vouch
for the old count.

Old archives (3.2.0 wrote `deg`/`degp` into its `globals/KG.ndjson`) must still restore: their counter lines are ignored.
"""

from __future__ import annotations

import json
import zipfile
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine import snapshot as snapshot_mod

# ---------------------------------------------------------------------------- the plan


def test_the_plan_no_longer_holds_the_counters_as_stores():
    assert '^KG("deg")' not in snapshot_mod.STORE_PLAN
    assert '^KG("degp")' not in snapshot_mod.STORE_PLAN


@pytest.mark.parametrize("store", ['^KG("gen")', '^KG("degc")'])
def test_the_generation_and_the_cache_are_derived_with_a_reason(store):
    assert snapshot_mod.STORE_PLAN[store] == "derived"
    assert snapshot_mod.DERIVED_REASONS[store].strip()


def test_the_export_walks_none_of_the_four():
    exported = {subs[0] for subs in snapshot_mod.kg_export_subscripts() if subs}

    assert not exported & {"deg", "degp", "gen", "degc"}
    assert {"out", "in"} <= exported


# ---------------------------------------------------------------------------- the import


class _Native:
    """The Native API calls the restore makes on `^KG`, recording writes and kills in order."""

    def __init__(self):
        self.ops = []

    def set(self, value, name, *subs):
        self.ops.append(("set", name) + tuple(subs))

    def kill(self, name, *subs):
        self.ops.append(("kill", name) + tuple(subs))

    def get(self, name, *subs):
        return None

    def classMethodVoid(self, *args):
        return None

    def classMethodValue(self, *args):
        return 0


def _ndjson(*entries):
    return "\n".join(json.dumps({"k": list(k), "v": v}) for k, v in entries)


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    conn.cursor.return_value.fetchone.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=4)


def test_deg_and_degp_lines_are_not_written_or_counted():
    engine = _engine()
    native = _Native()
    text = _ndjson(
        (("out", 0, "a", "R", "b"), "1"),
        (("deg", 0, "a"), "1"),
        (("degp", 0, "a", "R"), "1"),
        (("in", 0, "b", "R", "a"), "1"),
    )

    count = engine._import_global_from_ndjson(native, "^KG", text)

    assert count == 2
    written = [op for op in native.ops if op[0] == "set"]
    assert written == [("set", "^KG", "out", 0, "a", "R", "b"), ("set", "^KG", "in", 0, "b", "R", "a")]


@pytest.mark.parametrize("derived", ["gen", "degc"])
def test_a_hand_made_archive_cannot_plant_the_derived_stores(derived):
    engine = _engine()
    native = _Native()

    count = engine._import_global_from_ndjson(native, "^KG", _ndjson(((derived, 0, "a"), "9")))

    assert count == 0
    assert not [op for op in native.ops if op[0] == "set"]


def test_the_cache_and_generation_are_cleared_for_the_graphs_written_after_every_write():
    engine = _engine()
    native = _Native()
    text = _ndjson(
        (("out", 0, "a", "R", "b"), "1"),
        (("in", 0, "b", "R", "a"), "1"),
        (("out", "acme", "x", "R", "y"), "1"),
        (("in", "acme", "y", "R", "x"), "1"),
        (("out", 0, "a", "R", "c"), "1"),
    )

    engine._import_global_from_ndjson(native, "^KG", text)

    kills = [op for op in native.ops if op[0] == "kill"]
    assert sorted(kills, key=str) == sorted(
        [
            ("kill", "^KG", "degw", 0),
            ("kill", "^KG", "degc", 0),
            ("kill", "^KG", "gen", 0),
            ("kill", "^KG", "degw", "acme"),
            ("kill", "^KG", "degc", "acme"),
            ("kill", "^KG", "gen", "acme"),
        ],
        key=str,
    )
    last_set = max(i for i, op in enumerate(native.ops) if op[0] == "set")
    first_kill = min(i for i, op in enumerate(native.ops) if op[0] == "kill")
    assert first_kill > last_set, "a kill before the last write leaves a window where the old cache validates new rows"
    for graph in (0, "acme"):
        # the whole-graph stamp first, then the cache, then the generation
        assert native.ops.index(("kill", "^KG", "degw", graph)) < native.ops.index(("kill", "^KG", "degc", graph))
        assert native.ops.index(("kill", "^KG", "degc", graph)) < native.ops.index(("kill", "^KG", "gen", graph))


def test_nothing_is_cleared_when_no_adjacency_was_written():
    engine = _engine()
    native = _Native()

    engine._import_global_from_ndjson(native, "^KG", _ndjson((("label", 0, "Person", "a"), "1")))

    assert not [op for op in native.ops if op[0] == "kill"]


def test_another_global_is_imported_untouched():
    engine = _engine()
    native = _Native()

    count = engine._import_global_from_ndjson(native, "^NKG", _ndjson((("deg", 1), "5"), (("out", 1, 2), "1")))

    assert count == 2
    assert not [op for op in native.ops if op[0] == "kill"]


# ---------------------------------------------------------------------------- the whole restore


def _zip_with_globals(tmp_path, kg_text):
    path = tmp_path / "snap.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"tables": {}, "globals": {}, "has_vector_sql": False}))
        zf.writestr("globals/KG.ndjson", kg_text)
    return str(path)


def test_restore_snapshot_ignores_an_old_archives_counters_and_clears_the_cache(tmp_path):
    engine = _engine()
    native = _Native()
    engine._iris_obj = MagicMock(return_value=native)
    path = _zip_with_globals(
        tmp_path,
        _ndjson(
            (("out", 0, "a", "R", "b"), "1"),
            (("deg", 0, "a"), "1"),
            (("degp", 0, "a", "R"), "1"),
        ),
    )

    result = engine.restore_snapshot(path, merge=True)

    assert result["restored_globals"] == ["KG"]
    sets = [op for op in native.ops if op[0] == "set" and op[1] == "^KG"]
    assert ("set", "^KG", "out", 0, "a", "R", "b") in sets
    assert not [op for op in sets if op[2] in ("deg", "degp")]
    assert ("kill", "^KG", "degc", 0) in native.ops and ("kill", "^KG", "gen", 0) in native.ops


def _old_release_archives():
    from tests.e2e.fixtures.old_releases import OLD_RELEASES

    return OLD_RELEASES


@pytest.mark.parametrize("release", _old_release_archives(), ids=lambda r: r.tag)
def test_every_frozen_old_release_archive_still_restores_without_its_counters(release):
    engine = _engine()
    native = _Native()
    engine._iris_obj = MagicMock(return_value=native)

    result = engine.restore_snapshot(str(release.zip_path), merge=True)

    assert "KG" in result["restored_globals"]
    planted = [op for op in native.ops if op[0] == "set" and op[1] == "^KG" and op[2] in ("deg", "degp", "gen", "degc")]
    assert planted == []
    held_adjacency = [e for e in release.archive_global_nodes("^KG") if e["k"][0] in ("out", "in")]
    written_adjacency = [op for op in native.ops if op[0] == "set" and op[1] == "^KG" and op[2] in ("out", "in")]
    assert len(written_adjacency) == len(held_adjacency)
    if any(e["k"][0] in ("deg", "degp") for e in release.archive_global_nodes("^KG")):
        assert any(op[0] == "kill" for op in native.ops), "the archive wrote adjacency, so its graphs' cache is cleared"
