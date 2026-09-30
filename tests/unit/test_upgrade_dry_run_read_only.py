"""A dry run of `upgrade_to_4_0_0` writes nothing and always reports (DEBT entry 10, bug 2).

4.1.0's dry run crashed on 2.x installs, told the operator to run calls that write
without saying so, and recompiled `Graph.KG` (a package compile also breaks iFind's
generated class, which the dry run then "repaired" — two writes). These pin the
behaviour with fakes; `tests/e2e/test_upgrade_dry_run_2x.py` pins it against the
frozen 2.16 and 2.20 installs and diffs the namespace before and after.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.migrations import upgrade as up
from iris_vector_graph.migrations.docs_and_edge_vectors import place_documents
from iris_vector_graph.migrations.kg_node_stores import rekey_kg_node_stores
from tests.unit.migration_fakes_230 import docs_conn, kg_stores_conn

WRITING_CALLS = ("initialize_schema(", "prepare_docs(", "prepare_edge_vectors(")


def _says_it_writes(message: str) -> bool:
    return bool(re.search(r"\bwrites?\b", message))


def _graph_rows():
    return {
        "props": [{"s": "t:n1", "key": "name", "val": "Alice", "graph_id": ""}],
        "labels": [{"s": "t:n1", "label": "Person", "graph_id": ""}],
        "edges": [{"s": "t:n1", "p": "knows", "o_id": "t:n2", "graph_id": ""}],
    }


# --- the report shape ---------------------------------------------------------------


class TestBlockedIsItsOwnOutcome:
    def test_a_blocked_step_is_neither_run_nor_skipped(self):
        step = up.StepResult("docs", blocked_because="needs a column")
        assert step.blocked and not step.skipped and step.report is None

    def test_the_report_lists_blocked_steps(self):
        report = up.UpgradeReport(
            steps=(
                up.StepResult("embeddings", report=object()),
                up.StepResult("docs", blocked_because="x"),
                up.StepResult("edge_vectors", skipped_because="y"),
            )
        )
        assert report.blocked == ("docs",)
        assert report.skipped == ("edge_vectors",)


# --- the ^KG step's dry run ---------------------------------------------------------


class TestTheKgDryRunIsReadOnly:
    def test_it_does_not_recompile(self):
        conn = kg_stores_conn(**_graph_rows(), flat_entries={("prop", "t:n1", "name"): "A"})
        rekey_kg_node_stores(conn, dry_run=True)
        assert conn.registry.compiles == []
        assert conn.registry.killed == [] and conn.registry.rebuilds == []

    def test_it_does_not_repair_ifind(self, monkeypatch):
        from iris_vector_graph.migrations import kg_node_stores

        seen = []
        monkeypatch.setattr(
            kg_node_stores, "repair_ifind_helpers", lambda *a, **k: seen.append(a) or {}
        )
        rekey_kg_node_stores(kg_stores_conn(**_graph_rows()), dry_run=True)
        assert seen == []

    def test_uncompiled_entry_points_are_reported_not_raised(self):
        """Only the real run's recompile can say whether they come back, so the dry run
        says what is missing now and that the real run recompiles first."""
        conn = kg_stores_conn(**_graph_rows(), compiled_methods=(), compile_repairs=False)
        report = rekey_kg_node_stores(conn, dry_run=True)
        assert "Graph.KG.TraversalBuild::BuildKG" in report.entry_points_uncompiled
        assert conn.registry.compiles == []

    def test_the_real_run_still_refuses_after_its_recompile(self):
        conn = kg_stores_conn(**_graph_rows(), compiled_methods=(), compile_repairs=False)
        with pytest.raises(RuntimeError, match="BuildKG"):
            rekey_kg_node_stores(conn)
        assert conn.registry.killed == []

    def test_the_unscoped_refusal_names_the_step_that_writes_the_column(self):
        conn = kg_stores_conn(**_graph_rows(), scoped_columns=("rdf_edges",))
        with pytest.raises(RuntimeError) as excinfo:
            rekey_kg_node_stores(conn)
        message = str(excinfo.value)
        assert "embeddings" in message
        assert _says_it_writes(message), message


# --- the docs step's dry run --------------------------------------------------------


class TestTheDocsDryRunBeforePrepare:
    def test_it_predicts_every_row_as_unplaced(self):
        """`prepare_docs` adds `graph_id` NULL, so before it runs every row is unplaced
        — the dry run can predict from that without adding the column."""
        conn = docs_conn(
            [{"id": "n1", "text": "one"}, {"id": "doc-9", "text": "orphan"}],
            {"n1": ["tenant-a"]},
            has_graph_column=False,
        )
        report = place_documents(conn, dry_run=True)
        assert report.rows_placed == {"tenant-a": 1}
        assert report.rows_quarantined == {"no_node": 1}
        assert conn.registry.ddl == [] and conn.registry.quarantine == []

    def test_the_real_run_refusal_says_the_fix_writes(self):
        conn = docs_conn([{"id": "n1", "text": "one"}], {"n1": ["a"]}, has_graph_column=False)
        with pytest.raises(RuntimeError) as excinfo:
            place_documents(conn)
        assert _says_it_writes(str(excinfo.value)), excinfo.value


# --- the whole dry run --------------------------------------------------------------


class _Catalog:
    """INFORMATION_SCHEMA.COLUMNS over a fixed set of (table, column)."""

    def __init__(self, columns):
        self.columns = {(t.lower(), c.lower()) for t, c in columns}
        self.executed = []

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        self.executed.append(text)
        assert "INFORMATION_SCHEMA" in text.upper(), f"dry-run preflight wrote/read: {text}"
        args = list(params or [])
        table = re.search(r"(?i)TABLE_NAME = '([^']+)'", text)
        strs = [a for a in args if isinstance(a, str)]
        if table:
            t, c = table.group(1), strs[-1]
        else:
            t, c = strs[-2], strs[-1]
        self._rows = [(1 if (t.lower(), c.lower()) in self.columns else 0,)]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]

    def close(self):
        pass

    def commit(self):
        pass


PRE_214 = [("nodes", "node_id"), ("docs", "id"), ("rdf_labels", "s"), ("rdf_props", "s"),
           ("rdf_edges", "graph_id"), ("kg_EdgeEmbeddings", "s")]
V220 = PRE_214 + [("nodes", "graph_id")]


def _never(*_a, **_k):
    raise AssertionError("a blocked step must not be attempted")


class TestTheDryRunPreflight:
    def test_pre_214_blocks_embeddings_and_docs_on_nodes_graph_id(self, monkeypatch):
        monkeypatch.setattr(up, "migrate_to_graph_scoped_embeddings", _never)
        monkeypatch.setattr(up, "place_documents", _never)
        conn = _Catalog(PRE_214)
        for name in ("embeddings", "docs"):
            reason = up._dry_run_blocker(conn, name, "Graph_KG")
            assert reason and "nodes" in reason and "graph_id" in reason, name
            assert "initialize_schema(" in reason and _says_it_writes(reason), reason

    def test_unscoped_labels_block_the_kg_step_naming_the_embeddings_step(self):
        reason = up._dry_run_blocker(_Catalog(V220), "kg_node_stores", "Graph_KG")
        assert reason and "rdf_labels" in reason and "embeddings" in reason
        assert _says_it_writes(reason)

    def test_a_220_install_does_not_block_embeddings_or_docs(self):
        conn = _Catalog(V220)
        assert up._dry_run_blocker(conn, "embeddings", "Graph_KG") is None
        assert up._dry_run_blocker(conn, "docs", "Graph_KG") is None

    def test_a_scoped_install_blocks_nothing(self):
        cols = V220 + [("rdf_labels", "graph_id"), ("rdf_props", "graph_id")]
        for name in up.UPGRADE_STEPS:
            assert up._dry_run_blocker(_Catalog(cols), name, "Graph_KG") is None, name

    def test_a_blocked_dry_run_step_is_reported_not_attempted(self, monkeypatch):
        monkeypatch.setattr(up, "migrate_to_graph_scoped_embeddings", _never)
        monkeypatch.setattr(up, "rekey_kg_node_stores", _never)
        report = up.upgrade_to_4_0_0(
            _Catalog(PRE_214), steps=("embeddings", "kg_node_stores"), dry_run=True
        )
        assert report.blocked == ("embeddings", "kg_node_stores")

    def test_the_real_run_does_not_consult_the_preflight(self, monkeypatch):
        monkeypatch.setattr(up, "_dry_run_blocker", _never)
        calls = []
        monkeypatch.setattr(
            up, "rekey_kg_node_stores", lambda conn, **kw: calls.append(kw) or "report"
        )
        monkeypatch.setattr(up, "_tune", lambda conn, schema: {})
        report = up.upgrade_to_4_0_0(object(), steps=("kg_node_stores",))
        assert report["kg_node_stores"].report == "report"


def test_no_migration_message_sends_the_operator_to_a_write_unannounced():
    """Every refusal string in the migrations that names a writing call says it writes."""
    from pathlib import Path

    import iris_vector_graph.migrations as pkg

    offenders = []
    for path in Path(pkg.__file__).parent.glob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"raise RuntimeError\((.*?)\n\s*\)", src, re.S):
            body = m.group(1)
            if any(call in body for call in WRITING_CALLS) and not _says_it_writes(body):
                offenders.append(f"{path.name}: {' '.join(body.split())[:140]}")
    assert offenders == [], "\n".join(offenders)
