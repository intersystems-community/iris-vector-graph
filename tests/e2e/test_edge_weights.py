import pytest

from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture()
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


def test_create_edge_accepts_weight(engine, clean_test_data):
    p = clean_test_data
    engine.create_node(f"{p}a")
    engine.create_node(f"{p}b")
    assert engine.create_edge(f"{p}a", "CALLS", f"{p}b", weight=5.2) is True


def test_set_edge_weight_updates_existing(engine, clean_test_data):
    p = clean_test_data
    engine.create_node(f"{p}a")
    engine.create_node(f"{p}b")
    engine.create_edge(f"{p}a", "CALLS", f"{p}b", weight=1.0)
    result = engine.set_edge_weight(f"{p}a", "CALLS", f"{p}b", 3.7)
    assert isinstance(result, bool)


def test_create_edge_default_weight_still_works(engine, clean_test_data):
    p = clean_test_data
    engine.create_node(f"{p}a")
    engine.create_node(f"{p}b")
    assert engine.create_edge(f"{p}a", "KNOWS", f"{p}b") is True


def test_weight_is_keyword_not_positional_qualifiers(engine, clean_test_data):
    p = clean_test_data
    engine.create_node(f"{p}a")
    engine.create_node(f"{p}b")
    assert engine.create_edge(
        f"{p}a", "CALLS", f"{p}b", weight=2.5, qualifiers={"latency_ms": 42.7}
    ) is True
