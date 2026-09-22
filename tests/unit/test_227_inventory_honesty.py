"""Spec 227 T053 — the inventory reports the indexes IRIS has (FR-020, SC-009).

Spec 226 removed a report that invented an HNSW row from a table's *row count*: an
operator read `ONLINE` and concluded their searches were using an ANN index when
every one of them was a full scan. Routing turns one table into N, which is N
chances for that answer to come back by omission.

So the rule here is narrow and mechanical. An inventory row may say
`index_state == "present"` only when `%Dictionary.CompiledIndex` holds an index on
that route's table whose `TypeClass` is `%SQL.Index.HNSW`. Not when the registry row
says so — that is a record of what a `CREATE INDEX` *replied* to, once, possibly
against a table that has since been rebuilt. Not when the table has rows. Not when
the table merely has some index: `Type` reads `index` for a plain index and an HNSW
one alike, which is why 226 had to switch the read to `TypeClass`.

The fake below answers the class dictionary the way IRIS does — including honouring
the `TypeClass` filter only if the statement actually carries one — so an
implementation that asks the looser question fails here rather than in the field.

The live gate is `tests/e2e/test_227_inventory.py` (T054).
"""

from typing import Any, Dict, List, Optional, Tuple

import pytest

from tests.unit.route_fakes_227 import FakeConnection

#: Column order of the inventory's registry read. Asserted, not just used: the
#: registry carries `dimension` and `index_state` next to each other, and a SELECT
#: whose column list drifts from this tuple reads a width out of an index state.
INVENTORY_COLUMNS = (
    "table_name",
    "graph_id",
    "model_key",
    "dimension",
    "dtype",
    "index_state",
    "index_error",
    "recall_measured",
    "recall_measured_at",
)

HNSW = "%SQL.Index.HNSW"


class Catalog:
    """A namespace: registry rows, tables with rows in them, classes, and indexes.

    Separate from `route_fakes_227.FakeRegistry` because the inventory asks four
    questions that routing never asks — the class dictionary, the index dictionary,
    the table catalog and a per-graph row count — and a fake that answered them
    loosely would let the implementation under test look honest.
    """

    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        *,
        counts: Optional[Dict[Tuple[str, str], int]] = None,
        classes: Optional[Dict[str, str]] = None,
        indexes: Optional[Dict[str, List[Tuple[str, str, str]]]] = None,
        node_graphs: Optional[List[str]] = None,
        tables: Optional[List[str]] = None,
    ):
        self.rows = [dict(r) for r in (rows or [])]
        #: `(table, graph) -> row count`. A pair not listed has no rows.
        self.counts = {(t, g or ""): n for (t, g), n in dict(counts or {}).items()}
        #: `table -> projecting class`. A table not listed has no class, which is the
        #: DDL-built case `hnsw_indexes` has to survive.
        self.classes = dict(classes or {})
        #: `class -> [(sql_name, properties, type_class)]`.
        self.indexes = {k: list(v) for k, v in dict(indexes or {}).items()}
        self.node_graphs = list(node_graphs if node_graphs is not None else [""])
        self.tables = list(
            tables if tables is not None else sorted({t for t, _ in self.counts})
        )
        self.statements: List[Tuple[str, List[Any]]] = []
        self.commits = 0

    def statements_matching(self, needle: str) -> List[Tuple[str, List[Any]]]:
        return [(s, p) for s, p in self.statements if needle in s]

    def cursor(self):
        return CatalogCursor(self)


