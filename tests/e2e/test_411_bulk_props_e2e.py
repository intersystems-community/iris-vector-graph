"""4.1.1 — `bulk_create_nodes` stores what `create_node` stores, and QUICKSTART's queries answer.

Found by running `docs/setup/QUICKSTART.md` against a stock container (2026-09-30).
The ObjectScript bulk path (`Graph.KG.EdgeScan.BulkIngestNodesSQL`) stored `name` as
`"Olaparib"` with the JSON quotes, a boolean as `1` and `1.5` as a `$LB` number the
driver reads back as `Decimal`. `create_node` stores `Olaparib`, `true` and `'1.5'`.
The 2-hop `RETURN DISTINCT t.name` from an ID-bound node answered `[]` on top of that
(tests/unit/test_411_vlp_bound_source.py).

Both have to run live: the quoting and the `$LB` type happen inside IRIS.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.e2e]

PROPS = {"name": "Olaparib", "n": 1.5, "k": 7, "b": True, "tags": ["a", "b"], "q": 'say "hi"'}


@pytest.fixture
def engine(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true" or iris_connection is None:
        pytest.fail(
            "the stored value is decided inside IRIS; start ivg-iris-enterprise "
            "with scripts/enterprise-container.sh up."
        )
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    return eng


@pytest.fixture
def pfx(iris_connection):
    prefix = f"ivg411:bulk:{int(time.time() * 1000)}:"
    yield prefix
    cursor = iris_connection.cursor()
    for table, column in (
        ("Graph_KG.rdf_edges", "s"),
        ("Graph_KG.rdf_labels", "s"),
        ("Graph_KG.rdf_props", "s"),
        ("Graph_KG.nodes", "node_id"),
    ):
        cursor.execute(f"DELETE FROM {table} WHERE {column} %STARTSWITH ?", [prefix])
    iris_connection.commit()


def _props(conn, node_id):
    cursor = conn.cursor()
    cursor.execute(
        'SELECT "key", val FROM Graph_KG.rdf_props WHERE s = ? AND "key" <> ?',
        [node_id, "id"],
    )
    return {k: v for k, v in cursor.fetchall()}


class TestBulkPropsMatchCreateNode:
    def test_the_bulk_fast_path_stores_the_same_rows(self, iris_connection, engine, pfx):
        from iris_vector_graph import schema

        engine.create_node(pfx + "one", labels=["Drug"], properties=PROPS)
        with patch.object(
            schema, "_call_classmethod_large", wraps=schema._call_classmethod_large
        ) as spy:
            engine.bulk_create_nodes(
                [{"id": pfx + "bulk", "labels": ["Drug"], "properties": PROPS}]
            )
        assert any(
            c.args[2] == "BulkIngestNodesSQL" for c in spy.call_args_list
        ), "the ObjectScript fast path was not taken; this test would prove nothing"

        one, bulk = _props(iris_connection, pfx + "one"), _props(iris_connection, pfx + "bulk")
        assert bulk == one, f"bulk {bulk!r} != create_node {one!r}"
        assert bulk["name"] == "Olaparib" and bulk["b"] == "true"
        assert all(isinstance(v, str) for v in bulk.values()), bulk


class TestQuickstartShapedQueries:
    def test_two_hops_from_a_bound_node_return_target_names(self, engine, pfx):
        engine.bulk_create_nodes(
            [
                {"id": pfx + "BRCA1", "labels": ["Gene"], "properties": {"name": "BRCA1"}},
                {"id": pfx + "Olap", "labels": ["Drug"], "properties": {"name": "Olaparib"}},
                {"id": pfx + "Br", "labels": ["Disease"], "properties": {"name": "Breast cancer"}},
            ]
        )
        engine.bulk_create_edges(
            [
                {"source_id": pfx + "BRCA1", "predicate": "TARGETS", "target_id": pfx + "Olap"},
                {"source_id": pfx + "Olap", "predicate": "TREATS", "target_id": pfx + "Br"},
            ]
        )
        rows = engine.execute_cypher(
            "MATCH (g {node_id:$id})-[*1..2]->(t) RETURN DISTINCT t.name LIMIT 10",
            {"id": pfx + "BRCA1"},
        )["rows"]
        assert rows == [["Olaparib"], ["Breast cancer"]], rows

        one_hop = engine.execute_cypher(
            "MATCH (g {node_id:$id})-[:TARGETS]->(d) RETURN g.name, d.name",
            {"id": pfx + "BRCA1"},
        )["rows"]
        assert one_hop == [["BRCA1", "Olaparib"]], one_hop

    def test_distinct_names_from_two_nodes_sharing_one(self, engine, pfx):
        engine.bulk_create_nodes(
            [
                {"id": pfx + "g", "labels": ["Gene"], "properties": {"name": "G"}},
                {"id": pfx + "a", "labels": ["Disease"], "properties": {"name": "Same"}},
                {"id": pfx + "b", "labels": ["Disease"], "properties": {"name": "Same"}},
            ]
        )
        engine.bulk_create_edges(
            [
                {"source_id": pfx + "g", "predicate": "R", "target_id": pfx + "a"},
                {"source_id": pfx + "g", "predicate": "R", "target_id": pfx + "b"},
            ]
        )
        q = "MATCH (g {node_id:$id})-[*1..2]->(t) RETURN %s t.name"
        args = {"id": pfx + "g"}
        assert engine.execute_cypher(q % "DISTINCT", args)["rows"] == [["Same"]]
        assert engine.execute_cypher(q % "", args)["rows"] == [["Same"], ["Same"]]
