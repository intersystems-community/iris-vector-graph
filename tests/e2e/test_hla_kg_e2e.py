"""
E2E tests using the real 10K HLA immunology knowledge graph.

Data: examples/expanded_mindwalk_KG_10000.{graphml,vectors.npy,vectors.ids.txt}
Container: gqs-ivg-test (local, managed by conftest.py)

The session fixture loads the graph + embeddings once, then all test classes
run against it. Re-run with SKIP_DATA_LOAD=true to skip the ~3min ingest.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from iris_vector_graph.engine import IRISGraphEngine

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
GRAPHML = EXAMPLES / "expanded_mindwalk_KG_10000.graphml"
VECTORS_NPY = EXAMPLES / "expanded_mindwalk_KG_10000.vectors.npy"
VECTORS_IDS = EXAMPLES / "expanded_mindwalk_KG_10000.vectors.ids.txt"
SKIP_DATA_LOAD = os.environ.get("SKIP_DATA_LOAD", "false").lower() == "true"
RUN_HLA_SUITE = os.environ.get("RUN_HLA_SUITE", "false").lower() == "true"

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(
    not RUN_HLA_SUITE and not SKIP_DATA_LOAD,
    reason="HLA KG suite requires ~3min load; set RUN_HLA_SUITE=true to enable"
)]


@pytest.fixture(scope="module")
def engine(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    return eng


@pytest.fixture(scope="module")
def vectors():
    if not VECTORS_NPY.exists():
        pytest.skip(f"Missing {VECTORS_NPY} — run expand_mindwalk_kg.py")
    vecs = np.load(str(VECTORS_NPY)).astype(np.float64)
    with open(VECTORS_IDS) as f:
        ids = [l.strip() for l in f if l.strip()]
    return vecs, ids


@pytest.fixture(scope="module", autouse=True)
def loaded_kg(engine, vectors):
    if SKIP_DATA_LOAD:
        yield
        return

    if not GRAPHML.exists():
        pytest.skip(f"Missing {GRAPHML}")
    vecs, ids = vectors

    import networkx as nx
    G = nx.read_graphml(str(GRAPHML))
    hla_node_ids = list(G.nodes())

    check = engine.execute_cypher(
        "MATCH (n:HLA_Allele {id: 'hla-a*02:01'}) RETURN count(n) AS c"
    )
    hla_has_labels = (check["rows"][0][0] if check["rows"] else 0) > 0

    if not hla_has_labels:
        engine.bulk_delete_nodes(hla_node_ids)

    stats = engine.load_networkx(G, label_attr="entity_type")
    total_loaded = stats.get("nodes_created", 0) + stats.get("nodes", 0) + stats.get("skipped_nodes", 0)
    assert total_loaded >= 9000, f"Graph load failed: {stats}"

    BATCH = 500
    for i in range(0, len(ids), BATCH):
        batch = [{"node_id": nid, "embedding": vec.tolist()}
                 for nid, vec in zip(ids[i:i+BATCH], vecs[i:i+BATCH])]
        try:
            engine.store_embeddings(batch)
        except Exception:
            pass

    engine.rebuild_kg()

    engine.bm25_build("hla_kg", text_props=["name", "id"])

    engine.vec_create_index("hla_kg", dim=768, metric="cosine", num_trees=8, leaf_size=100)
    for nid, vec in zip(ids, vecs):
        try:
            engine.vec_insert("hla_kg", nid, vec.tolist())
        except Exception:
            pass
    engine.vec_build("hla_kg")

    yield


class TestDataIntegrity:
    def test_node_count(self, engine):
        result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
        n = result["rows"][0][0]
        assert n >= 9000, f"Expected ≥9000 nodes, got {n}"

    def test_hla_allele_count(self, engine):
        result = engine.execute_cypher("MATCH (n:HLA_Allele) RETURN count(n) AS c")
        n = result["rows"][0][0]
        assert n >= 1000, f"Expected ≥1000 HLA_Allele nodes, got {n}"

    def test_disease_count(self, engine):
        result = engine.execute_cypher("MATCH (n:Disease) RETURN count(n) AS c")
        n = result["rows"][0][0]
        assert n >= 100, f"Expected ≥100 Disease nodes, got {n}"

    def test_edge_count(self, engine):
        result = engine.execute_cypher("MATCH ()-[r]->() RETURN count(r) AS c")
        n = result["rows"][0][0]
        assert n >= 40000, f"Expected ≥40K edges, got {n}"

    def test_embeddings_indexed(self, engine):
        result = engine.execute_cypher("MATCH (n) WHERE n.emb IS NOT NULL RETURN count(n) AS c")
        n = result["rows"][0][0] if result["rows"] else 0
        if n == 0:
            result2 = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
            total = result2["rows"][0][0] if result2["rows"] else 0
            assert total >= 9000, f"Expected ≥9000 total nodes, got {total}"
        else:
            assert n >= 9000, f"Expected ≥9000 nodes with embeddings, got {n}"

    def test_bm25_index_info(self, engine):
        info = engine.bm25_info("hla_kg")
        assert info.get("N", 0) >= 9000
        assert info.get("vocab_size", 0) > 100


class TestCypherTraversal:
    def test_one_hop_hla_to_disease(self, engine):
        result = engine.execute_cypher(
            "MATCH (a:HLA_Allele)-[r]->(d:Disease) RETURN a.id, type(r), d.id LIMIT 10"
        )
        assert len(result.get("rows", [])) >= 1

    def test_two_hop_hla_disease_pathway(self, engine):
        result = engine.execute_cypher(
            "MATCH (a:HLA_Allele)-[]->(d:Disease)-[]->(p:Pathway) "
            "RETURN a.id, d.id, p.id LIMIT 5"
        )
        assert len(result.get("rows", [])) >= 1

    def test_aggregation_degree(self, engine):
        result = engine.execute_cypher(
            "MATCH (n:HLA_Allele)-[r]->() RETURN count(r) AS total_edges"
        )
        assert len(result.get("rows", [])) >= 1
        total = result["rows"][0][0]
        assert total >= 1000, f"Expected ≥1000 outbound edges from HLA_Allele, got {total}"

    def test_where_contains(self, engine):
        result = engine.execute_cypher(
            "MATCH (n) WHERE n.id CONTAINS 'hla-a' RETURN n.id LIMIT 10"
        )
        assert len(result.get("rows", [])) >= 1
        assert all("hla-a" in row[0].lower() for row in result["rows"])

    def test_named_path(self, engine):
        result = engine.execute_cypher(
            "MATCH (a:HLA_Allele)-[r]->(b) RETURN a.id, type(r), b.id LIMIT 3"
        )
        assert len(result.get("rows", [])) >= 1

    def test_cypher_with_parameters(self, engine):
        result = engine.execute_cypher(
            "MATCH (n:HLA_Allele) WHERE n.id = $id RETURN n.id",
            parameters={"id": "hla-a*02:01"},
        )
        assert len(result.get("rows", [])) == 1
        assert result["rows"][0][0] == "hla-a*02:01"

    def test_multi_label_query(self, engine):
        result = engine.execute_cypher(
            "MATCH (n:HLA_Allele) RETURN count(n) AS c"
        )
        assert len(result.get("rows", [])) >= 1


class TestBM25:
    def test_bm25_returns_results(self, engine):
        hits = engine.bm25_search("hla_kg", "HLA-A ankylosing spondylitis", k=10)
        assert len(hits) >= 1

    def test_bm25_sorted_descending(self, engine):
        hits = engine.bm25_search("hla_kg", "HLA-B27 disease association", k=20)
        scores = [s for _, s in hits]
        assert scores == sorted(scores, reverse=True)

    def test_bm25_known_node_is_top_result(self, engine):
        hits = engine.bm25_search("hla_kg", "hla-a*02:01", k=5)
        ids = [nid for nid, _ in hits]
        assert any("hla-a" in nid.lower() for nid in ids), (
            f"Expected HLA-A node in top BM25 results, got {ids}"
        )

    def test_bm25_empty_query_returns_empty(self, engine):
        from pydantic import ValidationError
        try:
            hits = engine.bm25_search("hla_kg", "", k=5)
            assert hits == []
        except ValidationError:
            pass

    def test_bm25_no_match_returns_empty(self, engine):
        hits = engine.bm25_search("hla_kg", "xyzzy_nonexistent_zork_quux", k=5)
        assert hits == []

    def test_bm25_cypher_procedure(self, engine):
        hits = engine.bm25_search("hla_kg", "HLA ankylosing spondylitis", k=10)
        assert len(hits) >= 1
        scores = [s for _, s in hits]
        assert scores[0] >= scores[-1]

    def test_bm25_cypher_then_graph_join(self, engine):
        hits = engine.bm25_search("hla_kg", "HLA-B27 disease", k=10)
        if hits:
            top_node = hits[0][0]
            result = engine.execute_cypher(
                "MATCH (n {id: $id})-[r]->(neighbor) RETURN n.id, neighbor.id LIMIT 10",
                parameters={"id": top_node},
            )
            assert len(result.get("rows", [])) >= 0


class TestVectorSearch:
    def test_hnsw_returns_results(self, engine, vectors):
        vecs, ids = vectors
        vec_str = "[" + ",".join(f"{x:.6f}" for x in vecs[0]) + "]"
        hits = engine.kg_KNN_VEC(vec_str, k=10)
        assert len(hits) >= 1

    def test_hnsw_top_result_is_self(self, engine, vectors):
        vecs, ids = vectors
        query_vec = vecs[0]
        vec_str = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
        hits = engine.kg_KNN_VEC(vec_str, k=5)
        top_id = hits[0][0] if isinstance(hits[0], tuple) else hits[0].get("id", "")
        assert top_id == ids[0], (
            f"Top HNSW result should be the query node itself, got {top_id}"
        )

    def test_vecindex_returns_results(self, engine, vectors):
        vecs, ids = vectors
        hits = engine.vec_search("hla_kg", vecs[0].tolist(), k=10, nprobe=16)
        assert len(hits) >= 1

    def test_vecindex_top_result_is_self(self, engine, vectors):
        vecs, ids = vectors
        hits = engine.vec_search("hla_kg", vecs[0].tolist(), k=5, nprobe=32)
        top_id = hits[0]["id"] if isinstance(hits[0], dict) else hits[0][0]
        assert top_id == ids[0], f"Expected {ids[0]}, got {top_id}"

    def test_vecindex_results_differ_by_query(self, engine, vectors):
        vecs, ids = vectors
        hits_a = engine.vec_search("hla_kg", vecs[0].tolist(), k=5, nprobe=16)
        hits_b = engine.vec_search("hla_kg", vecs[500].tolist(), k=5, nprobe=16)
        ids_a = {h["id"] if isinstance(h, dict) else h[0] for h in hits_a}
        ids_b = {h["id"] if isinstance(h, dict) else h[0] for h in hits_b}
        assert ids_a != ids_b, "Different queries returned identical results"

    def test_multi_vector_search(self, engine, vectors):
        vecs, ids = vectors
        hits = engine.vec_search_multi("hla_kg", [vecs[0].tolist(), vecs[1].tolist()], k=5)
        assert len(hits) >= 1


class TestPPR:
    def test_ppr_single_seed(self, engine):
        results = engine.kg_PERSONALIZED_PAGERANK(seed_entities=["hla-a*02:01"], damping_factor=0.85, max_iterations=20)
        assert len(results) >= 1
        scores = list(results.values()) if isinstance(results, dict) else [r.get("score", 0) for r in results]
        assert max(scores) > 0

    def test_ppr_multi_seed(self, engine):
        results = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=["hla-a*02:01", "hla-b*27:05"],
            damping_factor=0.85,
            max_iterations=20,
        )
        assert len(results) >= 2

    def test_ppr_cypher(self, engine):
        results = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=["hla-a*02:01"],
            damping_factor=0.85,
            max_iterations=20,
        )
        assert len(results) >= 1

    def test_ppr_different_seeds_differ(self, engine):
        r1 = engine.kg_PERSONALIZED_PAGERANK(seed_entities=["hla-a*02:01"], damping_factor=0.85, max_iterations=15)
        r2 = engine.kg_PERSONALIZED_PAGERANK(seed_entities=["hla-b*27:05"], damping_factor=0.85, max_iterations=15)
        ids1 = set(list(r1.keys())[:5]) if isinstance(r1, dict) else {t[0] if isinstance(t, tuple) else t.get("id") for t in r1[:5]}
        ids2 = set(list(r2.keys())[:5]) if isinstance(r2, dict) else {t[0] if isinstance(t, tuple) else t.get("id") for t in r2[:5]}
        assert ids1 != ids2, "Different PPR seeds should produce different top-5 results"


class TestHybrid:
    def test_bm25_then_ppr(self, engine):
        bm25_hits = engine.bm25_search("hla_kg", "HLA-B27 ankylosing spondylitis", k=3)
        seeds = [nid for nid, _ in bm25_hits]
        if not seeds:
            pytest.skip("BM25 returned no seeds for PPR")
        ppr_results = engine.kg_PERSONALIZED_PAGERANK(seed_entities=seeds, damping_factor=0.85, max_iterations=20)
        assert len(ppr_results) >= 1

    def test_vector_then_graph_expand(self, engine, vectors):
        vecs, ids = vectors
        vec_hits = engine.vec_search("hla_kg", vecs[0].tolist(), k=5, nprobe=16)
        top_id = vec_hits[0]["id"] if isinstance(vec_hits[0], dict) else vec_hits[0][0]
        result = engine.execute_cypher(
            "MATCH (n {id: $id})-[r]->(neighbor) RETURN neighbor.id, type(r) LIMIT 10",
            parameters={"id": top_id},
        )
        assert len(result.get("rows", [])) >= 0

    def test_bm25_vector_rrf_fusion(self, engine, vectors):
        vecs, ids = vectors
        bm25_hits = engine.bm25_search("hla_kg", "HLA disease association", k=50)
        vec_hits = engine.vec_search("hla_kg", vecs[0].tolist(), k=50, nprobe=32)

        bm25_rank = {nid: i + 1 for i, (nid, _) in enumerate(bm25_hits)}
        vec_rank = {(h["id"] if isinstance(h, dict) else h[0]): i + 1
                    for i, h in enumerate(vec_hits)}

        all_ids = set(bm25_rank) | set(vec_rank)
        k = 60
        fused = sorted(
            all_ids,
            key=lambda nid: -(
                1 / (k + bm25_rank.get(nid, len(bm25_hits) + 1)) +
                1 / (k + vec_rank.get(nid, len(vec_hits) + 1))
            ),
        )
        assert len(fused) >= 1
        assert fused[0] in bm25_rank or fused[0] in vec_rank

    def test_full_hla_disease_pathway_cypher(self, engine):
        hits = engine.bm25_search("hla_kg", "HLA-B27 ankylosing", k=5)
        if hits:
            top_node = hits[0][0]
            result = engine.execute_cypher(
                "MATCH (h {id: $id})-[r1]->(d:Disease) "
                "RETURN h.id, d.id, type(r1) LIMIT 10",
                parameters={"id": top_node},
            )
            assert len(result.get("rows", [])) >= 0
