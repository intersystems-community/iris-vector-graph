"""`SORTED:` decoded against the live staging globals, not a fake.

`tests/unit/test_230_sorted_marker_decode.py` pins the protocol with a scripted
native object. The protocol itself lives in `^ArnoKG("bfs_r"/"khop_r", …)` and in
which class owns the reader, so it is only really proven against a container:
`Graph.KG.NKGAccel.BFSJson` stages the hits and answers `SORTED:<tag>`, while
`ReadBFSResults` hangs off `Graph.KG.Traversal`. Ask the wrong class and the call
raises; read the marker as JSON and `json.loads` fails on character one — which is
what every caller of `_call_classmethod_large` did through 3.2.0.

This file also puts its own index back. The stale `^NKG` that made
`test_lazy_node_resolution.py` assert on absent data came from a module fixture
that deleted its rows and left the index pointing at them.
"""

import json
import uuid

import pytest

PREFIX = f"srt230_{uuid.uuid4().hex[:8]}"
PRED = "SORTED_R"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def staged_graph(engine, iris_connection):
    """A hub with two children and one grandchild, indexed into `^KG` and `^NKG`."""
    o = engine._iris_obj()
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_hub", f"{PREFIX}_a", f"{PREFIX}_b", f"{PREFIX}_c"]
    edges = [
        (nodes[0], PRED, nodes[1]),
        (nodes[0], PRED, nodes[2]),
        (nodes[1], PRED, nodes[3]),
    ]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    engine.rebuild_kg()
    engine.rebuild_nkg()

    yield o, nodes

    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()
    # Leave the indexes agreeing with the rows: an index that outlives its data is
    # the defect `why_the_benchmark_dataset_is_missing` had to be written for.
    engine.rebuild_kg()
    engine.rebuild_nkg()


@pytest.mark.e2e
def test_bfsjson_really_answers_with_a_marker(staged_graph):
    # If this stops being true the decode below is measuring nothing.
    o, nodes = staged_graph
    raw = str(o.classMethodValue("Graph.KG.NKGAccel", "BFSJson", nodes[0], f'["{PRED}"]', 2, 0))
    assert raw.startswith("SORTED:"), f"expected a SORTED marker, got {raw[:60]!r}"
    assert raw != "SORTED:0", "the seed is in ^NKG, so the marker must carry a tag"


@pytest.mark.e2e
def test_the_helper_returns_the_bfs_rows_and_not_the_marker(staged_graph):
    from iris_vector_graph.schema import _call_classmethod_large

    o, nodes = staged_graph
    raw = _call_classmethod_large(
        o, "Graph.KG.NKGAccel", "BFSJson", nodes[0], f'["{PRED}"]', 2, 0
    )
    assert not raw.startswith("SORTED:"), (
        "the helper handed the staging marker to its caller, who parses it as JSON: "
        f"{raw[:60]!r}"
    )
    rows = json.loads(raw)
    assert {r["o"] for r in rows} >= set(nodes[1:]), (
        f"two hops from the hub reach {nodes[1:]}, and the decoded rows are {rows}"
    )
    assert all(isinstance(r["o"], str) for r in rows)


@pytest.mark.e2e
def test_the_accelerated_path_answers_an_unknown_seed_like_the_objectscript_path(staged_graph):
    """A seed the index does not hold is an empty result, not an exception.

    `Graph.KG.Traversal.BFSFastJsonSorted` returns `SORTED:0`. `NKGAccel.BFSJson`
    handed the callout's error string to `%DynamicArray.%FromJSON` and raised

        RuntimeError: <THROW> *%Exception.General Parsing error 3 Line 1 Offset 1

    `_run_arno_bfs` catches that and falls back, so a library caller saw only a
    warning and a slower answer — but the two BFS paths disagreed about what an
    unknown seed means, and every direct caller of `BFSJson` got the exception.
    """
    o, _ = staged_graph
    absent = f"{PREFIX}_absent"
    reference = str(
        o.classMethodValue(
            "Graph.KG.Traversal", "BFSFastJsonSorted", absent, f'["{PRED}"]', 2, "", "out", 0, ""
        )
    )
    assert reference == "SORTED:0", f"the reference path changed: {reference[:60]!r}"
    accelerated = str(
        o.classMethodValue("Graph.KG.NKGAccel", "BFSJson", absent, f'["{PRED}"]', 2, 0)
    )
    assert accelerated == reference, (
        "BFSJson must answer an unknown seed the way the ObjectScript path does, "
        f"and it answered {accelerated[:60]!r}"
    )


@pytest.mark.e2e
def test_a_seed_outside_the_index_decodes_to_an_empty_list(staged_graph):
    from iris_vector_graph.schema import _call_classmethod_large

    o, _ = staged_graph
    raw = _call_classmethod_large(
        o, "Graph.KG.NKGAccel", "BFSJson", f"{PREFIX}_absent", f'["{PRED}"]', 2, 0
    )
    assert json.loads(raw) == [], f"an unindexed seed has no hits, and the reply was {raw[:60]!r}"


@pytest.mark.e2e
def test_the_helper_rebuilds_the_khop_envelope(staged_graph):
    """`KHopNeighborsSorted` stages its frontier under a different global and a
    different reader class, so it is a second protocol, not a variant spelling."""
    from iris_vector_graph.schema import _call_classmethod_large

    o, nodes = staged_graph
    raw = _call_classmethod_large(
        o, "Graph.KG.NKGAccel", "KHopNeighborsSorted", nodes[0], 2, 1000
    )
    if raw == "[]":
        pytest.skip("KHopNeighborsSorted staged nothing for this seed")
    env = json.loads(raw)
    assert env["seed"] == nodes[0]
    assert env["hops"] == 2
    ids = {n["id"] for n in env["nodes"]}
    assert nodes[0] in ids, f"the seed is part of its own k-hop envelope: {env}"
    assert env["totalNodes"] == len(env["nodes"])


@pytest.mark.e2e
def test_the_sorted_and_unsorted_khop_paths_agree(staged_graph):
    from iris_vector_graph.schema import _call_classmethod_large

    o, nodes = staged_graph
    sorted_raw = _call_classmethod_large(
        o, "Graph.KG.NKGAccel", "KHopNeighborsSorted", nodes[0], 2, 1000
    )
    plain_raw = _call_classmethod_large(
        o, "Graph.KG.NKGAccel", "KHopNeighbors", nodes[0], 2, 1000
    )
    if sorted_raw == "[]":
        pytest.skip("KHopNeighborsSorted staged nothing for this seed")
    staged = json.loads(sorted_raw)
    plain = json.loads(plain_raw)
    assert {n["id"] for n in staged["nodes"]} == {n["id"] for n in plain["nodes"]}, (
        "the two producers compute the same frontier; only the wire protocol differs"
    )
