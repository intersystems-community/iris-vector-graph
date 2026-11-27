"""4.7.2: an index setup could not create is named in the reply and at WARNING.

On IRIS 2026.1 no expression index is accepted in SQL DDL, so ``idx_edges_confidence`` (``JSON_VALUE(qualifiers,
'$.confidence')``) is refused with SQLCODE -1 and setup carried on: the skip was a DEBUG line, the
``initialize_schema()`` reply had no warning, and no key named the index. Now:

- ``GraphSchema.ensure_indexes(cursor, skipped=[...])`` appends ``{index, table, reason}`` for every index it could not
  create (the reason is the server's error on one line); an index that exists, or that the ALTER TABLE fallback
  created, is not skipped;
- ``initialize_schema()``'s reply carries them under ``skipped_indexes``, and the log gets one WARNING per index per
  run saying which queries run without it.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

# the text IRIS 2026.1.0 answers (repro of 2026-11-26), with its line breaks
KEYS_EXPECTED = (
    "<SQL ERROR>; Details: [SQLCODE: <-1>:<Invalid SQL statement>]\n"
    "[Location: <Prepare>]\n"
    "[%msg: < KEYS expected, IDENTIFIER (qualifiers) found ^CREATE INDEX idx_edges_confidence ON "
    "Graph_KG.rdf_edges(JSON_VALUE(qualifiers>]"
)
CONFIDENCE = "idx_edges_confidence"
ENGINE_LOGGER = "iris_vector_graph._engine.schema"


class _Cursor:
    """Executes everything except the statements `refuse` names (by index name), which raise `error`."""

    def __init__(self, refuse=(), error=KEYS_EXPECTED, alter_refuses=False):
        self.refuse = set(refuse)
        self.error = error
        self.alter_refuses = alter_refuses
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith("CREATE INDEX") and any(f" {name} " in sql for name in self.refuse):
            raise RuntimeError(self.error)
        if self.alter_refuses and sql.startswith("ALTER TABLE") and "ADD" in sql:
            raise RuntimeError("ALTER refused")

    def fetchone(self):
        return None

    def fetchall(self):
        return []


@pytest.fixture
def migrations():
    """The migrations ensure_indexes runs after its loop, answered; the index functions under test stay real."""
    with patch.object(GraphSchema, "upgrade_val_column", return_value=True), \
         patch.object(GraphSchema, "add_graph_id_column", return_value=True), \
         patch.object(GraphSchema, "tighten_graph_id_column", return_value=True), \
         patch.object(GraphSchema, "_spo_constraint_status", return_value={}), \
         patch.object(GraphSchema, "add_graph_id_to_nodes", return_value=True), \
         patch.object(GraphSchema, "ensure_props_value_key", return_value={}), \
         patch.object(GraphSchema, "ensure_adjacency_index", return_value={}):
        yield


# ---------------------------------------------------------------------------
# ensure_indexes reports what it skipped
# ---------------------------------------------------------------------------


def test_the_2026_1_refusal_is_reported_with_index_table_and_one_line_reason(migrations):
    skipped: list = []
    status = GraphSchema.ensure_indexes(_Cursor(refuse=[CONFIDENCE]), skipped=skipped)

    assert status[CONFIDENCE] is False
    assert [s["index"] for s in skipped] == [CONFIDENCE]
    entry = skipped[0]
    assert set(entry) == {"index", "table", "reason"}
    assert entry["table"] == "Graph_KG.rdf_edges"
    assert "KEYS expected, IDENTIFIER (qualifiers) found" in entry["reason"]
    assert "SQLCODE: <-1>" in entry["reason"]
    assert "\n" not in entry["reason"]
    assert len(entry["reason"]) <= 300


def test_nothing_is_reported_when_every_index_is_created(migrations):
    skipped: list = []
    GraphSchema.ensure_indexes(_Cursor(), skipped=skipped)
    assert skipped == []


def test_an_index_that_already_exists_is_not_skipped(migrations):
    skipped: list = []
    status = GraphSchema.ensure_indexes(
        _Cursor(refuse=[CONFIDENCE], error="[SQLCODE: <-324>:<Index already exists>]"), skipped=skipped
    )
    assert status[CONFIDENCE] is True
    assert skipped == []


@pytest.mark.parametrize("name", ["idx_props_val_ifind", "idx_docs_text_ifind"])
def test_the_ifind_indexes_are_reported_too(migrations, name):
    skipped: list = []
    GraphSchema.ensure_indexes(
        _Cursor(refuse=[name], error="<SQL ERROR>\n[%msg: <%iFind.Index.Basic not available>]"), skipped=skipped
    )
    assert [s["index"] for s in skipped] == [name]
    assert skipped[0]["table"] in ("Graph_KG.rdf_props", "Graph_KG.docs")


def test_a_required_index_that_fails_is_reported_not_swallowed(migrations):
    """A btree index is not optional, and a failure of one was recorded as False and nothing else."""
    skipped: list = []
    status = GraphSchema.ensure_indexes(_Cursor(refuse=["idx_labels_s"], error="[SQLCODE: <-400>] boom"),
                                        skipped=skipped)
    assert status["idx_labels_s"] is False
    assert "idx_labels_s" in [s["index"] for s in skipped]


def test_the_alter_table_fallback_that_works_is_not_a_skip(migrations):
    skipped: list = []
    status = GraphSchema.ensure_indexes(
        _Cursor(refuse=["idx_edges_s_p"], error="[SQLCODE: <-400>:<Fatal error>] rdf_edges"), skipped=skipped
    )
    assert status["idx_edges_s_p"] is True
    assert skipped == []


def test_an_alter_table_fallback_that_fails_is_a_skip(migrations):
    skipped: list = []
    cursor = _Cursor(refuse=["idx_edges_s_p"], error="[SQLCODE: <-400>:<Fatal error>] rdf_edges", alter_refuses=True)
    status = GraphSchema.ensure_indexes(cursor, skipped=skipped)
    assert status["idx_edges_s_p"] is False
    assert [s["index"] for s in skipped] == ["idx_edges_s_p"]
    assert "ALTER refused" in skipped[0]["reason"]


def test_the_problematic_index_drop_is_not_an_index_setup_creates(migrations):
    skipped: list = []

    class _DropRefuses(_Cursor):
        def execute(self, sql, params=None):
            if sql.startswith("DROP INDEX"):
                raise RuntimeError("index does not exist")
            super().execute(sql, params)

    GraphSchema.ensure_indexes(_DropRefuses(), skipped=skipped)
    assert skipped == []


def test_the_graph_id_indexes_report_their_refusal():
    skipped: list = []

    class _Refuses(_Cursor):
        def execute(self, sql, params=None):
            raise RuntimeError("[SQLCODE: <-30>] no graph_id column")

    assert GraphSchema.add_graph_id_index(_Refuses(), skipped=skipped) is False
    assert GraphSchema.add_graph_oid_index(_Refuses(), skipped=skipped) is False
    assert [s["index"] for s in skipped] == ["idx_edges_graph_id", "idx_edges_graph_oid"]
    assert all(s["table"] == "Graph_KG.rdf_edges" and "no graph_id column" in s["reason"] for s in skipped)


def test_ensure_indexes_still_works_without_the_out_argument(migrations):
    status = GraphSchema.ensure_indexes(_Cursor(refuse=[CONFIDENCE]))
    assert status[CONFIDENCE] is False


# ---------------------------------------------------------------------------
# the reply and the log
# ---------------------------------------------------------------------------


class _Setup:
    """A connection that refuses `idx_edges_confidence` wherever setup asks for it, the base DDL included."""

    def __init__(self):
        self.cursor_ = MagicMock()
        self.cursor_.fetchone.return_value = (0,)
        self.cursor_.fetchall.return_value = []
        self.sql = []

        def execute(sql, params=None):
            self.sql.append(sql)
            if CONFIDENCE in sql and sql.lstrip().startswith("CREATE INDEX"):
                raise RuntimeError(KEYS_EXPECTED)

        self.cursor_.execute.side_effect = execute

    def cursor(self):
        return self.cursor_

    def commit(self):
        pass


def _run_setup(conn=None, schema_prefix=None):
    conn = conn or _Setup()
    eng = IRISGraphEngine(conn, embedding_dimension=4, schema_prefix=schema_prefix)
    with patch.object(GraphSchema, "get_embedding_dimension", return_value=4), \
         patch.object(GraphSchema, "get_procedures_sql_list", return_value=[]), \
         patch.object(GraphSchema, "upgrade_val_column", return_value=True), \
         patch.object(GraphSchema, "add_graph_id_column", return_value=True), \
         patch.object(GraphSchema, "tighten_graph_id_column", return_value=True), \
         patch.object(GraphSchema, "_spo_constraint_status", return_value={}), \
         patch.object(GraphSchema, "add_graph_id_to_nodes", return_value=True), \
         patch.object(GraphSchema, "ensure_props_value_key", return_value={}), \
         patch.object(GraphSchema, "ensure_adjacency_index", return_value={}):
        return eng, eng._initialize_schema(auto_deploy_objectscript=False)


def test_the_reply_names_the_skipped_index():
    _, status = _run_setup()
    assert [s["index"] for s in status["skipped_indexes"]] == [CONFIDENCE]  # once, though two stages asked for it
    assert set(status["skipped_indexes"][0]) == {"index", "table", "reason"}
    assert "KEYS expected" in status["skipped_indexes"][0]["reason"]


def test_a_setup_that_skips_nothing_replies_an_empty_list():
    conn = _Setup()
    conn.cursor_.execute.side_effect = lambda sql, params=None: None
    _, status = _run_setup(conn)
    assert status["skipped_indexes"] == []


def test_one_warning_per_index_per_run_naming_the_index_and_what_runs_without_it(caplog):
    with caplog.at_level(logging.INFO, logger="iris_vector_graph"):
        _run_setup()
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and CONFIDENCE in r.getMessage()]
    assert len(lines) == 1, lines
    assert "confidence" in lines[0] and "qualifiers" in lines[0]
    assert "KEYS expected" in lines[0]
    assert "scan" in lines[0]  # which queries run without it: they scan rdf_edges


def test_a_second_run_warns_again_once(caplog):
    with caplog.at_level(logging.INFO, logger="iris_vector_graph"):
        _run_setup()
        caplog.clear()
        _run_setup()
    lines = [r for r in caplog.records if r.levelno == logging.WARNING and CONFIDENCE in r.getMessage()]
    assert len(lines) == 1


def test_a_non_default_schema_reports_the_table_in_that_schema():
    _, status = _run_setup(schema_prefix="RAG")
    entry = [s for s in status["skipped_indexes"] if s["index"] == CONFIDENCE][0]
    assert entry["table"] == "RAG.rdf_edges"
