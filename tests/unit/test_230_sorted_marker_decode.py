"""`_call_classmethod_large` has to decode every marker its callers can get back.

The helper is the Python side of two ObjectScript staging protocols, and through
3.2.0 it understood one of them:

    CHUNKED:<tag>:<n>          — the reply is in ^||LargeOut(tag, 1..n)
    SORTED:<tag>               — the rows are in ^ArnoKG("bfs_r", tag, step, o)
    SORTED:<tag>:<total>:<seed>:<k>
                               — the rows are in ^ArnoKG("khop_r", tag, dist, id)
    SORTED:0                   — the seed is not in ^NKG, so nothing was staged

`Graph.KG.NKGAccel.BFSJson` answers with the `SORTED:` form whenever the Rust
callout staged its hits (`NKGAccelTraversal.cls:387`), and the helper passed that
string straight through to its caller, who called `json.loads` on it:

    E   json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    raw = 'SORTED:35303_bfs'

Other readers in the library already decode it — `_engine/algorithms.py:348`,
`_engine/query.py:1704`, `stores/iris_sql_store.py:856` — so the protocol was
known and this one reader was simply left behind. Every caller of
`_call_classmethod_large` treats its return value as JSON, which makes decoding
the marker the helper's job rather than each caller's.
"""

import json

import pytest

from iris_vector_graph.schema import _call_classmethod_large


class _FakeIris:
    """Scripted stand-in for the object `iris.createIRIS()` returns.

    `replies` maps `(class, method)` to either a single string or a list of
    strings answered in call order.
    """

    def __init__(self, replies):
        self.replies = {k: (v if isinstance(v, list) else [v]) for k, v in replies.items()}
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        queue = self.replies.get((cls, method))
        if queue is None:
            raise AssertionError(f"unscripted call: {cls}.{method}{args}")
        return queue.pop(0) if len(queue) > 1 else queue[0]


def test_a_plain_reply_passes_through_unchanged():
    o = _FakeIris({("Graph.KG.NKGAccel", "BFSJson"): '[{"o":"node_1"}]'})
    assert _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson") == '[{"o":"node_1"}]'


def test_a_chunked_reply_is_still_reassembled_from_its_own_class():
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "BFSJson"): "CHUNKED:BFS:2",
            ("Graph.KG.NKGAccel", "ReadLargeOutChunk"): ['[{"o":"node_1"}', ',{"o":"node_2"}]'],
        }
    )
    out = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson")
    assert json.loads(out) == [{"o": "node_1"}, {"o": "node_2"}]


def test_a_sorted_bfs_marker_is_read_back_through_readbfsresults():
    # The staged rows belong to Graph.KG.Traversal, not to the class that answered
    # with the marker: `ReadBFSPage`/`ReadBFSResults` live there, which is why the
    # tag alone is not enough to read them.
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "BFSJson"): "SORTED:35303_bfs",
            ("Graph.KG.Traversal", "ReadBFSResults"): (
                '[{"s":"node_1","p":"KNOWS","o":"node_2","w":1,"step":1}]'
            ),
        }
    )
    rows = json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson"))
    assert [r["o"] for r in rows] == ["node_2"]
    assert ("Graph.KG.Traversal", "ReadBFSResults", ("35303_bfs",)) in o.calls


def test_a_sorted_bfs_marker_whose_rows_are_chunked_is_reassembled():
    # `ReadBFSResults` returns one string, and a large BFS can exceed what the
    # caller's protocol sends in one reply, so the reader's own answer may be
    # CHUNKED — read from the reader's class, not from the caller's.
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "BFSJson"): "SORTED:35303_bfs",
            ("Graph.KG.Traversal", "ReadBFSResults"): "CHUNKED:BFSR:2",
            ("Graph.KG.Traversal", "ReadLargeOutChunk"): ['[{"o":"node_2"}', ',{"o":"node_3"}]'],
        }
    )
    rows = json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson"))
    assert [r["o"] for r in rows] == ["node_2", "node_3"]


def test_sorted_zero_means_nothing_was_staged():
    # `KHopNeighborsSorted` answers SORTED:0 when the seed has no ^NKG index, and
    # the BFS path treats it the same way. An empty result is a JSON array, not a
    # marker the caller has to know about.
    o = _FakeIris({("Graph.KG.NKGAccel", "BFSJson"): "SORTED:0"})
    assert json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson")) == []


def test_a_sorted_khop_marker_is_rebuilt_into_the_khopneighbors_envelope():
    # `KHopNeighbors` and `KHopNeighborsSorted` compute the same thing and answer
    # differently: one returns the envelope, the other stages the nodes and returns
    # a marker carrying the seed and the hop count. A caller should not be able to
    # tell them apart through this helper.
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "KHopNeighborsSorted"): "SORTED:abc_1:2:node_1:2",
            ("Graph.KG.NKGAccel", "ReadKHopResults"): (
                '{"id":"node_1","dist":0},{"id":"node_2","dist":1}'
            ),
        }
    )
    env = json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "KHopNeighborsSorted"))
    assert env["seed"] == "node_1"
    assert env["hops"] == 2
    assert [n["id"] for n in env["nodes"]] == ["node_1", "node_2"]
    # The count comes from the rows actually read back, not from the marker: the
    # two producers disagree about whether the seed counts (`NKGAccelTraversal.cls`
    # returns `totalNodes` at :78 and `totalNodes - 1` at :172).
    assert env["totalNodes"] == 2


def test_a_seed_containing_a_colon_survives_the_khop_marker():
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "KHopNeighborsSorted"): "SORTED:abc_1:1:urn:node:7:3",
            ("Graph.KG.NKGAccel", "ReadKHopResults"): '{"id":"urn:node:7","dist":0}',
        }
    )
    env = json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "KHopNeighborsSorted"))
    assert env["seed"] == "urn:node:7"
    assert env["hops"] == 3


def test_an_empty_staged_khop_reads_as_an_empty_node_list():
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "KHopNeighborsSorted"): "SORTED:abc_1:0:node_1:2",
            ("Graph.KG.NKGAccel", "ReadKHopResults"): "",
        }
    )
    env = json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "KHopNeighborsSorted"))
    assert env["nodes"] == []
    assert env["totalNodes"] == 0


def test_a_reader_that_answers_with_a_marker_again_does_not_recurse():
    # `ReadBFSPage` returns a marker once the staged tree is gone (engine.py:122
    # breaks on exactly that). A helper that decoded it again would loop.
    o = _FakeIris(
        {
            ("Graph.KG.NKGAccel", "BFSJson"): "SORTED:35303_bfs",
            ("Graph.KG.Traversal", "ReadBFSResults"): "SORTED:35303_bfs",
        }
    )
    assert json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson")) == []


def test_a_malformed_marker_does_not_raise_on_the_caller():
    o = _FakeIris({("Graph.KG.NKGAccel", "BFSJson"): "SORTED:"})
    assert json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson")) == []


@pytest.mark.parametrize("reply", ["", "{}", "not json at all"])
def test_a_non_marker_reply_is_never_rewritten(reply):
    # The helper decodes markers; it is not a JSON validator. A caller that gets a
    # bad reply has to see the reply.
    o = _FakeIris({("Graph.KG.NKGAccel", "BFSJson"): reply})
    assert _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson") == reply
