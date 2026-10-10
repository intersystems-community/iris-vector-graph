"""Line coverage for iris.vector.graph.GraphOperators, called as class methods.

The engine's own kg_GRAPH_PATH and kg_TXT run in Python, so the ObjectScript procedures
behind the SQL names are called directly here. Edges carry a JSON `confidence` qualifier
for kg_TXT. Everything is written under a unique prefix and removed afterwards.
"""

from __future__ import annotations

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

GO = "iris.vector.graph.GraphOperators"


@pytest.fixture
def ops(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    p = f"go{uuid.uuid4().hex[:6]}"
    obj = eng._store._iris_obj()
    for n in ("a", "b", "c", "d"):
        eng.create_node(f"{p}:{n}")
    eng.create_edge(f"{p}:a", "KNOWS", f"{p}:b", qualifiers={"confidence": 900})
    eng.create_edge(f"{p}:b", "WORKS", f"{p}:c", qualifiers={"confidence": 500})
    eng.create_edge(f"{p}:a", "KNOWS", f"{p}:d", qualifiers={"confidence": 100})

    class Handle:
        prefix = p

        def rows(self, method, *args):
            out = obj.classMethodObject(GO, method, *args)
            return json.loads(str(out.invoke("%ToJSON")))

        def value(self, method, *args):
            return obj.classMethodValue(GO, method, *args)

    yield Handle()
    eng.delete_nodes_by_prefix(p)


@pytest.mark.parametrize("raw,expected", [(5, 5), ("", 50), (0, 1), (-3, 1), (5000, 1000), ("7", 7)])
def test_validate_k_clamps_to_a_usable_range(ops, raw, expected):
    assert ops.value("ValidateK", raw) == expected


def test_graph_path_returns_both_hops_of_a_two_step_path(ops):
    p = ops.prefix
    rows = ops.rows("kgGRAPHPATH", f"{p}:a", "KNOWS", "WORKS", 2)
    steps = {(r["step"], r["s"], r["p"], r["o"]) for r in rows}
    assert (1, f"{p}:a", "KNOWS", f"{p}:b") in steps
    assert (2, f"{p}:b", "WORKS", f"{p}:c") in steps


def test_graph_path_with_an_unknown_predicate_is_empty(ops):
    assert ops.rows("kgGRAPHPATH", f"{ops.prefix}:a", "NOPE", "WORKS", 2) == []


def test_text_search_ranks_a_match_above_a_confidence_only_hit(ops):
    p = ops.prefix
    rows = ops.rows("kgTXT", f"{p}:c", 10, 400)
    ids = [r["id"] for r in rows]
    # the edge b->c matches the text; a->b clears the confidence floor
    assert f"{p}:b" in ids and f"{p}:a" in ids
    assert ids.index(f"{p}:b") < ids.index(f"{p}:a")
    assert rows == sorted(rows, key=lambda r: -r["score"])


def test_text_search_respects_the_result_cap(ops):
    assert len(ops.rows("kgTXT", ops.prefix, 1, 0)) == 1


def test_fusion_ranks_by_the_text_side_when_the_vector_side_is_empty(ops):
    p = ops.prefix
    rows = ops.rows("kgRRFFUSE", 5, 5, 5, 60, "[0.1,0.2]", f"{p}:c")
    assert rows, "text hits should still be fused"
    assert all({"id", "rrf_score", "vector_score", "text_score"} <= set(r) for r in rows)
    assert [r["rrf_score"] for r in rows] == sorted((r["rrf_score"] for r in rows), reverse=True)
    assert len(ops.rows("kgRRFFUSE", 1, 5, 5, 60, "[0.1,0.2]", f"{p}:c")) == 1


def test_vector_search_returns_an_array_even_when_the_table_is_missing(ops):
    out = ops.rows("kgKNNVEC", json.dumps([0.1] * 768), 3)
    assert isinstance(out, list)
    assert isinstance(ops.rows("kgKNNVEC", json.dumps([0.1] * 768), 3, "Person"), list)
