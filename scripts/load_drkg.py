"""Load the DRKG (Drug Repurposing Knowledge Graph) into ivg-iris for biomed-scale
validation. 97,238 nodes (13 types) / 5,874,261 edges + 400-dim TransE embeddings.

Usage:
    python scripts/load_drkg.py [--host H] [--port P] [--edges-limit N] [--embeddings]

Source: https://github.com/gnn4dr/DRKG (Apache-2.0). Run scripts/... after the
217MB drkg.tar.gz is extracted under data/drkg/.
"""
import argparse
import time
from pathlib import Path

DRKG_DIR = Path(__file__).resolve().parent.parent / "data" / "drkg"


def _node_type(entity_id: str) -> str:
    return entity_id.split("::", 1)[0] if "::" in entity_id else "Entity"


def load_drkg(host, port, edges_limit=None, load_embeddings=False, batch=50000):
    import iris
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(host, port, "USER", "_SYSTEM", "SYS")
    engine = IRISGraphEngine(conn, embedding_dimension=400)
    engine.initialize_schema()

    entities_file = DRKG_DIR / "embed" / "entities.tsv"
    triples_file = DRKG_DIR / "drkg.tsv"
    if not triples_file.exists():
        raise SystemExit(f"DRKG not found at {triples_file}. Extract drkg.tar.gz first.")

    t0 = time.time()
    node_ids = []
    with open(entities_file) as f:
        for line in f:
            ent = line.split("\t", 1)[0]
            node_ids.append(ent)
    print(f"[{time.time()-t0:.1f}s] read {len(node_ids):,} entities")

    t1 = time.time()
    n_nodes = 0
    node_batch = []
    for ent in node_ids:
        node_batch.append({"id": ent, "labels": [_node_type(ent)]})
        if len(node_batch) >= batch:
            engine.bulk_create_nodes(node_batch)
            n_nodes += len(node_batch)
            node_batch = []
    if node_batch:
        engine.bulk_create_nodes(node_batch)
        n_nodes += len(node_batch)
    print(f"[{time.time()-t1:.1f}s] loaded {n_nodes:,} nodes")

    t2 = time.time()
    n_edges = 0
    edge_batch = []
    with engine.bulk_load_session() as session:
        with open(triples_file) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                h, r, t = parts
                edge_batch.append({"s": h, "p": r, "o": t})
                if len(edge_batch) >= batch:
                    session.add_edges(edge_batch)
                    n_edges += len(edge_batch)
                    edge_batch = []
                    if edges_limit and n_edges >= edges_limit:
                        break
        if edge_batch and not (edges_limit and n_edges >= edges_limit):
            session.add_edges(edge_batch)
            n_edges += len(edge_batch)
    print(f"[{time.time()-t2:.1f}s] loaded {n_edges:,} edges via bulk_load_session")
    print(f"  session stats: {session.stats}")

    if load_embeddings:
        import numpy as np
        t4 = time.time()
        emb = np.load(DRKG_DIR / "embed" / "DRKG_TransE_l2_entity.npy")
        n_emb = 0
        emb_batch = []
        for i, ent in enumerate(node_ids):
            emb_batch.append({"id": ent, "embedding": emb[i].tolist()})
            if len(emb_batch) >= 10000:
                engine.store_embeddings(emb_batch)
                n_emb += len(emb_batch)
                emb_batch = []
        if emb_batch:
            engine.store_embeddings(emb_batch)
            n_emb += len(emb_batch)
        print(f"[{time.time()-t4:.1f}s] loaded {n_emb:,} TransE embeddings")

    print(f"\nTOTAL: {n_nodes:,} nodes, {n_edges:,} edges in {time.time()-t0:.1f}s")
    return engine, n_nodes, n_edges


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.215.3")
    ap.add_argument("--port", type=int, default=1972)
    ap.add_argument("--edges-limit", type=int, default=None)
    ap.add_argument("--embeddings", action="store_true")
    args = ap.parse_args()
    load_drkg(args.host, args.port, args.edges_limit, args.embeddings)
