"""4.1.1 — SyncOnce rebuilds when the repository is behind the graph's watermarks.

SyncOnce reads rows with ID above each watermark. A repository emptied by
HS.FHIRServer.Installer.Reset (or restored from an older backup) has no such rows:
the sync reported ok, applied nothing, and the graph kept every node of the
resources that were gone. Found while making the FHIR E2E session reset its
scratch repository (2026-09-30).

SyncOnce now compares each feed's MAX(ID) with its watermark first. Behind on
either one, it hands over to Rebuild, which drops what the repository no longer
has, and says so in `rebuilt`. The live proof is the last case of
tests/e2e/test_411_rebuild_deleted_history_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/FHIRGraph.cls"


def _method(name: str) -> str:
    text = CLS.read_text()
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", text, re.S | re.M)
    assert m, f"{name} not found in {CLS.name}"
    return m.group(0)


def test_sync_compares_both_feeds_with_their_watermarks():
    body = _method("SyncOnce")
    assert "tMaxR < tCtx.WmRsrc" in body
    assert "tMaxV < tCtx.WmVer" in body


def test_sync_hands_over_to_rebuild_before_reading_a_feed():
    body = _method("SyncOnce")
    guard = body.index("tMaxR < tCtx.WmRsrc")
    assert guard < body.index("..Rebuild(pGraph)") < body.index('For tFeed = "wm_rsrc", "wm_ver"')
    assert '"rebuilt"' in body
    # Rebuild takes the same lock, so SyncOnce lets go of it first.
    between = body[guard : body.index("..Rebuild(pGraph)")]
    assert "Lock -^IVG.FHIRGraph(pGraph)" in between
