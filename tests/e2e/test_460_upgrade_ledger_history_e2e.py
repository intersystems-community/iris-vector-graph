"""4.6.0 R114: a 4.5.1 namespace upgraded to a non-default IVG Schema keeps its ledger history.

In 4.5.x the ledger's tables are the classes `Graph.KG.LedgerRevision` and `Graph.KG.LedgerStats`, projected into
`Graph_KG` whatever the engine's `schema_prefix`; in 4.6.0 they compile into the IVG Schema's own package (R084), so
`RAG.ledger_revisions` started empty while `^IVG.Ledger` (the head, the records) still described three revisions:
`history()` answered nothing and `verify()` raised "revision row missing for seq 3". The upgrade now copies the rows
into the IVG Schema's ledger tables when those are empty.

IVG262FX8U is built by v4.5.1's own code with `schema_prefix="RAG"` (`build_release_namespace.build`: its tables in
RAG, the seed copied there), then v4.5.1's own ledger is enabled on a RAG engine and two changesets committed
(`run_ledger_commits`). By hand: changeset 1 creates r114:a and r114:b with label R114 and r114:a -R114-> r114:b,
five records; changeset 2 sets r114:a's `step`, one. The genesis is 4.5.1's capture of the seed (its own count).
Then 4.6.0's `initialize_schema(schema_prefix="RAG")`, a dry run first.

Also (R114's second half): 4.5.1's `Graph.KG.TemporalIndexMS` inherited `BackfillStructuralRows`' `[ SqlProc ]` and so
projected `Graph_KG.TemporalIndexMS_BackfillStructuralRows`; 4.6.0's classes project no such routine, and the upgrade's
recompile removes it. No code, doc or manifest names it.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS, SCHEMA = "IVG262FX8U", "RAG"
MS_PROC = "TemporalIndexMS_BackfillStructuralRows"


def _routines_named(conn, name):
    return ns262.rows(conn, "SELECT ROUTINE_SCHEMA FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_NAME = ?", [name])


def _revisions(conn, schema):
    return [list(r) for r in ns262.rows(
        conn, f"SELECT seq, revision_id, kind, op_count FROM {schema}.ledger_revisions ORDER BY seq")]


@pytest.fixture(scope="module")
def upg():
    from build_release_namespace import build, run_ledger_commits

    from iris_vector_graph import IRISGraphEngine

    build("v4.5.1", NS, schema_prefix=SCHEMA)
    committed = run_ledger_commits("v4.5.1", NS, SCHEMA)
    conn = ns262.connect(NS)
    before = {"revisions": _revisions(conn, "Graph_KG"), "ms_proc": _routines_named(conn, MS_PROC)}
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    dry = engine.initialize_schema(dry_run=True)
    status = engine.initialize_schema()
    again = engine.initialize_schema()
    yield {"conn": conn, "engine": engine, "committed": committed, "before": before, "dry": dry, "status": status,
           "again": again}
    conn.close()


def test_the_fixture_has_451_ledger_history(upg):
    kinds = [(seq, kind, ops) for seq, _rid, kind, ops in upg["before"]["revisions"]]
    assert [k[:2] for k in kinds] == [(1, "genesis"), (2, "changeset"), (3, "changeset")]
    assert [k[2] for k in kinds[1:]] == [5, 1]
    assert upg["committed"]["revisions"] == [[s, k, o] for s, k, o in kinds]


def test_the_dry_run_counts_the_rows_it_would_copy(upg):
    assert upg["dry"]["ledger_history_copied"] == {"ledger_revisions": 3, "ledger_stats": 1}


def test_every_revision_is_in_the_ivg_schemas_ledger_table(upg):
    assert _revisions(upg["conn"], SCHEMA) == upg["before"]["revisions"]
    assert upg["status"]["ledger_history_copied"] == {"ledger_revisions": 3, "ledger_stats": 1}


def test_a_second_setup_copies_nothing(upg):
    assert upg["again"]["ledger_history_copied"] == {}
    assert _revisions(upg["conn"], SCHEMA) == upg["before"]["revisions"]


def test_history_lists_the_revisions_and_verify_is_equal(upg):
    engine = upg["engine"]
    ids = {rid for _s, rid, _k, _o in upg["before"]["revisions"]}
    assert {r.revision_id for r in engine.ledger.history(limit=10).revisions} == ids
    assert engine.ledger.head().seq == 3
    report = engine.ledger.verify()
    assert (report.result, report.differences) == ("equal", [])
    stats = ns262.rows(upg["conn"], f"SELECT state, head_seq FROM {SCHEMA}.ledger_stats")
    assert [tuple(r) for r in stats] == [("enabled", 3)]


def test_the_temporalindexms_projection_is_gone(upg):
    assert [tuple(r) for r in upg["before"]["ms_proc"]] == [("Graph_KG",)]
    assert _routines_named(upg["conn"], MS_PROC) == []
