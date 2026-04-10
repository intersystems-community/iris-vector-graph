#!/usr/bin/env python3
"""
Canonical data load for iris-vector-graph demo/testing.

Loads:
  1. 20K NCIT OBO terms (ontology hierarchy)
  2. 10K HLA immunology graph (multi-label, multi-rel-type)
  3. 768-dim pre-computed embeddings for HLA nodes
  4. BM25 index over all node names

Usage:
    python3 scripts/load_demo_data.py

Env vars:
    IRIS_HOST (default: localhost)
    IRIS_PORT (default: 1977)
    IRIS_NAMESPACE (default: USER)
    IRIS_USERNAME (default: _SYSTEM)
    IRIS_PASSWORD (default: SYS)
    NCIT_OBO (default: ../iris-vector-graph-private/examples/NCIT.obo)
    HLA_GRAPHML (default: ../iris-vector-graph-private/examples/expanded_mindwalk_KG_10000.graphml)
    HLA_VECTORS (default: ../iris-vector-graph-private/examples/expanded_mindwalk_KG_10000.vectors.npy)
    HLA_VECTOR_IDS (default: ../iris-vector-graph-private/examples/expanded_mindwalk_KG_10000.vectors.ids.txt)
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import iris
import networkx as nx
import numpy as np
import obonet

from iris_vector_graph.engine import IRISGraphEngine

PRIVATE = Path(__file__).resolve().parents[1] / ".." / "iris-vector-graph-private" / "examples"

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1977"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")

NCIT_OBO = Path(os.environ.get("NCIT_OBO", str(PRIVATE / "NCIT.obo")))
HLA_GRAPHML = Path(os.environ.get("HLA_GRAPHML", str(PRIVATE / "expanded_mindwalk_KG_10000.graphml")))
HLA_VECTORS = Path(os.environ.get("HLA_VECTORS", str(PRIVATE / "expanded_mindwalk_KG_10000.vectors.npy")))
HLA_VECTOR_IDS = Path(os.environ.get("HLA_VECTOR_IDS", str(PRIVATE / "expanded_mindwalk_KG_10000.vectors.ids.txt")))

NCIT_MAX_TERMS = 20_000


def main():
    conn = iris.connect(hostname=IRIS_HOST, port=IRIS_PORT, namespace=IRIS_NS,
                        username=IRIS_USER, password=IRIS_PASS)
    engine = IRISGraphEngine(conn, embedding_dimension=768)
    engine.initialize_schema()

    t_total = time.time()

    # ── 1. NCIT OBO ──────────────────────────────────────────────────────────
    if NCIT_OBO.exists():
        print(f"\n[1/4] Loading NCIT OBO ({NCIT_MAX_TERMS} terms)...")
        t0 = time.time()
        with open(NCIT_OBO, encoding="utf-8", errors="replace") as f:
            lines = []
            term_count = 0
            last_term_start = 0
            for line in f:
                if line.strip() == "[Term]":
                    term_count += 1
                    if term_count > NCIT_MAX_TERMS:
                        break
                    last_term_start = len(lines)
                lines.append(line)
        lines = lines[:last_term_start]

        G = obonet.read_obo(io.StringIO("".join(lines)))
        stats = engine.load_networkx(G, label_attr="namespace")
        print(f"      {stats} in {time.time()-t0:.0f}s")
    else:
        print(f"[1/4] SKIP — {NCIT_OBO} not found")

    # ── 2. HLA immunology graph ──────────────────────────────────────────────
    if HLA_GRAPHML.exists():
        print(f"\n[2/4] Loading HLA immunology graph...")
        t0 = time.time()
        G = nx.read_graphml(str(HLA_GRAPHML))
        stats = engine.load_networkx(G, label_attr="type")
        print(f"      {stats} in {time.time()-t0:.0f}s")
    else:
        print(f"[2/4] SKIP — {HLA_GRAPHML} not found")

    # ── 3. Pre-computed 768-dim embeddings ───────────────────────────────────
    if HLA_VECTORS.exists() and HLA_VECTOR_IDS.exists():
        print(f"\n[3/4] Loading 768-dim embeddings...")
        t0 = time.time()
        vecs = np.load(str(HLA_VECTORS)).astype(np.float64)
        with open(HLA_VECTOR_IDS) as f:
            ids = [l.strip() for l in f if l.strip()]

        BATCH = 200
        loaded = 0
        for i in range(0, len(ids), BATCH):
            batch = [
                {"node_id": nid, "embedding": vec.tolist()}
                for nid, vec in zip(ids[i:i+BATCH], vecs[i:i+BATCH])
            ]
            engine.store_embeddings(batch)
            loaded += len(batch)
            if loaded % 2000 == 0:
                print(f"      {loaded}/{len(ids)}")

        print(f"      {loaded} embeddings in {time.time()-t0:.0f}s")
    else:
        print(f"[3/4] SKIP — vectors not found")

    # ── 4. BM25 index ────────────────────────────────────────────────────────
    print(f"\n[4/4] Building BM25 index...")
    t0 = time.time()
    result = engine.bm25_build("default", text_props=["name", "id"])
    print(f"      {result} in {time.time()-t0:.0f}s")

    # ── Summary ──────────────────────────────────────────────────────────────
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
    nodes = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
    edges = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
    embs = cursor.fetchone()[0]
    cursor.execute("SELECT label, COUNT(*) c FROM Graph_KG.rdf_labels GROUP BY label ORDER BY c DESC")
    labels = cursor.fetchall()
    cursor.execute("SELECT DISTINCT TOP 20 p FROM Graph_KG.rdf_edges ORDER BY p")
    rels = [r[0] for r in cursor.fetchall()]

    print(f"\n{'='*60}")
    print(f"Total: {nodes:,} nodes, {edges:,} edges, {embs:,} embeddings")
    print(f"Labels: {', '.join(f'{r[0]}({r[1]})' for r in labels)}")
    print(f"Rel types ({len(rels)}): {', '.join(rels[:10])}{'...' if len(rels)>10 else ''}")
    print(f"Loaded in {time.time()-t_total:.0f}s total")
    print(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()