class CatalogCursor:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._rows: List[Tuple[Any, ...]] = []
        self.description: List[Any] = []

    # ------------------------------------------------------------------ DB-API

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        args = list(params or [])
        self.catalog.statements.append((flat, args))
        self._rows = self._select(flat, args) if flat.upper().startswith("SELECT") else []
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    # ----------------------------------------------------------------- reading

    def _select(self, flat: str, args: List[Any]) -> List[Tuple[Any, ...]]:
        if "%Dictionary.CompiledClass" in flat:
            return self._class_rows(flat)
        if "%Dictionary.CompiledIndex" in flat:
            return self._index_rows(flat)
        if "INFORMATION_SCHEMA.TABLES" in flat:
            return [(t,) for t in self.catalog.tables]
        if "INFORMATION_SCHEMA.COLUMNS" in flat:
            return [(1,)]
        if "embedding_registry" in flat:
            if "COUNT(*)" in flat:
                return [(len(self.catalog.rows),)]
            return [tuple(r.get(c) for c in INVENTORY_COLUMNS) for r in self.catalog.rows]
        if "DISTINCT graph_id" in flat:
            return [(g,) for g in self.catalog.node_graphs]
        if "COUNT(*)" in flat:
            return [(self._count(flat, args),)]
        return []

    def _table_in(self, flat: str) -> str:
        after = flat.split(" FROM ", 1)[1].strip()
        return after.split()[0].split(".")[-1]

    def _count(self, flat: str, args: List[Any]) -> int:
        table = self._table_in(flat)
        if table not in self.catalog.tables:
            # IRIS raises SQLCODE -30 for a table that is not there; a registry row
            # naming a dropped table is a real state and the inventory has to survive it.
            raise RuntimeError(
                f"[SQLCODE: <-30>:<Table or view not found>] Graph_KG.{table}"
            )
        graph = (args[0] if args else "") or ""
        return self.catalog.counts.get((table, graph), 0)

    def _class_rows(self, flat: str) -> List[Tuple[Any, ...]]:
        table = flat.split("SqlTableName = '", 1)[1].split("'", 1)[0]
        class_name = self.catalog.classes.get(table)
        return [(class_name,)] if class_name else []

    def _index_rows(self, flat: str) -> List[Tuple[Any, ...]]:
        """The index dictionary, honouring a `TypeClass` filter only when asked.

        This is the whole point of the fake. IRIS holds every index in this table —
        the bitmap on `graph_id`, the unique constraint, the ANN index — and tells
        them apart by `TypeClass`. A caller that omits that predicate is handed all
        of them, and calling the first one an HNSW index is exactly the synthesized
        row spec 226 removed.
        """
        parent = flat.split("parent = '", 1)[1].split("'", 1)[0]
        found = self.catalog.indexes.get(parent, [])
        if "TypeClass" in flat and "HNSW" in flat:
            found = [i for i in found if "HNSW" in (i[2] or "")]
        return [(name, props) for name, props, _type_class in found]


def _engine(catalog: Catalog):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(FakeConnection(catalog))
    catalog.statements.clear()
    return engine


def _route_row(table_name: str, graph_id: str = "", **over) -> Dict[str, Any]:
    row = {
        "table_name": table_name,
        "graph_id": graph_id,
        "model_key": "bge-small",
        "dimension": 384,
        "dtype": "DOUBLE",
        "index_state": None,
        "index_error": None,
        "recall_measured": None,
        "recall_measured_at": None,
    }
    row.update(over)
    return row


def _by_table(inventory) -> Dict[Optional[str], Any]:
    return {row.table_name: row for row in inventory}


# --- present means the dictionary says so ------------------------------------------


