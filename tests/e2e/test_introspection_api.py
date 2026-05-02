import pytest
from iris_vector_graph.engine import IRISGraphEngine


pytestmark = pytest.mark.requires_database


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    e = IRISGraphEngine(iris_connection, embedding_dimension=768)
    e.initialize_schema()
    e.create_node("test:alice", labels=["Person"], properties={"name": "Alice", "age": "30"})
    e.create_node("test:bob", labels=["Person"], properties={"name": "Bob"})
    e.create_node("test:aspirin", labels=["Drug"], properties={"name": "Aspirin"})
    e.create_edge("test:alice", "KNOWS", "test:bob")
    e.create_edge("test:alice", "TAKES", "test:aspirin")
    return e


class TestGetLabels:
    def test_returns_known_labels(self, engine):
        labels = engine.get_labels()
        assert "Person" in labels
        assert "Drug" in labels

    def test_returns_list(self, engine):
        assert isinstance(engine.get_labels(), list)

    def test_all_strings(self, engine):
        for label in engine.get_labels():
            assert isinstance(label, str)


class TestGetRelationshipTypes:
    def test_returns_known_types(self, engine):
        types = engine.get_relationship_types()
        assert "KNOWS" in types
        assert "TAKES" in types

    def test_returns_list(self, engine):
        assert isinstance(engine.get_relationship_types(), list)


class TestGetNodeCount:
    def test_total_count_positive(self, engine):
        assert engine.get_node_count() >= 3

    def test_count_by_label(self, engine):
        assert engine.get_node_count(label="Person") >= 2
        assert engine.get_node_count(label="Drug") >= 1

    def test_count_nonexistent_label_is_zero(self, engine):
        assert engine.get_node_count(label="NoSuchLabel_xyz") == 0

    def test_returns_int(self, engine):
        assert isinstance(engine.get_node_count(), int)


class TestGetEdgeCount:
    def test_total_count_positive(self, engine):
        assert engine.get_edge_count() >= 2

    def test_count_by_predicate(self, engine):
        assert engine.get_edge_count(predicate="KNOWS") >= 1

    def test_count_nonexistent_predicate_is_zero(self, engine):
        assert engine.get_edge_count(predicate="NO_SUCH_PRED_xyz") == 0

    def test_returns_int(self, engine):
        assert isinstance(engine.get_edge_count(), int)


class TestGetLabelDistribution:
    def test_returns_dict(self, engine):
        dist = engine.get_label_distribution()
        assert isinstance(dist, dict)

    def test_known_labels_present(self, engine):
        dist = engine.get_label_distribution()
        assert "Person" in dist
        assert "Drug" in dist

    def test_counts_are_positive_ints(self, engine):
        dist = engine.get_label_distribution()
        for label, count in dist.items():
            assert isinstance(count, int)
            assert count > 0


class TestGetPropertyKeys:
    def test_returns_list(self, engine):
        assert isinstance(engine.get_property_keys(), list)

    def test_known_keys_present(self, engine):
        keys = engine.get_property_keys()
        assert "name" in keys

    def test_filter_by_label(self, engine):
        keys = engine.get_property_keys(label="Person")
        assert "name" in keys

    def test_nonexistent_label_returns_empty(self, engine):
        assert engine.get_property_keys(label="NoSuchLabel_xyz") == []


class TestNodeExists:
    def test_existing_node_returns_true(self, engine):
        assert engine.node_exists("test:alice") is True

    def test_missing_node_returns_false(self, engine):
        assert engine.node_exists("test:does_not_exist_xyz") is False

    def test_returns_bool(self, engine):
        assert isinstance(engine.node_exists("test:alice"), bool)
