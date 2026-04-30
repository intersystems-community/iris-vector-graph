"""E2E tests for ^NKG integer-encoded index against live IRIS."""
import os
import uuid
import pytest

from iris_vector_graph.schema import _call_classmethod

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"NKG_{uuid.uuid4().hex[:6]}"


def _get_global(conn, gbl, *subs):
    method = "GetNKG" if gbl == "NKG" else "GetKG"
    result = _call_classmethod(conn, "Graph.KG.Meta", method, *subs)
    return None if result == "" else result


class TestNKGIndexE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        yield
        p = f"{PREFIX}:%"
        self.cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        self.conn.commit()

    def test_insert_then_buildkg_populates_nkg(self):
        """T024"""
        n1, n2, n3 = f"{PREFIX}:A", f"{PREFIX}:B", f"{PREFIX}:C"
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n1])
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n2])
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n3])
        self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'KNOWS', ?)", [n1, n2])
        self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'LIKES', ?)", [n1, n3])
        self.conn.commit()

        _call_classmethod(self.conn, "Graph.KG.Traversal", "BuildKG")

        sIdx = _get_global(self.conn, "NKG", "$NI", n1)
        assert sIdx is not None, f"Node {n1} not in ^NKG node dictionary"

        oIdx = _get_global(self.conn, "NKG", "$NI", n2)
        assert oIdx is not None

        pIdx = _get_global(self.conn, "NKG", "$LI", "KNOWS")
        assert pIdx is not None
        assert int(pIdx) >= 3

        structural_out = _get_global(self.conn, "NKG", "$LS", "0")
        assert structural_out == "out"

    def test_buildkg_populates_nkg_metadata(self):
        """T025"""
        for i in range(10):
            self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [f"{PREFIX}:N{i}"])
        for i in range(9):
            self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'NEXT', ?)",
                                [f"{PREFIX}:N{i}", f"{PREFIX}:N{i+1}"])
        self.conn.commit()

        _call_classmethod(self.conn, "Graph.KG.Traversal", "BuildKG")

        nc = _get_global(self.conn, "NKG", "$meta", "nodeCount")
        assert nc is not None
        assert int(nc) >= 10

        ver = _get_global(self.conn, "NKG", "$meta", "version")
        assert ver is not None
        assert int(ver) > 0

    def test_rebuild_after_delete_reflects_removal(self):
        """T026"""
        n1, n2 = f"{PREFIX}:X1", f"{PREFIX}:X2"
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n1])
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n2])
        self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'TREL', ?)", [n1, n2])
        self.conn.commit()

        _call_classmethod(self.conn, "Graph.KG.Traversal", "BuildKG")

        sIdx = _get_global(self.conn, "NKG", "$NI", n1)
        assert sIdx is not None

        ec_before = _get_global(self.conn, "NKG", "$meta", "edgeCount")

        self.cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'TREL' AND o_id = ?", [n1, n2])
        self.conn.commit()

        _call_classmethod(self.conn, "Graph.KG.Traversal", "BuildKG")

        ec_after = _get_global(self.conn, "NKG", "$meta", "edgeCount")
        assert ec_after is not None
        assert int(ec_after) < int(ec_before)

    def test_backward_compatibility(self):
        """T027"""
        n1, n2 = f"{PREFIX}:BC1", f"{PREFIX}:BC2"
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n1])
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n2])
        self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'COMPAT', ?)", [n1, n2])
        self.conn.commit()

        _call_classmethod(self.conn, "Graph.KG.Traversal", "BuildKG")

        kg_weight = _get_global(self.conn, "KG", "out", "0", n1, "COMPAT", n2)
        assert kg_weight is not None, "^KG out-edge missing"

        nkg_sIdx = _get_global(self.conn, "NKG", "$NI", n1)
        assert nkg_sIdx is not None, "^NKG node dictionary missing"
