import json

import pytest


def _cleanup_test_nodes(cursor):
    cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'TEST_NODE:%'")
    cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'TEST_NODE:%'")
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'TEST_NODE:%'")


def _parse_labels(raw):
    return json.loads(raw) if raw else []


def _parse_props(raw):
    items = json.loads(raw) if raw else []
    if items and isinstance(items[0], str):
        items = [json.loads(item) for item in items]
    return {item["key"]: item["value"] for item in items}


def test_return_node_includes_labels_and_properties(iris_connection, execute_cypher):
    cursor = iris_connection.cursor()
    _cleanup_test_nodes(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:1')")
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES ('TEST_NODE:1', 'Label1')")
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES ('TEST_NODE:1', 'Label2')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:1', 'prop1', 'val1')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:1', 'prop2', 'val2')")
    iris_connection.commit()

    result = execute_cypher("MATCH (n) WHERE n.id = 'TEST_NODE:1' RETURN n")

    assert result["rows"], "Expected at least one row"
    row = result["rows"][0]
    cols = result["columns"]
    row_map = dict(zip(cols, row))

    assert "n_id" in row_map
    assert row_map["n_id"] == "TEST_NODE:1"

    labels = _parse_labels(row_map.get("n_labels"))
    props = _parse_props(row_map.get("n_props"))

    assert "Label1" in labels
    assert "Label2" in labels
    assert props.get("prop1") == "val1"
    assert props.get("prop2") == "val2"


def test_labels_and_properties_functions(iris_connection, execute_cypher):
    cursor = iris_connection.cursor()
    _cleanup_test_nodes(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:2')")
    cursor.execute("INSERT INTO rdf_labels (s, label) VALUES ('TEST_NODE:2', 'Solo')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:2', 'only', 'one')")
    iris_connection.commit()

    result = execute_cypher(
        "MATCH (n) WHERE n.id = 'TEST_NODE:2' RETURN labels(n) AS labels, properties(n) AS props"
    )

    row = result["rows"][0]
    cols = result["columns"]
    row_map = dict(zip(cols, row))

    labels = _parse_labels(row_map.get("labels"))
    props = _parse_props(row_map.get("props"))

    assert labels == ["Solo"]
    assert props.get("only") == "one"


def test_order_by_limit(iris_connection, execute_cypher):
    cursor = iris_connection.cursor()
    _cleanup_test_nodes(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:order_1')")
    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:order_2')")
    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:order_3')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:order_1', 'created_at', '2025-01-01')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:order_2', 'created_at', '2025-01-03')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:order_3', 'created_at', '2025-01-02')")
    iris_connection.commit()

    result = execute_cypher(
        "MATCH (n) WHERE n.id STARTS WITH 'TEST_NODE:order_' RETURN n.id AS id ORDER BY n.created_at DESC LIMIT 2"
    )

    rows = result["rows"]
    assert len(rows) == 2
    ids = [row[result["columns"].index("id")] for row in rows]
    assert ids == ["TEST_NODE:order_2", "TEST_NODE:order_3"]


def test_numeric_comparison_filtering(iris_connection, execute_cypher):
    cursor = iris_connection.cursor()
    _cleanup_test_nodes(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:cmp_1')")
    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:cmp_2')")
    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:cmp_3')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:cmp_1', 'confidence', '0.3')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:cmp_2', 'confidence', '0.7')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:cmp_3', 'confidence', '0.9')")
    iris_connection.commit()

    result = execute_cypher(
        "MATCH (n) WHERE n.confidence >= 0.7 RETURN n.id AS id"
    )

    ids = {row[result["columns"].index("id")] for row in result["rows"]}
    assert ids == {"TEST_NODE:cmp_2", "TEST_NODE:cmp_3"}


def test_numeric_comparison_skips_non_numeric(iris_connection, execute_cypher):
    cursor = iris_connection.cursor()
    _cleanup_test_nodes(cursor)

    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:cmp_n1')")
    cursor.execute("INSERT INTO nodes (node_id) VALUES ('TEST_NODE:cmp_n2')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:cmp_n1', 'confidence', 'abc')")
    cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES ('TEST_NODE:cmp_n2', 'confidence', '0.9')")
    iris_connection.commit()

    result = execute_cypher(
        "MATCH (n) WHERE n.confidence >= 0.7 RETURN n.id AS id"
    )

    ids = {row[result["columns"].index("id")] for row in result["rows"]}
    assert ids == {"TEST_NODE:cmp_n2"}
