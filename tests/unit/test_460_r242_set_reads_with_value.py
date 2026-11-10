"""4.6.0 R242: a SET reads a value the WITH before it projected.

`MATCH (n) WITH n.k AS k, n SET n.r = k` raised UndefinedVariable. Each node's value is its own row of the stage: the
pair (node, value) is read once ahead of the writes by R187's `__capture_pairs__`, and the SET writes
`__PAIR_<key>__[row]`, which the executor spells as a CASE over the ids. A CASE holds one SQL type, and an integer
literal alone is stored as its text: the executor writes each type of value in its own statement, an integer as
`CAST(n AS BIGINT)` and a float as `CAST('x' AS DOUBLE)`.

tests/e2e/test_460_r242_set_reads_with_value_e2e.py checks the stored values and their types.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.stores.iris_sql_store import _expand_captured_ids, _pair_value_sql

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes)\b")


def _stmts(q):
    r = translate_to_sql(parse_query(q), {})
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


@pytest.mark.parametrize(
    "q, col",
    [
        ("MATCH (n:X) WITH n.k AS k, n SET n.r = k", "k"),
        ("MATCH (n:X) WITH n, n.k * 2 AS d SET n.r = d", "d"),
        ("MATCH (n:X) WITH n, n.k AS k WHERE k > 4 SET n.r = k", "k"),
        ("MATCH (n:X) WITH n.k AS k, n SET n += {r: k}", "k"),
    ],
)
def test_the_set_writes_each_nodes_own_value(q, col):
    stmts = _stmts(q)
    pairs = [s for s, _ in stmts if s.startswith("__capture_pairs__ ")]
    assert len(pairs) == 1, stmts
    key = pairs[0].split("\n", 1)[0].split()[1]
    # through CY_TYPED, which the executor decodes (4.6.0 R285: 5.4.1 fetched a stored number as text)
    # and the node's graph, the stage's __g_n, where the namespace may hold several (the writes run per graph)
    assert pairs[0].rstrip().endswith(
        f"SELECT Stage1.n, Graph_KG.CY_TYPED(Stage1.{col}), Stage1.__g_n\nFROM Stage1"
    ), pairs[0]
    # the UPDATE and the INSERT of key r read the pair's value
    writes = [s for s, p in stmts if ("INTO rdf_props" in s or s.startswith("UPDATE rdf_props")) and "r" in p]
    assert len(writes) == 2 and all(f"__PAIR_{key}__[" in s for s in writes), writes


def test_a_constant_is_written_at_its_type():
    stmts = _stmts("MATCH (n:X) WITH n, 7 AS c SET n.r = c")
    assert any(re.search(r"CAST\(__PAIR_\w+__\[[^\]]+\] AS BIGINT\)", s) for s, _ in stmts), stmts


def test_each_type_of_value_is_written_in_its_own_statement():
    stmt = "UPDATE rdf_props SET val = __PAIR_v0__[rdf_props.s] WHERE s IN (__IDS_v0__) AND \"key\" = ?"
    captured = {"v0": ["a", "b", "c", "d"], "v0#pairs": {"a": 3, "b": "hi", "c": 2.0, "d": None}}
    out = _expand_captured_ids(stmt, captured)
    assert len(out) == 4, out
    by_id = {re.search(r"WHEN '(\w)'", s).group(1): s for s in out}
    assert "THEN CAST(3 AS BIGINT) END" in by_id["a"] and "IN ('a')" in by_id["a"]
    assert "THEN 'hi' END" in by_id["b"] and "IN ('b')" in by_id["b"]
    assert "THEN CAST('2.0' AS DOUBLE) END" in by_id["c"]
    assert "THEN NULL END" in by_id["d"]


def test_a_pair_of_ids_runs_as_one_statement():
    stmt = "UPDATE rdf_props SET val = (SELECT x FROM t WHERE id = __PAIR_v0__[s]) WHERE s IN (__IDS_v0__)"
    out = _expand_captured_ids(stmt, {"v0": ["a", "b"], "v0#pairs": {"a": "m1", "b": "m2"}})
    assert len(out) == 1 and "WHEN 'a' THEN 'm1' WHEN 'b' THEN 'm2'" in out[0], out
    # 4.7.0: a boolean pair (a run-time list's row read as JSON) as a SET of true stores it, not Python's 'True'
    assert _pair_value_sql(True) == "'true'" and _pair_value_sql(None) == "NULL"
