"""4.7.2: a schema's class package may hold another tool's classes; only a name IVG itself creates there is refused.

4.7.1 refused `initialize_schema()` under `schema_prefix="RAG"` whenever package `RAG` held ANY class IVG did not
create. A tool that keeps its own tables in the same schema (`RAG.KGEntities`, `RAG.SourceDocuments`) was locked out,
though 4.4.2 allowed it and ADR-0016 names that layout as supported. IRIS needs no such refusal (spec 262, research
R2): the one thing that goes wrong is IVG creating a table, function, procedure or schema class whose generated class
name is already a class of someone else's.

These tests pin the decision, a pure function of the class names in the package, and the wrapper that asks the
database for them. No IRIS here; the live case is `tests/e2e/test_472_shared_package.py`.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.schema_name import (
    SCHEMA_CLASS_MARKER,
    SCHEMA_CLASSES,
    IVGSchemaError,
    colliding_classes,
    package_collisions,
    refuse_package_clash,
)

#: The classes a tool that keeps its own tables in the schema holds (the shapes IRIS generates for its tables).
FOREIGN_TOOL = ["RAG.KGEntities", "RAG.KGEntityDocuments", "RAG.KGRelationships", "RAG.KGTags", "RAG.SourceDocuments"]


def _ivg_tables():
    from iris_vector_graph.schema_name import _ivg_class_short_names

    return sorted(_ivg_class_short_names())


def _declared_routines():
    """(kind, name) of every function and procedure setup's DDL creates, read from the DDL, not from the code under test."""
    from iris_vector_graph.schema import GraphSchema

    found = []
    for stmt in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
        m = re.search(r"CREATE\s+OR\s+REPLACE\s+(FUNCTION|PROCEDURE)\s+\w+\.(\w+)", stmt, re.IGNORECASE)
        if m:
            found.append((m.group(1).upper(), m.group(2)))
    return found


# --------------------------------------------------------------------------- the decision


def test_another_tools_classes_in_the_package_are_not_a_clash():
    assert package_collisions("RAG", FOREIGN_TOOL) == []


def test_an_empty_package_is_not_a_clash():
    assert package_collisions("RAG", []) == []


def test_a_foreign_class_named_like_an_ivg_table_is_a_clash():
    assert package_collisions("RAG", [*FOREIGN_TOOL, "RAG.nodes"]) == ["RAG.nodes"]


def test_the_clash_is_found_whatever_the_case_of_the_foreign_class():
    """A table class IRIS made from `Nodes` is `RAG.Nodes`; IVG's `nodes` would be the same SQL table name."""
    assert package_collisions("RAG", ["RAG.Nodes", "RAG.RDFEdges"]) == ["RAG.Nodes", "RAG.RDFEdges"]


@pytest.mark.parametrize("short", _ivg_tables())
def test_every_table_class_ivg_creates_is_a_clash_when_someone_else_holds_it(short):
    assert package_collisions("RAG", [f"RAG.{short}"]) == [f"RAG.{short}"]


def test_the_table_list_the_decision_uses_has_the_tables_setup_creates():
    """A guard on the guard: the names above come from IVG's own lists, so check a few against the DDL by hand."""
    tables = set(_ivg_tables())
    assert {"nodes", "rdfedges", "rdflabels", "rdfprops", "docs", "kgnodeembeddings"} <= tables


def test_a_schema_class_without_the_marker_is_a_clash_and_with_it_is_ivgs():
    names = [f"RAG.{c}" for c in SCHEMA_CLASSES]
    assert package_collisions("RAG", names) == sorted(names)
    assert package_collisions("RAG", names, marked=names) == []
    assert package_collisions("RAG", names, marked=["RAG.SqlProcs"]) == sorted(n for n in names if n != "RAG.SqlProcs")


def test_the_marker_is_matched_whatever_the_case_of_the_class_name():
    assert package_collisions("RAG", ["RAG.SqlProcs"], marked=["rag.sqlprocs"]) == []


@pytest.mark.parametrize("kind, name", _declared_routines())
def test_a_class_with_the_exact_name_ivgs_routine_compiles_to_is_a_clash(kind, name):
    """`CREATE FUNCTION RAG.JSON_VALUE` makes class `RAG.funcJSONVALUE`; a procedure `kg_KNN_VEC` makes `prockgKNNVEC`."""
    generated = ("func" if kind == "FUNCTION" else "proc") + name.replace("_", "")
    assert package_collisions("RAG", [f"RAG.{generated}"]) == [f"RAG.{generated}"]
    assert package_collisions("RAG", [f"RAG.{generated.lower()}"]) == [f"RAG.{generated.lower()}"]


def test_a_function_class_for_a_name_ivg_does_not_create_is_not_a_clash():
    """The 4.7.1 rule skipped every class starting func or proc as IVG's; one that merely starts so is the other tool's."""
    assert package_collisions("RAG", ["RAG.funcFoo", "RAG.procedureLog", "RAG.functions", "RAG.process"]) == []


