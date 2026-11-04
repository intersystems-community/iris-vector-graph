"""4.6.0 R195, live: EigenvectorGlobal's answer is JSON, so eigenvector_centrality takes the ^NKG fast path.

`Graph.KG.NKGAccelCentrality.EigenvectorGlobal` wrote each score as ObjectScript renders it, `.5773502691896258`
with no leading zero, which is no JSON number: `json.loads` raised, the store swallowed it, and every call fell back
to the Python power iteration over `^KG` (one Native API round trip per node).

Namespace: `IVG_NAMESPACE`, an IVG460FX* scratch namespace set up for `Graph_KG` by
`scripts/dev/make_scratch_namespace.py` (IVG460FXL when this was written). The fixture empties it first
(`Graph.KG.Eraser.EraseAll`), so never point it at a namespace another run is using.

By hand: the default graph alone, a directed 3-cycle a -> b -> c -> a. Each node has one in-edge, so A^T x keeps the
uniform vector, and L2-normalised every score is 1/sqrt(3) = 0.57735: below 1, the case ObjectScript writes as `.577`.
"""

from __future__ import annotations

import json
import math
import os
from unittest import mock

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

EDGES = (("a", "b"), ("b", "c"), ("c", "a"))


def _id(n: str) -> str:
    return f"r195:{n}"


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for n in "abc":
        eng.create_node(_id(n))
    for s, o in EDGES:
        eng.create_edge(_id(s), "R", _id(o))
    conn.commit()
    eng.sync()
    yield eng
    conn.close()


def test_eigenvector_global_answers_json_numbers(engine):
    import iris

    raw = str(iris.createIRIS(engine.conn).classMethodValue("Graph.KG.NKGAccel", "EigenvectorGlobal", 30, 1e-6, 0))
    assert raw.startswith("OK:"), raw
    scores = {d["id"]: d["score"] for d in json.loads(raw[3:])}
    assert scores == pytest.approx({_id(n): 1 / math.sqrt(3) for n in "abc"}, abs=1e-6)


def test_eigenvector_centrality_takes_the_fast_path_and_answers_as_the_python_walk(engine):
    store = engine._store
    real = store._iris_obj
    calls = []

    def spying():
        native = real()

        class _Spy:
            def classMethodValue(self, cls, method, *args):
                calls.append(method)
                return native.classMethodValue(cls, method, *args)

            def __getattr__(self, name):
                return getattr(native, name)

        return _Spy()

    def no_walk(*a, **k):
        raise AssertionError("the Python walk answered: EigenvectorGlobal's answer was not read")

    with mock.patch.object(store, "_iris_obj", spying), mock.patch(
        "iris_vector_graph.stores.lazy_kg.LazyKG", side_effect=no_walk
    ):
        fast = {d["id"]: d["score"] for d in engine.eigenvector_centrality(max_iter=30, tol=1e-6)}
    assert "EigenvectorGlobal" in calls, calls

    # the Python walk, asked for by making ^NKG look merged with a named graph's edges (R184's skip)
    with mock.patch.object(store, "_named_graph_with_edges", return_value="r195-other"):
        walk = {d["id"]: d["score"] for d in engine.eigenvector_centrality(max_iter=30, tol=1e-6)}
    assert set(fast) == set(walk) == {_id(n) for n in "abc"}
    assert fast == pytest.approx(walk, abs=1e-6)
    assert fast == pytest.approx({_id(n): 1 / math.sqrt(3) for n in "abc"}, abs=1e-6)
