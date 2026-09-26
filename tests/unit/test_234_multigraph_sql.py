"""Spec 234 Phase 2 — the storage model's static half (T005, plus the migration order).

What is checked here without IRIS:

- ``RDF_EDGES_DDL`` carries ``ekey`` and the widened unique key, and no longer the
  triple-only one;
- ``GraphSchema.ensure_ekey`` runs its five steps in the order research R8 settled,
  so there is always a unique key in place, and does nothing on a table already at
  234;
- schema setup stops re-adding ``u_spo_graph`` once the table is at 234 — the old
  migration re-adds it on every run, which would silently put the triple-only key
  back after the swap;
- ``^IVG.GraphMode`` is in every inventory that has to name it;
- the mode API exists on the engine and on ``engine.graph``, and caches per engine;
- ``WriteAdjacency`` and ``DeleteAdjacency`` are byte-for-byte what they were: the
  keyed pair is added beside them, never threaded through them.

The live transitions are in ``tests/e2e/test_234_multigraph_storage_e2e.py``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph import schema as schema_mod
from iris_vector_graph.schema import RDF_EDGES_DDL, GraphSchema

ROOT = Path(__file__).resolve().parents[2]
CLS = ROOT / "iris_src" / "src" / "Graph" / "KG"


# --- T005: the DDL ------------------------------------------------------------------


def test_the_edge_table_declares_ekey():
    assert re.search(
        r"\bekey\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+0\b", RDF_EDGES_DDL
    ), RDF_EDGES_DDL


def test_the_unique_key_includes_ekey():
    assert "CONSTRAINT u_spo_graph_ekey UNIQUE (s, p, o_id, graph_id, ekey)" in RDF_EDGES_DDL


def test_the_triple_only_unique_key_is_gone():
    assert not re.search(r"CONSTRAINT\s+u_spo_graph\s+UNIQUE", RDF_EDGES_DDL), (
        "RDF_EDGES_DDL still declares u_spo_graph: a fresh install would refuse the "
        "second edge of a triple in a multigraph"
    )


def test_the_full_schema_script_uses_the_same_declaration():
    ddl = GraphSchema.get_base_schema_sql(embedding_dimension=384)
    assert "u_spo_graph_ekey" in ddl
    assert not re.search(r"CONSTRAINT\s+u_spo_graph\s+UNIQUE", ddl)


# --- the migration ------------------------------------------------------------------


class _CatalogCursor:
    """A cursor that answers the two catalog probes from a small state and records
    every other statement, applying the ones that change that state."""

    def __init__(self, *, column=None, constraints=()):
        # column: None (absent), "YES" (nullable) or "NO" (NOT NULL)
        self.column = column
        self.constraints = set(constraints)
        self.ddl: list = []
        self._rows: list = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        up = text.upper()
        if "INFORMATION_SCHEMA.COLUMNS" in up:
            self._rows = [] if self.column is None else [(self.column,)]
            return
        if "INFORMATION_SCHEMA.TABLE_CONSTRAINTS" in up:
            self._rows = [(c,) for c in sorted(self.constraints)]
            return
        self.ddl.append(text)
        if "ADD COLUMN EKEY" in up:
            self.column = "YES"
        elif "ALTER COLUMN EKEY NOT NULL" in up:
            self.column = "NO"
        elif "ADD CONSTRAINT U_SPO_GRAPH_EKEY" in up:
            self.constraints.add("u_spo_graph_ekey")
        elif "DROP CONSTRAINT U_SPO_GRAPH" in up and "EKEY" not in up:
            self.constraints.discard("u_spo_graph")
        self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def _step(ddl: list, needle: str) -> int:
    for i, stmt in enumerate(ddl):
        if needle in stmt.upper():
            return i
    raise AssertionError(f"no statement containing {needle!r} in {ddl}")


def test_ensure_ekey_runs_the_five_steps_in_order():
    cur = _CatalogCursor(column=None, constraints={"u_spo_graph"})
    result = GraphSchema.ensure_ekey(cur)

    add = _step(cur.ddl, "ADD COLUMN EKEY")
    fill = _step(cur.ddl, "SET EKEY = 0 WHERE EKEY IS NULL")
    not_null = _step(cur.ddl, "ALTER COLUMN EKEY NOT NULL")
    new_key = _step(cur.ddl, "ADD CONSTRAINT U_SPO_GRAPH_EKEY")
    drop_old = _step(cur.ddl, "DROP CONSTRAINT U_SPO_GRAPH")
    assert add < fill < not_null < new_key < drop_old, cur.ddl
    assert "UNIQUE (S, P, O_ID, GRAPH_ID, EKEY)" in cur.ddl[new_key].upper()
    assert result["status"] == "migrated", result


def test_ensure_ekey_on_a_table_at_234_changes_nothing():
    cur = _CatalogCursor(column="NO", constraints={"u_spo_graph_ekey"})
    result = GraphSchema.ensure_ekey(cur)
    assert cur.ddl == []
    assert result["status"] == "already at 234"


def test_ensure_ekey_twice_reports_already_at_234():
    cur = _CatalogCursor(column=None, constraints={"u_spo_graph"})
    GraphSchema.ensure_ekey(cur)
    cur.ddl.clear()
    assert GraphSchema.ensure_ekey(cur)["status"] == "already at 234"
    assert cur.ddl == []


def test_ensure_ekey_resumes_a_half_finished_migration():
    """The column landed but the process died before the key swap."""
    cur = _CatalogCursor(column="YES", constraints={"u_spo_graph"})
    result = GraphSchema.ensure_ekey(cur)
    assert not any("ADD COLUMN" in s.upper() for s in cur.ddl), cur.ddl
    _step(cur.ddl, "SET EKEY = 0 WHERE EKEY IS NULL")
    _step(cur.ddl, "ADD CONSTRAINT U_SPO_GRAPH_EKEY")
    _step(cur.ddl, "DROP CONSTRAINT U_SPO_GRAPH")
    assert result["status"] == "migrated"


def test_ensure_ekey_keeps_the_old_key_when_the_new_one_fails():
    """Never zero unique keys: the drop only follows a successful add."""

    class _Refusing(_CatalogCursor):
        def execute(self, sql, params=None):
            if "ADD CONSTRAINT U_SPO_GRAPH_EKEY" in " ".join(sql.split()).upper():
                raise RuntimeError("SQLCODE -400 simulated")
            super().execute(sql, params)

    cur = _Refusing(column=None, constraints={"u_spo_graph"})
    result = GraphSchema.ensure_ekey(cur)
    assert not any("DROP CONSTRAINT" in s.upper() for s in cur.ddl), cur.ddl
    assert "u_spo_graph" in cur.constraints
    assert result["status"] == "failed"


def test_schema_setup_does_not_readd_the_triple_key_after_the_swap(monkeypatch):
    calls = []
    monkeypatch.setattr(
        GraphSchema,
        "update_spo_unique_constraint",
        staticmethod(lambda cursor: calls.append("update_spo") or True),
    )
    monkeypatch.setattr(
        GraphSchema, "ensure_ekey", staticmethod(lambda cursor: {"status": "already at 234"})
    )
    cur = _CatalogCursor(column="NO", constraints={"u_spo_graph_ekey"})
    assert GraphSchema._at_234(cur) is True
    status = GraphSchema._spo_constraint_status(cur)
    assert calls == [], "update_spo_unique_constraint re-added u_spo_graph at 234"
    assert status["update_spo_unique_constraint"] is True
    assert status["ensure_ekey"] == {"status": "already at 234"}


def test_schema_setup_runs_the_old_migration_before_the_swap(monkeypatch):
    order = []
    monkeypatch.setattr(
        GraphSchema,
        "update_spo_unique_constraint",
        staticmethod(lambda cursor: order.append("update_spo") or True),
    )
    monkeypatch.setattr(
        GraphSchema,
        "ensure_ekey",
        staticmethod(lambda cursor: order.append("ensure_ekey") or {"status": "migrated"}),
    )
    cur = _CatalogCursor(column=None, constraints={"u_spo_graph"})
    GraphSchema._spo_constraint_status(cur)
    assert order == ["update_spo", "ensure_ekey"]


# --- ^IVG.GraphMode in every inventory ----------------------------------------------


def test_graph_mode_is_in_the_store_plan():
    from iris_vector_graph._engine import snapshot as snapshot_mod

    assert snapshot_mod.STORE_PLAN.get("^IVG.GraphMode") == "global"


def test_graph_mode_is_in_the_graph_stores_inventory():
    src = (CLS / "GraphStores.cls").read_text()
    assert 'Entry("^IVG.GraphMode", "global", "subscript 1", 1)' in src


def test_erase_graph_and_erase_all_clear_the_mode():
    src = (CLS / "Eraser.cls").read_text()
    erase_graph = src[src.index("ClassMethod EraseGraph") : src.index("ClassMethod EraseAll")]
    erase_all = src[src.index("ClassMethod EraseAll") :]
    assert "Kill ^IVG.GraphMode(tKey)" in erase_graph
    assert re.search(r"Kill \^IVG\.GraphMode\s*$", erase_all, re.M)


def test_graph_mode_class_has_its_three_methods():
    src = (CLS / "GraphMode.cls").read_text()
    for name in ("IsMulti", "Set", "ParallelCount"):
        assert re.search(rf"ClassMethod {name}\(", src), name


# --- the mode API --------------------------------------------------------------------


class _FakeIris:
    def __init__(self, parallel=0):
        self.mode = {}
        self.parallel = parallel
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method) + args)
        assert cls == "Graph.KG.GraphMode"
        graph = args[0]
        if method == "IsMulti":
            return 1 if self.mode.get(graph) else 0
        if method == "Apply":
            on = int(args[1])
            if not on and self.parallel:
                return f"parallel_edges_present:{self.parallel}"
            if on:
                self.mode[graph] = True
            else:
                self.mode.pop(graph, None)
            return "ok"
        raise AssertionError(method)


def _engine(fake):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._iris_obj = lambda: fake
    return eng


def test_is_multigraph_is_false_by_default():
    fake = _FakeIris()
    assert _engine(fake).is_multigraph("g1") is False


def test_set_multigraph_then_is_multigraph():
    fake = _FakeIris()
    eng = _engine(fake)
    eng.set_multigraph("g1", True)
    assert eng.is_multigraph("g1") is True
    assert eng.is_multigraph(None) is False


def test_is_multigraph_is_cached_per_engine():
    fake = _FakeIris()
    eng = _engine(fake)
    eng.is_multigraph("g1")
    eng.is_multigraph("g1")
    assert sum(1 for c in fake.calls if c[1] == "IsMulti") == 1


def test_set_multigraph_clears_the_cache():
    fake = _FakeIris()
    eng = _engine(fake)
    assert eng.is_multigraph("g1") is False
    eng.set_multigraph("g1", True)
    assert eng.is_multigraph("g1") is True


def test_disabling_with_parallel_edges_raises_with_the_count():
    from iris_vector_graph.errors import ParallelEdgesPresentError

    fake = _FakeIris(parallel=3)
    eng = _engine(fake)
    eng.set_multigraph("g1", True)
    with pytest.raises(ParallelEdgesPresentError) as exc:
        eng.set_multigraph("g1", False)
    assert exc.value.count == 3
    assert exc.value.code == "parallel_edges_present"
    assert "parallel_edges_present" in str(exc.value)
    assert eng.is_multigraph("g1") is True


def test_an_invalid_graph_name_is_refused_before_the_round_trip():
    fake = _FakeIris()
    with pytest.raises(ValueError):
        _engine(fake).set_multigraph("0", True)
    assert fake.calls == []


def test_the_graph_sub_engine_exposes_the_mode_api():
    from iris_vector_graph.engine import _GraphSubEngine

    assert hasattr(_GraphSubEngine, "set_multigraph")
    assert hasattr(_GraphSubEngine, "is_multigraph")


# --- WriteAdjacency / DeleteAdjacency are untouched ------------------------------


def _method_body(src: str, name: str) -> str:
    start = src.index(f"ClassMethod {name}(")
    brace = src.index("\n{", start)
    end = src.index("\n}\n", brace)
    return src[start : end + 3]


#: sha256 of each method's text at 2b3da99, before spec 234. With the mode off the
#: single-edge writers must be exactly what they were (FR-004, SC-003).
PRE_234_SHA = {
    "WriteAdjacency": "0c962bc9a96e723ff3bf07ecb761baca651094d14df10c9e11e222aa30332fe4",
    "DeleteAdjacency": "0ea5df73f49fc5d5661b4cdbde9f3f9bd56147f3f353aa10362ca5efad6d3509",
}


@pytest.mark.parametrize("name", sorted(PRE_234_SHA))
def test_the_single_edge_writers_are_unchanged(name):
    src = (CLS / "EdgeScan.cls").read_text()
    body = _method_body(src, name)
    assert hashlib.sha256(body.encode()).hexdigest() == PRE_234_SHA[name], (
        f"EdgeScan.{name} changed. Spec 234 adds the keyed pair beside it and leaves "
        "the single-edge writer byte-identical."
    )


def test_the_keyed_pair_exists_beside_them():
    src = (CLS / "EdgeScan.cls").read_text()
    assert "ClassMethod WriteAdjacencyKeyed(" in src
    assert "ClassMethod DeleteAdjacencyKeyed(" in src


def test_build_kg_keeps_its_single_edge_loop():
    """The pre-234 loop runs unchanged whenever no graph is in multigraph mode."""
    src = (CLS / "TraversalBuild.cls").read_text()
    body = _method_body(src, "BuildKG")
    assert "SELECT s, p, o_id, qualifiers, graph_id FROM Graph_KG.rdf_edges)" in body
    assert "$Data(^IVG.GraphMode)" in body


def test_module_exports_the_ddl_constant():
    assert schema_mod.RDF_EDGES_DDL is RDF_EDGES_DDL


def test_mock_cursor_update_spo_contract_still_holds():
    """test_schema_final_v10's contract for the pre-234 migration is unchanged."""
    cur = MagicMock()
    cur.execute.return_value = None
    assert GraphSchema.update_spo_unique_constraint(cur) is True
