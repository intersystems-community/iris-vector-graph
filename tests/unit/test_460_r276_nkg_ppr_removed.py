"""4.6.0 R276: ``NKGAccel.PPRNative``, ``NKGAccel.PPRJson`` and their ``NKGAccelTraversal`` bodies are removed.

They walked the seed's 2-hop neighbourhood only, passed a dangling node's score nowhere, and on the legacy ``^KG``
layout read ``^KG("out", node, ...)`` with no graph subscript, so their answer was neither networkx's personalized
PageRank nor ``kg_PERSONALIZED_PAGERANK``'s. R270 took the last IVG caller away (``ppr()``); what answers
personalized PageRank is ``Graph.KG.PageRank.RunJson`` and, with the Arno library proven, ``ArnoAccel.PPRNativeJson``.

No IRIS: the class sources are read as text. The live check that a deployed namespace no longer has the methods is
``tests/e2e/test_460_r276_nkg_ppr_removed_e2e.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLASSES = REPO / "iris_src" / "src" / "Graph" / "KG"
GONE = ("PPRNative", "PPRJson")


@pytest.mark.parametrize("cls", ["NKGAccel.cls", "NKGAccelTraversal.cls"])
@pytest.mark.parametrize("method", GONE)
def test_the_method_is_not_defined(cls, method):
    text = (CLASSES / cls).read_text()
    assert not re.search(rf"^ClassMethod {method}\(", text, re.M), f"{cls} still defines {method}"


def test_no_shipped_or_script_caller_names_them():
    """Nothing IVG ships or runs calls the NKG accelerator's PPR (``ArnoAccel.PPRJson``/``PPRNativeJson`` are other
    methods and stay)."""
    pattern = re.compile(r'NKGAccel(?:Traversal)?(?:"\s*,\s*"|\)\.)(PPRNative|PPRJson)\b')
    offenders = []
    for top in ("iris_vector_graph", "iris_src", "scripts"):
        for path in (REPO / top).rglob("*"):
            if path.suffix not in {".py", ".cls", ".sh", ".mac"} or not path.is_file():
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert offenders == []
