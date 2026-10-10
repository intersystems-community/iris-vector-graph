"""Line coverage for Graph.KG.Snapshot: the server-side file and global-export helpers.

The helpers work on the IRIS host's own filesystem, so every path lives under a unique
directory in /tmp inside the container, removed at the end. The exported global is a
throwaway ^IVGSnapTest.
"""

from __future__ import annotations

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SN = "Graph.KG.Snapshot"


@pytest.fixture
def snap(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    obj = eng._store._iris_obj()
    root = f"/tmp/ivg_snap_{uuid.uuid4().hex[:8]}/"

    class Handle:
        dir = root

        def value(self, method, *args):
            out = obj.classMethodValue(SN, method, *args)
            return "" if out is None else out

        def void(self, method, *args):
            obj.classMethodVoid(SN, method, *args)

    yield Handle()
    obj.kill("IVGSnapTest")
    obj.classMethodValue(SN, "DeleteDir", root)


def test_file_exists_and_size_follow_a_written_file(snap):
    path = snap.dir + "a.txt"
    snap.value("ExportGlobals", snap.dir, "[]")  # creates the directory chain
    assert snap.value("FileExists", path) == 0
    snap.void("WriteFileChunk", path, "hello")
    assert snap.value("FileExists", path) == 1
    assert snap.value("GetFileSize", path) == 5


def test_chunks_append_and_read_back_from_an_offset(snap):
    path = snap.dir + "b.txt"
    snap.value("ExportGlobals", snap.dir, "[]")  # creates the directory chain
    snap.void("WriteFileChunk", path, "abcdef")
    snap.void("WriteFileChunk", path, "ghi", 1)
    assert snap.value("ReadFileChunk", path) == "abcdefghi"
    assert snap.value("ReadFileChunk", path, 3, 4) == "defg"
    # a plain write rewinds and replaces what was there
    snap.void("WriteFileChunk", path, "Z")
    assert snap.value("ReadFileChunk", path).startswith("Z")


def test_export_then_import_round_trips_a_global(snap, iris_connection):
    obj = IRISGraphEngine(iris_connection, embedding_dimension=768)._store._iris_obj()
    obj.set("v1", "IVGSnapTest", "a")
    obj.set("v2", "IVGSnapTest", "b", 1)
    exported = json.loads(snap.value("ExportGlobals", snap.dir, json.dumps([{"name": "IVGSnapTest"}, {"name": ""}])))
    assert exported == [snap.dir + "IVGSnapTest.gof"]
    assert snap.value("FileExists", exported[0]) == 1
    obj.kill("IVGSnapTest")
    assert json.loads(snap.value("ImportGlobals", snap.dir)) == {"imported": 1}
    assert obj.get("IVGSnapTest", "a") == "v1"
    assert obj.get("IVGSnapTest", "b", 1) == "v2"


def test_import_from_an_empty_directory_imports_nothing(snap):
    snap.value("ExportGlobals", snap.dir, "[]")
    assert json.loads(snap.value("ImportGlobals", snap.dir)) == {"imported": 0}


def test_delete_dir_removes_the_tree(snap):
    snap.value("ExportGlobals", snap.dir, "[]")
    snap.void("WriteFileChunk", snap.dir + "c.txt", "x")
    assert snap.value("DeleteDir", snap.dir) == 1
    assert snap.value("FileExists", snap.dir + "c.txt") == 0
