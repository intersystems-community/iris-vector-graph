"""LDBC SNB SF10 full-schema loader using IVG engine methods.

Loads all entity types and relationships from CsvBasic-LongDateFormatter format
into the enterprise IRIS container via bulk_create_nodes + BulkIngestEdges.

Usage:
    python tests/benchmarks/ldbc_full_loader.py [--skip-comments] [--skip-posts] [--dry-run]
"""

import csv
import json
import os
import sys
import time

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "4972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")

SF10_ROOT = "/tmp/sf10_out/social_network-sf10-CsvBasic-LongDateFormatter"
DYN = f"{SF10_ROOT}/dynamic"
STA = f"{SF10_ROOT}/static"

BATCH_SIZE = 50_000


def _connect():
    import iris
    from iris_vector_graph.engine import IRISGraphEngine
    c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
    e = IRISGraphEngine(c)
    return c, e


def load_static_nodes(engine, dry_run=False):
    counts = {}
    for label, path in [
        ("Tag", f"{STA}/tag_0_0.csv"),
        ("Organisation", f"{STA}/organisation_0_0.csv"),
        ("Place", f"{STA}/place_0_0.csv"),
    ]:
        batch = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="|")
            for row in reader:
                nid = f"{label.lower()}_{row['id']}"
                props = {k: v for k, v in row.items() if k != "id" and v}
                batch.append({"id": nid, "labels": [label], "properties": props})
                if len(batch) >= BATCH_SIZE:
                    if not dry_run:
                        engine.bulk_create_nodes(batch)
                    batch = []
        if batch and not dry_run:
            engine.bulk_create_nodes(batch)
        counts[label] = len(batch)
        print(f"  {label}: loaded")
    return counts


def load_forum_nodes(engine, dry_run=False):
    path = f"{DYN}/forum_0_0.csv"
    batch = []
    total = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            nid = f"forum_{row['id']}"
            batch.append({"id": nid, "labels": ["Forum"], "properties": {"title": row.get("title", ""), "creationDate": row.get("creationDate", "")}})
            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    engine.bulk_create_nodes(batch)
                total += len(batch)
                batch = []
                print(f"    forum: {total:,} ...")
    if batch and not dry_run:
        engine.bulk_create_nodes(batch)
    total += len(batch)
    print(f"  Forum: {total:,} loaded")
    return total


def load_post_nodes(engine, dry_run=False):
    path = f"{DYN}/post_0_0.csv"
    batch = []
    total = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            nid = f"post_{row['id']}"
            batch.append({"id": nid, "labels": ["Post"], "properties": {"creationDate": row.get("creationDate", ""), "length": row.get("length", ""), "content": row.get("content", "")[:200]}})
            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    engine.bulk_create_nodes(batch)
                total += len(batch)
                batch = []
                if total % 500_000 == 0:
                    print(f"    post: {total:,} ...")
    if batch and not dry_run:
        engine.bulk_create_nodes(batch)
    total += len(batch)
    print(f"  Post: {total:,} loaded")
    return total


def load_comment_nodes(engine, dry_run=False):
    path = f"{DYN}/comment_0_0.csv"
    batch = []
    total = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            nid = f"comment_{row['id']}"
            batch.append({"id": nid, "labels": ["Comment"], "properties": {"creationDate": row.get("creationDate", ""), "length": row.get("length", "")}})
            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    engine.bulk_create_nodes(batch)
                total += len(batch)
                batch = []
                if total % 1_000_000 == 0:
                    print(f"    comment: {total:,} ...")
    if batch and not dry_run:
        engine.bulk_create_nodes(batch)
    total += len(batch)
    print(f"  Comment: {total:,} loaded")
    return total


def _bulk_ingest_edges(conn, edges_batch, predicate):
    from iris_vector_graph.schema import _call_classmethod_large
    import iris as iris_mod
    o = iris_mod.createIRIS(conn)
    _call_classmethod_large(o, "Graph.KG.EdgeScan", "BulkIngestEdges", json.dumps(edges_batch), predicate)


def load_edges_file(conn, path, col_s, col_o, predicate, prefix_s, prefix_o, dry_run=False):
    batch = []
    total = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            s = f"{prefix_s}{row[col_s]}"
            o = f"{prefix_o}{row[col_o]}"
            batch.append({"s": s, "p": predicate, "o": o})
            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    _bulk_ingest_edges(conn, batch, predicate)
                total += len(batch)
                batch = []
                if total % 500_000 == 0:
                    print(f"    {predicate}: {total:,} ...")
    if batch and not dry_run:
        _bulk_ingest_edges(conn, batch, predicate)
    total += len(batch)
    print(f"  {predicate}: {total:,} edges")
    return total


