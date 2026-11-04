"""4.6.0 R100: N-Quads export puts a named graph's labels, properties and qualifier triples in that graph.

`_build_rdflib_graph` added the edges of a named graph to that graph's context and everything else (`rdf:type`, the
literal triples, an edge's reifier) to the default graph, so a round trip moved a named graph's node types and
properties into the default graph.

The graph, worked by hand (`create_node` also stores an `id` property equal to the node ID):
    default graph:                    alice:Person {name: Alice}
    graph `http://example.org/trials`: bob:Person {name: Bob} -KNOWS {since: 2020}-> carol:Person {name: Carol}
The named graph's IRI is the graph ID as written (docs/SEMANTIC_LAYER.md). The graph ID is an absolute IRI here: one
that is not (`trials`) is written as the relative `<trials>`, which N-Quads parsers refuse (reported with R100).
"""

from __future__ import annotations

import pytest
import rdflib  # the dev extra's
from rdflib import BNode, Literal, URIRef
from rdflib.namespace import RDF

pytestmark = [pytest.mark.e2e]

TRIALS = "http://example.org/trials"


def u(x):
    return URIRef(f"urn:ivg:{x}")


def prop(k):
    return URIRef(f"urn:ivg:prop/{k}")


EXPECTED_DEFAULT = {
    (u("alice"), RDF.type, u("Person")),
    (u("alice"), prop("name"), Literal("Alice")),
    (u("alice"), prop("id"), Literal("alice")),
}
EXPECTED_TRIALS = {
    (u("bob"), RDF.type, u("Person")),
    (u("bob"), prop("name"), Literal("Bob")),
    (u("bob"), prop("id"), Literal("bob")),
    (u("carol"), RDF.type, u("Person")),
    (u("carol"), prop("name"), Literal("Carol")),
    (u("carol"), prop("id"), Literal("carol")),
    (u("bob"), u("KNOWS"), u("carol")),
}
# the KNOWS edge's qualifier: statement node (rdf:subject/predicate/object), reifier (rdf:reifies, since)
REIFIER_PREDICATES = sorted(
    [str(RDF.subject), str(RDF.predicate), str(RDF.object), str(RDF) + "reifies", "urn:ivg:qualifier/since"]
)


def _build(engine):
    engine.create_node("alice", labels=["Person"], properties={"name": "Alice"})
    engine.create_node("bob", labels=["Person"], properties={"name": "Bob"}, graph=TRIALS)
    engine.create_node("carol", labels=["Person"], properties={"name": "Carol"}, graph=TRIALS)
    engine.create_edge("bob", "KNOWS", "carol", qualifiers={"since": 2020}, graph=TRIALS)


def _by_graph(path):
    ds = rdflib.ConjunctiveGraph()
    ds.parse(str(path), format="nquads")
    out = {}
    for s, p, o, ctx in ds.quads((None, None, None, None)):
        name = str(ctx.identifier) if ctx is not None else ""
        if name != TRIALS:
            name = ""  # rdflib's own name for the default graph
        out.setdefault(name, set()).add((s, p, o))
    return out


def test_a_named_graphs_labels_and_properties_are_in_that_graph(iris_connection, iris_master_cleanup, tmp_path):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _build(engine)
    path = tmp_path / "graphs.nq"
    engine.export_rdf(str(path))

    graphs = _by_graph(path)
    assert set(graphs) == {"", TRIALS}
    assert graphs[""] == EXPECTED_DEFAULT
    trials = graphs[TRIALS]
    assert {t for t in trials if not isinstance(t[0], BNode)} == EXPECTED_TRIALS
    assert sorted(str(p) for s, p, o in trials if isinstance(s, BNode)) == REIFIER_PREDICATES


def test_the_file_reimports_with_each_property_in_its_graph(iris_connection, iris_master_cleanup, tmp_path):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _build(engine)
    path = tmp_path / "graphs.nq"
    engine.export_rdf(str(path))

    import iris as _iris

    _iris.createIRIS(iris_connection).classMethodValue("Graph.KG.Eraser", "EraseAll")
    iris_connection.commit()
    engine.import_rdf(str(path))

    cur = iris_connection.cursor()
    try:
        cur.execute(
            f"SELECT graph_id, s, val FROM {engine._t('rdf_props')} WHERE \"key\" = 'name' AND s LIKE 'urn:ivg:%'"
        )
        names = {(g, s, v) for g, s, v in cur.fetchall()}
    finally:
        cur.close()
    assert names == {
        ("", "urn:ivg:alice", "Alice"),
        (TRIALS, "urn:ivg:bob", "Bob"),
        (TRIALS, "urn:ivg:carol", "Carol"),
    }
