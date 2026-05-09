import json

import pytest


def _cleanup_test_nodes(engine):
    cursor = engine.conn.cursor()
    cursor.execute("DELETE FROM Graph_KG.rdf_reifications WHERE edge_id IN (SELECT edge_id FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?)", ["TEST_NODE:%", "TEST_NODE:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", ["TEST_NODE:%", "TEST_NODE:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", ["TEST_NODE:%"])
    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", ["TEST_NODE:%"])
    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", ["TEST_NODE:%"])
    engine.conn.commit()
    cursor.close()


def _parse_labels(raw):
    return json.loads(raw) if raw else []


def _parse_props(raw):
    items = json.loads(raw) if raw else []
    if items and isinstance(items[0], str):
        items = [json.loads(item) for item in items]
    return {item["key"]: item["value"] for item in items}


def test_return_node_includes_labels_and_properties(engine):
    _cleanup_test_nodes(engine)

    engine.create_node('TEST_NODE:1', labels=['Label1', 'Label2'], properties={'prop1': 'val1', 'prop2': 'val2'})

    result = engine.execute_cypher("MATCH (n) WHERE n.id = $id RETURN n", {"id": "TEST_NODE:1"})

    assert result.rows, "Expected at least one row"
    row = result.rows[0]
    cols = result.columns
    row_map = dict(zip(cols, row))

    assert "n_id" in row_map
    assert row_map["n_id"] == "TEST_NODE:1"

    labels = _parse_labels(row_map.get("n_labels"))
    props = _parse_props(row_map.get("n_props"))

    assert "Label1" in labels
    assert "Label2" in labels
    assert props.get("prop1") == "val1"
    assert props.get("prop2") == "val2"


def test_labels_and_properties_functions(engine):
    _cleanup_test_nodes(engine)

    engine.create_node('TEST_NODE:2', labels=['Solo'], properties={'only': 'one'})

    result = engine.execute_cypher(
        "MATCH (n) WHERE n.id = $id RETURN labels(n) AS labels, properties(n) AS props",
        {"id": "TEST_NODE:2"}
    )

    row = result.rows[0]
    cols = result.columns
    row_map = dict(zip(cols, row))

    labels = _parse_labels(row_map.get("labels"))
    props = _parse_props(row_map.get("props"))

    assert labels == ["Solo"]
    assert props.get("only") == "one"


def test_order_by_limit(engine):
    _cleanup_test_nodes(engine)

    engine.create_node('TEST_NODE:order_1', properties={'created_at': '2025-01-01'})
    engine.create_node('TEST_NODE:order_2', properties={'created_at': '2025-01-03'})
    engine.create_node('TEST_NODE:order_3', properties={'created_at': '2025-01-02'})

    result = engine.execute_cypher(
        "MATCH (n) WHERE n.id STARTS WITH 'TEST_NODE:order_' RETURN n.id AS id ORDER BY n.created_at DESC LIMIT 2"
    )

    rows = result.rows
    assert len(rows) == 2
    ids = [row[result.columns.index("id")] for row in rows]
    assert ids == ["TEST_NODE:order_2", "TEST_NODE:order_3"]


def test_numeric_comparison_filtering(engine):
    _cleanup_test_nodes(engine)

    engine.create_node('TEST_NODE:cmp_1', properties={'confidence': '0.3'})
    engine.create_node('TEST_NODE:cmp_2', properties={'confidence': '0.7'})
    engine.create_node('TEST_NODE:cmp_3', properties={'confidence': '0.9'})

    result = engine.execute_cypher(
        "MATCH (n) WHERE n.id STARTS WITH 'TEST_NODE:cmp_' AND n.confidence >= 0.7 RETURN n.id AS id"
    )

    ids = {row[result.columns.index("id")] for row in result.rows}
    assert ids == {"TEST_NODE:cmp_2", "TEST_NODE:cmp_3"}


def test_numeric_comparison_skips_non_numeric(engine):
    _cleanup_test_nodes(engine)

    engine.create_node('TEST_NODE:cmp_n1', properties={'confidence': 'abc'})
    engine.create_node('TEST_NODE:cmp_n2', properties={'confidence': '0.9'})

    result = engine.execute_cypher(
        "MATCH (n) WHERE n.id STARTS WITH 'TEST_NODE:cmp_' AND n.confidence >= 0.7 RETURN n.id AS id"
    )

    ids = {row[result.columns.index("id")] for row in result.rows}
    assert ids == {"TEST_NODE:cmp_n2"}
