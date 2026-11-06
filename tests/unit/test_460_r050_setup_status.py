"""4.6.0 R050 (expert walkthrough W4, W5): setup's status says what the run did, and a dry run says what it would do.

W5: a re-run of `initialize_schema()` that created nothing replied `tables_created: True` (the key was a constant) and
`kg_built: True` (the namespace's ^KG marker, set by an earlier run). Now `tables_created` lists the tables this run
created, `kg_built` says whether this run built ^KG and ^NKG from rdf_edges rows, and `objectscript_compiled` lists the
classes this run compiled (`objectscript_deployed` stays: the classes are there after the run). ADMIN_GUIDE documents
every key in one table.

W4: `initialize_schema(dry_run=True)` on a namespace never set up listed nothing it would create, and on one with rows
but no ^KG build it did not mention the rebuild the real run then made. It now replies, under the same keys as the real
run, the tables, routines and classes it would create, the adjacency indexes it would declare and whether it would build
^KG and ^NKG, reading the catalog and the deploy record only: the fakes below fail any write.

Expected answers come from the docs: ADMIN_GUIDE's inventory (docs/inventory.json: 18 tables after setup), "skips all
51 routines", the summary line "64 classes deployed; 3 skipped" on a server with embedded Python and no %AI.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine import legacy
from iris_vector_graph._engine.class_deploy import DeployResult, fingerprint, read_class_sources, rewrite_sources
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.routine_record import routine_fingerprint, routine_name
from iris_vector_graph.schema import GraphSchema

REPO = Path(__file__).resolve().parents[2]
INVENTORY = json.loads((REPO / "docs" / "inventory.json").read_text())
GUIDE = REPO / "docs" / "ADMIN_GUIDE.md"
SOURCES = REPO / "iris_src" / "src"
MCP = {"Graph.KG.MCPService", "Graph.KG.MCPToolSet", "Graph.KG.MCPTools"}


def inventory_tables() -> set:
    return {n for e in INVENTORY["entries"] if e["kind"] == "table" and e["present"] == "setup" for n in e["names"]}


def shipped_classes() -> set:
    return {".".join(p.relative_to(SOURCES).with_suffix("").parts) for p in SOURCES.rglob("*.cls")}


# ------------------------------------------------------------------------------------------------- a fake namespace


class _Namespace:
    """What setup's probes read: base tables, indexes, routines, compiled classes, rows in rdf_edges, globals."""

    def __init__(self, tables=(), indexes=(), routines=(), classes=(), edge_rows=0, globals_=None, kg_marker=False):
        self.tables, self.indexes, self.routines = set(tables), set(indexes), set(routines)
        self.classes, self.edge_rows, self.globals = set(classes), edge_rows, dict(globals_ or {})
        self.kg_marker = kg_marker
        self.written: list = []


class _Cursor:
    """Answers the catalog probes from `_Namespace`; `read_only` fails any statement but a SELECT."""

    def __init__(self, ns: _Namespace, read_only: bool):
        self.ns, self.read_only = ns, read_only
        self._rows: list = []
        self.description = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if self.read_only:
            assert text.upper().startswith("SELECT"), f"a dry run wrote: {text[:120]}"
        params = list(params or [])
        self._rows = []
        m = re.match(r"CREATE TABLE (?:IF NOT EXISTS )?\w+\.(\w+)", text, re.I)
        if m:
            self.ns.tables.add(m.group(1))
            return
        if not text.upper().startswith("SELECT"):
            self.ns.written.append(text[:80])
            return
        if text.startswith("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?"):
            self._rows = [(t,) for t in sorted(self.ns.tables)]
        elif "FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?" in text:
            self._rows = [(r,) for r in sorted(self.ns.routines)]
        elif text.startswith("SELECT COUNT(*) FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = ? AND INDEX_NAME = ?"):
            self._rows = [(int(params[1] in self.ns.indexes),)]
        elif text.startswith("SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?"):
            table, column = params[1], params[2]
            self._rows = [(int(table in self.ns.tables and (table, column) != ("kg_NodeEmbeddings", "id")),)]
        elif re.search(r"FROM \w+\.rdf_edges$", text) and "TOP 1 1" in text:
            self._rows = [(1,)] if self.ns.edge_rows else []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def executemany(self, sql, rows):
        assert not self.read_only, f"a dry run wrote: {sql[:120]}"

    def close(self):
        pass


