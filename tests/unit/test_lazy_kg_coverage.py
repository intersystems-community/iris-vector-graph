"""
Coverage tests for stores/lazy_kg.py uncovered paths.
All ^KG global access is mocked — no IRIS needed.
"""
import pytest
from unittest.mock import MagicMock, patch


def make_lazy_kg(subscript_map=None, get_map=None, include_sinks=True):
    """
    Create a LazyKG with mocked iris.createIRIS.

    subscript_map: {(args_tuple): return_value} for nextSubscript calls
    get_map: {(args_tuple): return_value} for get calls
    """
    iris_native = MagicMock()

    def ns(*args):
        if subscript_map:
            return subscript_map.get(args, None)
        return None

    def get_val(*args):
        if get_map:
            return get_map.get(args, None)
        return None

    iris_native.nextSubscript = ns
    iris_native.get = get_val

    iris_module = MagicMock()
    iris_module.createIRIS.return_value = iris_native

    conn = MagicMock()

    import sys
    with patch.dict(sys.modules, {"iris": iris_module}):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        lkg = LazyKG(conn, include_sinks=include_sinks)
        lkg._iris = iris_native  # ensure direct reference

    return lkg, iris_native


# ---------------------------------------------------------------------------
# iter_nodes with include_sinks=True (lines 97-106)
# ---------------------------------------------------------------------------

def test_iter_nodes_include_sinks():
    # ^KG("deg", *): returns "n1", then "" (end)
    # ^KG("in", 0, *): returns "n2", then "" (end)
    subscript_map = {
        (False, "^KG", "deg", 0, ""): "n1",
        (False, "^KG", "deg", 0, "n1"): None,
        (False, "^KG", "in", 0, ""): "n2",
        (False, "^KG", "in", 0, "n2"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map, include_sinks=True)
    nodes = list(lkg.iter_nodes())
    assert "n1" in nodes
    assert "n2" in nodes


def test_iter_nodes_include_sinks_no_duplicates():
    subscript_map = {
        (False, "^KG", "deg", 0, ""): "n1",
        (False, "^KG", "deg", 0, "n1"): None,
        # n1 appears again in sinks — should not duplicate
        (False, "^KG", "in", 0, ""): "n1",
        (False, "^KG", "in", 0, "n1"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map, include_sinks=True)
    nodes = list(lkg.iter_nodes())
    assert nodes.count("n1") == 1


def test_iter_nodes_cached():
    subscript_map = {
        (False, "^KG", "deg", 0, ""): "n1",
        (False, "^KG", "deg", 0, "n1"): None,
        (False, "^KG", "in", 0, ""): None,
    }
    lkg, iris_mock = make_lazy_kg(subscript_map=subscript_map, include_sinks=True)
    first = list(lkg.iter_nodes())
    second = list(lkg.iter_nodes())
    assert first == second


def test_iter_nodes_exclude_sinks():
    subscript_map = {
        (False, "^KG", "deg", 0, ""): "n1",
        (False, "^KG", "deg", 0, "n1"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map, include_sinks=False)
    nodes = list(lkg.iter_nodes())
    assert nodes == ["n1"]


# ---------------------------------------------------------------------------
# in_neighbors (lines 130-146)
# ---------------------------------------------------------------------------

def test_in_neighbors_basic():
    subscript_map = {
        # predicates under ^KG("in", 0, "n1", *)
        (False, "^KG", "in", 0, "n1", ""): "KNOWS",
        (False, "^KG", "in", 0, "n1", "KNOWS"): None,
        # sources under ^KG("in", 0, "n1", "KNOWS", *)
        (False, "^KG", "in", 0, "n1", "KNOWS", ""): "n2",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n2"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    result = lkg.in_neighbors("n1")
    assert "n2" in result


def test_in_neighbors_cached():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", ""): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    r1 = lkg.in_neighbors("n1")
    r2 = lkg.in_neighbors("n1")
    assert r1 is r2


def test_in_neighbors_multiple_predicates():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", ""): "KNOWS",
        (False, "^KG", "in", 0, "n1", "KNOWS"): "LIKES",
        (False, "^KG", "in", 0, "n1", "LIKES"): None,
        (False, "^KG", "in", 0, "n1", "KNOWS", ""): "n2",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n2"): None,
        (False, "^KG", "in", 0, "n1", "LIKES", ""): "n3",
        (False, "^KG", "in", 0, "n1", "LIKES", "n3"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    result = lkg.in_neighbors("n1")
    assert "n2" in result
    assert "n3" in result


def test_in_neighbors_dedup():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", ""): "KNOWS",
        (False, "^KG", "in", 0, "n1", "KNOWS"): "LIKES",
        (False, "^KG", "in", 0, "n1", "LIKES"): None,
        (False, "^KG", "in", 0, "n1", "KNOWS", ""): "n2",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n2"): None,
        # n2 appears again via LIKES
        (False, "^KG", "in", 0, "n1", "LIKES", ""): "n2",
        (False, "^KG", "in", 0, "n1", "LIKES", "n2"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    result = lkg.in_neighbors("n1")
    assert result.count("n2") == 1


# ---------------------------------------------------------------------------
# in_degree (lines 167-184)
# ---------------------------------------------------------------------------

def test_in_degree_basic():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", ""): "KNOWS",
        (False, "^KG", "in", 0, "n1", "KNOWS"): None,
        (False, "^KG", "in", 0, "n1", "KNOWS", ""): "n2",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n2"): "n3",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n3"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    assert lkg.in_degree("n1") == 2


def test_in_degree_cached():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", ""): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    r1 = lkg.in_degree("n1")
    r2 = lkg.in_degree("n1")
    assert r1 == r2 == 0
    assert "n1" in lkg._in_degree_cache


def test_in_degree_empty():
    subscript_map = {
        (False, "^KG", "in", 0, "n_missing", ""): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    assert lkg.in_degree("n_missing") == 0


# ---------------------------------------------------------------------------
# in_degree_for_predicate (lines 186-197)
# ---------------------------------------------------------------------------

def test_in_degree_for_predicate_basic():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", "KNOWS", ""): "n2",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n2"): "n3",
        (False, "^KG", "in", 0, "n1", "KNOWS", "n3"): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    assert lkg.in_degree_for_predicate("n1", "KNOWS") == 2


def test_in_degree_for_predicate_cached():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", "KNOWS", ""): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    r1 = lkg.in_degree_for_predicate("n1", "KNOWS")
    r2 = lkg.in_degree_for_predicate("n1", "KNOWS")
    assert r1 == r2 == 0
    assert ("n1", "KNOWS") in lkg._in_degp_cache


def test_in_degree_for_predicate_empty():
    subscript_map = {
        (False, "^KG", "in", 0, "n1", "MISSING_PRED", ""): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map)
    assert lkg.in_degree_for_predicate("n1", "MISSING_PRED") == 0


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------

def test_clear_cache():
    subscript_map = {
        (False, "^KG", "deg", 0, ""): "n1",
        (False, "^KG", "deg", 0, "n1"): None,
        (False, "^KG", "in", 0, ""): None,
    }
    lkg, _ = make_lazy_kg(subscript_map=subscript_map, include_sinks=True)
    list(lkg.iter_nodes())
    assert lkg._nodes_cache is not None
    lkg.clear_cache()
    assert lkg._nodes_cache is None
    assert lkg._out_cache == {}
    assert lkg._in_cache == {}
