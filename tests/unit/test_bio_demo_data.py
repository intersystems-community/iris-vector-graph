"""The biomedical demo's seed graph (`bio:demo`), checked without a database.

The page's three scenarios must be answerable from the seed: TP53 by name, a kinase
search by function, and a path from GAPDH to LDHA within the scenario's hop limit.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from iris_demo_server.services import bio_demo_data as bio  # noqa: E402


def _adjacency():
    adj = {}
    for a, b, _kind, _conf in bio.INTERACTIONS:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _hops(src, dst):
    adj, seen, q = _adjacency(), {src}, deque([(src, 0)])
    while q:
        cur, d = q.popleft()
        if cur == dst:
            return d
        for n in adj.get(cur, ()):
            if n not in seen:
                seen.add(n)
                q.append((n, d + 1))
    return None


def test_symbols_are_unique_and_every_protein_is_described():
    symbols = [p["symbol"] for p in bio.PROTEINS]
    assert len(symbols) == len(set(symbols))
    for p in bio.PROTEINS:
        assert p["name"] and p["annotation"]


def test_every_interaction_joins_two_seeded_proteins():
    symbols = {p["symbol"] for p in bio.PROTEINS}
    for a, b, kind, conf in bio.INTERACTIONS:
        assert a in symbols and b in symbols and a != b
        assert kind in bio.INTERACTION_TYPES
        assert 0.0 < conf <= 1.0


def test_no_interaction_is_listed_twice_in_either_direction():
    pairs = [frozenset((a, b)) for a, b, _, _ in bio.INTERACTIONS]
    assert len(pairs) == len(set(pairs))


def test_the_pathway_scenario_is_reachable_within_its_hop_limit():
    s = bio.SCENARIOS["metabolic_pathway"]
    d = _hops(s["source_protein_id"], s["target_protein_id"])
    assert d is not None and 2 <= d <= s["max_hops"]


def test_the_name_scenario_finds_tp53():
    assert "TP53" in {p["symbol"] for p in bio.PROTEINS}
    assert bio.SCENARIOS["cancer_protein"]["query_text"] == "TP53"


def test_the_function_scenario_matches_several_kinases():
    terms = bio.SCENARIOS["drug_target"]["query_text"].lower().split()
    hits = [p for p in bio.PROTEINS if all(t in p["annotation"].lower() for t in terms)]
    assert len(hits) >= 3


def test_node_ids_are_graph_local_and_prefixed():
    nodes, edges = bio.build_graph()
    assert all(n["id"].startswith("protein:") for n in nodes)
    assert {e["source"] for e in edges} | {e["target"] for e in edges} <= {n["id"] for n in nodes}
    assert len(edges) == len(bio.INTERACTIONS)


def test_protein_id_round_trips():
    assert bio.node_id("TP53") == "protein:TP53"
    assert bio.symbol("protein:TP53") == "TP53"
    assert bio.symbol("TP53") == "TP53"


def test_connection_defaults_to_this_projects_container(monkeypatch):
    for k in ("IVG_BIO_HOST", "IVG_BIO_PORT", "IVG_BIO_NAMESPACE", "IVG_FHIR_PORT", "IVG_FHIR_NAMESPACE"):
        monkeypatch.delenv(k, raising=False)
    s = bio.connection_settings()
    assert s["port"] == 31972
    assert s["namespace"] == "IVGFHIR"


def test_the_bio_namespace_can_be_named(monkeypatch):
    monkeypatch.setenv("IVG_BIO_NAMESPACE", "USER")
    monkeypatch.setenv("IVG_BIO_PORT", "41000")
    s = bio.connection_settings()
    assert (s["namespace"], s["port"]) == ("USER", 41000)
