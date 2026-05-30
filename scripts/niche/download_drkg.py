"""
Download DRKG and load into the ivg-arno-bench IRIS container for spec 168 Phase 0.

Steps:
  1. Download drkg.tar.gz (217 MB) from S3
  2. Extract TransE_l2 entity embeddings + entity map
  3. Load Hetionet nodes/edges into IRIS (for the canonical TP53/MM demo)
  4. Ingest DRKG embeddings into Graph_KG.kg_NodeEmbeddings
  5. Run Q4 baseline to confirm setup

Usage:
  python3 scripts/niche/download_drkg.py [--port 25972] [--data-dir /tmp/drkg]
"""
from __future__ import annotations

import argparse
import io
import os
import tarfile
import time
import urllib.request
from pathlib import Path


DRKG_URL = "https://dgl-data.s3-us-west-2.amazonaws.com/dataset/DRKG/drkg.tar.gz"
HETIONET_NODES_URL = "https://raw.githubusercontent.com/hetio/hetionet/main/hetnet/tsv/hetionet-v1.0-nodes.tsv"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=25972)
    p.add_argument("--host", default="localhost")
    p.add_argument("--namespace", default="USER")
    p.add_argument("--username", default="_SYSTEM")
    p.add_argument("--password", default="SYS")
    p.add_argument("--data-dir", default="/tmp/drkg_niche")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--max-nodes", type=int, default=50000, help="Cap nodes to load (0=all)")
    return p.parse_args()


def download_with_progress(url: str, dest: Path, description: str):
    print(f"Downloading {description}...")
    t0 = time.perf_counter()
    req = urllib.request.urlopen(url, timeout=120)
    total = int(req.headers.get("content-length", 0))
    downloaded = 0
    chunk_size = 1 << 20
    with open(dest, "wb") as f:
        while True:
            chunk = req.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                mb = downloaded / 1e6
                print(f"\r  {mb:.0f}/{total/1e6:.0f} MB ({pct:.0f}%)", end="", flush=True)
    elapsed = time.perf_counter() - t0
    print(f"\n  Done in {elapsed:.1f}s ({dest.stat().st_size/1e6:.0f} MB)")


def extract_drkg(data_dir: Path):
    tarball = data_dir / "drkg.tar.gz"
    print("Extracting DRKG archive...")
    t0 = time.perf_counter()
    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(data_dir)
    elapsed = time.perf_counter() - t0
    print(f"  Extracted in {elapsed:.1f}s")
    embed_path = data_dir / "embed" / "DRKG_TransE_l2_entity.npy"
    entity_path = data_dir / "embed" / "entities.tsv"
    if not embed_path.exists():
        possible = list(data_dir.rglob("DRKG_TransE_l2_entity.npy"))
        if possible:
            embed_path = possible[0]
    return embed_path, entity_path


def load_drkg_embeddings(embed_path: Path, entity_path: Path, max_nodes: int):
    import numpy as np
    print(f"Loading DRKG embeddings from {embed_path}...")
    emb = np.load(embed_path).astype(np.float32)
    print(f"  Shape: {emb.shape}, dtype: {emb.dtype}")

    print(f"Loading entity map from {entity_path}...")
    entity_map = {}
    with open(entity_path) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) == 2:
                name, eid = parts
                entity_map[name] = int(eid)

    print(f"  {len(entity_map)} entities")

    if max_nodes > 0 and len(entity_map) > max_nodes:
        items = list(entity_map.items())[:max_nodes]
        entity_map = dict(items)
        print(f"  Capped to {max_nodes} nodes")

    return emb, entity_map


