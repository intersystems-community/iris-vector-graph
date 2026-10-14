"""bench.py must not time a failed or empty shortest-path call as a success."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "benchmarks"))

import bench_utils  # noqa: E402


def test_weighted_sp_query_inlines_the_node_ids_so_it_runs_on_engines_that_drop_call_parameters():
    q = bench_utils.weighted_sp_query("node_1", "node_2")
    assert q == "CALL ivg.shortestPath.weighted('node_1', 'node_2', 'weight', 9999, 10) YIELD totalCost RETURN totalCost"
    assert "''" in bench_utils.weighted_sp_query("a'b", "c")  # quotes are escaped


def test_answer_or_raise_returns_rows_and_raises_on_error_or_empty():
    ok = SimpleNamespace(rows=[["{}", 3.0]], error=None, columns=["path", "totalCost"])
    assert bench_utils.answer_or_raise(ok) is ok
    with pytest.raises(RuntimeError, match="boom"):
        bench_utils.answer_or_raise(SimpleNamespace(rows=[], error="boom", columns=[]))
    with pytest.raises(RuntimeError, match="no rows"):
        bench_utils.answer_or_raise(SimpleNamespace(rows=[], error=None, columns=[]))


class _FakeIris:
    """classMethodValue(cls, method, *args) served from a table of replies; a reply that is an exception is raised."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method))
        reply = self.replies[(cls, method)]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply(*args) if callable(reply) else reply


def test_large_call_falls_back_to_the_stream_variant_only_when_the_string_overflows():
    chunks = {1: '{"o":"a"},{"o":"b"}', 2: ',{"o":"c"}'}  # the stream puts the separating comma at the start of the next row
    fake = _FakeIris({
        ("Graph.KG.Traversal", "BFSFastJson"): RuntimeError("<THROW>"),
        ("Graph.KG.Traversal", "BFSFastStream"): "CHUNKED:S:2",
        ("Graph.KG.Traversal", "ReadLargeOutChunk"): lambda tag, n: chunks[n],
    })
    out = bench_utils.call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "n0", "", 3)
    assert out == '[{"o":"a"},{"o":"b"},{"o":"c"}]'
    ok = _FakeIris({("Graph.KG.Traversal", "BFSFastJson"): '[{"o":"x"}]'})
    assert bench_utils.call_classmethod_large(ok, "Graph.KG.Traversal", "BFSFastJson", "n0", "", 2) == '[{"o":"x"}]'
    assert [c[1] for c in ok.calls] == ["BFSFastJson"]


def test_large_call_does_not_swallow_other_errors():
    fake = _FakeIris({("Graph.KG.Traversal", "BFSFastJson"): RuntimeError("<UNDEFINED>x")})
    with pytest.raises(RuntimeError, match="UNDEFINED"):
        bench_utils.call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "n0", "", 2)


def test_arno_sorted_tree_too_big_for_one_string_is_read_in_chunks():
    fake = _FakeIris({
        ("Graph.KG.NKGAccel", "BFSJson"): "SORTED:t1",
        ("Graph.KG.Traversal", "ReadBFSResults"): RuntimeError("<MAXSTRING>"),
        ("Graph.KG.Traversal", "ReadBFSResultsStream"): "CHUNKED:S:1",
        ("Graph.KG.Traversal", "ReadLargeOutChunk"): lambda tag, n: '{"o":"a"}',
    })
    assert bench_utils.call_classmethod_large(fake, "Graph.KG.NKGAccel", "BFSJson", "n0", "[]", 3, 0) == '[{"o":"a"}]'
