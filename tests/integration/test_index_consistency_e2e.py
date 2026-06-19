"""E2E: the ^KG/^NKG-vs-SQL consistency invariant.

This is the test the suite was missing. The existing index tests assert that
sync() *calls* its sub-methods (mock-level plumbing); none assert that after a
write the globals actually agree with the SQL tables. These tests exercise the
real invariant on a live container:

  1. A normal write (create_edge) keeps globals and SQL in sync.
  2. A BYPASS write (raw SQL INSERT) drifts — and verify_sync() catches it.
  3. verify_sync(heal=True) repairs the drift via sync().

Requires live IRIS (iris_vector_graph community container).
"""
from __future__ import annotations

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestIndexConsistencyInvariant:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self.run = uuid.uuid4().hex[:8]
        self.prefix = f"drift_{self.run}"
        yield
        cur = iris_connection.cursor()
        try:
            cur.execute(f"DELETE FROM Graph_KG.rdf_edges WHERE s LIKE '{self.prefix}%'")
            cur.execute(f"DELETE FROM Graph_KG.rdf_edges WHERE o_id LIKE '{self.prefix}%'")
            cur.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE '{self.prefix}%'")
            iris_connection.commit()
        except Exception:
            pass
        finally:
            cur.close()

    def _n(self, i):
        return f"{self.prefix}_n{i}"

    # NOTE: assertions are delta-based, not absolute. The shared enterprise DB
    # may already carry drift from other suites/benchmarks (we observed
    # NKG edgeCount=110 vs SQL=108 on a live container — the very drift this
    # detector exists to catch). Absolute COUNT equality is therefore unsafe;
    # we measure how the SQL-vs-global gap *changes* across an operation.

    def _raw_insert(self, n):
        """Insert n genuinely-new edge rows via raw SQL (bypassing create_edge).

        Uses run-unique subjects so the UNIQUE (s,p,o_id) constraint never
        silently swallows the insert in a shared/polluted DB. Returns rows added.
        """
        # rdf_edges has a FOREIGN KEY to nodes(node_id) — the endpoints must
        # exist first, or the INSERT fails with SQLCODE -121. (This FK is itself
        # the SQL layer's only built-in guard against orphan edges.)
        cur = self.conn.cursor()
        added = 0
        for i in range(n):
            s, o = f"{self.prefix}_raw{i}", f"{self.prefix}_rawtgt{i}"
            for nid in (s, o):
                cur.execute(
                    "INSERT INTO Graph_KG.nodes (node_id) SELECT ? "
                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id=?)",
                    [nid, nid],
                )
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                [s, "BYPASS", o],
            )
            added += cur.rowcount if cur.rowcount is not None else 1
        self.conn.commit()
        cur.close()
        return added

    def test_normal_write_then_sync_is_in_sync(self):
        """create_edge + sync() leaves the index covering every SQL edge."""
        for i in range(4):
            self.engine.create_node(self._n(i), labels=["Node"])
        for i in range(3):
            self.engine.create_edge(self._n(i), "LINK", self._n(i + 1))
        self.engine.sync()

        after = self.engine.verify_sync()
        # Self-maintaining writes + sync: globals cover SQL, flag is clear.
        assert after.global_edges >= after.sql_edges
        assert after.pending_sync is False
        assert after.in_sync is True

    def test_raw_sql_bypass_widens_the_gap(self):
        """A raw SQL INSERT (SQL-bridge / external-ETL pattern) drifts the index."""
        for i in range(4):
            self.engine.create_node(self._n(i), labels=["Node"])
        self.engine.sync()
        before = self.engine.verify_sync()

        # Bypass create_edge entirely — write the table directly.
        added = self._raw_insert(3)
        assert added == 3, "raw insert must add genuinely-new rows for this test"

        after = self.engine.verify_sync()
        # SQL grew by 3; globals did not → the gap widened by exactly 3.
        assert (after.sql_edges - after.global_edges) == \
               (before.sql_edges - before.global_edges) + 3
        assert after.in_sync is False

    def test_heal_repairs_drift(self):
        """verify_sync(heal=True) rebuilds globals so the gap closes to zero."""
        for i in range(4):
            self.engine.create_node(self._n(i), labels=["Node"])
        self.engine.create_edge(self._n(0), "LINK", self._n(1))
        self.engine.sync()

        # Introduce a known bypass drift, then confirm the detector sees a gap.
        added = self._raw_insert(2)
        assert added == 2
        drifted = self.engine.verify_sync()
        assert drifted.sql_edges > drifted.global_edges
        assert drifted.in_sync is False

        # heal=True runs a full BuildKG/BuildNKG rebuild from SQL.
        healed = self.engine.verify_sync(heal=True)
        assert healed.healed is True
        # Post-rebuild every SQL edge is indexed (globals no longer trail SQL).
        # We assert globals >= SQL rather than exact equality: ^NKG's meta
        # edgeCount over-counts on a DB that has seen deletes (documented in
        # verify_sync). The contract that matters — no SQL edge missing from the
        # index — holds.
        post = self.engine.verify_sync()
        assert post.global_edges >= post.sql_edges
        assert post.in_sync is True

    def test_drop_graph_flags_dirty(self):
        """drop_graph is a BYPASS — it must mark the index stale."""
        g = f"urn:graph:{self.prefix}"
        self.engine.create_node(self._n(0), labels=["Node"])
        self.engine.create_node(self._n(1), labels=["Node"])
        self.engine.create_edge(self._n(0), "LINK", self._n(1), graph=g)
        self.engine.sync()

        self.engine.drop_graph(g)
        # The in-process dirty flag must now be set even before a count check.
        assert self.engine._nkg_dirty is True
