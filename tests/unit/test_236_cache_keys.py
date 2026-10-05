"""Spec 236, Story 4b: what the algorithm result cache keys on, and what it refuses to store."""

from __future__ import annotations

import json

import pytest

from iris_vector_graph import algo_cache


@pytest.fixture(autouse=True)
def _cache_on(monkeypatch):
    monkeypatch.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)


def test_the_hash_ignores_key_order():
    a = algo_cache.param_hash({"damping": 0.85, "max_iterations": 20, "seeds": ["a"]})
    b = algo_cache.param_hash({"seeds": ["a"], "max_iterations": 20, "damping": 0.85})
    assert a == b


def test_numeric_spellings_of_one_value_hash_alike():
    assert algo_cache.param_hash({"d": 0.85}) == algo_cache.param_hash({"d": 0.850})
    assert algo_cache.param_hash({"n": 20}) == algo_cache.param_hash({"n": 20.0})


def test_a_string_is_not_a_number():
    # "20" the string and 20 the number are different parameters.
    assert algo_cache.param_hash({"n": 20}) != algo_cache.param_hash({"n": "20"})


def test_different_values_hash_differently():
    base = {"damping": 0.85, "max_iterations": 20}
    assert algo_cache.param_hash(base) != algo_cache.param_hash({**base, "damping": 0.5})
    assert algo_cache.param_hash(base) != algo_cache.param_hash({**base, "max_iterations": 21})
    assert algo_cache.param_hash(base) != algo_cache.param_hash({**base, "extra": None})


def test_the_hash_is_a_sha256_hex_of_canonical_json():
    import hashlib

    h = algo_cache.param_hash({"b": 1, "a": [1, 2]})
    canonical = json.dumps({"a": [1, 2], "b": 1}, sort_keys=True, separators=(",", ":"))
    assert h == hashlib.sha256(canonical.encode()).hexdigest()


def test_list_order_is_part_of_the_key_so_callers_sort_what_is_a_set():
    assert algo_cache.param_hash({"s": ["a", "b"]}) != algo_cache.param_hash({"s": ["b", "a"]})
    assert algo_cache.seed_key(["b", "a", "b"]) == ["a", "b"]


@pytest.mark.parametrize("algorithm", ["pagerank", "ppr", "wcc", "cdlp"])
def test_the_four_graph_wide_algorithms_are_cacheable(algorithm):
    assert algo_cache.cacheable(algorithm, "[]")


def test_subgraph_and_unknown_algorithms_are_never_cached():
    # subgraph reads live SQL rdf_labels, which no token covers (audit, self-bypass).
    assert not algo_cache.cacheable("subgraph", "[]")
    assert not algo_cache.cacheable("betweenness", "[]")


def test_an_oversize_payload_is_not_cached():
    assert algo_cache.cacheable("wcc", "x" * algo_cache.MAX_PAYLOAD_CHARS)
    assert not algo_cache.cacheable("wcc", "x" * (algo_cache.MAX_PAYLOAD_CHARS + 1))


def test_the_disable_switch(monkeypatch):
    monkeypatch.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)
    assert algo_cache.enabled()
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    assert not algo_cache.enabled()
    assert not algo_cache.cacheable("wcc", "[]")
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "0")
    assert algo_cache.enabled()


def test_the_graph_is_passed_as_a_name_and_never_derived_in_python():
    # ADR-0003: Graph.KG.GraphKey owns both key forms. The module must not spell one.
    import inspect

    source = inspect.getsource(algo_cache)
    assert "ZStrip" not in source and "$Char(1)" not in source
    assert algo_cache.graph_name(None) == "" and algo_cache.graph_name("g1") == "g1"
