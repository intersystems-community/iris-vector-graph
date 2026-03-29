"""E2E tests for ^NKG integer index against live IRIS."""
import os
import uuid
import threading
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"NKG_{uuid.uuid4().hex[:6]}"


class TestNkgIndex:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        import iris
        self.irispy = iris.createIRIS(iris_connection)
        self._cleanup()
        yield
        self._cleanup()

    def _cleanup(self):
        p = f"{PREFIX}%"
        self.cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        self.conn.commit()

    def _insert_edge(self, s, p, o):
        try:
            self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [s])
        except Exception:
            pass
        try:
            self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [o])
        except Exception:
            pass
        try:
            self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [s, p, o])
        except Exception:
            pass
        self.conn.commit()

    def _build_kg(self):
        self.irispy.classMethodVoid("Graph.KG.Traversal", "BuildKG")

    def _get_nkg(self, *subs):
        return self.irispy.get("^NKG", *subs)

    def _get_nkg_data(self, *subs):
        return self.irispy.isDefined("^NKG", *subs)

    def test_intern_node_returns_integer(self):
        """T007"""
        idx = self.irispy.classMethodValue("Graph.KG.GraphIndex", "InternNode", f"{PREFIX}:A")
        assert isinstance(idx, (int, str))
        assert int(idx) >= 0
        idx2 = self.irispy.classMethodValue("Graph.KG.GraphIndex", "InternNode", f"{PREFIX}:A")
        assert str(idx) == str(idx2)

    def test_intern_label_returns_integer_ge_3(self):
        """T008"""
        idx = self.irispy.classMethodValue("Graph.KG.GraphIndex", "InternLabel", "test_binds")
        assert int(idx) >= 3
        idx2 = self.irispy.classMethodValue("Graph.KG.GraphIndex", "InternLabel", "test_binds")
        assert str(idx) == str(idx2)

    def test_structural_labels_prepopulated(self):
        """T009"""
        self.irispy.classMethodVoid("Graph.KG.GraphIndex", "InitStructuralLabels")
        assert self._get_nkg("$LS", 0) == "out"
        assert self._get_nkg("$LS", 1) == "in"
        assert self._get_nkg("$LS", 2) == "deg"

    def test_insert_index_populates_nkg(self):
        """T010 + T024"""
        s, o, p = f"{PREFIX}:X", f"{PREFIX}:Y", "test_rel"
        self._insert_edge(s, p, o)
        self._build_kg()

        sIdx = self._get_nkg("$NI", s)
        oIdx = self._get_nkg("$NI", o)
        pIdx = self._get_nkg("$LI", p)
        assert sIdx is not None
        assert oIdx is not None
        assert pIdx is not None

        neg_p = -(int(pIdx) + 1)
        out_val = self._get_nkg(-1, int(sIdx), neg_p, int(oIdx))
        assert out_val is not None

        in_val = self._get_nkg(-2, int(oIdx), neg_p, int(sIdx))
        assert in_val is not None

    def test_build_kg_populates_nkg_metadata(self):
        """T013 + T025"""
        for i in range(5):
            s = f"{PREFIX}:B{i}"
            o = f"{PREFIX}:B{i+1}"
            self._insert_edge(s, "NEXT", o)

        self.irispy.classMethodVoid("Graph.KG.Traversal", "BuildKG")

        nc = self._get_nkg("$meta", "nodeCount")
        assert nc is not None
        assert int(nc) >= 6

        ec = self._get_nkg("$meta", "edgeCount")
        assert ec is not None
        assert int(ec) >= 5

    def test_delete_index_removes_nkg_entries(self):
        """T017 + T026"""
        s, o, p = f"{PREFIX}:D1", f"{PREFIX}:D2", "test_del"
        self._insert_edge(s, p, o)
        self._build_kg()

        sIdx = self._get_nkg("$NI", s)
        oIdx = self._get_nkg("$NI", o)
        pIdx = self._get_nkg("$LI", p)
        assert sIdx is not None
        neg_p = -(int(pIdx) + 1)
        assert self._get_nkg_data(-1, int(sIdx), neg_p, int(oIdx)) > 0

        v_before = self._get_nkg("$meta", "version")

        self.irispy.classMethodVoid("Graph.KG.GraphIndex", "DeleteIndex", "", s, p, o, "")

        assert self._get_nkg_data(-1, int(sIdx), neg_p, int(oIdx)) == 0

        v_after = self._get_nkg("$meta", "version")
        assert int(v_after) > int(v_before)

    def test_backward_compatibility_kg_still_works(self):
        """T027"""
        s, o = f"{PREFIX}:C1", f"{PREFIX}:C2"
        self._insert_edge(s, "COMPAT", o)
        self._build_kg()

        assert self.irispy.get("^KG", "out", s, "COMPAT", o) is not None
        assert self.irispy.get("^KG", "in", o, "COMPAT", s) is not None
        assert self.irispy.get("^KG", "deg", s) is not None

    def test_concurrent_inserts_no_duplicate_indices(self):
        """T027a"""
        import iris as _iris

        errors = []
        indices = {}

        def insert_thread(thread_id):
            try:
                conn = _iris.connect(hostname='localhost', port=1972, namespace='USER', username='test', password='test')
                cur = conn.cursor()
                s = f"{PREFIX}:T{thread_id}"
                o = f"{PREFIX}:T{thread_id}_target"
                cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [s])
                try:
                    cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [o])
                except Exception:
                    pass
                cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'CONCURRENT', ?)", [s, o])
                conn.commit()

                ip = _iris.createIRIS(conn)
                idx = ip.get("^NKG", "$NI", s)
                indices[s] = idx
                conn.close()
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=insert_thread, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"

        self.irispy.classMethodVoid("Graph.KG.Traversal", "BuildKG")

        for s in list(indices.keys()):
            idx = self.irispy.get("^NKG", "$NI", s)
            indices[s] = idx

        idx_values = [v for v in indices.values() if v is not None]
        assert len(idx_values) == len(set(idx_values)), f"Duplicate indices: {idx_values}"
        assert len(idx_values) == 5, f"Expected 5 indices, got {len(idx_values)}"
