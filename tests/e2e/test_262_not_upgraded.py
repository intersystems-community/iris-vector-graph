"""Spec 262 US4 (T047, T048): the 4.6.0 package on a namespace nobody has upgraded, and the manual route from a
namespace holding IVG tables in two schemas to one.

T047: IVG262UPG built by v4.5.1's own code; 4.6.0's engine, no `initialize_schema()`. Its first Cypher query raises
the FR-006a error, not a missing-function error, sends nothing, and changes nothing; the 4.5.1 `SQLUser` objects keep
answering SQL that calls them.

T048: IVG262UPG built by v4.5.1 with IVG tables in `Graph_KG` and in `RAG`. The consolidation route in
`docs/migration/v4.6.0.md` (drop the other schema's IVG tables with the SQL given there, record `RAG`, drop the other
schema's ledger tables, rebuild the adjacency index) is run as written: the SQL is read out of the document, so the
two cannot drift apart.

4.6.0 R178: the same route on a namespace whose 4.5.1 ledger (enabled on a RAG engine, two changesets committed) keeps
its history in `Graph_KG.ledger_revisions` and `ledger_stats`, where 4.5.x put them whatever the prefix. The drop block
dropped those tables before step 3's `initialize_schema()` could copy them (R114); measured, the drop itself failed
there (ERROR #5764: the revision table refuses row deletes). They now stay until step 3 has copied them; the
namespace's IVG tables are then in `RAG` alone, so step 3 records it as `inferred` (no ambiguity refusal: the probe
counts schemas holding `nodes` and `rdf_edges`), and step 4 drops them with `%NODELDATA`.

The namespace is IVG262UPG, or IVG262_NOTUPG_NS (IVG262* only), so a run beside another uses its own.
"""

from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG262_NOTUPG_NS", "IVG262UPG")
DOC = Path(__file__).resolve().parents[2] / "docs" / "migration" / "v4.6.0.md"
#: The seed's `knows` edges in the default graph (generate_old_snapshot): n1 Ada -> n2 Bes, n2 Bes -> n3 Cyd.
CMP_QUERY = "MATCH (a)-[:knows]->(b) WHERE a.name < b.name RETURN a.name, b.name"
CMP_ANSWER = [["Ada", "Bes"], ["Bes", "Cyd"]]


def _engine(conn, schema=None):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, schema_prefix=schema, native_cypher=False)


def _held(conn) -> dict:
    """The schemas holding IVG tables, with their counts: setup's own probe, run with the first-access check off."""
    engine = _engine(conn)
    with engine._schema_check_suspended():
        return engine._ivg_table_schemas()


def _build(**kw):
    from build_release_namespace import build

    return build("v4.5.1", NS, **kw)


def _sorted_rows(rows) -> list:
    return sorted([None if v is None else str(v) for v in row] for row in rows)


# --------------------------------------------------------------------------- T047

@pytest.fixture(scope="module")
def not_upgraded():
    _build()
    conn = ns262.connect(NS)
    yield conn
    conn.close()


@pytest.mark.parametrize("raise_on_error", [True, False])
def test_the_first_cypher_query_raises_the_not_upgraded_error_and_changes_nothing(not_upgraded, raise_on_error):
    from iris_vector_graph import IVGSchemaError

    before = ns262.deep_snapshot(not_upgraded)
    engine = _engine(not_upgraded)
    with pytest.raises(IVGSchemaError) as raised:
        engine.execute_cypher(CMP_QUERY, {}, raise_on_error=raise_on_error)
    text = str(raised.value)
    for part in ("set up by an earlier IVG release", "initialize_schema()", "initialize_schema(dry_run=True)", NS):
        assert part in text, text
    assert "CY_CMP" not in text and "SQLCODE" not in text  # not the missing-function error 4.6.0's SQL would meet
    assert ns262.deep_snapshot(not_upgraded) == before


def test_status_says_why_without_raising(not_upgraded):
    s = _engine(not_upgraded).status()
    assert s.schema is None and s.legacy_objects > 0
    assert any("set up by an earlier IVG release" in e for e in s.errors), s.errors
    assert "IVG Schema:      none recorded" in s.report()


def test_the_451_sql_objects_keep_answering(not_upgraded):
    _engine(not_upgraded)  # an engine built on the connection changes nothing about it
    assert ns262.rows(not_upgraded, "SELECT SQLUser.STR_SPLIT('a,b', ',')")[0][0] == '["a","b"]'
    assert ns262.rows(not_upgraded, "SELECT SQLUser.CY_CMP('a', 'b')")[0][0] is not None


def test_the_dry_run_is_the_way_forward_and_writes_nothing(not_upgraded):
    before = ns262.deep_snapshot(not_upgraded)
    dry = _engine(not_upgraded).initialize_schema(dry_run=True)
    assert (dry["schema"], dry["schema_recorded"], dry["dry_run"]) == ("Graph_KG", "inferred", True)
    assert ns262.deep_snapshot(not_upgraded) == before


def test_after_setup_the_same_engine_answers(not_upgraded):
    engine = _engine(not_upgraded)
    engine.initialize_schema()
    assert _sorted_rows(engine.execute_cypher(CMP_QUERY, {}, raise_on_error=True).rows) == CMP_ANSWER


# --------------------------------------------------------------------------- T048

def doc_block(marker: str) -> str:
    """The fenced block after `<!-- marker -->` in the migration notes."""
    text = DOC.read_text(encoding="utf-8")
    m = re.search(rf"<!-- {re.escape(marker)} -->\s*```(\w+)\n(.*?)```", text, re.S)
    assert m, f"no block marked {marker} in {DOC}"
    return textwrap.dedent(m.group(2))


