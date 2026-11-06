"""4.6.0 security sweep: values a caller, an archive or a config holds must not change the SQL IVG builds.

Each test hands one site an injection-shaped value and checks the statement still means what it meant: run against
SQLite where the fragment is plain SQL (its string literals quote as IRIS's do, `''` inside `'...'`), so the test
asserts the rows selected and not only the text.
"""
import json
import os
import sqlite3
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.embed_selector import EmbedSelector, build_edge_where, build_node_where
from iris_vector_graph.embedded import _inline_params

INJ = "x' OR '1'='1"


def _graph_db():
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH ':memory:' AS Graph_KG")
    db.execute("CREATE TABLE Graph_KG.nodes (node_id TEXT, graph_id TEXT)")
    db.execute("CREATE TABLE Graph_KG.rdf_labels (s TEXT, label TEXT, graph_id TEXT)")
    db.execute("CREATE TABLE Graph_KG.rdf_edges (s TEXT, p TEXT, o_id TEXT, graph_id TEXT)")
    db.execute("CREATE TABLE Graph_KG.kg_NodeEmbeddings (node_id TEXT, graph_id TEXT)")
    for nid, label in (("a", "Person"), ("b", "Drug"), ("c", "O'Hara")):
        db.execute("INSERT INTO Graph_KG.nodes VALUES (?, '')", [nid])
        db.execute("INSERT INTO Graph_KG.rdf_labels VALUES (?, ?, '')", [nid, label])
    db.execute("INSERT INTO Graph_KG.rdf_edges VALUES ('a', 'TREATS', 'b', '')")
    db.execute("INSERT INTO Graph_KG.rdf_edges VALUES ('c', 'it''s', 'b', '')")
    return db


def _nodes(sel, **kw):
    db = _graph_db()
    where = build_node_where(sel, **kw)
    return sorted(r[0] for r in db.execute(f"SELECT node_id FROM Graph_KG.nodes WHERE {where}"))


def _edges(sel, **kw):
    db = _graph_db()
    where = build_edge_where(sel, **kw)
    return sorted(r[:3] for r in db.execute(f"SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE {where}"))


class TestEmbedSelector:
    def test_label_literal_cannot_select_every_node(self):
        assert _nodes(EmbedSelector(label=INJ)) == []

    def test_label_with_a_quote_selects_its_nodes(self):
        assert _nodes(EmbedSelector(label="O'Hara")) == ["c"]

    def test_node_ids_cannot_widen_the_list(self):
        assert _nodes(EmbedSelector(node_ids=["a", "q') OR ('1'='1"])) == ["a"]

    def test_exclude_pattern_cannot_add_a_condition(self):
        # pasted, `NOT LIKE 'zz' AND '1'='0'` excluded every node
        assert _nodes(EmbedSelector(exclude_pattern="zz' AND '1'='0")) == ["a", "b", "c"]

    def test_graph_id_still_scopes(self):
        assert _nodes(EmbedSelector(label="Person"), graph_id="o'brien") == []
        assert _nodes(EmbedSelector(label="Person"), graph_id="") == ["a"]

    def test_edge_predicate_and_labels_cannot_select_every_edge(self):
        assert _edges(EmbedSelector(predicate=INJ)) == []
        assert _edges(EmbedSelector(source_label=INJ)) == []
        assert _edges(EmbedSelector(target_label=INJ)) == []

    def test_edge_predicate_with_a_quote_selects_its_edge(self):
        assert _edges(EmbedSelector(predicate="it's")) == [("c", "it's", "b")]
        assert _edges(EmbedSelector(source_label="O'Hara")) == [("c", "it's", "b")]