def test_an_hnsw_index_in_the_dictionary_reports_present():
    catalog = Catalog(
        [_route_row("kg_emb_aaaaaaaaaaaaaaaa")],
        counts={("kg_emb_aaaaaaaaaaaaaaaa", ""): 12},
        classes={"kg_emb_aaaaaaaaaaaaaaaa": "Graph.KG.kgembaaaaaaaaaaaaaaaa"},
        indexes={
            "Graph.KG.kgembaaaaaaaaaaaaaaaa": [("idxannHNSW", "emb", HNSW)],
        },
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_aaaaaaaaaaaaaaaa"]
    assert row.index_state == "present"
    assert row.index_name == "idxannHNSW"
    assert row.row_count == 12


def test_a_registry_row_claiming_present_is_not_enough():
    """The registry records what one `CREATE INDEX` replied, not what exists now.

    A table rebuilt by a migration, or restored from a backup taken before the
    build, keeps its registry row and loses its index. Reporting the row would tell
    the operator their searches are indexed while every one of them scans.
    """
    catalog = Catalog(
        [_route_row("kg_emb_bbbbbbbbbbbbbbbb", index_state="present")],
        counts={("kg_emb_bbbbbbbbbbbbbbbb", ""): 5},
        classes={"kg_emb_bbbbbbbbbbbbbbbb": "Graph.KG.kgembbbbbbbbbbbbbbbbb"},
        indexes={},
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_bbbbbbbbbbbbbbbb"]
    assert row.index_state != "present", (
        "the inventory repeated the registry's claim instead of reading the class "
        "dictionary; this is spec 226's synthesized index row with an extra hop"
    )
    assert row.index_name is None


def test_rows_in_the_table_are_not_an_index():
    """Verbatim the 3.1.0 bug: `ONLINE` when the table had rows, `NOT_BUILT` when not."""
    catalog = Catalog(
        [_route_row("kg_emb_cccccccccccccccc")],
        counts={("kg_emb_cccccccccccccccc", ""): 100_000},
        classes={"kg_emb_cccccccccccccccc": "Graph.KG.kgembcccccccccccccccc"},
        indexes={},
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_cccccccccccccccc"]
    assert row.row_count == 100_000
    assert row.index_state != "present"
    assert row.index_name is None


def test_a_plain_index_on_the_table_is_not_an_ann_index():
    """`Type` reads `index` for both. `TypeClass` is the column that tells them apart.

    Every routed table has non-ANN indexes by construction: the identity primary key
    on `emb_rowid` and the unique constraint on `(graph_id, node_id)`. If the
    dictionary read does not filter on `TypeClass`, every route in the namespace
    reports an ANN index it does not have.
    """
    catalog = Catalog(
        [_route_row("kg_emb_dddddddddddddddd")],
        counts={("kg_emb_dddddddddddddddd", ""): 3},
        classes={"kg_emb_dddddddddddddddd": "Graph.KG.kgembdddddddddddddddd"},
        indexes={
            "Graph.KG.kgembdddddddddddddddd": [
                ("uqgraphnode", "graph_id,node_id", "%Library.Index"),
                ("embrowid", "emb_rowid", "%Library.Index"),
            ]
        },
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_dddddddddddddddd"]
    assert row.index_state != "present", (
        "a unique constraint was reported as a vector index; the dictionary read is "
        "not filtering on TypeClass"
    )
    assert row.index_name is None


def test_the_dictionary_read_filters_on_type_class():
    """The predicate itself, so the fake above is not the only thing enforcing it."""
    catalog = Catalog(
        [_route_row("kg_emb_eeeeeeeeeeeeeeee")],
        counts={("kg_emb_eeeeeeeeeeeeeeee", ""): 1},
        classes={"kg_emb_eeeeeeeeeeeeeeee": "Graph.KG.kgembeeeeeeeeeeeeeeee"},
        indexes={"Graph.KG.kgembeeeeeeeeeeeeeeee": [("idxannHNSW", "emb", HNSW)]},
    )
    _engine(catalog).embedding_inventory()
    probes = catalog.statements_matching("%Dictionary.CompiledIndex")
    assert probes, "the inventory never read the index dictionary"
    for sql, _params in probes:
        assert "TypeClass" in sql, f"index probe does not name TypeClass: {sql}"


def test_no_route_reports_an_index_the_dictionary_does_not_hold():
    """SC-009 as a count: zero synthesized index rows across a whole namespace."""
    catalog = Catalog(
        [
            _route_row("kg_emb_1111111111111111", "tenant-a", index_state="present"),
            _route_row("kg_emb_2222222222222222", "tenant-b", model_key="e5-base"),
            _route_row("kg_NodeEmbeddings", "", model_key=None),
        ],
        counts={
            ("kg_emb_1111111111111111", "tenant-a"): 7,
            ("kg_emb_2222222222222222", "tenant-b"): 9,
            ("kg_NodeEmbeddings", ""): 400,
        },
        classes={
            "kg_emb_1111111111111111": "Graph.KG.kgemb1111111111111111",
            "kg_emb_2222222222222222": "Graph.KG.kgemb2222222222222222",
            "kg_NodeEmbeddings": "Graph.KG.kgNodeEmbeddings",
        },
        indexes={
            "Graph.KG.kgemb2222222222222222": [("idxannHNSW", "emb", HNSW)],
            "Graph.KG.kgNodeEmbeddings": [("idnode", "node_id", "%Library.Index")],
        },
        node_graphs=["", "tenant-a", "tenant-b"],
    )
    inventory = _engine(catalog).embedding_inventory()
    indexed = {r.table_name for r in inventory if r.index_state == "present"}
    assert indexed == {"kg_emb_2222222222222222"}, (
        f"inventory claims ANN indexes the dictionary does not hold: {indexed}"
    )
    synthesized = [
        r.table_name for r in inventory if r.index_name and r.index_state != "present"
    ]
    assert synthesized == [], f"index names reported without a present state: {synthesized}"


# --- absent and refused are different answers -------------------------------------


def test_a_recorded_refusal_is_reported_with_its_reason():
    """US4-2: no index, and the reason it was not built is available.

    `refused` and `absent` are worth distinguishing because they call for different
    actions: a refusal is an IRIS build limitation an operator can read and act on,
    while absence means nobody has tried yet.
    """
    catalog = Catalog(
        [
            _route_row(
                "kg_emb_ffffffffffffffff",
                index_state="refused",
                index_error="ERROR #7222: HNSW index requires a bitmap-extent-capable table",
            )
        ],
        counts={("kg_emb_ffffffffffffffff", ""): 2},
        classes={"kg_emb_ffffffffffffffff": "Graph.KG.kgembffffffffffffffff"},
        indexes={},
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_ffffffffffffffff"]
    assert row.index_state == "refused"
    assert "7222" in (row.index_error or ""), (
        "the refusal was reported without the text IRIS gave, which is the only thing "
        "that tells the operator why"
    )


def test_nothing_recorded_and_nothing_built_is_absent():
    catalog = Catalog(
        [_route_row("kg_emb_9999999999999999")],
        counts={("kg_emb_9999999999999999", ""): 4},
        classes={"kg_emb_9999999999999999": "Graph.KG.kgemb9999999999999999"},
        indexes={},
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_9999999999999999"]
    assert row.index_state == "absent"
    assert row.index_error is None


def test_a_table_with_no_projecting_class_is_absent_not_an_error():
    """A DDL-built table has whatever class name IRIS chose, or none this can find.

    `hnsw_indexes` answers empty there. The inventory must carry that through as
    "no index" rather than raising from the middle of a report over N routes.
    """
    catalog = Catalog(
        [_route_row("kg_emb_7777777777777777")],
        counts={("kg_emb_7777777777777777", ""): 6},
        classes={},
        indexes={},
    )
    row = _by_table(_engine(catalog).embedding_inventory())["kg_emb_7777777777777777"]
    assert row.index_state == "absent"
    assert row.row_count == 6


# --- what the report must not leave out -------------------------------------------


def test_a_graph_with_no_route_appears_with_no_table():
    """US4-3. Omitting it makes a missing route indistinguishable from an empty graph.

    Which is the one question the inventory exists to answer: an operator whose
    searches return nothing needs to know whether the vectors are elsewhere or were
    never written.
    """
    catalog = Catalog(
        [_route_row("kg_emb_1111111111111111", "tenant-a")],
        counts={("kg_emb_1111111111111111", "tenant-a"): 3},
        classes={"kg_emb_1111111111111111": "Graph.KG.kgemb1111111111111111"},
        indexes={},
        node_graphs=["", "tenant-a", "tenant-quiet"],
    )
    inventory = _engine(catalog).embedding_inventory()
    graphs = {row.graph_id for row in inventory}
    assert "tenant-quiet" in graphs, (
        f"a graph with nodes and no embeddings was omitted entirely: {graphs}"
    )
    quiet = [r for r in inventory if r.graph_id == "tenant-quiet"]
    assert len(quiet) == 1
    assert quiet[0].table_name is None
    assert quiet[0].row_count == 0
    assert quiet[0].dimension is None
    assert quiet[0].index_state == "absent"


def test_one_row_per_graph_and_model():
    """US4-1. Two models in one graph are two routes and two rows, not one merged row."""
    catalog = Catalog(
        [
            _route_row("kg_emb_aaaa000000000000", "tenant-a", model_key="bge-small"),
            _route_row(
                "kg_emb_aaaa111111111111",
                "tenant-a",
                model_key="e5-base",
                dimension=768,
            ),
        ],
        counts={
            ("kg_emb_aaaa000000000000", "tenant-a"): 11,
            ("kg_emb_aaaa111111111111", "tenant-a"): 22,
        },
        classes={},
        node_graphs=["tenant-a"],
    )
    inventory = _engine(catalog).embedding_inventory()
    pairs = {(r.graph_id, r.model_key, r.dimension, r.row_count) for r in inventory}
    assert pairs == {
        ("tenant-a", "bge-small", 384, 11),
        ("tenant-a", "e5-base", 768, 22),
    }, pairs


def test_a_registry_row_naming_a_dropped_table_still_appears():
    """The state a failed post-commit drop during an erase leaves, and a real one.

    Reporting nothing would make the stale row invisible to the operator who has to
    remove it; raising would take the whole report down over one bad row.
    """
    catalog = Catalog(
        [_route_row("kg_emb_dead000000000000", "tenant-gone")],
        counts={},
        tables=[],
        classes={},
        node_graphs=[""],
    )
    inventory = _engine(catalog).embedding_inventory()
    row = _by_table(inventory)["kg_emb_dead000000000000"]
    assert row.row_count == 0
    assert row.index_state == "absent"


def test_the_row_count_is_scoped_to_the_routes_graph():
    """A legacy table holds every graph's rows; a routed one holds one graph's.

    Counting the whole table would report the namespace's total against each graph —
    the same unscoped read the spec exists to remove, in the report about it.
    """
    catalog = Catalog(
        [
            _route_row("kg_NodeEmbeddings", "", model_key=None),
            _route_row("kg_NodeEmbeddings", "tenant-a", model_key=None),
        ],
        counts={("kg_NodeEmbeddings", ""): 40, ("kg_NodeEmbeddings", "tenant-a"): 2},
        classes={},
        node_graphs=["", "tenant-a"],
    )
    inventory = _engine(catalog).embedding_inventory()
    counted = {(r.graph_id, r.row_count) for r in inventory}
    assert counted == {("", 40), ("tenant-a", 2)}, counted
    for sql, params in catalog.statements_matching("COUNT(*)"):
        if "kg_NodeEmbeddings" not in sql:
            continue
        assert "COALESCE(graph_id" in sql, f"unscoped count over a shared table: {sql}"
        assert params, "the count names a graph in its SQL but binds nothing"


def test_the_registry_read_asks_for_the_columns_the_row_reports():
    catalog = Catalog(
        [_route_row("kg_emb_aaaaaaaaaaaaaaaa")],
        counts={("kg_emb_aaaaaaaaaaaaaaaa", ""): 1},
    )
    _engine(catalog).embedding_inventory()
    reads = [
        sql
        for sql, _p in catalog.statements_matching("embedding_registry")
        if "COUNT(*)" not in sql
    ]
    assert reads, "the inventory never read the registry"
    for column in INVENTORY_COLUMNS:
        assert any(column in sql for sql in reads), (
            f"the registry read does not ask for {column}, so the row cannot report it"
        )


def test_the_table_catalog_probe_is_restricted_to_the_graph_schema():
    """T056: `SQLUser.*` carries an auto-generated view per class.

    Left unfiltered, the probe answers with both the table and its view and the
    report doubles.
    """
    catalog = Catalog(
        [_route_row("kg_emb_aaaaaaaaaaaaaaaa")],
        counts={("kg_emb_aaaaaaaaaaaaaaaa", ""): 1},
    )
    _engine(catalog).embedding_inventory()
    probes = catalog.statements_matching("INFORMATION_SCHEMA.TABLES")
    for sql, params in probes:
        assert "TABLE_SCHEMA" in sql, f"catalog probe is namespace-wide: {sql}"
        assert "Graph_KG" in sql or "Graph_KG" in [str(p) for p in params], (
            f"catalog probe does not name the Graph_KG schema: {sql} {params}"
        )


def test_an_unreadable_registry_yields_an_empty_inventory_not_an_exception():
    """No registry means no routes to report, which is a fact and not a failure.

    `list_indexes` and the CLI call this; a raise here takes down a status command on
    a 3.2.0 database whose registry predates the route columns.
    """

    class Unreadable(Catalog):
        def cursor(self):
            return UnreadableCursor(self)

    class UnreadableCursor(CatalogCursor):
        def execute(self, sql, params=None):
            flat = " ".join(str(sql).split())
            if "embedding_registry" in flat:
                self.catalog.statements.append((flat, list(params or [])))
                raise RuntimeError(
                    "[SQLCODE: <-30>:<Table or view not found>] Graph_KG.embedding_registry"
                )
            return super().execute(sql, params)

    catalog = Unreadable([], node_graphs=[])
    assert _engine(catalog).embedding_inventory() == []


def test_the_inventory_length_is_the_routed_table_count():
    """FR-038: an operator watching route growth reads it off this list."""
    rows = [
        _route_row(f"kg_emb_{i:016x}", f"tenant-{i}", model_key="bge-small")
        for i in range(5)
    ]
    catalog = Catalog(
        rows,
        counts={(r["table_name"], r["graph_id"]): 1 for r in rows},
        node_graphs=[r["graph_id"] for r in rows],
    )
    inventory = _engine(catalog).embedding_inventory()
    routed = [r for r in inventory if r.table_name]
    assert len(routed) == 5, [r.table_name for r in inventory]


def test_the_row_is_frozen():
    """A report an operator can mutate is a report that can be edited into agreement."""
    from iris_vector_graph._engine.schema import EmbeddingInventoryRow

    row = EmbeddingInventoryRow(
        graph_id="",
        model_key=None,
        table_name="kg_NodeEmbeddings",
        dimension=384,
        dtype="DOUBLE",
        row_count=1,
        index_name=None,
        index_state="absent",
        index_error=None,
        recall_measured=None,
        recall_measured_at=None,
    )
    with pytest.raises(Exception):
        row.index_state = "present"
