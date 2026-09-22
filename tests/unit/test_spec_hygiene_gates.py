"""Spec-hygiene gates — catch structural regressions before they reach the container.

Gate 2: ObjectScript compile gate is enforced by enterprise-container.sh, not here.
Gate 3: the adjacency smoke test itself runs in enterprise-container.sh startup; this
        file asserts the probe's *shape*, because a probe that only ever writes the
        default graph cannot see a scoping regression.

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


# ---------------------------------------------------------------------------
# Gate 6 — the test-container name has exactly one default
# ---------------------------------------------------------------------------

def test_test_container_default_is_consistent():
    """Constitution VI names tests/conftest.py the authority for the test container.

    A file cannot be authoritative while it holds two different defaults for the
    same variable: a stale one makes a fixture branch on a container nobody is
    running.  Every `os.environ.get("IVG_TEST_CONTAINER", ...)` under tests/ must
    name the same container.
    """
    import pathlib
    repo = pathlib.Path(__file__).parent.parent.parent

    found: dict[str, list[str]] = {}
    for path in sorted((repo / "tests").rglob("*.py")):
        src = path.read_text()
        for lineno, line in enumerate(src.splitlines(), start=1):
            m = re.search(
                r'environ\.get\(\s*["\']IVG_TEST_CONTAINER["\']\s*,\s*["\']([^"\']+)["\']',
                line,
            )
            if m:
                found.setdefault(m.group(1), []).append(
                    f"{path.relative_to(repo)}:{lineno}"
                )

    assert found, "no IVG_TEST_CONTAINER default found under tests/ — gate is inert"
    assert len(found) == 1, (
        "IVG_TEST_CONTAINER has "
        f"{len(found)} different defaults under tests/:\n"
        + "\n".join(f"  {name!r} at {', '.join(sites)}" for name, sites in sorted(found.items()))
        + "\n\nPick one (the enterprise container) and update every site."
    )


# ---------------------------------------------------------------------------
# Gate 3 (shape) — the adjacency smoke probe has to exercise two graphs
# ---------------------------------------------------------------------------

def _smoke_probe_source() -> str:
    """The `up` path's adjacency smoke probe, from `scripts/enterprise-container.sh`."""
    import pathlib
    script = (
        pathlib.Path(__file__).parent.parent.parent
        / "scripts"
        / "enterprise-container.sh"
    ).read_text()
    start = script.index("Running adjacency smoke test")
    end = script.index("Adjacency smoke test failed", start)
    return script[start:end]


def test_the_adjacency_smoke_probe_writes_two_graphs():
    """One node ID, two graphs — the fixture spec 227 exists to make possible.

    Constitution VIII Gate 3 exists because a container can come up healthy and
    still have broken `^KG` population, which no unit test sees. Spec 227 replaced
    `UNIQUE (node_id)` on `nodes` with `UNIQUE (graph_id, node_id)`, so the probe
    that proved adjacency works now has a second job: prove it works *per graph*.
    A single-graph probe passes just as happily when every write lands in graph
    `''`, which is the exact regression the re-key can cause.
    """
    probe = _smoke_probe_source()
    assert "create_node(" in probe, "the smoke probe no longer creates nodes"
    graphs = set(re.findall(r"graph=['\"]([^'\"]+)['\"]", probe))
    assert len(graphs) >= 2, (
        f"the adjacency smoke probe writes {sorted(graphs) or 'no named graph'}. "
        "It has to create the same node ID in two graphs and BFS each one, or it "
        "cannot tell a graph-scoped ^KG from one where everything lands in the "
        "default graph."
    )


