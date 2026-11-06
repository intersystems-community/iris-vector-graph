"""4.6.0 R289 [SECURITY]: every SQL-callable IVG routine that writes a table or a graph global around SQL privileges
checks the write privilege on the table it writes before it writes, and one that writes through dynamic SQL raises the
refusal IRIS gives instead of answering as if it had written.

`backfill_temporal_rows` inserts node and relationship rows through embedded SQL (Graph.KG.Adjacency.AssertTemporalRow),
which IRIS does not check against the caller's privileges: a caller holding EXECUTE and SELECT only wrote them
(measured 2026-11-04, 2026.3.0AI). It now checks INSERT on nodes and rdf_edges first
(Graph.KG.SqlPrivilege.RequireWrite). `rebuild_edge_indices` runs `BUILD INDEX` through dynamic SQL, which IRIS checks
(it needs %BUILD_INDEX; UPDATE and DELETE on the tables do not do, measured), but Graph.KG.Ledger.RebuildEdgeIndices
caught the refusal and answered 0 tables rebuilt; it now raises it, naming the table.

The list is found, not written: each function setup's DDL creates and each SqlProc of Graph.KG.SqlProcs is followed
through the methods it calls (transitively, method by method). A write it reaches is the caller's data unless the
global is one a read keeps (DERIVED: work areas, caches and the indexes rebuilt from the tables). A global write or an
embedded `&sql` write is around privileges; a statement text run through %SQL.Statement is checked by IRIS. The live
check is tests/e2e/test_460_r289_sql_writers_privilege_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph._engine import legacy

CHECK = "##class(Graph.KG.SqlPrivilege).RequireWrite("
#: What a read writes, and why it is not the caller's data: each a global (and first subscript) a reader keeps.
DERIVED = {
    '^KG("degc': "the degree cache, derived from ^KG; a reader at R skips the store (R050)",
    '^KG("deg2p': "the 2-hop degree sketches, derived from ^KG",
    '^KG("deg2p_exact': "the same, exact",
    '^KG("deg2p_exact_merged': "the same, all graphs merged",
    "^NKG": "the interned adjacency, rebuilt from ^KG on a read after an erase (R268)",
    '^IVG.BuiltFrom("deg2p': "what the 2-hop sketches were built from",
    '^IVG.BuiltFrom("deg2p_exact': "the same, exact",
    '^ArnoKG("KG': "Arno's adjacency cache",
    '^ArnoKG("2hs': "the same, 2-hop",
    '^ArnoAccel("lib_path': "the library path a load records (configuration, R132)",
    '^ArnoAccel("dllid': "what releases before 4.5.2 kept here, killed on load",
    '^BM25Idx("__stop__': "the BM25 stop-word list, set up on first search",
    "^IRIS.Temp.IVG.VL": "the var-length walk's shared temporary storage",
    "^bcParSrc": "betweenness workers' temporary storage",
    "^bcBitAdj": "the same",
}
_METHOD = re.compile(r"^(?:ClassMethod|Method) (\w+)\(.*?\n\{\n(.*?)\n\}", re.M | re.S)
_CALL = re.compile(r"##class\(([\w.%]+)\)\.(\w+)\(|\.\.(\w+)\(")
#: A global set, killed, merged or incremented (not process-private): its name, and its first subscript when that is a
#: string literal.
_GLOBAL_WRITE = re.compile(r"(?i)(?:\b(?:set|kill|merge)\s+(?:\w+\s*=\s*[^,\n]*,\s*)*|\$Increment\()"
                           r"(\^(?!\|\|)[\w.%]+)(?:\(\"(\w*))?")
#: Embedded SQL that writes, and the object methods that write a table's storage: neither is checked for the caller.
_AROUND_SQL = re.compile(r"(?i)&sql\(\s*(?:INSERT|UPDATE|DELETE)\b|%BuildIndices|%PurgeIndices|%KillExtent|%DeleteId\(")
#: A writing statement's text (run through %SQL.Statement, which IRIS checks for the caller).
_DYNAMIC_SQL = re.compile(r"(?i)\bINSERT\s+INTO\b|\bUPDATE\s+\"?\w+\"?\.|\bDELETE\s+FROM\b|\bBUILD\s+INDEX\b"
                          r"|\bTRUNCATE\s+TABLE\b")


def _sources() -> dict:
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    return read_class_sources(packaged_class_dir())


def _method_bodies(sources: dict) -> dict:
    return {(cls, name): legacy._code(body) for cls, src in sources.items() for name, body in _METHOD.findall(src)}


def _writes(body: str) -> set:
    """The writes in one body: a global (name and first literal subscript), "&SQL" for an embedded write or a storage
    method, "DYNAMIC <statement>" for a writing statement's text."""
    found = {g + (f'("{sub}' if sub else "") for g, sub in _GLOBAL_WRITE.findall(body)}
    if _AROUND_SQL.search(body):
        found.add("&SQL")
    elif "&sql(" not in body.lower():
        found |= {"DYNAMIC " + " ".join(m.group(0).upper().split()) for m in _DYNAMIC_SQL.finditer(body)}
    return found