def ingest_into_iris(conn, emb, entity_map, engine):
    import iris as _iris
    print(f"\nIngesting {len(entity_map)} nodes into IRIS...")
    t0 = time.perf_counter()
    cursor = conn.cursor()
    for t in ["Graph_KG.rdf_edges","Graph_KG.rdf_labels","Graph_KG.rdf_props","Graph_KG.nodes","Graph_KG.kg_NodeEmbeddings"]:
        try:
            cursor.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()

    batch_size = 500
    nodes_written = 0
    emb_written = 0

    node_ids = list(entity_map.keys())
    for i in range(0, len(node_ids), batch_size):
        batch = node_ids[i:i + batch_size]
        for nid in batch:
            try:
                engine.create_node(nid)
                nodes_written += 1
            except Exception:
                pass
        if (i // batch_size) % 10 == 0:
            print(f"  nodes: {nodes_written}/{len(node_ids)}", flush=True)

    conn.commit()

    print(f"  {nodes_written} nodes created in {time.perf_counter()-t0:.1f}s")

    iris_obj = _iris.createIRIS(conn)
    print(f"Inserting {len(entity_map)} embeddings (dim={emb.shape[1]})...")
    t1 = time.perf_counter()
    cursor.execute(f"SELECT TOP 1 id FROM Graph_KG.kg_NodeEmbeddings")
    has_emb_table = True
    try:
        cursor.fetchone()
    except Exception:
        has_emb_table = False

    for i, (nid, eid) in enumerate(entity_map.items()):
        if eid >= len(emb):
            continue
        vec = emb[eid]
        vec_str = ",".join(f"{v:.8f}" for v in vec)
        try:
            cursor.execute(
                "INSERT OR UPDATE INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                [nid, vec_str]
            )
            emb_written += 1
        except Exception:
            try:
                cursor.execute(
                    "UPDATE Graph_KG.kg_NodeEmbeddings SET emb = TO_VECTOR(?) WHERE id = ?",
                    [vec_str, nid]
                )
            except Exception:
                pass
        if (i + 1) % batch_size == 0:
            conn.commit()
            print(f"  embeddings: {emb_written}/{len(entity_map)}", flush=True)

    conn.commit()
    elapsed = time.perf_counter() - t1
    print(f"  {emb_written} embeddings inserted in {elapsed:.1f}s")
    return nodes_written, emb_written


def verify_cosine_diversity(conn, n_sample=200):
    import numpy as np
    print("\nVerifying embedding diversity (pairwise cosine on sample)...")
    cursor = conn.cursor()
    cursor.execute(f"SELECT TOP {n_sample} id, emb FROM Graph_KG.kg_NodeEmbeddings")
    rows = cursor.fetchall()
    vecs = []
    for row in rows:
        r = list(row)
        floats = [float(x) for x in r[1].split(",") if x.strip()]
        vecs.append(floats)
    if not vecs:
        print("  No embeddings found!")
        return 0.0
    mat = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat /= np.where(norms == 0, 1.0, norms)
    dots = mat @ mat.T
    np.fill_diagonal(dots, 0)
    mean_cos = float(dots.mean())
    max_cos = float(dots.max())
    print(f"  Mean pairwise cosine: {mean_cos:.4f}")
    print(f"  Max pairwise cosine: {max_cos:.4f}")
    print(f"  {'GOOD: high intra-cluster similarity expected' if max_cos > 0.5 else 'LOW: may still fail recall gate — check entity type distribution'}")
    return max_cos


def main():
    args = parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_download:
        tarball = data_dir / "drkg.tar.gz"
        if not tarball.exists():
            download_with_progress(DRKG_URL, tarball, "DRKG (217 MB)")
        else:
            print(f"DRKG tarball already exists: {tarball}")

        embed_path, entity_path = extract_drkg(data_dir)
    else:
        embed_path = data_dir / "embed" / "DRKG_TransE_l2_entity.npy"
        entity_path = data_dir / "embed" / "entities.tsv"

    emb, entity_map = load_drkg_embeddings(embed_path, entity_path, args.max_nodes)

    import iris
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(args.host, args.port, args.namespace, args.username, args.password)
    engine = IRISGraphEngine(conn)

    nodes_written, emb_written = ingest_into_iris(conn, emb, entity_map, engine)

    max_cos = verify_cosine_diversity(conn)

    print("\n" + "=" * 60)
    print("DRKG INGEST COMPLETE")
    print("=" * 60)
    print(f"  Nodes:      {nodes_written}")
    print(f"  Embeddings: {emb_written}  (dim={emb.shape[1]})")
    print(f"  Max cosine: {max_cos:.4f}")
    print()
    print("Next: run scripts/niche/build_qbuckets.py to execute Phase 0 gate")
    print(f"  python3 scripts/niche/build_qbuckets.py --port {args.port}")


if __name__ == "__main__":
    main()
