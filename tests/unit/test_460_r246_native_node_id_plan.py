"""4.6.0 R246: the native planner takes an unlabelled `{node_id: ...}` anchor and `RETURN x.node_id`.

Since R204 the guides listed both as reads the native executor declines, so they took the SQL path and never compiled
to an `IVGC.*` class (walkthrough 1 W7: every hop read it ran had one or the other).

- An id on a node with no label is the node itself, as with a label (`"id"` in the node's spec, `labels` empty): as
  the anchor, the executor finds it in `Graph_KG.nodes`, scoped by graph, so a node with no edge and no label still
  matches a read with no hop (`Exec.Anchors`).
- `x.node_id` is the node's identity, never a property row (the SQL path reads the `node_id` column): in a projection,
  an ORDER BY or a grouping key it is the key `"__id"`, which the executor reads from the binding. `x.id` stays a
  property on the SQL path, so it stays declined here.

The plan format changed, so PLAN_VERSION is 3 (and Exec.cls's PLANVERSION, which test_253_native_executor_version holds
to it). tests/e2e/test_460_r246_native_node_id_e2e.py runs these against IRIS.
"""

import pytest

from iris_vector_graph.cypher import native_plan
from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query


def _plan(cypher, params=None):
    return plan_query(parse_query(cypher), params if params is not None else {"x": "a"})


# --- the anchor half ---------------------------------------------------------------------------------------------


def test_an_unlabelled_node_id_is_the_anchor():
    p = _plan("MATCH (a {node_id: $x})-[:KNOWS]->(b) RETURN b.name")
    assert p is not None
    assert p["anchor"] == 0
    assert p["nodes"][0] == {"labels": [], "props": [], "cmp": [], "id": {"v": "a"}}


def test_an_unlabelled_id_is_the_anchor_too():
    """{id: x} names the node x, as {node_id: x} does (R194)."""
    p = _plan("MATCH (a {id: $x})-[:KNOWS]->(b) RETURN b.name")
    assert p["anchor"] == 0 and p["nodes"][0]["id"] == {"v": "a"}


def test_an_unlabelled_node_id_with_no_hop_is_planned():
    """The node alone: the executor finds it in the nodes table, so one with no edge and no label still matches."""
    p = _plan("MATCH (a {node_id: $x}) RETURN a.name")
    assert p is not None
    assert p["hops"] == [] and p["anchor"] == 0 and p["nodes"][0]["id"] == {"v": "a"}


def test_an_unlabelled_node_id_wins_over_a_property_lookup():
    p = _plan("MATCH (a:P {name: 'A'})-[:KNOWS]->(b {node_id: $x}) RETURN a.name")
    assert p["anchor"] == 1
    assert p["nodes"][1]["labels"] == [] and p["nodes"][1]["id"] == {"v": "a"}


def test_an_unlabelled_node_id_with_a_property_check():
    p = _plan("MATCH (a {node_id: $x, kind: 'k'})-[:KNOWS]->(b) RETURN b.name")
    assert p["nodes"][0]["id"] == {"v": "a"} and p["nodes"][0]["props"] == [["kind", {"v": "k"}]]


@pytest.mark.parametrize(
    "cypher, params",
    [
        ("MATCH (a {node_id: 1})-[:KNOWS]->(b) RETURN b.name", {}),  # not a string: the SQL path decides
        ("MATCH (a {node_id: $x})-[:KNOWS]->(b) RETURN b.name", {"x": 1}),
        ("MATCH (a {id: $x, node_id: $x})-[:KNOWS]->(b) RETURN b.name", None),  # two ids
        ("MATCH (a {node_id: $missing})-[:KNOWS]->(b) RETURN b.name", None),
    ],
)
def test_these_anchors_stay_on_the_sql_path(cypher, params):
    assert _plan(cypher, params) is None


# --- RETURN x.node_id --------------------------------------------------------------------------------------------


