"""4.6.0 R162: `bfs_vector_rerank` says what it does on IVG: raise `NotImplementedError`.

No store IVG ships implements `execute_bfs_vector_rerank` (R145 made the call raise rather than fail with an
AttributeError), yet the docstring, and so docs/API_REFERENCE.md, described a working graph-filtered search with an
example answer, a recall figure and "~28ms for hops=1". The summary now says it is not implemented on IVG's store;
no timing or recall claim is left; and the call on the engine's real store raises as the docstring says.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine

REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "API_REFERENCE.md"


def test_the_summary_says_it_is_not_implemented_on_ivgs_store():
    summary = inspect.getdoc(IRISGraphEngine.bfs_vector_rerank).split("\n\n", 1)[0]
    assert "Not implemented on IVG's own store" in summary and "NotImplementedError" in summary, summary


def test_the_docstring_makes_no_timing_or_recall_claim():
    doc = inspect.getdoc(IRISGraphEngine.bfs_vector_rerank)
    assert not re.search(r"\d\s*ms\b|millisecond|recall|planned for", doc, re.I), doc


def test_the_api_reference_carries_the_summary():
    text = REFERENCE.read_text()
    entry = text[text.index("#### `bfs_vector_rerank`"):]
    entry = entry[: entry.index("\n#### ", 1)]
    assert "Not implemented on IVG's own store" in entry, entry


def test_the_call_raises_as_documented_on_the_engines_own_store():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    with pytest.raises(NotImplementedError, match="execute_bfs_vector_rerank"):
        engine.bfs_vector_rerank("a", [0.1, 0.2, 0.3, 0.4])
