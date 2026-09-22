"""A snapshot exports every store the inventory names, or says why it does not.

`save_snapshot` kept its own list of globals: `^KG("out")`, `^KG("in")`, and the
index globals. All five temporal subtrees were absent, so a snapshot of a database
with temporal edges restored a database with none — and reported success, because
nothing compared what it exported against what exists.

`Graph.KG.GraphStores.Inventory()` is the one declared inventory of stores holding
graph content (ADR-0004). The export plan here is the snapshot's answer for each of
its entries: exported, or derived and rebuilt on restore. An entry that is merely
*absent* from the plan is the defect this file exists to catch; the integration
companion (tests/integration/test_snapshot_inventory.py) is what keeps the plan
equal to the inventory.

The layout stamp is the second half. A snapshot taken before spec-223 holds flat
temporal globals, and restoring those into a graph-scoped reader loses every
temporal edge silently — the restore has to refuse rather than succeed emptily.
"""

from __future__ import annotations

import json
import zipfile
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine import snapshot as snapshot_mod

# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def test_every_temporal_store_is_in_the_plan():
    """The five subtrees that were missing, named one by one."""
    for store in ("tout", "tin", "bucket", "tagg", "edgeprop"):
        key = f'^KG("{store}")'
        assert key in snapshot_mod.STORE_PLAN, (
            f"{key} is not in the export plan, so a snapshot of a database with "
            "temporal edges restores a database with none"
        )
        assert snapshot_mod.STORE_PLAN[key] == "global"


def test_the_edge_embeddings_table_is_in_the_plan():
    assert snapshot_mod.STORE_PLAN["Graph_KG.kg_EdgeEmbeddings"] == "sql"


def test_the_embedding_registry_is_in_the_plan():
    """The registry is the only record of what a routed table holds (spec 227).

    Without it a snapshot of an install with routed embeddings comes back with the
    vectors' physical tables unnamed and unclaimed: `resolve_route` finds no row,
    every scoped search reports a miss, and the restore reports success.
    """
    assert snapshot_mod.STORE_PLAN["Graph_KG.embedding_registry"] == "sql"


def test_a_routed_table_is_planned_from_its_registry_row():
    plan = snapshot_mod.routed_export_plan(
        [("kg_emb_0123456789abcdef", "acme", "m1", 8, "DOUBLE")]
    )

    assert plan == {
        "Graph_KG.kg_emb_0123456789abcdef": {
            "graph_id": "acme",
            "model_key": "m1",
            "dimension": 8,
            "dtype": "DOUBLE",
        }
    }


def test_a_legacy_table_the_registry_claims_is_not_planned_as_a_route():
    """`kg_NodeEmbeddings` has a registry row of its own and is exported by name.

    Planning it here too would export the same rows twice and, worse, hand the
    restore a `CREATE TABLE` for a table the schema owns.
    """
    plan = snapshot_mod.routed_export_plan(
        [("kg_NodeEmbeddings", "", "m1", 8, "DOUBLE")]
    )

    assert plan == {}


def test_a_name_that_is_not_the_routed_shape_is_refused():
    """A registry row's table name becomes DDL on restore, so the shape is checked.

    `routing.route_table_name` produces `kg_emb_<16 hex>` and nothing else; anything
    else in that column is either damage or someone else's table.
    """
    plan = snapshot_mod.routed_export_plan(
        [
            ("kg_emb_nothex", "acme", "m", 8, "DOUBLE"),
            ("kg_emb_0123456789abcdef; DROP TABLE Graph_KG.nodes", "acme", "m", 8, "DOUBLE"),
        ]
    )

    assert plan == {}


def test_a_route_with_no_declared_width_is_refused():
    """A width is what makes the `CREATE TABLE` on restore possible at all."""
    plan = snapshot_mod.routed_export_plan(
        [("kg_emb_0123456789abcdef", "acme", "m", None, "DOUBLE")]
    )

    assert plan == {}


