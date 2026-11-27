"""4.7.2: `import_rdf`'s refused-batch message says what is true of the batches before it.

Outside a caller's transaction each batch commits as it goes, so "the batches before this one are committed". Inside one
(4.7.0: each batch is IVG's own transaction nested in the caller's) nothing is committed: those batches are open in the
caller's transaction, which the caller's commit or rollback decides. 4.7.0 said "committed" for both.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from iris_vector_graph.errors import GraphWriteError
from tests.unit.test_460_r276_except_audit import LOCK, _Cursor, _engine


def _nt(tmp_path):
    pytest.importorskip("rdflib")
    nt = tmp_path / "x.nt"
    nt.write_text("<http://x/a> <http://x/knows> <http://x/b> .\n")
    return str(nt)


def test_outside_a_callers_transaction_the_earlier_batches_are_committed(tmp_path):
    eng = _engine(_Cursor(fail=r"^COMMIT$", error=LOCK))
    with pytest.raises(GraphWriteError) as raised:
        eng.import_rdf(_nt(tmp_path))
    assert "the batches before this one are committed" in str(raised.value)


def test_inside_a_callers_transaction_the_earlier_batches_are_still_open(tmp_path):
    eng = _engine(_Cursor(fail=r"^COMMIT$", error=LOCK))
    eng._adjacency_indexed_cached = True
    with patch("iris_vector_graph._write_txn.caller_transaction_level", return_value=1):
        with pytest.raises(GraphWriteError) as raised:
            eng.import_rdf(_nt(tmp_path))
    text = str(raised.value)
    assert "committed" not in text.replace("nothing is committed", ""), text
    assert "still open in the caller's transaction" in text, text
    assert "1 triples read so far" in text, text
