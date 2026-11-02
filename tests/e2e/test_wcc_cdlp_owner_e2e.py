"""The WCC and CDLP Owners (Graph.KG.Algorithms) answer the question exactly (Tom, 2026-11-01, arno spec 085).

WCC stopped after 100 rounds, so on a graph whose diameter is more than 100 it returned the labels it had reached
by then: the far end of a long path in a component of its own. It now loops until no label changes (min-label
propagation always gets there, in at most diameter + 1 rounds). A round limit a caller passes is a safety limit:
reaching it before the labels settle throws, rather than returning labels that are not the components.

CDLP broke a tie between equally frequent labels with a numeric compare (``lbl < bestLabel``). Every label
that is not a number compares as 0, so between "abc" and "5" it picked "abc", while $Order, arno and every
other rule here put "5" first. It now breaks ties by collation (``]]``): the label that sorts first wins.

The references are computed in Python from the edges each test writes, under a fresh prefix, so the rest of the
namespace's default graph is not part of the question: neither algorithm looks past a node's neighbours.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections import Counter, defaultdict

import pytest

from tests.fixtures.arno_reader_graphs import GRAPHS

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")]

ADJ = "Graph.KG.Adjacency"
ALG = "Graph.KG.Algorithms"
P = "IVG_WCC_CDLP"
# an ObjectScript canonical number: these sort before every string, in numeric order
_CANONICAL = re.compile(r"^(-?[1-9]\d*(\.\d*[1-9])?|-?\.\d*[1-9]|0)$")


def _collation(label: str):
    return (0, float(label), "") if _CANONICAL.match(label) else (1, 0.0, label)


@pytest.fixture
def adj(iris_connection):
    """Write default-graph edges through the writer; take them out after."""
    import iris

    o = iris.createIRIS(iris_connection)
    written = []

    def put(s, dst):
        assert o.classMethodValue(ADJ, "Put", 0, s, P, dst, 1) == 1
        written.append((s, dst))

    yield o, put, written
    for s, dst in reversed(written):
        o.classMethodValue(ADJ, "Remove", 0, s, P, dst)


def _wcc(o, *args):
    return json.loads(str(o.classMethodValue(ALG, "WCCJson", *args)))


def _cdlp(o, iterations):
    return json.loads(str(o.classMethodValue(ALG, "CDLPJson", iterations)))


def _ref_wcc(edges):
    """{node: the component's first node in collation}."""
    parent = {}

    def find(v):
        parent.setdefault(v, v)
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for s, dst in edges:
        parent[find(s)] = find(dst)
    members = defaultdict(list)
    for v in list(parent):
        members[find(v)].append(v)
    return {v: min(vs, key=_collation) for vs in members.values() for v in vs}


def _ref_cdlp(edges, iterations):
    """The Owner's walk: every node counts its neighbours' labels (out and in), takes the most frequent, the
    first in collation on a tie; all nodes move together; a round where no label changes ends it."""
    nbrs = defaultdict(list)
    for s, dst in edges:
        nbrs[s].append(dst)
        nbrs[dst].append(s)
    labels = {v: v for v in nbrs}
    for _ in range(iterations):
        new = {}
        for v in labels:
            counts = Counter(labels[u] for u in nbrs[v])
            new[v] = min(counts, key=lambda lbl: (-counts[lbl], _collation(lbl)))
        if new == labels:
            break
        labels = new
    return labels


def _prefixed(edges, prefix):
    return [(prefix + s, prefix + dst) for s, _p, dst in edges]


def _mine(got, want):
    return {v: got.get(v) for v in want}


def _long_path(put, prefix, n=130):
    nodes = [f"{prefix}p{i:03d}" for i in range(n)]
    for a, b in zip(nodes, nodes[1:]):
        put(a, b)
    return nodes


def test_wcc_labels_a_path_longer_than_100_rounds_as_one_component(adj):
    o, put, _ = adj
    nodes = _long_path(put, f"wcc_{uuid.uuid4().hex[:6]}_")
    got = _wcc(o)
    assert {v: got.get(v) for v in nodes} == {v: nodes[0] for v in nodes}


def test_wcc_with_a_round_limit_it_cannot_settle_in_throws(adj):
    o, put, _ = adj
    _long_path(put, f"wcc_{uuid.uuid4().hex[:6]}_")
    with pytest.raises(Exception, match="did not converge"):
        _wcc(o, 10)


def test_wcc_with_a_round_limit_it_settles_in_answers(adj):
    o, put, _ = adj
    nodes = _long_path(put, f"wcc_{uuid.uuid4().hex[:6]}_")
    got = _wcc(o, 200)
    assert {v: got.get(v) for v in nodes} == {v: nodes[0] for v in nodes}


def test_engine_wcc_labels_a_long_path_as_one_component(adj, iris_connection):
    from iris_vector_graph import IRISGraphEngine

    o, put, _ = adj
    nodes = _long_path(put, f"wcc_{uuid.uuid4().hex[:6]}_")
    got = IRISGraphEngine(iris_connection).kg_WCC()
    assert {v: got.get(v) for v in nodes} == {v: nodes[0] for v in nodes}


@pytest.mark.parametrize("name", list(GRAPHS))
def test_wcc_matches_the_reference_on_the_reader_gate_graphs(adj, name):
    o, put, _ = adj
    edges = _prefixed(GRAPHS[name]["edges"], f"wg_{uuid.uuid4().hex[:6]}_")
    for s, dst in edges:
        put(s, dst)
    want = _ref_wcc(edges)
    assert _mine(_wcc(o), want) == want


def test_cdlp_breaks_a_tie_between_a_number_and_a_string_by_collation(adj):
    """c has two neighbours, each its own label: the number sorts first, so c takes it."""
    o, put, _ = adj
    prefix = f"cdlp_{uuid.uuid4().hex[:6]}_"
    number = str(900_000_000 + uuid.uuid4().int % 99_999_999)
    put(prefix + "c", number)
    put(prefix + "c", prefix + "a")
    assert _cdlp(o, 1)[prefix + "c"] == number


@pytest.mark.parametrize("name", list(GRAPHS))
def test_cdlp_matches_the_reference_on_the_reader_gate_graphs(adj, name):
    o, put, _ = adj
    edges = _prefixed(GRAPHS[name]["edges"], f"cg_{uuid.uuid4().hex[:6]}_")
    for s, dst in edges:
        put(s, dst)
    want = _ref_cdlp(edges, 10)
    assert _mine(_cdlp(o, 10), want) == want
