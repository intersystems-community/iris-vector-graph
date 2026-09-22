"""Spec 227 T048 — what an erase selects by (FR-017).

Erasure is the one operation whose bug is invisible: it deletes the other graph's
rows and then reports a count that looks like success. Before the re-key a node ID
belonged to one graph, so `DELETE ... WHERE s IN (SELECT node_id FROM nodes WHERE
graph = A)` was a correct way to reach A's children. After it, the same subquery
returns the shared node ID and the DELETE takes both graphs' children with it.

Asserted against the class source. The live gate is
`tests/e2e/test_227_erase_isolation.py` (T049), which needs the enterprise
container; what this file pins is that every statement in the erase path names a
`graph_id` of its own, that routed tables are found through the registry rather
than by recomputing a hash, and that the quarantine is not in either erase path
(FR-039).
"""

import ast
import re
from pathlib import Path

import pytest

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "Eraser.cls"


@pytest.fixture
def source():
    return CLS.read_text()


def _method(source: str, name: str) -> str:
    """One ClassMethod body, from its signature to the next ClassMethod."""
    m = re.search(
        rf"ClassMethod {name}\(.*?(?=\nClassMethod |\n\}}\s*$)", source, re.DOTALL
    )
    assert m, f"no ClassMethod {name} in Eraser.cls"
    return m.group(0)


def _code(body: str) -> str:
    """The method body with `//` comment text removed.

    The quarantine assertions below are about what the method *executes*. A comment
    saying the quarantine is deliberately untouched is the documentation those
    assertions want to see kept, not a violation of them.
    """
    return "\n".join(re.sub(r"//.*$", "", line) for line in body.splitlines())


def _deletes(body: str, table: str) -> list:
    """Every `DELETE FROM <table> ...` statement in a method body, whitespace-flat.

    Matched to the end of the embedded-SQL parenthesis group rather than to a
    newline: these statements span lines, and the predicate is the part that
    matters.
    """
    out = []
    for m in re.finditer(rf"&sql\(DELETE FROM Graph_KG\.{table}\b(.*?)\)\n", body, re.DOTALL):
        out.append(" ".join(m.group(0).split()))
    return out


# --- the child tables now carry their own graph ----------------------------------


@pytest.mark.parametrize("table", ["rdf_labels", "rdf_props"])
def test_child_rows_are_deleted_by_their_own_graph(source, table):
    body = _method(source, "EraseGraph")
    statements = _deletes(body, table)
    assert statements, f"EraseGraph no longer deletes from {table} at all"
    for sql in statements:
        assert "graph_id" in sql, (
            f"{table} is deleted without naming a graph: {sql}"
        )
        assert "SELECT node_id FROM Graph_KG.nodes" not in sql, (
            f"{table} is still reached through the node's graph, which after the "
            f"re-key returns the shared node ID once per graph: {sql}"
        )


@pytest.mark.parametrize(
    "table", ["kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"]
)
def test_legacy_embedding_rows_are_deleted_by_graph(source, table):
    body = _method(source, "EraseGraph")
    statements = _deletes(body, table)
    assert statements, f"EraseGraph no longer deletes from {table} at all"
    for sql in statements:
        assert "graph_id" in sql, f"{table} is deleted without naming a graph: {sql}"
        assert not re.search(r"\bid IN \(", sql), (
            f"{table} still selects by node ID alone, and the column it names was "
            f"removed by the re-key: {sql}"
        )


def test_no_erase_statement_names_the_removed_id_column(source):
    """The re-key replaced `id` with `node_id` on both embedding tables (T018/T019)."""
    for method in ("EraseGraph", "EraseAll"):
        body = _method(source, method)
        assert not re.search(r"\bWHERE id\b|\bid IN \(", body), (
            f"{method} still names the `id` column the re-key removed"
        )


def test_the_default_graph_keeps_coalesce_on_both_sides(source):
    """An empty host variable binds as SQL NULL in embedded SQL, so both sides.

    Without it, erasing the default graph compiles to `graph_id = NULL`, matches
    nothing, and returns a count that reads like success.
    """
    body = _method(source, "EraseGraph")
    for table in ("rdf_labels", "rdf_props", "kg_NodeEmbeddings"):
        for sql in _deletes(body, table):
            assert "COALESCE(graph_id, '') = COALESCE(:tName, '')" in sql, (
                f"{table}'s predicate is not COALESCEd on both sides: {sql}"
            )


# --- routed tables are found through the registry --------------------------------


