"""4.6.0 R296, pinned against the translation and the executor (no container).

docsgate found, on the release head under intersystems-irispython 5.4.0 and 5.4.1:

* `MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 RETURN n.id` answered no rows with SQLCODE -23 in `error`. A WITH's
  ORDER BY wraps the stage in `SELECT ... FROM (<stage>) __ob ORDER BY ...`, and a sort key that read a column of the
  stage's own tables (`n0.node_id`) was put in that outer ORDER BY, where `n0` does not exist. A key reading a
  property (`p2.val`, `rdf_props`) or an earlier stage was already projected into the stage as `__sortN`; one naming
  any other table of the stage was not (`n.node_id`, `id(n)`, `b.node_id`).
* `MATCH (n:KD) WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})` created nothing, raised nothing and answered
  `error` None. A CREATE or MERGE after a WITH that runs once per row (merge_rows: plan_row_create, plan_row_merge)
  and a CREATE run once per matched row (count_create) first run a read, the prefix; the store answers a failed read
  with `IVGResult.error`, which these executors never looked at: the -23 was only logged (`execute_sql error`), the
  prefix "had" no rows, and nothing was written.

The answers are proven live in tests/e2e/test_460_r296_with_order_by_scope_e2e.py.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from iris_vector_graph import GraphWriteError
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult

# An alias the translator gives a table (n0, l1, p2, e3, ...), read as `<alias>.`
_TABLE_REF = re.compile(r"\b([a-z]+\d+)\.")
# where an alias is defined: `FROM <table> <alias>`, `JOIN <table> <alias>`
_TABLE_DEF = re.compile(r"\b(?:FROM|JOIN)\s+\S+\s+(?:AS\s+)?([a-z]+\d+)\b")


def _sql(q: str) -> str:
    t = translate_to_sql(parse_query(q), {})
    return "\n".join(t.sql) if isinstance(t.sql, list) else t.sql


def _outer_order_bys(sql: str) -> list:
    """The ORDER BY of each `(...) __ob` wrapper, up to the end of its line."""
    return [m.group(1) for m in re.finditer(r"\) __ob ORDER BY ([^\n]*)", sql)]


@pytest.mark.parametrize("q", [
    "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 RETURN n.id",
    "MATCH (n:KD) WITH n ORDER BY n.node_id DESC LIMIT 2 RETURN n.id",
    "MATCH (n:KD) WITH n ORDER BY n.node_id RETURN n.id",
    "MATCH (n:KD) WITH n ORDER BY id(n) LIMIT 2 RETURN n.id",
    "MATCH (n:KD) WITH DISTINCT n ORDER BY n.node_id LIMIT 2 RETURN n.id",
    "MATCH (n:KD) WITH n.id AS i, n ORDER BY n.node_id LIMIT 2 RETURN i",
    "MATCH (n:KD) WITH n, count(*) AS c ORDER BY n.node_id LIMIT 2 RETURN n.id, c",
    "MATCH (a:KD)-[:KR]->(b) WITH a, b ORDER BY b.node_id DESC LIMIT 1 RETURN a.id, b.id",
    "MATCH (n:KD) WITH n, n.k AS k ORDER BY k + size(n.node_id) LIMIT 1 RETURN n.id",
    "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 WITH n ORDER BY n.node_id DESC LIMIT 1 RETURN n.id",
    "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 MATCH (n)-[:KR]->(m) RETURN n.id, m.id",
    "MATCH (n:KD) WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})",
    "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 SET n.s = 1",
    "MATCH (n:KD) WITH n ORDER BY n.node_id MERGE (:KM {src: n.id})",
])
def test_the_outer_order_by_names_no_table_of_the_stage(q):
    sql = _sql(q)
    obs = _outer_order_bys(sql)
    assert obs, sql
    for ob in obs:
        free = set(_TABLE_REF.findall(ob)) - set(_TABLE_DEF.findall(ob))
        assert not free, f"{sorted(free)} out of scope in ORDER BY {ob}\n{sql}"


def test_the_sort_key_is_projected_into_the_stage():
    sql = _sql("MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 RETURN n.id")
    assert "(%EXACT(Graph_KG.CY_SORT_KEY(n0.node_id, 'x'))) AS __sort0\nFROM Graph_KG.nodes n0" in sql, sql
    assert ") __ob ORDER BY __sort0 ASC" in sql, sql


def test_a_projected_key_reads_the_with_s_aliases_as_their_sql():
    """Inside the stage's SELECT `k` is a name of that SELECT, not a column: `k + size(n.node_id)` projected
    `k + LENGTH(n0.node_id)` (-29). It reads `(p2.val)`, the SQL `k` names."""
    sql = _sql("MATCH (n:KD) WITH n, n.k AS k ORDER BY k + size(n.node_id) LIMIT 1 RETURN n.id")
    (proj,) = re.findall(r", \((.*)\) AS __sort0\n", sql)
    assert "(p2.val) + " in proj and not re.search(r"\bk\b", proj), sql


def test_a_key_of_the_with_s_own_columns_stays_in_the_outer_order_by():
    """`a + 2` over the WITH's alias reads the stage's column outside the wrapper, as before (WithOrderBy)."""
    sql = _sql("MATCH (n:KD) WITH n.k AS a ORDER BY a + 2 LIMIT 2 RETURN a")
    (ob,) = _outer_order_bys(sql)
    assert "__sort" not in ob and re.search(r"\ba\b", ob), sql


# ── a query ending in a WITH with SKIP reads that WITH's stage ──────────────────


@pytest.mark.parametrize("q", [
    "MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 1 LIMIT 1 CREATE (:KD2 {src: n.id})",
    "MATCH (n:KD) WITH n ORDER BY n.k SKIP 1 CREATE (:KD2 {src: n.id})",
    "MATCH (n:KD) WITH n SKIP 1 CREATE (:KD2 {src: n.id})",
])
def test_the_prefix_of_a_per_row_create_after_skip_reads_its_stage(q):
    """The prefix ends in the WITH (merge_rows builds it with no part after). Its SELECT read FROM
    Stage{len(stages)}, and a SKIP's ROW_NUMBER stage (Stage1_rn) made that Stage2, which does not exist (-30)."""
    from iris_vector_graph.cypher.merge_rows import plan_row_create

    plan = plan_row_create(parse_query(q), {})
    assert plan is not None and plan.prefix.query_parts[-1].with_clause is not None
    sql = translate_to_sql(plan.prefix, {}).sql
    assert "Stage1_rn AS (" in sql and "\nStage1 AS (" in sql, sql
    assert "FROM Stage1\n" in sql and "Stage2" not in sql, sql


