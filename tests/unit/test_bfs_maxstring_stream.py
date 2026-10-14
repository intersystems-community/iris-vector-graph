"""A BFS answer longer than IRIS's string limit still comes back whole.

``Graph.KG.Traversal.BFSFastJson`` returns one JSON string. On the 100,000-node benchmark graph a 2-hop answer
is past the 3,641,144-character limit and the call raises ``<MAXSTRING>`` on every attempt. ``Host.call_large`` now
answers such a call through ``BFSFastStream``, which stages the same JSON in chunks, and only after the string
call has raised ``<MAXSTRING>``: a result that fits is the string call's own, byte for byte.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph import host as H
from iris_vector_graph.schema import _call_classmethod_large


class MaxStringError(RuntimeError):
    pass


class FakeIris:
    """The Native API object: the string call raises ``<MAXSTRING>``, the stream call stages chunks."""

    def __init__(self, string_reply=None, chunks=None, string_error=None):
        self.string_reply = string_reply
        self.string_error = string_error
        self.chunks = chunks or []
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if method in ("BFSFastJson", "BFSFastJsonDirect"):
            if self.string_error is not None:
                raise self.string_error
            return self.string_reply
        if method in ("BFSFastStream", "BFSFastStreamDirect"):
            return f"CHUNKED:tg9:{len(self.chunks)}"
        if method == "ReadLargeOutChunk":
            tag, i = args
            assert tag == "tg9"
            return self.chunks[i - 1]
        raise AssertionError(f"unexpected call {cls}.{method}")


def _rows(n, start=0):
    return [{"s": "a", "p": "R", "o": f"n{i}", "w": 1, "step": 1} for i in range(start, start + n)]


def _chunks_of(rows, size):
    """The staged form: comma-joined row objects cut at row boundaries, no brackets."""
    pieces = [",".join(json.dumps(r, separators=(",", ":")) for r in rows[i : i + size]) for i in range(0, len(rows), size)]
    return [p if i == 0 else "," + p for i, p in enumerate(pieces)]


def test_maxstring_on_the_string_call_is_answered_from_the_stream():
    rows = _rows(7)
    fake = FakeIris(string_error=MaxStringError("<MAXSTRING>BFSFastJson+37^Graph.KG.TraversalBFS.1"), chunks=_chunks_of(rows, 3))
    out = _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "node_5", "", 2)
    assert json.loads(out) == rows
    assert out.startswith("[") and out.endswith("]")
    stream_calls = [c for c in fake.calls if c[1] == "BFSFastStream"]
    assert stream_calls == [("Graph.KG.Traversal", "BFSFastStream", ("node_5", "", 2))]
    # the chunks are read from the class that staged them
    assert [c[:2] for c in fake.calls if c[1] == "ReadLargeOutChunk"] == [("Graph.KG.Traversal", "ReadLargeOutChunk")] * 3


@pytest.mark.parametrize("message", ["<THROW>", "<STRINGSTACK> 24 BFSFastJson+26^Graph.KG.TraversalBFS.1"])
def test_the_native_apis_account_of_a_string_overflow_is_enough(message):
    """On IRIS 2026.3 the Native API reports BFSFastJson's overflow as the bare text ``<THROW>``."""
    rows = _rows(5)
    fake = FakeIris(string_error=RuntimeError(message), chunks=_chunks_of(rows, 2))
    out = _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "node_0", "", 2)
    assert json.loads(out) == rows


def test_a_throw_that_the_stream_cannot_cure_surfaces_the_stream_error():
    class Both:
        def classMethodValue(self, cls, method, *args):
            raise RuntimeError("<THROW>" if method == "BFSFastJson" else "<UNDEFINED>stream")

    with pytest.raises(RuntimeError, match="UNDEFINED"):
        _call_classmethod_large(Both(), "Graph.KG.Traversal", "BFSFastJson", "s", "", 2)


def test_all_six_arguments_reach_the_stream_call():
    fake = FakeIris(string_error=MaxStringError("<MAXSTRING>"), chunks=_chunks_of(_rows(2), 5))
    _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "s", '["R"]', 3, "Person", "both", "g1")
    assert fake.calls[1] == ("Graph.KG.Traversal", "BFSFastStream", ("s", '["R"]', 3, "Person", "both", "g1"))


def test_direct_variant_falls_back_to_the_stream_too():
    fake = FakeIris(string_error=MaxStringError("<MAXSTRING>"), chunks=_chunks_of(_rows(4), 2))
    out = _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJsonDirect", "s", "", 2)
    assert len(json.loads(out)) == 4


def test_an_empty_stream_is_an_empty_list():
    fake = FakeIris(string_error=MaxStringError("<MAXSTRING>"), chunks=[])
    assert _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "s", "", 2) == "[]"