def test_the_graphs_routes_are_read_from_the_registry(source):
    """Not recomputed from the hash: the registry row is the authority (FR-011).

    A recomputed name finds nothing when IRIS derived a different class name, or
    when the route was created by an older naming rule — and "found nothing"
    here means a table full of the erased graph's vectors survives the erase.
    """
    body = _method(source, "EraseGraph")
    assert "embedding_registry" in body, (
        "EraseGraph never reads the registry, so it cannot know which routed "
        "tables this graph has"
    )
    assert re.search(r"%STARTSWITH\s*'kg_emb_'|kg_emb_", body), (
        "EraseGraph does not restrict itself to routed tables; a registry row "
        "naming kg_NodeEmbeddings must not be turned into a DROP TABLE"
    )


def test_each_routed_table_is_dropped(source):
    body = _method(source, "EraseGraph")
    assert "DROP TABLE" in body, "EraseGraph empties routes but never removes them"


def test_the_dropped_routes_registry_rows_go_with_them(source):
    body = _method(source, "EraseGraph")
    rows = _deletes(body, "embedding_registry")
    dynamic = re.findall(r"DELETE FROM Graph_KG\.embedding_registry", body)
    assert rows or dynamic, (
        "the routed tables are dropped but their registry rows survive, so the "
        "next resolve returns a route whose table does not exist"
    )


def test_the_legacy_tables_are_never_dropped(source):
    """Their rows go; the tables stay.

    `kg_NodeEmbeddings` is named statically by `kg_KNN_VEC`, by the bulk insert
    templates and by adoption. Dropping it on a default-graph erase would take the
    schema with the content.
    """
    body = _method(source, "EraseGraph")
    assert not re.search(r"DROP TABLE\s+Graph_KG\.kg_NodeEmbeddings", body)


# --- the quarantine belongs to no graph ------------------------------------------


def test_a_graph_erase_does_not_touch_the_quarantine(source):
    """A quarantined row has no graph, so no graph's erase can claim it (FR-039)."""
    body = _code(_method(source, "EraseGraph"))
    assert "embedding_quarantine" not in body, (
        "EraseGraph reaches the quarantine, where rows sit precisely because "
        "nobody could say which graph they belong to"
    )


def test_erase_all_does_not_empty_the_quarantine(source):
    """`EraseAll` removes content. A quarantined row is evidence, not content.

    It is the only record of a vector whose graph could not be recovered, and the
    operator placing it has nothing else to work from. Emptying it turns a pending
    decision into silent data loss — the one outcome the migration was designed to
    avoid (FR-039, FR-040).
    """
    body = _code(_method(source, "EraseAll"))
    assert "embedding_quarantine" not in body, (
        "EraseAll deletes the quarantine, which is the only copy of the vectors "
        "whose graph could not be determined"
    )


#: The one function allowed to remove a quarantined row.
#:
#: `place_quarantined` is the operator's placement decision itself: it INSERTs the row
#: into the route the operator named and deletes it in the same transaction, so the row
#: leaves the quarantine only by being placed. Every other removal is the `--force`
#: drain this check exists to keep out.
QUARANTINE_EXIT = "place_quarantined"


def _function_at(path, lineno: int):
    """The name of the innermost function containing ``lineno``, or ``None``."""
    tree = ast.parse(path.read_text())
    found = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= lineno <= end:
            if found is None or node.lineno > found.lineno:
                found = node
    return found.name if found else None


def test_no_python_statement_removes_the_quarantine_or_its_rows():
    """T051's other half: the Python schema layer creates the table and reads it.

    Nothing in the package may empty or drop it, except the placement path itself. A
    `--force` drain is the shape this check exists to keep out: the operator placing a
    row there has nothing else to work from, so removing it without a placement
    decision is the silent data loss the migration was built to avoid (FR-039, FR-040).
    """
    package = CLS.parents[4] / "iris_vector_graph"
    offenders = []
    for path in package.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "embedding_quarantine" not in line and "_quarantine_table" not in line:
                continue
            if not re.search(r"\b(DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b", line, re.I):
                continue
            if _function_at(path, lineno) == QUARANTINE_EXIT:
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "a Python path removes quarantined rows or the table itself: " + "; ".join(offenders)
    )


# --- whole-namespace erase -------------------------------------------------------


def test_erase_all_empties_the_registry(source):
    body = _method(source, "EraseAll")
    assert _deletes(body, "embedding_registry") or re.search(
        r"DELETE FROM Graph_KG\.embedding_registry", body
    ), "EraseAll leaves registry rows describing tables it emptied or dropped"


def test_erase_all_drops_every_routed_table(source):
    body = _method(source, "EraseAll")
    assert "DROP TABLE" in body, (
        "EraseAll leaves the routed tables behind; after it, the inventory reports "
        "routes for a namespace with no content"
    )
    assert re.search(r"%STARTSWITH\s*'kg_emb_'|kg_emb_", body)