def test_a_returned_node_id_is_the_identity_key():
    p = _plan("MATCH (a:P {name: 'A'})-[:KNOWS]->(b) RETURN b.node_id")
    assert p is not None and p["returns"] == [[1, "__id"]]


def test_node_id_beside_properties():
    p = _plan("MATCH (a {node_id: $x})-[:KNOWS]->(b) RETURN a.node_id, b.name, b.node_id AS id")
    assert p["returns"] == [[0, "__id"], [1, "name"], [1, "__id"]]


def test_ordered_by_node_id():
    p = _plan("MATCH (a:P)-[:KNOWS]->(b) RETURN b.name ORDER BY b.node_id DESC LIMIT 2")
    assert p["order"] == [[1, "__id", True]] and p["limit"] == 2


def test_distinct_node_id_ordered_by_it():
    p = _plan("MATCH (a:P)-[:KNOWS]->(b) RETURN DISTINCT b.node_id ORDER BY b.node_id")
    assert p["distinct"] and p["returns"] == [[1, "__id"]] and p["order"] == [[1, "__id", False]]


def test_node_id_as_a_grouping_key():
    p = _plan("MATCH (a:P)-[:KNOWS]->(b) RETURN a.node_id AS id, count(*) AS n ORDER BY id")
    assert p["agg"]["keys"] == [[0, "__id"]]
    assert p["agg"]["cols"] == [["k", 0], ["a", 0]] and p["agg"]["order"] == [[0, False]]


def test_node_id_as_a_grouping_key_of_a_label_scan_with_no_hop():
    p = _plan("MATCH (a:P) RETURN a.node_id AS id, count(*) AS n")
    assert p is not None and p["agg"]["keys"] == [[0, "__id"]] and p["hops"] == []


def test_node_id_over_a_variable_length_hop():
    p = _plan("MATCH (a {node_id: $x})-[:KNOWS*1..2]->(b) RETURN DISTINCT b.node_id")
    assert p["returns"] == [[1, "__id"]] and p["hops"][0]["min"] == 1


def test_node_id_in_a_two_stage_plan():
    p = _plan("MATCH (a:P)-[:KNOWS]->(b) WITH b.node_id AS id, count(*) AS n RETURN n, count(*) AS c ORDER BY n")
    assert p is not None
    assert p["agg"]["keys"] == [[1, "__id"]]
    assert p["stage2"]["keys"] == [1]


def test_a_two_stage_plan_returning_the_node_id_column():
    p = _plan("MATCH (a:P)-[:KNOWS]->(b) WITH b.node_id AS id, count(*) AS n WHERE n > 1 RETURN id, n ORDER BY id")
    assert p is not None
    assert p["agg"]["keys"] == [[1, "__id"]] and p["stage2"]["keys"] == [0, 1]
    assert p["stage2"]["where"] == [[1, ">", 1]]


@pytest.mark.parametrize(
    "cypher",
    [
        # `x.id` is a property row on the SQL path, not the identity: left to it
        "MATCH (a:P)-[:KNOWS]->(b) RETURN b.id",
        # node_id in a filter or a function: not supported natively
        "MATCH (a:P)-[:KNOWS]->(b) WHERE b.node_id <> 'x' RETURN b.name",
        "MATCH (a:P)-[:KNOWS]->(b) WHERE b.node_id = a.node_id RETURN b.name",
        "MATCH (a:P)-[:KNOWS]->(b) RETURN substring(b.node_id, 1) AS s, count(*)",
    ],
)
def test_these_stay_on_the_sql_path(cypher):
    assert _plan(cypher) is None


def test_ordered_by_an_id_property_reads_the_property():
    """`ORDER BY b.id` reads the `id` property on both paths (the SQL path's `b.id` is a property row)."""
    p = _plan("MATCH (a:P)-[:KNOWS]->(b) RETURN b.name ORDER BY b.id")
    assert p["order"] == [[1, "id", False]]


def test_the_plan_format_version():
    assert native_plan.PLAN_VERSION == 3
