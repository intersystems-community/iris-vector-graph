"""Migration statements as data: the docs and edge-vector step (spec 230).

The statements of ``migrations/docs_and_edge_vectors.py`` are declared in
``migrations/statements.py`` and looked up by name. Three things are checked here,
without a database:

* (a) the declarations are well formed: names match keys, placeholders match the
  documented parameters, no statement carries a trailing ``;``;
* (b) the rendered SQL text is byte-for-byte what the code produced before the
  refactor. The golden strings below were captured from the pre-refactor functions
  (``docs_prepare_sql`` and friends, and the inline statements of
  ``place_edge_vectors``) and are not regenerated from the new code;
* (c) ``run_statement`` executes through a fake cursor with the documented semantics.
"""

from __future__ import annotations

import dataclasses

import pytest

from iris_vector_graph.migrations import docs_and_edge_vectors as dev
from iris_vector_graph.migrations.statements import (
    EXPECTS,
    STATEMENTS,
    STAGING_SUFFIX,
    Statement,
    bind,
    run_statement,
)

TABLE = "Graph_KG.kg_EdgeEmbeddings_ivg400"
DIM = 384


def _render(name: str) -> str:
    return bind(name, schema="Graph_KG", dimension=DIM, dtype="DOUBLE", table=TABLE).sql


# ---------------------------------------------------------------------------
# (a) well-formed declarations
# ---------------------------------------------------------------------------


