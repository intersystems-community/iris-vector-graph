"""The examples run one after another in one namespace (scripts/quickstart_e2e.py's examples stage, and a newcomer
following the README), so an example that declares an embedding width declares the README's.

4.7.0 row 25 refuses a writer whose width differs from the namespace's declared one (EmbeddingIdentityConflict).
examples/demo_rdf_semantic_layer.py built IRISGraphEngine(conn, embedding_dimension=4) though it stores no vector, and
after the README's setup had declared 768 it stopped at initialize_schema() (the 4.7.0 gate's quickstart on the
Studio). Without a width initialize_schema() refuses on a namespace with no recorded one, so the example says 768.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _readme_width() -> int:
    widths = set(re.findall(r"IRISGraphEngine\(conn, embedding_dimension=(\d+)\)", (REPO / "README.md").read_text()))
    assert len(widths) == 1, widths
    return int(widths.pop())


def test_every_example_width_is_the_readmes():
    want = _readme_width()
    offenders = []
    for path in sorted((REPO / "examples").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "IRISGraphEngine":
                for k in node.keywords:
                    if k.arg == "embedding_dimension" and not (isinstance(k.value, ast.Constant) and k.value.value == want):
                        offenders.append(f"{path.name}:{node.lineno} {ast.unparse(k.value)}")
    assert offenders == [], f"README declares {want}"
