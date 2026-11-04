"""4.6.0 R122: the IRI a named graph is exported under, and the graph id an imported quad's IRI names."""

import pytest

from iris_vector_graph._engine._rdf_utils import graph_id_of_iri, graph_iri


@pytest.mark.parametrize(
    "graph_id, iri",
    [
        ("trials", "urn:ivg:graph:trials"),
        ("my trials/2", "urn:ivg:graph:my%20trials%2F2"),
        ("a<b>", "urn:ivg:graph:a%3Cb%3E"),
        ("http://example.org/trials", "http://example.org/trials"),
        ("urn:x:trials", "urn:x:trials"),
        # an id that is itself under the minted prefix is minted again, so it reads back as itself
        ("urn:ivg:graph:x", "urn:ivg:graph:urn%3Aivg%3Agraph%3Ax"),
    ],
)
def test_graph_iri_and_back(graph_id, iri):
    assert graph_iri(graph_id) == iri
    assert graph_id_of_iri(iri) == graph_id


def test_an_absolute_iri_from_elsewhere_is_its_own_graph_id():
    assert graph_id_of_iri("http://example.org/g") == "http://example.org/g"