class TestDeclarations:
    def test_every_name_equals_its_key(self):
        for key, stmt in STATEMENTS.items():
            assert isinstance(stmt, Statement), key
            assert stmt.name == key

    def test_every_expects_is_declared(self):
        assert set(EXPECTS) == {"rows-changed", "rows-selected", "ddl"}
        for stmt in STATEMENTS.values():
            assert stmt.expects in EXPECTS, stmt.name

    def test_placeholders_match_params_doc(self):
        for name in STATEMENTS:
            sql = _render(name)
            assert sql.count("?") == len(STATEMENTS[name].params_doc), name

    def test_no_trailing_semicolon(self):
        for name in STATEMENTS:
            sql = _render(name)
            assert ";" not in sql, name
            assert not sql.strip().endswith(";"), name

    def test_statement_is_frozen(self):
        stmt = STATEMENTS["docs_copy_placed"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            stmt.sql = "SELECT 1"  # type: ignore[misc]

    def test_unknown_expects_is_refused(self):
        with pytest.raises(ValueError):
            Statement(name="x", sql="SELECT 1", params_doc=(), expects="maybe")

    def test_staging_suffix_is_the_4_0_0_suffix(self):
        assert STAGING_SUFFIX == "_ivg400"
        assert dev.STAGING_SUFFIX == STAGING_SUFFIX


# ---------------------------------------------------------------------------
# (b) golden text, captured before the refactor
# ---------------------------------------------------------------------------

GOLDEN_DOCS_PREPARE = [
    "ALTER TABLE Graph_KG.docs ADD COLUMN graph_id VARCHAR(256) %EXACT NULL",
    "CREATE TABLE IF NOT EXISTS Graph_KG.docs_quarantine (\n"
    "    doc_id VARCHAR(256) %EXACT NOT NULL PRIMARY KEY,\n"
    "    text VARCHAR(4000) %EXACT,\n"
    "    reason VARCHAR(64) NOT NULL,\n"
    "    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n"
    ")",
]

GOLDEN_DOCS_FINISH = [
    "CREATE TABLE Graph_KG.docs_ivg400 (\n"
    "  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
    "  id       VARCHAR(256) %EXACT NOT NULL,\n"
    "  text     VARCHAR(4000) %EXACT,\n"
    "  CONSTRAINT pk_docs PRIMARY KEY (graph_id, id)\n"
    ")",
    "INSERT INTO Graph_KG.docs_ivg400 (graph_id, id, text) "
    "SELECT graph_id, id, text FROM Graph_KG.docs WHERE graph_id IS NOT NULL",
    "DROP TABLE Graph_KG.docs",
    "ALTER TABLE Graph_KG.docs_ivg400 RENAME docs",
    "CREATE INDEX idx_docs_graph ON Graph_KG.docs (graph_id)",
    "CREATE INDEX idx_docs_text_ifind ON TABLE Graph_KG.docs (text) AS %iFind.Index.Basic",
]

GOLDEN_EDGE_PREPARE_384 = [
    "CREATE TABLE Graph_KG.kg_EdgeEmbeddings_ivg400 (\n"
    "    emb_rowid BIGINT IDENTITY PRIMARY KEY,\n"
    "    graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
    "    s    VARCHAR(256) %EXACT NOT NULL,\n"
    "    p    VARCHAR(512) %EXACT NOT NULL,\n"
    "    o_id VARCHAR(256) %EXACT NOT NULL,\n"
    "    emb  VECTOR(DOUBLE, 384),\n"
    "    metadata VARCHAR(4000),\n"
    "    CONSTRAINT uq_edge_emb_graph_spo UNIQUE (graph_id, s, p, o_id)\n"
    ")",
    "CREATE TABLE IF NOT EXISTS Graph_KG.edge_vector_quarantine (\n"
    "    q_rowid BIGINT IDENTITY PRIMARY KEY,\n"
    "    s    VARCHAR(256) %EXACT NOT NULL,\n"
    "    p    VARCHAR(512) %EXACT NOT NULL,\n"
    "    o_id VARCHAR(256) %EXACT NOT NULL,\n"
    "    reason VARCHAR(64) NOT NULL,\n"
    "    emb  VECTOR(DOUBLE, 384),\n"
    "    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n"
    "    CONSTRAINT uq_edge_quarantine_spo UNIQUE (s, p, o_id)\n"
    ")",
]

GOLDEN_EDGE_FINISH = [
    "DROP TABLE Graph_KG.kg_EdgeEmbeddings",
    "ALTER TABLE Graph_KG.kg_EdgeEmbeddings_ivg400 RENAME kg_EdgeEmbeddings",
]

# The inline statements of place_edge_vectors, in the order a run issues them, with the
# parameters each one was given in the golden run.
GOLDEN_PLACE_EDGE_UNIQUE_GRAPH = (
    "INSERT INTO Graph_KG.kg_EdgeEmbeddings_ivg400 (graph_id, s, p, o_id, emb, metadata) "
    "SELECT COALESCE(r.graph_id, ''), e.s, e.p, e.o_id, e.emb, NULL "
    "FROM Graph_KG.kg_EdgeEmbeddings e JOIN ("
    "SELECT s, p, o_id, MIN(graph_id) AS graph_id "
    "FROM Graph_KG.rdf_edges GROUP BY s, p, o_id HAVING COUNT(DISTINCT graph_id) = 1"
    ") r ON r.s = e.s AND r.p = e.p AND r.o_id = e.o_id "
    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_EdgeEmbeddings_ivg400 t "
    "WHERE t.s = e.s AND t.p = e.p AND t.o_id = e.o_id)"
)
GOLDEN_PLACE_EDGE_QUARANTINE_NO_EDGE = (
    "INSERT INTO Graph_KG.edge_vector_quarantine (s, p, o_id, reason, emb, quarantined_at) "
    "SELECT e.s, e.p, e.o_id, 'no_edge', e.emb, CURRENT_TIMESTAMP "
    "FROM Graph_KG.kg_EdgeEmbeddings e WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges r "
    "WHERE r.s = e.s AND r.p = e.p AND r.o_id = e.o_id) "
    "AND NOT EXISTS (SELECT 1 FROM Graph_KG.edge_vector_quarantine q "
    "WHERE q.s = e.s AND q.p = e.p AND q.o_id = e.o_id)"
)
GOLDEN_PLACE_EDGE_BY_KEY = (
    "INSERT INTO Graph_KG.kg_EdgeEmbeddings_ivg400 (graph_id, s, p, o_id, emb, metadata) "
    "SELECT ?, s, p, o_id, emb, NULL FROM Graph_KG.kg_EdgeEmbeddings "
    "WHERE s = ? AND p = ? AND o_id = ?"
)
GOLDEN_PLACE_EDGE_QUARANTINE_BY_KEY = (
    "INSERT INTO Graph_KG.edge_vector_quarantine (s, p, o_id, reason, emb, quarantined_at) "
    "SELECT s, p, o_id, ?, emb, CURRENT_TIMESTAMP "
    "FROM Graph_KG.kg_EdgeEmbeddings WHERE s = ? AND p = ? AND o_id = ?"
)


class TestGoldenText:
    def test_docs_prepare(self):
        assert dev.docs_prepare_sql() == GOLDEN_DOCS_PREPARE
        assert [_render(n) for n in ("docs_add_graph_column", "docs_quarantine_create")] == GOLDEN_DOCS_PREPARE

    def test_docs_finish(self):
        assert dev.docs_finish_sql() == GOLDEN_DOCS_FINISH

    def test_edge_vectors_prepare_at_384(self):
        assert dev.edge_vectors_prepare_sql(384) == GOLDEN_EDGE_PREPARE_384

    def test_edge_vectors_finish(self):
        assert dev.edge_vectors_finish_sql() == GOLDEN_EDGE_FINISH

    def test_place_edge_vectors_fixed_statements(self):
        assert _render("edge_place_unique_graph") == GOLDEN_PLACE_EDGE_UNIQUE_GRAPH
        assert _render("edge_quarantine_no_edge") == GOLDEN_PLACE_EDGE_QUARANTINE_NO_EDGE
        assert _render("edge_place_by_key") == GOLDEN_PLACE_EDGE_BY_KEY
        assert _render("edge_quarantine_by_key") == GOLDEN_PLACE_EDGE_QUARANTINE_BY_KEY

    def test_placement_parameters_documented(self):
        assert STATEMENTS["edge_place_unique_graph"].params_doc == ()
        assert STATEMENTS["edge_quarantine_no_edge"].params_doc == ()
        assert STATEMENTS["edge_place_by_key"].params_doc == ("graph_id", "s", "p", "o_id")
        assert STATEMENTS["edge_quarantine_by_key"].params_doc == ("reason", "s", "p", "o_id")

    def test_expects_of_the_placement_statements(self):
        assert STATEMENTS["edge_place_unique_graph"].expects == "rows-changed"
        assert STATEMENTS["edge_quarantine_no_edge"].expects == "rows-changed"
        assert STATEMENTS["edge_place_by_key"].expects == "rows-changed"
        assert STATEMENTS["edge_quarantine_by_key"].expects == "rows-changed"
        assert STATEMENTS["edge_held_rows"].expects == "rows-selected"
        assert STATEMENTS["edge_width_reset_drop"].expects == "ddl"

    def test_held_rows_probe_text(self):
        assert _render("edge_held_rows") == "SELECT COUNT(*) FROM Graph_KG.kg_EdgeEmbeddings_ivg400"
        assert _render("edge_width_reset_drop") == "DROP TABLE Graph_KG.kg_EdgeEmbeddings_ivg400"


# ---------------------------------------------------------------------------
# (c) run_statement through a fake cursor
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.closed = False

    def execute(self, sql, *args):
        self.conn.calls.append((sql, args))
        if self.conn.fail_with is not None:
            raise self.conn.fail_with
        self._rows = list(self.conn.rows)

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True
        self.conn.closed_cursors += 1


class FakeConn:
    def __init__(self, rows=(), fail_with=None):
        self.rows = list(rows)
        self.fail_with = fail_with
        self.calls = []
        self.commits = 0
        self.closed_cursors = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def _stmt(sql, expects="ddl", params_doc=()):
    return Statement(name="probe", sql=sql, params_doc=params_doc, expects=expects)


class TestRunStatement:
    def test_strips_trailing_semicolon_and_whitespace(self):
        conn = FakeConn()
        run_statement(conn, _stmt("SELECT 1 ;  \n"))
        assert conn.calls[0][0] == "SELECT 1"

    def test_no_parameter_list_when_empty(self):
        conn = FakeConn()
        run_statement(conn, _stmt("DROP TABLE x"))
        assert conn.calls == [("DROP TABLE x", ())]

    def test_parameter_list_passed_when_given(self):
        conn = FakeConn()
        stmt = _stmt("DELETE FROM t WHERE a = ? AND b = ?", "rows-changed", ("a", "b"))
        run_statement(conn, stmt, ["1", "2"])
        assert conn.calls == [("DELETE FROM t WHERE a = ? AND b = ?", (["1", "2"],))]

    def test_placeholder_count_mismatch_executes_nothing(self):
        conn = FakeConn()
        stmt = _stmt("DELETE FROM t WHERE a = ?", "rows-changed", ("a",))
        with pytest.raises(ValueError):
            run_statement(conn, stmt, ["1", "2"])
        assert conn.calls == []

    def test_rows_selected_returns_the_rows(self):
        conn = FakeConn(rows=[(7,)])
        assert run_statement(conn, _stmt("SELECT COUNT(*) FROM t", "rows-selected")) == [(7,)]

    def test_ddl_and_rows_changed_return_nothing(self):
        conn = FakeConn(rows=[(7,)])
        assert run_statement(conn, _stmt("DROP TABLE t", "ddl")) is None
        assert run_statement(conn, _stmt("INSERT INTO t VALUES (1)", "rows-changed")) is None

    def test_cursor_closed_when_execute_fails(self):
        conn = FakeConn(fail_with=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            run_statement(conn, _stmt("DROP TABLE t"))
        assert conn.closed_cursors == 1

    def test_does_not_commit(self):
        conn = FakeConn()
        run_statement(conn, _stmt("DROP TABLE t"))
        assert conn.commits == 0
