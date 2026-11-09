"""4.6.5: ADMIN_GUIDE §3 says how a read refused for a missing privilege is logged, as the code logs it.

The guide said it "logs it only at DEBUG (logger `iris_vector_graph.stores.iris_sql_store`, `execute_sql error: …`), so
an alert on WARNING does not see it". Since 4.6.3 it is a WARNING (tests/unit/test_463_refused_read_warns.py); the guide
kept the 4.6.2 sentence. The logger name and the message the guide quotes are taken here from the record the code
writes, not typed again.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import MagicMock

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "ADMIN_GUIDE.md"
REFUSED = ("<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]\r\n[Location: <Prepare>]\r\n"
           "[%msg: <User R is not privileged for the operation>]")


def _logged_record(caplog) -> logging.LogRecord:
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError(REFUSED)
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        IRISGraphStore(conn).execute_sql("SELECT node_id FROM Graph_KG.nodes", [])
    records = [r for r in caplog.records if r.name.startswith("iris_vector_graph")]
    assert len(records) == 1, [(r.levelname, r.getMessage()) for r in records]
    return records[0]


def _paragraph() -> str:
    """The bullet of §3 that describes a refused read, on one line."""
    text = GUIDE.read_text()
    start = text.index("A read refused for a missing privilege does not raise")
    return " ".join(text[start:].split("\n- ", 1)[0].split())


def test_the_guide_names_the_level_the_logger_and_the_message_the_code_logs(caplog):
    record = _logged_record(caplog)
    assert record.levelno == logging.WARNING
    # the part of the message before IRIS's own text, which varies: "execute_sql refused for a missing privilege
    # (SQLCODE -99); the result's error holds it"
    stem = record.getMessage().split(": ", 1)[0]
    paragraph = _paragraph()
    assert "WARNING" in paragraph and "only at DEBUG" not in paragraph, paragraph
    assert f"`{record.name}`" in paragraph, paragraph
    assert stem in paragraph, paragraph
    assert "an alert on WARNING sees it" in paragraph, paragraph


def test_the_guide_keeps_other_failed_reads_at_debug():
    assert re.search(r"other failed reads .*DEBUG|DEBUG.*other failed reads", _paragraph()), _paragraph()
