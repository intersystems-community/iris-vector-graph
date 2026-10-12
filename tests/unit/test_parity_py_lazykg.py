"""LazyKG lists nodes from the adjacency and asks the server for degrees (spec 249).

`^KG("deg")` and `^KG("degp")` are gone: a counter kept with `$Increment` drifts when a transaction rolls back
(DEG-AUDIT.md), and `Graph.KG.Adj.Degree(pGraph, pSrc, pPred = "")` answers from the `out` entries behind a
generation-validated cache. So `LazyKG`:

* lists the sources of a graph by iterating `^KG("out", graph)` (it used to iterate `deg`, which misses a node that has
  edges when the counter was left too low and lists one that has none when it was left too high);
* gets `degree()` / `degree_for_predicate()` from `Adj.Degree`, with the graph key it already derives for every other read.

The fakes serve a `^KG` tree through the Native API calls `host_for(conn)` makes. `classMethodValue` records its
arguments, so the tests pin the whole call, not just the answer.
"""

from types import SimpleNamespace

import pytest

from iris_vector_graph._validate import graph_index_key
from iris_vector_graph.stores.lazy_kg import LazyKG


class _Native:
    """A `^KG` tree behind `nextSubscript` / `get` / `classMethodValue`, recording what was touched."""

    def __init__(self, tree, degree_result=None):
        self.tree = tree
        self.paths = []
        self.calls = []
        self.degree_result = degree_result

    def _node(self, subs):
        node = self.tree
        for s in subs:
            if not isinstance(node, dict):
                return None
            node = node.get(str(s))
            if node is None:
                return None
        return node

    def nextSubscript(self, _reverse, _global, *path):
        self.paths.append(tuple(str(p) for p in path))
        parent = self._node(path[:-1])
        if not isinstance(parent, dict):
            return None
        current = str(path[-1])
        keys = sorted(parent.keys())
        later = [k for k in keys if k > current] if current != "" else keys
        return later[0] if later else None

    def get(self, _global, *path):
        self.paths.append(tuple(str(p) for p in path))
        value = self._node(path)
        return value if not isinstance(value, dict) else None

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method) + tuple(args))
        return self.degree_result


def _lazykg(tree, degree_result=None, **kwargs):
    native = _Native(tree, degree_result)
    return LazyKG(SimpleNamespace(iris=native), **kwargs), native


OUT_ONLY = {"out": {"0": {"alice": {"KNOWS": {"bob": 1}}, "carol": {"KNOWS": {"bob": 1}}}}}


# --- listing: from `out`, not from `deg` -------------------------------------------------------------------------


def test_iter_nodes_lists_the_sources_of_out_when_no_counter_exists():
    """The tree has no `deg` at all, as after the counters are removed. It used to list nothing."""
    lkg, _ = _lazykg(OUT_ONLY, include_sinks=False)

    assert list(lkg.iter_nodes()) == ["alice", "carol"]


def test_iter_nodes_never_touches_the_counter_subtrees():
    lkg, native = _lazykg(OUT_ONLY, include_sinks=True)

    list(lkg.iter_nodes())

    touched = {p[0] for p in native.paths}
    assert not touched & {"deg", "degp"}, native.paths


def test_a_node_the_counter_would_have_missed_is_listed():
    """`deg` left too low reaches zero and is killed while edges remain: the node silently vanishes from the listing.
    The listing now reads the edges themselves, so a leftover or absent counter changes nothing."""
    tree = {
        "out": {"0": {"survivor": {"R": {"x": 1}}}},
        "deg": {"0": {"someone-else": 1}},  # a stale counter for a node with no edge, and none for `survivor`
    }
    lkg, _ = _lazykg(tree, include_sinks=False)

    assert list(lkg.iter_nodes()) == ["survivor"]


def test_iter_nodes_still_adds_sinks_from_in():
    tree = dict(OUT_ONLY)
    tree["in"] = {"0": {"bob": {"KNOWS": {"alice": 1}}, "dave": {"KNOWS": {"alice": 1}}}}
    lkg, _ = _lazykg(tree, include_sinks=True)

    assert list(lkg.iter_nodes()) == ["alice", "carol", "bob", "dave"]


def test_iter_nodes_in_a_named_graph_walks_that_graphs_out():
    tree = {"out": {"0": {"default-node": {"R": {"x": 1}}}, "tenant-a": {"alice": {"R": {"x": 1}}}}}
    lkg, native = _lazykg(tree, include_sinks=False, graph="tenant-a")

    assert list(lkg.iter_nodes()) == ["alice"]
    assert ("out", "tenant-a", "") in native.paths


# --- degree: Adj.Degree through the host ---------------------------------------------------------------------------


def test_degree_calls_adj_degree_with_the_default_graph_key_and_no_predicate():
    lkg, native = _lazykg({}, degree_result=3)

    assert lkg.degree("alice") == 3
    assert native.calls == [("Graph.KG.Adj", "Degree", 0, "alice", "")]


def test_degree_reads_no_counter_global():
    lkg, native = _lazykg({"deg": {"0": {"alice": 99}}}, degree_result=2)

    assert lkg.degree("alice") == 2
    assert not any(p and p[0] in ("deg", "degp") for p in native.paths), native.paths


def test_degree_for_predicate_passes_the_predicate():
    lkg, native = _lazykg({}, degree_result=2)

    assert lkg.degree_for_predicate("alice", "CITES") == 2
    assert native.calls == [("Graph.KG.Adj", "Degree", 0, "alice", "CITES")]


def test_a_named_graph_passes_its_own_key():
    lkg, native = _lazykg({}, degree_result=1, graph="tenant-a")

    lkg.degree("alice")
    lkg.degree_for_predicate("alice", "KNOWS")

    assert native.calls == [
        ("Graph.KG.Adj", "Degree", "tenant-a", "alice", ""),
        ("Graph.KG.Adj", "Degree", "tenant-a", "alice", "KNOWS"),
    ]


@pytest.mark.parametrize("graph", [None, ""])
def test_an_omitted_graph_key_is_the_one_graph_index_key_derives(graph):
    lkg, native = _lazykg({}, degree_result=1, graph=graph)

    lkg.degree("alice")

    assert native.calls[0][2] == graph_index_key(graph)


@pytest.mark.parametrize(
    "returned, expected",
    [(4, 4), ("4", 4), (4.0, 4), (None, 0), ("", 0)],
)
def test_degree_is_returned_as_an_int(returned, expected):
    lkg, _ = _lazykg({}, degree_result=returned)

    got = lkg.degree("alice")

    assert got == expected and type(got) is int


def test_degree_is_cached_per_node_and_per_pair():
    lkg, native = _lazykg({}, degree_result=3)

    assert lkg.degree("alice") == lkg.degree("alice") == 3
    assert lkg.degree_for_predicate("alice", "A") == 3
    assert lkg.degree_for_predicate("alice", "A") == 3
    assert lkg.degree_for_predicate("alice", "B") == 3

    assert len(native.calls) == 3, native.calls  # alice; (alice, A); (alice, B)


def test_clear_cache_asks_again():
    lkg, native = _lazykg({}, degree_result=3)
    lkg.degree("alice")

    lkg.clear_cache()
    lkg.degree("alice")

    assert len(native.calls) == 2


def test_a_percent_method_is_never_called():
    """The host refuses `%` methods; `Adj.Degree` is not one, and nothing here may become one."""
    lkg, native = _lazykg({}, degree_result=1)
    lkg.degree("a")
    lkg.degree_for_predicate("a", "p")

    assert all(not call[1].startswith("%") for call in native.calls)
