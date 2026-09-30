"""4.1.1 — every `Kill ^NKG` goes through GraphIndex.DropNKG, which keeps the version.

The live proof is tests/integration/test_411_nkg_version_monotonic.py.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG"


def test_no_class_kills_the_whole_of_nkg_but_drop_nkg():
    offenders = []
    for cls in SRC.glob("*.cls"):
        for i, line in enumerate(cls.read_text().splitlines(), 1):
            if re.search(r"\bKill\b[^/]*\^NKG\s*(,|$)", line.split("//")[0]):
                offenders.append(f"{cls.name}:{i}: {line.strip()}")
    drop = [o for o in offenders if o.startswith("GraphIndex.cls")]
    assert len(drop) == 1, offenders
    assert offenders == drop, offenders


def test_drop_nkg_carries_the_version_past_the_kill():
    body = re.search(
        r"^ClassMethod DropNKG\(.*?^\}", (SRC / "GraphIndex.cls").read_text(), re.S | re.M
    ).group(0)
    kill = body.index("Kill ^NKG")
    assert body.index('$Get(^NKG("$meta", "version")') < kill
    assert body.index('Set ^NKG("$meta", "version")', kill) > kill


def test_restore_raises_the_version_after_importing_globals():
    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph/_engine/snapshot.py").read_text()
    body = src[src.index("    def restore_snapshot(") :]
    body = body[: body.index("\n    def ", 10)]
    assert body.index("_import_global_from_ndjson") < body.index("_raise_nkg_version")


def test_no_test_kills_nkg_whole():
    """A test that kills ^NKG resets its version, and the next betweenness call in a
    process that already answered answers with the previous graph. Use DropNKG."""
    tests = Path(__file__).resolve().parents[1]
    hits = [
        str(p.relative_to(tests))
        for p in tests.rglob("*.py")
        if re.search(r"""\.kill\(\s*["']\^NKG["']\s*\)""", p.read_text())
    ]
    assert not hits, hits
