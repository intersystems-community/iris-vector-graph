"""4.6.0 R276: ``restore_snapshot`` raises when a commit fails, naming the table whose rows it was committing.

Each table's rows were committed in ``try: self.conn.commit() except Exception: pass``, so a commit IRIS refused
(a lock timeout, a lost connection, a constraint deferred to commit) left that table's rows unwritten while the
result counted them as restored. The restore now raises ``RuntimeError`` naming the table and IRIS's error; the
tables committed before it stay written.

The trigger is explicit: the fake cursor records the table of the last statement it ran, and the connection's
``commit`` raises when that table is the one under test. Every other commit succeeds, so each case shows the one
table it names.
"""

from __future__ import annotations

import json
import re
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

LOCK = "<SQL ERROR>; Details: [SQLCODE: <-110>:<Locking conflict in filing>]"
_TARGET = re.compile(r"(?:INSERT INTO|CREATE TABLE(?:\s+IF NOT EXISTS)?)\s+(\S+)", re.I)
ROUTED = "Graph_KG.kg_emb_0123456789abcdef"


class _Cursor:
    def __init__(self):
        self.last = None

    def execute(self, sql, params=None):
        m = _TARGET.search(sql)
        if m:
            self.last = m.group(1).split("(")[0]

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


def _archive(tmp_path):
    rows = {
        "Graph_KG.nodes": {"graph_id": "", "node_id": "a"},
        "Graph_KG.rdf_props": {"graph_id": "", "s": "a", "key": "k", "val": "v"},
        "Graph_KG.fhir_definitions": {"url": "http://x", "kind": "ValueSet"},
        "Graph_KG.kg_NodeEmbeddings": {"graph_id": "", "node_id": "a", "emb": "1,0,0,0"},
        "Graph_KG.kg_EdgeEmbeddings": {"s": "a", "p": "R", "o_id": "b", "emb": "1,0,0,0"},
        ROUTED: {"graph_id": "g1", "node_id": "a", "emb": "1,0,0,0"},
    }
    meta = {
        "version": "1.1",
        "tables": {},
        "routed_embedding_tables": {ROUTED: {"graph_id": "g1", "dimension": 4, "dtype": "DOUBLE", "rows": 1}},
    }
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps(meta))
        for table, row in rows.items():
            zf.writestr(f"sql/{table.replace('Graph_KG.', 'Graph_KG_')}.ndjson", json.dumps(row))
    return path


def _restore(tmp_path, refuse):
    """Restore the archive with the commit after a statement on ``refuse`` failing; return the result."""
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    cur = _Cursor()
    conn.cursor.return_value = cur

    def commit():
        if cur.last == refuse:
            raise RuntimeError(LOCK)

    conn.commit.side_effect = commit
    with patch.object(eng, "erase_all"), patch.object(eng, "_create_routed_table"), patch.object(
        eng, "_attempt_route_index", return_value=("none", None)
    ), patch.object(eng, "_record_route_index_state"), patch.object(eng, "invalidate_route_cache"):
        return eng.restore_snapshot(str(_archive(tmp_path)))


@pytest.mark.parametrize(
    "table",
    [
        "Graph_KG.nodes",
        "Graph_KG.rdf_props",
        "Graph_KG.fhir_definitions",
        "Graph_KG.kg_NodeEmbeddings",
        ROUTED,
        "Graph_KG.kg_EdgeEmbeddings",
    ],
)
def test_a_refused_commit_raises_naming_the_table(tmp_path, table):
    with pytest.raises(RuntimeError, match=re.escape(table) + r".*-110"):
        _restore(tmp_path, table)


def test_a_refused_fhir_ddl_commit_raises(tmp_path):
    """The FHIR tables' DDL is committed before their rows; the last statement is the last CREATE TABLE."""
    with pytest.raises(RuntimeError, match=r"FHIR tables.*-110"):
        _restore(tmp_path, "Graph_KG.fhir_canonical_refs")


def test_the_tables_before_it_are_named_as_restored(tmp_path):
    with pytest.raises(RuntimeError) as info:
        _restore(tmp_path, "Graph_KG.rdf_props")
    assert "Graph_KG.nodes" in str(info.value).split("rdf_props", 1)[1]


def test_every_commit_succeeding_restores_every_table(tmp_path):
    out = _restore(tmp_path, refuse="nothing")
    assert out["restored_tables"] == {
        "Graph_KG.nodes": 1,
        "Graph_KG.rdf_props": 1,
        "Graph_KG.fhir_definitions": 1,
        "Graph_KG.kg_NodeEmbeddings": 1,
        ROUTED: 1,
        "Graph_KG.kg_EdgeEmbeddings": 1,
    }
    assert out["failed_rows"] == {}
