"""4.6.0 R306: nodes with no edge get the row the documented answer gives them (iris_vector_graph/_edgeless.py).

Worked by hand on KNOWN_ISSUES's old example: a ring r1 -> r2 -> r3 -> r1 and a node solo with no edge.
"""

from __future__ import annotations

import pytest

from iris_vector_graph import _edgeless as ed
from iris_vector_graph._collation import collation_key


def test_collation_reads_canonical_decimals_as_numbers():
    ids = ["b", "10", "9", "-1", "07", "1.5", ".5", "1.50", "-0", "a"]
    assert sorted(ids, key=collation_key) == ["-1", ".5", "1.5", "9", "10", "-0", "07", "1.50", "a", "b"]


def test_degree_counts_every_node_in_n():
    rows = [["r1", 0.5, 1], ["r2", 0.5, 1], ["r3", 0.5, 1]]
    out = ed.complete_degree(rows, ["solo"], top_k=0)
    assert out == [["r1", pytest.approx(1 / 3), 1], ["r2", pytest.approx(1 / 3), 1], ["r3", pytest.approx(1 / 3), 1],
                   ["solo", 0.0, 0]]
    assert ed.complete_degree(rows, ["solo"], top_k=1) == [["r1", pytest.approx(1 / 3), 1]]


def test_harmonic_closeness_adds_a_zero_and_classical_is_zero_everywhere():
    rows = [["r1", 2 / 3], ["r2", 2 / 3], ["r3", 2 / 3]]
    assert ed.complete_closeness(rows, ["solo"], "harmonic", 0)[-1] == ["solo", 0.0]
    assert [s for _, s in ed.complete_closeness(rows, ["solo"], "classical", 0)] == [0.0] * 4
    # ties by id in IRIS collation: numbers by value, then text
    assert [v for v, _ in ed.complete_closeness([["10", 0.0], ["a", 0.0]], ["9"], "harmonic", 0)] == ["9", "10", "a"]


def test_a_route_that_listed_the_node_keeps_its_row():
    rows = [["solo", 0.0], ["r1", 1.0]]
    assert ed.complete_closeness(rows, ["solo"], "classical", 0) == [["r1", 1.0], ["solo", 0.0]]


def test_scored_rows_rank_then_cut():
    rows = [["x", 1, 0.333], ["a", 1, 1.0], ["b", 0, 0.0]]
    out = ed.complete_scored(rows, ["-1", "z"], [0, 0.0], top_k=3)
    assert out == [["a", 1, 1.0], ["x", 1, 0.333], ["-1", 0, 0.0]]


def test_groups_append_singletons_after_the_others():
    rows = [["a", 0, 2], ["b", 0, 2], ["c", 1, 1]]
    assert ed.complete_groups(rows, ["solo", "c"], 0) == rows + [["solo", 2, 1]]
    assert ed.complete_groups([], ["9", "10"], 0) == [["9", 0, 1], ["10", 1, 1]]
    assert ed.complete_groups(rows, ["solo"], 3) == rows


def test_labels_label_each_node_with_itself():
    assert ed.complete_labels({"r2": "r1", "r1": "r1"}, ["solo"]) == {"r1": "r1", "r2": "r1", "solo": "solo"}


def test_edgeless_keeps_only_the_nodes_without_any_edge():
    assert ed.edgeless({"solo": False, "loop": True, "10": False, "9": False}) == ["9", "10", "solo"]


class _Native:
    """Graph.KG.Edgeless.LonersJson's pages: two nodes per page."""

    def __init__(self, found):
        self.found, self.calls = sorted(found.items()), []

    def classMethodValue(self, cls, method, schema, graph, after, page):
        import json

        self.calls.append((cls, method, schema, graph, after, page))
        rest = [kv for kv in self.found if kv[0] > after] if after else self.found
        chunk = rest[:2]
        nxt = chunk[-1][0] if len(rest) > 2 else ""
        return json.dumps({"loners": {k: int(v) for k, v in chunk}, "next": nxt})


def test_loners_reads_every_page_of_one_graph():
    native = _Native({"solo": False, "loop": True, "a": False})
    assert ed.loners(native, "G", "g2", page=2) == {"solo": False, "loop": True, "a": False}
    assert [c[4] for c in native.calls] == ["", "loop"]
    assert {c[:4] for c in native.calls} == {("Graph.KG.Edgeless", "LonersJson", "G", "g2")}
    ed.loners(native, "G", None)
    assert native.calls[-1][3] == ""
