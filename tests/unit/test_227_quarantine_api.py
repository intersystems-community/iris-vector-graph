"""Spec 227 — the quarantine is readable and drainable one row at a time (FR-040).

A row reaches `Graph_KG.embedding_quarantine` when the migration could not say which
graph it belongs to. Everything about the API over it follows from what that means:

* **Nothing searches it.** A quarantined vector has no graph, so it cannot be scoped,
  and a KNN that reached it would return a neighbour from an unknown space — the leak
  spec 227 exists to close, re-opened at the bottom of the table.
* **Placement is one row, named explicitly, or it is a guess.** There is no
  `drain_quarantine` and no `--force`: the operator who knows where a row belongs
  knows it one row at a time, and a bulk placement is the silent default-graph
  assignment FR-028 forbids, spelled differently.
* **A refusal leaves the row where it is.** The quarantine is the only record that the
  vector exists at all. A refused placement that had already deleted it would destroy
  the thing the refusal was protecting.

`place_quarantined` therefore checks three things the route can be wrong about — that
the route exists, that its declared width and dtype match the row's, and that the node
exists *in that graph* — and moves the vector with an `INSERT ... SELECT` so the
floats never round-trip through Python.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from tests.unit.route_fakes_227 import FakeCursor, FakeRegistry, _table_after, engine_with

GRAPH = "graph-a"
OTHER = "graph-b"
MODEL = "model-a"
NODE = "n:1"
TABLE = "kg_emb_00000000000000aa"

#: Every column `list_quarantine` reports. The fake refuses a SELECT that names a
#: column the table does not have, so a drifting select list fails here rather than
#: reporting a reason out of a timestamp.
QUARANTINE_COLUMNS = (
    "q_rowid",
    "node_id",
    "source_table",
    "dimension",
    "dtype",
    "metadata",
    "reason",
    "quarantined_at",
)


def quarantine_row(**overrides) -> Dict[str, Any]:
    row = {
        "q_rowid": 1,
        "node_id": NODE,
        "source_table": "kg_NodeEmbeddings",
        "dimension": 4,
        "dtype": "DOUBLE",
        "metadata": None,
        "reason": "ambiguous_graph",
        "quarantined_at": "2026-09-20 03:00:00",
    }
    row.update(overrides)
    return row


def route_row(**overrides) -> Dict[str, Any]:
    row = {
        "table_name": TABLE,
        "graph_id": GRAPH,
        "model_key": MODEL,
        "mechanism": "declared",
        "declared_config": MODEL,
        "dimension": 4,
        "dtype": "DOUBLE",
        "index_state": "present",
        "index_error": None,
    }
    row.update(overrides)
    return row


class QuarantineRegistry(FakeRegistry):
    """A :class:`FakeRegistry` that also holds a quarantine table.

    Models the three things placement depends on: the rows, the uniqueness the target
    route enforces on `(graph_id, node_id)`, and — when `readable=False` — a database
    with no quarantine table at all, which is every 3.2.0 install.
    """

    def __init__(self, *args, quarantine=None, readable: bool = True,
                 insert_fails: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.quarantine: List[Dict[str, Any]] = [dict(r) for r in (quarantine or [])]
        self.quarantine_readable = readable
        self.insert_fails = insert_fails
        #: `(table, graph_id, node_id)` for every row placed into a route.
        self.placed: List[tuple] = []
        self.deleted: List[Any] = []

    def cursor(self):
        return QuarantineCursor(self)

    def row_with(self, q_rowid) -> Optional[Dict[str, Any]]:
        for row in self.quarantine:
            if str(row["q_rowid"]) == str(q_rowid):
                return row
        return None


class QuarantineCursor(FakeCursor):
    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        args = list(params or [])
        if "embedding_quarantine" not in flat:
            return super().execute(sql, params)

        self.registry.statements.append((flat, args))
        self._rows = []
        if not self.registry.quarantine_readable:
            raise RuntimeError(
                "[SQLCODE: <-30>:<Table or view not found>] Graph_KG.embedding_quarantine"
            )
        if flat.upper().startswith("SELECT"):
            self._rows = self._quarantine_rows(flat, args)
        elif flat.upper().startswith("INSERT"):
            self._place(flat, args)
        elif flat.upper().startswith("DELETE"):
            self._delete(flat, args)
        return self

    # ------------------------------------------------------------------ reading

    def _quarantine_rows(self, flat: str, args: List[Any]):
        columns = [
            c.strip()
            for c in flat.split("SELECT", 1)[1].split(" FROM ", 1)[0].split(",")
        ]
        rows = sorted(self.registry.quarantine, key=lambda r: int(r["q_rowid"]))
        if "q_rowid = ?" in flat:
            rows = [r for r in rows if str(r["q_rowid"]) == str(args[0])]
        elif "reason = ?" in flat:
            rows = [r for r in rows if r.get("reason") == (args[0] if args else None)]
        out = []
        for row in rows:
            values = []
            for column in columns:
                if column not in row:
                    raise RuntimeError(
                        f"[SQLCODE: <-29>:<Field not found>] {column} in embedding_quarantine"
                    )
                values.append(row[column])
            out.append(tuple(values))
        return out

    # ------------------------------------------------------------------ writing

    def _place(self, flat: str, args: List[Any]) -> None:
        if self.registry.insert_fails:
            raise RuntimeError(self.registry.insert_fails)
        table = _table_after(flat, "INSERT INTO")
        q_rowid = args[-1] if args else None
        row = self.registry.row_with(q_rowid)
        if row is None:
            return
        graph_id = args[0] if args else ""
        key = (table, graph_id or "", row["node_id"])
        if key in self.registry.placed:
            raise RuntimeError(
                "[SQLCODE: <-119>:<Unique constraint violation>] "
                f"uq_{table} already holds ({graph_id}, {row['node_id']})"
            )
        self.registry.placed.append(key)

    def _delete(self, flat: str, args: List[Any]) -> None:
        q_rowid = args[0] if args else None
        row = self.registry.row_with(q_rowid)
        if row is not None:
            self.registry.quarantine.remove(row)
            self.registry.deleted.append(q_rowid)


def engine_for(**kwargs):
    registry = QuarantineRegistry(**kwargs)
    return engine_with(registry), registry


def routed_engine(**kwargs):
    """An engine whose `(GRAPH, MODEL)` pair is routed and whose node exists."""
    kwargs.setdefault("rows", [route_row()])
    kwargs.setdefault("quarantine", [quarantine_row()])
    kwargs.setdefault("nodes", [(GRAPH, NODE)])
    return engine_for(**kwargs)


# --- list_quarantine ----------------------------------------------------------------


def test_every_row_is_reported_with_its_reason_and_source():
    engine, _registry = engine_for(
        quarantine=[
            quarantine_row(q_rowid=2, node_id="n:2", reason="no_node"),
            quarantine_row(q_rowid=1, metadata='{"k": 1}'),
        ]
    )
    rows = engine.list_quarantine()

    assert [r.q_rowid for r in rows] == [1, 2], "rows must come back in q_rowid order"
    first, second = rows
    assert first.node_id == NODE
    assert first.source_table == "kg_NodeEmbeddings"
    assert first.dimension == 4
    assert first.dtype == "DOUBLE"
    assert first.metadata == '{"k": 1}'
    assert first.reason == "ambiguous_graph"
    assert first.quarantined_at == "2026-09-20 03:00:00"
    assert second.reason == "no_node", "the reason is what says how to place the row"


def test_the_read_asks_for_every_column_it_reports():
    """A select list that drifts from the dataclass reports a value from the wrong column."""
    engine, registry = engine_for(quarantine=[quarantine_row()])
    engine.list_quarantine()

    reads = registry.statements_matching("embedding_quarantine")
    assert reads, "list_quarantine issued no statement"
    sql = reads[0][0]
    for column in QUARANTINE_COLUMNS:
        assert re.search(rf"\b{column}\b", sql), f"{column} is reported but never selected"


def test_a_reason_filter_is_bound_not_interpolated():
    engine, registry = engine_for(
        quarantine=[
            quarantine_row(q_rowid=1, reason="ambiguous_graph"),
            quarantine_row(q_rowid=2, node_id="n:2", reason="no_node"),
        ]
    )
    rows = engine.list_quarantine(reason="no_node")

    assert [r.q_rowid for r in rows] == [2]
    sql, params = registry.statements_matching("embedding_quarantine")[0]
    assert "no_node" not in sql, "the reason reached the statement text instead of a parameter"
    assert "no_node" in params


def test_an_unknown_reason_is_refused_rather_than_answered_empty():
    """A typo'd filter reads as "nothing is quarantined", which is the dangerous answer."""
    engine, _registry = engine_for(quarantine=[quarantine_row()])
    with pytest.raises(ValueError) as err:
        engine.list_quarantine(reason="ambigous_graph")
    assert "ambigous_graph" in str(err.value)


