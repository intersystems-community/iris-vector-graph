"""4.6.0 R156: a `CALL ivg.* YIELD <node>` followed by MATCH or WITH reads only columns its CTE has.

`CALL ivg.ppr(['a']) YIELD node, score MATCH (node)-[:R]->(m) RETURN m` joined `PPR.node_id`, and the CTE's columns are
`node` and `score`: IRIS refused the statement with SQLCODE -29 (`Field 'PPR.NODE_ID' not found`), so the query
answered nothing. R118 fixed the edge join for `ivg.vector.search` alone; a label (`MATCH (node:L)`) or a property
(`node.name`) on the yielded node still read `VecSearch.node_id`. Every procedure that yields a node is checked here:
each `<CTE>.<column>` the translation reads must be a column the CTE selects.

The answers on a live graph are in `tests/e2e/test_460_proc_yield_node_e2e.py`.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

#: procedure call, the variable it yields the node as, the CTE (or derived table) it reads from
CALLS = {
    "vector.search": ("CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 2) YIELD node, score", "node", "VecSearch"),
    "neighbors": ("CALL ivg.neighbors(['a'], 'R', 'out') YIELD neighbor", "neighbor", "Neighbors"),
    "ppr": ("CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score", "node", "PPR"),
    "bm25.search": ("CALL ivg.bm25.search('idx', 'apple', 5) YIELD node, score", "node", "BM25"),
    "ivf.search": ("CALL ivg.ivf.search('idx', [1.0, 0.0], 5, 1) YIELD node, score", "node", "IVF_SEARCH"),
    "retrieve": ("CALL ivg.retrieve('apple', 5, 'idx', 'L') YIELD node, rrf_score", "node", "Retrieve"),
    "degreeCentrality": ("CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, score", "node", "DegCent"),
    "betweenness": ("CALL ivg.betweenness({}) YIELD node, score", "node", "Betweenness"),
    "closeness": ("CALL ivg.closeness({}) YIELD node, score", "node", "Closeness"),
    "eigenvector": ("CALL ivg.eigenvector({}) YIELD node, score", "node", "Eigenvector"),
    "leiden": ("CALL ivg.leiden({}) YIELD node, community", "node", "Leiden"),
    "triangleCount": ("CALL ivg.triangleCount({}) YIELD node, triangles", "node", "TriangleCount"),
    "scc": ("CALL ivg.scc({}) YIELD node, component", "node", "SCC"),
    "kcore": ("CALL ivg.kcore({}) YIELD node, coreness", "node", "KCore"),
}

#: what follows the CALL: an edge from the node, a label and a property on it, the same after WITH, and RETURN of it
SHAPES = {
    "match_edge": "MATCH ({v})-[:R]->(m) RETURN m.node_id AS m",
    "match_label_prop": "MATCH ({v}:L)-[:R]->(m) WHERE {v}.name = 'A' RETURN {v}.name AS n, m.node_id AS m",
    "with_match": "WITH {v} MATCH ({v}:L)-[:R]->(m) RETURN {v}.name AS n, m.node_id AS m",
    "return": "RETURN {v}",
}


def _select_list(text: str) -> list:
    """The items of a SELECT list (`text` starts after SELECT), split at top-level commas, up to FROM or a newline."""
    items, depth, cur, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        if depth == 0 and (ch == "\n" or text.startswith(" FROM ", i)):
            break
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    return items + [cur]


def _cte_columns(sql: str, name: str) -> set:
    """The column names `name`'s CTE (or derived table) selects: its first SELECT list."""
    head = r"\s*SELECT (?:TOP \d+ )?(?:DISTINCT )?"
    m = re.search(rf"\b{name} AS \({head}", sql)
    if m:
        start = m.end()
    else:
        # a derived table, `(SELECT ...) <name>`: walk back from its closing parenthesis to the one that opens it
        end = re.search(rf"\)\s*{name}\b", sql)
        assert end, f"no CTE or derived table {name} in:\n{sql}"
        depth, i = 0, end.start()
        while True:
            depth += {")": 1, "(": -1}.get(sql[i], 0)
            if depth == 0:
                break
            i -= 1
        start = re.match(head, sql[i + 1:]).end() + i + 1
    cols = set()
    for part in _select_list(sql[start:]):
        part = part.strip()
        alias = re.search(r"\bAS (\w+)$", part)
        cols.add((alias.group(1) if alias else part.split(".")[-1]).lower())
    return cols


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("proc", sorted(CALLS))
def test_every_cte_column_read_is_one_the_cte_selects(proc, shape):
    call, var, cte = CALLS[proc]
    sql = translate_to_sql(parse_query(f"{call} {SHAPES[shape].format(v=var)}"), {"v": [1.0, 0.0]}).sql
    sql = sql if isinstance(sql, str) else "\n".join(sql)
    columns = _cte_columns(sql, cte)
    read = {c.lower() for c in re.findall(rf"\b{cte}\.(\w+)", sql)}
    assert read <= columns, f"{cte} has {sorted(columns)}, the translation reads {sorted(read - columns)}:\n{sql}"
