"""A 3.2.0-shaped database the migration can be driven against (spec 227, Phase 7).

Not a test file — the shared fake for `test_227_migration_bookkeeping.py` and
`test_227_migration_idempotence.py`. It exists because the migration's guarantees are
about *where rows end up*, and asserting that against mocked statement text proves
only that the statements were spelled a certain way. So this holds rows, moves them,
and refuses the moves IRIS would refuse.

What it models, and why the migration depends on each part:

- **A 3.2.0 embedding table**: `id VARCHAR PRIMARY KEY, emb, metadata`, with no
  `graph_id` and no `node_id`. The reshape is the reason the migration cannot simply
  `UPDATE ... SET graph_id`, so the fake answers the column probe honestly.
- **`nodes` as a node ID → graph list**. One graph is a placement, none is `no_node`,
  two is ambiguous. Nothing else decides a row's graph.
- **`(graph_id, node_id)` uniqueness on every target**, raising IRIS's `SQLCODE -119`.
  That constraint, not a check-then-insert, is what makes a re-run idempotent, so a
  fake that quietly accepted the second copy would make a broken migration pass.
- **An interruption**: `fail_after=N` raises on the Nth row-moving statement, which is
  the only way to test a resume against the state a half-finished pass leaves.

Registry reads and writes are inherited from :class:`route_fakes_227.FakeRegistry` —
the migration creates routes through the same `resolve_route` a write does, and a
second registry fake would let the two disagree.
"""

from typing import Any, Dict, List, Optional, Tuple

from tests.unit.route_fakes_227 import FakeConnection, FakeCursor, FakeRegistry, _table_after

#: Columns a 3.2.0 install declares on both embedding tables.
LEGACY_COLUMNS = ("id", "emb", "metadata")

#: Columns a 4.0.0 embedding table declares — legacy names and routed tables alike.
ROUTED_COLUMNS = ("emb_rowid", "graph_id", "node_id", "emb", "metadata")