def test_the_restore_reads_the_registry_back():
    """Exporting the registry without restoring it loses the routes just as completely.

    The restore's table order is a module constant precisely so this can be asserted:
    it was a local list inside `restore_snapshot`, which is how a table can be in the
    export and absent from the import with nothing to compare the two against.
    """
    assert "Graph_KG_embedding_registry.ndjson" in snapshot_mod.RESTORE_TABLE_ORDER


def test_the_registry_is_restored_after_the_nodes_it_describes():
    """Order matters for the FK: a routed table references `nodes (graph_id, node_id)`.

    The registry row itself has no FK, but the routed tables built from it do, so the
    nodes have to be in before the rebuild reads the registry.
    """
    order = snapshot_mod.RESTORE_TABLE_ORDER
    assert order.index("Graph_KG_nodes.ndjson") < order.index(
        "Graph_KG_embedding_registry.ndjson"
    )


def test_every_restored_table_is_a_planned_sql_store():
    """A file the restore loads that the export never writes is a dead branch."""
    unplanned = [
        fname
        for fname in snapshot_mod.RESTORE_TABLE_ORDER
        if snapshot_mod.STORE_PLAN.get(
            fname.replace("Graph_KG_", "Graph_KG.").replace(".ndjson", "")
        )
        != "sql"
    ]
    assert not unplanned, f"the restore loads tables the plan does not name: {unplanned}"


def test_the_plan_accounts_for_every_entry_it_holds():
    """No entry sits in the plan without a decision attached to it."""
    unknown = {
        store: disposition
        for store, disposition in snapshot_mod.STORE_PLAN.items()
        if disposition not in ("sql", "global", "derived")
    }
    assert not unknown, f"these plan entries name no disposition: {unknown}"


def test_the_globals_export_is_derived_from_the_plan():
    """One declaration, not two lists that drift apart.

    The `^KG` subtrees the exporter walks have to *be* the plan's `^KG` entries; a
    hand-maintained second list is how the temporal subtrees came to be missing from
    one and present in the other.
    """
    planned = {
        store[len('^KG("') : -len('")')]
        for store, disposition in snapshot_mod.STORE_PLAN.items()
        if store.startswith('^KG("') and disposition == "global"
    }

    exported = {subs[0] for subs in snapshot_mod.kg_export_subscripts() if subs}

    assert exported == planned, (
        "the ^KG subtrees the snapshot walks and the ones the plan names have "
        f"drifted apart: exported {sorted(exported)}, planned {sorted(planned)}"
    )


def test_a_derived_store_says_why_it_is_not_exported():
    """`^ArnoKG` is rebuilt from `^KG`, and the plan has to say so, not omit it."""
    assert snapshot_mod.STORE_PLAN["^ArnoKG"] == "derived"
    assert "^ArnoKG" in snapshot_mod.DERIVED_REASONS
    assert snapshot_mod.DERIVED_REASONS["^ArnoKG"].strip()


def test_every_derived_store_carries_a_reason():
    missing = [
        store
        for store, disposition in snapshot_mod.STORE_PLAN.items()
        if disposition == "derived" and not snapshot_mod.DERIVED_REASONS.get(store, "").strip()
    ]
    assert not missing, (
        "these stores are excluded from the snapshot with no reason recorded, which "
        f"is indistinguishable from having been forgotten: {missing}"
    )


# ---------------------------------------------------------------------------
# The layout stamp
# ---------------------------------------------------------------------------


def _snapshot_metadata(tmp_path, engine) -> dict:
    path = str(tmp_path / "s.zip")
    engine.save_snapshot(path, layers=[])
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("metadata.json"))


class _Engine(snapshot_mod.SnapshotMixin):
    """The mixin over a mocked connection: layers=[] touches neither SQL nor globals."""

    def __init__(self):
        self.conn = MagicMock()
        self.embedding_dimension = 4

    def _iris_obj(self):
        raise AssertionError("layers=[] must not reach the Native API")

    def _t(self, table):
        return f"Graph_KG.{table}"


def test_the_snapshot_stamps_its_layout(tmp_path):
    metadata = _snapshot_metadata(tmp_path, _Engine())

    assert metadata["layout"] == snapshot_mod.SNAPSHOT_LAYOUT
    assert metadata["layout"] == "graph-scoped"