def _reached(cls: str, body: str, methods: dict, seen: set) -> set:
    out = set()
    for m in _CALL.finditer(body):
        target = (m.group(1), m.group(2)) if m.group(1) else (cls, m.group(3))
        if target in seen or target not in methods:
            continue
        seen.add(target)
        out |= {(target, w) for w in _writes(methods[target])}
        out |= _reached(target[0], methods[target], methods, seen)
    return out


def _routines() -> dict:
    """{name: (class the body is in, "" for DDL; own code)}: setup's functions and the SqlProcs forwarders."""
    from iris_vector_graph.schema import GraphSchema

    out = {}
    for ddl in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
        m = re.search(r"CREATE OR REPLACE (?:FUNCTION|PROCEDURE) Graph_KG\.(\w+)\(", ddl)
        if m and "{" in ddl:
            out[m.group(1)] = ("", legacy._code(ddl[ddl.index("{"):]))
    for name, body in legacy._SQLPROC.findall(_sources()["Graph.KG.SqlProcs"]):
        out[name] = ("Graph.KG.SqlProcs", legacy._code(body))
    return out


def _data_writes() -> dict:
    """{routine: the writes it reaches that are not DERIVED}, each with the method that makes it."""
    methods = _method_bodies(_sources())
    out = {}
    for name, (cls, body) in _routines().items():
        writes = {(name, w) for w in _writes(body)} | set(_reached(cls, body, methods, set()))
        data = sorted((m, w) for m, w in writes if w not in DERIVED)
        if data:
            out[name] = data
    return out


def _around() -> dict:
    return {n: ws for n, ws in _data_writes().items() if any(not w.startswith("DYNAMIC ") for _m, w in ws)}


def test_the_writers_found_are_the_two_named():
    writers = _data_writes()
    assert set(writers) == {"backfill_temporal_rows", "rebuild_edge_indices"}, writers
    assert writers["backfill_temporal_rows"] == [(("Graph.KG.Adjacency", "AssertTemporalRow"), "&SQL")]
    assert writers["rebuild_edge_indices"] == [(("Graph.KG.Ledger", "RebuildEdgeIndices"), "DYNAMIC BUILD INDEX")]
    assert set(_around()) == {"backfill_temporal_rows"}


def test_every_derived_global_is_still_written_somewhere():
    """A DERIVED entry no reader writes any more is stale: remove it rather than let it excuse a new write."""
    methods = _method_bodies(_sources())
    written = set()
    for name, (cls, body) in _routines().items():
        written |= _writes(body) | {w for _m, w in _reached(cls, body, methods, set())}
    assert sorted(set(DERIVED) - written) == []


@pytest.mark.parametrize("name", sorted(_around()))
def test_every_writer_around_privileges_checks_before_it_calls_anything(name):
    cls, body = _routines()[name]
    at = body.find(CHECK)
    calls = [m.start() for m in re.finditer(r"##class\(([\w.%]+)\)", body) if m.group(1) != "Graph.KG.SqlPrivilege"]
    assert 0 <= at < min(calls + [len(body)]), body


def test_the_backfill_checks_insert_on_the_tables_it_writes():
    assert CHECK + '..Table("nodes")_","_..Table("rdf_edges"), "i")' in _routines()["backfill_temporal_rows"][1]


@pytest.mark.parametrize("name", sorted(set(_data_writes()) - set(_around())))
def test_every_dynamic_sql_writer_raises_what_iris_refused(name):
    """No `Catch {}` and no unchecked %SQLCODE in the method that runs the statement: a refused BUILD INDEX answered
    0 tables rebuilt."""
    methods = _method_bodies(_sources())
    for (cls, meth), _w in _data_writes()[name]:
        body = methods[(cls, meth)]
        assert not re.search(r"Catch\s*(?:\w+\s*)?\{\s*\}", body), (cls, meth, body)
        assert re.search(r"%SQLCODE\s*<\s*0", body) and "CreateFromSQLCODE(" in body, (cls, meth, body)


def test_require_write_checks_each_privilege_and_names_it():
    src = _sources()["Graph.KG.SqlPrivilege"]
    body = src[src.index("ClassMethod RequireWrite("):]
    body = legacy._code(body[:body.index("\n}") + 2])
    assert "CreateFromSQLCODE(-99" in body and '" lacks "_' in body
    names = _sources()["Graph.KG.SqlPrivilege"]
    for word in ('"INSERT"', '"UPDATE"', '"DELETE"'):
        assert word in names
    assert not re.search(r"\^[%\w(]", legacy._code(src))  # the check reads no global
