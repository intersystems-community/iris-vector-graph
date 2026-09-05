"""Spec 213 US1 — atomic multi-operation commit and head (live ivg-iris-enterprise).

T024 foundation smoke; T026 scenarios 1–5. Uses `iris_connection` (default container
ivg-iris-enterprise per tests/conftest.py) and the `ledger_reset` fixture.
"""

import os

import pytest

from iris_vector_graph.ledger import Changeset, EmptyChangesetError
from tests.integration._ledger_helpers import canonical_tables, make_engine, seed_graph

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    return make_engine(iris_connection)


def _kg_out(engine, s, p, o):
    return str(engine._iris_obj().classMethodValue("Graph.KG.Meta", "GetKG", "out", "0", s, p, o))


class TestFoundation:
    def test_foundation_enable_and_reject_empty(self, engine):
        genesis = engine.ledger.enable()
        assert genesis.seq == 1 and genesis.parent_id is None and genesis.kind == "genesis"
        again = engine.ledger.enable()
        assert again.revision_id == genesis.revision_id
        with pytest.raises(EmptyChangesetError):
            engine.ledger.commit(Changeset(actor="t", actor_type="human"))
        assert engine.ledger.head().revision_id == genesis.revision_id


class TestUS1CommitHead:
    def test_full_kind_changeset_creates_one_revision_and_is_visible(self, engine):
        h0 = engine.ledger.enable()
        cs = Changeset(
            actor="ingest:etl-42",
            actor_type="ingest",
            message="equipment sync",
            source={"system": "cmms"},
            correlation_id="run-1",
        )
        cs.create_node("pump-7", labels=["Equipment"], properties={"status": "ok"})
        cs.create_node("tank-2", labels=["Vessel"])
        cs.add_label("pump-7", "Critical")
        cs.set_property("pump-7", "status", "warn")
        r = cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"weight": "2.5"})
        cs.set_qualifier(r, "capacity", "100")

        res = engine.ledger.commit(cs)
        assert res.replayed is False
        assert res.revision.seq == 2 and res.revision.parent_id == h0.revision_id
        assert res.revision.kind == "changeset" and res.revision.actor == "ingest:etl-42"
        assert res.stmt_ids == {4: res.stmt_ids[4]}  # exactly one create_rel at op index 4
        head = engine.ledger.head()
        assert head.revision_id == res.revision.revision_id

        # visible through SQL
        tables = canonical_tables(engine.conn)
        assert tables["nodes"]["pump-7"]["labels"] == {"Equipment", "Critical"}
        assert tables["nodes"]["pump-7"]["props"] == {"status": "warn"}
        assert tables["rels"][("pump-7", "FEEDS", "tank-2", None)]["quals"] == {
            "weight": "2.5",
            "capacity": "100",
        }
        # visible through Cypher
        rows = engine.execute_cypher(
            "MATCH (a {node_id:'pump-7'})-[:FEEDS]->(b) RETURN b.node_id AS id"
        )["rows"]
        assert [r[0] for r in rows] == ["tank-2"]
        # visible through adjacency (^KG("out",0,...)) with the committed weight
        assert _kg_out(engine, "pump-7", "FEEDS", "tank-2") == "2.5"

    def test_head_metadata_fields(self, engine):
        engine.ledger.enable()
        cs = Changeset(
            actor="human:tom",
            actor_type="human",
            message="m",
            correlation_id="c-1",
            source={"k": "v"},
            idempotency_key="key-1",
        )
        cs.create_node("n1")
        res = engine.ledger.commit(cs)
        head = engine.ledger.head()
        assert head.revision_id == res.revision.revision_id and len(head.revision_id) == 32
        assert head.parent_id is not None and head.seq == 2
        assert head.actor == "human:tom" and head.actor_type == "human"
        assert head.conn_user  # system-observed
        assert head.message == "m" and head.correlation_id == "c-1" and head.source == {"k": "v"}
        assert head.idempotency_key == "key-1" and head.op_count == 1
        # UTC milliseconds, within 5 minutes of now
        import time

        assert abs(head.committed_ms - int(time.time() * 1000)) < 300_000

    def test_head_before_any_commit_is_genesis(self, engine):
        genesis = engine.ledger.enable()
        head = engine.ledger.head()
        assert head.revision_id == genesis.revision_id and head.kind == "genesis" and head.seq == 1

    def test_genesis_on_prepopulated_graph_describes_it_and_leaves_view_unchanged(self, engine):
        nodes, rels = seed_graph(engine, 5, 4)
        before = canonical_tables(engine.conn)
        genesis = engine.ledger.enable()
        after = canonical_tables(engine.conn)
        assert after == before
        # 5 create_node + 5 add_label(Seed) + 5 set_prop(idx) + 4 create_rel (legacy edges have no quals)
        assert genesis.op_count == 5 + 5 + 5 + 4
        rev = engine.ledger.get_revision(genesis.revision_id)
        ops = [r.op for r in rev.records]
        assert ops.count("create_node") == 5 and ops.count("create_rel") == 4
        assert all("genesis" in r.flags for r in rev.records)
        # every relationship received a statement identity
        stmt_ids = {r.entity_id for r in rev.records if r.op == "create_rel"}
        assert len(stmt_ids) == 4

    def test_revisions_are_immutable_via_sql(self, engine):
        engine.ledger.enable()
        cs = Changeset(actor="a", actor_type="human", message="original")
        cs.create_node("n1")
        res = engine.ledger.commit(cs)
        cur = engine.conn.cursor()
        try:
            with pytest.raises(Exception):
                cur.execute(
                    "UPDATE Graph_KG.ledger_revisions SET message = 'tampered' WHERE seq = ?",
                    [res.revision.seq],
                )
            engine.conn.rollback()
            with pytest.raises(Exception):
                cur.execute(
                    "DELETE FROM Graph_KG.ledger_revisions WHERE seq = ?", [res.revision.seq]
                )
            engine.conn.rollback()
            cur.execute(
                "SELECT message FROM Graph_KG.ledger_revisions WHERE seq = ?", [res.revision.seq]
            )
            assert cur.fetchone()[0] == "original"
        finally:
            cur.close()
        # no public mutator exists on the client
        from iris_vector_graph.ledger import GraphLedger

        assert not any(n.startswith(("update_", "delete_", "edit_")) for n in dir(GraphLedger))
