"""A registry that behaves like IRIS enough to test routing against (spec 227).

Not a test file — the shared fake for `test_227_resolve_route.py`,
`test_227_identity_per_route.py` and `test_227_route_concurrency.py`. It lives
next to them rather than in `conftest.py` because it is a fake, not a fixture:
three files construct it differently and one of them drives it from two threads.

What it models, and why each part matters to a route:

- **The registry primary key** `(table_name, graph_id)`. A second INSERT for the
  same pair raises, which is exactly how two processes creating one route
  arbitrate (FR-014). Nothing in Python decides the winner.
- **`CREATE TABLE` on an existing table raises** with IRIS's wording, so the
  create path has to tolerate it rather than treat it as failure.
- **`CREATE INDEX ... AS HNSW` can be refused.** Set `index_error` and the fake
  raises the verbatim text a NoPWS/community build reports, so the route still
  has to end up usable (FR-019).

Everything else answers empty. A route resolve that depends on some other read
succeeding would be depending on something this fake is not claiming to model.
"""

from typing import Any, Dict, List, Optional, Tuple

#: Column order of the route lookup. The fake serves this list and nothing else,
#: so a change to the SELECT in `resolve_route` fails loudly here instead of
#: silently reading a dimension out of an index_state.
ROUTE_COLUMNS = ("table_name", "dimension", "dtype", "index_state", "index_error")

#: Column order of spec 226's identity read (`get_embedding_identity`).
IDENTITY_COLUMNS = ("mechanism", "model_key", "declared_config", "dimension", "dtype")


class FakeRegistry:
    """The rows, the tables, and a log of every statement issued."""

    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        tables: Optional[List[str]] = None,
        index_error: Optional[str] = None,
        dimension_for: Optional[Dict[str, int]] = None,
        nodes: Optional[List[Tuple[str, str]]] = None,
        counts: Optional[Dict[str, Optional[int]]] = None,
    ):
        self.rows: List[Dict[str, Any]] = [dict(r) for r in (rows or [])]
        self.tables: List[str] = list(tables or [])
        self.index_error = index_error
        #: Declared `emb` width per table, keyed case-insensitively on the bare name.
        #: Consulted when something asks the data dictionary how wide a column is; a
        #: table not listed has no declared width, which is the `SQLCODE -260` shape.
        self.dimension_for = {
            k.lower(): v for k, v in dict(dimension_for or {}).items()
        }
        #: `(graph_id, node_id)` pairs that exist in `nodes`. ``None`` means every node
        #: exists — the default, so a routing test does not have to build a graph first.
        self.nodes = None if nodes is None else {(g or "", n) for g, n in nodes}
        #: `COUNT(*)` per table, keyed case-insensitively on the bare name. A table
        #: mapped to ``None`` does not exist and its count raises, which is how a
        #: registry row that outlived its table is modelled. A table not mentioned at
        #: all keeps the default answer of 1 — the "does this exist" probe.
        self.counts = {k.lower(): v for k, v in dict(counts or {}).items()}
        self.statements: List[Tuple[str, List[Any]]] = []
        self.created_tables: List[str] = []
        self.created_indexes: List[str] = []
        self.commits = 0

    # ---------------------------------------------------------------- queries

    def statements_matching(self, needle: str) -> List[Tuple[str, List[Any]]]:
        return [(sql, params) for sql, params in self.statements if needle in sql]

    def row_for(self, table_name: str, graph_id: str) -> Optional[Dict[str, Any]]:
        for row in self.rows:
            if row.get("table_name") == table_name and row.get("graph_id", "") == graph_id:
                return row
        return None

    def cursor(self):
        return FakeCursor(self)


