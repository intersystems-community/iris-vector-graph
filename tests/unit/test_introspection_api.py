import pytest
from unittest.mock import MagicMock


def _make_engine(rows_by_sql=None):
    """Return an IRISGraphEngine backed by a mock connection.

    rows_by_sql: dict mapping SQL substring -> list of row tuples returned by fetchall().
    """
    from iris_vector_graph.engine import IRISGraphEngine

    rows_by_sql = rows_by_sql or {}

    def _cursor_factory():
        cursor = MagicMock()
        cursor.description = [("col",)]
        executed = []

        def _execute(sql, params=None):
            executed.clear()
            executed.append(sql)
            for key, rows in rows_by_sql.items():
                if key in sql:
                    cursor.fetchall.return_value = rows
                    cursor.fetchone.return_value = rows[0] if rows else None
                    return
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = None

        cursor.execute.side_effect = _execute
        return cursor

    conn = MagicMock()
    conn.cursor.side_effect = _cursor_factory
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine._schema_prefix = "Graph_KG"
    return engine


class TestGetLabels:
    def test_returns_list_of_strings(self):
        engine = _make_engine({"rdf_labels": [("Person",), ("Drug",), ("Gene",)]})
        result = engine.get_labels()
        assert result == ["Person", "Drug", "Gene"]

    def test_returns_empty_list_when_no_labels(self):
        engine = _make_engine({})
        result = engine.get_labels()
        assert result == []

    def test_returns_list_type(self):
        engine = _make_engine({"rdf_labels": [("X",)]})
        assert isinstance(engine.get_labels(), list)


class TestGetRelationshipTypes:
    def test_returns_list_of_strings(self):
        engine = _make_engine({"rdf_edges": [("KNOWS",), ("TREATS",)]})
        result = engine.get_relationship_types()
        assert result == ["KNOWS", "TREATS"]

    def test_returns_empty_list_when_no_edges(self):
        engine = _make_engine({})
        result = engine.get_relationship_types()
        assert result == []


class TestGetNodeCount:
    def test_total_count_no_label(self):
        engine = _make_engine({"Graph_KG.nodes": [(42,)]})
        assert engine.get_node_count() == 42

    def test_count_with_label(self):
        engine = _make_engine({"rdf_labels": [(7,)]})
        assert engine.get_node_count(label="Person") == 7

    def test_returns_int(self):
        engine = _make_engine({"Graph_KG.nodes": [(0,)]})
        assert isinstance(engine.get_node_count(), int)

    def test_returns_zero_when_empty(self):
        engine = _make_engine({"Graph_KG.nodes": [(0,)]})
        assert engine.get_node_count() == 0


class TestGetEdgeCount:
    def test_total_count_no_predicate(self):
        engine = _make_engine({"rdf_edges": [(99,)]})
        assert engine.get_edge_count() == 99

    def test_count_with_predicate(self):
        engine = _make_engine({"rdf_edges": [(5,)]})
        assert engine.get_edge_count(predicate="KNOWS") == 5

    def test_returns_int(self):
        engine = _make_engine({"rdf_edges": [(0,)]})
        assert isinstance(engine.get_edge_count(), int)


class TestGetLabelDistribution:
    def test_returns_dict(self):
        engine = _make_engine({"rdf_labels": [("Person", 10), ("Drug", 5)]})
        result = engine.get_label_distribution()
        assert isinstance(result, dict)

    def test_correct_mapping(self):
        engine = _make_engine({"rdf_labels": [("Person", 10), ("Drug", 5)]})
        result = engine.get_label_distribution()
        assert result == {"Person": 10, "Drug": 5}

    def test_empty_graph(self):
        engine = _make_engine({})
        assert engine.get_label_distribution() == {}


class TestGetPropertyKeys:
    def test_all_keys_no_label(self):
        engine = _make_engine({"rdf_props": [("name",), ("age",)]})
        result = engine.get_property_keys()
        assert result == ["name", "age"]

    def test_keys_with_label_filter(self):
        engine = _make_engine({"rdf_props": [("name",)]})
        result = engine.get_property_keys(label="Person")
        assert result == ["name"]

    def test_returns_list(self):
        engine = _make_engine({"rdf_props": [("x",)]})
        assert isinstance(engine.get_property_keys(), list)


class TestNodeExists:
    def test_returns_true_when_node_found(self):
        engine = _make_engine({"Graph_KG.nodes": [(1,)]})
        assert engine.node_exists("n1") is True

    def test_returns_false_when_not_found(self):
        engine = _make_engine({})
        assert engine.node_exists("missing") is False

    def test_returns_bool(self):
        engine = _make_engine({"Graph_KG.nodes": [(1,)]})
        assert isinstance(engine.node_exists("n1"), bool)
