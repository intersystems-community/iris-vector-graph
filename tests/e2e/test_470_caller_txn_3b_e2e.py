"""4.7.0 row 3b: the rest of IVG's `conn.commit()` calls never commit a transaction their caller opened.

The same defect class as row 3 (python-devx; tests/e2e/test_470_caller_txn_e2e.py): measured on 4.6.5, each call
below committed (or rolled back) the caller's open transaction, so the caller's own row outlived the caller's rollback.
Each now takes one of two shapes, read from a second connection once the caller's transaction is over:

  joins    the caller opens a transaction (`START TRANSACTION`, then an INSERT of its own), calls, rolls back:
           the caller's row and the call's writes are both gone; or commits: both are there
  refuses  the call raises before doing anything (`CallerTransactionOpenError`, or the ledger's
           `LedgerTransactionOpenError` for `enable()` and `redact()`, tests/e2e/test_470_ledger_caller_txn_e2e.py),
           `$TLEVEL` is still 1, and the caller's rollback takes its row back

Joins: `embed_edges`, `map_sql_table`, `map_sql_relationship`, `remove_table_mapping`, `attach_embeddings_to_table`,
`fhir_bridge_add`, `code_crosswalk_add`, `migrate_fhir_bridges_to_crosswalk`, `register_namespace`, `import_rdf`,
`bulk_create_edges` of 500 or more (already joined; the index drop it makes alone is skipped inside a transaction).
Refuses: `restore_snapshot` (it erases and creates tables), `BulkLoader` (%NOINDEX %NOCHECK loads, %BuildIndices,
the ^KG rebuild, each committed per batch), and the Arno bridge's wrapper functions when one must be created (DDL
and a class compile). FHIR graph tables that must be created (`_ensure_fhir_graph_tables`, which needs a FHIR server
namespace) refuse too: tests/unit/test_470_caller_txn_3b_unit.py.

    IVG_R470_TXN_NS=IVG470TXN pytest tests/e2e/test_470_caller_txn_3b_e2e.py
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_TXN_NS", "")
CALLER_TABLE = "SQLUser.ivg470b_caller"
DATA_TABLE = "SQLUser.ivg470b_data"
SCRATCH = Path.home() / ".cache" / "ivg-bench" / "rel-470" / "txn-scratch"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_TXN_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _engine(conn):
    from iris_vector_graph import IRISGraphEngine

    eng = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    eng.embedder = Fake()
    return eng


class Fake:
    VALUE = 0.25

    def encode(self, texts, **_):
        import numpy as np

        if isinstance(texts, str):
            return np.full(rr.DIM, self.VALUE)
        return np.full((len(texts), rr.DIM), self.VALUE)


@pytest.fixture(scope="module")
def observer():
    conn = _connect()
    cur = conn.cursor()
    for table in (CALLER_TABLE, DATA_TABLE):
        try:
            cur.execute(f"DROP TABLE {table}")
        except Exception:  # noqa: BLE001 - absent on the first run
            pass
    cur.execute(f"CREATE TABLE {CALLER_TABLE} (k VARCHAR(80) PRIMARY KEY)")
    cur.execute(f"CREATE TABLE {DATA_TABLE} (k VARCHAR(80) PRIMARY KEY, txt VARCHAR(200), ref VARCHAR(80))")
    yield conn
    for table in (CALLER_TABLE, DATA_TABLE):
        cur.execute(f"DROP TABLE {table}")
    conn.close()


@pytest.fixture
def caller():
    conn = _connect()
    yield conn
    try:
        conn.rollback()
    finally:
        conn.close()


def _level(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT $TLEVEL")
    return int(cur.fetchone()[0])


def _count(obs, sql: str, params=()) -> int:
    cur = obs.cursor()
    cur.execute(sql, list(params))
    return int(cur.fetchone()[0])


def _has_caller_row(obs, key: str) -> bool:
    return _count(obs, f"SELECT COUNT(*) FROM {CALLER_TABLE} WHERE k = ?", [key]) == 1


def _open(conn, u: str) -> None:
    cur = conn.cursor()
    cur.execute("START TRANSACTION")
    cur.execute(f"INSERT INTO {CALLER_TABLE} (k) VALUES (?)", [u])


def _uid() -> str:
    return "t470b" + uuid.uuid4().hex[:10]


def _mapping(o, label) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.table_mappings WHERE label = ?", [label])


def _rel_mapping(o, label) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.relationship_mappings WHERE source_label = ?", [label])


def _node_vector(o, nid) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = ? AND graph_id = ''", [nid])


def _edge_vector(o, s, p, t) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.kg_EdgeEmbeddings WHERE s = ? AND p = ? AND o_id = ?", [s, p, t])


def _crosswalk(o, code) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.code_crosswalk WHERE code = ?", [code])


def _bridge(o, code) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.fhir_bridges WHERE fhir_code = ?", [code])


def _node(o, nid) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [nid])


def _edge(o, s, p, t) -> int:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?", [s, p, t])


# ------------------------------------------------------------------------------------- the calls that join


def _seed_data(e, u):
    e.conn.cursor().execute(f"INSERT INTO {DATA_TABLE} (k, txt, ref) VALUES (?, ?, ?)", [u, "text " + u, u])


def _ttl(u) -> str:
    path = SCRATCH / f"{u}.ttl"
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<urn:{u}:a> <urn:p:R470B> <urn:{u}:b> .\n<urn:{u}:a> <urn:p:name> \"A\" .\n")
    return str(path)


def _edge_id(e, u):
    cur = e.conn.cursor()
    cur.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'R470B'", [f"{u}a"])
    return int(cur.fetchone()[0])


def _reified(o, u) -> bool:
    return _count(o, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [f"{u}r"]) == 1 \
        and _node(o, f"{u}r") == 1


JOINS = {
    # 4.7.0 (3b): reify_edge and delete_reification called conn.commit()
    "reify_edge": dict(
        setup=lambda e, u: e.create_edge(f"{u}a", "R470B", f"{u}b"),
        call=lambda e, u: e.reify_edge(_edge_id(e, u), reifier_id=f"{u}r", props={"k": "v"}),
        done=lambda o, u: _reified(o, u),
    ),
    "delete_reification": dict(
        setup=lambda e, u: (e.create_edge(f"{u}a", "R470B", f"{u}b"), e.reify_edge(_edge_id(e, u), f"{u}r")),
        call=lambda e, u: e.delete_reification(f"{u}r"),
        done=lambda o, u: not _reified(o, u),
    ),
    "embed_edges": dict(
        setup=lambda e, u: e.create_edge(f"{u}a", "R470B", f"{u}b"),
        call=lambda e, u: e.embed_edges(model=Fake(), text_fn=lambda s, p, o: s, predicate="R470B", force=True),
        done=lambda o, u: _edge_vector(o, f"{u}a", "R470B", f"{u}b") == 1,
    ),
    "map_sql_table": dict(
        setup=lambda e, u: None,
        call=lambda e, u: e.map_sql_table(DATA_TABLE, "k", f"L{u}"),
        done=lambda o, u: _mapping(o, f"L{u}") == 1,
    ),
    "map_sql_relationship": dict(
        setup=lambda e, u: e.map_sql_table(DATA_TABLE, "k", f"L{u}"),
        call=lambda e, u: e.map_sql_relationship(f"L{u}", "REF", f"L{u}", target_fk="ref"),
        done=lambda o, u: _rel_mapping(o, f"L{u}") == 1,
    ),
    "remove_table_mapping": dict(
        setup=lambda e, u: e.map_sql_table(DATA_TABLE, "k", f"L{u}"),
        call=lambda e, u: e.remove_table_mapping(f"L{u}"),
        done=lambda o, u: _mapping(o, f"L{u}") == 0,
    ),
    "attach_embeddings_to_table": dict(
        # a row's vector is stored for the node `<label>:<id>`, which must exist (store_embedding checks)
        setup=lambda e, u: (_seed_data(e, u), e.map_sql_table(DATA_TABLE, "k", f"L{u}"), e.create_node(f"L{u}:{u}")),
        call=lambda e, u: e.attach_embeddings_to_table(f"L{u}", ["txt"], force=True),
        done=lambda o, u: _node_vector(o, f"L{u}:{u}") == 1,
    ),
    "fhir_bridge_add": dict(
        setup=lambda e, u: None,
        call=lambda e, u: e.fhir_bridge_add(f"C{u}", f"{u}n"),
        done=lambda o, u: _bridge(o, f"C{u}") == 1 and _crosswalk(o, f"C{u}") == 1,
    ),
    "code_crosswalk_add": dict(
        setup=lambda e, u: None,
        call=lambda e, u: e.code_crosswalk_add("http://hl7.org/fhir/sid/icd-10-cm", f"C{u}", f"{u}n"),
        done=lambda o, u: _crosswalk(o, f"C{u}") == 1,
    ),
    "migrate_fhir_bridges_to_crosswalk": dict(
        setup=lambda e, u: (e.fhir_bridge_add(f"C{u}", f"{u}n"),
                            e.conn.cursor().execute("DELETE FROM Graph_KG.code_crosswalk WHERE code = ?", [f"C{u}"])),
        call=lambda e, u: e.migrate_fhir_bridges_to_crosswalk(),
        done=lambda o, u: _crosswalk(o, f"C{u}") == 1,
    ),
    "register_namespace": dict(
        setup=lambda e, u: None,
        call=lambda e, u: e.register_namespace(f"p{u}", f"http://example.org/{u}/"),
        done=lambda o, u: _count(o, "SELECT COUNT(*) FROM Graph_KG.rdf_namespaces WHERE prefix = ?", [f"p{u}"]) == 1,
    ),
    "import_rdf": dict(
        setup=lambda e, u: None,
        call=lambda e, u: e.import_rdf(_ttl(u), format="turtle"),
        done=lambda o, u: _node(o, f"urn:{u}:a") == 1 and _edge(o, f"urn:{u}:a", "urn:p:R470B", f"urn:{u}:b") == 1,
    ),
    "bulk_create_edges_600": dict(
        setup=lambda e, u: None,
        call=lambda e, u: e.bulk_create_edges(
            [{"source_id": f"{u}s{i}", "predicate": "R470B", "target_id": f"{u}t{i}"} for i in range(600)]),
        done=lambda o, u: _count(o, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?", [f"{u}s"]) == 600,
    ),
}


@pytest.mark.parametrize("how", ("rollback", "commit"))
@pytest.mark.parametrize("name", list(JOINS))
def test_the_call_joins_the_callers_transaction(observer, caller, name, how):
    w, u = JOINS[name], _uid()
    eng = _engine(caller)
    w["setup"](eng, u)
    before = w["done"](observer, u)
    _open(caller, u)
    w["call"](eng, u)
    assert _level(caller) == 1, f"{name} left the caller's transaction at another level"
    caller.cursor().execute(how.upper())
    kept = how == "commit"
    assert _has_caller_row(observer, u) is kept, f"the caller's row did not follow the caller's {how}"
    assert w["done"](observer, u) is (kept or before), f"{name}'s writes did not follow the caller's {how}"


# ------------------------------------------------------------------------------------- the calls that refuse


def _snapshot(e, u) -> str:
    path = str(SCRATCH / f"{u}.ivgsnap")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    e.create_node(f"{u}snap")
    e.save_snapshot(path)
    return path


def _bulk_loader(e):
    from iris_vector_graph.bulk_loader import BulkLoader

    return BulkLoader(e.conn)


def _stale_arno_record(e, u):
    """The record names a wrapper with other text, so the next _ensure_zf_call_function must create it."""
    import json

    import iris

    from iris_vector_graph.stores import arno_bridge

    native = iris.createIRIS(e.conn)
    raw = native.get("^IVG.Deploy", "routines")
    record = json.loads(str(raw)) if raw else {}
    record["IVG_ARNO_ZF_PROBE"] = "stale-" + u
    native.set(json.dumps(record, sort_keys=True), "^IVG.Deploy", "routines")
    arno_bridge._probe_cache.pop(arno_bridge._conn_key(e.conn), None)


def _arno_wrappers(e, u):
    from iris_vector_graph.stores import arno_bridge

    arno_bridge._ensure_zf_call_function(e.conn, schema="Graph_KG")


REFUSALS = {
    "restore_snapshot": dict(setup=_snapshot, call=lambda e, path: e.restore_snapshot(path, merge=True)),
    "BulkLoader.load_nodes": dict(setup=lambda e, u: u,
                                  call=lambda e, u: _bulk_loader(e).load_nodes([(f"{u}n", {"namespace": "T470B"})])),
    "BulkLoader.load_edges": dict(setup=lambda e, u: u,
                                  call=lambda e, u: _bulk_loader(e).load_edges([(f"{u}a", "R470B", f"{u}b", None)])),
    "BulkLoader.rebuild_all_indices": dict(setup=lambda e, u: u, call=lambda e, u: _bulk_loader(e).rebuild_all_indices()),
    "BulkLoader.build_graph_globals": dict(setup=lambda e, u: u, call=lambda e, u: _bulk_loader(e).build_graph_globals()),
    "arno_wrappers": dict(setup=lambda e, u: (_stale_arno_record(e, u), u)[1], call=_arno_wrappers),
}


@pytest.mark.parametrize("name", list(REFUSALS))
def test_the_call_refuses_the_callers_transaction(observer, caller, name):
    from iris_vector_graph.errors import CallerTransactionOpenError

    w, u = REFUSALS[name], _uid()
    eng = _engine(caller)
    arg = w["setup"](eng, u)
    _open(caller, u)
    with pytest.raises(CallerTransactionOpenError) as info:
        w["call"](eng, arg)
    assert "$TLEVEL 1" in str(info.value), str(info.value)
    assert _level(caller) == 1, f"{name}'s refusal ended the caller's transaction"
    caller.cursor().execute("ROLLBACK")
    assert not _has_caller_row(observer, u), "the caller's row outlived the caller's rollback"


def test_arno_wrappers_with_nothing_to_create_neither_refuse_nor_commit(observer, caller):
    """A wrapper the record holds with this text is not created again, so inside the caller's transaction there is
    nothing to refuse, and nothing is committed: the caller's rollback takes its row back."""
    from iris_vector_graph.stores import arno_bridge

    u = _uid()
    eng = _engine(caller)
    _arno_wrappers(eng, u)  # outside a transaction: the record now holds every wrapper
    arno_bridge._probe_cache.pop(arno_bridge._conn_key(caller), None)
    _open(caller, u)
    _arno_wrappers(eng, u)
    assert _level(caller) == 1
    caller.cursor().execute("ROLLBACK")
    assert not _has_caller_row(observer, u)


@pytest.mark.parametrize("in_caller_txn", (False, True))
def test_a_refused_reify_edge_leaves_no_reifier_node(observer, caller, in_caller_txn):
    """A label wider than rdf_labels.label is refused after the reifier node is created: the node goes with the
    refusal (4.7.0: create_node's transaction is nested in reify_edge's), and a caller's earlier row stays in its
    open transaction."""
    from iris_vector_graph.errors import GraphWriteError

    u = _uid()
    eng = _engine(caller)
    assert eng.create_edge(f"{u}a", "R470B", f"{u}b")
    eid = _edge_id(eng, u)
    if in_caller_txn:
        _open(caller, u)
    with pytest.raises(GraphWriteError):
        eng.reify_edge(eid, reifier_id=f"{u}r", label="L" * 129)
    if in_caller_txn:
        assert _level(caller) == 1
        caller.cursor().execute("COMMIT")
        assert _has_caller_row(observer, u)
    assert _node(observer, f"{u}r") == 0
    assert _count(observer, "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [f"{u}r"]) == 0