def test_a_function_name_is_not_a_procedure_name():
    """IVG's FUNCTION JSON_VALUE is class funcJSONVALUE; a foreign procJSONVALUE is not that class."""
    assert package_collisions("RAG", ["RAG.procJSONVALUE"]) == []
    assert package_collisions("RAG", ["RAG.funckgKNNVEC"]) == []


@pytest.mark.parametrize("prefix", ["kgemb", "kgeemb"])
def test_a_routed_embedding_table_class_is_a_clash_by_its_exact_shape(prefix):
    """`kg_emb_<16 hex>` is class `kgemb<16 hex>`; the hash is IVG's to choose, so any class of that shape is refused."""
    shaped = f"RAG.{prefix}0123456789abcdef"
    assert package_collisions("RAG", [shaped]) == [shaped]
    assert package_collisions("RAG", [f"RAG.{prefix}0123456789ABCDEF"]) == [f"RAG.{prefix}0123456789ABCDEF"]


@pytest.mark.parametrize("name", ["RAG.kgembeddings", "RAG.kgemb", "RAG.kgeembed", "RAG.kgemb0123", "RAG.kgemb0123456789abcdefg"])
def test_a_class_that_only_starts_like_a_routed_table_is_not_a_clash(name):
    assert package_collisions("RAG", [name]) == []


def test_the_classes_iris_generates_under_a_table_class_are_the_tables():
    """An index's class (`docs.hCBQuQ`) is generated with IVG's own `docs`: the table is the only name that can clash."""
    assert package_collisions("RAG", ["RAG.docs.hCBQuQ", "RAG.rdfprops.hIDX"]) == []
    assert package_collisions("RAG", ["RAG.docs", "RAG.docs.hCBQuQ"]) == ["RAG.docs"]


def test_a_sub_package_of_the_other_tool_is_not_a_clash():
    assert package_collisions("RAG", ["RAG.Util.Helper", "RAG.KGEntities.Index1"]) == []


def test_a_multi_part_package_name():
    assert package_collisions("MY.APP", ["MY.APP.Orders", "MY.APP.nodes"]) == ["MY.APP.nodes"]


def test_the_result_is_sorted_and_lists_each_class_once():
    got = package_collisions("RAG", ["RAG.rdfedges", "RAG.nodes", "RAG.rdfedges"])
    assert got == ["RAG.nodes", "RAG.rdfedges"]


# --------------------------------------------------------------------------- the query wrapper and the refusal


class _Cursor:
    def __init__(self, classes, marked, tables):
        self.classes, self.marked, self.tables, self.rows, self.seen = classes, marked, tables, [], []

    def execute(self, sql, params=None):
        self.seen.append(sql)
        assert not re.search(r"\b(DELETE|DROP|UPDATE|INSERT|ALTER)\b", sql.upper())  # only ever a read
        if "INFORMATION_SCHEMA.TABLES" in sql:
            self.rows = [(t,) for t in self.tables]
        elif "%Dictionary.ParameterDefinition" in sql:
            assert params[0] == SCHEMA_CLASS_MARKER
            self.rows = [(c,) for c in self.marked]
        else:
            assert "%Dictionary.ClassDefinition" in sql
            self.rows = [(c,) for c in self.classes]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, classes, marked=(), tables=()):
        self.classes, self.marked, self.tables = classes, marked, tables

    def cursor(self):
        return _Cursor(self.classes, self.marked, self.tables)


def test_a_shared_package_is_accepted_by_the_refusal():
    refuse_package_clash(_Conn(FOREIGN_TOOL), "RAG")
    refuse_package_clash(_Conn(FOREIGN_TOOL), "rag")  # schema names are not case-sensitive


def test_the_default_schema_is_never_checked():
    assert colliding_classes(_Conn(["Graph.KG.nodes", "Graph.KG.Foreign"]), "Graph_KG") == []


def test_a_collision_is_refused_naming_the_classes_and_saying_nothing_was_created():
    conn = _Conn([*FOREIGN_TOOL, "RAG.nodes", "RAG.rdfedges"])
    with pytest.raises(IVGSchemaError) as raised:
        refuse_package_clash(conn, "RAG")
    text = str(raised.value)
    assert "RAG.nodes" in text and "RAG.rdfedges" in text and "Nothing was created" in text
    assert "RAG.KGEntities" not in text, "the other tool's non-colliding classes are not what is wrong"


def test_the_wrapper_finds_the_marked_schema_classes_ivg_deployed():
    conn = _Conn(["IVGALT.SqlProcs", "IVGALT.LedgerStats", "IVGALT.Other"],
                 marked=["IVGALT.SqlProcs", "IVGALT.LedgerStats"])
    assert colliding_classes(conn, "IVGALT") == []


# --------------------------------------------------------------------------- a second run: those classes are IVG's own

