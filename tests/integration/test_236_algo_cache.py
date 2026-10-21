"""Spec 236, Story 4b: the shared algorithm result cache, through the engine.

A hit is read off the store's own counters and off ``^IVG.AlgoCache`` itself, never
inferred from timing. Runs in the scratch namespace IVG236 with the sentinel asserted
before every erase.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph import algo_cache
from tests.integration._236_world import PRED, World


@pytest.fixture
def world(ivg_236_scratch, monkeypatch):
    monkeypatch.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)
    w = World(ivg_236_scratch)
    w.reset()
    w.native().classMethodValue("Graph.KG.AlgoCache", "InvalidateAll")
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.AlgoCache", "InvalidateAll")
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def stats(engine) -> dict:
    return dict(getattr(engine._store, "_algo_cache_stats", {}))


def entries(world) -> list:
    """Every (graphKey, algorithm, paramHash) under ^IVG.AlgoCache."""
    n = world.native()
    out = []


    def children(*subs):
        cur = ""
        while True:
            cur = n.nextSubscript(False, "IVG.AlgoCache", *subs, cur)
            if cur is None:
                return
            yield cur

    for g in children():
        for a in children(g):
            for h in children(g, a):
                out.append((str(g), a, h))
    return out


def all_payload_text(world) -> str:
    n = world.native()
    return " ".join(str(n.get("IVG.AlgoCache", *k)) for k in entries(world))


def run_all(engine):
    return {
        "wcc": engine.kg_WCC(),
        "cdlp": engine.kg_CDLP(),
        "pagerank": sorted(engine.kg_PAGERANK()),
        "ppr": sorted(engine.kg_PPR(["a"])),
    }


# ------------------------------------------------------------------------- hits


@pytest.mark.parametrize("algorithm", ["wcc", "cdlp", "pagerank", "ppr"])
def test_a_repeat_call_is_served_from_the_cache_and_equals_the_first(world, algorithm):
    e = world.engine()
    call = {
        "wcc": e.kg_WCC,
        "cdlp": e.kg_CDLP,
        "pagerank": lambda: sorted(e.kg_PAGERANK()),
        "ppr": lambda: sorted(e.kg_PPR(["a"])),
    }[algorithm]
    first = call()
    s1 = stats(e)
    second = call()
    s2 = stats(e)
    assert second == first
    assert s2.get("hit", 0) == s1.get("hit", 0) + 1, (s1, s2)
    assert s2.get("put", 0) == s1.get("put", 0), "the repeat recomputed and stored again"


def test_the_default_graph_is_keyed_by_the_integer_zero_not_the_ledger_form(world):
    world.engine().kg_WCC()
    keys = entries(world)
    assert keys and {g for g, _, _ in keys} == {"0"}, keys


def test_a_second_connection_hits_the_first_ones_entry(world):
    first = world.engine()
    expected = first.kg_WCC()
    world.renew()
    second = world.engine()
    assert second is not first
    assert second.kg_WCC() == expected
    assert stats(second).get("hit", 0) == 1 and stats(second).get("put", 0) == 0


def test_different_parameters_are_different_entries(world):
    e = world.engine()
    e.kg_CDLP(5)
    e.kg_CDLP(10)
    hashes = {h for _, a, h in entries(world) if a == "cdlp"}
    assert len(hashes) == 2


def test_two_graphs_never_see_each_others_answers(world):
    e = world.engine()
    e.create_edge("x", PRED, "z", graph="g1")
    world.commit()
    default = sorted(e.kg_PPR(["a"]))
    named = sorted(e.kg_PERSONALIZED_PAGERANK(["x"], graph="g1").items())
    assert default != named
    assert {g for g, a, _ in entries(world) if a == "ppr"} == {"0", "g1"}
    assert sorted(e.kg_PPR(["a"])) == default
    assert sorted(e.kg_PERSONALIZED_PAGERANK(["x"], graph="g1").items()) == named


# ----------------------------------------------------------------- invalidation


def test_a_write_makes_the_next_call_a_miss_that_overwrites_the_entry(world):
    e = world.engine()
    before = e.kg_WCC()
    tok0 = world.token()
    e.create_edge("q1", PRED, "q2")
    world.commit()
    s1 = stats(e)
    after = e.kg_WCC()
    s2 = stats(e)
    assert after != before and "q1" in after
    assert s2.get("miss", 0) == s1.get("miss", 0) + 1 and s2.get("put", 0) == s1.get("put", 0) + 1
    key = next(h for _, a, h in entries(world) if a == "wcc")
    assert str(world.native().classMethodValue("Graph.KG.AlgoCache", "Lookup", "", "wcc", key)).startswith("H")
    assert world.token() > tok0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.create_edge("n1", PRED, "n2"),
        lambda e: e.delete_edge("c", PRED, "d"),
        lambda e: e.delete_node("b"),
        lambda e: e.erase_graph(None),
        lambda e: e.erase_all(),
    ],
    ids=["create_edge", "delete_edge", "delete_node", "erase_graph", "erase_all"],
)
def test_after_any_write_the_cached_answer_equals_the_recomputation(world, mutate):
    e = world.engine()
    run_all(e)  # fill the cache
    mutate(e)
    world.commit()
    cached = run_all(e)
    world.renew()
    truth_env = world.engine()
    truth_env._store._algo_cache_off = True
    assert cached == run_all(truth_env)


def test_erased_content_is_in_no_cached_payload(world):
    e = world.engine()
    e.create_edge("secret_a", PRED, "secret_b")
    world.commit()
    run_all(e)
    e.kg_PPR(["secret_a"])
    assert "secret_a" in all_payload_text(world)
    e.delete_node("secret_a")
    world.commit()
    assert "secret_a" not in all_payload_text(world), "erasure left the id in a cached payload"
    # ...and not after the next call either.
    run_all(world.engine())
    assert "secret_a" not in all_payload_text(world)


def test_erase_all_empties_the_cache(world):
    e = world.engine()
    run_all(e)
    assert entries(world)
    e.erase_all()
    assert entries(world) == []


# ------------------------------------------------------------------- what is not cached


def test_subgraph_is_never_cached(world):
    e = world.engine()
    e.kg_SUBGRAPH(["a"], k_hops=2)
    e.kg_SUBGRAPH(["a"], k_hops=2)
    assert not [k for k in entries(world) if k[1] == "subgraph"]
    assert stats(e).get("hit", 0) == 0


def test_the_disable_switch_computes_every_time_and_stores_nothing(world, monkeypatch):
    monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    e = world.engine()
    e.kg_WCC()
    e.kg_WCC()
    assert entries(world) == []
    assert stats(e).get("hit", 0) == 0


def test_an_oversize_payload_is_not_stored_and_the_call_still_succeeds(world, monkeypatch):
    monkeypatch.setattr(algo_cache, "MAX_PAYLOAD_CHARS", 10)
    e = world.engine()
    result = e.kg_WCC()
    assert result and entries(world) == []


def test_a_missing_cache_class_degrades_to_computing(world, monkeypatch):
    e = world.engine()
    real = e._store._call_classmethod

    def broken(cls, method, *args):
        if cls == "Graph.KG.AlgoCache":
            raise RuntimeError("<CLASS DOES NOT EXIST>")
        return real(cls, method, *args)

    monkeypatch.setattr(e._store, "_call_classmethod", broken)
    assert e.kg_WCC()
    assert e.kg_WCC()


# ----------------------------------------------- writes through SQL (spec 252)


def test_a_cypher_write_reaches_the_owners_and_so_the_cache_at_once(world):
    """Since spec 252 the adjacency index maintains ^KG inside a Cypher or SQL write's own transaction, so no sync()
    is needed: the next run sees the new edge, and cached == recomputed. (Before 252 neither touched ^KG until sync().)"""
    e = world.engine()
    before = run_all(e)
    e.execute_cypher("CREATE (:N {id: 'c1'})-[:T]->(:N {id: 'c2'})")
    world.commit()
    after = run_all(e)
    assert after != before and {"c1", "c2"} <= set(after["wcc"])
    world.renew()
    e3 = world.engine()
    e3._store._algo_cache_off = True
    assert after == run_all(e3)


# ------------------------------------------------------- the front layer, live


def test_a_large_result_is_held_in_front_and_a_write_still_invalidates_it(world):
    n = world.native()
    for i in range(1500):
        n.set(1, "^KG", "out", 0, f"big{i:05d}", PRED, f"big{i + 1:05d}")
        n.set(1, "^KG", "in", 0, f"big{i + 1:05d}", PRED, f"big{i:05d}")
    n.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
    e = world.engine()
    first = e.kg_WCC()
    assert len(json.dumps(first)) >= algo_cache.FRONT_MIN_CHARS
    second = e.kg_WCC()
    assert second == first and stats(e).get("front_hit", 0) == 1
    e.create_edge("late1", PRED, "late2")
    world.commit()
    third = e.kg_WCC()
    assert "late1" in third and stats(e).get("front_hit", 0) == 1


def test_get_and_invalidate_graph_follow_the_contract(world):
    n = world.native()
    token = int(n.classMethodValue("Graph.KG.AlgoCache", "Token"))
    n.classMethodValue("Graph.KG.AlgoCache", "Put", "", "wcc", "h1", token, "PAYLOAD")
    n.classMethodValue("Graph.KG.AlgoCache", "Put", "g1", "wcc", "h1", token, "OTHER")
    assert str(n.classMethodValue("Graph.KG.AlgoCache", "Get", "", "wcc", "h1")) == "PAYLOAD"
    assert n.classMethodValue("Graph.KG.AlgoCache", "Get", "", "wcc", "nope") in (None, "")
    n.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
    assert n.classMethodValue("Graph.KG.AlgoCache", "Get", "", "wcc", "h1") in (None, "")  # stale token
    n.classMethodValue("Graph.KG.AlgoCache", "Put", "", "wcc", "h1", int(n.classMethodValue("Graph.KG.AlgoCache", "Token")), "FRESH")
    n.classMethodValue("Graph.KG.AlgoCache", "InvalidateGraph", "")
    assert n.classMethodValue("Graph.KG.AlgoCache", "Get", "", "wcc", "h1") in (None, "")
    assert n.isDefined("IVG.AlgoCache", "g1") > 0  # another graph's entries are untouched
