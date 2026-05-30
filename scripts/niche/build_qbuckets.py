"""
Phase 0: NICHE-Quantized Embeddings — IVF k-means bucket index build

Spec 168-niche-quantized-embeddings, Phase 0 gate:
  - Bucket fill < 5% imbalance (largest bucket < 5% of nodes)
  - Build time < 60s
  - Recall@10 >= 0.85 (Phase 0 measurement, NFR-168-002 is >= 0.90 for Phase 1)

Usage:
  python3 scripts/niche/build_qbuckets.py [--n-clusters 512] [--port 25972]
"""
from __future__ import annotations

import argparse
import time
import sys
import statistics
from pathlib import Path

import numpy as np
import iris


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=25972)
    p.add_argument("--host", default="localhost")
    p.add_argument("--namespace", default="USER")
    p.add_argument("--username", default="_SYSTEM")
    p.add_argument("--password", default="SYS")
    p.add_argument("--n-clusters", type=int, default=512)
    p.add_argument("--batch-size", type=int, default=4096)
    p.add_argument("--random-state", type=int, default=42)
    p.add_argument("--dry-run", action="store_true", help="Train but don't write to ^NKG")
    return p.parse_args()


def load_embeddings(conn, port):
    """Load all embeddings from Graph_KG.kg_NodeEmbeddings.
    
    Returns:
        node_ids: list of string node IDs
        embeddings: numpy array (N, D)
    """
    print("Loading embeddings from Graph_KG.kg_NodeEmbeddings...", flush=True)
    t0 = time.perf_counter()
    cursor = conn.cursor()
    cursor.execute("SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings ORDER BY id")
    rows = cursor.fetchall()
    elapsed = time.perf_counter() - t0
    print(f"  Fetched {len(rows)} rows in {elapsed:.1f}s", flush=True)

    node_ids = []
    vecs = []
    for row in rows:
        row_list = list(row)
        nid = row_list[0]
        emb_str = row_list[1]
        floats = [float(x) for x in emb_str.split(",") if x.strip()]
        node_ids.append(nid)
        vecs.append(floats)

    embeddings = np.array(vecs, dtype=np.float32)
    # L2-normalize for cosine (matches IRIS VECTOR_COSINE semantics)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embeddings /= norms

    print(f"  {len(node_ids)} nodes, dim={embeddings.shape[1]}", flush=True)
    return node_ids, embeddings


def train_kmeans(embeddings: np.ndarray, n_clusters: int, batch_size: int, random_state: int):
    """Train MiniBatchKMeans and return cluster assignments."""
    from sklearn.cluster import MiniBatchKMeans

    print(f"\nTraining MiniBatchKMeans K={n_clusters} (batch_size={batch_size}, seed={random_state})...", flush=True)
    t0 = time.perf_counter()
    km = MiniBatchKMeans(
        n_clusters=n_clusters,
        batch_size=batch_size,
        random_state=random_state,
        n_init=3,
        max_iter=100,
        verbose=0,
    )
    labels = km.fit_predict(embeddings)
    elapsed = time.perf_counter() - t0
    print(f"  Trained in {elapsed:.1f}s", flush=True)
    return km, labels, elapsed


def check_bucket_fill(labels: np.ndarray, n_clusters: int, n_nodes: int) -> dict:
    """Check bucket fill distribution for Phase 0 gate."""
    counts = np.bincount(labels, minlength=n_clusters)
    max_fill = counts.max() / n_nodes
    min_fill = counts[counts > 0].min() / n_nodes
    mean_fill = counts.mean() / n_nodes
    empty = int((counts == 0).sum())
    print(f"\nBucket fill stats (K={n_clusters}, N={n_nodes}):")
    print(f"  max fill: {max_fill*100:.2f}% (gate: < 5%)")
    print(f"  min fill: {min_fill*100:.4f}%")
    print(f"  mean fill: {mean_fill*100:.4f}%")
    print(f"  empty buckets: {empty}")
    return {
        "max_fill": float(max_fill),
        "min_fill": float(min_fill),
        "mean_fill": float(mean_fill),
        "empty_buckets": int(empty),
        "gate_pass": max_fill < 0.05,
    }


def measure_recall(embeddings: np.ndarray, node_ids: list, labels: np.ndarray,
                   km, n_queries: int = 200, top_k: int = 10, max_buckets: int = 8) -> float:
    """Measure Recall@10 of bucket-filtered search vs exact full-precision search."""
    rng = np.random.default_rng(42)
    query_indices = rng.choice(len(node_ids), size=n_queries, replace=False)
    
    recalls = []
    for qi in query_indices:
        q_vec = embeddings[qi]
        
        # Exact top-K (ground truth)
        dists = embeddings @ q_vec
        exact_topk = set(np.argpartition(-dists, top_k + 1)[:top_k + 1])
        exact_topk.discard(qi)
        exact_topk = set(list(exact_topk)[:top_k])

        # Bucket-filtered: find top-max_buckets nearest centroids, get all nodes in them
        centroid_dists = km.cluster_centers_ @ q_vec
        top_bucket_ids = np.argpartition(-centroid_dists, max_buckets)[:max_buckets]
        
        candidates = set()
        for b in top_bucket_ids:
            bucket_nodes = np.where(labels == b)[0]
            candidates.update(bucket_nodes.tolist())
        candidates.discard(qi)
        
        # Rerank candidates
        if candidates:
            cand_list = list(candidates)
            cand_vecs = embeddings[cand_list]
            cand_dists = cand_vecs @ q_vec
            topk_in_cand = np.argpartition(-cand_dists, min(top_k, len(cand_list) - 1))[:top_k]
            filtered_topk = set(cand_list[i] for i in topk_in_cand)
        else:
            filtered_topk = set()
        
        recall = len(exact_topk & filtered_topk) / len(exact_topk) if exact_topk else 1.0
        recalls.append(recall)
    
    mean_recall = float(np.mean(recalls))
    print(f"\nRecall@{top_k} (max_buckets={max_buckets}, {n_queries} queries): {mean_recall:.4f}")
    return mean_recall


