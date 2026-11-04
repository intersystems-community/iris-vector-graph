"""R213: the bulk and loader entry points take the shapes their docstrings state, and refuse any other one.

Walkthrough 2 (B1): ``bulk_load_session().add_edges([{"source_id": "ann", "predicate": "WORKS_AT", "target_id":
"acme2"}])`` returned 0, wrote nothing and raised nothing, because ``bulk_ingest_edges`` read only ``s``/``source`` and
``o``/``target`` and dropped an edge with neither. The same quiet zero was in ``bulk_create_edges`` (a dict missing a key
was skipped), ``bulk_create_nodes`` (a node without ``id``), ``bulk_create_edges_temporal`` (a missing endpoint became
``""``), ``BulkLoader.load_edges`` / ``load_nodes`` (a dict unpacked into its key names) and ``import_graph_ndjson``
(an edge line without ``source``/``predicate``/``target`` was skipped without a word).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(deployed=True):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    cur.rowcount = 1
    conn.cursor.return_value = cur
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng._nkg_dirty = False
    caps = MagicMock()
    caps.objectscript_deployed = deployed
    eng.capabilities = caps  # _known_capabilities reads the same attribute
    eng._iris_obj = MagicMock(return_value=MagicMock())
    eng.sync = MagicMock()
    eng.__dict__["_ledger_guard_obj"] = MagicMock()  # no ledger probe: the cursor sees only the write path
    return eng, cur


def _sent_edges(call_mock):
    """The edges the server-side loader was handed, chunk by chunk."""
    out = []
    for c in call_mock.call_args_list:
        out.extend(json.loads(c.args[3]))
    return out


class TestBulkIngestEdgesShapes:
    def test_source_id_predicate_target_id_dicts_are_written(self):
        eng, _ = _engine()
        with patch("iris_vector_graph.schema._call_classmethod_large", return_value=2) as call:
            n = eng.bulk_ingest_edges(
                [
                    {"source_id": "ann", "predicate": "WORKS_AT", "target_id": "acme2"},
                    {"source_id": "ben", "target_id": "acme2"},
                ],
                predicate="KNOWS",
                auto_sync=False,
            )
        assert n == 2
        assert _sent_edges(call) == [
            {"s": "ann", "p": "WORKS_AT", "o": "acme2"},
            {"s": "ben", "p": "KNOWS", "o": "acme2"},
        ]

    def test_the_other_documented_shapes_still_load(self):
        eng, _ = _engine()
        with patch("iris_vector_graph.schema._call_classmethod_large", return_value=4) as call:
            eng.bulk_ingest_edges(
                [("a", "b"), ("a", "c", "LIKES"), {"s": "d", "p": "R", "o": "e"}, {"source": "f", "target": "g"}],
                predicate="KNOWS",
                auto_sync=False,
            )
        assert _sent_edges(call) == [
            {"s": "a", "p": "KNOWS", "o": "b"},
            {"s": "a", "p": "LIKES", "o": "c"},
            {"s": "d", "p": "R", "o": "e"},
            {"s": "f", "p": "KNOWS", "o": "g"},
        ]

    @pytest.mark.parametrize(
        "edge, words",
        [
            ({"from": "a", "to": "b"}, "'from'"),
            ({"source_id": "a", "predicate": "R"}, "target"),
            ({"predicate": "R", "target_id": "b"}, "source"),
            ({"s": "", "o": "b"}, "source"),
            (("a",), "2 or 3"),
            (("a", "b", "R", "x"), "2 or 3"),
            ("a->b", "tuple"),
            ({"s": "a", "o": "b", "weight": 2.0}, "'weight'"),
            ({"s": "a", "o": "b", "graph": "proj"}, "default graph"),
        ],
    )
    def test_an_unknown_shape_raises_before_anything_is_written(self, edge, words):
        eng, cur = _engine()
        with patch("iris_vector_graph.schema._call_classmethod_large") as call:
            with pytest.raises(ValueError, match=words):
                eng.bulk_ingest_edges([("ok1", "ok2"), edge], auto_sync=False)
        call.assert_not_called()
        cur.execute.assert_not_called()

    def test_the_message_names_the_edge_and_the_accepted_shapes(self):
        eng, _ = _engine()
        with patch("iris_vector_graph.schema._call_classmethod_large"):
            with pytest.raises(ValueError) as exc:
                eng.bulk_ingest_edges([("a", "b"), {"src": "a", "dst": "b"}], auto_sync=False)
        msg = str(exc.value)
        assert "edge 1" in msg and "source_id" in msg and "target_id" in msg

    def test_the_sql_path_takes_the_same_shapes(self):
        eng, cur = _engine(deployed=False)
        eng._ensure_edge_endpoints = MagicMock()
        eng._ekey_guard = MagicMock(return_value="")
        eng._adjacency_indexed_cached = True
        n = eng.bulk_ingest_edges(
            [{"source_id": "ann", "predicate": "WORKS_AT", "target_id": "acme2"}], auto_sync=False
        )
        assert n == 1
        sql, params = cur.execute.call_args.args
        assert params[:3] == ["ann", "WORKS_AT", "acme2"]


class TestSessionAddEdges:
    def test_add_edges_takes_the_source_id_shape(self):
        eng, _ = _engine()
        eng._ledger_active = False
        from iris_vector_graph._engine.nodes_edges import _BulkLoadSession

        stats = {"nodes": 0, "edges": 0}
        session = _BulkLoadSession(eng, stats, max_retries=0)
        with patch("iris_vector_graph.schema._call_classmethod_large", return_value=1) as call:
            n = session.add_edges([{"source_id": "ann", "predicate": "WORKS_AT", "target_id": "acme2"}])
        assert n == 1 and stats["edges"] == 1
        assert _sent_edges(call) == [{"s": "ann", "p": "WORKS_AT", "o": "acme2"}]

    def test_add_edges_refuses_an_unknown_shape(self):
        eng, _ = _engine()
        from iris_vector_graph._engine.nodes_edges import _BulkLoadSession

        stats = {"nodes": 0, "edges": 0}
        session = _BulkLoadSession(eng, stats, max_retries=0)
        with patch("iris_vector_graph.schema._call_classmethod_large") as call:
            with pytest.raises(ValueError):
                session.add_edges([{"subject": "ann", "object": "acme2"}])
        call.assert_not_called()
        assert stats["edges"] == 0


class TestBulkCreateEdgesShapes:
    @pytest.mark.parametrize(
        "edge, words",
        [
            ({"source_id": "a", "predicate": "R"}, "target_id"),
            ({"s": "a", "p": "R", "o": "b"}, "source_id"),
            ({"source_id": "a", "predicate": "R", "target_id": "b", "weight": 5.0}, "'weight'"),
            ({"source_id": "a", "predicate": "R", "target_id": "b", "qualifiers": {"w": 1}}, "'qualifiers'"),
            (("a", "R", "b"), "dict"),
        ],
    )
    def test_an_edge_it_cannot_write_raises_before_any_sql(self, edge, words):
        eng, cur = _engine(deployed=False)
        eng._ensure_edge_endpoints = MagicMock()
        ok = {"source_id": "x", "predicate": "R", "target_id": "y"}
        with pytest.raises(ValueError, match=words):
            eng.bulk_create_edges([ok, edge], auto_sync=False)
        cur.execute.assert_not_called()
        cur.executemany.assert_not_called()
        eng._ensure_edge_endpoints.assert_not_called()

    def test_graph_key_is_accepted(self):
        eng, cur = _engine(deployed=False)
        eng._ensure_edge_endpoints = MagicMock()
        n = eng.bulk_create_edges(
            [{"source_id": "a", "predicate": "R", "target_id": "b", "graph": "proj"}], auto_sync=False
        )
        assert n == 1


class TestBulkCreateNodesShapes:
    @pytest.mark.parametrize(
        "node, words",
        [
            ({"node_id": "a"}, "'id'"),
            ({"labels": ["P"]}, "'id'"),
            ({"id": "a", "label": "P"}, "'label'"),
            ("a", "dict"),
        ],
    )
    def test_a_node_it_cannot_write_raises_before_any_write(self, node, words):
        eng, cur = _engine(deployed=True)
        with patch("iris_vector_graph.schema._call_classmethod_large") as call:
            with pytest.raises(ValueError, match=words):
                eng.bulk_create_nodes([{"id": "ok"}, node])
        call.assert_not_called()
        cur.executemany.assert_not_called()


class TestBulkCreateEdgesTemporalShapes:
    @pytest.mark.parametrize(
        "edge, words",
        [
            ({"p": "R", "o": "b", "ts": 1}, "source"),
            ({"s": "a", "o": "b", "ts": 1}, "predicate"),
            ({"s": "a", "p": "R", "ts": 1}, "target"),
            ({"s": "a", "p": "R", "o": "b"}, "timestamp"),
            ({"s": "a", "p": "R", "o": "b", "ts": 1, "value": 3}, "'value'"),
        ],
    )
    def test_an_edge_it_cannot_write_raises_before_the_server_call(self, edge, words):
        eng, _ = _engine()
        eng._store = MagicMock()
        ok = {"s": "x", "p": "R", "o": "y", "ts": 5}
        with pytest.raises(ValueError, match=words):
            eng.bulk_create_edges_temporal([ok, edge])
        eng._store.bulk_write_temporal_edges.assert_not_called()

    def test_source_id_and_target_id_are_accepted(self):
        eng, _ = _engine()
        eng._store = MagicMock()
        eng._store.bulk_write_temporal_edges.return_value = MagicMock(rows=[[1, 0, 0]], error=None)
        eng.bulk_create_edges_temporal([{"source_id": "a", "predicate": "R", "target_id": "b", "timestamp": 7}])
        sent = eng._store.bulk_write_temporal_edges.call_args.args[0]
        assert [(e["source"], e["predicate"], e["target"], e["timestamp"]) for e in sent] == [("a", "R", "b", 7)]


class TestBulkLoaderShapes:
    def _loader(self):
        from iris_vector_graph.bulk_loader import BulkLoader

        conn = MagicMock()
        conn.cursor.return_value.fetchall.return_value = []
        return BulkLoader(conn), conn

    def test_load_edges_refuses_a_dict(self):
        loader, conn = self._loader()
        edge = {"source_id": "a", "predicate": "R", "target_id": "b", "qualifiers": None}
        with pytest.raises(ValueError, match="tuple"):
            loader.load_edges([("x", "R", "y", None), edge])
        conn.cursor.return_value.executemany.assert_not_called()

    def test_load_edges_refuses_a_three_tuple(self):
        loader, conn = self._loader()
        with pytest.raises(ValueError, match="4"):
            loader.load_edges([("a", "R", "b")])

    def test_load_nodes_refuses_a_dict(self):
        loader, conn = self._loader()
        with pytest.raises(ValueError, match="tuple"):
            loader.load_nodes([("x", {}), {"id": "a", "labels": ["P"]}])
        conn.cursor.return_value.executemany.assert_not_called()


class TestImportNdjsonShapes:
    def test_edge_line_with_source_id_keys_is_written(self, tmp_path):
        eng, _ = _engine()
        eng.create_edge = MagicMock(return_value=True)
        eng.create_node = MagicMock(return_value=True)
        p = tmp_path / "g.ndjson"
        p.write_text(json.dumps({"kind": "edge", "source_id": "a", "predicate": "R", "target_id": "b"}) + "\n")
        out = eng.import_graph_ndjson(str(p))
        assert out["edges"] == 1
        eng.create_edge.assert_called_once()
        assert eng.create_edge.call_args.args[:3] == ("a", "R", "b")

    def test_an_edge_line_it_cannot_write_is_counted_and_logged(self, tmp_path, caplog):
        eng, _ = _engine()
        eng.create_edge = MagicMock(return_value=True)
        p = tmp_path / "g.ndjson"
        p.write_text(json.dumps({"kind": "edge", "from": "a", "to": "b"}) + "\n")
        with caplog.at_level("WARNING"):
            out = eng.import_graph_ndjson(str(p))
        assert out["edges"] == 0 and out["skipped"] == 1
        assert "line 1" in caplog.text
        eng.create_edge.assert_not_called()
