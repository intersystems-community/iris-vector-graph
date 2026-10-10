"""Line coverage for Graph.KG.VecIndex: the random-hyperplane forest, every metric, brute force.

Two well-separated clusters in four dimensions: A_i = [1, 0.01 i, 0, 0] and
B_i = [0, 0, 1, 0.01 i], 60 vectors each. A query in cluster A must never return a B,
whether the answer comes from the trees or from the brute-force path.
"""

from __future__ import annotations

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

VI = "Graph.KG.VecIndex"
N = 60


def docs():
    out = [{"id": f"A_{i}", "vec": [1.0, 0.01 * i, 0.0, 0.0]} for i in range(N)]
    out += [{"id": f"B_{i}", "vec": [0.0, 0.0, 1.0, 0.01 * i]} for i in range(N)]
    return out


@pytest.fixture
def vi(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    store = eng._store
    made: list[str] = []

    class Handle:
        def call(self, method, *args):
            raw = store._call_classmethod(VI, method, *args)
            return None if raw is None else str(raw)

        def make(self, metric="cosine", trees=3, leaf=8, load=True):
            name = f"t_vi_{uuid.uuid4().hex[:8]}"
            made.append(name)
            self.call("Create", name, 4, metric, trees, leaf)
            if load:
                got = json.loads(self.call("InsertBatchJSON", name, json.dumps(docs())))
                assert got == {"inserted": 2 * N}
            return name

        def search(self, name, vec, k=5, nprobe=8):
            return json.loads(self.call("SearchJSON", name, json.dumps(vec), k, nprobe))

    h = Handle()
    h.native_obj = store._iris_obj()
    h.insert = lambda name, doc, vec: store._iris_obj().classMethodVoid(VI, "InsertJSON", name, doc, json.dumps(vec))
    h.drop = lambda name: store._iris_obj().classMethodVoid(VI, "Drop", name)
    yield h
    for name in made:
        h.drop(name)


def test_build_reports_the_forest_it_made(vi):
    name = vi.make()
    built = json.loads(vi.call("Build", name))
    assert built == {"trees": 3, "vectors": 2 * N, "dim": 4, "metric": "cosine"}
    info = json.loads(vi.call("Info", name))
    assert (info["count"], info["trees"], info["leafSize"], info["dim"]) == (2 * N, 3, 8, 4)


def test_build_of_an_index_that_was_never_created_is_an_error_object(vi):
    assert json.loads(vi.call("Build", "t_vi_never_created")) == {"error": "no vectors found"}


def test_build_of_a_created_but_empty_index_builds_an_empty_forest(vi):
    name = vi.make(load=False)
    assert json.loads(vi.call("Build", name))["vectors"] == 0


def test_tree_search_stays_inside_the_query_cluster(vi):
    name = vi.make()
    vi.call("Build", name)
    hits = vi.search(name, [1.0, 0.2, 0.0, 0.0], k=10, nprobe=16)
    assert 0 < len(hits) <= 10
    assert all(h["id"].startswith("A_") for h in hits)
    assert [h["distance"] for h in hits] == sorted(h["distance"] for h in hits)


def test_a_vector_inserted_after_the_build_is_found(vi):
    name = vi.make()
    vi.call("Build", name)
    vi.insert(name, "A_new", [1.0, 0.123, 0.0, 0.0])
    hits = vi.search(name, [1.0, 0.123, 0.0, 0.0], k=3, nprobe=16)
    assert hits[0]["id"] == "A_new"
    assert json.loads(vi.call("Info", name))["count"] == 2 * N + 1


def test_without_trees_search_is_brute_force_and_exact(vi):
    name = vi.make(trees=0)
    hits = vi.search(name, [1.0, 0.05, 0.0, 0.0], k=3)
    assert hits[0]["id"] == "A_5"
    assert len(hits) == 3


@pytest.mark.parametrize(
    "metric,query,expected",
    [
        ("cosine", [1.0, 0.05, 0.0, 0.0], "A_5"),
        ("l2", [1.0, 0.052, 0.0, 0.0], "A_5"),
        ("l1", [1.0, 0.052, 0.0, 0.0], "A_5"),
        ("chebyshev", [1.0, 0.052, 0.0, 0.0], "A_5"),
        # the largest dot product with [1, .05, 0, 0] is the largest 0.01 i
        ("dot", [1.0, 0.05, 0.0, 0.0], f"A_{N - 1}"),
    ],
)
def test_every_metric_ranks_its_nearest_first(vi, metric, query, expected):
    name = vi.make(metric=metric, trees=0)
    hits = vi.search(name, query, k=2)
    assert hits[0]["id"] == expected
    if metric in ("cosine", "dot"):
        # similarity metrics report a score that falls as the distance rises
        assert hits[0]["score"] >= hits[1]["score"]
    else:
        assert hits[0]["score"] <= hits[1]["score"]


def test_unknown_metric_falls_back_to_cosine(vi):
    name = vi.make(metric="bogus", trees=0)
    assert vi.search(name, [1.0, 0.05, 0.0, 0.0], k=1)[0]["id"] == "A_5"


def test_search_multi_answers_each_query_in_order(vi):
    name = vi.make(trees=0)
    queries = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    out = json.loads(vi.call("SearchMultiJSON", name, json.dumps(queries), 2, 8))
    assert out[0][0]["id"] == "A_0" and out[1][0]["id"] == "B_0"
    assert all(h["id"].startswith("A_") for h in out[0])
    assert all(h["id"].startswith("B_") for h in out[1])


def test_drop_removes_the_index(vi):
    name = vi.make(trees=0)
    vi.drop(name)
    assert json.loads(vi.call("Info", name))["count"] == 0


# --------------------------------------------------------------------- the distance helpers


def _session(*statements):
    import os
    import subprocess

    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    script = 'write "<<"_"OUT",!\n' + "\n".join(statements) + '\nwrite !,"OUT"_">>",!\nhalt\n'
    out = subprocess.run(
        ["docker", "exec", "-i", container, "iris", "session", "IRIS", "-U", "USER"],
        input=script, capture_output=True, text=True, timeout=120,
    ).stdout
    body = out.split("<<OUT", 1)[1].split("OUT>>", 1)[0]
    return dict(
        line.split("=", 1) for line in body.splitlines() if "=" in line and not line.startswith("USER")
    )


def test_similarity_and_distance_follow_their_definitions_for_every_metric():
    got = _session(
        'set a="" set b=""',
        'set $vector(a,1,"double")=1,$vector(a,2,"double")=0,$vector(a,3,"double")=0',
        'set $vector(b,1,"double")=0,$vector(b,2,"double")=1,$vector(b,3,"double")=0',
        'for m="cosine","dot","l2","l1","chebyshev","other" { write "S_"_m_"=",$fnumber(##class(Graph.KG.VecIndex).Similarity(a,b,m),"",6),! }',
        'for m="cosine","dot","l2","l1","chebyshev","other" { write "D_"_m_"=",$fnumber(##class(Graph.KG.VecIndex).Distance(a,b,m),"",6),! }',
    )
    import math

    assert float(got["S_cosine"]) == 0 and float(got["S_dot"]) == 0
    assert float(got["S_l2"]) == pytest.approx(1 / (1 + math.sqrt(2)), abs=1e-5)
    assert float(got["S_l1"]) == pytest.approx(1 / 3, abs=1e-5)
    assert float(got["S_chebyshev"]) == pytest.approx(0.5, abs=1e-5)
    assert float(got["S_other"]) == 0  # an unknown metric reads as cosine
    assert float(got["D_cosine"]) == 1 and float(got["D_dot"]) == 0 and float(got["D_other"]) == 1
    assert float(got["D_l2"]) == pytest.approx(math.sqrt(2), abs=1e-5)
    assert float(got["D_l1"]) == 2 and float(got["D_chebyshev"]) == 1


def test_mahalanobis_with_an_identity_matrix_is_the_euclidean_distance():
    got = _session(
        'set a="" set b=""',
        'set $vector(a,1,"double")=1,$vector(a,2,"double")=0,$vector(a,3,"double")=0',
        'set $vector(b,1,"double")=0,$vector(b,2,"double")=1,$vector(b,3,"double")=0',
        'for i=1:1:3 { set r="" for j=1:1:3 { set $vector(r,j,"double")=$select(i=j:1,1:0) } do ##class(Graph.KG.VecIndex).SetInverseCov("ivgmahal",i,r) }',
        'write "M=",$fnumber(##class(Graph.KG.VecIndex).Mahalanobis(a,b,"ivgmahal"),"",6),!',
        'write "N=",$fnumber(##class(Graph.KG.VecIndex).Mahalanobis(a,b,"ivgnomatrix"),"",6),!',
        'write "Z=",$fnumber(##class(Graph.KG.VecIndex).Mahalanobis(a,a,"ivgmahal"),"",6),!',
        'kill ^VecIdx("ivgmahal")',
    )
    import math

    assert float(got["M"]) == pytest.approx(math.sqrt(2), abs=1e-5)
    assert float(got["N"]) == 0  # no matrix stored: no contribution
    assert float(got["Z"]) == 0


def test_build_reads_the_dimension_from_a_vector_when_the_config_has_none(vi):
    name = vi.make(trees=2)
    store_obj = vi.native_obj
    store_obj.kill("VecIdx", name, "cfg", "dim")
    built = json.loads(vi.call("Build", name))
    assert built["dim"] == 4 and built["vectors"] == 2 * N
