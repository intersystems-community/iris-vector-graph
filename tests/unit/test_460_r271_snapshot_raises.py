"""4.6.0 R271: ``save_snapshot`` raises on a table or global it cannot read, and ``restore_snapshot`` on a global it
cannot write, naming it and the error.

``save_snapshot`` caught every read error: a table was recorded with 0 rows, the node vectors as absent, the edge
vectors, the registry and a global left out, and the whole globals layer skipped on a warning. Each archive said
less than the database held, and a restore from it erased the database and put back that much less. Likewise
``restore_snapshot`` logged a failed global import and returned as if it had restored. The one read that may fail
quietly is of a table that does not exist where its absence is a fact: a FHIR table (made only when a FHIR graph
is), and a routed table the registry names but the database lost (archived as a route of 0 rows, restored empty).

No IRIS: the connection's cursor and Native API are fakes. The live check is
``tests/e2e/test_460_r271_snapshot_unreadable_table_e2e.py``.
"""

from __future__ import annotations

import json
import re
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.snapshot import SnapshotReadError
from iris_vector_graph.engine import IRISGraphEngine

REFUSED = "<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]"
MISSING = "<SQL ERROR>; Details: [SQLCODE: <-30>:<Table or view not found>]"
_FROM = re.compile(r"FROM\s+(\S+)", re.I)


class _Cursor:
    """Every table empty except the registry rows given; ``errors`` maps a table to the error its SELECT raises."""

    def __init__(self, errors=None, registry=()):
        self.errors, self.registry, self.description, self._rows = dict(errors or {}), list(registry), [], []

    def execute(self, sql, params=None):
        table = _FROM.search(sql).group(1)
        if table in self.errors:
            raise Exception(self.errors[table])
        self.description = [("node_id", None)]
        self._rows = list(self.registry) if table.endswith("embedding_registry") and "table_name" in sql else []

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def _engine(cursor):
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    conn.cursor.return_value = cursor
    return eng


def _save(eng, path, layers=("sql",), iris_obj=None):
    with patch("iris_vector_graph.schema._call_classmethod", return_value="2026.3"), patch.object(
        eng, "_iris_obj", return_value=iris_obj or MagicMock(nextSubscript=MagicMock(return_value=None))
    ):
        return eng.save_snapshot(str(path), layers=list(layers))


@pytest.mark.parametrize(
    "table",
    [
        "Graph_KG.nodes",
        "Graph_KG.rdf_props",
        "Graph_KG.embedding_registry",
        "Graph_KG.kg_NodeEmbeddings",
        "Graph_KG.kg_EdgeEmbeddings",
    ],
)
def test_a_refused_table_raises_and_writes_nothing(tmp_path, table):
    path = tmp_path / "snap.zip"
    with pytest.raises(SnapshotReadError, match=re.escape(table) + r".*-99"):
        _save(_engine(_Cursor({table: REFUSED})), path)
    assert not path.exists()


def test_it_is_a_runtime_error():
    assert issubclass(SnapshotReadError, RuntimeError)


def test_a_missing_core_table_raises(tmp_path):
    with pytest.raises(SnapshotReadError, match=r"Graph_KG\.rdf_edges.*-30"):
        _save(_engine(_Cursor({"Graph_KG.rdf_edges": MISSING})), tmp_path / "snap.zip")


def test_an_absent_fhir_table_is_left_out_not_counted(tmp_path):
    out = _save(_engine(_Cursor({"Graph_KG.fhir_unresolved": MISSING})), tmp_path / "snap.zip")
    assert "Graph_KG.fhir_unresolved" not in out["tables"]
    assert out["tables"]["Graph_KG.fhir_definitions"] == 0
    with zipfile.ZipFile(out["path"]) as zf:
        assert "sql/Graph_KG_fhir_unresolved.ndjson" not in zf.namelist()


def test_a_refused_fhir_table_raises(tmp_path):
    with pytest.raises(SnapshotReadError, match=r"fhir_unresolved.*-99"):
        _save(_engine(_Cursor({"Graph_KG.fhir_unresolved": REFUSED})), tmp_path / "snap.zip")


ROUTE = ("kg_emb_0123456789abcdef", "g1", "m", 4, "DOUBLE")


def test_a_lost_routed_table_is_a_route_of_zero_rows(tmp_path):
    cur = _Cursor({"Graph_KG.kg_emb_0123456789abcdef": MISSING}, registry=[ROUTE])
    out = _save(_engine(cur), tmp_path / "snap.zip")
    with zipfile.ZipFile(out["path"]) as zf:
        routes = json.loads(zf.read("metadata.json"))["routed_embedding_tables"]
    assert routes["Graph_KG.kg_emb_0123456789abcdef"]["rows"] == 0


def test_a_refused_routed_table_raises(tmp_path):
    cur = _Cursor({"Graph_KG.kg_emb_0123456789abcdef": REFUSED}, registry=[ROUTE])
    with pytest.raises(SnapshotReadError, match=r"kg_emb_0123456789abcdef.*-99"):
        _save(_engine(cur), tmp_path / "snap.zip")


def test_an_unreadable_global_raises(tmp_path):
    iris_obj = MagicMock()
    iris_obj.nextSubscript.side_effect = RuntimeError("<PROTECT> ^KG")
    path = tmp_path / "snap.zip"
    with pytest.raises(SnapshotReadError, match=r"\^KG.*<PROTECT>"):
        _save(_engine(_Cursor()), path, layers=("sql", "globals"), iris_obj=iris_obj)
    assert not path.exists()


def test_no_native_api_with_the_globals_layer_raises(tmp_path):
    eng = _engine(_Cursor())
    with patch("iris_vector_graph.schema._call_classmethod", return_value="2026.3"), patch.object(
        eng, "_iris_obj", side_effect=RuntimeError("no Native API")
    ), pytest.raises(SnapshotReadError, match="no Native API"):
        eng.save_snapshot(str(tmp_path / "snap.zip"))


def test_an_absent_global_is_no_lines_not_an_error(tmp_path):
    out = _save(_engine(_Cursor()), tmp_path / "snap.zip", layers=("sql", "globals"))
    assert out["globals"] == []


# ------------------------------------------------------------------ restore


def _archive(tmp_path):
    path = tmp_path / "in.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"version": "1.1", "tables": {}, "globals": {"KG": {}}}))
        zf.writestr("globals/KG.ndjson", json.dumps({"k": ["out", 0, "a", "R", "b"], "v": "1"}))
    return path


def test_restore_raises_on_a_global_it_cannot_write(tmp_path):
    eng = _engine(_Cursor())
    iris_obj = MagicMock()
    iris_obj.get.return_value = 0
    with patch.object(eng, "_iris_obj", return_value=iris_obj), patch.object(eng, "erase_all"), patch.object(
        eng, "_import_global_from_ndjson", side_effect=RuntimeError("<PROTECT> ^KG")
    ), pytest.raises(RuntimeError, match=r"\^KG.*<PROTECT>"):
        eng.restore_snapshot(str(_archive(tmp_path)))


@pytest.mark.parametrize(
    "message, missing",
    [
        (MISSING, True),
        ("SQLCODE=-30 table not found", True),
        (REFUSED, False),
        ("<SQL ERROR>; Details: [SQLCODE: <-306>:<Column not found>]", False),
        ("SQLCODE=-307", False),
    ],
)
def test_only_sqlcode_minus_30_is_a_missing_table(message, missing):
    from iris_vector_graph._engine.snapshot import _table_missing

    assert _table_missing(Exception(message)) is missing
