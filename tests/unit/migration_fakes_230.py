"""In-memory stand-in for IRIS while placing `Graph_KG.docs` rows in a graph.

Spec 230 US2's migration has to account for every pre-migration document: placed
in exactly one graph, or quarantined with a reason, never deleted and never
guessed (FR-018, FR-020). That is bookkeeping, and bookkeeping is provable without
a database — provided the fake refuses the things IRIS refuses.

Separate from `migration_fakes_227.py` rather than an extension of it: that fake
speaks the 227 statement shapes (a table reshape, an `INSERT … SELECT` drain,
routed embedding columns) and `docs` is a different move — the table gains a
column and its rows stay put, so placement is an `UPDATE`. Bending the shared fake
to serve both would leave two passing 227 test files depending on branches written
for `docs`.

What the fake enforces, because IRIS does:

* `graph_id IS NULL` narrows a read. A migration that filters on it and gets every
  row back looks idempotent when it is not.
* `UPDATE … WHERE graph_id IS NULL` touches no already-placed row, and reports how
  many rows it changed.
* The quarantine table rejects a duplicate `doc_id` with SQLCODE -119, the way a
  primary key does, so a second run that re-quarantines is a failure and not a
  silently doubled count.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional, Sequence


class _Error(Exception):
    """Stands in for an `iris.dbapi` error: the message carries the SQLCODE."""


class DocsRegistry:
    """The rows the fake holds, and the answers it derives from them."""

    def __init__(
        self,
        docs: Iterable[Mapping[str, object]],
        node_graphs: Mapping[str, Sequence[str]],
        *,
        has_graph_column: bool = True,
        has_docs_table: bool = True,
        has_staging_table: bool = False,
    ):
        #: `{"id": ..., "text": ..., "graph_id": None}` — `None` means unplaced.
        self.docs: list[dict] = [
            {"id": row["id"], "text": row.get("text", ""), "graph_id": row.get("graph_id")}
            for row in docs
        ]
        #: Which graphs hold each node ID. The empty string is the default graph.
        self.node_graphs = {node: list(graphs) for node, graphs in node_graphs.items()}
        self.has_graph_column = has_graph_column
        #: `docs` itself. `finish_docs` drops it one statement before the rename, so a
        #: pass killed in between leaves an install with no `docs` at all.
        self.has_docs_table = has_docs_table
        #: The rebuilt table waiting to be renamed over `docs`.
        self.has_staging_table = has_staging_table
        self.quarantine: list[dict] = []
        #: Every id ever removed from `docs`, so a test can prove none were.
        self.deleted: list[str] = []
        self.executed: list[tuple[str, tuple]] = []
        self.ddl: list[str] = []

    # -- helpers the tests read ------------------------------------------------

    def placed(self) -> dict[str, Optional[str]]:
        return {row["id"]: row["graph_id"] for row in self.docs}

    def quarantined(self) -> dict[str, str]:
        return {row["doc_id"]: row["reason"] for row in self.quarantine}

    def unplaced(self) -> list[str]:
        return sorted(row["id"] for row in self.docs if row["graph_id"] is None)

    def snapshot(self) -> tuple:
        """Everything a second run must leave identical."""
        return (
            sorted((r["id"], r["text"], r["graph_id"]) for r in self.docs),
            sorted((r["doc_id"], r["reason"]) for r in self.quarantine),
        )


class DocsCursor:
    """Answers the handful of statements the placement issues, and nothing else.

    An unrecognised statement raises rather than returning an empty result: a
    migration that drifts to a statement the fake never modelled would otherwise
    pass by doing nothing.
    """

    def __init__(self, registry: DocsRegistry):
        self.registry = registry
        self._rows: list[tuple] = []
        self.rowcount = -1

    # -- DB-API surface -------------------------------------------------------

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        args = tuple(params or ())
        self.registry.executed.append((text, args))
        self._rows = []
        self.rowcount = -1
        for handler in (
            self._ddl,
            self._column_probe,
            self._count,
            self._unplaced_scan,
            self._graphs_of_node,
            self._quarantine_scan,
            self._place,
            self._quarantine_insert,
            self._delete,
        ):
            if handler(text, args):
                return self
        raise AssertionError(f"fake has no handler for: {text!r} {args!r}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    # -- handlers -------------------------------------------------------------

    def _ddl(self, text, args):
        if re.match(r"(?i)^(CREATE|ALTER|DROP)\b", text):
            self.registry.ddl.append(text)
            if re.search(r"(?i)ALTER TABLE .*docs .*ADD COLUMN .*graph_id", text):
                self.registry.has_graph_column = True
            return True
        return False

    def _column_probe(self, text, args):
        if "INFORMATION_SCHEMA.COLUMNS" not in text.upper():
            return False
        wanted = next((a for a in args if isinstance(a, str)), "")
        table = re.search(r"(?i)TABLE_NAME = '([^']+)'", text)
        asked = table.group(1) if table else "docs"
        if asked.lower().endswith("_ivg400"):
            # A table nobody dropped reports no columns, which is how the migrator
            # tells a missing table from a missing column.
            present = self.registry.has_staging_table
        else:
            present = self.registry.has_docs_table and (
                self.registry.has_graph_column or wanted.lower() != "graph_id"
            )
        self._rows = [(1 if present else 0,)]
        return True

    def _count(self, text, args):
        match = re.match(r"(?i)^SELECT COUNT\(\*\) FROM [\w.]*?(docs_quarantine|docs)\s*$", text)
        if not match:
            return False
        table = match.group(1)
        rows = self.registry.quarantine if table == "docs_quarantine" else self.registry.docs
        self._rows = [(len(rows),)]
        return True

    def _unplaced_scan(self, text, args):
        """`SELECT TOP n id, text FROM … docs WHERE graph_id IS NULL AND id > ? ORDER BY id`."""
        if not re.search(r"(?i)FROM [\w.]*docs\b", text) or "graph_id IS NULL" not in text:
            return False
        if not re.match(r"(?i)^SELECT", text):
            return False
        after = args[0] if args else ""
        limit = (
            int(re.search(r"(?i)TOP (\d+)", text).group(1))
            if re.search(r"(?i)TOP \d+", text)
            else None
        )
        rows = sorted(
            (r for r in self.registry.docs if r["graph_id"] is None and str(r["id"]) > str(after)),
            key=lambda r: str(r["id"]),
        )
        if limit is not None:
            rows = rows[:limit]
        self._rows = [(r["id"], r["text"]) for r in rows]
        return True

    def _graphs_of_node(self, text, args):
        if not re.search(r"(?i)FROM [\w.]*nodes\b", text):
            return False
        node = args[0] if args else None
        self._rows = [(g,) for g in sorted(self.registry.node_graphs.get(node, []))]
        return True

    def _quarantine_scan(self, text, args):
        if not re.search(r"(?i)^SELECT .*FROM [\w.]*docs_quarantine\b", text):
            return False
        self._rows = [(r["doc_id"], r["reason"]) for r in self.registry.quarantine]
        return True

    def _place(self, text, args):
        """`UPDATE … docs SET graph_id = ? WHERE id = ? AND graph_id IS NULL`."""
        if not re.match(r"(?i)^UPDATE [\w.]*docs\b", text):
            return False
        assert (
            "graph_id IS NULL" in text
        ), f"placement must not overwrite an already-placed row: {text!r}"
        graph, doc_id = args[0], args[1]
        changed = 0
        for row in self.registry.docs:
            if row["id"] == doc_id and row["graph_id"] is None:
                row["graph_id"] = graph
                changed += 1
        self.rowcount = changed
        return True

    def _quarantine_insert(self, text, args):
        if not re.match(r"(?i)^INSERT INTO [\w.]*docs_quarantine\b", text):
            return False
        doc_id, body, reason = args[0], args[1], args[2]
        if any(r["doc_id"] == doc_id for r in self.registry.quarantine):
            raise _Error("SQLCODE=-119 unique constraint violation on docs_quarantine")
        self.registry.quarantine.append({"doc_id": doc_id, "text": body, "reason": reason})
        self.rowcount = 1
        return True

    def _delete(self, text, args):
        if not re.match(r"(?i)^DELETE FROM [\w.]*docs\b", text):
            return False
        doc_id = args[0] if args else None
        before = len(self.registry.docs)
        self.registry.docs = [
            r for r in self.registry.docs if not (r["id"] == doc_id and r["graph_id"] is None)
        ]
        removed = before - len(self.registry.docs)
        if removed:
            self.registry.deleted.append(doc_id)
        self.rowcount = removed
        return True


class DocsConnection:
    def __init__(self, registry: DocsRegistry):
        self.registry = registry
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return DocsCursor(self.registry)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def docs_conn(docs, node_graphs, **kwargs) -> DocsConnection:
    """A connection over `docs` rows and the graphs their IDs live in."""
    return DocsConnection(DocsRegistry(docs, node_graphs, **kwargs))


# ---------------------------------------------------------------------------
# Edge vectors
# ---------------------------------------------------------------------------
#
# A pre-4.0.0 `kg_EdgeEmbeddings` is keyed `(s, p, o_id)` with no `graph_id` and no
# `metadata`, and 4.0.0 wants an identity primary key, which no `ALTER` reaches. So
# the rows move to a staging table in the 4.0.0 shape and the staging table is
# renamed into place — the same move spec 227 made for the node tables.
#
# The fake therefore models three tables rather than one, and enforces what IRIS
# enforces about them:
#
# * the staging table's `(graph_id, s, p, o_id)` unique constraint rejects a second
#   copy with SQLCODE -119, which is what makes a resumed run idempotent instead of
#   doubling rows;
# * a vector only ever moves by `INSERT ... SELECT`, so a statement that names `emb`
#   in a plain `SELECT` is an ADR-0005 violation and the fake refuses it;
# * the source table keeps its rows: the reshape is a separate step, and a placement
#   that deleted as it went would leave a killed run with vectors in neither table.


class EdgeVectorRegistry:
    """The rows the edge-vector fake holds, and the answers it derives from them."""

    def __init__(
        self,
        vectors: Iterable[Mapping[str, object]],
        edge_graphs: Mapping[tuple, Sequence[str]],
    ):
        #: `{"s": ..., "p": ..., "o_id": ..., "emb": ...}` — the 3.2.0 source table.
        self.source: list[dict] = [
            {
                "s": row["s"],
                "p": row["p"],
                "o_id": row["o_id"],
                "emb": row.get("emb", "vector-payload"),
            }
            for row in vectors
        ]
        #: Which graphs assert each `(s, p, o_id)`. The empty string is the default.
        self.edge_graphs = {tuple(key): list(graphs) for key, graphs in edge_graphs.items()}
        #: Rows moved into the 4.0.0-shaped staging table, each with its graph.
        self.staging: list[dict] = []
        self.quarantine: list[dict] = []
        self.deleted: list[tuple] = []
        self.executed: list[tuple[str, tuple]] = []
        self.ddl: list[str] = []

    # -- helpers the tests read ------------------------------------------------

    def placed(self) -> dict[tuple, str]:
        return {(r["s"], r["p"], r["o_id"]): r["graph_id"] for r in self.staging}

    def quarantined(self) -> dict[tuple, str]:
        return {(r["s"], r["p"], r["o_id"]): r["reason"] for r in self.quarantine}

    def snapshot(self) -> tuple:
        return (
            sorted((r["s"], r["p"], r["o_id"], r["graph_id"], r["emb"]) for r in self.staging),
            sorted((r["s"], r["p"], r["o_id"], r["reason"]) for r in self.quarantine),
            sorted((r["s"], r["p"], r["o_id"]) for r in self.source),
        )


class EdgeVectorCursor:
    """Answers the statements the edge-vector placement issues, and nothing else."""

    def __init__(self, registry: EdgeVectorRegistry):
        self.registry = registry
        self._rows: list[tuple] = []
        self.rowcount = -1

    # -- DB-API surface -------------------------------------------------------

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        args = tuple(params or ())
        self.registry.executed.append((text, args))
        self._rows = []
        self.rowcount = -1
        self._refuse_read_vectors(text)
        for handler in (
            self._ddl,
            self._graphs_of_edge,
            self._staged_scan,
            self._quarantine_scan,
            self._source_scan,
            self._place,
            self._quarantine_insert,
            self._delete,
        ):
            if handler(text, args):
                return self
        raise AssertionError(f"fake has no handler for: {text!r} {args!r}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    # -- handlers -------------------------------------------------------------

    @staticmethod
    def _refuse_read_vectors(text) -> None:
        """A vector may be copied inside IRIS, never fetched into Python (ADR-0005)."""
        if re.match(r"(?i)^SELECT\b", text) and re.search(r"(?i)\bemb\b", text):
            raise AssertionError(f"a vector must not be read into Python (ADR-0005): {text!r}")

    def _ddl(self, text, args):
        if re.match(r"(?i)^(CREATE|ALTER|DROP)\b", text):
            self.registry.ddl.append(text)
            return True
        return False

    def _graphs_of_edge(self, text, args):
        if not re.search(r"(?i)FROM [\w.]*rdf_edges\b", text):
            return False
        key = tuple(args[:3])
        self._rows = [(g,) for g in sorted(self.registry.edge_graphs.get(key, []))]
        return True

    def _staged_scan(self, text, args):
        if not re.match(r"(?i)^SELECT .*FROM [\w.]*kg_EdgeEmbeddings_ivg400\b", text):
            return False
        self._rows = [(r["s"], r["p"], r["o_id"]) for r in self.registry.staging]
        return True

    def _quarantine_scan(self, text, args):
        if not re.match(r"(?i)^SELECT .*FROM [\w.]*edge_vector_quarantine\b", text):
            return False
        self._rows = [(r["s"], r["p"], r["o_id"], r["reason"]) for r in self.registry.quarantine]
        return True

    def _source_scan(self, text, args):
        if not re.match(r"(?i)^SELECT .*FROM [\w.]*kg_EdgeEmbeddings\b", text):
            return False
        rows = sorted(
            self.registry.source, key=lambda r: (str(r["s"]), str(r["p"]), str(r["o_id"]))
        )
        self._rows = [(r["s"], r["p"], r["o_id"]) for r in rows]
        return True

    def _place(self, text, args):
        """`INSERT INTO … _ivg400 (…) SELECT ?, s, p, o_id, emb, … FROM … WHERE …`."""
        if not re.match(r"(?i)^INSERT INTO [\w.]*kg_EdgeEmbeddings_ivg400\b", text):
            return False
        assert re.search(
            r"(?i)\bSELECT\b", text
        ), f"a vector moves by INSERT … SELECT, not by a bound value: {text!r}"
        graph, key = args[0], tuple(args[1:4])
        row = next((r for r in self.registry.source if (r["s"], r["p"], r["o_id"]) == key), None)
        if row is None:
            self.rowcount = 0
            return True
        if any(
            (r["s"], r["p"], r["o_id"]) == key and r["graph_id"] == graph
            for r in self.registry.staging
        ):
            raise _Error("SQLCODE=-119 unique constraint violation on kg_EdgeEmbeddings_ivg400")
        self.registry.staging.append({**row, "graph_id": graph, "metadata": None})
        self.rowcount = 1
        return True

    def _quarantine_insert(self, text, args):
        if not re.match(r"(?i)^INSERT INTO [\w.]*edge_vector_quarantine\b", text):
            return False
        assert re.search(
            r"(?i)\bSELECT\b", text
        ), f"a quarantined vector moves by INSERT … SELECT too: {text!r}"
        reason, key = args[0], tuple(args[1:4])
        row = next((r for r in self.registry.source if (r["s"], r["p"], r["o_id"]) == key), None)
        if row is None:
            self.rowcount = 0
            return True
        if any((r["s"], r["p"], r["o_id"]) == key for r in self.registry.quarantine):
            raise _Error("SQLCODE=-119 unique constraint violation on edge_vector_quarantine")
        self.registry.quarantine.append({**row, "reason": reason})
        self.rowcount = 1
        return True

    def _delete(self, text, args):
        if not re.match(r"(?i)^DELETE FROM [\w.]*kg_EdgeEmbeddings\b", text):
            return False
        key = tuple(args[:3])
        before = len(self.registry.source)
        self.registry.source = [
            r for r in self.registry.source if (r["s"], r["p"], r["o_id"]) != key
        ]
        if before != len(self.registry.source):
            self.registry.deleted.append(key)
        self.rowcount = before - len(self.registry.source)
        return True


class EdgeVectorConnection:
    def __init__(self, registry: EdgeVectorRegistry):
        self.registry = registry
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return EdgeVectorCursor(self.registry)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def edge_vectors_conn(vectors, edge_graphs) -> EdgeVectorConnection:
    """A connection over `kg_EdgeEmbeddings` rows and the graphs asserting them."""
    return EdgeVectorConnection(EdgeVectorRegistry(vectors, edge_graphs))


# ---------------------------------------------------------------------------
# The ^KG node stores
# ---------------------------------------------------------------------------
#
# `^KG("prop")`, `^KG("label")`, `^KG("deg2p")` and `^KG("deg2p_exact")` gain a graph
# subscript in 4.0.0, and an upgraded install holds entries in the flat layout that no
# graph-scoped reader will ever see. The migration drops them and has the server
# rebuild from the graph-scoped SQL rows.
#
# The fake therefore plays the *server*, not the globals: it owns the rebuild, so a
# migrator that wrote entries itself would be a second writer of a layout only
# `Graph.KG.TraversalBuild` is allowed to spell. It enforces that by refusing `set`
# on `^KG` outright.
#
# What else it enforces, because IRIS does:
#
# * a graph of `''` or `None` keys the subscript `0`, and a named graph keys itself
#   (`Graph.KG.GraphKey.ForIndex`, ADR-0003);
# * `nextSubscript` answers `None` past the last sibling and does not distinguish "no
#   value here" from "nothing here" — which is why a missed call site is silent;
# * a `Kill` of a subtree takes every descendant, and a `Kill` of a subtree that does
#   not exist is not an error.


def _index_key(graph):
    """`Graph.KG.GraphKey.ForIndex` — the default graph is the integer 0."""
    name = "" if graph is None else str(graph).replace("\x00", "")
    return 0 if name == "" else name


def _canon_sub(sub):
    """One subscript in canonical form, the way IRIS stores it.

    `^KG("prop", 0, ...)` and `^KG("prop", "0", ...)` are the same node on a real
    server: a subscript in canonical numeric form collates and compares as the
    number. `nextSubscript` hands a subscript back as a string, so a caller that
    feeds it straight into `getString` — which is the only thing a walk can do — has
    to land on the node it just found. Without this the fake would fail a correct
    migration and pass one that only ever walked named graphs.
    """
    text = str(sub)
    if text.lstrip("-").isdigit() and text == str(int(text)):
        return int(text)
    return text


class KgStoresRegistry:
    """The SQL rows the rebuild reads, and the subscript tree it writes."""

    def __init__(
        self,
        props: Iterable[Mapping[str, object]] = (),
        labels: Iterable[Mapping[str, object]] = (),
        edges: Iterable[Mapping[str, object]] = (),
        *,
        flat_entries: Mapping[tuple, object] = None,
        scoped_columns: Iterable[str] = ("rdf_props", "rdf_labels", "rdf_edges"),
        absent_tables: Iterable[str] = (),
        compiled_methods: Iterable[tuple] = None,
        compile_repairs: bool = True,
    ):
        self.props = [dict(row) for row in props]
        self.labels = [dict(row) for row in labels]
        self.edges = [dict(row) for row in edges]
        #: `{("prop", "n1", "name"): "x"}` — the pre-4.0.0 layout, as an upgraded
        #: install holds it. The migration has to leave none of it behind.
        self.tree: dict = dict(flat_entries or {})
        #: Which source tables declare `graph_id`. A table that does not is why the
        #: migration refuses rather than defaulting every entry to graph 0.
        self.scoped_columns = set(scoped_columns)
        #: Tables that do not exist at all. `INFORMATION_SCHEMA.COLUMNS` answers 0 for
        #: these exactly as it does for a table missing one column, which is how a
        #: deleted `Graph_KG.rdf_edges` got reported as an un-migrated one.
        self.absent_tables = set(absent_tables)
        #: Which `(class, method)` pairs `%Dictionary.CompiledMethod` answers for. On a
        #: real 3.2.0 install the deploy compiled `Graph.KG.TraversalBuild` while
        #: `rdf_labels` still had no `graph_id`, so its embedded SQL failed and the
        #: method has no entry point — measured as `ERROR #5123` from the rebuild.
        self.compiled_methods = (
            {("Graph.KG.TraversalBuild", "BuildKG"),
             ("Graph.KG.TraversalBuild", "Build2HopStats"),
             ("Graph.KG.TraversalBuild", "Build2HopExactStats")}
            if compiled_methods is None
            else {tuple(pair) for pair in compiled_methods}
        )
        #: Whether recompiling the package brings the missing entry points back — true
        #: when the only thing wrong was the column the class reads, false when the
        #: class is broken for some other reason.
        self.compile_repairs = compile_repairs
        self.killed: list = []
        self.rebuilds: list = []
        self.compiles: list = []
        self.executed: list = []
        #: Every ordered event, so a test can assert the recompile precedes the kill.
        self.order: list = []

    # -- helpers the tests read ------------------------------------------------

    def entries(self, store: str) -> dict:
        return {k[1:]: v for k, v in self.tree.items() if k and k[0] == store}

    def snapshot(self) -> tuple:
        return tuple(sorted((tuple(map(str, k)), str(v)) for k, v in self.tree.items()))

    # -- the rebuild the server owns ------------------------------------------

    def build_kg(self) -> int:
        """`Graph.KG.TraversalBuild.BuildKG`, in the 4.0.0 layout.

        Labels and properties are derived from the rows' own `graph_id`. A row whose
        `graph_id` is NULL is a default-graph row — not a row whose graph is looked up
        from somewhere else (FR-011).
        """
        self.rebuilds.append("BuildKG")
        self.order.append(("rebuild", "BuildKG"))
        for store in ("label", "prop", "out"):
            self._kill(store)
        for row in self.labels:
            key = _index_key(row.get("graph_id"))
            self.tree[("label", key, row["label"], row["s"])] = ""
        for row in self.props:
            key = _index_key(row.get("graph_id"))
            self.tree[("prop", key, row["s"], row["key"])] = row.get("val", "")
        for row in self.edges:
            key = _index_key(row.get("graph_id"))
            self.tree[("out", key, row["s"], row["p"], row["o_id"])] = row.get("weight", 1)
        return len(self.labels) + len(self.props) + len(self.edges)

    def build_2hop(self) -> int:
        """`Graph.KG.TraversalBuild.Build2HopStats`, per graph.

        Counted from `^KG("out", graphKey, ...)` alone, so a two-hop number can only
        be built out of edges that graph asserts — the cross-graph sum this story
        removes is not reachable from here.
        """
        self.rebuilds.append("Build2HopStats")
        self._kill("deg2p")
        written = 0
        for store, key, src, pred, mid in [k for k in self.tree if k[0] == "out"]:
            count = len([k for k in self.tree if k[0] == "out" and k[1] == key and k[2] == mid])
            if not count:
                continue
            slot = ("deg2p", key, src, pred)
            self.tree[slot] = self.tree.get(slot, 0) + count
            written += 1
        return written

    # -- the globals ----------------------------------------------------------

    def _kill(self, *path) -> None:
        prefix = tuple(_canon_sub(p) for p in path)
        for key in [k for k in self.tree if k[: len(prefix)] == prefix]:
            del self.tree[key]


class KgStoresCursor:
    """Answers the column probes and the row counts the migration reads."""

    def __init__(self, registry: KgStoresRegistry):
        self.registry = registry
        self._rows: list = []
        self.rowcount = -1

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        args = tuple(params or ())
        self.registry.executed.append((text, args))
        self._rows = []
        if "INFORMATION_SCHEMA.TABLES" in text.upper():
            table = re.search(r"(?i)TABLE_NAME = '([^']+)'", text)
            asked = table.group(1) if table else ""
            if not asked:
                asked = next((a for a in args if isinstance(a, str)), "")
            self._rows = [(0 if asked in self.registry.absent_tables else 1,)]
            return self
        if "INFORMATION_SCHEMA.COLUMNS" in text.upper():
            table = re.search(r"(?i)TABLE_NAME = '([^']+)'", text)
            asked = table.group(1) if table else ""
            wanted = next((a for a in args if isinstance(a, str)), "graph_id")
            if asked in self.registry.absent_tables:
                present = False  # no table, so no column either
            else:
                present = asked in self.registry.scoped_columns or wanted.lower() != "graph_id"
            self._rows = [(1 if present else 0,)]
            return self
        if "%DICTIONARY.COMPILEDMETHOD" in text.upper():
            pair = tuple(str(a) for a in args[:2])
            self._rows = [(1 if pair in self.registry.compiled_methods else 0,)]
            return self
        raise AssertionError(f"fake has no handler for: {text!r} {args!r}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class KgStoresNative:
    """The Native API surface the migration is allowed to use, and no more."""

    def __init__(self, registry: KgStoresRegistry):
        self.registry = registry

    # -- reads ----------------------------------------------------------------

    def nextSubscript(self, _reverse, name, *subs):
        assert name == "^KG", f"the rebuild step has no business in {name}"
        prefix = tuple(_canon_sub(s) for s in subs[:-1])
        after = subs[-1]
        siblings = sorted(
            {
                str(key[len(prefix)])
                for key in self.registry.tree
                if len(key) > len(prefix) and key[: len(prefix)] == prefix
            }
        )
        for sibling in siblings:
            if str(after) == "" or sibling > str(after):
                return sibling
        return None

    def getString(self, name, *subs):
        assert name == "^KG"
        value = self.registry.tree.get(tuple(_canon_sub(s) for s in subs))
        return None if value is None else str(value)

    # -- writes ---------------------------------------------------------------

    def kill(self, name, *subs):
        assert name == "^KG", f"the rebuild step has no business in {name}"
        self.registry.killed.append(tuple(subs))
        self.registry.order.append(("kill",) + tuple(str(s) for s in subs))
        self.registry._kill(*subs)

    def set(self, *_a, **_kw):
        raise AssertionError(
            "the migration may not write ^KG entries itself: the layout has one "
            "writer, Graph.KG.TraversalBuild (FR-010)"
        )

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("Graph.KG.GraphKey", "ForIndex"):
            return _index_key(args[0] if args else "")
        if (cls, method) == ("%SYSTEM.OBJ", "CompilePackage"):
            self.registry.compiles.append(tuple(str(a) for a in args))
            self.registry.order.append(("compile",) + tuple(str(a) for a in args))
            if self.registry.compile_repairs:
                # A recompile of already-loaded source: the class definition is in the
                # namespace, only its compiled form was missing because the embedded
                # SQL named a column that did not exist yet.
                self.registry.compiled_methods |= {
                    (name, meth)
                    for name, meth in (
                        ("Graph.KG.TraversalBuild", "BuildKG"),
                        ("Graph.KG.TraversalBuild", "Build2HopStats"),
                        ("Graph.KG.TraversalBuild", "Build2HopExactStats"),
                    )
                    if name.startswith(str(args[0] if args else ""))
                }
            return 1
        if cls in ("Graph.KG.Traversal", "Graph.KG.TraversalBuild"):
            if method == "BuildKG":
                return self.registry.build_kg()
            if method in ("Build2HopStats", "Build2HopExactStats"):
                return self.registry.build_2hop()
        raise AssertionError(f"fake has no handler for {cls}.{method}({args!r})")

    def classMethodVoid(self, cls, method, *args):
        self.classMethodValue(cls, method, *args)


class KgStoresConnection:
    def __init__(self, registry: KgStoresRegistry):
        self.registry = registry
        self.iris = KgStoresNative(registry)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return KgStoresCursor(self.registry)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def kg_stores_conn(**kwargs) -> KgStoresConnection:
    """A connection over the graph-scoped SQL rows and the `^KG` tree built from them."""
    return KgStoresConnection(KgStoresRegistry(**kwargs))
