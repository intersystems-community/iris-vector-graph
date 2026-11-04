"""4.6.0 R167: `YIELD <column> AS <name>` binds `<name>` on every `ivg.*` procedure.

`CALL ivg.ppr(['a']) YIELD node AS n RETURN n` raised `SyntaxError: Undefined variable: n`: each procedure's
translation registered every yield item as a variable, and an aliased item is the pair `('node', 'n')`, so the
variable bound was the pair and `n` was never bound. The procedure table (`translator._IVG_PROCEDURES`) is walked
here, so a procedure added to it without a case below fails `test_every_procedure_in_the_table_has_a_case`.

The answers on a live graph are in `tests/e2e/test_460_proc_yield_alias_e2e.py`.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher import translator
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

#: procedure -> (the call, the columns it yields: (column, kind)); kind "node" is the yielded node, "value" a scalar
CALLS = {
    "ivg.vector.search": ("CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 2)", [("node", "node"), ("score", "value")]),
    "ivg.neighbors": ("CALL ivg.neighbors(['a'], 'R', 'both')", [("neighbor", "node")]),
    "ivg.ppr": ("CALL ivg.ppr(['a'], 0.85, 20)", [("node", "node"), ("score", "value")]),
    "ivg.bm25.search": ("CALL ivg.bm25.search('idx', 'apple', 5)", [("node", "node"), ("score", "value")]),
    "ivg.ivf.search": ("CALL ivg.ivf.search('idx', [1.0, 0.0], 5, 1)", [("node", "node"), ("score", "value")]),
    "ivg.retrieve": ("CALL ivg.retrieve('apple', 5, 'idx', 'L')", [("node", "node"), ("rrf_score", "value")]),
    "ivg.shortestPath.weighted": (
        "CALL ivg.shortestPath.weighted('a', 'b')",
        [("path", "value"), ("totalCost", "value")],
    ),
    "ivg.degreeCentrality": ("CALL ivg.degreeCentrality({direction: 'out'})", [("node", "node"), ("score", "value")]),
    "ivg.betweenness": ("CALL ivg.betweenness({})", [("node", "node"), ("score", "value")]),
    "ivg.closeness": ("CALL ivg.closeness({})", [("node", "node"), ("score", "value")]),
    "ivg.eigenvector": ("CALL ivg.eigenvector({})", [("node", "node"), ("score", "value")]),
    "ivg.leiden": ("CALL ivg.leiden({})", [("node", "node"), ("community", "value")]),
    "ivg.triangleCount": ("CALL ivg.triangleCount({})", [("node", "node"), ("triangles", "value")]),
    "ivg.scc": ("CALL ivg.scc({})", [("node", "node"), ("component", "value")]),
    "ivg.kcore": ("CALL ivg.kcore({})", [("node", "node"), ("coreness", "value")]),
}


def _sql(query):
    sql = translate_to_sql(parse_query(query), {"v": [1.0, 0.0]}).sql
    return sql if isinstance(sql, str) else "\n".join(sql)


def _aliased(proc):
    call, cols = CALLS[proc]
    names = {c: f"x_{i}" for i, (c, _) in enumerate(cols)}
    return call, cols, names, f"{call} YIELD " + ", ".join(f"{c} AS {names[c]}" for c, _ in cols)


def test_every_procedure_in_the_table_has_a_case():
    assert set(translator._IVG_PROCEDURES) == set(CALLS)


@pytest.mark.parametrize("proc", sorted(CALLS))
def test_return_of_every_aliased_column(proc):
    call, cols, names, head = _aliased(proc)
    plain = _sql(f"{call} YIELD {', '.join(c for c, _ in cols)} RETURN {', '.join(c for c, _ in cols)}")
    sql = _sql(f"{head} RETURN {', '.join(names[c] for c, _ in cols)}")
    # the RETURN reads each alias where the unaliased query read the column, under the alias's own name
    for c, kind in cols:
        out = f"{names[c]}_id" if kind == "node" else names[c]
        assert re.search(rf"\bAS {out}\b", sql), f"{out} not projected:\n{sql}"
        assert re.search(rf"\bAS {c if kind == 'value' else c + '_id'}\b", plain)


#: the CTE (or derived table) each node-yielding procedure reads from (tests/unit/test_460_proc_yield_node_columns.py)
CTE = {
    "ivg.vector.search": "VecSearch", "ivg.neighbors": "Neighbors", "ivg.ppr": "PPR", "ivg.bm25.search": "BM25",
    "ivg.ivf.search": "IVF_SEARCH", "ivg.retrieve": "Retrieve", "ivg.degreeCentrality": "DegCent",
    "ivg.betweenness": "Betweenness", "ivg.closeness": "Closeness", "ivg.eigenvector": "Eigenvector",
    "ivg.leiden": "Leiden", "ivg.triangleCount": "TriangleCount", "ivg.scc": "SCC", "ivg.kcore": "KCore",
}


@pytest.mark.parametrize("proc", sorted(CTE))
def test_an_aliased_node_is_the_procedure_s_node(proc):
    """`MATCH (x)` after `YIELD node AS x` joins the procedure's node, not a fresh, unbound `x` over every node."""
    call, cols, names, head = _aliased(proc)
    v = names[cols[0][0]]
    sql = _sql(f"{head} MATCH ({v}:L)-[:R]->(m) WHERE {v}.name = 'A' RETURN {v}.name AS n, m.node_id AS m")
    assert re.search(rf"\b{CTE[proc]}\.node_id\b", sql), sql
    assert not re.search(r"FROM nodes n\d+\b", sql), f"{v} matched every node:\n{sql}"


def test_a_value_alias_can_be_filtered_and_ordered():
    sql = _sql("CALL ivg.ppr(['a']) YIELD node AS n, score AS sc WITH n, sc WHERE sc > 0.1 RETURN n, sc ORDER BY sc DESC")
    assert re.search(r"\bsc > 0\.1|\bsc > CAST", sql) and "ORDER BY" in sql, sql


def test_two_columns_swapped_by_alias():
    """`YIELD node AS score, score AS node`: `score` is the node and `node` the number, and no column is doubled."""
    sql = _sql("CALL ivg.ppr(['a']) YIELD node AS score, score AS node RETURN score, node")
    assert "PPR_Y.node AS score, PPR_Y.score AS node" in sql, sql
    assert "score AS score_id" in sql and re.search(r"\bnode AS node\n", sql), sql


def test_an_alias_bound_twice_is_refused():
    with pytest.raises(SyntaxError, match="already bound"):
        _sql("CALL ivg.ppr(['a']) YIELD node AS x, score AS x RETURN x")


@pytest.mark.parametrize("name", ["s", "label", "val", "p", "node_id"])
@pytest.mark.parametrize("column", ["node", "score"])
def test_an_alias_that_names_a_store_column_is_refused(name, column):
    """A yielded column is read unqualified. As `s`, `RETURN x` read rdf_labels' own `s` in its label lookup (every
    label the node's), and `RETURN x.name, s` was ambiguous with the joined property row (SQLCODE -27, live)."""
    other = "score AS sc" if column == "node" else "node AS x"
    with pytest.raises(SyntaxError, match="column of IVG's tables"):
        _sql(f"CALL ivg.ppr(['a']) YIELD {column} AS {name}, {other} RETURN {name}")


def test_a_value_alias_of_another_name_is_kept():
    sql = _sql("CALL ivg.ppr(['a']) YIELD node AS n, score AS sc RETURN n, sc")
    assert "PPR_Y.score AS sc" in sql, sql
