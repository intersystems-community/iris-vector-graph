"""4.6.0 R106: in a namespace whose IVG Schema is IVGALT, the ledger's genesis reads IVGALT's tables.

IVG262FX8, made anew (empty: `make_scratch_namespace.recreate`), then `initialize_schema(schema_prefix="IVGALT")`.
Two nodes with one label each and one relationship, written before the ledger is enabled. Worked by hand, the genesis
records them: create_node r106:a, create_node r106:b, add_label L twice, create_rel r106:a-R->r106:b, five records
(the reserved `id` property is not recorded). `verify()` right after is `equal`, and the revision is in
IVGALT.ledger_revisions.
Scratch namespace: `IVG_NS_IVG262FX8` (default IVG262FX8). The file drops and rebuilds it (4.6.0 R191): point it at
a namespace no other run is using.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS, SCHEMA = os.environ.get("IVG_NS_IVG262FX8", "IVG262FX8"), "IVGALT"


@pytest.fixture(scope="module")
def alt():
    from iris_vector_graph import IRISGraphEngine

    ns262.recreate(NS)
    conn = ns262.connect(NS)
    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    engine.initialize_schema()
    engine.create_node("r106:a", labels=["L"])
    engine.create_node("r106:b", labels=["L"])
    engine.create_edge("r106:a", "R", "r106:b")
    conn.commit()
    yield engine, conn
    conn.close()


def test_genesis_reads_the_ivg_schemas_tables_and_verifies(alt):
    engine, conn = alt
    genesis = engine.ledger.enable()
    assert (genesis.seq, genesis.kind, genesis.op_count) == (1, "genesis", 5)
    records = engine.ledger.get_revision(genesis.revision_id).records
    assert sorted((r.op, r.entity_id if r.op != "create_rel" else None) for r in records) == [
        ("add_label", "r106:a"), ("add_label", "r106:b"), ("create_node", "r106:a"), ("create_node", "r106:b"),
        ("create_rel", None),
    ]
    report = engine.ledger.verify()
    assert (report.result, report.differences) == ("equal", [])
    cur = conn.cursor()
    cur.execute(f"SELECT seq, kind FROM {SCHEMA}.ledger_revisions")
    assert [tuple(r) for r in cur.fetchall()] == [(1, "genesis")]
