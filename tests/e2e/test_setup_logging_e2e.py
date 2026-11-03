"""4.6.0 (NR6, NR11, NR18): a real `initialize_schema()` logs no WARNING and no wrapper mock line.

A fresh interpreter runs the README's order, `import iris`, then IVG, then `initialize_schema()` twice, against the
test namespace, with logging at INFO. Before 4.6.0 importing IVG made the embedded-Python wrapper return four
MagicMocks ("Class or module 'IRISNative' not found in iris_embedded_python. Returning a mock object"), and setup
warned "Namespace mismatch: connected to '<NS>', expected 'USER'" in any namespace but USER. The namespace is the
connection's; each run ends with the same one-line summary.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

REPO = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(
    """
    import logging, sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    import iris
    print("---- import ivg", flush=True)
    from iris_vector_graph.engine import IRISGraphEngine
    conn = iris.connect(sys.argv[1], int(sys.argv[2]), sys.argv[3], "_SYSTEM", "SYS")
    for run in (1, 2):
        print(f"---- init {run}", flush=True)
        IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    print("---- done", flush=True)
    """
)


@pytest.fixture(scope="module")
def log(iris_connection):
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    namespace = iris_connection.namespace
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT, iris_connection.hostname, str(iris_connection.port), namespace],
        capture_output=True, text=True, env=env, timeout=600,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return namespace, out.stdout.splitlines()


def _after(lines, marker):
    return lines[lines.index(marker) + 1:]


def test_no_wrapper_mock_line(log):
    _, lines = log
    assert [line for line in lines if "Returning a mock object" in line] == []


def test_setup_logs_no_warning(log):
    _, lines = log
    assert [line for line in _after(lines, "---- import ivg") if line.startswith(("WARNING", "ERROR"))] == []


def test_each_run_ends_with_the_same_summary_naming_the_namespace(log):
    namespace, lines = log
    summaries = [line for line in lines if line.startswith("INFO") and ": IVG ready" in line]
    assert len(summaries) == 2, lines
    assert summaries[0] == summaries[1]
    assert f"namespace {namespace}" in summaries[0]
    assert not [line for line in lines if "Namespace mismatch" in line]
