"""4.7.2, live: setup names the indexes it could not create, and an engine on tables without classes says so.

Two scratch namespaces, each with a database of its own (`scripts/dev/make_scratch_namespace.py`):

- `IVG_SETUP_VIS_NS`, IVG installed: whatever the server accepts or refuses, every optional index is either in the
  catalog or in `status["skipped_indexes"]`, and nothing listed is in the catalog. On IRIS 2026.1 and on the 2026.3 AI
  build `idx_edges_confidence` is refused (no expression index in SQL DDL), so it is the listed one; on a server that
  accepts it the list is empty and the index exists. Either way the reply agrees with the catalog.
- `IVG_NOCLASSES_NS`, made with `--no-install` and given the tables by `initialize_schema(auto_deploy_objectscript=
  False)`: an engine's first read of its capabilities logs one WARNING naming the missing classes, and
  `missing_classes` lists the packaged set with `Graph.KG.PageRank` first. (A namespace with no tables, and one with
  the classes, are the unit tests': tests/unit/test_472_classes_missing_warning.py.)

    IVG_SETUP_VIS_NS=IVG470W2 IVG_NOCLASSES_NS=IVG470W2B pytest tests/e2e/test_472_setup_visibility_e2e.py
"""

from __future__ import annotations

import logging
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema
from tests.e2e import ns262, scratch_vars

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    scratch_vars.requires("IVG_SETUP_VIS_NS", "IVG_NOCLASSES_NS"),
]

OPTIONAL = {
    "idx_edges_confidence": "rdf_edges",
    "idx_props_val_ifind": "rdf_props",
    "idx_docs_text_ifind": "docs",
}


def _rows(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or [])
    return cur.fetchall()


def _indexes(conn) -> set:
    rows = _rows(
        conn,
        "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = 'Graph_KG' "
        "AND TABLE_NAME IN ('rdf_edges', 'rdf_props', 'docs')",
    )
    return {str(r[0]).lower() for r in rows}


def _dimension(conn) -> int:
    return GraphSchema.get_embedding_dimension(conn.cursor()) or 768


@pytest.fixture(scope="module")
def installed():
    conn = ns262.connect(os.environ["IVG_SETUP_VIS_NS"])
    engine = IRISGraphEngine(conn, embedding_dimension=_dimension(conn))
    status = engine.initialize_schema()
    yield conn, status
    conn.close()


def test_every_optional_index_is_in_the_catalog_or_in_the_reply(installed):
    conn, status = installed
    present = _indexes(conn)
    listed = {entry["index"] for entry in status["skipped_indexes"]}
    for name in OPTIONAL:
        assert (name in present) != (name in listed), (name, name in present, name in listed)


def test_nothing_listed_exists_and_each_entry_is_whole(installed):
    conn, status = installed
    present = _indexes(conn)
    for entry in status["skipped_indexes"]:
        assert entry["index"].lower() not in present
        assert set(entry) == {"index", "table", "reason"}
        assert entry["table"].startswith("Graph_KG.")
        assert entry["reason"] and "\n" not in entry["reason"] and len(entry["reason"]) <= 300


def test_a_second_run_lists_the_same_indexes(installed):
    conn, first = installed
    engine = IRISGraphEngine(conn, embedding_dimension=_dimension(conn))
    second = engine.initialize_schema()
    assert sorted(e["index"] for e in second["skipped_indexes"]) == sorted(e["index"] for e in first["skipped_indexes"])


def test_the_confidence_index_refusal_is_the_servers_own_words_when_it_is_refused(installed):
    _, status = installed
    refused = [e for e in status["skipped_indexes"] if e["index"] == "idx_edges_confidence"]
    if not refused:
        pytest.skip("this server accepts the expression index; nothing was refused")
    assert "SQLCODE" in refused[0]["reason"]


def test_tables_without_classes_warn_once_and_name_the_classes(caplog):
    conn = ns262.connect(os.environ["IVG_NOCLASSES_NS"])
    try:
        IRISGraphEngine(conn, embedding_dimension=8).initialize_schema(auto_deploy_objectscript=False)
        engine = IRISGraphEngine(conn, embedding_dimension=8)
        with caplog.at_level(logging.INFO, logger="iris_vector_graph.engine"):
            caps = engine.capabilities
            engine.capabilities = None
            engine.capabilities
        lines = [r.getMessage() for r in caplog.records
                 if r.levelno == logging.WARNING and r.name == "iris_vector_graph.engine"]
        assert len(lines) == 1, lines
        assert "Graph.KG.PageRank" in lines[0]
        assert caps.objectscript_deployed is False
        assert caps.missing_classes[0] == "Graph.KG.PageRank"
        assert len(caps.missing_classes) > 50
    finally:
        conn.close()
