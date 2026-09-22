"""The 4.0.0 re-key of the `^KG` node stores (spec 230, FR-010, FR-011, FR-020).

Spec 214 put the graph key first in `^KG("out")`, `^KG("in")`, `^KG("deg")` and
`^KG("degp")` and left four stores flat:

===========================  =====================================
3.2.0                        4.0.0
===========================  =====================================
``^KG("prop", s, key)``      ``^KG("prop", graph, s, key)``
``^KG("label", label, s)``   ``^KG("label", graph, label, s)``
``^KG("deg2p", s, p)``       ``^KG("deg2p", graph, s, p)``
``^KG("deg2p_exact", s, p)`` ``^KG("deg2p_exact", graph, s, p)``
===========================  =====================================

An upgraded install holds every entry in the left-hand layout, which no 4.0.0 reader
addresses — and a reader on the wrong layout does not fail, it answers nothing:
``$Data`` and ``$Order`` on an absent subscript mean "nothing here", so a
label-filtered traversal quietly stops matching and a property map quietly comes
back empty.

**The entries are dropped, not moved.** Nothing in a flat entry says which graph it
belongs to, which is the defect itself: before 227 a node ID named one graph, so the
question never arose; after ``UNIQUE (graph_id, node_id)`` the same ID lives in two
graphs and ``^KG("prop", "n1", "name")`` holds whichever graph wrote last.
``^KG("deg2p")`` is worse than ambiguous — the count was summed across every graph,
so the *number* belongs to no graph and no placement could make it true.

A migration that read the flat entry and looked its node up in ``Graph_KG.nodes``
would be inferring the graph, and that lookup answers two graphs for one ID exactly
when it matters. FR-011 closes the option: no entry's graph is inferred. Instead the
subtrees are killed and the server rebuilds them from the SQL rows, each of which
carries its own ``graph_id``.

This step therefore writes no ``^KG`` entry itself. The layout has one writer,
``Graph.KG.TraversalBuild`` (FR-010); a second spelling of the subscript order here
would be free to drift from it, and drift in a subscript order is the silent kind.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Tuple

from iris_vector_graph.schema import repair_ifind_helpers

logger = logging.getLogger(__name__)

#: The schema whose rows the rebuild reads. Overridable for a non-default install.
DEFAULT_SCHEMA = "Graph_KG"

#: The `^KG` subtrees this step re-keys. ``deg2p_exact`` travels with ``deg2p``: it
#: is the same statistic counted exactly rather than summed, written and read beside
#: it, and leaving it flat would leave ``KHop2CountExact`` answering from a tree no
#: erase can reach.
REKEYED_STORES: Tuple[str, ...] = ("prop", "label", "deg2p", "deg2p_exact")

#: The rows the rebuild derives the new layout from. Each must declare ``graph_id``:
#: the rebuild reads the graph off the row, so a source still on the pre-214 shape
#: would send every entry to the default graph — after the kill, with nothing left to
#: recover the real layout from.
SOURCE_TABLES: Tuple[str, ...] = ("rdf_props", "rdf_labels", "rdf_edges")

#: Ordered, and the order matters: ``Build2HopStats`` counts two-hop reach out of
#: ``^KG("out", graph, ...)``, which ``BuildKG`` has just written.
REBUILD_STEPS: Tuple[Tuple[str, str], ...] = (
    ("Graph.KG.TraversalBuild", "BuildKG"),
    ("Graph.KG.TraversalBuild", "Build2HopStats"),
    ("Graph.KG.TraversalBuild", "Build2HopExactStats"),
)

#: Recompiled before the kill, and the reason is an ordering the upgrade cannot avoid:
#: `initialize_schema` deploys the ObjectScript layer before the 227 re-key gives
#: `rdf_labels` and `rdf_props` their `graph_id` column, so on a 3.2.0 install
#: `Graph.KG.TraversalBuild`'s embedded SQL compiles against a table without the column
#: it names ("Field 'GRAPH_ID' not found in the applicable tables") and the class ends
#: up with no compiled methods at all. Recompiling the whole package rather than the
#: three classes below: every class whose embedded SQL reads a re-keyed table is in the
#: same position, and `Graph.KG.Subgraph` was measured failing the same way.
REBUILD_PACKAGE = "Graph.KG"

#: How deep below a store name an entry can sit: ``(graph, label, s)`` is three, and
#: the guard is generous rather than exact so that a walk over an unexpected tree
#: ends instead of recursing forever.
_MAX_DEPTH = 8


@dataclass(frozen=True)
class KgRekeyReport:
    """What one re-key pass dropped and what the server rebuilt in its place."""

    #: ``{store: {graph_id: entries}}`` after the rebuild, counted by walking the
    #: tree rather than by trusting the rebuild's own return value — the point of the
    #: report is that the entries are where 4.0.0 readers look. The empty string is
    #: the default graph, whose subscript is the integer ``0`` (ADR-0003).
    entries_rebuilt: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: Entries found in the four subtrees before the kill. Every one of them is in a
    #: layout no 4.0.0 reader addresses, so this is a count of what the pass removed,
    #: not of what it lost: the same statistics come back from the rows.
    entries_dropped: int = 0


def rekey_kg_node_stores(
    conn,
    *,
    dry_run: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> KgRekeyReport:
    """Drop the flat `^KG` node stores and have the server rebuild them keyed by graph.

    Args:
        conn: A connection to the namespace to migrate. Used for the column probes
            through a cursor and for the Native API, which is how a global is killed
            and a class method ordered.
        dry_run: Report what would be dropped and write nothing. What the rebuild
            will write is deliberately not predicted: predicting the two-hop counts
            would mean a second implementation of the walk that produces them, and
            the whole point of this step is that the server owns that walk.
        schema: The SQL schema holding the source rows.

    Returns:
        A :class:`KgRekeyReport`. Safe to re-run: the step is a kill and a rebuild,
        so a second pass does the same work again and lands in the same place.

    Raises:
        RuntimeError: if a source table does not declare ``graph_id``, or if a rebuild
            method has no compiled entry point even after the recompile. Both are
            raised before anything is killed — a refusal after the kill would leave the
            install with neither layout.
    """
    _require_scoped_sources(conn, schema)

    native = _native(conn)
    # The sources declare `graph_id` now; the classes that read them may have been
    # compiled when they did not. Recompiling is not a data write, so a dry run does it
    # too: skipping it would make the pre-flight report a failure the real run
    # does not have.
    _recompile_rebuild_classes(native)
    # The recompile above is a *package* compile, and a package compile deletes the
    # generated class an iFind index is searched through without writing a new one
    # (spec 230, FR-030). Nothing reports it — the index definition survives — so the
    # text leg only fails later, at query Open, with <CLASS DOES NOT EXIST>. Repaired
    # here, next to the compile that breaks it, and in a dry run too: the dry run does
    # the same compile, so it does the same damage.
    _repair_ifind_after_recompile(conn, schema)
    _require_rebuild_entry_points(conn)

    dropped = sum(_count_entries(native, store) for store in REKEYED_STORES)

    if dry_run:
        return KgRekeyReport(entries_rebuilt={}, entries_dropped=dropped)

    # The kill is the whole reason this is a migration. Rebuilding without it would
    # leave every flat entry in place beside the new ones, and `Kill ^KG("prop")`
    # from here is honest in a way `Kill ^KG("prop", someNodeId)` never was.
    for store in REKEYED_STORES:
        native.kill("^KG", store)

    for class_name, method in REBUILD_STEPS:
        logger.debug("Rebuilding ^KG via %s.%s()", class_name, method)
        native.classMethodValue(class_name, method)

    _commit(conn)

    return KgRekeyReport(
        entries_rebuilt={store: _count_per_graph(native, store) for store in REKEYED_STORES},
        entries_dropped=dropped,
    )


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _require_scoped_sources(conn, schema: str) -> None:
    """Refuse unless every source table exists and carries a graph.

    The two failures read identically off ``INFORMATION_SCHEMA.COLUMNS`` — both answer
    no rows — and they are not the same problem. A real 3.2.0 upgrade hit the first one:
    deleting ``Graph.KG.Edge`` had taken ``Graph_KG.rdf_edges`` with it, and reporting a
    missing column sent the operator back to ``initialize_schema``, which is what had
    just destroyed the table. So the tables are probed separately and named for what
    they are.
    """
    cursor = conn.cursor()
    try:
        absent = [t for t in SOURCE_TABLES if not _table_exists(cursor, schema, t)]
        unscoped = [
            t
            for t in SOURCE_TABLES
            if t not in absent and not _declares(cursor, schema, t, "graph_id")
        ]
    finally:
        _close(cursor)

    if absent:
        raise RuntimeError(
            "the ^KG rebuild reads each entry's graph off the row it comes from, and "
            f"{', '.join(schema + '.' + t for t in absent)} does not exist. An upgrade "
            "from v3.2.0 reaches this when the class-owned table was deleted without "
            "being rebuilt: look for the staged rows in "
            f"{schema}.rdf_edges__ivg400rescue and restore them before re-running the "
            "upgrade — nothing has been dropped."
        )
    if unscoped:
        raise RuntimeError(
            "the ^KG rebuild reads each entry's graph off the row it comes from, and "
            f"{', '.join(schema + '.' + t for t in unscoped)} has no graph_id column. "
            "Run initialize_schema() (spec 214's migration) before re-keying the "
            "globals — nothing has been dropped."
        )


def _recompile_rebuild_classes(native) -> None:
    """Recompile :data:`REBUILD_PACKAGE` so its embedded SQL binds to the re-keyed rows.

    Best-effort on its own: what matters is whether the entry points exist afterwards,
    and :func:`_require_rebuild_entry_points` is what answers that. A container with no
    source loaded, or a class broken for an unrelated reason, fails there with a message
    naming the method instead of here with a compiler status nobody reads.
    """
    try:
        native.classMethodValue("%SYSTEM.OBJ", "CompilePackage", REBUILD_PACKAGE, "ck-d")
    except Exception as exc:  # pragma: no cover - exercised against a live server
        logger.debug("recompile of %s skipped: %s", REBUILD_PACKAGE, exc)


def _repair_ifind_after_recompile(conn, schema: str) -> None:
    """Make the schema's iFind indexes searchable again after the package compile.

    Best-effort, and deliberately not a refusal: an index left unsearchable is a dead
    text leg, not a lost one — recompiling its owning class fixes it at any later time,
    whereas refusing here would abandon an upgrade over a repairable index. What the
    repair could not fix is logged by :func:`repair_ifind_helpers` itself.
    """
    cursor = conn.cursor()
    try:
        repaired = repair_ifind_helpers(cursor, conn=conn, schema=schema)
        broken = sorted(name for name, ok in repaired.items() if not ok)
    except Exception as exc:  # pragma: no cover - driver-dependent
        logger.debug("iFind helper repair skipped: %s", exc)
        return
    finally:
        _close(cursor)
    if broken:
        logger.warning(
            "iFind index not searchable after the %s recompile: %s. Text search through "
            "it fails with <CLASS DOES NOT EXIST> until the owning class is recompiled.",
            REBUILD_PACKAGE,
            ", ".join(broken),
        )


def _require_rebuild_entry_points(conn) -> None:
    """Refuse before the kill when a rebuild method has no compiled entry point.

    ``Kill`` then ``classMethodValue`` is the whole step, and the kill is not
    reversible: a class that fails to compile turns the second half into
    ``ERROR #5123: Unable to find entry point``, and by then the flat entries are gone
    and the scoped ones were never written.
    """
    cursor = conn.cursor()
    try:
        missing = [
            (cls, method)
            for cls, method in REBUILD_STEPS
            if not _compiled_method(cursor, cls, method)
        ]
    finally:
        _close(cursor)
    if missing:
        named = ", ".join(f"{cls}::{method}" for cls, method in missing)
        raise RuntimeError(
            f"the ^KG rebuild is ordered out of {named}, and IRIS has no compiled entry "
            f"point for it. A recompile of {REBUILD_PACKAGE} did not produce one, so the "
            "class either is not loaded in this namespace or does not compile — check "
            "the compiler output for the class, then re-run. Nothing has been dropped."
        )


def _compiled_method(cursor, class_name: str, method: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM %Dictionary.CompiledMethod WHERE parent = ? AND Name = ?",
        [class_name, method],
    )
    rows = list(cursor.fetchall() or [])
    return bool(rows and rows[0] and rows[0][0])


def _table_exists(cursor, schema: str, table: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = ?",
        [table],
    )
    rows = list(cursor.fetchall() or [])
    return bool(rows and rows[0] and rows[0][0])


def _declares(cursor, schema: str, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}' "
        "AND COLUMN_NAME = ?",
        [column],
    )
    rows = list(cursor.fetchall() or [])
    return bool(rows and rows[0] and rows[0][0])


def _native(conn):
    """The Native API handle for `conn`.

    A connection that already carries one — the tests' stand-in, and the wrappers
    that keep a dedicated native connection of their own — is used as given, because
    ``createIRIS()`` on a connection that has since run cursor DDL is the corruption
    ``engine.py:1106`` documents.
    """
    existing = getattr(conn, "iris", None)
    if existing is not None:
        return existing
    import iris as _iris

    return _iris.createIRIS(conn)


def _count_entries(native, store: str) -> int:
    return sum(1 for _ in _leaves(native, (store,)))


def _count_per_graph(native, store: str) -> Dict[str, int]:
    """``{graph_id: entries}`` for one store, read back out of the tree."""
    counts: Dict[str, int] = {}
    for graph in _subscripts(native, (store,)):
        name = _graph_name(graph)
        counts[name] = counts.get(name, 0) + sum(1 for _ in _leaves(native, (store, graph)))
    return counts


def _graph_name(subscript: str) -> str:
    """The SQL spelling of a `^KG` graph subscript.

    ``Graph.KG.GraphKey.ForIndex`` writes the integer ``0`` for the default graph and
    the graph's own name otherwise, and ``Validate`` rejects the *name* ``"0"`` — so
    this mapping is total, not a guess.
    """
    return "" if str(subscript) == "0" else str(subscript)


def _leaves(native, path: Tuple[str, ...]) -> Iterator[Tuple[str, ...]]:
    """Every subscript path below `path` that holds a value.

    A value, not a node: an interior subscript exists because something below it
    does, and counting those would count each entry once per level.
    """
    if len(path) > _MAX_DEPTH:
        logger.debug("^KG walk stopped at depth %d: %r", len(path), path)
        return
    if _value_at(native, path) is not None:
        yield path
    for subscript in _subscripts(native, path):
        yield from _leaves(native, path + (subscript,))


def _subscripts(native, path: Tuple[str, ...]) -> List[str]:
    """The immediate children of `^KG(*path)`, in collation order."""
    children: List[str] = []
    after = ""
    while True:
        nxt = native.nextSubscript(False, "^KG", *path, after)
        if nxt is None or str(nxt) == "":
            return children
        children.append(str(nxt))
        after = nxt


def _value_at(native, path: Tuple[str, ...]):
    try:
        return native.getString("^KG", *path)
    except Exception as e:  # pragma: no cover - driver-dependent on an interior node
        logger.debug("No value at ^KG%r: %s", path, e)
        return None


def _commit(conn) -> None:
    try:
        conn.commit()
    except Exception as e:  # pragma: no cover - autocommit connections
        logger.debug("Commit not needed: %s", e)


def _close(cursor) -> None:
    try:
        cursor.close()
    except Exception:  # pragma: no cover - driver-dependent
        pass
