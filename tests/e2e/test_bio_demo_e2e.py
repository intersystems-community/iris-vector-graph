"""The biomedical demo against its seeded graph (`bio:demo` in IVGFHIR).

Drives the demo client the way the page's three scenarios do: TP53 by name, a
kinase-inhibitor search by annotation, and the glycolysis path from GAPDH to LDHA.
Every answer is checked against the seed in `bio_demo_data`, not against itself.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("fasthtml")

from iris_demo_server.models.biomedical import PathwayQuery, ProteinSearchQuery  # noqa: E402
from iris_demo_server.services import bio_demo_data as bio  # noqa: E402

pytestmark = [pytest.mark.e2e]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def client(fhir_conn, fhir_engine):
    from iris_demo_server.services.iris_biomedical_client import IRISBiomedicalClient

    bio.seed(fhir_conn, fhir_engine, log=lambda _: None)
    return IRISBiomedicalClient(conn=fhir_conn)


def _neighbours(sym):
    out = set()
    for a, b, _, _ in bio.INTERACTIONS:
        if a == sym:
            out.add(b)
        elif b == sym:
            out.add(a)
    return out


def test_stats_count_the_seed(client):
    s = client.stats()
    assert (s["proteins"], s["interactions"]) == (len(bio.PROTEINS), len(bio.INTERACTIONS))


def test_name_search_finds_tp53_first(client):
    r = run(client.search_proteins(ProteinSearchQuery(query_text="TP53", query_type="name")))
    assert r.proteins[0].protein_id == "TP53"
    assert r.similarity_scores[0] == 1.0
    assert r.similarity_scores == sorted(r.similarity_scores, reverse=True)


def test_name_search_is_case_insensitive(client):
    r = run(client.search_proteins(ProteinSearchQuery(query_text="gapdh", query_type="name")))
    assert [p.protein_id for p in r.proteins] == ["GAPDH"]


def test_function_search_matches_every_annotated_inhibitor_target(client):
    r = run(client.search_proteins(
        ProteinSearchQuery(query_text="kinase inhibitor", query_type="function", top_k=50)))
    both = {p["symbol"] for p in bio.PROTEINS
            if "kinase" in p["annotation"].lower() and "inhibitor" in p["annotation"].lower()}
    full = {p.protein_id for p, s in zip(r.proteins, r.similarity_scores) if s == 1.0}
    assert full == both
    assert all(0.0 < s <= 1.0 for s in r.similarity_scores)


def test_search_with_no_match_is_empty(client):
    r = run(client.search_proteins(ProteinSearchQuery(query_text="zzzz-nothing", query_type="name")))
    assert r.proteins == [] and r.similarity_scores == []


def test_network_is_the_seeded_neighbourhood(client):
    net = run(client.get_interaction_network("TP53", expand_depth=1))
    ids = {n.protein_id for n in net.nodes}
    assert ids == {"TP53"} | _neighbours("TP53")
    for e in net.edges:
        assert "TP53" in (e.source_protein_id, e.target_protein_id)
    assert len(net.edges) == len(_neighbours("TP53"))


def test_network_depth_two_reaches_further(client):
    one = run(client.get_interaction_network("GAPDH", expand_depth=1))
    two = run(client.get_interaction_network("GAPDH", expand_depth=2))
    assert {n.protein_id for n in one.nodes} == {"GAPDH", "PGK1"}
    assert {n.protein_id for n in two.nodes} == {"GAPDH", "PGK1", "PGAM1"}


def test_unknown_protein_is_an_error(client):
    with pytest.raises(RuntimeError, match="not found"):
        run(client.get_interaction_network("NOPE1", expand_depth=1))


def test_pathway_walks_glycolysis(client):
    s = bio.SCENARIOS["metabolic_pathway"]
    p = run(client.find_pathway(PathwayQuery(**s)))
    assert p.path == ["GAPDH", "PGK1", "PGAM1", "ENO1", "PKM", "LDHA"]
    assert [x.protein_id for x in p.intermediate_proteins] == p.path
    assert len(p.path_interactions) == len(p.path) - 1
    assert {i.interaction_type for i in p.path_interactions} == {"pathway"}
    assert p.confidence == pytest.approx(0.9)


def test_pathway_beyond_the_hop_limit_is_an_error(client):
    with pytest.raises(RuntimeError, match="No path"):
        run(client.find_pathway(PathwayQuery(source_protein_id="GAPDH", target_protein_id="LDHA",
                                             max_hops=3)))


def test_the_client_only_reads_its_graph(client, fhir_engine):
    """A same-named node in another graph must not leak into the demo."""
    other = "bio:leak-probe"
    fhir_engine.erase_graph(other)
    try:
        fhir_engine.create_node("protein:TP53", labels=["Protein"],
                                properties={"symbol": "TP53", "name": "LEAK"}, graph=other)
        fhir_engine.create_node("protein:ZZZ9", labels=["Protein"],
                                properties={"symbol": "ZZZ9", "name": "LEAK"}, graph=other)
        fhir_engine.create_edge("protein:TP53", bio.PREDICATE, "protein:ZZZ9", graph=other)
        net = run(client.get_interaction_network("TP53", expand_depth=1))
        assert "ZZZ9" not in {n.protein_id for n in net.nodes}
        assert all(n.name != "LEAK" for n in net.nodes)
    finally:
        fhir_engine.erase_graph(other)
