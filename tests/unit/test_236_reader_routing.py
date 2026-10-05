"""Spec 236, Story 1: which path answers, decided from a report and a proof. No container."""

from __future__ import annotations

import json

import pytest

from iris_vector_graph import arno_readers
from iris_vector_graph.arno_readers import IN_SCOPE, Proof, parse_report, route
from tests.unit._236_helpers import fake_report, proof_doc

ALL = {name: 2 for name in IN_SCOPE}


def _proof(**mins) -> Proof:
    return Proof.from_dict(proof_doc(**mins))


@pytest.mark.parametrize("algorithm", IN_SCOPE)
class TestRouteByRevision:
    def test_no_entry_in_the_report_gives_the_owner(self, algorithm):
        assert route(algorithm, {}, _proof(**{algorithm: 1})) == "owner"

    def test_a_reader_below_the_proven_minimum_gives_the_owner(self, algorithm):
        assert route(algorithm, {algorithm: 1}, _proof(**{algorithm: 2})) == "owner"

    def test_a_reader_at_the_minimum_is_native(self, algorithm):
        assert route(algorithm, {algorithm: 2}, _proof(**{algorithm: 2})) == "native"

    def test_a_reader_above_the_minimum_is_native(self, algorithm):
        assert route(algorithm, {algorithm: 5}, _proof(**{algorithm: 2})) == "native"

    def test_an_algorithm_absent_from_the_proof_is_never_native(self, algorithm):
        assert route(algorithm, {algorithm: 9}, _proof()) == "owner"
        assert route(algorithm, {algorithm: 9}, Proof.empty()) == "owner"

    def test_only_the_reader_for_this_algorithm_counts(self, algorithm):
        others = {n: 9 for n in IN_SCOPE if n != algorithm}
        assert route(algorithm, others, _proof(**ALL)) == "owner"

    def test_a_named_graph_always_gives_the_owner(self, algorithm):
        # FR-014a: the readers see one adjacency with no graph dimension.
        assert route(algorithm, ALL, _proof(**ALL), graph="g1") == "owner"

    def test_the_default_graph_spellings_are_native(self, algorithm):
        for graph in ("", None):
            assert route(algorithm, ALL, _proof(**ALL), graph=graph) == "native"


def test_each_algorithm_is_decided_on_its_own_revision():
    readers = {"pagerank": 1, "ppr": 2, "wcc": 2, "cdlp": 1, "subgraph": 1}
    proof = _proof(pagerank=1, ppr=2, wcc=2, cdlp=2, subgraph=1)
    assert {a: route(a, readers, proof) for a in IN_SCOPE} == {
        "pagerank": "native",
        "ppr": "native",
        "wcc": "native",
        "cdlp": "owner",
        "subgraph": "native",
    }


def test_betweenness_is_never_native_however_the_report_and_proof_read():
    assert route("betweenness", {"betweenness": 9}, _proof(**ALL)) == "owner"
    # Even a Proof built around the loader cannot put it in scope.
    hand_built = Proof(algorithms={"betweenness": {"min_revision": 1}})
    assert route("betweenness", {"betweenness": 9}, hand_built) == "owner"
    # And the loader refuses a file that names it.
    doc = proof_doc(pagerank=1)
    doc["algorithms"]["betweenness"] = dict(doc["algorithms"]["pagerank"])
    with pytest.raises(ValueError):
        Proof.from_dict(doc)


def test_an_unknown_reader_name_in_the_report_is_ignored():
    readers = {"frobnicate": 7, "pagerank": 1}
    assert route("pagerank", readers, _proof(pagerank=1)) == "native"
    assert route("wcc", readers, _proof(wcc=1)) == "owner"


# ----------------------------------------------------------------- the report parser


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", "null", "42", '"readers"', "{", '{"readers": "x"}', '{"readers": []}'],
)
def test_a_malformed_report_means_no_readers_and_logs_a_diagnostic(raw, caplog):
    with caplog.at_level("WARNING", logger="iris_vector_graph.arno_readers"):
        assert parse_report(raw) == {}
    if raw.strip():
        assert any("capabilit" in r.getMessage().lower() for r in caplog.records)