#: What setup leaves in a schema: its tables, a function and a procedure class, a routed table, the schema classes.
IVGS_OWN = ["RAG.nodes", "RAG.rdfedges", "RAG.rdfprops", "RAG.docs", "RAG.docs.hCBQuQ", "RAG.funcJSONVALUE",
            "RAG.prockgKNNVEC", "RAG.kgemb0123456789abcdef", "RAG.SqlProcs", "RAG.LedgerRevision", "RAG.LedgerStats"]
SCHEMA_CLASS_NAMES = ["RAG.SqlProcs", "RAG.LedgerRevision", "RAG.LedgerStats"]


def test_a_second_run_does_not_refuse_the_classes_the_first_one_made():
    conn = _Conn([*FOREIGN_TOOL, *IVGS_OWN], marked=SCHEMA_CLASS_NAMES, tables=["nodes", "rdf_edges"])
    assert colliding_classes(conn, "RAG") == []
    refuse_package_clash(conn, "RAG")


def test_the_same_classes_with_no_ivg_tables_in_the_schema_are_a_clash():
    """Before IVG ran, a class named like IVG's table, function or routed table is the other tool's."""
    conn = _Conn([*FOREIGN_TOOL, "RAG.nodes", "RAG.funcJSONVALUE", "RAG.kgemb0123456789abcdef"], tables=["nodes"])
    assert colliding_classes(conn, "RAG") == ["RAG.funcJSONVALUE", "RAG.kgemb0123456789abcdef", "RAG.nodes"]


def test_a_second_run_still_refuses_a_schema_class_that_is_not_ivgs():
    conn = _Conn([*IVGS_OWN], marked=["RAG.LedgerRevision", "RAG.LedgerStats"], tables=["nodes", "rdf_edges"])
    assert colliding_classes(conn, "RAG") == ["RAG.SqlProcs"]


def test_ivg_tables_present_leaves_the_decision_to_the_schema_classes_alone():
    assert package_collisions("RAG", IVGS_OWN, marked=SCHEMA_CLASS_NAMES, ivg_tables_present=True) == []
    assert package_collisions("RAG", IVGS_OWN, marked=[], ivg_tables_present=True) == sorted(SCHEMA_CLASS_NAMES)


def test_the_wrapper_refuses_a_schema_class_without_the_marker():
    """An application's own table `IVGALT.SqlProcs` must never be loaded over (the deploy refuses it too)."""
    conn = _Conn(["IVGALT.SqlProcs", "IVGALT.LedgerStats"], marked=["IVGALT.LedgerStats"])
    assert colliding_classes(conn, "IVGALT") == ["IVGALT.SqlProcs"]


def test_the_wrapper_matches_the_package_whatever_the_case_of_the_schema():
    conn = _Conn(["IVGCLASH.nodes"])
    assert colliding_classes(conn, "ivgclash") == ["IVGCLASH.nodes"]


# --------------------------------------------------------------------------- an interrupted rdf_edges rescue


def _rescue_table():
    from iris_vector_graph.schema import RESCUE_STAGING_TABLE

    return RESCUE_STAGING_TABLE


def test_an_interrupted_rescue_is_ivgs_own_schema():
    """The 3.x/4.0 rescue drops `rdf_edges` and holds its rows in the staging table; the retry that resumes it runs
    this check with `nodes` and the staging table present and no `rdf_edges`: IVG's classes are not clashes then."""
    conn = _Conn([*FOREIGN_TOOL, *IVGS_OWN], marked=SCHEMA_CLASS_NAMES, tables=["nodes", _rescue_table()])
    assert colliding_classes(conn, "RAG") == []
    refuse_package_clash(conn, "RAG")


def test_a_foreign_nodes_with_neither_rdf_edges_nor_the_staging_table_is_a_clash():
    conn = _Conn([*FOREIGN_TOOL, "RAG.nodes"], tables=["nodes", "KGEntities", "SourceDocuments"])
    assert colliding_classes(conn, "RAG") == ["RAG.nodes"]


def test_the_staging_table_without_nodes_is_not_ivgs_schema():
    conn = _Conn(["RAG.nodes", "RAG.funcJSONVALUE"], tables=[_rescue_table()])
    assert colliding_classes(conn, "RAG") == ["RAG.funcJSONVALUE", "RAG.nodes"]


def test_the_table_query_asks_for_the_staging_table_by_the_name_setup_uses():
    conn = _Conn([], tables=[])
    cur = conn.cursor()
    seen = []
    orig = cur.execute
    cur.execute = lambda sql, params=None: (seen.append((sql, params)), orig(sql, params))[1]
    conn.cursor = lambda: cur
    colliding_classes(conn, "RAG")
    sql = " ".join(q for q, _ in seen if "INFORMATION_SCHEMA.TABLES" in q)
    assert _rescue_table() in sql or any(_rescue_table() in (p or []) for _, p in seen)
