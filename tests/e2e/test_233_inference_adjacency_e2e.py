"""Spec 233 FR-015: inferred edges reach the adjacency index, and inference reads
only the target graph.

`import_rdf(..., infer="rdfs")` into a scratch graph, next to a second graph whose
edges the rules must not use. Concept expansion walks `^KG`, so an inferred edge
that is only in `rdf_edges` is invisible to it.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]

EX = "http://example.org/ivg233/"
SUB = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

_A = f"""@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <{EX}> .
ex:Dog rdfs:subClassOf ex:Mammal .
ex:Mammal rdfs:subClassOf ex:Animal .
ex:rex a ex:Dog .
ex:p rdfs:domain ex:Q .
"""
_B = f"""@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <{EX}> .
ex:x ex:p ex:y .
ex:Animal rdfs:subClassOf ex:Thing .
"""


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


@pytest.fixture(scope="module")
def inferred(fhir_conn_required, tmp_path_factory):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(fhir_conn_required, embedding_dimension=4)
    tag = uuid.uuid4().hex[:8]
    a, b = f"infa{tag}", f"infb{tag}"
    d = tmp_path_factory.mktemp("inf")
    (d / "a.ttl").write_text(_A)
    (d / "b.ttl").write_text(_B)
    try:
        eng.import_rdf(str(d / "b.ttl"), graph=b)
        result = eng.import_rdf(str(d / "a.ttl"), graph=a, infer="rdfs")
        yield eng, a, b, result
    finally:
        eng.erase_graph(a)
        eng.erase_graph(b)


def _edges(conn, graph):
    return {(s, p, o) for s, p, o in _rows(conn, "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", graph)}


def _in_kg(eng, graph, s, p, o):
    iris = eng._iris_obj()
    gk = str(iris.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph))
    return str(iris.classMethodValue("Graph.KG.Meta", "GetKG", "out", gk, s, p, o) or "") != ""


def test_inferred_edges_in_rdf_edges(fhir_conn_required, inferred):
    _, a, _, result = inferred
    edges = _edges(fhir_conn_required, a)
    print(f"\nimport_rdf result {result}")
    assert (EX + "Dog", SUB, EX + "Animal") in edges
    assert (EX + "rex", TYPE, EX + "Mammal") in edges


def test_inferred_edges_in_adjacency(inferred):
    eng, a, _, _ = inferred
    assert _in_kg(eng, a, EX + "Dog", SUB, EX + "Mammal"), "an asserted edge is not in ^KG"
    assert _in_kg(eng, a, EX + "Dog", SUB, EX + "Animal"), "the inferred edge is not in ^KG"
    assert _in_kg(eng, a, EX + "rex", TYPE, EX + "Mammal")


def test_other_graph_not_used(fhir_conn_required, inferred):
    _, a, _, _ = inferred
    edges = _edges(fhir_conn_required, a)
    assert (EX + "x", TYPE, EX + "Q") not in edges, "a domain rule in A typed a node from B's edges"
    assert (EX + "Dog", SUB, EX + "Thing") not in edges, "B's subClassOf edge joined A's closure"
