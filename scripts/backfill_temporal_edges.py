#!/usr/bin/env python3
"""
Backfill temporal edges into ivg from FHIR resources already in arno.

Fetches all Encounters, Conditions, Observations, MedicationRequests, DiagnosticReports
from arno-fhir, extracts patient reference + clinical date, and writes pointer nodes +
temporal edges into ivg-iris via the IRISGraphEngine directly.

Also performs lazy vectorization: Condition and DiagnosticReport nodes are embedded
using all-MiniLM-L6-v2 and stored in kg_NodeEmbeddings if not already present.

Usage:
  python scripts/backfill_temporal_edges.py
  python scripts/backfill_temporal_edges.py --arno-url http://dpgenai1:8094 --ivg-port 21972
  python scripts/backfill_temporal_edges.py --no-embed   # skip vectorization
"""
from __future__ import annotations

import argparse
import sys
import warnings
warnings.filterwarnings("ignore")

import requests


def _extract_patient_ref(resource: dict) -> str:
    for field in ("subject", "patient"):
        ref = resource.get(field, {})
        if isinstance(ref, dict):
            r = ref.get("reference", "")
            if r.startswith("Patient/"):
                return r
    return ""


def _extract_date(resource: dict) -> str:
    for field in ("period", "effectiveDateTime", "recordedDate", "authoredOn",
                  "onsetDateTime", "effectivePeriod", "issued"):
        val = resource.get(field)
        if isinstance(val, dict):
            val = val.get("start") or val.get("end") or ""
        if isinstance(val, str) and val:
            return val[:10]
    meta = resource.get("meta", {})
    lu = meta.get("lastUpdated", "")
    if lu:
        return lu[:10]
    return ""


def fetch_all(arno_url: str, resource_type: str, page_size: int = 500) -> list[dict]:
    """Fetch all resources of a type from arno — uses large page to avoid pagination issues."""
    session = requests.Session()
    url = f"{arno_url}/fhir/{resource_type}?_count={page_size}"
    resources = []
    try:
        resp = session.get(url, timeout=60)
        if resp.status_code == 200:
            bundle = resp.json()
            for entry in bundle.get("entry", []):
                resources.append(entry["resource"])
    except Exception as e:
        print(f"  WARN: fetch {resource_type} failed: {e}")
    return resources


def main():
    parser = argparse.ArgumentParser(description="Backfill temporal edges + lazy embeddings into ivg from arno FHIR data")
    parser.add_argument("--arno-url",  default="http://dpgenai1:8094")
    parser.add_argument("--ivg-host",  default="localhost")
    parser.add_argument("--ivg-port",  type=int, default=21972)
    parser.add_argument("--ivg-ns",    default="USER")
    parser.add_argument("--ivg-user",  default="_SYSTEM")
    parser.add_argument("--ivg-pass",  default="SYS")
    parser.add_argument("--no-embed",  action="store_true", help="Skip lazy vectorization")
    args = parser.parse_args()

    # Connect to ivg-iris
    print(f"Connecting to ivg-iris at {args.ivg_host}:{args.ivg_port} ...")
    from iris_devtester.utils.dbapi_compat import get_connection
    conn = get_connection(args.ivg_host, args.ivg_port, args.ivg_ns, args.ivg_user, args.ivg_pass)
    from iris_vector_graph import IRISGraphEngine
    eng = IRISGraphEngine(conn)
    print(f"  ✓ connected")

    from api.routers.fhir_event import (
        is_embed_eligible, _extract_embed_text, already_embedded, unix_from_date,
        _extract_codes,
    )

    # Load embedder once if needed
    embed_model = None
    if not args.no_embed:
        print("Loading all-MiniLM-L6-v2 for lazy vectorization...")
        from sentence_transformers import SentenceTransformer
        embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("  ✓ model loaded (384-dim)")

    resource_types = ["Encounter", "Condition", "Observation", "MedicationRequest", "DiagnosticReport"]
    total_nodes = 0
    total_edges = 0
    total_skipped = 0
    total_embedded = 0

    for rtype in resource_types:
        print(f"\nFetching {rtype} from {args.arno_url} ...", end="", flush=True)
        resources = fetch_all(args.arno_url, rtype)
        print(f" {len(resources)} found")

        for resource in resources:
            rid = resource.get("id", "")
            if not rid:
                continue

            fhir_url = f"{rtype}/{rid}"
            patient_ref = _extract_patient_ref(resource)
            date_str = _extract_date(resource)

            # Upsert node — carry clinical codes (ICD-10/ATC/RxNorm/SNOMED) as
            # properties so Shaarpec's VGAE trains on coded nodes, not bare pointers.
            code_props = _extract_codes(rtype, resource)
            try:
                eng.create_node(
                    node_id=fhir_url,
                    labels=[rtype],
                    properties=code_props or None,
                )
                total_nodes += 1
            except Exception:
                pass

            if not patient_ref:
                total_skipped += 1
            else:
                # Upsert patient pointer node
                try:
                    eng.create_node(node_id=patient_ref, labels=["Patient"])
                except Exception:
                    pass

                # Write temporal edge
                ts, _ = unix_from_date(date_str if date_str else None)
                try:
                    eng.create_edge_temporal(
                        source=patient_ref,
                        predicate=rtype.upper(),
                        target=fhir_url,
                        timestamp=ts,
                        weight=1.0,
                        upsert=True,
                    )
                    total_edges += 1
                except Exception as e:
                    print(f"  WARN: edge write failed for {fhir_url}: {e}")

            # Lazy vectorization for eligible types
            if embed_model and is_embed_eligible(rtype, resource):
                embed_text = _extract_embed_text(rtype, resource)
                if embed_text and not already_embedded(eng, fhir_url):
                    try:
                        vec = embed_model.encode(embed_text).tolist()
                        eng.store_embedding(fhir_url, vec)
                        total_embedded += 1
                    except Exception as e:
                        print(f"  WARN: embed failed for {fhir_url}: {e}")

        embedded_msg = f", {total_embedded} embedded so far" if embed_model else ""
        print(f"  ✓ {rtype}: nodes={total_nodes}, edges={total_edges}{embedded_msg}")

    print(f"\n{'='*60}")
    print(f"Backfill complete:")
    print(f"  Pointer nodes written: {total_nodes}")
    print(f"  Temporal edges written: {total_edges}")
    print(f"  Resources skipped (no patient ref): {total_skipped}")
    if embed_model:
        print(f"  Lazy embeddings stored: {total_embedded}")

    # Verify
    edges = eng.get_edges_in_window(start=0, end=9_999_999_999)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
    node_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
    embed_count = cur.fetchone()[0]
    print(f"\nVerification:")
    print(f"  Graph_KG.nodes: {node_count}")
    print(f"  Temporal edges: {len(edges)}")
    print(f"  Node embeddings: {embed_count}")

    if embed_count > 0:
        print(f"\n✓ Lazy vectorization active — {embed_count} nodes ready for _v_ semantic search")
        print(f"  Example: GET /fhir/Condition?_v_content=heart+failure+reduced+ejection")
    print(f"\n✓ Demo ready: {len(edges)} temporal edges, {node_count} pointer nodes")


if __name__ == "__main__":
    main()
