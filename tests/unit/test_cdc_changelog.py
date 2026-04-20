import os
import time
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestCDCChangelogE2E:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self._run = uuid.uuid4().hex[:8]
        self.engine = IRISGraphEngine(iris_connection, cdc=True)
        self.engine_nocdc = IRISGraphEngine(iris_connection, cdc=False)
        yield
        self._cleanup()

    def _n(self, label):
        return f"cdc_{label}_{self._run}"

    def _cleanup(self):
        try:
            self.engine.clear_changelog()
        except Exception:
            pass
        cursor = self.conn.cursor()
        prefix = f"cdc_%_{self._run}"
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
        try:
            self.conn.commit()
        except Exception:
            pass

    def test_cdc_disabled_by_default(self):
        a, b = self._n("A"), self._n("B")
        self.engine_nocdc.create_node(a)
        self.engine_nocdc.create_node(b)
        self.engine_nocdc.create_edge(a, "R", b)
        assert self.engine_nocdc.cdc is False
        changes = self.engine_nocdc.get_changes_since(0)
        assert changes == [], f"cdc=False should write nothing, got {changes}"

    def test_create_edge_writes_cdc(self):
        ts_before = int(time.time() * 1000)
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "TREATS", b)
        changes = self.engine.get_changes_since(ts_before)
        assert len(changes) >= 1
        create_changes = [c for c in changes if c["op"] == "CREATE_EDGE"]
        assert len(create_changes) >= 1
        c = create_changes[0]
        assert c["src"] == a
        assert c["pred"] == "TREATS"
        assert c["dst"] == b

    def test_delete_edge_writes_cdc(self):
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        ts_before = int(time.time() * 1000)
        self.engine.delete_edge(a, "R", b)
        changes = self.engine.get_changes_since(ts_before)
        delete_changes = [c for c in changes if c["op"] == "DELETE_EDGE"]
        assert len(delete_changes) >= 1
        assert delete_changes[0]["src"] == a

    def test_get_changes_since_millis(self):
        a, b, c_node = self._n("A"), self._n("B"), self._n("C")
        for n in [a, b, c_node]:
            self.engine.create_node(n)
        ts_before = int(time.time() * 1000)
        self.engine.create_edge(a, "R", b)
        self.engine.create_edge(a, "R", c_node)
        self.engine.create_edge(b, "R", c_node)
        changes = self.engine.get_changes_since(ts_before)
        create_changes = [c for c in changes if c["op"] == "CREATE_EDGE"]
        assert len(create_changes) == 3
        for ch in create_changes:
            assert ch["ts"] >= ts_before
            assert "seq" in ch
            assert "src" in ch and "pred" in ch and "dst" in ch

    def test_replay_changes_idempotent(self):
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        ts_before = int(time.time() * 1000)
        self.engine.create_edge(a, "R", b)
        changes = self.engine.get_changes_since(ts_before)
        self.engine.delete_edge(a, "R", b)
        result = self.engine.replay_changes(changes)
        assert result["applied"] >= 1
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id IS NULL",
            [a, "R", b],
        )
        count1 = cursor.fetchone()[0]
        assert count1 >= 1
        result2 = self.engine.replay_changes(changes)
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id IS NULL",
            [a, "R", b],
        )
        count2 = cursor.fetchone()[0]
        assert count2 == count1, (
            f"Replay not idempotent: first={count1}, second={count2}"
        )

    def test_replay_record_flag(self):
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        ts_before = int(time.time() * 1000)
        self.engine.create_edge(a, "R", b)
        changes = self.engine.get_changes_since(ts_before)
        self.engine.delete_edge(a, "R", b)
        self.engine.clear_changelog()
        ts_replay = int(time.time() * 1000)
        self.engine.replay_changes(changes, record_replay=True)
        replay_changes = self.engine.get_changes_since(ts_replay)
        replay_ops = [c["op"] for c in replay_changes]
        assert any("REPLAY" in op for op in replay_ops), (
            f"record_replay=True should write REPLAY_* ops, got {replay_ops}"
        )

    def test_clear_changelog(self):
        a, b = self._n("A"), self._n("B")
        self.engine.create_node(a)
        self.engine.create_node(b)
        self.engine.create_edge(a, "R", b)
        assert len(self.engine.get_changes_since(0)) > 0
        self.engine.clear_changelog()
        assert self.engine.get_changes_since(0) == []

    def test_five_creates_five_entries(self):
        nodes = [self._n(str(i)) for i in range(6)]
        for n in nodes:
            self.engine.create_node(n)
        ts_before = int(time.time() * 1000)
        for i in range(5):
            self.engine.create_edge(nodes[i], "R", nodes[i + 1])
        changes = self.engine.get_changes_since(ts_before)
        create_changes = [c for c in changes if c["op"] == "CREATE_EDGE"]
        assert len(create_changes) == 5, (
            f"Expected 5 CDC entries, got {len(create_changes)}"
        )