class FakeCursor:
    """One cursor over a :class:`FakeRegistry`. Reusable, like the IRIS driver's."""

    def __init__(self, registry: FakeRegistry):
        self.registry = registry
        self._rows: List[Tuple[Any, ...]] = []
        self.description: List[Any] = []
        self.closed = False

    # ------------------------------------------------------------------ DB-API

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        args = list(params or [])
        self.registry.statements.append((flat, args))
        self._rows = []

        if flat.upper().startswith("SELECT"):
            self._rows = self._select(flat, args)
        elif "CREATE TABLE" in flat:
            self._create_table(flat)
        elif "CREATE INDEX" in flat:
            self._create_index(flat)
        elif "INSERT INTO" in flat and "embedding_registry" in flat:
            self._insert_registry(flat, args)
        elif "UPDATE" in flat and "embedding_registry" in flat:
            self._update_registry(flat, args)
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, size: int = 1):
        batch, self._rows = self._rows[:size], self._rows[size:]
        return batch

    def close(self):
        self.closed = True

    # ----------------------------------------------------------------- reading

    def _select(self, flat: str, args: List[Any]) -> List[Tuple[Any, ...]]:
        if "INFORMATION_SCHEMA.COLUMNS" in flat:
            if "'emb'" in flat:
                # A width question, answered from `dimension_for` and not invented: a
                # fake that reported a width for every table would let adoption record
                # one for a column that declares none.
                table = str(args[1] if len(args) > 1 else "").split(".")[-1].lower()
                width = self.registry.dimension_for.get(table)
                return [(f"VECTOR(DOUBLE,{width})",)] if width else []
            # Every table this fake knows about carries graph_id: a 227 schema.
            return [(1,)]

        if "embedding_registry" not in flat:
            if "COUNT(*)" in flat:
                return [(self._count(flat, args),)]
            if self.registry.nodes is not None and _selects_node_list(flat):
                # A node list, answered from `nodes` and filtered by the statement's
                # own graph literal. A fake that returned every node regardless would
                # make an unscoped node selection look correct — the exact defect the
                # 227 tests exist to catch.
                return [
                    (node_id,)
                    for graph_id, node_id in sorted(self.registry.nodes)
                    if _graph_matches(flat, graph_id)
                ]
            return []

        if flat.startswith("SELECT DISTINCT table_name"):
            return self._route_listing()
        if flat.startswith("SELECT table_name"):
            return self._route_rows(flat, args)
        if flat.startswith("SELECT mechanism"):
            return self._identity_rows(args)
        if "COUNT(*)" in flat:
            return [(len(self.registry.rows),)]
        return []

    def _count(self, flat: str, args: List[Any]) -> int:
        """`COUNT(*)` on a table other than the registry.

        Three questions share this shape. "How many rows are in this table?" — answered
        from `counts` when the test said, because a row count summed over several tables
        is only a test if the tables answer differently. "Does this table exist?" —
        answered yes otherwise, since a fake that denied it would make every path under
        test take an absent-table branch. "Does this node exist in this graph?" —
        answered from `nodes` when the test bothered to say, and yes otherwise.
        """
        if self.registry.counts and not args:
            table = _counted_table(flat)
            if table in self.registry.counts:
                count = self.registry.counts[table]
                if count is None:
                    raise RuntimeError(
                        f"[SQLCODE: <-30>:<Table or view not found>] {table}"
                    )
                return count
        if self.registry.nodes is not None and "nodes" in flat and len(args) > 1:
            node_id, graph_id = args[0], args[1]
            return 1 if (graph_id or "", node_id) in self.registry.nodes else 0
        return 1

    def _route_listing(self) -> List[Tuple[Any, ...]]:
        """`(table_name, kind)` for every registry row, as the row count reads it.

        A row with no `kind` value answers `'node'`: that is what the column's DEFAULT
        gives a row written before 4.0.0, and this fake has to say the same or the
        count would be tested against a registry no install has.
        """
        return [
            (row.get("table_name"), row.get("kind") or "node")
            for row in self.registry.rows
        ]

    def _route_rows(self, flat: str, args: List[Any]) -> List[Tuple[Any, ...]]:
        graph_id = args[0] if args else ""
        wants_null_model = "model_key IS NULL" in flat
        # Spec 230: the route query binds the kind between the graph and the model, so
        # the positions of the remaining parameters depend on whether it is there. Read
        # from the statement rather than guessed — a fake that assumed the old order
        # would hand the node route's row to an edge lookup, which is the whole defect.
        scoped_by_kind = "COALESCE(kind, 'node')" in flat
        kind = args[1] if (scoped_by_kind and len(args) > 1) else None
        rest = args[2:] if scoped_by_kind else args[1:]
        model_key = None if wants_null_model else (rest[0] if rest else None)
        out = []
        for row in self.registry.rows:
            if (row.get("graph_id") or "") != (graph_id or ""):
                continue
            # A row with no `kind` is a node route: that is what every row written
            # before 4.0.0 is, and what the column's DEFAULT says.
            if kind is not None and (row.get("kind") or "node") != kind:
                continue
            if wants_null_model:
                if row.get("model_key") is not None:
                    continue
            elif row.get("model_key") != model_key:
                continue
            out.append(tuple(row.get(col) for col in ROUTE_COLUMNS))
        return out

    def _identity_rows(self, args: List[Any]) -> List[Tuple[Any, ...]]:
        table_name = args[0] if args else None
        graph_id = args[1] if len(args) > 1 else ""
        row = self.registry.row_for(table_name, graph_id or "")
        if row is None:
            return []
        return [tuple(row.get(col) for col in IDENTITY_COLUMNS)]

    # ----------------------------------------------------------------- writing

    def _create_table(self, flat: str) -> None:
        name = _table_after(flat, "CREATE TABLE")
        if name in self.registry.tables:
            raise RuntimeError(
                f"[SQLCODE: <-201>:<Table or view name not unique>] table {name} already exists"
            )
        self.registry.tables.append(name)
        self.registry.created_tables.append(name)

    def _create_index(self, flat: str) -> None:
        if self.registry.index_error:
            raise RuntimeError(self.registry.index_error)
        self.registry.created_indexes.append(flat)

    def _insert_registry(self, flat: str, args: List[Any]) -> None:
        columns = _insert_columns(flat)
        row = dict(zip(columns, args))
        row.setdefault("graph_id", "")
        existing = self.registry.row_for(row.get("table_name"), row.get("graph_id") or "")
        if existing is not None:
            raise RuntimeError(
                "[SQLCODE: <-119>:<Unique constraint violation>] pk_embedding_registry"
            )
        self.registry.rows.append(row)

    def _update_registry(self, flat: str, args: List[Any]) -> None:
        # Only the route-state update is modelled: SET index_state = ?, index_error = ?
        # WHERE table_name = ? AND graph_id = ?
        if "index_state" not in flat:
            return
        if len(args) < 4:
            return
        state, error, table_name, graph_id = args[0], args[1], args[-2], args[-1]
        row = self.registry.row_for(table_name, graph_id or "")
        if row is not None:
            row["index_state"] = state
            row["index_error"] = error


