"""NKGAccelTraversal paths that run without the Arno library: the ObjectScript fallbacks.

Scratch namespace IVG236, where the library is not loaded. StructuralGuard takes a $LIST, which only
ObjectScript can build, so those cases run in a terminal session (markers around the output).
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.integration._236_world import PRED, World

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
TRAV = "Graph.KG.NKGAccelTraversal"


@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.native().classMethodValue("Graph.KG.Eraser", "EraseAll")
    w.renew()
    e = w.engine()
    for s, o in (("a", "b"), ("b", "c"), ("c", "d")):
        e.create_edge(s, PRED, o)
    e.create_edge("a", "U", "z")
    w.commit()
    w.native().classMethodValue("Graph.KG.Traversal", "BuildNKG")
    w.renew()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def call(world, method, *args):
    native = world.native()
    out = native.classMethodValue(TRAV, method, *args)
    world.scratch.require_scratch(native)
    return "" if out is None else str(out)


def session(*statements):
    script = 'write "<<"_"OUT",!\n' + "\n".join(statements) + '\nwrite !,"OUT"_">>",!\nhalt\n'
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "IVG236"],
        input=script, capture_output=True, text=True, timeout=120,
    ).stdout
    body = out.split("<<OUT", 1)[1].split("OUT>>", 1)[0]
    return "\n".join(line for line in body.splitlines() if line.strip() and not line.startswith("IVG236")).strip()


def test_bfs_json_answers_in_the_sorted_wire_form_whichever_path_serves_it(world):
    out = call(world, "BFSJson", "a", "[]", 3, 0)
    assert out.startswith("SORTED:"), out
    parts = out.split(":")
    if len(parts) == 3:  # rows were parked under a tag
        rows = json.loads(str(world.native().classMethodValue("Graph.KG.Traversal", "ReadBFSResults", parts[1])))
        assert {r["o"] for r in rows} <= {"b", "c", "d", "z"} and rows
    else:
        # the Arno path answers "SORTED:<tag>_bfs": a tag with the rows staged under it
        assert out != "SORTED:0" and len(parts) == 2


def test_shortest_path_nkg_answers_unreachable_and_degenerate_pairs(world):
    assert json.loads(call(world, "ShortestPathNKG", "", "d")) == {"hops": -1}
    assert json.loads(call(world, "ShortestPathNKG", "a", "nobody")) == {"hops": -1}
    assert json.loads(call(world, "ShortestPathNKG", "nobody", "a")) == {"hops": -1}
    assert json.loads(call(world, "ShortestPathNKG", "a", "a")) == {"hops": 0}
    assert json.loads(call(world, "ShortestPathNKG", "d", "a", 5))["hops"] in (-1, 3)


def test_structural_guard_requires_every_predicate_on_the_node(world):
    native = world.native()
    a = int(native.get("NKG", "$NI", "a"))
    d = int(native.get("NKG", "$NI", "d"))
    # predicate indexes are stored as -(index+1) subscripts; find the one used by PRED
    out = session(
        f'set pi=$order(^NKG(-1,{a},""))',
        f'write "G1:",##class(Graph.KG.NKGAccelTraversal).StructuralGuard({a},$lb(-pi-1)),!',
        f'write "G0:",##class(Graph.KG.NKGAccelTraversal).StructuralGuard({a},$lb(99999)),!',
        f'write "GD:",##class(Graph.KG.NKGAccelTraversal).StructuralGuard({d},$lb(-pi-1)),!',
        f'write "GE:",##class(Graph.KG.NKGAccelTraversal).StructuralGuard({a},""),!',
    )
    got = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
    assert got == {"G1": "1", "G0": "0", "GD": "0", "GE": "1"}, got


def test_random_walk_json_walks_the_out_edges_and_stops_at_a_dead_end(world):
    walks = json.loads(call(world, "RandomWalkJson", "c", 5, 3))
    assert len(walks) == 3 and all(w[0] == "c" for w in walks)
    assert all(w[:2] in (["c"], ["c", "d"]) for w in walks)  # d has no out-edge: the walk ends there


# ----------------------------------------------------------- ArnoAccel wrappers without the library

ARNO = "Graph.KG.ArnoAccel"


def arno(world, method, *args):
    native = world.native()
    out = native.classMethodValue(ARNO, method, *args)
    world.scratch.require_scratch(native)
    return "" if out is None else str(out)


@pytest.fixture
def no_library(world):
    """The scratch namespace never loads the library; make that explicit for the call."""
    native = world.native()
    saved = native.get("ArnoAccel", "dllid")
    native.kill("ArnoAccel", "dllid")
    yield world
    if saved is not None:
        native.set(saved, "ArnoAccel", "dllid")


def test_the_sampling_wrappers_answer_a_neutral_value_when_the_library_is_absent(no_library):
    assert json.loads(arno(no_library, "KhopSampleJson", '["a"]', 2, 5)) == {"layers": [], "error": "arno unavailable"}
    assert arno(no_library, "NeighborAggJson", '["a"]', 2) == "{}"
    assert arno(no_library, "RandomWalkJson", '["a"]', 5, 2) == "[]"


def test_the_graph_json_is_cached_against_the_version_token(no_library):
    native = no_library.native()
    arno(no_library, "KhopSampleJson", '["a"]', 2, 5)  # fills the cache before it asks the library
    version = native.get("KG", "__version")
    assert native.get("ArnoKG", "KG", "version") == version
    cached = native.get("ArnoKG", "KG", "graph_json")
    assert cached and "a" in cached
    arno(no_library, "RandomWalkJson", '["a"]', 5, 2)  # unchanged graph: served from the cache
    assert native.get("ArnoKG", "KG", "graph_json") == cached
    no_library.engine().create_edge("c", PRED, "e")  # a write moves the token ...
    no_library.commit()
    arno(no_library, "NeighborAggJson", '["a"]', 2)
    assert native.get("ArnoKG", "KG", "version") != version  # ... so the next call rebuilds
    assert "e" in native.get("ArnoKG", "KG", "graph_json")