def write_qbuckets(conn, node_ids: list, labels: np.ndarray, n_clusters: int):
    """Write ^NKG("q", bucketIdx, nodeIdx) = 1 for all nodes via Native API."""
    print(f"\nWriting ^NKG(\"q\",...) for {len(node_ids)} nodes...", flush=True)
    t0 = time.perf_counter()
    iris_obj = iris.createIRIS(conn)

    # Kill existing bucket index
    iris_obj.kill("^NKG", "q")

    # Get nodeIdx from ^NKG("$NI", node_id) → integer index
    # Write in batches using a transaction
    batch_size = 1000
    written = 0
    errors = 0
    
    iris_obj.tStart()
    for i, (nid, bucket) in enumerate(zip(node_ids, labels)):
        # Get integer node index from NKG name→int map
        node_idx = iris_obj.get("^NKG", "$NI", nid)
        if node_idx is None:
            errors += 1
            continue
        # Store: ^NKG("q", bucketIdx, nodeIdx) = 1
        iris_obj.set(1, "^NKG", "q", int(bucket), int(node_idx))
        written += 1
        if (i + 1) % batch_size == 0:
            iris_obj.tCommit()
            iris_obj.tStart()
            print(f"  {i+1}/{len(node_ids)} written...", flush=True)
    
    iris_obj.tCommit()

    elapsed = time.perf_counter() - t0
    print(f"  Wrote {written} entries in {elapsed:.1f}s ({errors} skipped/missing)", flush=True)
    
    # Write metadata
    iris_obj.set(n_clusters, "^NKG", "q", "$meta", "n_clusters")
    iris_obj.set(len(node_ids), "^NKG", "q", "$meta", "n_nodes")
    iris_obj.set(int(time.time()), "^NKG", "q", "$meta", "built_at")
    iris_obj.set("ivf_kmeans", "^NKG", "q", "$meta", "quantizer")
    
    return written, elapsed


def main():
    args = parse_args()

    print("=" * 60)
    print("NICHE Phase 0: IVF k-means bucket index build")
    print(f"Target: K={args.n_clusters}, port={args.port}")
    print("=" * 60)

    total_t0 = time.perf_counter()

    conn = iris.connect(args.host, args.port, args.namespace, args.username, args.password)
    node_ids, embeddings = load_embeddings(conn, args.port)
    n_nodes = len(node_ids)

    km, labels, train_time = train_kmeans(
        embeddings, args.n_clusters, args.batch_size, args.random_state
    )

    fill_stats = check_bucket_fill(labels, args.n_clusters, n_nodes)

    recall = measure_recall(embeddings, node_ids, labels, km)

    total_elapsed = time.perf_counter() - total_t0

    print(f"\nTotal build time: {total_elapsed:.1f}s (gate: < 60s)")

    print("\n" + "=" * 60)
    print("PHASE 0 GATE RESULTS")
    print("=" * 60)
    gate_fill = fill_stats["gate_pass"]
    gate_time = total_elapsed < 60
    gate_recall = recall >= 0.85
    print(f"  Bucket fill < 5%:  {'PASS' if gate_fill  else 'FAIL'}  (max={fill_stats['max_fill']*100:.2f}%)")
    print(f"  Build time < 60s:  {'PASS' if gate_time  else 'FAIL'}  ({total_elapsed:.1f}s)")
    print(f"  Recall@10 >= 0.85: {'PASS' if gate_recall else 'FAIL'}  ({recall:.4f})")
    all_pass = gate_fill and gate_time and gate_recall
    print(f"\n  OVERALL: {'PASS → proceed to Phase 1' if all_pass else 'FAIL → defer to v2.1.x'}")

    if all_pass and not args.dry_run:
        written, write_time = write_qbuckets(conn, node_ids, labels, args.n_clusters)
        print(f"\nBucket index written: {written} entries in {write_time:.1f}s")
        # Save centroids for Phase 1 fused query
        centroids_path = Path(__file__).parent / "centroids.npy"
        np.save(centroids_path, km.cluster_centers_.astype(np.float32))
        print(f"Centroids saved: {centroids_path} ({km.cluster_centers_.shape})")
    elif args.dry_run:
        print("\n(dry-run: skipping ^NKG write)")
    else:
        print("\nGate failed — not writing bucket index.")
        sys.exit(1)

    return all_pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
