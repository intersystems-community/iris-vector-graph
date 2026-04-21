import json
import os
import time
import uuid
import zipfile

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

SQL_TABLES = [
    "Graph_KG.nodes",
    "Graph_KG.rdf_edges",
    "Graph_KG.rdf_labels",
    "Graph_KG.rdf_props",
]


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSnapshotE2E:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection)
        self._run = uuid.uuid4().hex[:8]
        self._snapshot_paths = []
        yield
        self._cleanup()

    def _n(self, label):
        return f"snap_{label}_{self._run}"

    def _cleanup(self):
        cursor = self.conn.cursor()
        prefix = f"snap_%_{self._run}"
        for table in [
            "Graph_KG.rdf_edges",
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.nodes",
        ]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        try:
            self.conn.commit()
        except Exception:
            pass
        for path in self._snapshot_paths:
            try:
                os.unlink(path)
            except Exception:
                pass

    def _snapshot_path(self):
        path = f"/tmp/test_snapshot_{self._run}.ivg"
        self._snapshot_paths.append(path)
        return path

    def _count(self, table, where_col, prefix):
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where_col} LIKE ?", [prefix]
        )
        return cursor.fetchone()[0]

    def test_save_restore_roundtrip(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for n in [a, b, c]:
            self.engine.create_node(n)
        self.engine.create_edge(a, "R", b)
        self.engine.create_edge(b, "R", c)
        prefix = f"snap_%_{self._run}"
        nodes_before = self._count("Graph_KG.nodes", "node_id", prefix)
        edges_before = self._count("Graph_KG.rdf_edges", "s", prefix)
        assert nodes_before == 3
        assert edges_before == 2
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        cursor = self.conn.cursor()
        for table in [
            "Graph_KG.rdf_edges",
            "Graph_KG.rdf_labels",
            "Graph_KG.rdf_props",
            "Graph_KG.nodes",
        ]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        self.conn.commit()
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 0
        result = self.engine.restore_snapshot(path)
        assert result["restored_tables"]["Graph_KG.nodes"] >= 3
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 3
        assert self._count("Graph_KG.rdf_edges", "s", prefix) == 2

    def test_snapshot_info_staticmethod(self):
        from iris_vector_graph.engine import IRISGraphEngine

        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        info = IRISGraphEngine.snapshot_info(path)
        assert isinstance(info, dict)
        assert "metadata" in info or "tables" in info or "version" in info
        assert info.get("tables", {}).get("Graph_KG.nodes", 0) >= 1

    def test_restore_is_destructive_by_default(self):
        a, b = self._n("A"), self._n("B")
        extra = self._n("EXTRA")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        self.engine.create_node(extra)
        prefix = f"snap_%_{self._run}"
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 3
        self.engine.restore_snapshot(path)
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 2, (
            "Destructive restore should remove the extra node added after snapshot"
        )

    def test_restore_merge_preserves_local(self):
        a, b = self._n("A"), self._n("B")
        extra = self._n("EXTRA")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        self.engine.create_node(extra)
        self.engine.restore_snapshot(path, merge=True)
        prefix = f"snap_%_{self._run}"
        assert self._count("Graph_KG.nodes", "node_id", prefix) == 3, (
            "merge=True should preserve local nodes added after snapshot"
        )

    def test_snapshot_includes_globals_for_bfs(self):
        a, b, c = self._n("A"), self._n("B"), self._n("C")
        for n in [a, b, c]:
            self.engine.create_node(n)
        self.engine.create_edge(a, "CONN", b)
        self.engine.create_edge(b, "CONN", c)
        path = self._snapshot_path()
        self.engine.save_snapshot(path)
        prefix = f"snap_%_{self._run}"
        cursor = self.conn.cursor()
        for table in ["Graph_KG.rdf_edges", "Graph_KG.nodes"]:
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE s LIKE ? OR o_id LIKE ?",
                    [prefix, prefix],
                )
            except Exception:
                pass
            try:
                cursor.execute(f"DELETE FROM {table} WHERE node_id LIKE ?", [prefix])
            except Exception:
                pass
        self.conn.commit()
        self.engine.restore_snapshot(path)
        result = self.engine.execute_cypher(
            f"MATCH p = shortestPath((x {{id:'{a}'}})-[*..4]-(y {{id:'{c}'}})) RETURN p"
        )
        assert len(result["rows"]) == 1, (
            "BFS via ^KG globals should work after snapshot restore"
        )
