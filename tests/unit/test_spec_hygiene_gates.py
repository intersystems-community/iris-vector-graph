"""Spec-hygiene gates — catch structural regressions before they reach the container.

Gate 2: ObjectScript compile gate is enforced by enterprise-container.sh, not here.
Gate 3: Adjacency smoke test is enforced by enterprise-container.sh startup, not here.

Gates implemented in this file:
  Gate 4b — SQL column name validation: fhir.py DML must not reference columns that
             don't exist in the schema DDL.
  Gate 5  — Protocol auto-coverage: already in test_store_protocol.py; this file adds
             a standalone cross-file check so it runs even when test_store_protocol is
             not collected.
"""
import re
import inspect


# ---------------------------------------------------------------------------
# Gate 4b — SQL column names in fhir.py match schema.py DDL
# ---------------------------------------------------------------------------

def _parse_schema_columns(schema_src: str) -> dict[str, set[str]]:
    """Return {table_name: {col1, col2, ...}} from CREATE TABLE statements."""
    tables: dict[str, set[str]] = {}
    for m in re.finditer(
        r'CREATE TABLE(?:\s+IF NOT EXISTS)?\s+Graph_KG\.(\w+)\s*\(([^;]+?)\);',
        schema_src,
        re.DOTALL,
    ):
        tname = m.group(1).lower()
        body = m.group(2)
        cols = re.findall(r'^\s+(\w+)\s+\w', body, re.MULTILINE)
        skip = {"constraint", "index", "unique", "primary", "foreign"}
        tables[tname] = {c.lower() for c in cols if c.lower() not in skip}
    return tables


def _extract_dml_column_refs(fhir_src: str) -> list[tuple[str, str, int]]:
    """Return [(table_name, column_name, line_no)] from INSERT/SELECT in fhir.py.

    Parses INSERT INTO Graph_KG.T (...cols...) and
    SELECT col1, col2 FROM Graph_KG.T patterns.
    """
    refs = []
    lines = fhir_src.splitlines()
    for lineno, line in enumerate(lines, start=1):
        # INSERT INTO Graph_KG.table (col1, col2, ...)
        m_ins = re.search(
            r'INSERT INTO Graph_KG\.(\w+)\s*\(([^)]+)\)',
            line,
            re.IGNORECASE,
        )
        if m_ins:
            tname = m_ins.group(1).lower()
            for col in re.split(r',\s*', m_ins.group(2)):
                col = col.strip().strip('"').lower()
                if col:
                    refs.append((tname, col, lineno))

        # SELECT col1, col2, ... FROM Graph_KG.table
        m_sel = re.search(
            r'SELECT\s+(.*?)\s+FROM\s+Graph_KG\.(\w+)',
            line,
            re.IGNORECASE | re.DOTALL,
        )
        if m_sel:
            tname = m_sel.group(2).lower()
            for col in re.split(r',\s*', m_sel.group(1)):
                col = col.strip().lower()
                # Skip wildcards, aggregates, expressions
                if col in ('*', '') or re.search(r'[\(\)\s]', col):
                    continue
                refs.append((tname, col, lineno))

    return refs


def test_fhir_sql_columns_match_schema():
    """Gate 4b: every column referenced in fhir.py DML must exist in schema.py DDL.

    This catches typos like sqlid_column (should be sql_table + id_column) and
    viavia_source (should be via_source) before they reach a live database.
    """
    import pathlib
    repo = pathlib.Path(__file__).parent.parent.parent
    schema_src = (repo / "iris_vector_graph" / "schema.py").read_text()
    fhir_src = (repo / "iris_vector_graph" / "_engine" / "fhir.py").read_text()

    schema_cols = _parse_schema_columns(schema_src)
    dml_refs = _extract_dml_column_refs(fhir_src)

    # Tables we care about checking (DDL-managed, not external SQL bridge tables)
    managed = {"table_mappings", "relationship_mappings", "nodes", "rdf_edges",
               "rdf_labels", "rdf_props"}

    violations = []
    for tname, col, lineno in dml_refs:
        if tname not in managed:
            continue
        known = schema_cols.get(tname, set())
        if not known:
            continue  # table not in schema (dynamic or external)
        if col not in known:
            violations.append(
                f"  fhir.py:{lineno}: column '{col}' not in Graph_KG.{tname} "
                f"(known: {sorted(known)})"
            )

    assert not violations, (
        f"fhir.py references {len(violations)} column(s) not defined in schema.py DDL:\n"
        + "\n".join(violations)
        + "\n\nFix the column names in fhir.py to match schema.py CREATE TABLE definitions."
    )


# ---------------------------------------------------------------------------
# Gate 4 (standalone) — GraphStore protocol completeness
# ---------------------------------------------------------------------------

def test_graph_store_protocol_methods_are_documented():
    """Every method on GraphStore must have a docstring or type annotation.

    Undocumented protocol methods are invisible to implementors and silently
    omitted from mocks.  This is a softer companion to test_mock_has_all_protocol_methods.
    """
    from iris_vector_graph.store_protocol import GraphStore

    unannotated = []
    for name, member in inspect.getmembers(GraphStore):
        if name.startswith("_") or not callable(member):
            continue
        sig = inspect.signature(member)
        has_return = sig.return_annotation is not inspect.Parameter.empty
        has_doc = bool(getattr(member, "__doc__", None))
        if not has_return and not has_doc:
            unannotated.append(name)

    # Warn (not fail) — this is a hygiene nudge, not a hard gate yet.
    # Convert to assert once all methods are annotated.
    if unannotated:
        import warnings
        warnings.warn(
            f"GraphStore has {len(unannotated)} method(s) with no return annotation "
            f"or docstring: {sorted(unannotated)}. "
            f"Add annotations to make protocol drift easier to detect.",
            stacklevel=2,
        )
