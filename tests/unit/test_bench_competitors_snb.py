"""Neo4j/Memgraph side of the SNB benchmark: statement text and batching (no database)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench" / "competitors"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import snb  # noqa: E402


def test_node_statement_creates_one_label_from_a_property_map():
    assert snb.node_statement("Person") == "UNWIND $rows AS r CREATE (n:Person) SET n = r"


def test_edge_statement_matches_endpoints_by_label_and_id():
    assert snb.edge_statement("KNOWS", "Person", "Person") == "UNWIND $rows AS r MATCH (a:Person {sid: r.s}), (b:Person {sid: r.t}) CREATE (a)-[:KNOWS]->(b)"


def test_index_statements_per_engine():
    assert snb.index_statement("neo4j", "Person") == "CREATE INDEX snb_person_id IF NOT EXISTS FOR (n:Person) ON (n.sid)"
    assert snb.index_statement("memgraph", "Person") == "CREATE INDEX ON :Person(sid)"


def test_group_edges_batches_by_type_and_endpoint_labels():
    edges = [
        {"type": "KNOWS", "src": ("Person", "1"), "dst": ("Person", "2")},
        {"type": "HAS_CREATOR", "src": ("Post", "9"), "dst": ("Person", "1")},
        {"type": "KNOWS", "src": ("Person", "3"), "dst": ("Person", "4")},
    ]
    groups = list(snb.edge_groups(edges, size=1))
    assert [(g[0], g[1], g[2], len(g[3])) for g in groups] == [("KNOWS", "Person", "Person", 1), ("HAS_CREATOR", "Post", "Person", 1), ("KNOWS", "Person", "Person", 1)]
    assert groups[0][3] == [{"s": "1", "t": "2"}]


def test_node_groups_batch_by_label():
    nodes = [{"label": "Person", "id": "1", "props": {"sid": "1"}}, {"label": "Tag", "id": "2", "props": {"sid": "2", "name": "x"}}]
    out = list(snb.node_groups(nodes, size=10))
    assert out == [("Person", [{"sid": "1"}]), ("Tag", [{"sid": "2", "name": "x"}])]
