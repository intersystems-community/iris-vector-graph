"""The vector-routing unit tests never reach IRIS; the live timing check is an E2E test.

`test_native_tier_faster_than_ivf_on_live_db` sat in tests/unit/test_vector_search_routing.py and took
`iris_connection`. `SKIP_IRIS_TESTS=true` does not hide a running container (tests/conftest.py skips only when the
container is absent), so an "offline" unit run on the laptop ran it against ivg-iris-enterprise, and it left
`Graph_KG.kg_NodeEmbeddings.emb` at another width than the session's while other suites were using the container
(4.6.0, 2026-11-07). It now lives in tests/e2e/test_vector_search_native_tier_e2e.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNIT = REPO / "tests" / "unit" / "test_vector_search_routing.py"
E2E = REPO / "tests" / "e2e" / "test_vector_search_native_tier_e2e.py"
LIVE_FIXTURES = {"iris_connection", "iris_cursor", "iris_test_container", "arno_iris_connection"}


def _tests_taking(path: Path, fixtures: set) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            if {a.arg for a in node.args.args} & fixtures:
                out.append(node.name)
    return out


def test_the_unit_file_takes_no_live_fixture():
    assert _tests_taking(UNIT, LIVE_FIXTURES) == []


def test_the_live_timing_check_is_an_e2e_test():
    assert _tests_taking(E2E, {"iris_connection"}) == ["test_native_tier_faster_than_ivf_on_live_db"]
    assert "pytest.mark.e2e" in E2E.read_text() and "pytest.mark.requires_database" in E2E.read_text()