def test_only_positive_integer_revisions_are_kept():
    raw = json.dumps({"readers": {"a": 1, "b": "2", "c": 2.5, "d": True, "e": 0, "f": -1, "g": 3}})
    assert parse_report(raw) == {"a": 1, "g": 3}


def test_the_bare_map_and_the_full_report_parse_alike():
    bare = json.dumps({"pagerank": 1, "wcc": 2})
    assert parse_report(bare) == parse_report(fake_report({"pagerank": 1, "wcc": 2}))


def test_a_version_string_change_with_equal_revisions_gives_equal_routing():
    readers = {"pagerank": 1, "wcc": 2}
    a = parse_report(fake_report(readers, version="arno-callout 1.1.0+aaaaaaaaaaaa"))
    b = parse_report(fake_report(readers, version="arno-callout 2.0.0+bbbbbbbbbbbb"))
    proof = _proof(pagerank=1, wcc=2)
    assert a == b
    assert {x: route(x, a, proof) for x in IN_SCOPE} == {x: route(x, b, proof) for x in IN_SCOPE}


def test_the_version_string_is_never_consulted_by_routing():
    import inspect

    assert "version" not in inspect.getsource(route).lower().replace("revision", "")


def test_read_reader_report_asks_the_library_through_the_classmethod():
    calls = []

    class Source:
        def _call_classmethod(self, cls, method, *args):
            calls.append((cls, method, args))
            return fake_report({"pagerank": 1})

    assert arno_readers.read_reader_report(Source()) == {"pagerank": 1}
    assert calls == [("Graph.KG.ArnoAccel", "ReaderRevisions", ())]


def test_read_reader_report_swallows_a_failing_call_and_logs(caplog):
    class Source:
        def _call_classmethod(self, *_):
            raise RuntimeError("<ILLEGAL VALUE>")

    with caplog.at_level("WARNING", logger="iris_vector_graph.arno_readers"):
        assert arno_readers.read_reader_report(Source()) == {}
    assert any("ILLEGAL VALUE" in r.getMessage() for r in caplog.records)


# -------------------------------------------------- dispatch falls back to the Owner

from unittest.mock import MagicMock  # noqa: E402

from iris_vector_graph.stores.arno_bridge import ArnoError  # noqa: E402
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore  # noqa: E402

OWNER_PAGERANK = json.dumps([{"id": "a", "score": 0.6}, {"id": "b", "score": 0.4}])
OWNER_MEMBERS = json.dumps({"a": "a", "b": "a"})
OWNER_SUBGRAPH = json.dumps({"nodes": ["a"], "edges": [], "properties": {}, "labels": {}})


def _store(readers=None, proof=None):
    """A store whose reader report and proof are set, and whose two call paths are mocks."""
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._namespace = "USER"
    store._arno_available = True
    store._arno_capabilities = {"algorithms": list(IN_SCOPE), "bfs": True, "rust_callout": True}
    store._arno_reader_report = dict(readers if readers is not None else {n: 1 for n in IN_SCOPE})
    store._arno_proof = proof if proof is not None else _proof(**{n: 1 for n in IN_SCOPE})
    store._detect_arno = lambda: True
    store._arno_call = MagicMock(side_effect=ArnoError("native reader raised"))
    store._call_classmethod = MagicMock()
    return store


def _owner_names(store):
    return [(c.args[0], c.args[1]) for c in store._call_classmethod.call_args_list]


def test_pagerank_falls_back_to_the_owner_when_the_native_reader_raises():
    store = _store()
    store._call_classmethod.return_value = OWNER_PAGERANK
    result = store.execute_pagerank(0.85, 20)
    assert result.error is None
    assert [r[0] for r in result.rows] == ["a", "b"]
    assert store._arno_call.call_args.args[:2] == ("Graph.KG.ArnoAccel", "PageRankNativeJson")
    assert _owner_names(store) == [("Graph.KG.PageRank", "PageRankGlobalJson")]


def test_ppr_falls_back_to_the_owner_when_the_native_reader_raises():
    store = _store()
    store._call_classmethod.return_value = OWNER_PAGERANK
    result = store.execute_ppr(["a"], 0.85, 20)
    assert result.error is None and len(result.rows) == 2
    assert store._arno_call.call_args.args[:2] == ("Graph.KG.ArnoAccel", "PPRNativeJson")
    assert _owner_names(store) == [("Graph.KG.PageRank", "RunJson")]