class UnreadableRegistry(FakeRegistry):
    """A 3.2.0-or-earlier install: every statement naming the registry fails.

    Distinct from an empty registry, and the distinction is the point: no row means
    "this pair has no route yet", while an unreadable registry means "this database
    does not do routing", and the second must keep writing and reading where 3.2.0
    did rather than concluding that nothing is routed.
    """

    def cursor(self):
        return UnreadableRegistryCursor(self)


class UnreadableRegistryCursor(FakeCursor):
    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        if "embedding_registry" in flat:
            self.registry.statements.append((flat, list(params or [])))
            self._rows = []
            raise RuntimeError(
                "[SQLCODE: <-30>:<Table or view not found>] Graph_KG.embedding_registry"
            )
        return super().execute(sql, params)


class GarbledRegistry(FakeRegistry):
    """A registry whose answer to the route query is not a route row.

    Two ways to get here, one of them in the field: a 3.2.0 registry table that
    predates `index_state` answers a five-column SELECT with an error, but a
    connection pooling or proxy layer that re-serves a previous result set answers
    it with whatever shape that result had. Either way the answer is not evidence
    about routes, and the only safe reading of it is that this database does not
    route — not that it routes to whatever happened to be in column one.
    """

    def cursor(self):
        return GarbledRegistryCursor(self)


class GarbledRegistryCursor(FakeCursor):
    def _route_rows(self, flat: str, args: List[Any]) -> List[Tuple[Any, ...]]:
        # Two columns where the query asked for five.
        return [("kg_emb_deadbeefdeadbeef", 4)]


class FakeConnection:
    """`conn` for an engine: one registry, cursors on demand, counted commits."""

    def __init__(self, registry: FakeRegistry):
        self.registry = registry

    def cursor(self):
        return self.registry.cursor()

    def commit(self):
        self.registry.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _selects_node_list(flat: str) -> bool:
    """A `SELECT node_id FROM <schema>.nodes ...` and nothing else.

    `kg_NodeEmbeddings` also projects `node_id`, and answering that read from the node
    list would make every node look already-embedded.
    """
    upper = flat.upper()
    return (
        upper.startswith("SELECT NODE_ID")
        and ".NODES" in upper
        and "EMBEDDING" not in upper
    )


