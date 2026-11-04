"""4.6.0 R274 (NKGPopulated), pinned against Graph.KG.TraversalBuild's source (no container): "populated" is a
dictionary in ^NKG("$NI"), as EnsureNKG reads "built", not $Data(^NKG), which DropNKG's version stamp keeps at 1.

The answers are proven live in tests/e2e/test_460_r274_nkg_populated_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

KG = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"


def _method(cls: str, name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", (KG / cls).read_text(), re.S | re.M)
    assert m, name
    return m.group(0)


def test_populated_reads_the_dictionary():
    body = _method("TraversalBuild.cls", "NKGPopulated")
    assert 'Return ($Order(^NKG("$NI", "")) \'= "")' in body
    assert "$Data(^NKG)" not in body


def test_drop_still_leaves_the_version_stamp():
    """What made $Data(^NKG) answer 1 after a drop: the version it keeps for readers to see the change."""
    assert 'Set ^NKG("$meta", "version") = tVer + 1' in _method("GraphIndex.cls", "DropNKG")