def test_the_adjacency_smoke_probe_reads_each_graph_separately():
    """`USE GRAPH` is how a Cypher read picks a graph (see cypher/parser.py).

    An unprefixed `MATCH` reads the default graph, where the probe writes nothing —
    so a probe that writes two graphs and reads without `USE GRAPH` fails for the
    wrong reason, and one that reads a single graph twice proves nothing about the
    other.  The graph has to come from the same pairs the writes came from.
    """
    probe = _smoke_probe_source()
    assert "USE GRAPH" in probe, (
        "the smoke probe's Cypher read carries no USE GRAPH prefix, so it reads the "
        "default graph — which is not where it wrote."
    )
    written = set(re.findall(r"graph=['\"]([^'\"]+)['\"]", probe))
    read_loop = re.search(r"for\s+graph\s*,\s*expected\s+in\s*\((.*?)\)\s*:", probe, re.DOTALL)
    assert read_loop, (
        "the probe no longer loops (graph, expected) pairs over its USE GRAPH read; "
        "without that the two graphs are not both read."
    )
    read = set(re.findall(r"['\"]([^'\"]+)['\"]\s*,\s*['\"][^'\"]+['\"]", read_loop.group(1)))
    assert written and read >= written, (
        f"the probe writes {sorted(written)} but only reads {sorted(read)}. "
        "Every graph it writes has to be read on its own, or a cross-graph leak "
        "still passes."
    )


def test_the_adjacency_smoke_probe_asserts_isolation_not_just_presence():
    """A non-empty result is not the assertion; the *right* neighbour is.

    Returning rows proves `^KG` was written. It does not prove the rows belong to
    the graph that was asked for — and under a graph-blind adjacency index both
    graphs' neighbours come back, which is a passing non-empty result.
    """
    probe = _smoke_probe_source()
    assert re.search(r"len\(rows\)\s*!=\s*1|len\(rows\)\s*==\s*1", probe), (
        "the probe checks only that rows is non-empty. Assert the row count and "
        "the returned neighbour per graph, so another graph's neighbour fails it."
    )


# ---------------------------------------------------------------------------
# Gate 7 — every embedding-table statement is graph-scoped and uses the 4.0.0 key
# ---------------------------------------------------------------------------
#
# `reader-inventory.md` is a hand-kept list, so it proves nothing about a statement
# written after the sweep. This gate reads the SQL back out of the source and holds
# every statement that names an embedding table to two rules:
#
#   1. it carries a graph predicate — the scope leak spec 227 exists to close;
#   2. it does not name an `id` column — 4.0.0 re-keyed these tables to
#      `(graph_id, node_id)`, and a DDL-built table still answers `SELECT id` with
#      IRIS's implicit RowID, so a stale `id` is a *silent* wrong answer, not an error.
#
# Scope is deliberately narrow. `rdf_labels`/`rdf_props` are not covered here:
# label-catalogue reads over a whole namespace are correct by design (see
# reader-inventory.md §10), so a statement-level rule there would be an allowlist
# of a hundred entries rather than a gate. The embedding tables have no such
# legitimate namespace-wide reader.

import ast
import pathlib

_PKG = pathlib.Path(__file__).resolve().parents[2] / "iris_vector_graph"

#: `scripts/` ships alongside the package: the documented NodePK migration, the
#: loaders, the demos. The graph-predicate gate below stays on the package, because
#: a single-graph demo needs no predicate — but a column that no longer exists is
#: wrong everywhere, and `scripts/migrations/migrate_to_nodepk.py` proved it by
#: selecting `id` from a re-keyed table and folding RowIDs into a list of node IDs.
_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"

#

# Only tables that are *certainly* embedding tables. A generic `{table}` or
# `{table_name}` placeholder belongs to a helper that copies or counts whatever it
# is handed (`dbapi_utils.py`, `snapshot.py`'s table walker), and its caller is
# where the scope decision lives.
#
# A placeholder counts when its *expression* names an embedding table or a vector:
# `{VECTOR_TABLE}` hid the snapshot exporter's `SELECT id` from an earlier version
# of this gate, which is precisely the kind of miss a gate exists to prevent.
_EMB_TABLE = re.compile(
    r"kg_NodeEmbeddings|kg_emb_|\{[^{}]*(?:emb|vector_table)[^{}]*\}", re.IGNORECASE
)
# `kg_EdgeEmbeddings` is out of the gate's reach because the table has no `graph_id`
# column at all: it is keyed `(s, p, o_id)` and was never given a graph by spec 227,
# so every statement against it is namespace-wide by the schema, not by an oversight.
# Flagging them would turn the gate into an allowlist of things it cannot fix.
# Recorded as a known gap in reader-inventory.md §3, alongside `^KG("prop")`/
# `^KG("label")`. Remove this exclusion when edge embeddings get a graph.
_EDGE_EMB = re.compile(r"EdgeEmbeddings|EDGE_VECTOR_TABLE", re.IGNORECASE)
_CARRIES_GRAPH = re.compile(r"graph_id|graph_scope_predicate|scope", re.IGNORECASE)
_ROW_OP = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
# `SELECT` qualifiers are consumed in any order and any number, because the shape
# that shipped in the NodePK migration was `SELECT DISTINCT id` — which the earlier
# pattern (`SELECT` then an optional `TOP n`) could not see at all.
_BARE_ID = re.compile(
    r"(?<![\w.])id\s*(?:=|,|\)|\bIN\b|\bFROM\b|\bLIKE\b|\bIS\b|$)"
    r"|SELECT\s+(?:(?:TOP\s+\d+|DISTINCT|ALL)\s+)*id\b",
    re.IGNORECASE,
)
# A regex literal is source text about SQL, not SQL.
_REGEXY = re.compile(r"\\s|\(\?:|\\b|\\w|\\d")