def test_a_database_with_no_quarantine_table_reports_nothing():
    """3.2.0. A status command must not raise on it."""
    engine, _registry = engine_for(readable=False)
    assert engine.list_quarantine() == []


def test_a_reported_row_is_frozen():
    engine, _registry = engine_for(quarantine=[quarantine_row()])
    row = engine.list_quarantine()[0]
    with pytest.raises(Exception):
        row.reason = "no_node"


# --- place_quarantined: the happy path ---------------------------------------------


def test_placing_a_row_moves_it_into_the_route_and_removes_it():
    engine, registry = routed_engine()
    assert engine.place_quarantined(1, graph=GRAPH, model_key=MODEL) is True

    assert registry.placed == [(TABLE, GRAPH, NODE)]
    assert registry.deleted == [1]
    assert registry.row_with(1) is None, "the row was placed and still sits in quarantine"
    assert registry.commits >= 1, "the move was never committed"


def test_the_insert_precedes_the_delete():
    """Order is the only thing standing between a refused write and a lost vector."""
    engine, registry = routed_engine()
    engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)

    kinds = [
        sql.split()[0].upper()
        for sql, _ in registry.statements_matching("embedding_quarantine")
        if sql.split()[0].upper() in ("INSERT", "DELETE")
    ]
    assert kinds == ["INSERT", "DELETE"], f"statements ran in the order {kinds}"


