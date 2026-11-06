"""4.6.0 R310 follow-up (fixes66): embed_nodes selects node_ids a run of 500 at a time (IRIS refuses 1000 literals
in a statement), and an id given twice is embedded once, as the single IN list it replaced did. With the id in two
runs, each run's SELECT found it, so the node was embedded twice."""

from __future__ import annotations

from iris_vector_graph._engine.embeddings import _SELECT_ID_RUN, _node_id_runs


def test_none_is_one_unrestricted_run():
    assert _node_id_runs(None) == [None]


def test_an_empty_list_is_one_empty_run():
    assert _node_id_runs([]) == [[]]


def test_1200_ids_with_10_repeated_across_runs_are_each_in_one_run_once():
    ids = [f"n{i}" for i in range(1200)]
    given = ids + ids[:10] + ids[600:605]  # the repeats fall in later runs than their first place
    runs = _node_id_runs(given)
    flat = [i for run in runs for i in run]
    assert flat == ids  # order-preserving, each once
    assert all(len(run) <= _SELECT_ID_RUN for run in runs)
