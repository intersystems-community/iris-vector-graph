"""4.6.0 R175: the Arno adjacency upload walks the `^KG` layout spec 214 wrote, and its failures are not swallowed.

`^KG("out", graph, s, p, o)` (graph: integer 0 for the default graph; Graph.KG.Adjacency is the one writer). The
server-side function `ivg_arno_build_adj` walked `^KG("out", "", 0, ...)`, the layout before spec 214, so every call
failed with `<SUBSCRIPT> ^KG("out","")`; `build_kg_adjacency_chunked` caught every exception and walked `^KG` over the
Native API instead, so the fast path never ran and nothing said so.

The Native API here is a dict of subscript tuples (`^KG`'s nodes); the cursor records the statements and the chunks.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.stores import arno_bridge

#: A 3-cycle in the default graph (key 0) and one edge in graph "g2", which the default-graph walk leaves out.
KG = {
    ("out", 0, "a", "R", "b"): 1, ("out", 0, "b", "R", "c"): 1, ("out", 0, "c", "R", "a"): 1,
    ("in", 0, "b", "R", "a"): 1, ("in", 0, "c", "R", "b"): 1, ("in", 0, "a", "R", "c"): 1,
    ("out", "g2", "x", "R", "y"): 1, ("in", "g2", "y", "R", "x"): 1,
}


class _Native:
    """`nextSubscript` over KG, as IRIS orders it (integers before strings)."""

    def __init__(self, kg):
        self.kg = kg

    def nextSubscript(self, reverse, name, *subs):
        assert name == "^KG" and not reverse
        *prefix, after = subs
        if "" in prefix:
            raise RuntimeError(f'<SUBSCRIPT> ^KG{tuple(subs)}')  # what IRIS does with a "" mid-subscript
        depth = len(prefix)
        keys = sorted({k[depth] for k in self.kg if k[:depth] == tuple(prefix) and len(k) > depth},
                      key=lambda v: (isinstance(v, str), v))
        for k in keys:
            if after == "" or (isinstance(k, str), k) > (isinstance(after, str), after):
                return k
        return None


class _Cursor:
    def __init__(self, conn):
        self.conn, self._row = conn, None

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self._row = None
        if "ivg_arno_adj_append" in sql and params:
            self.conn.chunks.append(params[1])
            self._row = ("OK",)
        elif "ivg_arno_build_adj(" in sql and sql.lstrip().upper().startswith("SELECT"):
            self._row = (self.conn.build_status,)

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _Conn:
    def __init__(self, kg=KG, build_status="OK:3:3:1"):
        self.iris = _Native(kg)
        self.sql: list = []
        self.chunks: list = []
        self.build_status = build_status

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()
    arno_bridge._probe_cache.clear()
    monkeypatch.setattr(arno_bridge, "arno_available", lambda conn, schema="Graph_KG": True)
    arno_bridge._probe_cache[arno_bridge._conn_key(c)] = {"available": True, "lib_path": "/lib/arno.so"}
    monkeypatch.setattr(arno_bridge, "_create_routines", lambda conn, cur, ddls, schema: c.sql.extend(ddls) or [])
    yield c
    arno_bridge._probe_cache.clear()


def test_a_server_side_failure_raises_instead_of_falling_back(conn, monkeypatch):
    def broken(c, schema="Graph_KG"):
        raise RuntimeError('<SUBSCRIPT> ^KG("out","")')

    monkeypatch.setattr(arno_bridge, "_build_kg_adjacency_serverside", broken)
    with pytest.raises(RuntimeError, match="SUBSCRIPT"):
        arno_bridge.build_kg_adjacency_chunked(conn)
    assert conn.chunks == []  # the Native API walk did not run in its place


def test_an_arno_error_from_the_server_side_walk_raises_too(conn, monkeypatch):
    conn.build_status = "ERROR: dlopen"
    with pytest.raises(arno_bridge.ArnoError, match="dlopen"):
        arno_bridge.build_kg_adjacency_chunked(conn)
    assert conn.chunks == []


def test_the_server_side_function_walks_the_graph_keyed_layout(conn):
    """The function's ObjectScript reads ^KG("out", 0, src, pred, dst) and ^KG("in", 0, node), the default graph, as
    the Native API walk does; no subscript is "" (the pre-214 ^KG("out", "", 0, ...))."""
    arno_bridge._build_kg_adjacency_serverside(conn)
    ddl = next(s for s in conn.sql if "FUNCTION" in s and "ivg_arno_build_adj" in s)
    refs = re.findall(r'\^KG\(([^)]*)\)', ddl)
    assert refs, ddl
    assert all(not r.split(",")[1].strip() == '""' for r in refs), refs
    assert '"out",0,node' in ddl.replace(" ", "") and '"out",0,src,pred,dst' in ddl.replace(" ", "")
    assert '"in",0,node' in ddl.replace(" ", "")


def test_the_native_api_walk_uploads_the_default_graphs_adjacency(conn):
    """`server_side=False` is the Native API walk (what the server-side function must agree with): the 3-cycle, its
    nodes in $Order order, and not g2's edge."""
    idx_to_node, edges = arno_bridge.build_kg_adjacency_chunked(conn, server_side=False)
    assert (idx_to_node, edges) == (["a", "b", "c"], 3)
    assert "".join(conn.chunks) == "NODEMAP:3\n0=a\n1=b\n2=c\n---\n0:1\n1:2\n2:0\n"


def test_without_the_library_nothing_is_walked(monkeypatch):
    c = _Conn()
    monkeypatch.setattr(arno_bridge, "arno_available", lambda conn, schema="Graph_KG": False)
    walked = []
    monkeypatch.setattr(arno_bridge, "host_for", lambda conn: walked.append(conn))
    with pytest.raises(arno_bridge.ArnoError, match="not available"):
        arno_bridge.build_kg_adjacency_chunked(c)
    assert walked == [] and c.sql == []