class _Native:
    """The Native API calls a dry run makes; `set` and `kill` fail it."""

    def __init__(self, ns: _Namespace):
        self.ns = ns

    def get(self, glo, *subs):
        return self.ns.globals.get((glo,) + subs)

    def set(self, value, glo, *subs):
        raise AssertionError(f"a dry run set {glo}{subs}")

    def kill(self, glo, *subs):
        raise AssertionError(f"a dry run killed {glo}{subs}")

    def isDefined(self, glo, *subs):
        return 0

    def classMethodObject(self, cls, method, *args):
        assert (cls, method) == ("%SYS.Python", "Import"), (cls, method)
        return object()

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "IVGDRY"
        if (cls, method) in (("%Dictionary.CompiledClass", "%ExistsId"), ("%Dictionary.CompiledClass", "IDKEYExists")):
            return int(args[0] in self.ns.classes)
        if (cls, method) == ("Graph.KG.Meta", "IsSet"):
            if "Graph.KG.Meta" not in self.ns.classes:
                raise RuntimeError("<CLASS DOES NOT EXIST> *Graph.KG.Meta")
            return int(self.ns.kg_marker and args[0] in ("kg_built", "nkg_built"))
        raise AssertionError(f"a dry run called {cls}.{method}{args}")


def _dry_run(monkeypatch, ns: _Namespace) -> dict:
    monkeypatch.setenv("IVG_EMBEDDED_PYTHON", "1")
    monkeypatch.delenv("IVG_FORCE_CLASS_DEPLOY", raising=False)
    native = _Native(ns)
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = lambda: _Cursor(ns, read_only=True)
    conn.commit.side_effect = AssertionError("a dry run committed")
    monkeypatch.setattr("iris.createIRIS", lambda c: native)
    monkeypatch.setattr("iris_vector_graph.schema_name.refuse_package_clash", lambda conn, schema: None)
    monkeypatch.setattr(legacy, "present_legacy_objects", lambda conn, nat: [])
    monkeypatch.setattr(legacy, "copy_grants", lambda conn, found, schema, dry_run: [])
    monkeypatch.setattr(legacy, "copy_ledger_history", lambda conn, schema, dry_run: {})
    monkeypatch.setattr(legacy, "grant_read_path", lambda *a, **k: [])
    monkeypatch.setattr(legacy, "arno_copies", lambda conn: [])
    monkeypatch.setattr(IRISGraphEngine, "_plan_vector_dimensions", lambda self, cur, dim: None, raising=False)
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine._schema_prefix = "Graph_KG"
    engine.embedding_dimension = 768
    monkeypatch.setattr(IRISGraphEngine, "_iris_obj", lambda self: native, raising=False)
    return engine._record_and_initialize(True, True)