def test_a_result_that_fits_is_the_string_calls_own_and_the_stream_is_never_called():
    reply = json.dumps(_rows(3))
    fake = FakeIris(string_reply=reply)
    assert _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "s", "", 1) == reply
    assert [c[1] for c in fake.calls] == ["BFSFastJson"]


def test_an_error_that_is_not_maxstring_is_raised_not_swallowed():
    fake = FakeIris(string_error=RuntimeError("<UNDEFINED>x"), chunks=_chunks_of(_rows(2), 2))
    with pytest.raises(RuntimeError, match="UNDEFINED"):
        _call_classmethod_large(fake, "Graph.KG.Traversal", "BFSFastJson", "s", "", 1)
    assert [c[1] for c in fake.calls] == ["BFSFastJson"]


def test_a_method_without_a_stream_variant_still_raises_maxstring():
    class Raiser:
        def classMethodValue(self, cls, method, *args):
            raise MaxStringError("<MAXSTRING>")

    with pytest.raises(MaxStringError):
        _call_classmethod_large(Raiser(), "Graph.KG.Other", "Big", "x")


def test_the_stream_fallback_table_names_methods_that_exist_in_objectscript():
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
    traversal = (src / "Traversal.cls").read_text()
    bfs = (src / "TraversalBFS.cls").read_text()
    for (cls, string_method), stream_method in H.STREAM_FALLBACKS.items():
        assert cls == "Graph.KG.Traversal"
        assert f"ClassMethod {stream_method}(" in traversal and f"ClassMethod {stream_method}(" in bfs
        assert f"ClassMethod {string_method}(" in traversal
    assert "ClassMethod ReadLargeOutChunk(" in traversal and "ClassMethod ReadLargeOutChunk(" in bfs


# ---------------------------------------------------------------------------------- the Arno leg: SORTED: replies


class FakeArnoIris:
    """``NKGAccel.BFSJson`` answers ``SORTED:<tag>:<n>``; ``ReadBFSResults`` builds one string and raises <MAXSTRING>."""

    def __init__(self, chunks, reader_error=None, stream_missing=False):
        self.chunks = chunks
        self.reader_error = reader_error
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if (cls, method) == ("Graph.KG.NKGAccel", "BFSJson"):
            return "SORTED:77_bfs:5"
        if (cls, method) == ("Graph.KG.Traversal", "ReadBFSResults"):
            if self.reader_error is not None:
                raise self.reader_error
            return "[" + "".join(self.chunks) + "]"
        if (cls, method) == ("Graph.KG.Traversal", "ReadBFSResultsStream"):
            assert args == ("77_bfs",)
            return f"CHUNKED:S:{len(self.chunks)}"
        if (cls, method) == ("Graph.KG.Traversal", "ReadLargeOutChunk"):
            assert args[0] == "S"
            return self.chunks[args[1] - 1]
        raise AssertionError(f"unexpected call {cls}.{method}")


def test_a_sorted_tree_too_big_for_the_string_reader_is_read_in_chunks():
    rows = _rows(9)
    fake = FakeArnoIris(_chunks_of(rows, 4), reader_error=MaxStringError("<MAXSTRING>ReadBFSResults+9^Graph.KG.TraversalBFS.1"))
    out = _call_classmethod_large(fake, "Graph.KG.NKGAccel", "BFSJson", "node_5", "[]", 3, 0)
    assert json.loads(out) == rows
    assert ("Graph.KG.Traversal", "ReadBFSResultsStream", ("77_bfs",)) in fake.calls


def test_a_sorted_tree_that_fits_is_read_by_the_string_reader_alone():
    rows = _rows(3)
    fake = FakeArnoIris(_chunks_of(rows, 2))
    out = _call_classmethod_large(fake, "Graph.KG.NKGAccel", "BFSJson", "node_5", "[]", 2, 0)
    assert json.loads(out) == rows
    assert not [c for c in fake.calls if c[1] in ("ReadBFSResultsStream", "ReadLargeOutChunk")]


def test_a_sorted_reader_error_that_is_not_maxstring_is_raised():
    fake = FakeArnoIris([], reader_error=RuntimeError("<UNDEFINED>"))
    with pytest.raises(RuntimeError, match="UNDEFINED"):
        _call_classmethod_large(fake, "Graph.KG.NKGAccel", "BFSJson", "node_5", "[]", 2, 0)


def test_the_sorted_stream_reader_exists_in_objectscript():
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
    assert "ClassMethod ReadBFSResultsStream(" in (src / "Traversal.cls").read_text()
    assert "ClassMethod ReadBFSResultsStream(" in (src / "TraversalBFS.cls").read_text()
