"""arno_bridge — invoke arno-callout `$ZF(-5)` rzf functions from Python (Spec 163 FR-024).

Wraps the IRIS `$ZF(-4,...)` / `$ZF(-5,...)` extension function family for
Python clients. Provides:

  * `arno_available(conn)` — probe whether `libarno_callout.so` is loaded;
    cached after first probe per connection.
  * `arno_call(conn, fn_name, *args)` — look up `fn_name` in the loaded DLL
    and invoke it via `$ZF(-5)`. Returns the JSON string result.
  * `ArnoError` — raised on `<FUNCTION DOES NOT EXIST>` (fn_id=0), library
    not loaded, or runtime error from the Rust kernel.

Bug S immunity: `$ZF(-5)` invocation does NOT route through `%SYS.DBSRV`
class lookup. Confirmed via existing arno deployments (`kg_pagerank_global`,
`kg_wcc_global`, `kg_cdlp_global`) work from external Python today.

Environment overrides:
  IVG_DISABLE_ARNO=1     — force `arno_available()` to return False
                            (used by tests to exercise the LazyKG fallback path)
  IVG_ARNO_LIB=/path     — override the default DLL path
                            (default: /usr/irissys/mgr/libarno_callout.so)
"""

from __future__ import annotations

import os
from typing import Any, Optional


DEFAULT_LIB_PATH = "/usr/irissys/mgr/libarno_callout.so"


class ArnoError(RuntimeError):
    """Raised when arno-callout invocation fails.

    Subclasses error categories:
        - LibraryNotLoaded: $ZF(-4,1,...) returned 0 / library missing
        - FunctionNotFound: $ZF(-4,3,...) returned 0 (fn_name not in GetZFTable)
        - RuntimeError: the Rust kernel returned an "ERROR: ..." string
    """


_probe_cache: dict = {}


def _conn_key(conn) -> int:
    """Return a stable identity key for a connection (id() suffices — connections are not hashable)."""
    return id(conn)


