"""LazyKG reads the ``^KG`` layout that exists, not the one spec 162 wrote against.

Spec 214 put the graph between the tree name and the node in every adjacency
store: ``^KG("out", graph, s, p, o)``, ``^KG("in", graph, o, p, s)``,
``^KG("deg", graph, s)``, ``^KG("degp", graph, s, p)``, with the integer ``0``
keying the default graph (``iris_vector_graph._validate.graph_index_key``).
``LazyKG`` was never moved with it. It hardcoded a literal ``0`` in the ``out``
and ``in`` walks — where it happens to land on the default graph's subscript by
coincidence, because the old shard number and the new default graph key are both
``0`` — and omitted it entirely from ``deg`` and ``degp``.

The visible damage on a live container:

- ``iter_nodes()`` walks ``^KG("deg", *)`` and yields the *graph* subscripts, so
  degree centrality scored one node called ``"0"``.
- ``degree(node)`` and ``degree_for_predicate(node, p)`` read one subscript short
  and got nothing, so every out-degree was ``0`` — which is a number, not an
  error, so k-core reported a ring with min core 0 and betweenness found no
  reachable nodes.

These tests serve the real layout through a fake Native API and assert the
subscript paths, because that is the whole fact under test: nothing about the
shape of the answer distinguishes "walked the wrong depth" from "the graph is
empty".
"""

from unittest.mock import MagicMock, patch

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


def _kg(graph_key, *, out=None, deg=None, degp=None, inn=None) -> dict:
    """One graph's adjacency, keyed under the graph subscript like the real global."""
    g = str(graph_key)
    tree = {}
    if out is not None:
        tree["out"] = {g: out}
    if inn is not None:
        tree["in"] = {g: inn}
    if deg is not None:
        tree["deg"] = {g: deg}
    if degp is not None:
        tree["degp"] = {g: degp}
    return tree


def _lazykg(tree, **kwargs):
    fake = _FakeGlobals(tree)
    with patch("iris.createIRIS", return_value=fake):
        from iris_vector_graph.stores.lazy_kg import LazyKG

        return LazyKG(MagicMock(), **kwargs), fake


# --- the default graph ----------------------------------------------------------


def test_iter_nodes_yields_nodes_and_not_graph_subscripts():
    """The failure that scored a node called ``"0"``.

    Walking ``^KG("deg", *)`` on the current layout enumerates graphs, and every
    name it returns is a real subscript — so the result looks like a one-node
    graph rather than like a bug.
    """
    tree = _kg(0, deg={"alice": 2, "bob": 1})
    lkg, _ = _lazykg(tree, include_sinks=False)

    assert list(lkg.iter_nodes()) == ["alice", "bob"]


def test_degree_reads_the_graph_keyed_counter():
    tree = _kg(0, deg={"alice": 3})
    lkg, fake = _lazykg(tree, include_sinks=False)

    assert lkg.degree("alice") == 3
    assert ("deg", "0", "alice") in fake.paths, (
        f"degree read the wrong depth: {fake.paths}"
    )


def test_degree_for_predicate_reads_the_graph_keyed_counter():
    tree = _kg(0, degp={"alice": {"KNOWS": 2}})
    lkg, fake = _lazykg(tree, include_sinks=False)

    assert lkg.degree_for_predicate("alice", "KNOWS") == 2
    assert ("degp", "0", "alice", "KNOWS") in fake.paths, fake.paths


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
    tree = _kg(0, deg={"alice": 1}, inn={"bob": {"KNOWS": {"alice": 1}}})
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
        "deg": {"0": {"default-only": 1}, "tenant-a": {"alice": 2}},
        "out": {"tenant-a": {"alice": {"KNOWS": {"bob": 1}}}},
        "degp": {"tenant-a": {"alice": {"KNOWS": 2}}},
    }
    lkg, _ = _lazykg(tree, include_sinks=False, graph="tenant-a")

    assert list(lkg.iter_nodes()) == ["alice"]
    assert lkg.degree("alice") == 2
    assert lkg.degree_for_predicate("alice", "KNOWS") == 2
    assert lkg.out_neighbors("alice") == ["bob"]


def test_two_graphs_do_not_bleed_into_each_other():
    tree = {
        "deg": {"tenant-a": {"shared:1": 5}, "tenant-b": {"shared:1": 9}},
        "out": {
            "tenant-a": {"shared:1": {"R": {"a-only": 1}}},
            "tenant-b": {"shared:1": {"R": {"b-only": 1}}},
        },
    }
    a, _ = _lazykg(tree, include_sinks=False, graph="tenant-a")
    b, _ = _lazykg(tree, include_sinks=False, graph="tenant-b")

    assert a.degree("shared:1") == 5
    assert b.degree("shared:1") == 9
    assert a.out_neighbors("shared:1") == ["a-only"]
    assert b.out_neighbors("shared:1") == ["b-only"]


@pytest.mark.parametrize("graph", [None, ""])
def test_an_omitted_graph_is_the_default_graph(graph):
    """``None`` and ``""`` both mean the default graph, whose key is the integer 0.

    Asserted against ``graph_index_key`` rather than against a literal so the two
    derivations cannot drift: ADR-0003 gives that function sole ownership of the
    mapping on the Python side.
    """
    tree = _kg(graph_index_key(graph), deg={"alice": 1})
    lkg, fake = _lazykg(tree, include_sinks=False, graph=graph)

    assert list(lkg.iter_nodes()) == ["alice"]
    assert lkg.degree("alice") == 1
    assert ("deg", str(graph_index_key(graph)), "alice") in fake.paths, fake.paths


def test_a_reserved_graph_name_is_refused_at_construction():
    """``"0"`` is the default graph's key, so accepting it as a name would make
    two different graphs read the same subtree."""
    with pytest.raises(ValueError):
        _lazykg(_kg(0, deg={}), graph="0")
