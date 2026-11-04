"""4.6.0 R207 (walkthrough 1, P3) and R225 (walkthrough 2, papercut 14): `status().report()` says what it found.

- With `IVG_DISABLE_ARNO=1`, `acceleration_label` read `ObjectScript (Arno not loaded)` and `arno.lib_path` named the
  default path, exactly as with the library absent: an operator could not see that the switch took effect. The label
  names the switch, and no library path is given, since none was tried.
- `Arno library: /usr/irissys/mgr/libarno_callout.so (not loaded)` was printed for a file that is not on the server.
  The line says the file is not there (`arno.lib_found` False).
- `Vector index: empty (6 indexed)` was printed for six stored vectors and no ANN index. It says there is no ANN index
  and the vectors are searched by a scan.
"""

from __future__ import annotations

from iris_vector_graph.status import ArnoStatus, EngineStatus, IndexInventory, TableCounts


def _report(**arno):
    return EngineStatus(arno=ArnoStatus(**arno)).report()


def test_the_switch_is_named_in_the_label():
    s = EngineStatus(arno=ArnoStatus(disabled=True))
    assert s.acceleration_label == "ObjectScript (Arno disabled: IVG_DISABLE_ARNO=1)"


def test_an_absent_library_reads_as_not_loaded():
    assert EngineStatus(arno=ArnoStatus(lib_path="/x.so", lib_found=False)).acceleration_label == (
        "ObjectScript (Arno not loaded)"
    )


def test_a_missing_file_is_said_to_be_missing():
    report = _report(lib_path="/usr/irissys/mgr/libarno_callout.so", lib_found=False)
    assert "Arno library:    /usr/irissys/mgr/libarno_callout.so (not loaded: no such file on the server)" in report


def test_a_file_that_is_there_and_did_not_load_says_not_loaded():
    report = _report(lib_path="/usr/irissys/mgr/libarno_callout.so", lib_found=True)
    assert "Arno library:    /usr/irissys/mgr/libarno_callout.so (not loaded)" in report


def test_a_loaded_library_says_loaded():
    report = _report(loaded=True, version="arno-callout 1.2.1+abc", lib_path="/l.so", lib_found=True)
    assert "Acceleration:    Arno 1.2.1+abc" in report
    assert "Arno library:    /l.so (loaded)" in report


def test_vectors_without_an_ann_index_are_not_called_empty():
    s = EngineStatus(tables=TableCounts(node_embeddings=6))
    assert s.vector_index_state == "empty"  # the state's value is unchanged; the report says what it means
    assert "Vector index:    no ANN index (6 vectors, searched by a scan)" in s.report()


def test_an_indexed_table_still_reads_ready():
    s = EngineStatus(tables=TableCounts(node_embeddings=6), indexes=IndexInventory(hnsw_built=True))
    assert "Vector index:    ready (6 indexed)" in s.report()


def _status_with(monkeypatch, *, disabled: bool, exists: int):
    """The engine's status() over a namespace with IVG's classes and no Arno library loaded."""
    from iris_vector_graph._engine import admin

    calls = []

    class Native:
        def classMethodValue(self, cls, method, *args):
            calls.append((cls, method))
            if method == "LibPath":
                return "/usr/irissys/mgr/libarno_callout.so"
            if method == "LibExists":
                return exists
            return ""

    if disabled:
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    else:
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    return admin.arno_status(Native(), deployed=True, loaded=False, version="", capabilities={}), calls


def test_status_under_the_switch_names_no_library_and_asks_the_server_nothing(monkeypatch):
    arno, calls = _status_with(monkeypatch, disabled=True, exists=1)
    assert arno.disabled is True and arno.lib_path == "" and arno.lib_found is None
    assert calls == []


def test_status_asks_the_server_whether_the_file_is_there(monkeypatch):
    arno, _ = _status_with(monkeypatch, disabled=False, exists=0)
    assert arno.lib_path == "/usr/irissys/mgr/libarno_callout.so"
    assert arno.lib_found is False and arno.disabled is False


def test_an_older_server_without_lib_exists_leaves_it_unknown(monkeypatch):
    from iris_vector_graph._engine import admin

    class Native:
        def classMethodValue(self, cls, method, *args):
            if method == "LibExists":
                raise RuntimeError("<METHOD DOES NOT EXIST> *LibExists,Graph.KG.ArnoAccel")
            return "/l.so"

    monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    arno = admin.arno_status(Native(), deployed=True, loaded=False, version="", capabilities={})
    assert arno.lib_path == "/l.so" and arno.lib_found is None

