#!/usr/bin/env python3
"""Load ICD-10-CM → MeSH mappings from UMLS MRCONSO.RRF into Graph_KG.fhir_bridges."""
import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def parse_mrconso(path: Path):
    icd_by_cui: dict[str, list[str]] = {}
    mesh_by_cui: dict[str, list[tuple[str, str]]] = {}
    parsed = 0
    skipped = 0

    log.info(f"Pass 1+2: scanning {path} ...")
    t0 = time.time()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 15:
                skipped += 1
                continue
            parsed += 1
            cui, sab, tty, code = fields[0], fields[11], fields[12], fields[13]

            if sab == "ICD10CM":
                icd_by_cui.setdefault(cui, []).append(code)
            elif sab == "MSH" and tty == "MH":
                mesh_by_cui.setdefault(cui, []).append((code, fields[14]))

    elapsed = time.time() - t0
    log.info(f"Parsed {parsed:,} lines in {elapsed:.1f}s ({skipped} skipped)")
    log.info(f"  ICD10CM CUIs: {len(icd_by_cui):,}")
    log.info(f"  MSH MH CUIs:  {len(mesh_by_cui):,}")

    seen = set()
    pairs = []
    for cui, icd_codes in icd_by_cui.items():
        if cui in mesh_by_cui:
            for icd in icd_codes:
                for mesh_id, mesh_name in mesh_by_cui[cui]:
                    key = (icd, f"MeSH:{mesh_id}")
                    if key not in seen:
                        seen.add(key)
                        pairs.append((icd, f"MeSH:{mesh_id}", cui))

    log.info(f"  ICD→MeSH pairs: {len(pairs):,}")
    return pairs


def load_to_iris(pairs, conn, dry_run=False):
    if dry_run:
        log.info(f"DRY RUN: would insert {len(pairs):,} rows")
        for icd, mesh, cui in pairs[:10]:
            log.info(f"  {icd} → {mesh} (CUI: {cui})")
        return 0

    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Graph_KG.fhir_bridges (
                fhir_code VARCHAR(64) %EXACT NOT NULL,
                kg_node_id VARCHAR(256) %EXACT NOT NULL,
                fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
                bridge_type VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
                confidence FLOAT DEFAULT 1.0,
                source_cui VARCHAR(16),
                CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
            )
        """)
        conn.commit()
    except Exception:
        conn.commit()

    inserted = 0
    dupes = 0
    t0 = time.time()
    for i, (icd, mesh, cui) in enumerate(pairs):
        try:
            cursor.execute(
                "INSERT INTO Graph_KG.fhir_bridges "
                "(fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence, source_cui) "
                "VALUES (?, ?, 'ICD10CM', 'icd10_to_mesh', 1.0, ?)",
                [icd, mesh, cui],
            )
            inserted += 1
        except Exception:
            dupes += 1
        if (i + 1) % 10000 == 0:
            conn.commit()
            log.info(f"  {i+1:,}/{len(pairs):,} ({inserted:,} inserted, {dupes:,} dupes)")
    conn.commit()
    elapsed = time.time() - t0
    log.info(f"Done: {inserted:,} inserted, {dupes:,} duplicates skipped in {elapsed:.1f}s")
    cursor.close()
    return inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mrconso", required=True, type=Path, help="Path to MRCONSO.RRF")
    parser.add_argument("--container", default="iris-vector-graph-main", help="IRIS container name")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't insert")
    args = parser.parse_args()

    if not args.mrconso.exists():
        log.error(f"MRCONSO file not found: {args.mrconso}")
        sys.exit(1)

    pairs = parse_mrconso(args.mrconso)

    if args.dry_run:
        load_to_iris(pairs, None, dry_run=True)
        return

    from iris_devtester import IRISContainer
    from iris_devtester.utils.dbapi_compat import get_connection

    c = IRISContainer.attach(args.container)
    port = c.get_exposed_port(1972)
    conn = get_connection("localhost", port, "USER", "test", "test")
    load_to_iris(pairs, conn)
    conn.close()


if __name__ == "__main__":
    main()
