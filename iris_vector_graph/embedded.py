"""
iris_vector_graph.embedded — dbapi2 adapter for IRIS embedded Python.

Allows IRISGraphEngine and BulkLoader to run inside an ObjectScript
Language=python method with zero boilerplate:

    from iris_vector_graph.embedded import EmbeddedConnection
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(EmbeddedConnection())
    engine.initialize_schema()

Only available when running inside IRIS (the embedded iris module must be
importable). Raises ImportError with a clear message otherwise.
"""

__all__ = ["EmbeddedConnection", "EmbeddedCursor"]


def _require_iris_sql():
    try:
        import iris  # noqa: F401
        return iris.sql
    except ImportError as exc:
        raise ImportError(
            "iris_vector_graph.embedded requires the embedded iris module "
            "(available only when running inside IRIS as a Language=python method). "
            "For external callers use iris.connect() and pass the connection directly."
        ) from exc


class EmbeddedCursor:
    """dbapi2 cursor backed by iris.sql.prepare / execute.

    Implements the subset of the dbapi2 cursor interface used by
    IRISGraphEngine and BulkLoader:
      execute, executemany, fetchone, fetchall, fetchmany,
      description, rowcount, close.
    """

    def __init__(self):
        self._rs = None
        self._rows = None   # materialised row cache (used by fetchmany)
        self._pos = 0       # position in materialised cache
        self.description = None
        self.rowcount = -1

    def execute(self, sql, params=None):
        iris_sql = _require_iris_sql()
        # START TRANSACTION / COMMIT / ROLLBACK are no-ops in embedded context:
        # IRIS manages transactions automatically in Language=python methods.
        # Calling iris.tstart() / tcommit() from inside a wgproto job raises <COMMAND>.
        lowered = sql.strip().upper()
        if lowered in ("START TRANSACTION", "COMMIT", "ROLLBACK",
                       "BEGIN", "BEGIN TRANSACTION"):
            self._rs = None
            self._rows = None
            self.description = None
            self.rowcount = -1
            return

        stmt = iris_sql.prepare(sql)
        if params:
            self._rs = stmt.execute(*params)
        else:
            self._rs = stmt.execute()

        self._rows = None
        self._pos = 0

        # Populate description from result set metadata if available
        self.description = None
        self.rowcount = -1
        if self._rs is not None:
            try:
                cols = self._rs.columnCount()
                if cols and cols > 0:
                    self.description = [
                        (self._rs.columnName(i), None, None, None, None, None, None)
                        for i in range(1, cols + 1)
                    ]
            except Exception:
                pass

    def executemany(self, sql, seq):
        iris_sql = _require_iris_sql()
        stmt = iris_sql.prepare(sql)
        count = 0
        for params in seq:
            stmt.execute(*params)
            count += 1
        self.rowcount = count
        self._rs = None
        self._rows = None

    def _materialise(self):
        """Drain self._rs into self._rows once; used by fetchone/fetchmany."""
        if self._rows is None:
            self._rows = []
            if self._rs is not None:
                try:
                    for row in self._rs:
                        self._rows.append(tuple(row))
                except Exception:
                    pass
                # Do NOT assign self._rs = None here — description may still be needed
            self._pos = 0

    def fetchall(self):
        self._materialise()
        result = self._rows[self._pos:]
        self._pos = len(self._rows)
        self._rs = None
        return result

    def fetchone(self):
        self._materialise()
        if self._pos >= len(self._rows):
            self._rs = None
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size=None):
        self._materialise()
        size = size or 1
        result = self._rows[self._pos:self._pos + size]
        self._pos += len(result)
        return result

    def close(self):
        self._rs = None
        self._rows = None


class EmbeddedConnection:
    """dbapi2 adapter for IRIS embedded Python (Language=python methods).

    Wraps iris.sql.prepare/execute so IRISGraphEngine and BulkLoader work
    identically inside IRIS as they do with an external iris.connect() connection.

    Transactions are managed automatically by IRIS in embedded context —
    commit() and rollback() are intentional no-ops. START TRANSACTION /
    COMMIT / ROLLBACK issued via cursor.execute() are also silently dropped
    (calling iris.tstart/tcommit from a wgproto job raises <COMMAND>).

    Usage::

        from iris_vector_graph.embedded import EmbeddedConnection
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(EmbeddedConnection())
        engine.initialize_schema()
    """

    def cursor(self):
        return EmbeddedCursor()

    def commit(self):
        pass  # auto-managed by IRIS in embedded context

    def rollback(self):
        pass  # auto-managed by IRIS in embedded context

    def close(self):
        pass
