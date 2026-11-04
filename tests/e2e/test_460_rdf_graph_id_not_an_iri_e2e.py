"""4.6.0 R122: a graph whose id is not an absolute IRI exports under a minted IRI and imports back under its id.

`export_rdf` named a graph's context with the graph id as written, so graph `trials` was written `<trials>`, a
relative IRI N-Quads parsers (rdflib's included) refuse. Such an id is now written `urn:ivg:graph:<id>` (percent-encoded),
and `import_rdf` reads that IRI back as the id. An id that is already an absolute IRI is written as it is.

The graph, worked by hand: graph `trials` holds bob:Person {name: Bob} -KNOWS-> carol:Person {name: Carol};
graph `my trials/2` holds dave:Person {name: Dave}; the default graph holds alice:Person {name: Alice}.
"""

from __future__ import annotations

import pytest
import rdflib  # the dev extra's
from rdflib import URIRef
from rdflib.namespace import RDF

pytestmark = [pytest.mark.e2e]


def _build(engine):
    engine.create_node("alice", labels=["Person"], properties={"name": "Alice"})
    engine.create_node("bob", labels=["Person"], properties={"name": "Bob"}, graph="trials")
    engine.create_node("carol", labels=["Person"], properties={"name": "Carol"}, graph="trials")
    engine.create_edge("bob", "KNOWS", "carol", graph="trials")
    engine.create_node("dave", labels=["Person"], properties={"name": "Dave"}, graph="my trials/2")


@pytest.fixture
def exported(iris_connection, iris_master_cleanup, tmp_path):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _build(engine)
    path = tmp_path / "graphs.nq"
    engine.export_rdf(str(path))
    return engine, path


def test_the_export_parses_and_names_each_graph(exported):
    _engine, path = exported
    ds = rdflib.ConjunctiveGraph()
    ds.parse(str(path), format="nquads")
    types = {
        (str(s), str(ctx.identifier))
        for s, p, o, ctx in ds.quads((None, RDF.type, None, None))
    }
    default = {name for s, name in types if s == "urn:ivg:alice"}
    assert len(default) == 1 and not next(iter(default)).startswith("urn:ivg:graph:")
    assert {(s, name) for s, name in types if s != "urn:ivg:alice"} == {
        ("urn:ivg:bob", "urn:ivg:graph:trials"),
        ("urn:ivg:carol", "urn:ivg:graph:trials"),
        ("urn:ivg:dave", "urn:ivg:graph:my%20trials%2F2"),
    }
    assert (URIRef("urn:ivg:bob"), URIRef("urn:ivg:KNOWS"), URIRef("urn:ivg:carol")) in ds.get_context(
        URIRef("urn:ivg:graph:trials")
    )


def test_the_round_trip_restores_the_graph_ids(exported, iris_connection):
    engine, path = exported
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
        cur.execute(f"SELECT graph_id, s, p, o_id FROM {engine._t('rdf_edges')} WHERE p = 'urn:ivg:KNOWS'")
        edges = {tuple(r) for r in cur.fetchall()}
    finally:
        cur.close()
    assert names == {
        ("", "urn:ivg:alice", "Alice"),
        ("trials", "urn:ivg:bob", "Bob"),
        ("trials", "urn:ivg:carol", "Carol"),
        ("my trials/2", "urn:ivg:dave", "Dave"),
    }
    assert edges == {("trials", "urn:ivg:bob", "urn:ivg:KNOWS", "urn:ivg:carol")}
