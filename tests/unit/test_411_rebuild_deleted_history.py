"""4.1.1 — FHIR Rebuild does not resync deleted history.

FHIR deletes are soft: the Rsrc row stays with Deleted=1. The IVGFHIR test repo
held 23,638 live rows beside 282,582 deleted ones (2026-09-30), and Rebuild sent
every one through ResyncKey -> DropNode, about 20 statements per key, so a rebuild
cost the deleted history rather than the live data. The E2E gate spent most of its
time there.

Rebuild now reads Deleted in its scan and skips a deleted key. A deleted key that
still has a node is dropped by the stale pass, which already walks the graph's
nodes; one with only unresolved rows is swept the same way. SyncAllDefinitions
reads live keys only: both of its tables were just emptied, so a deleted key had
nothing to do there.

The live proof is tests/e2e/test_411_rebuild_deleted_history_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/FHIRGraph.cls"

LIVE = "(Deleted IS NULL OR Deleted = 0)"


def _method(name: str) -> str:
    text = CLS.read_text()
    m = re.search(rf"^(?:Class)?Method {name}\(.*?^\}}", text, re.S | re.M)
    assert m, f"{name} not found in {CLS.name}"
    return m.group(0)


def test_rebuild_scan_reads_deleted_and_skips_it():
    body = _method("Rebuild")
    assert "SELECT Key, Deleted FROM" in body
    scan = body[body.index("SELECT Key, Deleted FROM") :]
    scan = scan[: scan.index("RunBatch")]
    assert "Continue" in scan, "a deleted key must not reach RunBatch"
    assert "..Live(" in scan or ".Live(" in scan, "the scan should cache liveness it read"


def test_rebuild_stale_pass_still_walks_nodes_and_unresolved_sources():
    body = _method("Rebuild")
    assert "SELECT node_id FROM Graph_KG.nodes WHERE graph_id = ?" in body
    assert "SELECT source FROM Graph_KG.fhir_unresolved WHERE graph_id = ?" in body
    assert "DropNode" in body


def test_sync_all_definitions_reads_live_keys_only():
    body = _method("SyncAllDefinitions")
    assert LIVE in body