class TestEmbeddedInlineParams:
    """EmbeddedConnection pastes every bound value into the SQL; a `?` inside a string literal is not a marker."""

    def test_question_mark_inside_a_literal_is_left_alone(self):
        sql = _inline_params("SELECT 'what?' AS a FROM t WHERE b = ?", ["x OR 1=1 --"])
        assert sql == "SELECT 'what?' AS a FROM t WHERE b = 'x OR 1=1 --'"

    def test_doubled_quote_inside_a_literal_does_not_end_it(self):
        sql = _inline_params("SELECT 'it''s ?' AS a WHERE b = ?", ["v"])
        assert sql == "SELECT 'it''s ?' AS a WHERE b = 'v'"

    def test_question_mark_in_a_delimited_identifier_or_comment_is_left_alone(self):
        assert _inline_params('SELECT "a?" FROM t WHERE b = ?', [1]) == 'SELECT "a?" FROM t WHERE b = 1'
        assert _inline_params("SELECT 1 /* ? */ WHERE b = ? -- ?\n", [2]) == "SELECT 1 /* ? */ WHERE b = 2 -- ?\n"

    def test_answer_is_unchanged_under_sqlite(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE t (b TEXT)")
        db.execute("INSERT INTO t VALUES ('v'), ('w')")
        sql = _inline_params("SELECT 'what?', b FROM t WHERE b = ?", ["v' OR '1'='1"])
        assert db.execute(sql).fetchall() == []
        sql = _inline_params("SELECT 'what?', b FROM t WHERE b = ?", ["v"])
        assert db.execute(sql).fetchall() == [("what?", "v")]

    def test_too_few_params_still_raises(self):
        with pytest.raises(IndexError):
            _inline_params("SELECT ? , ?", [1])


# ── restore_snapshot: an archive is data, not SQL or a global name ────────────────────────────────────────────────


def _archive(sql_files, global_files=None, metadata=None):
    metadata = metadata or {"tables": {}, "globals": {}, "has_vector_sql": False}
    tmp = tempfile.NamedTemporaryFile(suffix=".ivgsnap", delete=False, dir=os.environ.get("TMPDIR"))
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as zf:
        zf.writestr("metadata.json", json.dumps(metadata))
        for k, v in sql_files.items():
            zf.writestr(k, v)
        for k, v in (global_files or {}).items():
            zf.writestr(k, v)
    return tmp.name


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = 0
    iris_obj.get.return_value = 0
    eng._iris_obj = MagicMock(return_value=iris_obj)
    return eng, cursor, iris_obj


def _sql_sent(cursor):
    return [c.args[0] for c in cursor.execute.call_args_list if c.args]


MARK = "pwn_marker"


class TestRestoreSnapshotArchiveValues:
    def _restore(self, sql_files, global_files=None, metadata=None, merge=True):
        path = _archive(sql_files, global_files, metadata)
        try:
            eng, cursor, iris_obj = _engine()
            result = eng.restore_snapshot(path, merge=merge)
            return result, _sql_sent(cursor), iris_obj
        finally:
            os.unlink(path)

    @pytest.mark.parametrize("merge", [True, False])
    def test_a_column_name_cannot_carry_sql(self, merge):
        row = {"node_id": "a", "graph_id": "", f"node_id) SELECT 1 {MARK} --": 1}
        result, sql, _ = self._restore({"sql/Graph_KG_nodes.ndjson": json.dumps(row)}, merge=merge)
        assert not [s for s in sql if MARK in s]
        assert result["failed_rows"].get("Graph_KG.nodes") == 1

    def test_an_ordinary_row_is_still_restored(self):
        row = {"node_id": "a", "graph_id": ""}
        result, sql, _ = self._restore({"sql/Graph_KG_nodes.ndjson": json.dumps(row)})
        assert result["restored_tables"]["Graph_KG.nodes"] == 1

    @pytest.mark.parametrize("merge", [True, False])
    def test_a_vector_is_bound_not_pasted(self, merge):
        emb = f"1,2,3,4', DOUBLE), 'x') -- {MARK}"
        line = json.dumps({"node_id": "a", "graph_id": "", "emb": emb, "metadata": None})
        _, sql, _ = self._restore({"sql/Graph_KG_kg_NodeEmbeddings.ndjson": line}, merge=merge)
        assert not [s for s in sql if MARK in s]

    @pytest.mark.parametrize("merge", [True, False])
    def test_an_edge_vector_is_bound_not_pasted(self, merge):
        emb = f"1,2,3,4', DOUBLE)) -- {MARK}"
        line = json.dumps({"s": "a", "p": "R", "o_id": "b", "graph_id": "", "emb": emb})
        _, sql, _ = self._restore({"sql/Graph_KG_kg_EdgeEmbeddings.ndjson": line}, merge=merge)
        assert not [s for s in sql if MARK in s]

    def test_a_routed_table_name_is_rebuilt_from_its_shape(self):
        short = "kg_emb_0123456789abcdef"
        evil = f"Graph_KG.kg_NodeEmbeddings {MARK} WHERE 1=1 --"
        routed = f"{evil.split('.')[0]} {MARK}.{short}"
        metadata = {
            "tables": {},
            "globals": {},
            "routed_embedding_tables": {routed: {"graph_id": "g", "dimension": 4, "dtype": "DOUBLE"}},
        }
        line = json.dumps({"node_id": "a", "graph_id": "g", "emb": "1,2,3,4", "metadata": None})
        fname = f"sql/{routed.replace('Graph_KG.', 'Graph_KG_')}.ndjson"
        _, sql, _ = self._restore({fname: line}, metadata=metadata)
        assert not [s for s in sql if MARK in s]

    def test_a_global_ivg_never_exports_is_not_written(self):
        entry = json.dumps({"k": ["x"], "v": "1"})
        result, _, iris_obj = self._restore(
            {}, global_files={"globals/ROUTINE.ndjson": entry, "globals/KG.ndjson": entry}
        )
        written = {c.args[1] for c in iris_obj.set.call_args_list}
        assert "^ROUTINE" not in written
        assert "^KG" in written
        assert result["failed_rows"].get("^ROUTINE") == 1
        assert "ROUTINE" not in result["restored_globals"]


# ── smaller sites: a caller's identifiers and counts ─────────────────────────────────────────────────────────────


class TestAttachEmbeddingsToTable:
    def test_text_columns_must_be_identifiers(self):
        eng, cursor, _ = _engine()
        eng.get_table_mapping = MagicMock(
            return_value={"sql_table": "Clinic.Patient", "id_column": "ID", "prop_columns": None}
        )
        with pytest.raises(ValueError):
            eng.attach_embeddings_to_table("Patient", ["Name FROM Security.Users --"])
        assert not [s for s in _sql_sent(cursor) if "Security.Users" in s]


class TestTopCounts:
    def test_text_search_k_and_table_must_be_what_they_say(self):
        from iris_vector_graph.text_search import TextSearchEngine

        conn = MagicMock()
        ts = TextSearchEngine(conn)
        with pytest.raises((ValueError, TypeError)):
            ts.search_documents("q", k="1 * FROM Security.Users --")
        with pytest.raises(ValueError):
            ts.search_documents("q", table_name="docs WHERE 1=1 --")
        sent = [c.args[0] for c in conn.cursor.return_value.execute.call_args_list if c.args]
        assert not [s for s in sent if "Security.Users" in s or "1=1" in s]

    def test_kg_txt_like_fallback_k_is_a_number(self):
        eng, cursor, _ = _engine()
        sent = []

        def execute(sql, params=None):
            sent.append(sql)
            if "%FIND" in sql or "%ID %FIND" in sql or "search_index" in sql.lower():
                raise RuntimeError("SQLCODE -359 no %FIND")

        cursor.execute.side_effect = execute
        with pytest.raises((ValueError, TypeError)):
            eng.kg_TXT("aspirin", k=f"1 d.id, 1 FROM Security.Users {MARK} --")
        assert not [s for s in sent if MARK in s]


class TestVectorDtype:
    def test_vector_dtype_is_an_element_type(self):
        from iris_vector_graph.engine import IRISGraphEngine

        with pytest.raises(ValueError):
            IRISGraphEngine(MagicMock(), vector_dtype="DOUBLE), 'x') --")
        assert IRISGraphEngine(MagicMock(), vector_dtype="float").vector_dtype == "FLOAT"


class TestFetchFirstProbe:
    def test_connection_params_are_not_pasted_into_the_probe_script(self):
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine(MagicMock())
        eng._connection_params = {"hostname": "h", "password": "p'''+__import__('os').getpid()+r'''"}
        done = MagicMock(returncode=0, stdout="OK\nDROPPED\n", stderr="")
        with patch("subprocess.run", return_value=done) as run:
            eng._detect_fetch_first_unsafe()
        script = run.call_args.args[0][2]
        assert "__import__('os')" not in script
        assert json.loads(run.call_args.kwargs["input"]) == eng._connection_params


class TestStoredVectorText:
    def test_store_embedding_vector_is_bound(self):
        eng, cursor, _ = _engine()
        eng._route_for_write = MagicMock(return_value=("kg_NodeEmbeddings", None))
        eng.enforce_embedding_identity = MagicMock()
        eng._route_width = MagicMock(return_value=4)
        eng._assert_node_exists = MagicMock()
        try:
            eng.store_embedding("a", [0.1, 0.2, 0.3, f"0.4', DOUBLE), 'x') -- {MARK}"])
        except Exception:
            pass
        assert not [s for s in _sql_sent(cursor) if MARK in s]


    def test_store_embedding_dtype_is_an_element_type(self):
        eng, cursor, _ = _engine()
        eng._route_for_write = MagicMock(return_value=("kg_NodeEmbeddings", None))
        eng.enforce_embedding_identity = MagicMock()
        eng._route_width = MagicMock(return_value=4)
        eng._assert_node_exists = MagicMock()
        with pytest.raises(ValueError):
            eng.store_embedding("a", [0.1, 0.2, 0.3, 0.4], dtype=f"DOUBLE), 'x') -- {MARK}")
        assert not [s for s in _sql_sent(cursor) if MARK in s]


class TestPinnedStageColumns:
    def test_a_column_name_with_a_double_quote_stays_one_identifier(self):
        from iris_vector_graph.stores.iris_sql_store import _pinned_stage_sql

        sql = _pinned_stage_sql(['a" FROM x --'], [])
        assert '"a"" FROM x --"' in sql
        sql = _pinned_stage_sql(['a" FROM x --'], [[1]])
        assert '"a"" FROM x --"' in sql