def _set_up_namespace(edge_rows=0, kg_marker=False) -> _Namespace:
    """A namespace one 4.6.0 setup made: every table, index, routine and class, and the deploy record of each."""
    stmts = [s for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG") if s.strip()]
    routines = {routine_name(s): routine_fingerprint(s, "Graph_KG") for s in stmts if routine_name(s)}
    sources = rewrite_sources(read_class_sources(SOURCES), "Graph_KG")
    selected = {n: t for n, t in sources.items() if n not in MCP}
    return _Namespace(
        tables=inventory_tables(), indexes={"idx_edges_adj", "idx_labels_adj"}, routines=set(routines),
        classes=set(selected), edge_rows=edge_rows, kg_marker=kg_marker,
        globals_={("^IVG.Deploy", "schema"): "Graph_KG", ("^IVG.Deploy", "routines"): json.dumps(routines),
                  ("^IVG.Deploy", "fingerprint"): fingerprint(selected)})


# ------------------------------------------------------------------------------------------------ W4: the dry run


def test_a_dry_run_on_a_namespace_never_set_up_lists_what_setup_would_create(monkeypatch):
    status = _dry_run(monkeypatch, _Namespace())
    assert status["dry_run"] is True and status["schema_recorded"] == "new"
    assert status["tables_created"] == sorted(inventory_tables())
    assert len(status["tables_created"]) == 18
    assert len(status["routines"]["created"]) == 51 and status["routines"]["unchanged"] == 0
    assert set(status["objectscript_compiled"]) == shipped_classes() - MCP
    assert len(status["objectscript_compiled"]) == 64
    assert set(status["objectscript_skipped"]) == MCP
    assert status["adjacency_index"] == {"idx_edges_adj": "created", "idx_labels_adj": "created"}
    assert status["kg_built"] is False  # no rows to build from


def test_a_dry_run_where_rows_wait_for_their_first_build_says_kg_would_be_built(monkeypatch):
    """The walkthrough's USER: set up empty, then loaded; the next setup built ^KG and ^NKG."""
    status = _dry_run(monkeypatch, _set_up_namespace(edge_rows=11, kg_marker=False))
    assert status["kg_built"] is True
    assert status["tables_created"] == []
    assert status["routines"] == {"created": [], "unchanged": 51, "failed": {}}
    assert status["objectscript_compiled"] == []
    assert status["adjacency_index"] == {"idx_edges_adj": "present", "idx_labels_adj": "present"}


def test_a_dry_run_on_a_namespace_already_built_plans_nothing(monkeypatch):
    status = _dry_run(monkeypatch, _set_up_namespace(edge_rows=11, kg_marker=True))
    assert status["kg_built"] is False
    assert (status["tables_created"], status["routines"]["created"], status["objectscript_compiled"]) == ([], [], [])


def test_a_dry_run_names_a_dropped_table_and_routine(monkeypatch):
    ns = _set_up_namespace()
    ns.tables.discard("docs")
    ns.routines.discard("CY_CMP")
    status = _dry_run(monkeypatch, ns)
    assert status["tables_created"] == ["docs"]
    assert status["routines"]["created"] == ["CY_CMP"] and status["routines"]["unchanged"] == 50


# ------------------------------------------------------------------------------------------- W5: the real run's keys


def _real_run(caplog, ns: _Namespace, deploy: DeployResult, built: bool, caps_kg_built: bool = False) -> dict:
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = lambda: _Cursor(ns, read_only=False)

    def deploy_classes(cursor, path, conn=None, force=None, schema=None):
        GraphSchema.last_deploy = deploy
        return IRISCapabilities(objectscript_deployed=True, kg_built=caps_kg_built)

    class _Quiet:
        def classMethodValue(self, cls, method, *args):
            if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
                return "IVGRUN"
            raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

        def classMethodVoid(self, *a):
            pass

        def get(self, *subs):
            return None

        def set(self, value, *subs):
            pass

    with patch("iris.createIRIS", lambda c: _Quiet()), \
            patch.object(GraphSchema, "deploy_objectscript_classes", staticmethod(deploy_classes)), \
            patch.object(GraphSchema, "ensure_adjacency_index",
                         staticmethod(lambda cur: {"idx_edges_adj": "present", "idx_labels_adj": "present"})), \
            patch.object(GraphSchema, "bootstrap_kg_global",
                         staticmethod(lambda cur, conn=None, schema="Graph_KG": built)), \
            caplog.at_level(logging.INFO):
        return IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()


def test_a_run_that_creates_the_tables_lists_them(caplog):
    """The fake creates what the base DDL and the registry/quarantine create; the ledger tables are its classes'."""
    status = _real_run(caplog, _Namespace(), DeployResult(deployed=True, compiled=["Graph.KG.PageRank"]), built=False)
    assert status["tables_created"] == sorted(inventory_tables() - {"ledger_revisions", "ledger_stats"})


def test_a_rerun_that_creates_no_table_says_so(caplog):
    status = _real_run(caplog, _Namespace(tables=inventory_tables()), DeployResult(deployed=True, unchanged=True,
                                                                               compiled=["Graph.KG.PageRank"]),
                       built=False, caps_kg_built=True)
    assert status["tables_created"] == []
    assert status["objectscript_compiled"] == []  # the classes were unchanged
    assert status["objectscript_deployed"] is True  # and they are there
    assert status["kg_built"] is False  # ^KG was built by an earlier run, not this one


def test_kg_built_says_this_run_built_kg(caplog):
    status = _real_run(caplog, _Namespace(tables=inventory_tables()), DeployResult(deployed=True, unchanged=True),
                       built=True)
    assert status["kg_built"] is True


def test_objectscript_compiled_lists_the_classes_compiled_and_not_the_failed(caplog):
    deploy = DeployResult(deployed=True, compiled=["Graph.KG.PageRank", "IVG.Percentile", "Z.Code"],
                          failed={"IVG.Percentile": "ERROR #5083"}, errors=["IVG.Percentile: ERROR #5083"])
    status = _real_run(caplog, _Namespace(tables=inventory_tables()), deploy, built=False)
    assert status["objectscript_compiled"] == ["Graph.KG.PageRank", "Z.Code"]


# --------------------------------------------------------------------------------------------- W5: the docs' table


def guide_keys() -> dict:
    """{key: "both" | "run" | "dry run"} from ADMIN_GUIDE's status table (the one after `<!-- setup-status -->`)."""
    lines = GUIDE.read_text().splitlines()
    start = lines.index("<!-- setup-status -->")
    out = {}
    for line in lines[start + 1:]:
        if not line.strip():
            if out:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("Key",) or set(cells[0]) <= {"-"}:
            continue
        (key,) = re.findall(r"`(\w+)`", cells[0])
        out[key] = cells[1]
    return out


def test_the_guide_documents_every_key_a_run_returns(caplog):
    status = _real_run(caplog, _Namespace(), DeployResult(deployed=True, compiled=["Graph.KG.PageRank"]), built=False)
    documented = {k for k, where in guide_keys().items() if where in ("both", "run")}
    assert documented == set(status)


def test_the_guide_documents_every_key_a_dry_run_returns(monkeypatch):
    status = _dry_run(monkeypatch, _Namespace())
    documented = {k for k, where in guide_keys().items() if where in ("both", "dry run")}
    assert documented == set(status)


def test_the_guide_says_where_each_key_is_returned():
    assert set(guide_keys().values()) <= {"both", "run", "dry run"}


# ----------------------------------------------------------------------------------------------- the setup tables


def test_setup_tables_are_the_inventory_s():
    from iris_vector_graph._engine.schema import setup_tables

    assert setup_tables() == sorted(inventory_tables())


# ------------------------------------------------------- round 2 (expert walkthrough W6): the catalog's spelling


def _catalog_names() -> list:
    """The routines setup creates, as their DDL spells them: the name INFORMATION_SCHEMA.ROUTINES lists."""
    from iris_vector_graph.routine_record import declared_routine_name

    stmts = [s for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG") if s.strip()]
    return sorted((declared_routine_name(s) for s in stmts if declared_routine_name(s)), key=str.upper)


def test_the_dry_run_names_the_routines_as_the_catalog_spells_them(monkeypatch):
    """The reply said KG_PPR and KG_BETWEENNESS; INFORMATION_SCHEMA.ROUTINES and the inventory say kg_PPR and
    kg_Betweenness, so a script could not diff the two."""
    created = _dry_run(monkeypatch, _Namespace())["routines"]["created"]
    assert "kg_PPR" in created and "kg_Betweenness" in created and "KG_PPR" not in created
    assert created == _catalog_names()


def test_the_run_names_the_routines_as_the_catalog_spells_them(caplog):
    status = _real_run(caplog, _Namespace(), DeployResult(deployed=True, compiled=["Graph.KG.PageRank"]), built=False)
    created = status["routines"]["created"]
    assert "kg_PPR" in created and "KG_PPR" not in created
    assert created == [n for n in _catalog_names() if n in created]
