"""4.6.0 R099: `export_rdf(label_filter=...)` exports only the filtered nodes' properties.

`_build_rdflib_graph` narrowed `rdf_labels` and `rdf_edges` to the labels asked for and read `rdf_props` with no label
condition, so every node's literal triples went into the file.

The graph, worked by hand (default graph; `create_node` also stores an `id` property equal to the node ID):
    alice:Person {name: Alice} -WORKS_AT-> acme:Company {name: Acme}
    bob:Person {name: Bob}
    acme -OWNS-> alice
A `Person` export is alice's and bob's type and properties and the edge alice starts: acme is not a Person, so neither
its properties nor its OWNS edge are in it. Parsing the file back (rdflib) gives exactly that.
"""

from __future__ import annotations

import pytest
import rdflib  # the dev extra's

pytestmark = [pytest.mark.e2e]


def _expected():
    from rdflib import Literal, URIRef
    from rdflib.namespace import RDF

    def u(x):
        return URIRef(f"urn:ivg:{x}")

    def prop(k):
        return URIRef(f"urn:ivg:prop/{k}")

    return {
        (u("alice"), RDF.type, u("Person")),
        (u("alice"), prop("name"), Literal("Alice")),
        (u("alice"), prop("id"), Literal("alice")),
        (u("alice"), u("WORKS_AT"), u("acme")),
        (u("bob"), RDF.type, u("Person")),
        (u("bob"), prop("name"), Literal("Bob")),
        (u("bob"), prop("id"), Literal("bob")),
    }


def test_a_label_filtered_export_holds_only_those_nodes_properties(iris_connection, iris_master_cleanup, tmp_path):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    engine.create_node("alice", labels=["Person"], properties={"name": "Alice"})
    engine.create_node("bob", labels=["Person"], properties={"name": "Bob"})
    engine.create_node("acme", labels=["Company"], properties={"name": "Acme"})
    engine.create_edge("alice", "WORKS_AT", "acme")
    engine.create_edge("acme", "OWNS", "alice")

    path = tmp_path / "people.nt"
    engine.export_rdf(str(path), label_filter=["Person"])

    back = rdflib.Graph()
    back.parse(str(path), format="nt")
    assert set(back) == _expected()