def test_a_restore_refuses_a_snapshot_from_another_layout(tmp_path):
    path = str(tmp_path / "flat.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "metadata.json",
            json.dumps({"version": "1.1", "layout": "flat", "layers": [], "tables": {}}),
        )

    engine = _Engine()
    with pytest.raises(ValueError, match="flat"):
        engine.restore_snapshot(path)


def _collate(sub: str):
    """IRIS subscript collation: canonical numbers ascending, then strings."""
    try:
        return (0, float(sub), "")
    except ValueError:
        return (1, 0.0, sub)


class _FakeGlobal:
    """A subscripted global with the two calls the exporter makes on it.

    `nextSubscript(False, name, *path, current)` returns the next sibling of
    `current` at that level, or "" when there is none — the Native API's spelling of
    `$Order`. Numbers collate before strings, so the default graph's key `0` is the
    *first* sibling of every graph-scoped level.
    """

    def __init__(self, name: str, data: dict):
        self.name = name
        self.data = {tuple(str(s) for s in k): v for k, v in data.items()}

    def _siblings(self, parent: tuple) -> list:
        n = len(parent)
        return sorted(
            {k[n] for k in self.data if len(k) > n and k[:n] == parent}, key=_collate
        )

    def nextSubscript(self, _reverse, name, *subs):
        assert name == self.name
        parent = tuple(str(s) for s in subs[:-1])
        current = subs[-1]
        for sib in self._siblings(parent):
            if current == "" or _collate(sib) > _collate(str(current)):
                return sib
        return ""

    def get(self, name, *subs):
        assert name == self.name
        return self.data.get(tuple(str(s) for s in subs))


def _exported_keys(data: dict, prefix_subs: list) -> list:
    engine = _Engine()
    lines = engine._export_global_to_ndjson(_FakeGlobal("^KG", data), "^KG", prefix_subs)
    return [json.loads(line)["k"] for line in lines]


def test_the_export_reaches_the_default_graph(tmp_path):
    """The default graph's key is the integer `0` (ADR-0001, ADR-0003).

    The walk seeded itself with `0` and asked for the *next* subscript, so the one
    subscript it could never return was `0` itself — every default-graph edge was
    skipped by a snapshot that reported success.
    """
    keys = _exported_keys(
        {
            ("out", "0", "a", "KNOWS", "b"): "1",
            ("out", "acme", "a", "KNOWS", "b"): "1",
        },
        ["out"],
    )

    assert ["out", "0", "a", "KNOWS", "b"] in keys, (
        f"the default graph was skipped by the export; it emitted {keys}"
    )
    assert ["out", "acme", "a", "KNOWS", "b"] in keys


def test_the_export_reaches_a_zero_timestamp():
    """The same skip one level deeper: `0` is a legal subscript everywhere.

    A timestamp of `0` is the epoch, which real ingest of undated data produces.
    """
    keys = _exported_keys({("tout", "0", "0", "a", "SAW", "b"): "1"}, ["tout"])

    assert ["tout", "0", "0", "a", "SAW", "b"] in keys, keys


def test_the_export_emits_a_value_carrying_interior_node():
    """A node with both a value and children has to appear once, not be skipped."""
    keys = _exported_keys({("deg", "0", "a"): "3", ("deg", "0", "a", "x"): "1"}, ["deg"])

    assert ["deg", "0", "a"] in keys
    assert ["deg", "0", "a", "x"] in keys


def test_a_restore_accepts_a_snapshot_taken_before_the_stamp(tmp_path):
    """Pre-stamp snapshots hold no temporal globals, so there is nothing to lose.

    Refusing them would break every archive taken before this change for the sake of
    a mismatch that cannot bite.
    """
    path = str(tmp_path / "old.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"version": "1.1", "layers": [], "tables": {}}))

    engine = _Engine()
    engine.erase_all = MagicMock(return_value={})
    result = engine.restore_snapshot(path)

    assert isinstance(result, dict)
