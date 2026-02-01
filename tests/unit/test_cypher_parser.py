import pytest

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import parse_query


def test_parse_return_node():
    query = "MATCH (n) RETURN n"
    parsed = parse_query(query)

    assert parsed.return_clause is not None
    assert len(parsed.return_clause.items) == 1
    item = parsed.return_clause.items[0]
    assert isinstance(item.expression, ast.Variable)
    assert item.expression.name == "n"


def test_parse_labels_function():
    query = "MATCH (n) RETURN labels(n)"
    parsed = parse_query(query)

    item = parsed.return_clause.items[0]
    assert isinstance(item.expression, ast.FunctionCall)
    assert item.expression.function_name == "labels"
    assert len(item.expression.arguments) == 1
    assert isinstance(item.expression.arguments[0], ast.Variable)
    assert item.expression.arguments[0].name == "n"


def test_parse_properties_function():
    query = "MATCH (n) RETURN properties(n)"
    parsed = parse_query(query)

    item = parsed.return_clause.items[0]
    assert isinstance(item.expression, ast.FunctionCall)
    assert item.expression.function_name == "properties"
    assert len(item.expression.arguments) == 1
    assert isinstance(item.expression.arguments[0], ast.Variable)
    assert item.expression.arguments[0].name == "n"


def test_parse_order_by_limit():
    query = "MATCH (n) RETURN n.id ORDER BY n.created_at DESC LIMIT 5"
    parsed = parse_query(query)

    assert parsed.order_by_clause is not None
    assert len(parsed.order_by_clause.items) == 1
    order_item = parsed.order_by_clause.items[0]

    assert isinstance(order_item.expression, ast.PropertyReference)
    assert order_item.expression.property_name == "created_at"
    assert order_item.ascending is False
    assert parsed.limit == 5
