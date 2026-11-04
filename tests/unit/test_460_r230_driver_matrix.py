"""4.6.0 R230: the driver drift gate, scripts/dev/driver_matrix.sh, runs what reads property values under the repo's
driver and under the one a fresh install gets.

intersystems-irispython 5.4.0 (the repo's .venv) and 5.4.1 (a fresh `pip install`) decode a `$DOUBLE` stored in a
VARCHAR column differently (docs/dev/driver-drift.md), so a green .venv run says nothing about what users see. The
script's file list is checked here: every file exists and is an E2E file that reads node or relationship values; the
script runs each one per interpreter, and the TCK when given a namespace.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/dev/driver_matrix.sh"
LIST = REPO / "scripts/dev/driver_matrix_e2e.txt"


def _files() -> list:
    return [ln.strip() for ln in LIST.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def test_the_script_is_there_and_executable():
    assert SCRIPT.is_file() and os.access(SCRIPT, os.X_OK)


def test_every_listed_file_is_an_e2e_test_that_exists():
    files = _files()
    assert len(files) >= 10, files
    for rel in files:
        assert re.fullmatch(r"tests/e2e/test_\w+\.py", rel), rel
        assert (REPO / rel).is_file(), rel


def test_the_list_holds_the_typed_read_files():
    """R218 (typed node reads), R248 (whole-node values), R264 (embed_nodes text): the three that measured the drift."""
    files = set(_files())
    for rel in ("tests/e2e/test_460_r218_typed_node_reads_e2e.py", "tests/e2e/test_460_r248_typed_node_values_e2e.py",
                "tests/e2e/test_460_r264_embed_text_typed_e2e.py"):
        assert rel in files, rel


def test_the_script_runs_both_interpreters_and_the_tck_on_request():
    text = SCRIPT.read_text()
    assert "IVG_PY_REPO" in text and "IVG_PY_FRESH" in text
    assert "scripts/tck/run_all.sh" in text and "summarize.py" in text
    assert "/tmp" not in text  # results and venvs live under ~/.cache (never /tmp)


def test_usage_without_arguments_says_how_to_run_it():
    out = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 2
    assert "usage: scripts/dev/driver_matrix.sh" in out.stderr
