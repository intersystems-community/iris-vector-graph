"""4.6.0 R175: the server-side Arno adjacency walk (`ivg_arno_build_adj`) runs, and agrees with the Native API walk.

It walked `^KG("out", "", 0, ...)`, the layout before spec 214, so every call failed with `<SUBSCRIPT> ^KG("out","")`
and `build_kg_adjacency_chunked` swallowed the error and walked `^KG` over the Native API instead.

The namespace (IVG262CONA, or IVG_R175_NAMESPACE; IVG262* only) is made anew and set up by this release. The graph,
by hand: a 3-cycle r175a -> r175b -> r175c -> r175a in the default graph, and r175x -> r175y in graph "g2". Both walks
read the default graph: nodes [r175a, r175b, r175c] in $Order order, three edges, and each upload, read by the Rust
SCC kernel, answers one component of size 3.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_R175_NAMESPACE", "IVG262CONA")
CYCLE = ("r175a", "r175b", "r175c")


@pytest.fixture(scope="module")
def conn():
    if not NS.startswith("IVG262"):
        pytest.fail(f"refusing {NS}: an IVG262* scratch namespace only")
    from iris_vector_graph import IRISGraphEngine

    ns262.recreate(NS)
    c = ns262.connect(NS)
    engine = IRISGraphEngine(c, embedding_dimension=8, namespace=NS, native_cypher=False)
    engine.initialize_schema()
    for n in CYCLE:
        assert engine.create_node(n, labels=["R175"])
    for a, b in zip(CYCLE, CYCLE[1:] + CYCLE[:1]):
        assert engine.create_edge(a, "R", b)
    for n in ("r175x", "r175y"):
        engine.create_node(n, labels=["R175"], graph="g2")
    engine.create_edge("r175x", "R", "r175y", graph="g2")
    c.commit()
    yield c
    c.close()


def _scc(c):
    import json

    from iris_vector_graph.stores.arno_bridge import arno_call

    raw = arno_call(c, "kg_scc_run", 100)
    return sorted((r["id"], r["size"]) for r in json.loads(raw))


def test_the_server_side_walk_answers_and_matches_the_native_api_walk(conn, monkeypatch):
    from iris_vector_graph.stores import arno_bridge

    arno_bridge.clear_probe_cache()
    if not arno_bridge.arno_available(conn):
        pytest.fail("libarno_callout is not available in this namespace: the server-side walk cannot be measured")

    answered = []
    real = arno_bridge._build_kg_adjacency_serverside

    def spy(c, schema="Graph_KG"):
        out = real(c, schema=schema)
        answered.append(out)
        return out

    monkeypatch.setattr(arno_bridge, "_build_kg_adjacency_serverside", spy)
    server = arno_bridge.build_kg_adjacency_chunked(conn)
    assert answered == [server]  # the server-side function answered, nothing else
    server_scc = _scc(conn)

    native = arno_bridge.build_kg_adjacency_chunked(conn, server_side=False)
    native_scc = _scc(conn)

    assert server == native == (list(CYCLE), 3)
    assert server_scc == native_scc == [(n, 3) for n in CYCLE]


def test_the_engines_scc_takes_the_server_side_path(conn, monkeypatch):
    """`strongly_connected_components()` reaches the server-side walk (before R175 it raised inside it, and the store
    fell back without a word)."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.stores import arno_bridge

    calls = []
    real = arno_bridge._build_kg_adjacency_serverside
    monkeypatch.setattr(arno_bridge, "_build_kg_adjacency_serverside",
                        lambda c, schema="Graph_KG": calls.append(schema) or real(c, schema=schema))
    engine = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)
    rows = engine.strongly_connected_components()
    assert calls == ["Graph_KG"]
    assert sorted((r["id"], r["size"]) for r in rows) == [(n, 3) for n in CYCLE]