def rebuild_nkg(conn):
    from iris_vector_graph.schema import _call_classmethod
    t0 = time.perf_counter()
    _call_classmethod(conn, "Graph.KG.Traversal", "BuildNKG")
    print(f"  BuildNKG (arno integer index): {(time.perf_counter()-t0):.1f}s")


def run_full_load(skip_comments=False, skip_posts=False, dry_run=False):
    conn, engine = _connect()
    print(f"Connected to IRIS {IRIS_HOST}:{IRIS_PORT}")

    print("\n=== Phase 1: Static nodes (Tag, Organisation, Place) ===")
    t0 = time.perf_counter()
    load_static_nodes(engine, dry_run)
    print(f"  Done in {time.perf_counter()-t0:.1f}s")

    print("\n=== Phase 2: Forum nodes ===")
    t0 = time.perf_counter()
    load_forum_nodes(engine, dry_run)
    print(f"  Done in {time.perf_counter()-t0:.1f}s")

    if not skip_posts:
        print("\n=== Phase 3: Post nodes ===")
        t0 = time.perf_counter()
        load_post_nodes(engine, dry_run)
        print(f"  Done in {time.perf_counter()-t0:.1f}s")

    if not skip_comments:
        print("\n=== Phase 4: Comment nodes ===")
        t0 = time.perf_counter()
        load_comment_nodes(engine, dry_run)
        print(f"  Done in {time.perf_counter()-t0:.1f}s")

    print("\n=== Phase 5: Edges ===")
    edge_specs = [
        (f"{DYN}/post_hasCreator_person_0_0.csv", "Post.id", "Person.id", "HAS_CREATOR", "post_", "p_"),
        (f"{DYN}/forum_hasMember_person_0_0.csv", "Forum.id", "Person.id", "HAS_MEMBER", "forum_", "p_"),
        (f"{DYN}/forum_hasTag_tag_0_0.csv", "Forum.id", "Tag.id", "HAS_TAG", "forum_", "tag_"),
        (f"{DYN}/post_hasTag_tag_0_0.csv", "Post.id", "Tag.id", "HAS_TAG", "post_", "tag_"),
        (f"{DYN}/person_hasInterest_tag_0_0.csv", "Person.id", "Tag.id", "INTERESTED_IN", "p_", "tag_"),
        (f"{DYN}/person_workAt_organisation_0_0.csv", "Person.id", "Organisation.id", "WORKS_AT", "p_", "organisation_"),
        (f"{DYN}/person_studyAt_organisation_0_0.csv", "Person.id", "Organisation.id", "STUDIED_AT", "p_", "organisation_"),
        (f"{DYN}/person_isLocatedIn_place_0_0.csv", "Person.id", "Place.id", "LOCATED_IN", "p_", "place_"),
        (f"{DYN}/post_isLocatedIn_place_0_0.csv", "Post.id", "Place.id", "LOCATED_IN", "post_", "place_"),
        (f"{DYN}/person_likes_post_0_0.csv", "Person.id", "Post.id", "LIKES", "p_", "post_"),
    ]
    if not skip_comments:
        edge_specs += [
            (f"{DYN}/comment_hasCreator_person_0_0.csv", "Comment.id", "Person.id", "HAS_CREATOR", "comment_", "p_"),
            (f"{DYN}/comment_hasTag_tag_0_0.csv", "Comment.id", "Tag.id", "HAS_TAG", "comment_", "tag_"),
            (f"{DYN}/comment_replyOf_post_0_0.csv", "Comment.id", "Post.id", "REPLY_OF", "comment_", "post_"),
            (f"{DYN}/comment_replyOf_comment_0_0.csv", "Comment.id", "Comment.id", "REPLY_OF", "comment_", "comment_"),
            (f"{DYN}/person_likes_comment_0_0.csv", "Person.id", "Comment.id", "LIKES", "p_", "comment_"),
        ]

    t0 = time.perf_counter()
    for path, col_s, col_o, pred, pfx_s, pfx_o in edge_specs:
        if os.path.exists(path):
            load_edges_file(conn, path, col_s, col_o, pred, pfx_s, pfx_o, dry_run)
        else:
            print(f"  SKIPPING (not found): {path}")
    print(f"  Edges done in {time.perf_counter()-t0:.1f}s")

    if not dry_run:
        print("\n=== Phase 6: Rebuild ^NKG (arno integer index for fast BFS) ===")
        rebuild_nkg(conn)

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    skip_comments = "--skip-comments" in sys.argv
    skip_posts = "--skip-posts" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY RUN - no writes]")
    run_full_load(skip_comments=skip_comments, skip_posts=skip_posts, dry_run=dry_run)