def test_the_prefix_of_a_per_row_merge_after_skip_reads_its_stage():
    from iris_vector_graph.cypher.merge_rows import plan_row_merge

    plan = plan_row_merge(parse_query("MATCH (n:KD) WITH n.id AS i ORDER BY i SKIP 1 MERGE (:KM {src: i})"), {})
    assert plan is not None
    sql = translate_to_sql(plan.prefix, {}).sql
    assert "Stage2" not in sql and re.search(r"FROM Stage1\b", sql), sql


# ── a MERGE after a WITH that carries a node, reading its properties ───────────


@pytest.mark.parametrize("q", [
    "MATCH (n:KD) WITH n MERGE (:KM {src: n.id})",
    "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 MERGE (:KM {src: n.id})",
    "MATCH (n:KD) WITH n, n.k AS k MERGE (:KM {src: n.id, k: k})",
])
def test_a_merge_reading_a_carried_node_s_property_runs_once_per_row(q):
    """The static MERGE bound `n.id`'s AST as a parameter ("Unsupported argument type", GraphWriteError), with or
    without the ORDER BY. As R286 carries them for a CREATE, the prefix reads the properties and each row's MERGE
    gets its values inlined."""
    from iris_vector_graph.cypher import ast
    from iris_vector_graph.cypher.merge_rows import plan_row_merge

    plan = plan_row_merge(parse_query(q), {})
    assert plan is not None
    assert ("n", "id") in plan.prop_refs.values()
    (col,) = [c for c, r in plan.prop_refs.items() if r == ("n", "id")]
    assert col in plan.row_vars and col in plan.typed_cols
    items = {i.alias: i.expression for i in plan.prefix.return_clause.items}
    assert items[col] == ast.PropertyReference(variable="n", property_name="id")
    row = {c: "d1" if c == col else 3 for c in plan.row_vars}
    bound = plan.bind(row)
    (merge,) = bound.query_parts[0].clauses
    assert merge.pattern.nodes[0].properties["src"] == ast.Literal(value="d1")


