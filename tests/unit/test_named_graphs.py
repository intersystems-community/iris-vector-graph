import json
import os
import tempfile
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

_MINI_TTL = """\
@prefix ex: <http://example.org/> .
ex:Drug1 ex:treats ex:Disease1 .
ex:Drug2 ex:treats ex:Disease2 .
"""


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestNamedGraphsE2E:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(iris_connection)
        self._run = uuid.uuid4().hex[:8]
        yield
        self._cleanup()

    def _n(self, label):
        return f"ng_{label}_{self._run}"

    def _g(self, label):
        return f"g_{label}_{self._run}"

    def _cleanup(self):
        for g in [
            self._g("import"),
            self._g("g1"),
            self._g("g2"),
            self._g("bulk"),
            self._g("override"),
            self._g("temporal"),
            self._g("tbulk"),
            self._g("all"),
            self._g("drop"),
        ]:
            try:
                self.engine.drop_graph(g)
            except Exception:
                pass
        cursor = self.engine.conn.cursor()
        prefix = f"ng_%_{self._run}"
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
            self.engine.conn.commit()
        except Exception:
            pass

    def _use_graph_count(self, graph_id):
        r = self.engine.execute_cypher(
            f"USE GRAPH '{graph_id}' MATCH (a)-[r]->(b) RETURN count(r) AS c"
        )
        rows = r.get("rows", [])
        return rows[0][0] if rows else 0

    def _use_graph_targets(self, src_id, graph_id):
        r = self.engine.execute_cypher(
            f"USE GRAPH '{graph_id}' MATCH (a {{id:'{src_id}'}})-[r]->(b) RETURN b.id"
        )
        return [row[0] for row in r.get("rows", [])]

    def test_import_rdf_graph_id_written(self):
        g = self._g("import")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ttl", delete=False) as f:
            f.write(_MINI_TTL)
            path = f.name
        try:
            result = self.engine.import_rdf(path, graph=g)
            assert result["edges"] > 0, f"import_rdf inserted 0 edges (result={result})"
            count = self._use_graph_count(g)
            assert count > 0, (
                f"USE GRAPH '{g}' returned 0 edges — graph_id not written to rdf_edges"
            )
        finally:
            os.unlink(path)

    def test_use_graph_no_cross_graph_leak(self):
        a = self._n("A")
        b = self._n("B")
        c = self._n("C")
        g1 = self._g("g1")
        g2 = self._g("g2")
        for nid in [a, b, c]:
            self.engine.create_node(nid)
        self.engine.create_edge(a, "R", b, graph=g1)
        self.engine.create_edge(a, "R", c, graph=g2)
        targets_g1 = self._use_graph_targets(a, g1)
        assert targets_g1 == [b], (
            f"USE GRAPH '{g1}' returned {targets_g1}, expected [{b}] — cross-graph leak"
        )

    def test_bulk_create_edges_graph_id(self):
        x = self._n("X")
        y = self._n("Y")
        z = self._n("Z")
        g_bulk = self._g("bulk")
        g_override = self._g("override")
        for nid in [x, y, z]:
            self.engine.create_node(nid)
        self.engine.bulk_create_edges(
            [
                {"source_id": x, "predicate": "P", "target_id": y},
                {"source_id": x, "predicate": "P", "target_id": z, "graph": g_override},
            ],
            graph=g_bulk,
        )
        targets_bulk = self._use_graph_targets(x, g_bulk)
        assert y in targets_bulk, f"{y} not in {g_bulk}: {targets_bulk}"
        assert z not in targets_bulk, f"{z} should not be in {g_bulk}"
        targets_override = self._use_graph_targets(x, g_override)
        assert z in targets_override, f"{z} not in {g_override}: {targets_override}"

    def test_create_edge_temporal_graph_id(self):
        a = self._n("ta")
        b = self._n("tb")
        g = self._g("temporal")
        result = self.engine.create_edge_temporal(
            a, "CALLS_AT", b, timestamp=1000, graph=g
        )
        assert result is True
        count = self._use_graph_count(g)
        assert count > 0, (
            f"USE GRAPH '{g}' returned 0 — temporal edge graph_id not written"
        )

    def test_bulk_create_edges_temporal_graph_id(self):
        a = self._n("ta2")
        b = self._n("tb2")
        g = self._g("tbulk")
        self.engine.bulk_create_edges_temporal(
            [{"s": a, "p": "CALLS", "o": b, "ts": 1000, "w": 1.0}], graph=g
        )
        count = self._use_graph_count(g)
        assert count > 0, (
            f"USE GRAPH '{g}' returned 0 — bulk temporal graph_id not written"
        )

    def test_rel_type_properties_non_empty(self):
        a = self._n("rp_a")
        b = self._n("rp_b")
        self.engine.create_edge(a, "TREATS", b)
        result = self.engine._try_system_procedure(
            type("P", (), {"procedure_name": "db.schema.reltypeproperties"})()
        )
        assert result is not None
        assert len(result["rows"]) > 0, (
            "db.schema.relTypeProperties always returns empty — not fixed"
        )

    def test_list_graphs_includes_all_write_paths(self):
        g = self._g("all")
        a, b = self._n("la"), self._n("lb")
        for nid in [a, b]:
            self.engine.create_node(nid)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ttl", delete=False) as f:
            f.write(_MINI_TTL)
            path = f.name
        try:
            self.engine.import_rdf(path, graph=g)
        finally:
            os.unlink(path)
        self.engine.create_edge(a, "R", b, graph=g)
        self.engine.create_edge_temporal(a, "T", b, timestamp=1000, graph=g)
        graphs = self.engine.list_graphs()
        assert g in graphs, f"{g} not in list_graphs(): {graphs}"

    def test_drop_graph_removes_all_paths(self):
        g = self._g("drop")
        a, b = self._n("da"), self._n("db")
        for nid in [a, b]:
            self.engine.create_node(nid)
        self.engine.create_edge(a, "R", b, graph=g)
        self.engine.create_edge_temporal(a, "T", b, timestamp=1000, graph=g)
        assert self._use_graph_count(g) > 0
        self.engine.drop_graph(g)
        assert self._use_graph_count(g) == 0, (
            f"drop_graph did not remove all edges from {g}"
        )
