"""T192-02: LRU cache hit rate test for parse_query."""
import pytest
from iris_vector_graph.cypher.parser import _parse_query_cached, parse_query


def test_cache_hit_rate():
    _parse_query_cached.cache_clear()
    query = "MATCH (n:Person) WHERE n.age > 30 RETURN n"
    for _ in range(100):
        parse_query(query)
    info = _parse_query_cached.cache_info()
    hit_rate = info.hits / (info.hits + info.misses)
    assert hit_rate >= 0.90, f"Cache hit rate {hit_rate:.0%} < 90%"


def test_cached_result_is_independent_copy():
    """Mutating one returned AST must not affect the next call."""
    _parse_query_cached.cache_clear()
    query = "MATCH (n) RETURN n"
    ast1 = parse_query(query)
    ast1.query_parts.clear()
    ast2 = parse_query(query)
    assert ast2.query_parts, "Cache returned a shared reference — deepcopy is broken"


def test_different_queries_cached_independently():
    _parse_query_cached.cache_clear()
    q1 = "MATCH (a) RETURN a"
    q2 = "MATCH (b) RETURN b"
    for _ in range(10):
        parse_query(q1)
        parse_query(q2)
    info = _parse_query_cached.cache_info()
    assert info.currsize == 2
