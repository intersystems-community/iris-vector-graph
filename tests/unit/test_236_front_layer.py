"""Spec 236, FR-021: the process-private layer in front of the shared cache.

Benchmarked on ivg-iris-enterprise (specs/236-arno-callout-adoption/research.md): for a
242-character result a shared hit costs 0.10 ms against 0.08 ms for the token check alone
(1.3x); for a 110,000-character result 1.28 ms against 0.10 ms (12x). So the layer exists,
but only for results large enough that moving the payload is the cost; it always keeps the
token check, so it can never serve an answer the shared cache would not.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph import algo_cache
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class FakeServer:
    """Answers Graph.KG.AlgoCache the way the ObjectScript class does."""

    def __init__(self):
        self.token = 1
        self.entries = {}
        self.calls = []

    def __call__(self, cls, method, *args):
        assert cls == "Graph.KG.AlgoCache"
        self.calls.append(method)
        if method == "Token":
            return self.token
        if method == "Lookup":
            key = args
            entry = self.entries.get(key)
            if entry and entry[0] == self.token:
                return f"H{self.token}|{entry[1]}"
            return f"M{self.token}"
        if method == "Put":
            g, a, h, token, payload = args
            self.entries[(g, a, h)] = (int(token), payload)
            return 1
        raise AssertionError(method)


def _store(server):
    store = IRISGraphStore.__new__(IRISGraphStore)
    store._call_classmethod = server
    return store


def _big(n):
    return [[f"node{i:06d}", i] for i in range(n)]


def _run(store, rows, computed):
    def compute():
        computed.append(1)
        return IVGResult(columns=["id", "component_id"], rows=rows)

    return store._cached_algorithm("wcc", None, {}, ["id", "component_id"], compute)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)


def test_a_large_result_is_served_the_second_time_from_the_front_layer_with_the_token_checked():
    server, computed = FakeServer(), []
    store = _store(server)
    rows = _big(2000)
    assert len(json.dumps(rows)) >= algo_cache.FRONT_MIN_CHARS
    _run(store, rows, computed)
    server.calls.clear()
    again = _run(store, rows, computed)
    assert again.rows == rows and len(computed) == 1
    assert server.calls == ["Token"], "the front layer must check the token and nothing else"


def test_a_small_result_uses_no_front_layer():
    server, computed = FakeServer(), []
    store = _store(server)
    rows = [["a", 1], ["b", 1]]
    _run(store, rows, computed)
    server.calls.clear()
    _run(store, rows, computed)
    assert server.calls == ["Lookup"]
    assert not store.__dict__.get("_algo_cache_memo")


def test_a_moved_token_sends_the_call_back_to_the_shared_cache_and_the_owner():
    server, computed = FakeServer(), []
    store = _store(server)
    rows = _big(2000)
    _run(store, rows, computed)
    server.token += 1
    server.calls.clear()
    _run(store, rows, computed)
    assert server.calls[:2] == ["Token", "Lookup"] and len(computed) == 2
    assert "Put" in server.calls


def test_the_layer_holds_a_bounded_number_of_entries():
    server = FakeServer()
    store = _store(server)
    for i in range(algo_cache.FRONT_MAX_ENTRIES + 5):
        store._cached_algorithm(
            "wcc", None, {"i": i}, ["id", "component_id"],
            lambda: IVGResult(columns=["id", "component_id"], rows=_big(2000)),
        )
    assert len(store._algo_cache_memo) == algo_cache.FRONT_MAX_ENTRIES


def test_a_caller_mutating_a_returned_result_cannot_poison_the_layer():
    server, computed = FakeServer(), []
    store = _store(server)
    rows = _big(2000)
    _run(store, rows, computed)
    first = _run(store, rows, computed)
    first.rows[0][1] = "corrupted"
    first.rows.pop()
    again = _run(store, rows, computed)
    assert again.rows == rows


def test_the_disable_switch_turns_the_layer_off_too(monkeypatch):
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    server, computed = FakeServer(), []
    store = _store(server)
    _run(store, _big(2000), computed)
    _run(store, _big(2000), computed)
    assert server.calls == [] and len(computed) == 2
