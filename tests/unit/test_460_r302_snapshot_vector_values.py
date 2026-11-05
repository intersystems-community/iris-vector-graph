"""4.6.0 R302: save_snapshot reads the vector index globals' values on the server.

^IVF, ^PLAID and ^VecIdx hold `$vector` values (PLAID's docPacked a $List of them). The Native API's getters raise
`<LIST ERROR> ... type detected : 16` on one, so save_snapshot raised on every namespace with such an index. Their
values are now read by `Graph.KG.Snapshot.NodeValueJSON` and written back by `Graph.KG.Snapshot.SetNodeJSON`; the
other globals keep the Native API's `get`/`set`. The live round trip is tests/unit/test_snapshot.py
(TestSnapshotCarriesVectorIndexes).
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph._engine.snapshot import VECTOR_VALUE_GLOBALS, SnapshotMixin


class Native:
    """The Native API calls the export and import make, over a dict of subscript tuples. `nextSubscript` answers
    strings ('0', not 0) and `get` raises on a node the server would hold as a `$vector`, as the driver does."""

    def __init__(self, nodes: dict):
        self.nodes = {(g, *k): v for (g, *k), v in nodes.items()}
        self.calls: list = []

    def nextSubscript(self, reversed_, gname, *subs):
        *parent, cur = subs
        depth = len(parent) + 1
        children = sorted({k[depth] for k in self.nodes if k[0] == gname and len(k) > depth
                           and list(k[1:depth]) == parent})
        later = [c for c in children if c > cur]
        return later[0] if later else None

    def get(self, gname, *subs):
        v = self.nodes.get((gname, *subs))
        if isinstance(v, dict):
            raise RuntimeError("<LIST ERROR> Incorrect list format, unsupported type for IRISList; type detected : 16")
        return v

    def set(self, value, gname, *subs):
        self.calls.append(("set", gname, list(subs), value))

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, *args))
        assert (cls, method) == ("Graph.KG.Snapshot", "NodeValueJSON")
        gname, subs = args[0], json.loads(args[1])
        v = self.nodes.get((gname, *subs))
        if v is None:
            return ""
        return json.dumps(v if isinstance(v, dict) else {"v": str(v)})

    def classMethodVoid(self, cls, method, *args):
        self.calls.append((cls, method, *args))


def _export(native, gname):
    return [json.loads(line) for line in SnapshotMixin.__new__(SnapshotMixin)._export_global_to_ndjson(native, gname, [])]


def test_the_vector_globals_are_the_three_that_hold_vectors():
    assert set(VECTOR_VALUE_GLOBALS) == {"^IVF", "^PLAID", "^VecIdx"}


def test_a_vector_global_is_read_on_the_server_and_its_vectors_keep_their_values():
    native = Native({
        ("^IVF", "i", "centroid", "0"): {"vec": "double", "v": [1.5, -2.0]},
        ("^IVF", "i", "cfg", "dim"): 2,
    })
    assert _export(native, "^IVF") == [
        {"k": ["i", "centroid", "0"], "vec": "double", "v": [1.5, -2.0]},
        {"k": ["i", "cfg", "dim"], "v": "2"},
    ]
    assert not any(c[0] == "set" for c in native.calls)


def test_another_global_keeps_the_native_get():
    native = Native({("^KG", "out", "0", "a"): 1})
    assert _export(native, "^KG") == [{"k": ["out", "0", "a"], "v": "1"}]
    assert native.calls == []


def test_a_vector_line_is_written_back_by_the_server_and_a_plain_line_by_set():
    native = Native({})
    eng = SnapshotMixin.__new__(SnapshotMixin)
    eng._adjacency_indexed = False
    ndjson = "\n".join([
        json.dumps({"k": ["i", "centroid", "0"], "vec": "double", "v": [1.5, -2.0]}),
        json.dumps({"k": ["p", "docPacked", "d"], "list": [{"vec": "double", "v": [0.5]}]}),
        json.dumps({"k": ["i", "cfg", "dim"], "v": "2"}),
    ])
    assert eng._import_global_from_ndjson(native, "^IVF", ndjson) == 3
    assert native.calls == [
        ("Graph.KG.Snapshot", "SetNodeJSON", "^IVF", '["i", "centroid", "0"]', '{"vec": "double", "v": [1.5, -2.0]}'),
        ("Graph.KG.Snapshot", "SetNodeJSON", "^IVF", '["p", "docPacked", "d"]',
         '{"list": [{"vec": "double", "v": [0.5]}]}'),
        ("set", "^IVF", ["i", "cfg", "dim"], "2"),
    ]


@pytest.mark.parametrize("method", ["NodeValueJSON", "SetNodeJSON"])
def test_the_class_methods_exist_in_the_snapshot_class(method):
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/Snapshot.cls").read_text()
    assert f"ClassMethod {method}(" in text
