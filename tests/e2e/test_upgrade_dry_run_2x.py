"""`upgrade_to_4_0_0(dry_run=True)` on a real 2.x install (DEBT entry 10, bug 2).

4.1.0's dry run crashed on a 2.16 and a 2.20 install: the embeddings step read
`nodes.graph_id` (pre-214 has none, SQLCODE -29), the docs step refused because
`docs` has no `graph_id` until `prepare_docs()` adds it, and the `^KG` step refused
because `rdf_labels`/`rdf_props` get theirs from the embeddings step, which a dry run
does not run. Both refusals told the operator to run a call that writes, without
saying so. And the `^KG` step's dry run recompiled `Graph.KG`, which is not read-only.

No test caught it because the 2.x fixtures had no DDL, so nothing could build a 2.x
schema, and no test ran a whole dry run. This one replays each 2.x release's own
`initialize_schema` DDL, rows and globals into `IVGLEGACY` (its own database), runs
the dry run, and checks two things: every step either predicts or says why it
cannot, and nothing in the namespace changed. The frozen 3.2.0 install runs through
the same checks: its `rdf_labels` has no `graph_id` either, so its `^KG` step is
blocked the same way.

    export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
    .venv/bin/pytest tests/e2e/test_upgrade_dry_run_2x.py -p no:cacheprovider
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from tests.e2e.fixtures.old_releases import OLD_RELEASES, RELEASE_IDS

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
PORT = int(os.environ.get("IVG_PORT", "31972"))
NAMESPACE = "IVGLEGACY"
SCHEMA = "Graph_KG"

#: Calls that write. A reason naming one has to say so.
WRITING_CALLS = ("initialize_schema(", "prepare_docs(", "prepare_edge_vectors(")

pytestmark = pytest.mark.e2e


def _ensure_namespace() -> None:
    script = (
        'Set dir="/usr/irissys/mgr/ivglegacy/"\n'
        'If \'##class(Config.Namespaces).Exists("IVGLEGACY") { '
        "Do ##class(%File).CreateDirectoryChain(dir)  "
        "Set db=##class(SYS.Database).%New()  Set db.Directory=dir  Set sc=db.%Save()  "
        'Kill p  Set p("Directory")=dir  Set sc=##class(Config.Databases).Create("IVGLEGACY",.p)  '
        'Kill p  Set p("Globals")="IVGLEGACY",p("Routines")="IVGLEGACY"  '
        'Set sc=##class(Config.Namespaces).Create("IVGLEGACY",.p) }\n'
        'Write "IVGLEGACY_EXISTS=",##class(Config.Namespaces).Exists("IVGLEGACY"),!\n'
        "Halt\n"
    )
    try:
        out = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
            input=script,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"docker unavailable for {CONTAINER}: {exc}")
    if "IVGLEGACY_EXISTS=1" not in out:
        pytest.skip(f"could not create {NAMESPACE} in {CONTAINER}: {out[-400:]}")


def _connect():
    import iris

    return iris.connect("localhost", PORT, NAMESPACE, "_SYSTEM", "SYS")


def _quietly(cursor, sql, params=None) -> bool:
    try:
        cursor.execute(sql, params or [])
        return True
    except Exception:
        return False


def _tables(cursor) -> list:
    cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'",
        [SCHEMA],
    )
    return sorted(str(r[0]) for r in cursor.fetchall())


def _wipe(conn) -> None:
    """No Graph_KG tables, views, procedures or ^KG/^NKG left in the namespace."""
    import iris

    cursor = conn.cursor()
    cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = ?", [SCHEMA]
    )
    for (view,) in cursor.fetchall():
        _quietly(cursor, f"DROP VIEW {SCHEMA}.{view}")
    # Foreign keys decide the order; retry until nothing more drops.
    for _ in range(6):
        left = _tables(cursor)
        if not left:
            break
        for table in left:
            _quietly(cursor, f"DROP TABLE {SCHEMA}.{table} CASCADE")
    assert not _tables(cursor), f"could not clear {SCHEMA}: {_tables(cursor)}"
    native = iris.createIRIS(conn)
    for g in ("KG", "NKG", "IVG.Ledger"):
        native.kill(f"^{g}")
    conn.commit()


def _insert_rows(cursor, release) -> None:
    for table in (
        "nodes",
        "rdf_labels",
        "rdf_props",
        "rdf_edges",
        "rdf_reifications",
        "kg_NodeEmbeddings",
        "kg_EdgeEmbeddings",
    ):
        for row in release.archive_rows(f"{SCHEMA}.{table}"):
            cols = [c for c in row if not (table == "rdf_edges" and c == "edge_id")]
            marks = [
                "TO_VECTOR(?, DOUBLE)" if c == "emb" else "?" for c in cols
            ]
            cursor.execute(
                f"INSERT INTO {SCHEMA}.{table} ({', '.join(cols)}) "
                f"VALUES ({', '.join(marks)})",
                [row[c] for c in cols],
            )
    # The archives carry no docs. One names a node, one names nothing, so the docs
    # prediction has a placement and a quarantine to report.
    cursor.execute(f"INSERT INTO {SCHEMA}.docs (id, text) VALUES (?, ?)", ["n1", "Ada"])
    cursor.execute(f"INSERT INTO {SCHEMA}.docs (id, text) VALUES (?, ?)", ["orphan", "x"])


@pytest.fixture(scope="module")
def legacy_conn():
    pytest.importorskip("iris")
    _ensure_namespace()
    try:
        conn = _connect()
    except Exception as exc:
        pytest.skip(f"{NAMESPACE} not reachable on {PORT}: {exc}")
    yield conn
    conn.close()


@pytest.fixture(params=OLD_RELEASES, ids=RELEASE_IDS)
def install(request, legacy_conn):
    """IVGLEGACY holding exactly what that release's initialize_schema and seed left."""
    import iris

    release = request.param
    statements = release.ddl_statements()
    assert statements, f"{release.tag} was frozen without --ddl"
    _wipe(legacy_conn)
    cursor = legacy_conn.cursor()
    for statement in statements:
        # Same tolerance the release's own initialize_schema had: its ALTERs exist to
        # bring an older table forward and fail against the one its CREATE just made.
        _quietly(cursor, statement)
    _insert_rows(cursor, release)
    legacy_conn.commit()
    release.write_globals(iris.createIRIS(legacy_conn))
    return release, legacy_conn