def doc_statements(marker: str, other: str) -> list:
    """The SQL block's statements, with the document's placeholder schema `OTHER` named."""
    sql = doc_block(marker).replace("OTHER", other)
    return [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]


def _run_sql(conn, statements) -> None:
    cur = conn.cursor()
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()
    cur.close()


@pytest.fixture(scope="module")
def two_schemas(not_upgraded):
    _build()
    _build(keep=True, seed=False, schema_prefix="RAG")  # the RAG tables, with the Graph_KG rows copied in
    conn = ns262.connect(NS)
    yield conn
    conn.close()


def test_the_documented_consolidation_route_ends_with_one_schema_and_the_graphs_answers(two_schemas):
    from iris_vector_graph import IVGSchemaError

    conn = two_schemas
    assert sorted(_held(conn)) == ["Graph_KG", "RAG"]
    with pytest.raises(IVGSchemaError):
        _engine(conn).initialize_schema()  # step 0: the error that sends the operator to the route

    from iris_vector_graph import IRISGraphEngine

    # 2. the SQL that drops the other schema's IVG tables, in the order the document gives
    _run_sql(conn, doc_statements("consolidation-drop", "Graph_KG"))

    # 3. record the schema kept (the document's Python block, with the names it uses)
    namespace = {"conn": conn, "IRISGraphEngine": IRISGraphEngine, "EMBEDDING_DIMENSION": 8}
    exec(doc_block("consolidation-record"), namespace)  # noqa: S102 - the document's own code, run as written
    assert namespace["status"]["schema_recorded"] == "inferred"
    assert ns262.native(conn).get("^IVG.Deploy", "schema") == "RAG"

    # 4. drop the other schema's ledger tables, now that step 3 has read them
    _run_sql(conn, doc_statements("consolidation-ledger-drop", "Graph_KG"))

    # 5. rebuild the adjacency index from the schema kept (the document's Python block)
    exec(doc_block("consolidation-rebuild"), namespace)  # noqa: S102

    held = _held(conn)
    assert list(held) == ["RAG"], held
    assert ns262.rows(conn, "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'"
                      )[0][0] == 0
    engine = _engine(conn, "RAG")
    assert _sorted_rows(engine.execute_cypher(CMP_QUERY, {}, raise_on_error=True).rows) == CMP_ANSWER
    # ^KG holds the kept schema's edges and no others: an edge only the dropped schema had is not traversable
    assert engine.get_edge_count() == held["RAG"]["rdf_edges"]
    edges = ns262.rows(conn, "SELECT COUNT(*) FROM RAG.rdf_edges")[0][0]
    kg = int(ns262.native(conn).classMethodValue("Graph.KG.Traversal", "KGEdgeCount", 100000))
    assert kg == edges, (kg, edges)


# --------------------------------------------------------------------------- R178

def _revisions(conn, schema):
    return [[int(r[0]), str(r[1]), str(r[2]), int(r[3] or 0)] for r in ns262.rows(
        conn, f"SELECT seq, revision_id, kind, op_count FROM {schema}.ledger_revisions ORDER BY seq")]


@pytest.fixture(scope="module")
def two_schemas_with_ledger():
    from build_release_namespace import run_ledger_commits

    _build()
    _build(keep=True, seed=False, schema_prefix="RAG")
    committed = run_ledger_commits("v4.5.1", NS, "RAG")
    conn = ns262.connect(NS)
    yield conn, committed
    conn.close()


def test_the_consolidation_route_keeps_the_ledger_history(two_schemas_with_ledger):
    from iris_vector_graph import IRISGraphEngine

    conn, committed = two_schemas_with_ledger
    before = _revisions(conn, "Graph_KG")
    assert [r[2] for r in before] == ["genesis", "changeset", "changeset"]
    assert [[seq, kind, ops] for seq, _rid, kind, ops in before] == committed["revisions"]
    assert ns262.rows(conn, "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'RAG' "
                            "AND TABLE_NAME = 'ledger_revisions'")[0][0] == 0  # 4.5.1 made no RAG ledger table

    # 2. drop the other schema's IVG tables (OTHER = Graph_KG)
    _run_sql(conn, doc_statements("consolidation-drop", "Graph_KG"))
    # 3. record RAG: its tables are the only IVG tables, and the ledger rows left in Graph_KG are copied
    namespace = {"conn": conn, "IRISGraphEngine": IRISGraphEngine, "EMBEDDING_DIMENSION": 8}
    exec(doc_block("consolidation-record"), namespace)  # noqa: S102 - the document's own code, run as written
    status = namespace["status"]
    assert (status["schema"], status["schema_recorded"]) == ("RAG", "inferred")
    assert status["ledger_history_copied"] == {"ledger_revisions": 3, "ledger_stats": 1}
    assert _revisions(conn, "RAG") == before
    # 4. then the other schema's ledger tables go; 5. the adjacency index is rebuilt
    _run_sql(conn, doc_statements("consolidation-ledger-drop", "Graph_KG"))
    exec(doc_block("consolidation-rebuild"), namespace)  # noqa: S102

    assert ns262.rows(conn, "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'"
                      )[0][0] == 0
    # the document's word for what %NODELDATA leaves: the 4.5.1 rows' globals, read by nothing now
    nat = ns262.native(conn)
    assert int(nat.isDefined("^Graph.KG.LedgerRevisionD")) and int(nat.isDefined("^Graph.KG.LedgerStatsD"))
    engine = _engine(conn, "RAG")
    assert [r.revision_id for r in engine.ledger.history(limit=10).revisions] == [rid for _s, rid, _k, _o in before]
    assert engine.ledger.head().seq == 3
    report = engine.ledger.verify()
    assert (report.result, report.differences) == ("equal", [])