def _graph_matches(flat: str, graph_id: str) -> bool:
    """Whether a statement's inlined graph literal selects `graph_id`.

    `build_node_where` interpolates the graph rather than binding it (it returns a
    WHERE body, not a statement), so the graph is in the SQL text and this fake has
    to read it from there. A statement naming no graph matches every graph — which
    is what makes an unscoped selection visible to a test.
    """
    import re as _re

    literals = _re.findall(r"COALESCE\(graph_id, ''\)\s*=\s*COALESCE\('([^']*)'", flat)
    if not literals:
        return "graph_id" not in flat
    return (graph_id or "") in literals


def _counted_table(flat: str) -> str:
    """The bare, lowercased table a `SELECT COUNT(*) FROM <table>` names."""
    rest = flat.split(" FROM ", 1)[1].strip() if " FROM " in flat else ""
    name = rest.split()[0] if rest else ""
    return name.split(".")[-1].lower()


def _table_after(flat: str, keyword: str) -> str:
    """The bare table name a DDL statement names.

    The schema prefix is dropped because IRIS resolves `Graph_KG.t` and `t` to one
    object — a fake that kept the prefix would let a second `CREATE TABLE` of the
    same table succeed, which is the one thing the create path has to survive.
    """
    rest = flat.split(keyword, 1)[1].strip()
    name = rest.split("(", 1)[0].strip()
    name = name.split()[0] if name else ""
    return name.split(".")[-1]


def _insert_columns(flat: str) -> List[str]:
    inner = flat.split("(", 1)[1].split(")", 1)[0]
    return [c.strip().strip('"') for c in inner.split(",")]


def teach_registry_route(
    cursor,
    graph_id: str,
    *,
    model_key: Optional[str] = None,
    table_name: Optional[str] = None,
    dimension: int = 4,
    dtype: str = "DOUBLE",
) -> Tuple[Any, ...]:
    """Give a `MagicMock` cursor one route, without disturbing its other answers.

    A `MagicMock` answers every `SELECT` identically, so an engine asking "what is
    this pair's route" is handed the test's KNN rows and the KNN is handed the route
    row. This keeps them apart by looking at the statement: the route query gets a
    route row, everything else falls through to whatever the test configured
    (`mock.DEFAULT` is how a `side_effect` says "use `return_value`").

    Needed by tests written before routing existed, which assert the *shape* of the
    statement a graph-scoped read or write generates. Their pair has to be routed
    for there to be a statement at all — an unrouted pair reads nothing (FR-013).
    """
    from unittest.mock import DEFAULT

    from iris_vector_graph.routing import route_table_name

    row = (
        table_name or route_table_name(graph_id, model_key),
        dimension,
        dtype,
        "present",
        None,
    )
    asked_the_registry = {"yes": False}
    inner = cursor.execute.side_effect

    def execute(sql, params=None, *args, **kwargs):
        asked_the_registry["yes"] = "embedding_registry" in " ".join(str(sql).split())
        if inner is not None:
            return inner(sql, params, *args, **kwargs)
        return DEFAULT

    def fetchall(*args, **kwargs):
        return [row] if asked_the_registry["yes"] else DEFAULT

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = fetchall
    return row


def answer_by_statement(cursor, answers: Dict[str, List[Any]], default: Any = ()) -> None:
    """Make a `MagicMock` cursor answer `fetchall` from what the statement asked for.

    A test written before routing existed set `fetchall.side_effect` to a fixed list of
    result sets, in the order the old code read them. Routed code reads the registry as
    well, and more than once, so a fixed list hands the route lookup the test's data and
    then runs out — `StopIteration` out of the mock rather than a failed assertion.

    `answers` maps a substring of the statement to the rows that statement answers with;
    the first match in iteration order wins, and an unmatched statement gets `default`.
    Any `execute` side effect the test already installed still runs.
    """
    state = {"sql": ""}
    inner = cursor.execute.side_effect

    def execute(sql, params=None, *args, **kwargs):
        state["sql"] = " ".join(str(sql).split())
        if inner is not None:
            return inner(sql, params, *args, **kwargs)
        return None

    def fetchall(*args, **kwargs):
        for needle, rows in answers.items():
            if needle in state["sql"]:
                return list(rows)
        return list(default)

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = fetchall


def engine_with(registry: FakeRegistry, **kwargs):
    """An `IRISGraphEngine` over `registry`, with construction noise discarded."""
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(FakeConnection(registry), **kwargs)
    registry.statements.clear()
    return engine
