"""4.6.0 R280 [SECURITY]: every SQL-callable IVG function that reads a global checks SELECT before it reads.

IRIS checks a global read against the database resource only, so a function walking `^KG`, `^NKG`, `^BM25Idx`, `^IVF`
or `^IVG.Ledger` answered a caller holding EXECUTE and no SELECT (measured on 2026.3.0AI: CY_VLP_ENDS, kg_PPR,
kg_DegreeCentrality, MatchEdges, DijkstraPath). Each SQL entry point now calls Graph.KG.SqlPrivilege.RequireSelect with
the table the global stands for, before it reads. The list is found, not written down: every function setup's DDL
creates, every SqlProc of Graph.KG.SqlProcs, and every Arno wrapper whose body reads around SQL privileges (the R278
derivation, legacy._reads_within_privileges) must check, but the two SQL-callable writers. The live check is
tests/e2e/test_460_r280_sql_global_readers_privilege_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph._engine import legacy

CHECK = "##class(Graph.KG.SqlPrivilege).RequireSelect("
#: SQL-callable, reading around privileges, and not a reader: two upgrade helpers that write ^KG / the temporal rows
#: (they check the write privilege instead, R289: tests/unit/test_460_r289_sql_writers_check_write.py), and the route
#: guard, which answers 0 or a refusal and no row. Prop_Set (4.7.0) writes a node property's val slot and answers the
#: value it was given: it reads the row only to check it holds the old value, after RequireWrite.
NOT_READERS = {"backfill_temporal_rows", "rebuild_edge_indices", "kg_VEC_ROUTE_GUARD", "Prop_Set"}
#: The Graph.KG classes a body may name before its check: Adjacency.Indexed() reads the class dictionary only.
BEFORE_CHECK = {"Graph.KG.SqlPrivilege", "Graph.KG.Adjacency"}


def _readers():
    """The routines that would read around privileges without their SELECT check (the check taken out of each body:
    with it, CY_VLP_ENDS is within privileges and on R278's read path)."""
    bodies = legacy._routine_bodies()
    unchecked = {n: b.replace(CHECK, "(") for n, b in bodies.items()}
    return {n: b for n, b in bodies.items() if not legacy._reads_within_privileges(n, unchecked)}, bodies


def _first_read(body: str) -> int:
    """Where the body first names a Graph.KG class other than BEFORE_CHECK, or a global; len(body) if nowhere."""
    hits = [m.start() for m in re.finditer(r"##class\((Graph\.KG\.\w+|IVG\.\w+)\)", body)
            if m.group(1) not in BEFORE_CHECK]
    hits += [m.start() for m in re.finditer(r"\$Get\(\^|\$Order\(\^|\^KG\(|\^NKG\(|\^IVF\(", body)]
    return min(hits) if hits else len(body)


def test_the_readers_found_are_the_ones_measured_and_the_rest():
    readers, _ = _readers()
    for n in ("CY_VLP_ENDS", "kg_PPR", "kg_DegreeCentrality", "kg_Betweenness", "kg_Closeness", "kg_Eigenvector",
              "kg_Leiden", "kg_TriangleCount", "kg_SCC", "kg_KCore", "kg_BM25", "kg_IVF", "MatchEdges",
              "MatchEdgesAllGraphs", "DijkstraPath"):
        assert n in readers, n


def test_every_reader_checks_select_before_it_reads():
    readers, _ = _readers()
    unchecked = []
    for name, body in readers.items():
        if name in NOT_READERS:
            continue
        code = legacy._code(body)
        at = code.find(CHECK)
        if at < 0 or at > _first_read(code):
            unchecked.append(name)
    assert unchecked == []


@pytest.mark.parametrize("name, table", [
    ("CY_VLP_ENDS", "(tbl)"), ("kg_PPR", '("Graph_KG.rdf_edges")'), ("kg_SCC", '("Graph_KG.rdf_edges")'),
    ("kg_BM25", '(..Table("rdf_props"))'), ("MatchEdges", '(..Table("rdf_edges"))'),
    ("DijkstraPath", '(..Table("rdf_edges"))')])
def test_each_checks_the_table_its_global_stands_for(name, table):
    _, bodies = _readers()
    assert CHECK + table.lstrip("(") in bodies[name] or (CHECK[:-1] + table) in bodies[name], bodies[name][:400]


def test_the_ledger_records_procedure_checks_ledger_revisions():
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    src = read_class_sources(packaged_class_dir())["Graph.KG.SqlProcs"]
    execute = src[src.index("ClassMethod LedgerRecordsExecute"):]
    execute = execute[:execute.index("\n}")]
    assert CHECK + '..Table("ledger_revisions"))' in execute


def test_the_ivf_index_records_its_table_and_kg_ivf_checks_it():
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    sources = read_class_sources(packaged_class_dir())
    assert 'Set:pTable\'="" ^IVF(name, "cfg", "table") = pTable' in sources["Graph.KG.IVFIndex"]
    assert (CHECK + '$Get(^IVF(name, "cfg", "table"), ..Table("kg_NodeEmbeddings")))') in sources["Graph.KG.SqlProcs"]


def test_the_arno_wrappers_that_reach_the_graph_check():
    from pathlib import Path

    from iris_vector_graph.stores import arno_bridge

    src = Path(arno_bridge.__file__).read_text(encoding="utf-8")  # the DDL templates, {schema} unfilled
    for name in ("ivg_arno_zf_call_2s", "ivg_arno_zf_call_kg_triangle", "ivg_arno_zf_call_kg_scc",
                 "ivg_arno_zf_call_kg_kcore", "ivg_arno_zf_call_kg_leiden", "ivg_arno_build_adj"):
        m = re.search(r"FUNCTION \{schema\}\." + name + r"\(.*?LANGUAGE OBJECTSCRIPT\n\{\{\n(.*?)\n\}\}", src, re.S)
        assert m, name
        assert m.group(1).lstrip().startswith("do " + CHECK + '"{schema}.rdf_edges")'), (name, m.group(1)[:200])


def test_the_check_reads_nothing_itself():
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    src = legacy._code(read_class_sources(packaged_class_dir())["Graph.KG.SqlPrivilege"])
    assert not re.search(r"\^[%\w(]", src)
    assert set(re.findall(r"##class\(([%\w.]+)\)", src)) == {"%Exception.SQL"}
    assert "CreateFromSQLCODE(-99" in src and '"User "_$Username_" lacks SELECT on "_tTable' in src