class MigrationRegistry(FakeRegistry):
    """Tables with rows in them, plus the registry :class:`FakeRegistry` already models."""

    def __init__(
        self,
        *,
        source_rows: Optional[Dict[str, List[dict]]] = None,
        node_graphs: Optional[Dict[str, List[str]]] = None,
        labels: Optional[List[dict]] = None,
        props: Optional[List[dict]] = None,
        quarantine: Optional[List[dict]] = None,
        data: Optional[Dict[str, List[dict]]] = None,
        columns: Optional[Dict[str, Tuple[str, ...]]] = None,
        fail_after: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        #: node_id -> the graphs holding a node with that ID.
        self.node_graphs: Dict[str, List[str]] = {
            k: list(v) for k, v in dict(node_graphs or {}).items()
        }
        self.nodes = {(g, n) for n, graphs in self.node_graphs.items() for g in graphs}

        #: Every table that holds rows, keyed on its bare name.
        self.data: Dict[str, List[dict]] = {
            "rdf_labels": [dict(r) for r in (labels or [])],
            "rdf_props": [dict(r) for r in (props or [])],
            "embedding_quarantine": [dict(r) for r in (quarantine or [])],
        }
        for table, rows in dict(source_rows or {}).items():
            self.data[table] = [dict(r) for r in rows]
        for table, rows in dict(data or {}).items():
            self.data[table] = [dict(r) for r in rows]

        #: Declared columns per table. The migration reads this to decide whether a
        #: table still has 3.2.0's shape, so it is the fake's most load-bearing answer.
        self.columns: Dict[str, Tuple[str, ...]] = {
            "nodes": ("node_id", "graph_id"),
            "rdf_labels": ("s", "label"),
            "rdf_props": ("s", "key", "val"),
            "embedding_quarantine": (
                "q_rowid",
                "node_id",
                "source_table",
                "emb",
                "dimension",
                "dtype",
                "metadata",
                "reason",
            ),
        }
        for table in dict(source_rows or {}):
            self.columns.setdefault(table, LEGACY_COLUMNS)
        self.columns.update({k: tuple(v) for k, v in dict(columns or {}).items()})
        for table in self.columns:
            self.data.setdefault(table, [])
        self.tables = sorted(set(self.tables) | set(self.columns))

        #: Raise on this many-th row-moving statement, simulating a killed process.
        self.fail_after = fail_after
        self.moves = 0
        self.ddl: List[str] = []
        self.dropped: List[str] = []
        self.renamed: List[Tuple[str, str]] = []
        self._q_rowid = max(
            [int(r.get("q_rowid") or 0) for r in self.data["embedding_quarantine"]] or [0]
        )

    # ------------------------------------------------------------------ helpers

    def cursor(self):
        return MigrationCursor(self)

    def rows_in(self, table: str) -> List[dict]:
        return self.data.get(table.split(".")[-1], [])

    def placed_in(self, table: str) -> List[Tuple[str, str]]:
        """`(graph_id, node_id)` of every row that reached ``table``."""
        return [(r.get("graph_id", ""), r.get("node_id")) for r in self.rows_in(table)]

    def quarantined(self) -> List[dict]:
        return self.rows_in("embedding_quarantine")

    def next_q_rowid(self) -> int:
        self._q_rowid += 1
        return self._q_rowid

    def note_move(self) -> None:
        """Count a row-moving statement, and raise once if this pass is to be killed.

        The interruption fires once and clears itself, because ``fail_after`` models a
        killed *process*: the re-run is a new one, and a fake that kept raising would
        make every resume impossible rather than testing it.
        """
        self.moves += 1
        if self.fail_after is not None and self.moves > self.fail_after:
            self.fail_after = None
            raise RuntimeError("[SQLCODE: <-99>] the migration process was killed")


class MigrationCursor(FakeCursor):
    """A cursor that actually moves rows, and refuses what IRIS would refuse."""

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        args = list(params or [])
        upper = flat.upper()

        for handler in (
            self._column_probe,
            self._graphs_of_node,
            self._quarantine_scan,
            self._registry_scan,
            self._keys_of,
            self._max_of,
            self._structural_read,
            self._insert_select,
            self._insert_values,
            self._update_registry_fields,
            self._delete,
            self._ddl,
        ):
            handled = handler(flat, upper, args)
            if handled is not None:
                self.registry.statements.append((flat, args))
                self._rows = handled
                return self
        return super().execute(sql, params)

    # ------------------------------------------------------------------- reading

    def _column_probe(self, flat, upper, args):
        if "INFORMATION_SCHEMA.COLUMNS" not in upper or "COLUMN_NAME = ?" not in upper:
            return None
        table = str(args[1] if len(args) > 1 else "").split(".")[-1]
        column = str(args[2] if len(args) > 2 else "")
        declared = self.registry.columns.get(table)
        if declared is None:
            return [(0,)]
        return [(1 if column in declared else 0,)]

    def _graphs_of_node(self, flat, upper, args):
        if "DISTINCT GRAPH_ID" not in upper or "NODES" not in upper:
            return None
        node_id = args[0] if args else None
        return [(g,) for g in sorted(self.registry.node_graphs.get(node_id, []))]

    @staticmethod
    def _selected_columns(flat) -> List[str]:
        return [
            c.strip().split(".")[-1].strip('"')
            for c in flat.split("SELECT", 1)[1].split(" FROM ", 1)[0].split(",")
        ]

    def _quarantine_scan(self, flat, upper, args):
        """`SELECT <cols> FROM <quarantine> ORDER BY q_rowid`.

        The report names the rows it could not place, and one ordered read of the
        quarantine is where both the per-reason counts and the IDs come from. Deriving
        them from the plan instead would lose the rows an earlier killed pass wrote.
        """
        if not upper.startswith("SELECT") or "EMBEDDING_QUARANTINE" not in upper:
            return None
        if "ORDER BY Q_ROWID" not in upper or "MAX(" in upper:
            return None
        columns = self._selected_columns(flat)
        rows = sorted(
            self.registry.data.get("embedding_quarantine", []),
            key=lambda r: int(r.get("q_rowid") or 0),
        )
        return [tuple(r.get(c) for c in columns) for r in rows]

    def _registry_scan(self, flat, upper, args):
        """Every registry row, unfiltered — :class:`FakeRegistry` answers only a
        graph-filtered read, and the report has to name tables it was not told about."""
        if not upper.startswith("SELECT") or "EMBEDDING_REGISTRY" not in upper:
            return None
        if " WHERE " in upper or args or "COUNT(" in upper:
            return None
        columns = self._selected_columns(flat)
        rows = sorted(self.registry.rows, key=lambda r: str(r.get("table_name") or ""))
        return [tuple(r.get(c) for c in columns) for r in rows]

    def _keys_of(self, flat, upper, args):
        """`SELECT <key> FROM <table> WHERE <key> > ? ORDER BY <key>` — the drain's read."""
        if not upper.startswith("SELECT") or " ORDER BY " not in upper or "MAX(" in upper:
            return None
        if " FROM " not in flat or ">" not in flat:
            return None
        table = _table_after(flat, " FROM ")
        rows = self.registry.data.get(table)
        if rows is None:
            return None
        key = flat.split("SELECT", 1)[1].split(" FROM ", 1)[0].strip()
        after = args[0] if args else ""
        keys = sorted(str(r[key]) for r in rows if key in r)
        return [(k,) for k in keys if after is None or k > str(after or "")]

    def _max_of(self, flat, upper, args):
        if not upper.startswith("SELECT MAX("):
            return None
        column = flat.split("MAX(", 1)[1].split(")", 1)[0].strip()
        table = _table_after(flat, " FROM ")
        rows = self.registry.data.get(table)
        if rows is None:
            return [(None,)]
        if "source_table = ?" in flat and args:
            rows = [r for r in rows if r.get("source_table") == args[0]]
        values = [str(r[column]) for r in rows if r.get(column) is not None]
        return [(max(values),)] if values else [(None,)]

    def _structural_read(self, flat, upper, args):
        """The label/prop rows whose node does not sit in exactly one graph."""
        if not upper.startswith("SELECT") or "COUNT(DISTINCT" not in upper:
            return None
        table = _table_after(flat, " FROM ")
        rows = self.registry.data.get(table)
        if rows is None:
            return None
        columns = [
            c.strip().split(".")[-1].strip('"')
            for c in flat.split("SELECT", 1)[1].split(" FROM ", 1)[0].split(",")
        ]
        out = []
        for row in sorted(rows, key=lambda r: tuple(str(r.get(c)) for c in columns)):
            graphs = self.registry.node_graphs.get(row.get("s"), [])
            if len(set(graphs)) == 1:
                continue
            out.append(tuple(row.get(c) for c in columns))
        return out

    # ------------------------------------------------------------------- writing

    def _insert_select(self, flat, upper, args):
        """`INSERT INTO <target> (...) SELECT ... FROM <source> WHERE <key> = ?`."""
        if not upper.startswith("INSERT INTO") or " SELECT " not in upper:
            return None
        target = _table_after(flat, "INSERT INTO")
        source = _table_after(flat, " FROM ")
        rows = self.registry.data.get(source)
        if rows is None:
            return []
        key = "node_id" if "node_id" in self.registry.columns.get(source, ()) else "id"
        wanted = str(args[-1]) if args else None
        row = next((r for r in rows if str(r.get(key)) == wanted), None)
        if row is None:
            return []

        self.registry.note_move()
        if target == "embedding_quarantine":
            self.registry.data[target].append(
                {
                    "q_rowid": self.registry.next_q_rowid(),
                    "node_id": row.get(key),
                    "source_table": args[0],
                    "emb": row.get("emb"),
                    "dimension": args[1],
                    "dtype": args[2],
                    "metadata": row.get("metadata"),
                    "reason": args[3],
                }
            )
            return []

        graph_id = args[0] if args else ""
        self._refuse_duplicate(target, graph_id, row.get(key))
        self.registry.data.setdefault(target, []).append(
            {
                "graph_id": graph_id,
                "node_id": row.get(key),
                "emb": row.get("emb"),
                "metadata": row.get("metadata"),
            }
        )
        return []

    def _insert_values(self, flat, upper, args):
        if not upper.startswith("INSERT INTO") or " VALUES " not in upper:
            return None
        target = _table_after(flat, "INSERT INTO")
        if target not in self.registry.data or target == "embedding_registry":
            return None
        columns = [
            c.strip().strip('"')
            for c in flat.split("(", 1)[1].split(")", 1)[0].split(",")
        ]
        row = dict(zip(columns, args))
        self.registry.note_move()
        if target == "embedding_quarantine":
            row.setdefault("q_rowid", self.registry.next_q_rowid())
        self.registry.data[target].append(row)
        return []

    def _update_registry_fields(self, flat, upper, args):
        """A registry UPDATE other than the index-state one, applied to the row.

        :class:`FakeRegistry` models only ``SET index_state``, and T066's two rewrites
        are the thing under test: naming the graph a legacy table's rows were placed in,
        and carrying a recorded width forward to its column. Asserting those against
        statement text would pass for an UPDATE whose WHERE clause matched nothing.
        """
        if not upper.startswith("UPDATE") or "EMBEDDING_REGISTRY" not in upper:
            return None
        if "INDEX_STATE" in upper:
            return None
        assignments, _, condition = flat.split(" SET ", 1)[1].partition(" WHERE ")
        remaining = list(args)

        updates = {}
        for part in assignments.split(","):
            column, _, value = part.partition("=")
            if "?" not in value:  # CURRENT_TIMESTAMP and friends bind nothing
                continue
            updates[column.strip().strip('"')] = remaining.pop(0)

        tests = []
        for part in condition.split(" AND ") if condition else []:
            if "?" not in part:
                continue
            column, operator = (part.split("<>", 1)[0], "<>") if "<>" in part else (
                part.split("=", 1)[0],
                "=",
            )
            tests.append((column.strip().strip('"'), operator, remaining.pop(0)))

        for row in self.registry.rows:
            if all(
                (str(row.get(c) or "") == str(v or ""))
                if op == "="
                else (str(row.get(c) or "") != str(v or ""))
                for c, op, v in tests
            ):
                row.update(updates)
        return []

    def _delete(self, flat, upper, args):
        if not upper.startswith("DELETE FROM"):
            return None
        table = _table_after(flat, "DELETE FROM")
        rows = self.registry.data.get(table)
        if rows is None:
            return None
        columns = [
            part.split("=")[0].strip().strip('"')
            for part in flat.split(" WHERE ", 1)[1].split(" AND ")
        ] if " WHERE " in flat else []
        keep = []
        for row in rows:
            if all(
                str(row.get(col)) == str(val) for col, val in zip(columns, args)
            ) and columns:
                self.registry.note_move()
                continue
            keep.append(row)
        self.registry.data[table] = keep
        return []

    def _ddl(self, flat, upper, args):
        if upper.startswith("CREATE TABLE"):
            table = _table_after(flat, "CREATE TABLE")
            self.registry.ddl.append(flat)
            if table in self.registry.columns:
                raise RuntimeError(
                    "[SQLCODE: <-201>:<Table or view name not unique>] "
                    f"table {table} already exists"
                )
            self.registry.columns[table] = ROUTED_COLUMNS
            self.registry.data.setdefault(table, [])
            self.registry.tables.append(table)
            self.registry.created_tables.append(table)
            return []
        if upper.startswith("DROP TABLE"):
            table = _table_after(flat, "DROP TABLE")
            self.registry.ddl.append(flat)
            self.registry.dropped.append(table)
            self.registry.columns.pop(table, None)
            self.registry.data.pop(table, None)
            return []
        if upper.startswith("ALTER TABLE"):
            self.registry.ddl.append(flat)
            if " RENAME " in upper:
                old = _table_after(flat, "ALTER TABLE")
                target = flat.split(" RENAME ", 1)[1].strip().split()[0]
                if "." in target:
                    # IRIS takes the new name unqualified — the table cannot change
                    # schema — and refuses a qualified one at Prepare. Accepting it
                    # here would hide a rename that never happens on a server: the
                    # migrator's fallback tolerates the syntax error and copies every
                    # vector a second time instead.
                    raise RuntimeError(
                        "[SQLCODE: <-1>:<Invalid SQL statement>] Unqualified table "
                        f"name expected, qualified table name found ^{flat}"
                    )
                new = target
                self.registry.columns[new] = self.registry.columns.pop(old, ROUTED_COLUMNS)
                self.registry.data[new] = self.registry.data.pop(old, [])
                self.registry.renamed.append((old, new))
            return []
        if upper.startswith("CREATE INDEX"):
            return None  # FakeRegistry models the ANN index, including its refusal
        return None

    def _refuse_duplicate(self, table: str, graph_id, node_id) -> None:
        for row in self.registry.data.get(table, []):
            if row.get("graph_id", "") == (graph_id or "") and row.get("node_id") == node_id:
                raise RuntimeError(
                    "[SQLCODE: <-119>:<Unique constraint violation>] "
                    f"uq_{table} already holds ({graph_id}, {node_id})"
                )


def migration_conn(registry: MigrationRegistry):
    return FakeConnection(registry)


def legacy_install(
    *,
    node_graphs: Dict[str, List[str]],
    vectors: Optional[List[str]] = None,
    table: str = "kg_NodeEmbeddings",
    dimension: int = 4,
    registry_rows: Optional[List[dict]] = None,
    **kwargs,
) -> MigrationRegistry:
    """A 3.2.0 install: one unscoped embedding table, one adopted registry row.

    ``vectors`` names the node IDs that have a vector; every one gets a distinguishable
    placeholder value so a misrouted row is visible as the wrong vector rather than as
    the right count.
    """
    ids = list(vectors if vectors is not None else node_graphs)
    rows = [{"id": n, "emb": f"<{n}>", "metadata": None} for n in ids]
    default_registry = [
        {
            "table_name": table,
            "graph_id": "",
            "mechanism": None,
            "model_key": None,
            "declared_config": None,
            "dimension": dimension,
            "dtype": "DOUBLE",
            "set_by": "adopted",
        }
    ]
    return MigrationRegistry(
        source_rows={table: rows},
        node_graphs=node_graphs,
        rows=registry_rows if registry_rows is not None else default_registry,
        dimension_for={table: dimension},
        **kwargs,
    )
