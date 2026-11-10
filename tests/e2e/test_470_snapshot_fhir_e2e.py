"""4.7.0 row 26: a snapshot carries a FHIR graph's registration (`fhir_graphs`) and the code crosswalk.

On 4.6.5 `save_snapshot` exported neither table, so a restore into a namespace without them brought back the graph's
nodes and edges and no `fhir_graphs` row (the graph is no longer registered: no sync, no `fhir_graph_status`) and no
`code_crosswalk` row (every `fhir_resolve_concepts` answers nothing). Into the same namespace a non-merge restore left
the registration with the watermarks `erase_all()` had reset to 0, so the next sync re-derived the whole repository.

The namespace has no FHIR server, so the registration is written at the table level: the row `fhir_graph_register`
writes, with every column set, and three crosswalk rows, two of one code system (a merge restore guarded on the first
column only, `code_system_uri`, would skip the second). The known answer is the rows the test wrote.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_snapshot_fhir_e2e.py
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
SCRATCH = Path.home() / ".cache" / "ivg-bench" / "rel-470" / "txn-scratch"
LAZY_FHIR_TABLES = ("fhir_graphs", "fhir_unresolved", "fhir_definitions", "fhir_canonical_refs")

GRAPH_COLS = (
    "graph_id", "repo_id", "rsrc_schema", "search_schema", "ver_schema", "endpoints", "denylist", "interval_s",
    "wm_rsrc", "wm_ver", "task_id", "last_sync", "last_rebuild", "last_error", "last_counts", "json_links",
    "interp_version",
)
CROSSWALK_COLS = (
    "code_system_uri", "code", "target_graph", "target_node_id", "relation", "source", "source_version", "confidence",
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


@pytest.fixture
def eng():
    from iris_vector_graph import IRISGraphEngine

    conn = _connect()
    engine = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    yield engine
    conn.close()


def _uid() -> str:
    return "t470f" + uuid.uuid4().hex[:10]


def _create_fhir_tables(conn) -> None:
    """The lazy FHIR tables, as the first registration in a FHIR namespace makes them."""
    from iris_vector_graph.schema import GraphSchema
    from iris_vector_graph.utils import _split_sql_statements

    cur = conn.cursor()
    for stmt in _split_sql_statements(GraphSchema.get_fhir_graph_schema_sql()):
        if not stmt.strip():
            continue
        try:
            cur.execute(stmt)
        except Exception as e:  # noqa: BLE001 - the indexes answer "already exists" on a second run
            if "already" not in str(e).lower():
                raise
    conn.commit()


def _drop_fhir_tables(conn) -> None:
    cur = conn.cursor()
    for t in LAZY_FHIR_TABLES:
        cur.execute(f"DROP TABLE IF EXISTS Graph_KG.{t}")
    conn.commit()


def _known(u: str):
    graph = (
        f"fhir:T470:{u}", "X0470", "HSFHIR_X0470_R", "HSFHIR_X0470_S", "HSFHIR_X0470_V", '["/fhir/r4"]',
        '["Provenance"]', 120, 4242, 1717, "17", "2026-11-10 09:15:30", "2026-11-09 23:01:02", "last: timeout",
        '{"Patient": 3}', '{"subject": 1}', 3,
    )
    system = f"http://example.org/{u}/icd"
    crosswalk = [
        (system, "E11.9", "", f"{u}diabetes", "exact", "loinc-map", "2.76", 1.0),
        (system, "I10", "", f"{u}hypertension", "broader", "loinc-map", "2.76", 0.5),
        (f"http://example.org/{u}/atc", "A10BA02", f"fhir:T470:{u}", f"{u}metformin", "related", None, None, 0.25),
    ]
    return graph, crosswalk


def _write(conn, graph, crosswalk) -> None:
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO Graph_KG.fhir_graphs ({', '.join(GRAPH_COLS)}) VALUES ({', '.join('?' * len(GRAPH_COLS))})",
        list(graph),
    )
    for row in crosswalk:
        cur.execute(
            f"INSERT INTO Graph_KG.code_crosswalk ({', '.join(CROSSWALK_COLS)}) "
            f"VALUES ({', '.join('?' * len(CROSSWALK_COLS))})",
            list(row),
        )
    conn.commit()


def _norm(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[10] in " T" and v[13] == ":":
        return v[:10] + " " + v[11:19]
    if hasattr(v, "__float__") and not isinstance(v, int):
        return float(v)
    return v


def _graph_row(conn, graph_id):
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {', '.join(GRAPH_COLS)} FROM Graph_KG.fhir_graphs WHERE graph_id = ?", [graph_id])
    except Exception as e:  # noqa: BLE001 - only a missing table is "no row"
        if "<-30>" in str(e):
            return None
        raise
    row = cur.fetchone()
    return None if row is None else tuple(_norm(v) for v in row)


def _crosswalk_rows(conn, u):
    cur = conn.cursor()
    cur.execute(
        f"SELECT {', '.join(CROSSWALK_COLS)} FROM Graph_KG.code_crosswalk WHERE target_node_id %STARTSWITH ? "
        "ORDER BY code", [u],
    )
    return [tuple(_norm(v) for v in r) for r in cur.fetchall()]


def _expected(graph, crosswalk):
    return tuple(_norm(v) for v in graph), sorted((tuple(_norm(v) for v in r) for r in crosswalk), key=lambda r: r[1])


def _snapshot(eng, u) -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = str(SCRATCH / f"{u}.ivgsnap")
    eng.save_snapshot(path, layers=["sql"])
    return path


def _remove(conn, crosswalk) -> None:
    cur = conn.cursor()
    for row in crosswalk:
        cur.execute("DELETE FROM Graph_KG.code_crosswalk WHERE target_graph = ? AND target_node_id = ? "
                    "AND code_system_uri = ? AND code = ?", [row[2], row[3], row[0], row[1]])
    conn.commit()
    _drop_fhir_tables(conn)


@pytest.mark.parametrize("merge", (False, True), ids=("replace", "merge"))
def test_a_restore_into_a_namespace_without_them_brings_back_the_registration_and_crosswalk(eng, merge):
    """The target has no FHIR tables and none of the crosswalk rows: as a namespace the archive is moved to."""
    u = _uid()
    _create_fhir_tables(eng.conn)
    graph, crosswalk = _known(u)
    _write(eng.conn, graph, crosswalk)
    want_graph, want_crosswalk = _expected(graph, crosswalk)
    assert _graph_row(eng.conn, graph[0]) == want_graph
    assert _crosswalk_rows(eng.conn, u) == want_crosswalk
    path = _snapshot(eng, u)
    _remove(eng.conn, crosswalk)
    assert _graph_row(eng.conn, graph[0]) is None and _crosswalk_rows(eng.conn, u) == []

    result = eng.restore_snapshot(path, merge=merge)

    assert _graph_row(eng.conn, graph[0]) == want_graph, "the FHIR graph's registration was not restored"
    assert _crosswalk_rows(eng.conn, u) == want_crosswalk, "the crosswalk was not restored"
    assert "Graph_KG.fhir_graphs" not in result["failed_rows"], result["failed_rows"]
    assert "Graph_KG.code_crosswalk" not in result["failed_rows"], result["failed_rows"]


def test_a_replacing_restore_puts_back_the_snapshots_watermarks(eng):
    """`erase_all()` keeps a registration and resets its watermarks; the restored edges are the snapshot's, and so
    are the watermarks that account for them. The crosswalk, which `erase_all()` does not touch, is unchanged."""
    u = _uid()
    _create_fhir_tables(eng.conn)
    graph, crosswalk = _known(u)
    _write(eng.conn, graph, crosswalk)
    want_graph, want_crosswalk = _expected(graph, crosswalk)
    path = _snapshot(eng, u)

    result = eng.restore_snapshot(path, merge=False)

    assert _graph_row(eng.conn, graph[0]) == want_graph
    assert _crosswalk_rows(eng.conn, u) == want_crosswalk
    assert "Graph_KG.fhir_graphs" not in result["failed_rows"], result["failed_rows"]
    assert "Graph_KG.code_crosswalk" not in result["failed_rows"], result["failed_rows"]


def test_a_merge_restore_keeps_the_live_rows_and_adds_the_missing_ones(eng):
    """A merge keeps what the target holds: the registration as it stands now (a later sync's watermark) and a
    crosswalk row's current relation. A row the target lacks comes back, though another row of its code system is
    there."""
    u = _uid()
    _create_fhir_tables(eng.conn)
    graph, crosswalk = _known(u)
    _write(eng.conn, graph, crosswalk)
    path = _snapshot(eng, u)
    cur = eng.conn.cursor()
    cur.execute("UPDATE Graph_KG.fhir_graphs SET wm_rsrc = 9999 WHERE graph_id = ?", [graph[0]])
    cur.execute("UPDATE Graph_KG.code_crosswalk SET relation = 'narrower' WHERE target_node_id = ?", [f"{u}diabetes"])
    cur.execute("DELETE FROM Graph_KG.code_crosswalk WHERE target_node_id = ?", [f"{u}hypertension"])
    eng.conn.commit()

    result = eng.restore_snapshot(path, merge=True)

    live = list(graph)
    live[GRAPH_COLS.index("wm_rsrc")] = 9999
    rows = [list(r) for r in crosswalk]
    rows[0][CROSSWALK_COLS.index("relation")] = "narrower"
    want_graph, want_crosswalk = _expected(live, rows)
    assert _graph_row(eng.conn, graph[0]) == want_graph
    assert _crosswalk_rows(eng.conn, u) == want_crosswalk
    assert "Graph_KG.code_crosswalk" not in result["failed_rows"], result["failed_rows"]
