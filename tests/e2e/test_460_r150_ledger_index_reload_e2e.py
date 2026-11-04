"""4.6.0 R150, live: reloading the ledger table class keeps `idx_ledger_correlation`, and setup builds an empty one.

Setup created the index with `CREATE INDEX`, which writes it into the compiled class only, so a `$System.OBJ.Load` of
the class source (what `tcp-deploy` and the deploy run) dropped it until the next `initialize_schema()`. The class now
declares it (tests/unit/test_460_r150_ledger_index_declared.py). A declaration added by a compile over rows already
written builds nothing, so setup calls `LedgerRevision.BuildCorrelationIndex`, which builds it when the table has rows
and the index has no entry.

In `IVG_NAMESPACE` (IVG Schema RAG, so the ledger class is `RAG.LedgerRevision`): the ledger is enabled and one
changeset committed with correlation id `r150-corr`; `ledger.history(correlation_id='r150-corr')` must answer exactly
that revision, worked from the commit's own receipt.
"""

from __future__ import annotations

import uuid

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

CLASS = f"{alt.SCHEMA}.LedgerRevision"
INDEX_SQL = (
    "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = 'ledger_revisions' "
    "AND INDEX_NAME = 'idx_ledger_correlation'"
)


@pytest.fixture(scope="module")
def ledgered():
    import iris

    from iris_vector_graph.ledger import Changeset

    alt.make_namespace()
    conn = alt.connect()
    engine = alt.engine(conn)
    engine.initialize_schema()
    enabled_here = engine.ledger.meta().get("state") != "enabled"
    if enabled_here:
        engine.ledger.enable()
    corr = f"r150-corr-{uuid.uuid4().hex[:8]}"
    cs = Changeset(actor="r150", actor_type="system", correlation_id=corr)
    cs.create_node(f"r150:{corr}", labels=["R150"])
    receipt = engine.ledger.commit(cs)
    yield {"engine": engine, "conn": conn, "nat": iris.createIRIS(conn), "corr": corr, "receipt": receipt}
    if enabled_here:
        engine.ledger.disable()
    conn.close()


def _index_present(conn) -> bool:
    return bool(alt.rows(conn, INDEX_SQL, [alt.SCHEMA]))


def _found(ctx) -> list:
    page = ctx["engine"].ledger.history(correlation_id=ctx["corr"], limit=10)
    return [r.revision_id for r in page.revisions]


def _reload_class(ctx) -> None:
    """Load and compile the ledger class from this checkout's source, rewritten for the schema, as the deploy does."""
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources, rewrite_sources

    text = rewrite_sources(read_class_sources(packaged_class_dir()), alt.SCHEMA)[CLASS]
    nat = ctx["nat"]
    stream = nat.classMethodObject("%Stream.GlobalCharacter", "%New")
    stream.invoke("Write", text)
    stream.invoke("Rewind")
    status = nat.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "ck-d")
    assert nat.classMethodValue("%SYSTEM.Status", "IsOK", status), nat.classMethodValue(
        "%SYSTEM.Status", "GetErrorText", status
    )


def _index_global(ctx) -> str:
    [(loc,)] = alt.rows(ctx["conn"], "SELECT IndexLocation FROM %Dictionary.CompiledStorage WHERE parent = ?", [CLASS])
    return str(loc).lstrip("^")


def _entries(ctx) -> int:
    """How many row ids the index holds, NULL correlation ids included."""
    nat, name, n = ctx["nat"], _index_global(ctx), 0
    value = nat.nextSubscript(False, name, "idxledgercorrelation", "")
    while value is not None:
        row = nat.nextSubscript(False, name, "idxledgercorrelation", value, "")
        while row is not None:
            n += 1
            row = nat.nextSubscript(False, name, "idxledgercorrelation", value, row)
        value = nat.nextSubscript(False, name, "idxledgercorrelation", value)
    return n


def test_setup_leaves_the_index_and_it_answers(ledgered):
    assert _index_present(ledgered["conn"])
    assert _found(ledgered) == [ledgered["receipt"].revision.revision_id]


def test_reloading_the_class_keeps_the_index(ledgered):
    _reload_class(ledgered)
    assert _index_present(ledgered["conn"]), "a reload of the class dropped idx_ledger_correlation"
    assert _found(ledgered) == [ledgered["receipt"].revision.revision_id]


def test_setup_builds_an_index_that_has_no_entry(ledgered):
    rows = alt.rows(ledgered["conn"], f"SELECT COUNT(*) FROM {alt.SCHEMA}.ledger_revisions")[0][0]
    # What a compile that adds the declaration over existing rows leaves: the index, with no entry.
    ledgered["nat"].kill(_index_global(ledgered), "idxledgercorrelation")
    assert _entries(ledgered) == 0
    status = ledgered["engine"].initialize_schema()
    assert status["ledger_index"] == "present", status
    assert _entries(ledgered) == rows, "every row, NULL correlation ids included, has an entry"
    assert _found(ledgered) == [ledgered["receipt"].revision.revision_id]
