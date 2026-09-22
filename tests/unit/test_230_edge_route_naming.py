"""Spec 230 US2 (FR-006) — edge vectors get the same routing as node vectors.

Spec 227 routed node embeddings per `(graph_id, model_key)` and left
`Graph_KG.kg_EdgeEmbeddings` alone: one namespace-wide table, keyed `(s, p, o_id)`,
one declared width, no graph. So two graphs cannot embed their edges under
different models, `edge_vector_search` answers every graph's edges, and the second
graph to arrive at a different width is refused at INSERT with SQLCODE -104.

This file pins the naming half — the nine guarantees `test_227_route_naming.py`
pins for node routes, restated for edge routes, plus the one guarantee that only
exists because there are now two kinds: a node route and an edge route for the
same `(graph_id, model_key)` must never be the same table. They hold different
key spaces, and merging them would put edge triples in a table whose FK points at
`nodes`.

Written out rather than parametrised over both functions: a shared parametrised
test passes when both functions are the same function, which is the bug.
"""

import hashlib
import subprocess
import sys

import pytest

from iris_vector_graph.constants import EDGE_ROUTE_TABLE_PREFIX, ROUTE_TABLE_PREFIX
from iris_vector_graph.routing import edge_route_table_name, route_table_name


def test_name_is_prefix_plus_16_hex():
    name = edge_route_table_name("g", "m")
    assert name.startswith(EDGE_ROUTE_TABLE_PREFIX)
    suffix = name[len(EDGE_ROUTE_TABLE_PREFIX) :]
    assert len(suffix) == 16, f"expected 16 hex characters, got {suffix!r}"
    assert all(c in "0123456789abcdef" for c in suffix), suffix


def test_name_matches_the_documented_hash():
    """Stated independently of the implementation, same as the node version."""
    graph, model = "ivg230-A", "ivg230-model-a"
    expected = (
        EDGE_ROUTE_TABLE_PREFIX
        + hashlib.sha256(f"{graph}\0{model}".encode("utf-8")).hexdigest()[:16]
    )
    assert edge_route_table_name(graph, model) == expected


def test_an_edge_route_is_never_a_node_route():
    """The distinguishing bit is the prefix, and it has to be load-bearing.

    Same graph, same model, two kinds of vector: `kg_emb_…` holds `(graph_id,
    node_id)` rows with an FK to `nodes`, `kg_eemb_…` holds `(graph_id, s, p,
    o_id)`. One table cannot be both.
    """
    assert edge_route_table_name("g", "m") != route_table_name("g", "m")
    assert EDGE_ROUTE_TABLE_PREFIX != ROUTE_TABLE_PREFIX


def test_the_two_prefixes_cannot_be_confused_by_a_prefix_test():
    """`kg_emb_` must not be a prefix of an edge route name.

    Code that classifies a table by `name.startswith(ROUTE_TABLE_PREFIX)` — the
    admin row count and the migration's table scan both do — would otherwise read
    every edge route as a node route.
    """
    edge = edge_route_table_name("g", "m")
    assert not edge.startswith(ROUTE_TABLE_PREFIX), edge
    node = route_table_name("g", "m")
    assert not node.startswith(EDGE_ROUTE_TABLE_PREFIX), node


def test_none_model_key_is_the_empty_model():
    """Spec 226's undeclared identity: a caller that named no model has one."""
    assert edge_route_table_name("g", None) == edge_route_table_name("g", "")


def test_separator_prevents_boundary_collision():
    assert edge_route_table_name("a", "bc") != edge_route_table_name("ab", "c")


def test_default_graph_is_a_route_like_any_other():
    assert edge_route_table_name("", "m") != edge_route_table_name("g", "m")
    assert edge_route_table_name("", None) == edge_route_table_name("", "")


def test_none_graph_is_refused():
    """`None` is not the default graph; `''` is. Hashing `None` would give the
    default graph a second name and split its rows across two tables."""
    with pytest.raises(TypeError):
        edge_route_table_name(None, "m")


@pytest.mark.parametrize(
    "graph",
    [
        "",
        "simple",
        "with space",
        "with-dash_and_underscore",
        "with.dots.and:colons",
        "with/slash\\backslash",
        'quote"and\'apostrophe',
        "unicode-ü-漢字-🜚",
        "x" * 4096,
        "\n\t\r",
        "graph_id",
        "DROP TABLE Graph_KG.nodes",
    ],
)
def test_name_is_a_legal_identifier_for_any_graph_id(graph):
    name = edge_route_table_name(graph, "model/with:punctuation")
    assert name.isidentifier(), f"{name!r} is not a legal identifier"
    derived = "Graph.KG." + name.replace("_", "")
    assert len(derived) <= 220, f"derived class name is {len(derived)} chars"


def test_name_is_stable_across_processes():
    here = edge_route_table_name("ivg230-A", "ivg230-model-a")
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from iris_vector_graph.routing import edge_route_table_name;"
            "print(edge_route_table_name('ivg230-A', 'ivg230-model-a'))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == here


def test_name_is_case_sensitive_in_the_graph_id():
    assert edge_route_table_name("Graph", "m") != edge_route_table_name("graph", "m")


def test_the_registry_can_tell_the_two_kinds_apart():
    """A route is *found* by reading the registry, never by recomputing a hash
    (FR-011/FR-015), and the registry's route lookup keys on `(graph_id,
    model_key)` alone. Without a `kind` discriminator an edge route is a candidate
    answer for a node read, and a node read would be handed a table of edges.
    """
    from iris_vector_graph._engine.schema import _EMBEDDING_REGISTRY_DDL, ROUTE_KINDS

    assert "kind" in _EMBEDDING_REGISTRY_DDL, _EMBEDDING_REGISTRY_DDL
    assert set(ROUTE_KINDS) == {"node", "edge"}, ROUTE_KINDS
