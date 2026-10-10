"""Spec 236, Story 2: the equivalence rules and the proof writer. No container."""

from __future__ import annotations

import copy


from iris_vector_graph import _arno_gate as gate
from iris_vector_graph.arno_readers import Proof, validate_proof
from tests.unit._236_helpers import FIXTURES, SHA, proof_doc

# ---------------------------------------------------------------- scores (pagerank, ppr)

OWNER = [["a", 0.5], ["b", 0.3], ["c", 0.2]]


def test_scores_within_relative_tolerance_are_equivalent():
    native = [["a", 0.5 * (1 + 5e-7)], ["b", 0.3], ["c", 0.2]]
    assert gate.scores_equivalent(native, OWNER).ok


def test_a_score_outside_tolerance_fails():
    assert not gate.scores_equivalent([["a", 0.5001], ["b", 0.3], ["c", 0.2]], OWNER).ok


def test_a_different_node_set_fails_both_ways():
    assert not gate.scores_equivalent(OWNER[:2], OWNER).ok
    assert not gate.scores_equivalent(OWNER + [["d", 0.0]], OWNER).ok


def test_ranking_must_agree_where_scores_are_not_tied():
    swapped = [["a", 0.3], ["b", 0.5], ["c", 0.2]]
    assert not gate.scores_equivalent(swapped, OWNER).ok


def test_tied_scores_may_rank_either_way():
    owner = [["a", 0.4], ["b", 0.4], ["c", 0.2]]
    native = [["b", 0.4], ["a", 0.4], ["c", 0.2]]
    assert gate.scores_equivalent(native, owner).ok


# ------------------------------------------------------------------ partitions (wcc)


def test_partitions_equal_ignores_component_names():
    owner = [["a", "x"], ["b", "x"], ["c", "y"]]
    native = [["c", 7], ["a", 3], ["b", 3]]
    assert gate.partitions_equal(native, owner).ok


def test_partitions_that_split_or_merge_differ():
    owner = [["a", "x"], ["b", "x"], ["c", "y"]]
    assert not gate.partitions_equal([["a", 1], ["b", 2], ["c", 3]], owner).ok
    assert not gate.partitions_equal([["a", 1], ["b", 1], ["c", 1]], owner).ok


def test_partitions_over_different_nodes_differ():
    assert not gate.partitions_equal([["a", 1]], [["a", 1], ["b", 1]]).ok


# ---------------------------------------------------------------------- subgraph


def test_subgraph_requires_exactly_equal_node_and_edge_sets():
    owner = {"nodes": ["a", "b"], "edges": [["a", "R", "b"]]}
    assert gate.subgraph_equal({"nodes": ["b", "a"], "edges": [["a", "R", "b"]]}, owner).ok
    assert not gate.subgraph_equal({"nodes": ["a"], "edges": [["a", "R", "b"]]}, owner).ok
    assert not gate.subgraph_equal({"nodes": ["a", "b"], "edges": []}, owner).ok
    # The predicate is part of the edge: an edge that lost it is a different edge.
    assert not gate.subgraph_equal({"nodes": ["a", "b"], "edges": [["a", "REL", "b"]]}, owner).ok


# ------------------------------------------------------------------------- cdlp


def test_similarity_is_one_for_equal_partitions_and_lower_for_different_ones():
    a = [["a", 1], ["b", 1], ["c", 2], ["d", 2]]
    assert gate.partition_similarity(a, [["a", "x"], ["b", "x"], ["c", "y"], ["d", "y"]]) == 1.0
    assert gate.partition_similarity(a, [["a", 1], ["b", 1], ["c", 1], ["d", 1]]) < 1.0
    assert gate.partition_similarity([["a", 1], ["b", 2]], [["a", 1], ["b", 2]]) == 1.0


def test_cdlp_passes_only_when_repeats_are_identical_and_similarity_meets_threshold():
    owner = [["a", 1], ["b", 1], ["c", 2]]
    same = [["a", 9], ["b", 9], ["c", 4]]
    assert gate.cdlp_equivalent([same] * 5, owner, threshold=0.9).ok
    varying = [same, same, [["a", 9], ["b", 4], ["c", 4]], same, same]
    assert not gate.cdlp_equivalent(varying, owner, threshold=0.0).ok
    far = [["a", 1], ["b", 2], ["c", 3]]
    assert not gate.cdlp_equivalent([far] * 5, owner, threshold=0.9).ok