def test_wcc_falls_back_to_the_owner_when_the_native_reader_raises():
    store = _store()
    store._call_classmethod.return_value = OWNER_MEMBERS
    result = store.execute_wcc()
    assert result.error is None and len(result.rows) == 2
    assert store._arno_call.call_args.args[:2] == ("Graph.KG.ArnoAccel", "WCCNativeJson")
    assert _owner_names(store) == [("Graph.KG.Algorithms", "WCCJson")]


def test_cdlp_falls_back_to_the_owner_when_the_native_reader_raises():
    store = _store()
    store._call_classmethod.return_value = OWNER_MEMBERS
    result = store.execute_cdlp(10)
    assert result.error is None and len(result.rows) == 2
    assert store._arno_call.call_args.args[:2] == ("Graph.KG.ArnoAccel", "CDLPNativeJson")
    assert _owner_names(store) == [("Graph.KG.Algorithms", "CDLPJson")]


def test_subgraph_falls_back_to_the_owner_when_the_native_reader_raises():
    store = _store()
    store._call_classmethod.return_value = OWNER_SUBGRAPH
    result = store.execute_subgraph(["a"], 2, [], 100)
    assert result.error is None
    assert store._arno_call.call_args.args[:2] == ("Graph.KG.ArnoAccel", "SubgraphNativeJson")
    assert _owner_names(store) == [("Graph.KG.Subgraph", "SubgraphJson")]


@pytest.mark.parametrize(
    "readers,proof",
    [
        ({}, None),
        ({n: 1 for n in IN_SCOPE}, _proof()),
        ({n: 1 for n in IN_SCOPE}, Proof.empty()),
    ],
    ids=["old-library", "empty-proof-list", "no-proof"],
)
def test_without_a_sufficient_reader_the_native_call_is_never_made(readers, proof):
    store = _store(readers=readers, proof=proof if proof is not None else _proof(**{n: 1 for n in IN_SCOPE}))
    store._call_classmethod.side_effect = [
        OWNER_PAGERANK, OWNER_PAGERANK, OWNER_MEMBERS, OWNER_MEMBERS, OWNER_SUBGRAPH,
    ]
    store.execute_pagerank(0.85, 20)
    store.execute_ppr(["a"], 0.85, 20)
    store.execute_wcc()
    store.execute_cdlp(10)
    store.execute_subgraph(["a"], 2, [], 100)
    assert store._arno_call.call_count == 0
    assert len(store._call_classmethod.call_args_list) == 5


def test_a_named_graph_ppr_goes_to_the_owner_even_with_a_sufficient_reader():
    store = _store()
    store._call_classmethod.return_value = OWNER_PAGERANK
    store.execute_ppr(["a"], 0.85, 20, graph="g1")
    assert store._arno_call.call_count == 0


def test_a_sufficient_reader_that_answers_is_used_and_the_owner_is_not_asked():
    store = _store()
    store._arno_call = MagicMock(return_value=OWNER_PAGERANK)
    result = store.execute_pagerank(0.85, 20)
    assert [r[0] for r in result.rows] == ["a", "b"]
    assert store._call_classmethod.call_count == 0


def test_the_reader_report_is_read_once_per_store_and_again_after_a_reload():
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = True
    reports = iter([fake_report({"pagerank": 1}), fake_report({"pagerank": 3})])
    store._call_classmethod = MagicMock(side_effect=lambda *a: next(reports))
    assert store._arno_readers() == {"pagerank": 1}
    assert store._arno_readers() == {"pagerank": 1}
    assert store._call_classmethod.call_count == 1
    store._arno_reader_report = None  # what a library reload does
    assert store._arno_readers() == {"pagerank": 3}


def test_outside_user_every_algorithm_goes_to_the_owner():
    # The library's readers walk USER's ^KG whatever namespace the caller is in.
    store = _store()
    store._namespace = "IVG236"
    assert {a: store._arno_route(a) for a in IN_SCOPE} == {a: "owner" for a in IN_SCOPE}
    store._namespace = "USER"
    assert {a: store._arno_route(a) for a in IN_SCOPE} == {a: "native" for a in IN_SCOPE}