@pytest.mark.parametrize("q", [
    "MATCH (n:KD) WITH n MERGE (n)-[:KR3]->(:KM)",  # the node itself: stays static
    "MATCH (n:KD) WITH n MERGE (m:KM {src: n.id}) RETURN n",  # a RETURN of the node
    "MATCH (n:KD) WITH n MERGE (m:KM {src: n.id}) ON CREATE SET m.v = n.k",  # read in ON CREATE
    "MATCH (n:KD) WITH n MERGE (:KM {src: n})",  # the node as a value
])
def test_other_uses_of_a_carried_node_keep_the_static_merge(q):
    from iris_vector_graph.cypher.merge_rows import plan_row_merge

    assert plan_row_merge(parse_query(q), {}) is None


# ── a write whose prefix read fails raises ─────────────────────────────────────

_FAILED = (
    "<SQL ERROR>; Details: [SQLCODE: <-23>:<Label is not listed among the applicable tables>] "
    "[%msg: < Label 'N0' is not listed among the applicable tables>]"
)


@pytest.fixture
def eng(monkeypatch):
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine(MagicMock(), store=MagicMock())
    monkeypatch.setattr(e, "_probe_if_idle", lambda: None, raising=True)
    e.native_cypher = False
    # every read the store runs fails as IRIS's -23 did; a write would reach execute_transaction
    e._store.execute_sql.return_value = IVGResult(columns=[], rows=[], error=_FAILED)
    e._store.execute_transaction.return_value = IVGResult(columns=[], rows=[])
    return e


@pytest.mark.parametrize("q", [
    # plan_row_create: the WITH's rows, read first
    "MATCH (n:KD) WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})",
    "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 CREATE (n)-[:KR2]->(:KD2)",
    # plan_row_merge: the WITH's values, read first
    "MATCH (n:KD) WITH n.id AS i ORDER BY i MERGE (:KM {src: i})",
    # count_create: the MATCH's count, read first
    "MATCH (a:KD) CREATE (:KX)",
])
@pytest.mark.parametrize("raise_on_error", [False, True])
def test_a_write_whose_prefix_read_fails_raises_and_writes_nothing(eng, q, raise_on_error):
    with pytest.raises(GraphWriteError) as exc:
        eng.execute_cypher(q, raise_on_error=raise_on_error)
    assert exc.value.sqlcode == -23
    assert "nothing written" in str(exc.value)
    assert "Label 'N0' is not listed" in str(exc.value)
    eng._store.execute_transaction.assert_not_called()


def test_a_per_row_write_answering_an_error_raises(eng):
    """A store that answers a failed write with `error` (not the IRIS store, which raises) still fails the query."""
    eng._store.execute_sql.return_value = IVGResult(columns=["i"], rows=[["d1"], ["d2"]])
    eng._store.execute_transaction.return_value = IVGResult(columns=[], rows=[], error=_FAILED)
    with pytest.raises(GraphWriteError) as exc:
        eng.execute_cypher("MATCH (n:KD) WITH n.id AS i MERGE (:KM {src: i})")
    assert exc.value.sqlcode == -23
    assert eng._store.execute_transaction.call_count == 1  # the second row is not run


def test_a_read_still_answers_its_error():
    """A read keeps its contract: `error` set, no exception without raise_on_error (USER_GUIDE "Errors")."""
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine(MagicMock(), store=MagicMock())
    e._probe_if_idle = lambda: None
    e.native_cypher = False
    e._store.execute_sql.return_value = IVGResult(columns=[], rows=[], error=_FAILED)
    r = e.execute_cypher("MATCH (n:KD) WITH n ORDER BY n.k LIMIT 2 RETURN n.id")
    assert r.error == _FAILED and r.rows == []