def _ensure_zf_call_function(conn) -> None:
    """Install the single multi-arg SQL function that wraps load+lookup+call.

    This is idempotent (CREATE OR REPLACE) and runs once per connection (cached
    by connection key). Bug S immunity: SQL function bodies execute as
    LANGUAGE OBJECTSCRIPT inside IRIS — they don't route through %SYS.DBSRV
    class lookup that fails for `%SYSTEM.Util.Evaluate` from external Python.
    """
    key = _conn_key(conn)
    if _probe_cache.get(key, {}).get("ddl_installed"):
        return

    ddl_calls = [
        # 0-arg version (probe only)
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_probe(libpath VARCHAR(512), fname VARCHAR(128))
RETURNS BIGINT
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 0
    set fnid = $ZF(-4,3,dllid,fname)
    quit fnid
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_call_2s(libpath VARCHAR(512), fname VARCHAR(128), a1 VARCHAR(512), a2 VARCHAR(512))
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed for "_libpath
    set fnid = $ZF(-4,3,dllid,fname)
    quit:fnid=0 "ERROR: function not found "_fname
    set result = $ZF(-5,dllid,fnid,a1,a2)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_call_kg_triangle(libpath VARCHAR(512), gname VARCHAR(64), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_triangle_count_global")
    quit:fnid=0 "ERROR: kg_triangle_count_global not found"
    set result = $ZF(-5,dllid,fnid,gname,topk)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_call_kg_scc(libpath VARCHAR(512), gname VARCHAR(64), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_scc_global")
    quit:fnid=0 "ERROR: kg_scc_global not found"
    set result = $ZF(-5,dllid,fnid,gname,topk)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_call_kg_kcore(libpath VARCHAR(512), gname VARCHAR(64), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_kcore_global")
    quit:fnid=0 "ERROR: kg_kcore_global not found"
    set result = $ZF(-5,dllid,fnid,gname,topk)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_call_kg_leiden(plib VARCHAR(512), pgname VARCHAR(64), pmaxlevels INT, pgamma DOUBLE, ptol DOUBLE, ptopk INT, pmembudget INT, pseed INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,plib)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_leiden_global")
    quit:fnid=0 "ERROR: kg_leiden_global not found"
    set result = $ZF(-5,dllid,fnid,pgname,pmaxlevels,pgamma,ptol,ptopk,pmembudget,pseed)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_triangle_json(libpath VARCHAR(512), gjson VARCHAR(32000), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_triangle_count_json")
    quit:fnid=0 "ERROR: kg_triangle_count_json not found"
    set result = $ZF(-5,dllid,fnid,gjson,topk)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_scc_json(libpath VARCHAR(512), gjson VARCHAR(32000), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_scc_json")
    quit:fnid=0 "ERROR: kg_scc_json not found"
    set result = $ZF(-5,dllid,fnid,gjson,topk)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_kcore_json(libpath VARCHAR(512), gjson VARCHAR(32000), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_kcore_json")
    quit:fnid=0 "ERROR: kg_kcore_json not found"
    set result = $ZF(-5,dllid,fnid,gjson,topk)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_leiden_json(plib VARCHAR(512), pjson VARCHAR(32000), pmaxlevels INT, pgamma DOUBLE, ptol DOUBLE, ptopk INT, pmembudget INT, pseed INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,plib)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_leiden_json")
    quit:fnid=0 "ERROR: kg_leiden_json not found"
    set result = $ZF(-5,dllid,fnid,pjson,pmaxlevels,pgamma,ptol,ptopk,pmembudget,pseed)
    quit result
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_triangle_run(libpath VARCHAR(512), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_triangle_count_run")
    quit:fnid=0 "ERROR: kg_triangle_count_run not found"
    quit $ZF(-5,dllid,fnid,topk)
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_scc_run(libpath VARCHAR(512), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_scc_run")
    quit:fnid=0 "ERROR: kg_scc_run not found"
    quit $ZF(-5,dllid,fnid,topk)
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_kcore_run(libpath VARCHAR(512), topk INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_kcore_run")
    quit:fnid=0 "ERROR: kg_kcore_run not found"
    quit $ZF(-5,dllid,fnid,topk)
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_leiden_run(plib VARCHAR(512), pmaxlevels INT, pgamma DOUBLE, ptol DOUBLE, ptopk INT, pmembudget INT, pseed INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,plib)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_leiden_run")
    quit:fnid=0 "ERROR: kg_leiden_run not found"
    quit $ZF(-5,dllid,fnid,pmaxlevels,pgamma,ptol,ptopk,pmembudget,pseed)
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_get_result_chunk(libpath VARCHAR(512), offs BIGINT, len INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_get_result_chunk")
    quit:fnid=0 "ERROR: kg_get_result_chunk not found"
    quit $ZF(-5,dllid,fnid,offs,len)
}""",
        """
CREATE OR REPLACE FUNCTION ivg_arno_zf_get_result_len(libpath VARCHAR(512))
RETURNS BIGINT
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 -1
    set fnid = $ZF(-4,3,dllid,"kg_get_result_len")
    quit:fnid=0 -1
    quit $ZF(-5,dllid,fnid)
}""",
    ]

    cur = conn.cursor()
    for ddl in ddl_calls:
        cur.execute(ddl.strip())
    conn.commit()

    if key not in _probe_cache:
        _probe_cache[key] = {}
    _probe_cache[key]["ddl_installed"] = True


def arno_available(conn) -> bool:
    """Probe whether `libarno_callout.so` is loaded and callable from this connection.

    Caches result per connection. Honors `IVG_DISABLE_ARNO=1` for forcing
    the LazyKG fallback path in tests.
    """
    if os.environ.get("IVG_DISABLE_ARNO") == "1":
        return False

    key = _conn_key(conn)
    if key in _probe_cache and "available" in _probe_cache[key]:
        return _probe_cache[key]["available"]

    lib_path = os.environ.get("IVG_ARNO_LIB", DEFAULT_LIB_PATH)

    try:
        _ensure_zf_call_function(conn)
    except Exception:
        if key not in _probe_cache:
            _probe_cache[key] = {}
        _probe_cache[key]["available"] = False
        _probe_cache[key]["lib_path"] = lib_path
        return False

    try:
        cur = conn.cursor()
        cur.execute("SELECT ivg_arno_zf_probe(?, ?)", [lib_path, "version"])
        row = cur.fetchone()
        fn_id = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        if key not in _probe_cache:
            _probe_cache[key] = {}
        _probe_cache[key]["available"] = False
        _probe_cache[key]["lib_path"] = lib_path
        return False

    available = fn_id > 0
    if key not in _probe_cache:
        _probe_cache[key] = {}
    _probe_cache[key]["available"] = available
    _probe_cache[key]["lib_path"] = lib_path
    return available


def arno_call(conn, fn_name: str, *args: Any) -> str:
    """Invoke an arno `$ZF(-5)` function by name and return its string result.

    Routes to a typed SQL wrapper based on `fn_name` to satisfy IRIS SQL
    function signature requirements (each wrapper has a fixed arg arity).
    Bug S immunity: SQL function body executes inside IRIS without going
    through `%SYS.DBSRV` class lookup.

    Args:
        conn: IRIS dbapi connection.
        fn_name: One of "kg_triangle_count_global", "kg_scc_global",
            "kg_kcore_global", "kg_leiden_global".
        *args: Arguments matching the Rust function signature.

    Returns:
        String result from the kernel (typically JSON).

    Raises:
        ArnoError: When library not loaded, function not found, or kernel
            returned a string starting with "ERROR:".
    """
    if not arno_available(conn):
        raise ArnoError(
            f"libarno_callout not available; cannot call {fn_name!r}. "
            "Set IVG_ARNO_LIB or deploy libarno_callout.so to /usr/irissys/mgr/."
        )

    lib_path = _probe_cache[_conn_key(conn)]["lib_path"]

    sql_fn = _SQL_FN_DISPATCH.get(fn_name)
    if sql_fn is None:
        raise ArnoError(
            f"No SQL wrapper registered for {fn_name!r}. "
            f"Supported: {sorted(_SQL_FN_DISPATCH.keys())}"
        )

    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {sql_fn[0]}(?, {sql_fn[1]})",
                    [lib_path, *args])
        row = cur.fetchone()
    except Exception as e:
        raise ArnoError(f"$ZF(-5) call to {fn_name!r} failed: {e}") from e

    if not row or row[0] is None:
        raise ArnoError(f"{fn_name!r} returned NULL")

    result_str = str(row[0])
    if result_str.startswith("ERROR:"):
        raise ArnoError(f"{fn_name!r} returned: {result_str}")

    if fn_name.endswith("_run") and result_str.startswith("OK:"):
        try:
            total_len = int(result_str[3:])
        except ValueError:
            return result_str
        if total_len <= 0:
            return ""
        chunks: list = []
        offset = 0
        chunk_size = 30000
        cur2 = conn.cursor()
        while offset < total_len:
            length = min(chunk_size, total_len - offset)
            cur2.execute("SELECT ivg_arno_zf_get_result_chunk(?, ?, ?)",
                         [lib_path, offset, length])
            chunk_row = cur2.fetchone()
            if not chunk_row or chunk_row[0] is None:
                raise ArnoError(f"{fn_name!r} chunk fetch returned NULL at offset {offset}")
            chunk = str(chunk_row[0])
            if chunk.startswith("ERROR:"):
                raise ArnoError(f"{fn_name!r} chunk fetch: {chunk}")
            chunks.append(chunk)
            offset += len(chunk) if chunk else length
            if not chunk:
                break
        return "".join(chunks)

    return result_str


_SQL_FN_DISPATCH = {
    "kg_triangle_count_global": ("ivg_arno_zf_call_kg_triangle", "?, ?"),
    "kg_scc_global": ("ivg_arno_zf_call_kg_scc", "?, ?"),
    "kg_kcore_global": ("ivg_arno_zf_call_kg_kcore", "?, ?"),
    "kg_leiden_global": ("ivg_arno_zf_call_kg_leiden", "?, ?, ?, ?, ?, ?, ?"),
    "kg_triangle_count_json": ("ivg_arno_zf_triangle_json", "?, ?"),
    "kg_scc_json": ("ivg_arno_zf_scc_json", "?, ?"),
    "kg_kcore_json": ("ivg_arno_zf_kcore_json", "?, ?"),
    "kg_leiden_json": ("ivg_arno_zf_leiden_json", "?, ?, ?, ?, ?, ?, ?"),
    "kg_triangle_count_run": ("ivg_arno_zf_triangle_run", "?"),
    "kg_scc_run": ("ivg_arno_zf_scc_run", "?"),
    "kg_kcore_run": ("ivg_arno_zf_kcore_run", "?"),
    "kg_leiden_run": ("ivg_arno_zf_leiden_run", "?, ?, ?, ?, ?, ?"),
}


def _quote_zf_arg(arg: Any) -> str:
    """Format a single argument for the $ZF(-5) ObjectScript expression.

    Strings get double-quoted with embedded quote escaping per ObjectScript convention.
    Numbers pass through unquoted. None becomes empty string.
    """
    if arg is None:
        return '""'
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    s = str(arg).replace('"', '""')
    return f'"{s}"'


def remap_kernel_ids(result_json: str, idx_to_node: list) -> list:
    """Convert integer-indexed kernel result rows back to original node IDs.

    The `kg_*_run` kernels operate on integer-indexed adjacency strings and
    emit results with `"id"` set to the stringified integer (e.g., `"42"`).
    Map those back via `idx_to_node[i]` (built by `build_kg_adjacency_chunked`).

    Returns the parsed list of result dicts with `id` rewritten to the
    original string node IDs.
    """
    import json as _json
    parsed = _json.loads(result_json) if result_json else []
    out: list = []
    for r in parsed:
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "")
        try:
            i = int(rid)
            if 0 <= i < len(idx_to_node):
                r = {**r, "id": idx_to_node[i]}
        except (TypeError, ValueError):
            pass
        out.append(r)
    return out


def clear_probe_cache() -> None:
    """Reset the per-connection probe cache. Used by tests to re-probe after deploys."""
    _probe_cache.clear()


def build_kg_adjacency_json(conn) -> str:
    """Walk `^KG("out", 0, src, predicate, dst)` via Native API and serialize as
    `{"nodes": [...], "edges": [{"s": "...", "d": "..."}, ...]}` JSON.

    This bypasses the rzf `ns.keys` truncation bug observed when iterating
    long-string-subscripted globals from inside a `$ZF(-5)` callout. The
    `iris.createIRIS().nextSubscript()` Native API call from Python works
    correctly on the same data.

    Used by `_*_arno` helpers in `iris_sql_store.py` to feed the `*_json`
    Rust kernels (`kg_triangle_count_json`, `kg_scc_json`, etc.).

    Note: IRIS SQL VARCHAR(32000) caps the single-call JSON payload at ~30KB.
    For larger graphs use `build_kg_adjacency_chunked` + `kg_*_run` kernels
    (canonical chunked-upload pattern from arno's Graph.KG.ArnoAccelNKG).
    """
    import iris as _iris
    import json as _json
    iris_inst = _iris.createIRIS(conn)

    nodes_seen: dict = {}
    nodes_list: list = []
    edges: list = []

    def _intern(node_id: str) -> int:
        idx = nodes_seen.get(node_id)
        if idx is None:
            idx = len(nodes_list)
            nodes_list.append(node_id)
            nodes_seen[node_id] = idx
        return idx

    src = iris_inst.nextSubscript(False, "^KG", "out", 0, "")
    while src is not None and src != "":
        _intern(src)
        pred = iris_inst.nextSubscript(False, "^KG", "out", 0, src, "")
        while pred is not None and pred != "":
            dst = iris_inst.nextSubscript(False, "^KG", "out", 0, src, pred, "")
            while dst is not None and dst != "":
                _intern(dst)
                edges.append({"s": src, "d": dst})
                dst = iris_inst.nextSubscript(False, "^KG", "out", 0, src, pred, dst)
            pred = iris_inst.nextSubscript(False, "^KG", "out", 0, src, pred)
        src = iris_inst.nextSubscript(False, "^KG", "out", 0, src)

    in_src = iris_inst.nextSubscript(False, "^KG", "in", 0, "")
    while in_src is not None and in_src != "":
        if in_src not in nodes_seen:
            _intern(in_src)
        in_src = iris_inst.nextSubscript(False, "^KG", "in", 0, in_src)

    return _json.dumps({"nodes": nodes_list, "edges": edges}, separators=(",", ":"))


def build_kg_adjacency_chunked(conn) -> "tuple[list[str], int]":
    """Build NKG-format adjacency string with embedded NODEMAP header by walking
    `^KG("out", 0, src, predicate, dst)` via Native API, then upload it in
    30KB chunks via `kg_adj_append` to the Rust-side buffer.

    Format (matches `Graph.KG.NKGAccel.ExportAdjacencyKG` + `parse_nkg_adjacency_with_nodemap`):

        NODEMAP:N
        0=node_name_0
        1=node_name_1
        ...
        ---
        0:1,2,3
        1:0,2
        ...

    Returns (node_id_list, edge_count) for caller convenience; the kernel itself
    resolves indices back to names via the NODEMAP header so result rows already
    carry original node IDs and no Python-side remap is needed.

    Bypasses (1) the rzf `ns.keys` truncation bug on long-string subscripts (we
    walk via Native API in Python, which works), and (2) the 32KB single-VARCHAR
    parameter limit (we chunk to 30KB pieces). Mirrors arno's canonical
    `Graph.KG.ArnoAccelNKG` ObjectScript pattern adapted to run from Python.

    Performance: at v1.99.0 the Python walk via Native API takes ~0.9s on
    ER(2000, 9941e) due to ~20k `nextSubscript` round-trips. The optimized
    server-side path (`build_kg_adjacency_serverside`) drops this to ~50ms by
    moving the entire walk into a SQL OBJECTSCRIPT function. Falls through
    to the server-side path automatically when libarno is available.
    """
    if arno_available(conn):
        try:
            return _build_kg_adjacency_serverside(conn)
        except ArnoError:
            pass
        except Exception:
            pass

    import iris as _iris
    iris_inst = _iris.createIRIS(conn)

    node_to_idx: dict = {}
    idx_to_node: list = []

    def _intern(node_id: str) -> int:
        idx = node_to_idx.get(node_id)
        if idx is None:
            idx = len(idx_to_node)
            idx_to_node.append(node_id)
            node_to_idx[node_id] = idx
        return idx

    src = iris_inst.nextSubscript(False, "^KG", "out", 0, "")
    while src is not None and src != "":
        _intern(src)
        src = iris_inst.nextSubscript(False, "^KG", "out", 0, src)

    in_src = iris_inst.nextSubscript(False, "^KG", "in", 0, "")
    while in_src is not None and in_src != "":
        if in_src not in node_to_idx:
            _intern(in_src)
        in_src = iris_inst.nextSubscript(False, "^KG", "in", 0, in_src)

    edge_count = 0
    body_lines: list = []
    src = iris_inst.nextSubscript(False, "^KG", "out", 0, "")
    while src is not None and src != "":
        s_idx = node_to_idx[src]
        nbrs: list = []
        pred = iris_inst.nextSubscript(False, "^KG", "out", 0, src, "")
        while pred is not None and pred != "":
            dst = iris_inst.nextSubscript(False, "^KG", "out", 0, src, pred, "")
            while dst is not None and dst != "":
                d_idx = node_to_idx.get(dst)
                if d_idx is None:
                    d_idx = _intern(dst)
                nbrs.append(str(d_idx))
                edge_count += 1
                dst = iris_inst.nextSubscript(False, "^KG", "out", 0, src, pred, dst)
            pred = iris_inst.nextSubscript(False, "^KG", "out", 0, src, pred)
        if nbrs:
            body_lines.append(f"{s_idx}:{','.join(nbrs)}")
        src = iris_inst.nextSubscript(False, "^KG", "out", 0, src)

    n = len(idx_to_node)
    header_parts = [f"NODEMAP:{n}"]
    for i, name in enumerate(idx_to_node):
        header_parts.append(f"{i}={name}")
    header = "\n".join(header_parts) + "\n"
    body = "\n".join(body_lines) + ("\n" if body_lines else "")
    adj_str = header + "---\n" + body

    chunk_size = 30000
    if not arno_available(conn):
        raise ArnoError("libarno_callout not available; cannot send adjacency chunks.")

    cur = conn.cursor()
    cur.execute(
        """CREATE OR REPLACE FUNCTION ivg_arno_adj_append(libpath VARCHAR(512), chunk VARCHAR(32000))
RETURNS VARCHAR(64)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen failed"
    set fnid = $ZF(-4,3,dllid,"kg_adj_append")
    quit:fnid=0 "ERROR: kg_adj_append not found"
    quit $ZF(-5,dllid,fnid,chunk)
}""".strip())
    conn.commit()

    lib_path = _probe_cache[_conn_key(conn)]["lib_path"]
    pos = 0
    while pos < len(adj_str):
        chunk = adj_str[pos : pos + chunk_size]
        cur.execute("SELECT ivg_arno_adj_append(?, ?)", [lib_path, chunk])
        row = cur.fetchone()
        if row and row[0] and str(row[0]).startswith("ERROR"):
            raise ArnoError(f"kg_adj_append failed: {row[0]}")
        pos += chunk_size

    return idx_to_node, edge_count


def _build_kg_adjacency_serverside(conn) -> "tuple[list[str], int]":
    """Server-side ^KG walk: SQL OBJECTSCRIPT function does the entire walk
    + chunked kg_adj_append calls in a single Python→IRIS round-trip.

    Returns (idx_to_node, edge_count). The nodemap is built inside ObjectScript
    via $Order on `^KG("out",0,...)` / `^KG("in",0,...)`, then the entire
    NODEMAP+adjacency string is built in IRIS memory and pushed to libarno's
    Mutex<String> buffer in 30KB chunks via direct $ZF(-5,kg_adj_append,...)
    calls. The nodemap is also persisted to `^||arnonodemap` in process-private
    storage; the function returns "<edge_count>:<node_count>" as a tiny status
    string, and Python pulls the nodemap out via a follow-up read.

    Performance target: ~50-100ms on ER(2000,9941e), down from ~960ms for
    the per-call Native-API path.
    """
    key = _conn_key(conn)
    cache = _probe_cache.get(key, {})
    lib_path = cache.get("lib_path", DEFAULT_LIB_PATH)

    cur = conn.cursor()
    if not cache.get("serverside_ddl_installed"):
        ddl = """CREATE OR REPLACE FUNCTION ivg_arno_build_adj(libpath VARCHAR(512))
RETURNS VARCHAR(64)
LANGUAGE OBJECTSCRIPT
{
    set dllid = $ZF(-4,1,libpath)
    quit:dllid=0 "ERROR: dlopen"
    set fnid = $ZF(-4,3,dllid,"kg_adj_append")
    quit:fnid=0 "ERROR: kg_adj_append"
    kill ^||arnonodemap, ^||arnonodelookup, ^||arnonodeschunks
    set n = 0, node = ""
    for {
        set node = $Order(^KG("out",0,node))
        quit:node=""
        set ^||arnonodemap(n) = node
        set ^||arnonodelookup(node) = n
        set n = n + 1
    }
    set node = ""
    for {
        set node = $Order(^KG("in",0,node))
        quit:node=""
        if '$Data(^||arnonodelookup(node)) {
            set ^||arnonodemap(n) = node
            set ^||arnonodelookup(node) = n
            set n = n + 1
        }
    }
    set buf = "NODEMAP:"_n_$Char(10)
    set i = 0
    while i < n {
        set buf = buf_i_"="_^||arnonodemap(i)_$Char(10)
        set i = i + 1
        if $Length(buf) > 12000 {
            do $ZF(-5,dllid,fnid,buf)
            set buf = ""
        }
    }
    if $Length(buf) > 0 {
        do $ZF(-5,dllid,fnid,buf)
        set buf = ""
    }
    do $ZF(-5,dllid,fnid,"---"_$Char(10))
    set chunkSize = 12000
    set ec = 0
    set buf = ""
    set src = ""
    for {
        set src = $Order(^KG("out",0,src))
        quit:src=""
        set sIdx = $Get(^||arnonodelookup(src))
        if sIdx="" continue
        set nbrs = ""
        set pred = ""
        for {
            set pred = $Order(^KG("out",0,src,pred))
            quit:pred=""
            set dst = ""
            for {
                set dst = $Order(^KG("out",0,src,pred,dst))
                quit:dst=""
                set dIdx = $Get(^||arnonodelookup(dst))
                if dIdx="" continue
                if nbrs'="" set nbrs = nbrs_","
                set nbrs = nbrs_dIdx
                set ec = ec + 1
            }
        }
        if nbrs="" continue
        set line = sIdx_":"_nbrs_$Char(10)
        if $Length(buf)+$Length(line) > chunkSize {
            do $ZF(-5,dllid,fnid,buf)
            set buf = ""
        }
        set buf = buf_line
    }
    if $Length(buf) > 0 do $ZF(-5,dllid,fnid,buf)
    set chunkNum = 0, ndchunk = ""
    set i = 0
    while i < n {
        set entry = ^||arnonodemap(i)_$Char(31)
        if $Length(ndchunk)+$Length(entry) > 30000 {
            set chunkNum = chunkNum + 1
            set ^||arnonodeschunks(chunkNum) = ndchunk
            set ndchunk = ""
        }
        set ndchunk = ndchunk_entry
        set i = i + 1
    }
    if $Length(ndchunk) > 0 {
        set chunkNum = chunkNum + 1
        set ^||arnonodeschunks(chunkNum) = ndchunk
    }
    set ^||arnonodeschunks = chunkNum
    quit "OK:"_n_":"_ec_":"_chunkNum
}""".strip()
        cur.execute(ddl)
        ddl2 = """CREATE OR REPLACE FUNCTION ivg_arno_get_node_chunk(idx INT)
RETURNS VARCHAR(32000)
LANGUAGE OBJECTSCRIPT
{
    quit $Get(^||arnonodeschunks(idx))
}""".strip()
        cur.execute(ddl2)
        conn.commit()
        if key not in _probe_cache:
            _probe_cache[key] = {}
        _probe_cache[key]["serverside_ddl_installed"] = True

    cur.execute(f"SELECT ivg_arno_build_adj('{lib_path}')")
    row = cur.fetchone()
    if not row or row[0] is None:
        raise ArnoError("ivg_arno_build_adj returned NULL")
    status = str(row[0])
    if status.startswith("ERROR"):
        raise ArnoError(f"ivg_arno_build_adj: {status}")
    if not status.startswith("OK:"):
        raise ArnoError(f"ivg_arno_build_adj unexpected status: {status[:100]}")
    parts = status[3:].split(":")
    if len(parts) != 3:
        raise ArnoError(f"ivg_arno_build_adj malformed status: {status[:100]}")
    n = int(parts[0])
    edge_count = int(parts[1])
    chunk_count = int(parts[2])

    if n == 0:
        return [], 0

    chunks: list = []
    for i in range(1, chunk_count + 1):
        cur.execute(f"SELECT ivg_arno_get_node_chunk({i})")
        chunk_row = cur.fetchone()
        if chunk_row and chunk_row[0]:
            chunks.append(str(chunk_row[0]))
    full = "".join(chunks)
    idx_to_node = full.rstrip("\x1f").split("\x1f") if full else []
    return idx_to_node, edge_count
