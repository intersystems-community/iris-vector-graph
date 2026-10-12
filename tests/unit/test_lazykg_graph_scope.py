"""LazyKG reads the ``^KG`` layout that exists, not the one spec 162 wrote against.

Spec 214 put the graph between the tree name and the node in every adjacency
store: ``^KG("out", graph, s, p, o)``, ``^KG("in", graph, o, p, s)``, with the
integer ``0`` keying the default graph
(``iris_vector_graph._validate.graph_index_key``). ``LazyKG`` was never moved with
it. It hardcoded a literal ``0`` in the ``out`` and ``in`` walks — where it
happens to land on the default graph's subscript by coincidence, because the old
shard number and the new default graph key are both ``0`` — and omitted it
entirely from the stored counters ``deg`` and ``degp``.

The visible damage on a live container:

- ``iter_nodes()`` walked ``^KG("deg", *)`` and yielded the *graph* subscripts, so
  degree centrality scored one node called ``"0"``.
- ``degree(node)`` and ``degree_for_predicate(node, p)`` read one subscript short
  and got nothing, so every out-degree was ``0`` — which is a number, not an
  error, so k-core reported a ring with min core 0 and betweenness found no
  reachable nodes.

Spec 249 removed the counters. The same failure is still possible one level up:
``iter_nodes()`` iterates ``^KG("out", *)`` and must do so under the graph, and
``degree`` must hand ``Graph.KG.Adj.Degree`` the graph key (the fake below answers
it from the ``out`` tree, counting a parallel edge's ``"#"`` multiplicity as the
real method does, and records the key it was given).

These tests serve the real layout through a fake Native API and assert the
subscript paths, because that is the whole fact under test: nothing about the
shape of the answer distinguishes "walked the wrong depth" from "the graph is
empty".
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._validate import graph_index_key


class _FakeGlobals:
    """A ``^KG`` subtree served the way the IRIS Native API serves one.

    Keys are compared as strings: IRIS canonicalizes the integer subscript ``0``
    and returns it as ``'0'``, which is exactly why the default graph key and the
    old shard number are indistinguishable at the wire and why this defect stayed
    hidden on the ``out``/``in`` walks.
    """

    def __init__(self, tree: dict):
        self.tree = tree
        self.paths: list = []
        self.degree_calls: list = []

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
        """`Graph.KG.Adj.Degree(graph, src, pred = "")`, answered from the ``out`` tree of the graph it was given."""
        assert (cls, method) == ("Graph.KG.Adj", "Degree"), (cls, method)
        graph, src = args[0], args[1]
        pred = args[2] if len(args) > 2 else ""
        self.degree_calls.append((str(graph), str(src), str(pred)))
        preds = self._node(("out", graph, src)) or {}
        total = 0
        for p, targets in preds.items():
            if pred != "" and p != pred:
                continue
            for _o, leaf in targets.items():
                total += int(leaf.get("#", 1)) if isinstance(leaf, dict) else 1
        return total


def _kg(graph_key, *, out=None, inn=None) -> dict:
    """One graph's adjacency, keyed under the graph subscript like the real global."""
    g = str(graph_key)
    tree = {}
    if out is not None:
        tree["out"] = {g: out}
    if inn is not None:
        tree["in"] = {g: inn}
    return tree


def _lazykg(tree, **kwargs):
    fake = _FakeGlobals(tree)
    from iris_vector_graph.stores.lazy_kg import LazyKG

    # The fake is the connection's own native object (conn.iris), which host_for uses before iris.createIRIS.
    return LazyKG(SimpleNamespace(iris=fake), **kwargs), fake


# --- the default graph ----------------------------------------------------------


def test_iter_nodes_yields_nodes_and_not_graph_subscripts():
    """The failure that scored a node called ``"0"``.

    Walking ``^KG("out", *)`` without the graph subscript enumerates graphs, and every
    name it returns is a real subscript — so the result looks like a one-node
    graph rather than like a bug.
    """
    tree = _kg(0, out={"alice": {"R": {"x": 1}}, "bob": {"R": {"y": 1}}})
    lkg, _ = _lazykg(tree, include_sinks=False)

    assert list(lkg.iter_nodes()) == ["alice", "bob"]


def test_degree_asks_for_the_graph_keyed_degree():
    tree = _kg(0, out={"alice": {"R": {"x": 1, "y": 1}, "S": {"z": 1}}})
    lkg, fake = _lazykg(tree, include_sinks=False)

    assert lkg.degree("alice") == 3
    assert fake.degree_calls == [("0", "alice", "")], (
        f"degree asked the wrong graph or node: {fake.degree_calls}"
    )