# Statements that name an embedding table and are namespace-wide on purpose. Exact
# text match, so a statement that changes shape has to be re-justified here rather
# than inheriting an old exemption.
_ALLOWED_UNSCOPED = {
    # Admin and diagnostic totals: a count of every embedding in the namespace is the
    # question being asked. Scoping these would answer a different one.
    "SELECT COUNT(*) FROM {self._t('kg_NodeEmbeddings')}",
    "SELECT COUNT(*) FROM {self._t('kg_NodeEmbeddings_optimized')}",
    # Dtype/width probes: read the column declaration, never a row. `WHERE 1=0`,
    # `TOP 1` and `LIMIT 1` are shape questions, and a graph predicate cannot change
    # the answer because the declaration belongs to the table, not to a row.
    "SELECT TOP 1 VECTOR_COSINE(emb, TO_VECTOR('[0]', DOUBLE))"
    " FROM {self._t('kg_NodeEmbeddings')} WHERE 1=0",
    "SELECT TOP 1 emb FROM {self._t('kg_NodeEmbeddings')} WHERE emb IS NOT NULL",
    "SELECT VECTOR_COSINE(emb, TO_VECTOR(?, {dtype}))"
    " FROM {self._t('kg_NodeEmbeddings')} WHERE emb IS NOT NULL LIMIT 1",
    # Deletes that follow their node. `delete_node`/`delete_nodes` take no `graph`,
    # so they delete a node ID wherever it lives — the vector has to go with it or it
    # outlives the node it describes. Same reach as every other delete in those
    # methods (reader-inventory.md §10).
    "DELETE FROM {self._t('kg_NodeEmbeddings')} WHERE node_id = ?",
    "DELETE FROM {self._t('kg_NodeEmbeddings')} WHERE node_id IN ({phs})",
    # Cypher DELETE: `_scope_dml_statement` inserts the graph predicate into this
    # statement when `USE GRAPH` is in force, so the literal text carries none.
    "{cte}DELETE FROM {_table('kg_NodeEmbeddings')} WHERE node_id IN ({subquery})",
    # Subgraph decoration: the traversal that produced `nodes` is namespace-wide
    # (Graph.KG.Subgraph takes no graph), so scoping only the vector read would
    # answer a narrower question than the node list it annotates.
    "SELECT node_id, emb FROM {emb_table} WHERE node_id IN ({phs})",
    # Scan-cost benchmarks: measuring a full-table scan is the point. These two entries
    # used to carry the pre-4.0.0 text — unqualified table names and an untyped
    # `TO_VECTOR(?)` — so once `vector_utils` was qualified and typed they stopped
    # matching and the gate reported the benchmark as an unscoped read. The width is now
    # read from the column, so `{dim}` is the interpolation, not a constant.
    "SELECT TOP {k} node_id, VECTOR_COSINE(emb, TO_VECTOR(?, {_DTYPE}, {dim})) as similarity"
    " FROM {_SCHEMA}.kg_NodeEmbeddings_optimized ORDER BY similarity DESC",
    "SELECT TOP 100 node_id, emb FROM {_SCHEMA}.kg_NodeEmbeddings WHERE emb IS NOT NULL",
}


