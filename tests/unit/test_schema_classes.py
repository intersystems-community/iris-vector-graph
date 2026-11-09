"""4.6.0 R084: IVG's SQL procedures and ledger tables come from classes the deploy moves into the IVG Schema's package.

Spec 262 first mapped the `Graph.KG` and `IVG` packages' SQL name onto the IVG Schema and rewrote six code classes'
`SqlSchemaName = Graph_KG`. With a schema other than `Graph_KG` IRIS then mapped that schema back onto package
`Graph.KG` (`$$GetPackage^%apiDDL("RAG")` was `Graph.KG`), so in a namespace still holding `Graph_KG` table classes
setup's DDL compiled into `Graph.KG` and collided with them (-400 on `prockgKNNVEC`, -324 on `idx_edges_adj`).

Now no package is remapped. Every `[ SqlProc ]` lives in one forwarding class, `Graph.KG.SqlProcs`, whose methods
call the `Graph.KG`/`IVG` code; it and the two persistent ledger classes are the schema classes, which the deploy's
source rewrite moves into the schema's own package (`IVGALT.SqlProcs`, `MY.APP.LedgerStats`). For `Graph_KG` they
stay in `Graph.KG`, so the shipped sources are what compiles.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources
from iris_vector_graph.schema_name import SCHEMA_CLASS_MARKER, SCHEMA_CLASSES, foreign_classes

FORWARDER = "Graph.KG.SqlProcs"

#: Every SQL name IVG's classes projected before R084. The translator and the stores call some of them through
#: `_fn()`, and 4.5.x SQL reaches the rest through the legacy manifest's replacements: none may go missing.
EXPECTED_SQL_NAMES = {
    *(f"CypherFn_IVG{m}" for m in (
        "REGEXMATCH", "JSONARRAYLENGTH", "JSONVALUE", "JSONKEYS", "LISTREVERSE", "STRSPLIT", "NEWID", "RAND",
        "LISTHEAD", "LISTLAST", "LISTTAIL", "LISTGET", "TYPEERROR", "MAPGET", "JSONSET", "JSONREMOVE", "RANGE")),
    "JSON_KEYS", "Percentile_PDISC", "Percentile_PCONT", "kg_BM25", "kg_IVF", "MatchEdges", "MatchEdgesAllGraphs",
    "DijkstraPath", "backfill_temporal_rows", "rebuild_edge_indices", "ledger_records",
    "Txn_Begin", "Txn_End", "Txn_Undo",  # 4.6.4: IVG's own part of a write (Graph.KG.Txn)
}

_KEYWORDS_WITH_SQLPROC = re.compile(r"^(?:ClassMethod|Query) \w+\(.*\)[^\n]*\[[^\]\n]*\bSqlProc\b", re.M)
_METHOD = re.compile(r"^ClassMethod (\w+)\(([^\n]*)\) As ([%\w.]+)(?: \[ ([^\]]*) \])?\s*\n\{\n(.*?)\n\}", re.M | re.S)
_QUERY = re.compile(r"^Query (\w+)\(([^\n]*)\) As %Query\(ROWSPEC = \"([^\"]*)\"\)(?: \[ ([^\]]*) \])?", re.M)
#: A forward, after the SELECT check of a forwarder whose target reads a global (4.6.0 R280) or the write check of one
#: whose target writes around privileges (R289).
_FORWARD = re.compile(r"^(?:\s*Do ##class\(Graph\.KG\.SqlPrivilege\)\.Require(?:Select|Write)\([^\n]*\)\n)?"
                      r"\s*Quit ##class\(([\w.]+)\)\.(\w+)\((.*)\)\s*$")


@pytest.fixture(scope="module")
def sources():
    return read_class_sources(packaged_class_dir())


def _sql_name(keywords: str) -> str:
    m = re.search(r"SqlName = (\w+)", keywords or "")
    return m.group(1) if m else ""


def _param_names(spec: str) -> list:
    return [p.strip().split()[0].split("=")[0].lstrip("&") for p in spec.split(",") if p.strip()]


def _methods(text: str) -> dict:
    return {m.group(1): m for m in _METHOD.finditer(text)}


def test_only_the_forwarder_projects_sql_procedures(sources):
    assert sorted(n for n, t in sources.items() if _KEYWORDS_WITH_SQLPROC.search(t) and n != FORWARDER) == []


def test_the_forwarder_projects_every_name_callers_use(sources):
    text = sources[FORWARDER]
    names = [_sql_name(m.group(4)) for m in _METHOD.finditer(text) if "SqlProc" in (m.group(4) or "")]
    names += [_sql_name(m.group(4)) for m in _QUERY.finditer(text) if "SqlProc" in (m.group(4) or "")]
    assert len(names) == len(set(names))
    assert set(names) == EXPECTED_SQL_NAMES


def test_each_forwarder_calls_its_target_with_the_targets_signature(sources):
    """A forwarder is `Quit ##class(T).M(args)`, with M's formal spec and return type and its arguments in order,
    after the SELECT check when T.M reads a global (R280)."""
    checked = 0
    for name, m in _methods(sources[FORWARDER]).items():
        if "SqlProc" not in (m.group(4) or ""):
            continue
        call = _FORWARD.match(m.group(5))
        assert call, f"{name} is not a one-line forward: {m.group(5)!r}"
        target_class, target_method, args = call.groups()
        target = _methods(sources[target_class]).get(target_method)
        assert target is not None, f"{name} forwards to {target_class}.{target_method}, which does not exist"
        assert (m.group(2), m.group(3)) == (target.group(2), target.group(3)), name
        assert [a.strip() for a in args.split(",") if a.strip()] == _param_names(m.group(2)), name
        assert "SqlProc" not in (target.group(4) or "")
        checked += 1
    assert checked == len(EXPECTED_SQL_NAMES) - 1  # ledger_records is a query


def test_the_ledger_records_query_forwards_execute_fetch_and_close(sources):
    """The query itself lives only in the forwarder: Graph.KG.Ledger's own `Query Records` had the SQL name
    `Ledger_Records`, which IRIS counts as `ledger_records` (ERROR #5083) even with no SqlProc, measured."""
    fwd = {m.group(1): m for m in _QUERY.finditer(sources[FORWARDER])}
    q = next(m for m in fwd.values() if _sql_name(m.group(4)) == "ledger_records")
    # the 14 columns MutationRecord.from_row reads, in its order
    assert [c.split(":")[0] for c in q.group(3).split(",")] == [
        "seq", "ordinal", "op", "entity_kind", "entity_id", "attr", "prior", "prior_null", "new", "new_null",
        "graph", "flags", "digest", "redacted_op"]
    assert re.search(r"^Query ", sources["Graph.KG.Ledger"], re.M) is None
    methods = _methods(sources[FORWARDER])
    targets = _methods(sources["Graph.KG.Ledger"])
    for part in ("Execute", "Fetch", "Close"):
        m = methods[q.group(1) + part]
        assert m.group(2) == targets["Records" + part].group(2)
        call = _FORWARD.match(m.group(5))
        assert call and call.group(1, 2) == ("Graph.KG.Ledger", "Records" + part)


def _ddl_routines() -> set:
    from iris_vector_graph.schema import GraphSchema

    text = "\n".join(GraphSchema.get_procedures_sql_list()) + GraphSchema.get_base_schema_sql()
    return set(re.findall(r"CREATE (?:OR REPLACE )?(?:FUNCTION|PROCEDURE) Graph_KG\.(\w+)", text))


def test_every_name_the_translator_and_the_stores_call_resolves():
    """`_fn('X')` names a DDL function or one the forwarder projects (the built names: `Percentile_` + PDISC/PCONT)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "iris_vector_graph"
    called = set()
    for path in root.rglob("*.py"):
        called |= set(re.findall(r"""_fn\(\s*['"](\w+)['"]""", path.read_text(encoding="utf-8")))
    called = {n for n in called if not n.endswith("_")} | {"Percentile_PDISC", "Percentile_PCONT"}
    called |= {"ledger_records", "backfill_temporal_rows"}  # replay.py and schema.py name them in SQL text
    assert called - _ddl_routines() - EXPECTED_SQL_NAMES == set()


def test_every_legacy_replacement_of_a_class_projection_is_a_forwarder():
    """4.5.x's `SQLUser.CypherFn_IVGJSONSET` (a `User.CypherFn` SqlProc) is replaced by the IVG Schema's
    `CypherFn_IVGJSONSET`, which the forwarder projects; the DDL-made ones are replaced by DDL."""
    from iris_vector_graph._engine.legacy import _derived_class
    from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST

    projected = {str(e["replacement"]) for e in LEGACY_MANIFEST
                 if e["replacement"] and e["sql_name"] and not _derived_class(e)}
    assert projected and projected - EXPECTED_SQL_NAMES == set()


def test_the_schema_classes_are_shipped_in_graph_kg_and_carry_the_marker(sources):
    for short in SCHEMA_CLASSES:
        text = sources[f"Graph.KG.{short}"]
        assert re.search(rf"^Parameter {SCHEMA_CLASS_MARKER} = 1;", text, re.M), short


def test_the_ledger_tables_store_under_their_own_package(sources):
    """Default storage, so `Graph.KG.LedgerRevision` keeps 4.5.x's `^Graph.KG.LedgerRevisionD` and
    `IVGALT.LedgerRevision` gets `^IVGALT.LedgerRevisionD`. Pinned to the Graph.KG globals, the IVGALT class could
    not compile beside a 4.5.x one (ERROR #5564, two classes on one global: measured in a 4.5.1 namespace)."""
    for short in ("LedgerRevision", "LedgerStats"):
        assert "DEFAULTGLOBAL" not in sources[f"Graph.KG.{short}"]
        assert not re.search(r"^Storage ", sources[f"Graph.KG.{short}"], re.M)


@pytest.mark.parametrize("schema, package", [("Graph_KG", "Graph.KG"), ("IVGALT", "IVGALT"), ("MY_APP", "MY.APP")])
def test_the_snapshot_exports_the_schemas_ledger_globals(schema, package):
    from iris_vector_graph._engine.snapshot import ledger_storage_globals

    assert ledger_storage_globals(schema) == [f"{package}.LedgerRevisionD", f"{package}.LedgerRevisionI",
                                              f"{package}.LedgerStatsD"]


def test_every_class_that_projects_sql_is_a_schema_class(sources):
    projects = re.compile(r"\bSqlProc\b|^Class\s+[\w.]+\s+Extends\s+\(?[^\n]*%(?:Library\.)?Persistent", re.M)
    assert sorted(n for n, t in sources.items() if projects.search(t)) == sorted(f"Graph.KG.{c}" for c in SCHEMA_CLASSES)


def test_no_schema_class_name_is_one_setups_ddl_generates():
    """A DDL table `X_y` compiles to class `<pkg>.Xy`, a function to `func…`, a procedure to `proc…`."""
    from iris_vector_graph.schema_name import _ivg_class_short_names

    ddl = _ivg_class_short_names()
    for short in SCHEMA_CLASSES:
        assert short.lower() not in ddl and not short.startswith(("func", "proc"))


# --------------------------------------------------------------------------- the package-clash check


class _Cursor:
    def __init__(self, classes, marked):
        self.classes, self.marked, self.rows = classes, marked, []

    def execute(self, sql, params=None):
        if "%Dictionary.ParameterDefinition" in sql:
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
    def __init__(self, classes, marked=()):
        self.classes, self.marked = classes, marked

    def cursor(self):
        return _Cursor(self.classes, self.marked)


def test_the_schema_classes_ivg_deployed_are_not_someone_elses():
    conn = _Conn(["IVGALT.SqlProcs", "IVGALT.LedgerStats", "IVGALT.LedgerRevision", "IVGALT.rdfedges"],
                 marked=["IVGALT.SqlProcs", "IVGALT.LedgerStats", "IVGALT.LedgerRevision"])
    assert foreign_classes(conn, "IVGALT") == []


def test_a_class_of_a_schema_class_name_without_the_marker_is_someone_elses():
    """An application's own table `IVGALT.SqlProcs` (class `IVGALT.SqlProcs`) must never be loaded over."""
    conn = _Conn(["IVGALT.SqlProcs", "IVGALT.LedgerStats"], marked=["IVGALT.LedgerStats"])
    assert foreign_classes(conn, "IVGALT") == ["IVGALT.SqlProcs"]