# --- what "nothing changed" means ----------------------------------------------------


def _rows(cursor, sql, params=None) -> list:
    cursor.execute(sql, params or [])
    return sorted(tuple(str(v) for v in r) for r in cursor.fetchall())


def _walk(native, gname, path=()):
    try:
        if native.isDefined(gname, *path) % 2:
            yield path, native.getString(gname, *path)
    except Exception:
        pass
    after = ""
    while True:
        nxt = native.nextSubscript(False, gname, *path, after)
        if nxt is None or str(nxt) == "":
            return
        yield from _walk(native, gname, path + (nxt,))
        after = nxt


def _state(conn) -> dict:
    import iris

    cursor = conn.cursor()
    state = {
        "columns": _rows(
            cursor,
            "SELECT TABLE_NAME, COLUMN_NAME, IS_NULLABLE, DATA_TYPE, COLUMN_DEFAULT "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ?",
            [SCHEMA],
        ),
        "constraints": _rows(
            cursor,
            "SELECT TABLE_NAME, CONSTRAINT_NAME, CONSTRAINT_TYPE "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = ?",
            [SCHEMA],
        ),
        "indexes": _rows(
            cursor,
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.INDEXES "
            "WHERE TABLE_SCHEMA = ?",
            [SCHEMA],
        ),
        "routines": _rows(
            cursor,
            "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?",
            [SCHEMA],
        ),
        "classes": _rows(
            cursor,
            "SELECT ID, TimeChanged FROM %Dictionary.CompiledClass "
            "WHERE ID %STARTSWITH 'Graph.KG'",
        ),
    }
    for table in _tables(cursor):
        cursor.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{table}")
        state[f"rows:{table}"] = int(cursor.fetchone()[0])
    native = iris.createIRIS(conn)
    for gname in ("^KG", "^NKG", "^IVG.Ledger"):
        state[gname] = sorted((tuple(map(str, p)), v) for p, v in _walk(native, gname))
    cursor.close()
    return state