def test_the_vector_never_round_trips_through_python():
    """`INSERT ... SELECT`: IRIS copies the vector, so nothing reshapes or reformats it."""
    engine, registry = routed_engine()
    engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)

    inserts = [
        sql for sql, _ in registry.statements_matching("embedding_quarantine")
        if sql.upper().startswith("INSERT")
    ]
    assert len(inserts) == 1
    assert "SELECT" in inserts[0].upper(), "the placement read the vector out and wrote it back"
    assert "TO_VECTOR" not in inserts[0].upper(), (
        "a re-serialized vector is a reformatted vector (ADR-0005)"
    )
    reads = [
        sql for sql, _ in registry.statements_matching("embedding_quarantine")
        if sql.upper().startswith("SELECT")
    ]
    assert all(not re.search(r"\bemb\b", sql) for sql in reads), (
        "the vector was selected into Python: " + "; ".join(reads)
    )


def test_the_placement_writes_the_graph_the_caller_named():
    engine, registry = routed_engine()
    engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)

    sql, params = [
        (sql, params)
        for sql, params in registry.statements_matching("embedding_quarantine")
        if sql.upper().startswith("INSERT")
    ][0]
    assert GRAPH in params, "the target graph is not bound; the row would carry no graph"
    assert GRAPH not in sql


# --- place_quarantined: the refusals ----------------------------------------------


def _wrote_nothing(registry):
    writes = [
        sql
        for sql, _ in registry.statements
        if sql.upper().startswith(("INSERT", "DELETE", "UPDATE", "CREATE"))
    ]
    assert writes == [], f"a refused placement wrote: {writes}"
    assert registry.deleted == []


def test_an_unrouted_pair_is_refused():
    engine, registry = routed_engine(rows=[])
    with pytest.raises(ValueError) as err:
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    assert GRAPH in str(err.value) and MODEL in str(err.value)
    assert registry.row_with(1) is not None
    _wrote_nothing(registry)