# ---------------------------------------------------------------------- repeats


def test_repeats_must_be_identical():
    assert gate.repeats_identical([OWNER] * 5).ok
    assert not gate.repeats_identical([OWNER, OWNER, [["a", 0.5]], OWNER, OWNER]).ok
    # Order of rows is not part of identity.
    assert gate.repeats_identical([OWNER, list(reversed(OWNER))]).ok


# ------------------------------------------------------------------ proof writer


def _result(passed=True, revision=2, **extra):
    base = {"passed": passed, "revision": revision, "fixtures": list(FIXTURES), "repeats": 5, "dropped": []}
    base.update(extra)
    return base


def _build(results, previous=None):
    return gate.build_proof(
        previous=previous,
        version="arno-callout 1.1.0+3b2123b8e7bb",
        sha256={"arm64": SHA},
        results=results,
        proven_at="2026-10-04",
    )


def test_a_passing_algorithm_is_recorded_at_the_revision_it_passed_on():
    doc = _build({"pagerank": _result(revision=1), "wcc": _result(revision=2)})
    assert validate_proof(doc) == []
    proof = Proof.from_dict(doc)
    assert proof.min_revision("pagerank") == 1 and proof.min_revision("wcc") == 2


def test_a_failing_algorithm_is_never_recorded_and_the_others_still_are():
    doc = _build({"pagerank": _result(passed=False), "wcc": _result()})
    assert "pagerank" not in doc["algorithms"]
    assert "wcc" in doc["algorithms"]


def test_a_failing_algorithm_is_held_back_even_when_an_earlier_run_proved_it():
    previous = _build({"pagerank": _result(), "wcc": _result()})
    doc = _build({"pagerank": _result(passed=False), "wcc": _result()}, previous=previous)
    assert "pagerank" not in doc["algorithms"] and "wcc" in doc["algorithms"]


def test_an_algorithm_not_rerun_keeps_its_earlier_record():
    previous = _build({"pagerank": _result(revision=1)})
    doc = _build({"wcc": _result()}, previous=previous)
    assert set(doc["algorithms"]) == {"pagerank", "wcc"}


def test_dropped_fixtures_and_the_cdlp_threshold_are_written():
    res = _result(dropped=[{"fixture": "tie_heavy", "reason": "owner unstable"}], cdlp_similarity=0.85)
    doc = _build({"cdlp": res})
    entry = doc["algorithms"]["cdlp"]
    assert entry["dropped"] == [{"fixture": "tie_heavy", "reason": "owner unstable"}]
    assert entry["cdlp_similarity"] == 0.85
    assert validate_proof(doc) == []


def test_build_proof_does_not_mutate_the_previous_document():
    previous = proof_doc(pagerank=1)
    snapshot = copy.deepcopy(previous)
    _build({"wcc": _result()}, previous=previous)
    assert previous == snapshot


# --------------------------------------------------- check against a loaded library


def test_check_fails_when_the_proof_claims_a_revision_above_the_report():
    doc = proof_doc(pagerank=2, wcc=2)
    problems = gate.check_proof_against_report(doc, {"pagerank": 1, "wcc": 2})
    assert len(problems) == 1 and "pagerank" in problems[0]


def test_check_passes_when_every_claim_is_met_or_exceeded():
    assert gate.check_proof_against_report(proof_doc(pagerank=1), {"pagerank": 3}) == []


def test_check_fails_for_a_claim_when_the_library_reports_no_readers():
    assert gate.check_proof_against_report(proof_doc(pagerank=1), {}) != []


def test_check_of_an_empty_proof_passes_with_any_library():
    assert gate.check_proof_against_report(proof_doc(), {}) == []


def test_a_missing_library_fails_under_the_require_switch_and_skips_otherwise():
    assert gate.missing_library_action({"IVG_REQUIRE_ARNO": "1"}) == "fail"
    assert gate.missing_library_action({}) == "skip"
    assert gate.missing_library_action({"IVG_REQUIRE_ARNO": "0"}) == "skip"
    assert gate.missing_library_action({"IVG_REQUIRE_ARNO": ""}) == "skip"