def _diff(before: dict, after: dict) -> dict:
    return {
        k: (before.get(k), after.get(k))
        for k in set(before) | set(after)
        if before.get(k) != after.get(k)
    }


# --- the tests ----------------------------------------------------------------------


def test_the_fixture_is_the_old_shape(install):
    """Without this the file passes vacuously on a namespace that is already 4.x."""
    release, conn = install
    state = _state(conn)
    columns = {(t, c) for t, c, *_ in state["columns"]}
    assert ("docs", "graph_id") not in columns
    assert ("rdf_labels", "graph_id") not in columns
    assert ("kg_NodeEmbeddings", "id") in columns
    assert (("nodes", "graph_id") in columns) == (release.tag != "v2.16.0")
    assert state["rows:nodes"] == 6
    assert state["^KG"], "no globals written"


def test_a_dry_run_reports_every_step_and_raises_nothing(install):
    from iris_vector_graph.migrations import UPGRADE_STEPS, upgrade_to_4_0_0

    _, conn = install
    report = upgrade_to_4_0_0(conn, dry_run=True)

    assert report.names == UPGRADE_STEPS
    for step in report.steps:
        assert step.report is not None or step.skipped or step.blocked, step


def test_a_dry_run_writes_nothing(install):
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    _, conn = install
    before = _state(conn)
    upgrade_to_4_0_0(conn, dry_run=True)
    assert _diff(before, _state(conn)) == {}


def test_a_blocked_step_says_which_call_writes(install):
    """A dry run exists so that nothing is written before the operator decides. Its
    report cannot then send them to a writing call as if it were a read."""
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    _, conn = install
    report = upgrade_to_4_0_0(conn, dry_run=True)

    assert report.blocked, "a 2.x install has steps a dry run cannot predict"
    for step in report.steps:
        reason = step.blocked_because or ""
        for call in WRITING_CALLS:
            if call in reason:
                assert re.search(r"\bwrites?\b", reason), (step.name, reason)


def test_what_a_2x_dry_run_can_and_cannot_predict(install):
    """Each step either predicts from the tables as they are, or names the missing
    column and the step that supplies it."""
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    release, conn = install
    report = upgrade_to_4_0_0(conn, dry_run=True)

    # Nothing gives rdf_labels/rdf_props a graph_id but the embeddings step's writes.
    kg = report["kg_node_stores"]
    assert kg.blocked and "rdf_labels" in kg.blocked_because
    assert "embeddings" in kg.blocked_because

    assert report["edge_vectors"].report is not None

    if release.tag == "v2.16.0":
        # Pre-214: nodes has no graph_id, and both of the first two steps read it.
        for name in ("embeddings", "docs"):
            step = report[name]
            assert step.blocked, name
            assert "nodes" in step.blocked_because and "graph_id" in step.blocked_because
            assert "initialize_schema(" in step.blocked_because
    else:
        # 2.20 and 3.2: nodes carries graph_id, so both predict from the rows.
        assert report["embeddings"].report is not None
        docs = report["docs"].report
        assert docs is not None
        # `docs` has no graph_id yet, so every row is unplaced: n1 is a default-graph
        # node, "orphan" names no node.
        assert docs.rows_placed == {"": 1}
        assert docs.rows_quarantined == {"no_node": 1}