def _sql_strings(path: pathlib.Path):
    """Yield (lineno, text) for every string literal that looks like SQL.

    f-strings are flattened with their placeholders left as `{expr}` so a table
    name chosen at runtime still matches. Docstrings are skipped: they are prose
    about SQL, and prose cannot leak a row.
    """
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    def flat(node):
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.JoinedStr):
            out = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    out.append(part.value)
                elif isinstance(part, ast.FormattedValue):
                    out.append("{%s}" % ast.unparse(part.value))
            return "".join(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = flat(node.left), flat(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    seen: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        if id(node) in docstrings:
            continue
        text = flat(node)
        if not text or not _ROW_OP.search(text) or _REGEXY.search(text):
            continue
        # Parent and child AST nodes both match on a concatenation; keep the longest.
        line = node.lineno
        if len(text) > len(seen.get(line, "")):
            seen[line] = text
    return sorted(seen.items())


def _embedding_statements(root: pathlib.Path = _PKG):
    """[(relpath, lineno, text)] for SQL naming an embedding table."""
    out = []
    for path in sorted(root.rglob("*.py")):
        for lineno, text in _sql_strings(path):
            if _EMB_TABLE.search(text) and not _EDGE_EMB.search(text):
                out.append((str(path.relative_to(root.parent)), lineno, " ".join(text.split())))
    return out


def test_every_embedding_statement_is_graph_scoped():
    """A statement touching an embedding table carries a graph predicate.

    The registry routes a table per `(graph_id, model_key)`, but routing alone does
    not scope a read: the legacy `kg_NodeEmbeddings` holds every graph that has not
    been routed away, so a predicate is still what keeps one graph's KNN out of
    another's (FR-009, SC-004).
    """
    offenders = [
        (p, n, t)
        for p, n, t in _embedding_statements()
        if not _CARRIES_GRAPH.search(t) and t not in _ALLOWED_UNSCOPED
    ]
    assert not offenders, "embedding SQL with no graph predicate:\n" + "\n".join(
        f"  {p}:{n}  {t}" for p, n, t in offenders
    )


def test_no_embedding_statement_names_the_removed_id_column():
    """4.0.0 re-keyed the embedding tables to `(graph_id, node_id)`; `id` is gone.

    This is not a style rule. A DDL-created table has an implicit RowID called `ID`,
    so `SELECT id FROM kg_NodeEmbeddings` still parses, still returns rows, and
    returns integers where the caller expects node IDs — and `INSERT ... (id, emb)`
    writes at the RowID instead of the key. Both fail quietly.
    """
    offenders = [
        (p, n, t) for p, n, t in _embedding_statements() if _BARE_ID.search(t)
    ]
    assert not offenders, (
        "embedding SQL naming the `id` column removed by the 227 re-key "
        "(use node_id):\n" + "\n".join(f"  {p}:{n}  {t}" for p, n, t in offenders)
    )


def test_the_id_pattern_catches_the_shapes_that_actually_shipped():
    """The gate only sees what its pattern matches, and it missed three live sites.

    `SELECT DISTINCT id FROM kg_NodeEmbeddings` is how the NodePK migration read the
    column, and the first version of `_BARE_ID` recognised `SELECT id` and
    `SELECT TOP n id` and stopped — so the shape that shipped was the one shape the
    gate could not see. Pinned here because the pattern is the whole gate: a miss
    here is silent, and a false positive is a wrongly-blocked commit.
    """
    caught = (
        "SELECT DISTINCT id FROM Graph_KG.kg_NodeEmbeddings",
        "SELECT id FROM kg_NodeEmbeddings",
        "SELECT TOP 1 id FROM Graph_KG.kg_NodeEmbeddings",
        "SELECT COUNT(DISTINCT id) FROM kg_NodeEmbeddings",
        "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
        "DELETE FROM kg_NodeEmbeddings WHERE id LIKE ?",
        "SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings ORDER BY id",
    )
    for text in caught:
        assert _BARE_ID.search(text), f"the gate cannot see `id` in: {text}"

    # `node_id` ends in `id`, and so do plenty of other column names. A gate that
    # flagged them would be turned off rather than obeyed.
    for text in (
        "SELECT node_id, emb FROM kg_NodeEmbeddings WHERE graph_id = ?",
        "DELETE FROM kg_NodeEmbeddings WHERE node_id IN (?, ?)",
        "SELECT COUNT(DISTINCT node_id) FROM kg_NodeEmbeddings",
    ):
        assert not _BARE_ID.search(text), f"false positive on: {text}"


def test_no_shipped_script_names_the_removed_id_column():
    """The same rule, for the code under `scripts/`, because that is where it bit.

    `scripts/migrations/migrate_to_nodepk.py` is the documented NodePK migration, and
    it selected `DISTINCT id` from `kg_NodeEmbeddings` for node discovery and orphan
    detection. After the 227 re-key that column is the implicit RowID: discovery
    returned integers mixed into a list of node IDs and orphan detection compared
    those integers against `nodes.node_id`, both without raising.

    Only the `id` rule is widened to `scripts/`, not the graph-predicate rule above:
    a single-graph loader or demo legitimately reads its whole table, while a column
    that no longer exists is wrong wherever it is named.
    """
    offenders = [
        (p, n, t) for p, n, t in _embedding_statements(_SCRIPTS) if _BARE_ID.search(t)
    ]
    assert not offenders, (
        "embedding SQL under scripts/ naming the `id` column removed by the 227 "
        "re-key (use node_id):\n" + "\n".join(f"  {p}:{n}  {t}" for p, n, t in offenders)
    )


def test_the_gate_actually_finds_embedding_statements():
    """A gate that matches nothing passes for the wrong reason."""
    found = _embedding_statements()
    assert len(found) > 20, (
        f"only {len(found)} embedding statements found — the SQL extractor has "
        "probably stopped recognising the table names it is meant to police"
    )
    in_scripts = _embedding_statements(_SCRIPTS)
    assert len(in_scripts) > 5, (
        f"only {len(in_scripts)} embedding statements found under scripts/ — the "
        "widened `id` gate would then be policing nothing"
    )


# ---------------------------------------------------------------------------
# Gate 8 — every graph-accepting public API says what a graph ID is not (FR-032)
# ---------------------------------------------------------------------------

#: The public methods a caller reaches a named graph through. Each one's docstring
#: has to say that a graph ID is collision avoidance, because the sentence only
#: helps where someone is about to pass one — a single note in `constants.py`
#: reaches nobody reading `help(engine.kg_KNN_VEC)`.
_GRAPH_ACCEPTING_APIS = (
    "store_embedding",
    "store_embeddings",
    "get_embedding",
    "get_embeddings",
    "embed_nodes",
    "embedding_count",
    "attach_embeddings_to_table",
    "kg_KNN_VEC",
    "kg_RRF_FUSE",
    "vector_search",
    "erase_graph",
)


def test_every_graph_accepting_api_states_it_is_not_authorisation():
    """Gate 8: a `graph` parameter is collision avoidance, not an authorisation
    boundary, and the docstring next to it has to say so (spec 227 FR-032).

    The spec is explicit that 227 must not imply isolation it does not enforce. A
    caller who reads `graph="tenant-a"` and stops there has been told the opposite
    of the truth by omission, and the place they read it is the docstring.
    """
    import inspect

    from iris_vector_graph.engine import IRISGraphEngine

    missing = []
    for name in _GRAPH_ACCEPTING_APIS:
        method = getattr(IRISGraphEngine, name, None)
        assert method is not None, f"{name} is not on IRISGraphEngine any more"
        assert "graph" in inspect.signature(method).parameters, f"{name} lost its graph"
        doc = (inspect.getdoc(method) or "").lower()
        if "collision avoidance" not in doc and "fr-032" not in doc:
            missing.append(name)
    assert not missing, (
        "these accept a graph and do not say a graph ID is collision avoidance "
        f"rather than an authorisation boundary: {missing}"
    )


# ---------------------------------------------------------------------------
# Gate 9 — exactly one `Graph.KG` class tree in the repository (spec 230 FR-022)
# ---------------------------------------------------------------------------
#
# `deploy/projects/ObjectScript/Graph/KG/` held a second copy of the classes,
# last touched at v1.37.0, loaded by an orphaned `deploy/Dockerfile` that built
# `FROM ivg-iris-base` — an image nothing in the repo produces. It predated specs
# 214, 223, 226 and 227: its `MCPTools.cls:178` still read `kg_NodeEmbeddings`
# through an `id` column that no longer exists, so an image built from it answered
# semantic search with the RowID and no graph predicate.
#
# Two trees is not a documentation problem. Whichever one an operator finds first
# is the one they deploy, and only one of them is true. This gate keeps that at
# one.

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Directories that legitimately hold their own checkout. `.claude/worktrees/`
#: holds agent worktrees — full clones of this repo, each with its own
#: `iris_src/`. They are not a second tree *in* the repo, and a gate that counted
#: them would fail for a reason the developer cannot fix by editing code.
_NOT_THIS_CHECKOUT = (".git", ".claude", "node_modules", ".venv", "build", "dist")


def _graph_kg_class_trees() -> list[pathlib.Path]:
    """Every directory under the repo root holding `Graph/KG/*.cls`."""
    found = []
    for candidate in _REPO_ROOT.rglob("Graph/KG"):
        rel = candidate.relative_to(_REPO_ROOT)
        if any(part in _NOT_THIS_CHECKOUT for part in rel.parts):
            continue
        if not candidate.is_dir():
            continue
        if any(candidate.glob("*.cls")):
            found.append(rel)
    return sorted(found)


def test_exactly_one_graph_kg_class_tree():
    """Gate 9: one ObjectScript source of truth, `iris_src/src/Graph/KG/`."""
    trees = _graph_kg_class_trees()
    assert trees == [pathlib.Path("iris_src/src/Graph/KG")], (
        "the repository must hold exactly one Graph.KG class tree "
        f"(spec 230 FR-022); found {[str(t) for t in trees]}"
    )


def test_the_tree_gate_looks_where_the_classes_actually_are():
    """A gate that found nothing would pass the assertion above by accident.

    `_graph_kg_class_trees` returning `[]` makes the equality fail rather than
    pass, but only because the expected list is non-empty. This asserts the
    finder positively: it locates the real tree and that tree has classes in it.
    """
    trees = _graph_kg_class_trees()
    assert trees, "the finder located no Graph.KG tree at all — it is broken"
    real = _REPO_ROOT / trees[0]
    assert len(list(real.glob("*.cls"))) > 10, (
        f"{trees[0]} holds {len(list(real.glob('*.cls')))} classes; the source tree "
        "has dozens, so the finder is matching the wrong directory"
    )


# ---------------------------------------------------------------------------
# Gate 10 — a falsy graph never reaches an unscoped statement (spec 230 SC-002)
# ---------------------------------------------------------------------------
#
# `retract_inference` tested `if graph:` and gave the else-branch a DELETE with no
# graph predicate at all, so `retract_inference()` and `retract_inference("")` — both
# of which mean the default graph everywhere else in IVG — deleted every graph's
# inferred edges and reported the count as a success. That is the shape this gate
# holds down: a branch a caller reaches by *omitting* the graph, holding a statement
# against a graph-scoped table, mentioning no graph.
#
# `''` and `None` are the same graph, and it is not "all of them". The dangerous
# branch is therefore whichever one runs when the graph is falsy:
#
#     if graph:            -> the `else`
#     if not graph:        -> the body
#     if graph is None:    -> the body
#     if graph is not None:-> the `else`
#
# A branch is cleared by naming `graph_id`, by calling one of the scoping helpers
# that name it on the branch's behalf, or by holding no statement against a
# graph-scoped table in the first place.

_GRAPH_PARAMS = ("graph", "graph_id", "graph_name")

#: Tables whose rows belong to a graph. Matched only through the accessors that
#: actually name them, so the English word "nodes" in a comment is not a table.
_SCOPED_TABLE_REF = re.compile(
    r"(?:Graph_KG\.|_t\(\s*['\"]|_table\(\s*['\"]|SQLUser\.)"
    r"(nodes|rdf_edges|rdf_labels|rdf_props|kg_NodeEmbeddings)",
    re.IGNORECASE,
)

#: Helpers that emit the graph predicate for their caller. A branch that delegates
#: is scoped; the helper is held to this gate on its own account.
_SCOPING_HELPER = re.compile(
    r"graph_filter_sql|graph_scope_predicate|_graph_filter_clause|_graph_predicate"
    r"|_scope_dml_statement|_graph_clause|graph_id",
    re.IGNORECASE,
)


def _falsy_graph_branches(source: str):
    """[(lineno, branch source)] for each branch reachable with a falsy graph.

    Only `if` tests written directly against a graph parameter count. A test
    combined with something else (`if graph and rebuild:`) is not a statement about
    what the default graph means, so it is left alone.
    """
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.If):
            continue
        test, branch = node.test, None
        if isinstance(test, ast.Name) and test.id in _GRAPH_PARAMS:
            branch = node.orelse  # if graph: ... else: <falsy>
        elif (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id in _GRAPH_PARAMS
        ):
            branch = node.body  # if not graph: <falsy>
        elif (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id in _GRAPH_PARAMS
            and len(test.ops) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value in (None, "")
        ):
            op = test.ops[0]
            if isinstance(op, (ast.Is, ast.Eq)):
                branch = node.body  # if graph is None: <falsy>
            elif isinstance(op, (ast.IsNot, ast.NotEq)):
                branch = node.orelse  # if graph is not None: ... else: <falsy>
        if branch:
            out.append((branch[0].lineno, "\n".join(ast.unparse(s) for s in branch)))
    return out


#: Branches that are namespace-wide when the graph is omitted, on purpose. Exact
#: text, as in Gate 7: a branch that changes shape is re-justified here rather than
#: inheriting an old exemption.
_ALLOWED_FALSY_BRANCHES = {
    # `count_embeddings(None)` is documented as the namespace total — "how much is in
    # there" — and scoping it would answer a different question. A named graph counts
    # that route instead and an unrouted pair counts zero, so there is no fallback
    # from a scoped count to this one (spec 227; audited in spec 230 T016).
    "cursor.execute(f\"SELECT COUNT(*) FROM {self._t('kg_NodeEmbeddings')}\")",
}


def _unscoped_falsy_branches(root: pathlib.Path = _PKG):
    """[(relpath, lineno, branch)] for falsy-graph branches naming no graph."""
    found = []
    for path in sorted(root.rglob("*.py")):
        for lineno, branch in _falsy_graph_branches(path.read_text()):
            if not _SCOPED_TABLE_REF.search(branch):
                continue
            if _SCOPING_HELPER.search(branch):
                continue
            if branch.strip() in _ALLOWED_FALSY_BRANCHES:
                continue
            found.append((str(path.relative_to(root.parent)), lineno, branch))
    return found


def test_no_falsy_graph_branch_reaches_an_unscoped_statement():
    """Gate 10: omitting the graph must mean the default graph, not every graph."""
    offenders = _unscoped_falsy_branches()

    assert not offenders, "\n\n".join(
        f"{rel}:{lineno} runs when the caller omits the graph and names a "
        f"graph-scoped table with no graph predicate:\n{branch}"
        for rel, lineno, branch in offenders
    )


def test_the_falsy_branch_gate_catches_the_shape_that_shipped():
    """The pre-fix `retract_inference`, reduced to its shape.

    A gate whose detector no longer matches anything passes the assertion above by
    doing nothing, so the detector is exercised positively here on both the broken
    and the fixed form.
    """
    broken = (
        "def retract_inference(self, graph=None):\n"
        "    if graph:\n"
        "        cursor.execute('DELETE FROM Graph_KG.rdf_edges WHERE graph_id = ?', [graph])\n"
        "    else:\n"
        "        cursor.execute('DELETE FROM Graph_KG.rdf_edges')\n"
    )
    branches = _falsy_graph_branches(broken)
    assert len(branches) == 1, f"the detector did not find the else-branch: {branches}"
    assert _SCOPED_TABLE_REF.search(branches[0][1])
    assert not _SCOPING_HELPER.search(branches[0][1]), (
        "the broken form reads as scoped, so the gate would have missed it"
    )

    fixed = broken.replace(
        "cursor.execute('DELETE FROM Graph_KG.rdf_edges')",
        "cursor.execute(\"DELETE FROM Graph_KG.rdf_edges WHERE COALESCE(graph_id, '') = ''\")",
    )
    assert _SCOPING_HELPER.search(_falsy_graph_branches(fixed)[0][1]), (
        "the fixed form still reads as unscoped, so the gate cannot be satisfied"
    )


def test_the_falsy_branch_gate_reads_the_whole_package():
    """A detector that parsed nothing would report a clean package."""
    seen = sum(len(_falsy_graph_branches(p.read_text())) for p in _PKG.rglob("*.py"))
    assert seen >= 5, (
        f"the detector found {seen} falsy-graph branches in the package; there are "
        "several by construction, so it is no longer matching the shapes in use"
    )