def test_no_route_is_created_on_the_operators_behalf():
    """Creating one would declare a width from a quarantined row nobody vouched for."""
    engine, registry = routed_engine(rows=[])
    with pytest.raises(ValueError):
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    assert registry.created_tables == []


def test_a_width_the_route_does_not_declare_is_refused():
    engine, registry = routed_engine(
        rows=[route_row(dimension=768)], quarantine=[quarantine_row(dimension=384)]
    )
    with pytest.raises(EmbeddingIdentityConflict) as err:
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    assert "384" in str(err.value) and "768" in str(err.value)
    assert registry.row_with(1) is not None
    _wrote_nothing(registry)


def test_a_route_with_no_declared_width_is_refused():
    """Unknown is not agreement. The INSERT would be measured against a width nobody read."""
    engine, registry = routed_engine(rows=[route_row(dimension=None)])
    with pytest.raises(EmbeddingIdentityConflict):
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    _wrote_nothing(registry)


def test_a_dtype_the_route_does_not_declare_is_refused():
    engine, registry = routed_engine(
        rows=[route_row(dtype="DOUBLE")], quarantine=[quarantine_row(dtype="FLOAT")]
    )
    with pytest.raises(EmbeddingIdentityConflict) as err:
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    assert "FLOAT" in str(err.value).upper()
    _wrote_nothing(registry)


def test_a_dtype_that_differs_only_in_case_is_accepted():
    engine, registry = routed_engine(quarantine=[quarantine_row(dtype="double")])
    assert engine.place_quarantined(1, graph=GRAPH, model_key=MODEL) is True
    assert registry.placed == [(TABLE, GRAPH, NODE)]


def test_a_node_that_is_not_in_that_graph_is_refused():
    """The FK would refuse it anyway; refusing here says which of the two is wrong."""
    engine, registry = routed_engine(nodes=[(OTHER, NODE)])
    with pytest.raises(ValueError) as err:
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    assert NODE in str(err.value) and GRAPH in str(err.value)
    assert registry.row_with(1) is not None
    _wrote_nothing(registry)


def test_a_missing_q_rowid_is_refused():
    engine, registry = routed_engine()
    with pytest.raises(ValueError) as err:
        engine.place_quarantined(99, graph=GRAPH, model_key=MODEL)
    assert "99" in str(err.value)
    _wrote_nothing(registry)


def test_a_refused_insert_leaves_the_row_in_quarantine():
    """The route already holds a vector for that node, or the FK refused. Either way
    the quarantined row is still the only copy, so the DELETE must not follow."""
    engine, registry = routed_engine(
        insert_fails="[SQLCODE: <-119>:<Unique constraint violation>] uq_kg_emb"
    )
    with pytest.raises(Exception):
        engine.place_quarantined(1, graph=GRAPH, model_key=MODEL)
    assert registry.row_with(1) is not None
    assert registry.deleted == []


# --- FR-040: nothing drains it, nothing searches it ------------------------------


def test_there_is_no_bulk_drain_and_no_force():
    import inspect

    from iris_vector_graph.engine import IRISGraphEngine

    assert not hasattr(IRISGraphEngine, "drain_quarantine")
    params = inspect.signature(IRISGraphEngine.place_quarantined).parameters
    assert set(params) == {"self", "q_rowid", "graph", "model_key"}, (
        f"place_quarantined grew a parameter: {sorted(params)}"
    )


def test_only_the_schema_mixin_and_the_migration_name_the_quarantine_table():
    """No read path, no search path, no procedure body (FR-040)."""
    package = Path(__file__).resolve().parents[2] / "iris_vector_graph"
    allowed = {
        Path("_engine/schema.py"),
        Path("security.py"),  # the table allowlist, which names every table
        Path("migrations/__init__.py"),
        Path("migrations/graph_scoped_embeddings.py"),
    }
    offenders = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if "embedding_quarantine" in path.read_text(encoding="utf-8")
        and path.relative_to(package) not in allowed
    )
    assert offenders == [], f"these modules reach into the quarantine: {offenders}"
