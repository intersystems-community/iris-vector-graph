"""4.7.0 row 2: the server node loader's per-row lookups read the node's own rows, not every row of a label or key.

Graph.KG.EdgeScan.BulkIngestNodesSQL checks each label and property row before it inserts it. In a new namespace (the
tables' statistics taken near empty) the optimizer answered `... WHERE graph_id = '' AND s = :id AND "key" = :key`
from idx_props_key, every node holding the key, and the label check from idx_labels_label, every node of the label
(measured with EXPLAIN on 2026.3): each 1,000-node chunk took longer than the last, and 10,000 label-only nodes took
21.9 s where repeated loads took 1 s (R089's `id` row on every node doubled it). `%IGNOREINDEX` takes those two out
of the plan; the answers are in tests/e2e/test_470_node_load_new_namespace_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "EdgeScan.cls"


def _method(name: str) -> str:
    text = SRC.read_text()
    start = text.index(f"ClassMethod {name}(")
    return text[start:text.index("\n}\n", start)]


def test_each_lookup_ignores_the_non_selective_index():
    body = _method("BulkIngestNodesSQL")
    lookups = re.findall(r"&sql\(SELECT 1 INTO :\w+ FROM ([^)]*?) WHERE", body)
    assert lookups == [
        "%IGNOREINDEX Graph_KG.rdf_labels.idx_labels_label Graph_KG.rdf_labels",
        "%IGNOREINDEX Graph_KG.rdf_props.idx_props_key, Graph_KG.rdf_props.idx_props_key_valkey Graph_KG.rdf_props",
    ], lookups



def test_the_python_writers_guards_ignore_the_same_indexes():
    """create_node, store_node and the bulk SQL path's guards had the same plan on a new namespace (EXPLAIN on 2026.3:
    idx_props_key / idx_labels_label): 3,000 create_node calls took 0.86 rising to 1.72 s per 500, now about 0.5 s."""
    from iris_vector_graph.schema import GraphSchema, guard_from

    assert guard_from("RAG.rdf_props") == (
        "%IGNOREINDEX RAG.rdf_props.idx_props_key, RAG.rdf_props.idx_props_key_valkey RAG.rdf_props")
    assert guard_from("RAG.rdf_labels") == "%IGNOREINDEX RAG.rdf_labels.idx_labels_label RAG.rdf_labels"
    with pytest.raises(ValueError):
        guard_from("RAG.rdf_edges")
    for table in ("rdf_props", "rdf_props_with_graph", "rdf_labels", "rdf_labels_with_graph"):
        sql = GraphSchema.get_bulk_insert_sql(table)
        got = re.search(r"NOT EXISTS \(SELECT 1 FROM (.*?) WHERE", sql).group(1)
        assert got == guard_from("Graph_KG." + table.replace("_with_graph", "")), (table, got)


WRITES = [
    "UNWIND $rows AS r CREATE (:UW {uwk: r, name: 'x'})",
    "CREATE (:L {id: 'x', a: 1})",
    "MATCH (n:L) SET n.b = 1",
    "MATCH (n:L) SET n:M",
    "MERGE (n:L {id: 'y'}) ON CREATE SET n.c = 1",
    "USE GRAPH 'g' CREATE (:L {id: 'x', a: 1})",
]


def cypher_row_guards(query: str) -> list:
    """`(FROM, WHERE)` of each per-node guard (`NOT EXISTS (SELECT 1 FROM ... WHERE ... s = ...)`) the translation of
    `query` writes with, on rdf_props and rdf_labels. Shared with the E2E, which EXPLAINs them."""
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    sql = translate_to_sql(parse_query(query), {"rows": ["a", "b"]}).sql
    found = []
    for stmt in sql if isinstance(sql, list) else [sql]:
        for m in re.finditer(r"NOT EXISTS \(SELECT 1 FROM (.*?) WHERE ", stmt):
            depth, i = 1, m.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(stmt[i], 0)
                i += 1
            where = stmt[m.end():i - 1]
            if "rdf_props" in m.group(1) or "rdf_labels" in m.group(1):
                if re.search(r"\bs = ", where):
                    found.append((m.group(1), where))
    return found


@pytest.mark.parametrize("query", WRITES)
def test_the_cypher_writers_guards_ignore_the_same_indexes(query):
    """`UNWIND $rows AS r CREATE (:UW {id: r, name: 'x'})`, 1,000 rows a statement into a new namespace: 2.31, 2.9 ...
    7.19 s for the ten statements on 9995d0d7 (EXPLAIN: idx_props_key / idx_labels_label)."""
    from iris_vector_graph.schema import guard_from

    guards = cypher_row_guards(query)
    assert guards, query
    for frm, _ in guards:
        table = "Graph_KG.rdf_props" if "rdf_props" in frm else "Graph_KG.rdf_labels"
        assert frm == guard_from(table), (query, frm)
