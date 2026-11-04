"""4.6.0 R137: the first `initialize_schema()` creates `idx_ledger_correlation`.

`ledger_revisions` is the table of a class (`Graph.KG.LedgerRevision`), so it exists only once the deploy (step 6) has
compiled it. `ensure_indexes` (step 3) asked for the index before that, so on a fresh namespace the `CREATE INDEX` met
no table and was skipped as optional; the second run added it, rewriting the class. Measured by T061
(`tests/e2e/test_262_inventory.py`); here the order is pinned with a catalog stand-in whose `ledger_revisions` table
appears when the deploy runs.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from iris_vector_graph._engine.class_deploy import DeployResult
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

LEDGER_DDL = "CREATE INDEX idx_ledger_correlation ON Graph_KG.ledger_revisions (correlation_id)"


class _Namespace:
    """What setup can see: a ledger table only after the deploy, and the indexes created on it."""

    def __init__(self):
        self.deployed = False
        self.indexes: set = set()
        self.log: list = []


class _Cursor:
    description = None
    rowcount = 0

    def __init__(self, ns: _Namespace):
        self.ns, self._row = ns, None

    def execute(self, sql, params=None):
        self.ns.log.append(sql)
        self._row = None
        if "INFORMATION_SCHEMA.TABLES" in sql and "ledger_revisions" in sql:
            self._row = (1,) if self.ns.deployed else None
        elif "INFORMATION_SCHEMA.INDEXES" in sql and "idx_ledger_correlation" in (sql + str(params)):
            self._row = (1,) if "idx_ledger_correlation" in self.ns.indexes else None
        elif sql.startswith("CREATE INDEX idx_ledger_correlation"):
            if not self.ns.deployed:
                raise RuntimeError("[SQLCODE: <-30>:<Table or view not found>] Graph_KG.ledger_revisions")
            if "idx_ledger_correlation" in self.ns.indexes:
                raise RuntimeError("[SQLCODE: <-324>:<Index with this name already defined for this table>]")
            self.ns.indexes.add("idx_ledger_correlation")

    def executemany(self, sql, rows):
        return None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def close(self):
        pass


class _Native:
    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "IVG460FX6"
        if (cls, method) == ("Graph.KG.LedgerRevision", "BuildCorrelationIndex"):
            return "1"  # a present index is checked for entries since 4.6.0 R150 (test_460_r150_*)
        raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

    def classMethodVoid(self, cls, method, *args):
        self.classMethodValue(cls, method, *args)

    def get(self, *subscripts):
        return None

    def set(self, value, *subscripts):
        pass


def _setup(ns: _Namespace) -> dict:
    def deploy(cursor, path, conn=None, force=None, schema=None):
        ns.log.append("DEPLOY")
        ns.deployed = True
        GraphSchema.last_deploy = DeployResult(deployed=True, compiled=["Graph.KG.LedgerRevision"])
        return IRISCapabilities(objectscript_deployed=True)

    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = lambda *a, **k: _Cursor(ns)
    with patch("iris.createIRIS", lambda c: _Native(), create=False), \
            patch.object(GraphSchema, "deploy_objectscript_classes", staticmethod(deploy)), \
            patch.object(GraphSchema, "ensure_adjacency_index", staticmethod(lambda cur: {})), \
            patch.object(GraphSchema, "bootstrap_kg_global", staticmethod(lambda cur, conn=None: False)):
        return IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()


def test_the_first_run_creates_the_index_after_the_ledger_table_is_compiled():
    ns = _Namespace()
    status = _setup(ns)
    assert "idx_ledger_correlation" in ns.indexes
    assert status["ledger_index"] == "created"
    creates = [i for i, s in enumerate(ns.log) if s == LEDGER_DDL]
    assert creates and creates[0] > ns.log.index("DEPLOY"), "asked for before the deploy compiled the table"
    assert len(creates) == 1, "asked for twice in one run"


def test_a_second_run_leaves_it_alone():
    ns = _Namespace()
    _setup(ns)
    ns.log.clear()
    status = _setup(ns)
    assert status["ledger_index"] == "present"
    assert LEDGER_DDL not in ns.log


def test_without_a_ledger_table_it_is_skipped_and_says_why():
    """A server that did not compile the ledger class (an aborted deploy) has no table to index."""
    cur = _Cursor(_Namespace())
    assert GraphSchema.ensure_ledger_index(cur) == "skipped: no ledger_revisions table"
    assert LEDGER_DDL not in cur.ns.log


def test_a_failed_create_is_a_setup_warning_not_a_silent_skip(caplog):
    ns = _Namespace()

    class _Refusing(_Cursor):
        def execute(self, sql, params=None):
            if sql == LEDGER_DDL:
                raise RuntimeError("[SQLCODE: <-110>:<Locking conflict in filing>]")
            return super().execute(sql, params)

    def deploy(cursor, path, conn=None, force=None, schema=None):
        ns.deployed = True
        GraphSchema.last_deploy = DeployResult(deployed=True)
        return IRISCapabilities(objectscript_deployed=True)

    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = lambda *a, **k: _Refusing(ns)
    with patch("iris.createIRIS", lambda c: _Native(), create=False), \
            patch.object(GraphSchema, "deploy_objectscript_classes", staticmethod(deploy)), \
            patch.object(GraphSchema, "ensure_adjacency_index", staticmethod(lambda cur: {})), \
            patch.object(GraphSchema, "bootstrap_kg_global", staticmethod(lambda cur, conn=None: False)), \
            caplog.at_level(logging.WARNING):
        status = IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    assert status["ledger_index"].startswith("not created")
    assert any("idx_ledger_correlation" in w and "-110" in w for w in status["warnings"])


def test_ensure_indexes_no_longer_asks_for_it():
    """Step 3 runs before the deploy, where the table may not exist yet; the one request is after the deploy."""
    cur = MagicMock()
    GraphSchema.ensure_indexes(cur)
    assert not [c for c in cur.execute.call_args_list if c.args and "idx_ledger_correlation" in c.args[0]]