def test_degree_for_predicate_asks_for_the_graph_keyed_degree_of_that_predicate():
    tree = _kg(0, out={"alice": {"KNOWS": {"bob": 1, "carol": 1}, "CITES": {"d": 1}}})
    lkg, fake = _lazykg(tree, include_sinks=False)

    assert lkg.degree_for_predicate("alice", "KNOWS") == 2
    assert fake.degree_calls == [("0", "alice", "KNOWS")], fake.degree_calls


def test_a_parallel_edge_counts_its_multiplicity():
    tree = _kg(0, out={"alice": {"KNOWS": {"bob": {"#": 3, "1": 1, "2": 1, "3": 1}}}})
    lkg, _ = _lazykg(tree, include_sinks=False)

    assert lkg.degree("alice") == 3


def test_out_neighbors_walks_under_the_graph_key():
    tree = _kg(0, out={"alice": {"KNOWS": {"bob": 1}, "CITES": {"carol": 1}}})
    lkg, _ = _lazykg(tree, include_sinks=False)

    assert sorted(lkg.out_neighbors("alice")) == ["bob", "carol"]


def test_in_neighbors_and_in_degree_walk_under_the_graph_key():
    tree = _kg(0, inn={"bob": {"KNOWS": {"alice": 1, "carol": 1}}})
    lkg, _ = _lazykg(tree, include_sinks=False)

    assert sorted(lkg.in_neighbors("bob")) == ["alice", "carol"]
    assert lkg.in_degree("bob") == 2
    assert lkg.in_degree_for_predicate("bob", "KNOWS") == 2


def test_sinks_come_from_the_graphs_inbound_tree():
    tree = _kg(0, out={"alice": {"KNOWS": {"bob": 1}}}, inn={"bob": {"KNOWS": {"alice": 1}}})
    lkg, _ = _lazykg(tree, include_sinks=True)

    assert sorted(lkg.iter_nodes()) == ["alice", "bob"]


# --- a named graph --------------------------------------------------------------


def test_a_named_graph_reads_its_own_subtree():
    """The graph is a constructor argument, not a coincidence.

    Without one, every LazyKG read is pinned to whichever subscript the literal
    ``0`` happens to name — the default graph — and a named graph's adjacency is
    unreachable through this adapter at all.
    """
    tree = {
        "out": {
            "0": {"default-only": {"R": {"x": 1}}},
            "tenant-a": {"alice": {"KNOWS": {"bob": 1, "carol": 1}}},
        },
    }
    lkg, fake = _lazykg(tree, include_sinks=False, graph="tenant-a")

    assert list(lkg.iter_nodes()) == ["alice"]
    assert lkg.degree("alice") == 2
    assert lkg.degree_for_predicate("alice", "KNOWS") == 2
    assert lkg.out_neighbors("alice") == ["bob", "carol"]
    assert {call[0] for call in fake.degree_calls} == {"tenant-a"}


def test_two_graphs_do_not_bleed_into_each_other():
    tree = {
        "out": {
            "tenant-a": {"shared:1": {"R": {"a-only": 1, "a-two": 1}}},
            "tenant-b": {"shared:1": {"R": {"b-only": 1, "b-two": 1, "b-three": 1}}},
        },
    }
    a, _ = _lazykg(tree, include_sinks=False, graph="tenant-a")
    b, _ = _lazykg(tree, include_sinks=False, graph="tenant-b")

    assert a.degree("shared:1") == 2
    assert b.degree("shared:1") == 3
    assert a.out_neighbors("shared:1") == ["a-only", "a-two"]
    assert b.out_neighbors("shared:1") == ["b-only", "b-three", "b-two"]


@pytest.mark.parametrize("graph", [None, ""])
def test_an_omitted_graph_is_the_default_graph(graph):
    """``None`` and ``""`` both mean the default graph, whose key is the integer 0.

    Asserted against ``graph_index_key`` rather than against a literal so the two
    derivations cannot drift: ADR-0003 gives that function sole ownership of the
    mapping on the Python side.
    """
    tree = _kg(graph_index_key(graph), out={"alice": {"R": {"x": 1}}})
    lkg, fake = _lazykg(tree, include_sinks=False, graph=graph)

    assert list(lkg.iter_nodes()) == ["alice"]
    assert lkg.degree("alice") == 1
    assert fake.degree_calls == [(str(graph_index_key(graph)), "alice", "")], fake.degree_calls


def test_a_reserved_graph_name_is_refused_at_construction():
    """``"0"`` is the default graph's key, so accepting it as a name would make
    two different graphs read the same subtree."""
    with pytest.raises(ValueError):
        _lazykg(_kg(0, out={}), graph="0")
