"""Undefined names, three of them inside handlers that hide the `NameError` (T081).

`ruff check --select F821 iris_vector_graph/` found five distinct undefined names
in library code. Two are quoted annotations that never evaluate; three sit in live
code paths wrapped in `except Exception` handlers that log and continue, so the
`NameError` reads as "the fast path was unavailable" rather than "this line cannot
run at all":

- `_BULK_CHUNK_SIZE` in `_engine/nodes_edges.py` is defined only in `engine.py`.
  Both ObjectScript bulk-ingest fast paths raise on their first loop and their
  handlers warn "falling back to SQL path", so every deployed container has been
  silently taking the row-at-a-time path.
- `Path` in `_engine/schema.py` is never imported, so `initialize_schema(
  auto_deploy_objectscript=True)` raises before it can deploy anything and logs
  the miss at DEBUG as "expected in Docker".
- `cur` in `_engine/vector.py` is never opened, so the SQL fallback that lists
  IVF/BM25/PLAID indexes when the ObjectScript listing fails discovers nothing.

The other two (`Union`/`Index` in `_engine/vector.py`, `SyncReport` in
`_engine/admin.py`, plus `rdflib` which stays a type-checking-only name) are
annotation-only: inert until something calls `typing.get_type_hints` on the
method, which is why they are pinned here rather than deleted.

These tests assert the paths run, not that they succeed against a real IRIS —
the point is that the code reaches its own logic instead of dying on a name.
"""

from __future__ import annotations

import logging
import sys
import types
import typing
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(dim: int = 4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("node_id", None)]
    engine = IRISGraphEngine(conn, embedding_dimension=dim)
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return engine, cursor


class TestBulkChunkSize:
    """The ObjectScript bulk fast paths must chunk, not raise `NameError`."""

    def test_bulk_create_nodes_uses_the_objectscript_path(self, caplog):
        engine, _cursor = _engine()
        engine.capabilities.objectscript_deployed = True
        engine._iris_obj = MagicMock(return_value=MagicMock())

        nodes = [{"id": f"n{i}", "labels": ["L"], "properties": {}} for i in range(3)]

        with caplog.at_level(logging.WARNING):
            with patch("iris_vector_graph.schema._call_classmethod_large", return_value=3) as large:
                created = engine.bulk_create_nodes(nodes)

        assert created == ["n0", "n1", "n2"]
        assert large.call_count == 1
        assert "falling back to SQL path" not in caplog.text

    def test_bulk_ingest_edges_uses_the_objectscript_path(self, caplog):
        engine, _cursor = _engine()
        engine.capabilities.objectscript_deployed = True
        engine._iris_obj = MagicMock(return_value=MagicMock())

        edges = [("a", "b"), ("b", "c")]

        with caplog.at_level(logging.WARNING):
            with patch("iris_vector_graph.schema._call_classmethod_large", return_value=2) as large:
                n = engine.bulk_ingest_edges(edges, predicate="rel", auto_sync=False)

        assert n == 2
        assert large.call_count == 1
        assert "falling back to SQL path" not in caplog.text

    def test_a_batch_larger_than_one_chunk_is_split(self):
        engine, _cursor = _engine()
        engine.capabilities.objectscript_deployed = True
        engine._iris_obj = MagicMock(return_value=MagicMock())

        from iris_vector_graph.constants import BULK_CHUNK_SIZE

        total = BULK_CHUNK_SIZE + 5
        nodes = [{"id": f"n{i}", "labels": [], "properties": {}} for i in range(total)]

        with patch("iris_vector_graph.schema._call_classmethod_large") as large:
            large.side_effect = lambda *a, **k: len(__import__("json").loads(a[3]))
            created = engine.bulk_create_nodes(nodes)

        assert large.call_count == 2
        assert len(created) == total


class TestAutoDeployPath:
    """`auto_deploy_objectscript=True` must reach the deploy call."""

    def test_initialize_schema_reaches_deploy_objectscript_classes(self):
        engine, cursor = _engine()
        cursor.execute.side_effect = None

        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=""):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch(
                    "iris_vector_graph.schema.GraphSchema.get_procedures_sql_list",
                    return_value=[],
                ):
                    with patch(
                        "iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                        return_value=MagicMock(objectscript_deployed=False, kg_built=False),
                    ) as deploy:
                        engine.initialize_schema(auto_deploy_objectscript=True)

        assert deploy.call_count == 1, "deploy never ran — the path expression raised"
        pkg_dir = deploy.call_args.args[1]
        assert str(pkg_dir).endswith("iris_src")


class TestIndexRegistrySQLFallback:
    """When the ObjectScript listing fails, the SQL listing must actually run."""

    def test_sql_fallback_discovers_index_names(self, monkeypatch):
        engine, cursor = _engine()

        # No `iris.gref`: force the ObjectScript/SQL fallback chain.
        monkeypatch.setitem(sys.modules, "iris", types.ModuleType("iris"))

        def _boom(*_a, **_kw):
            raise RuntimeError("ObjectScript listing unavailable")

        monkeypatch.setattr("iris_vector_graph.schema._call_classmethod", _boom)
        monkeypatch.setattr(type(engine), "_probe_native_vec", lambda self: False)

        rows = {
            "ivf_indexes": [("ivf_a",)],
            "bm25_indexes": [("bm25_a",)],
            "plaid_indexes": [("plaid_a",)],
        }
        state: dict[str, list] = {"last": []}

        def _execute(sql, *_a, **_kw):
            state["last"] = next((v for table, v in rows.items() if table in str(sql)), [])

        cursor.execute.side_effect = _execute
        cursor.fetchall.side_effect = lambda: state["last"]

        registry = engine._build_index_registry()

        assert registry == {"ivf_a": "ivf", "bm25_a": "bm25", "plaid_a": "plaid"}


class TestAnnotationsResolve:
    """Quoted annotations must name something importable."""

    @pytest.mark.parametrize(
        "method", ["index", "create_index", "list_indexes", "search_nodes_by_vector"]
    )
    def test_get_type_hints_resolves(self, method):
        from iris_vector_graph._engine.vector import VectorMixin

        typing.get_type_hints(getattr(VectorMixin, method))

    def test_verify_sync_return_annotation_resolves(self):
        from iris_vector_graph._engine.admin import AdminMixin

        hints = typing.get_type_hints(AdminMixin.verify_sync)
        from iris_vector_graph.status import SyncReport

        assert hints["return"] is SyncReport

    def test_shapes_graph_annotation_names_a_real_rdflib_type(self):
        """`rdflib` stays an optional dependency, so the name is only for type checkers.

        Importing it eagerly to make the annotation resolvable at runtime would put a
        heavyweight optional import on every `import iris_vector_graph`. Supplying it
        here checks the claim that matters: `rdflib.Graph` is a real type.
        """
        rdflib = pytest.importorskip("rdflib")
        from iris_vector_graph._engine import shacl

        hints = typing.get_type_hints(shacl._load_shapes_graph, localns={"rdflib": rdflib})
        assert hints["return"] is rdflib.Graph
