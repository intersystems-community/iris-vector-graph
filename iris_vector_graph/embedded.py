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

## Known constraints for Language=python (wgproto) context

### sys.path shadowing after pip install
When iris-vector-graph is pip-installed inside an IRIS container, the pip
package puts an external `iris` (intersystems_irispython) on sys.path. This
external `iris` lacks `iris.execute`, `iris.gref`, `iris.cls`, and
`iris.sql` — so Language=python methods that call EmbeddedConnection break.

Fix: ensure `/usr/irissys/lib/python` is first in sys.path so the embedded
`iris` module takes priority. This module does that automatically in
`_require_iris_sql()`.

### Long-running operations (model loading, bulk embed) and XD timeout
Language=python methods run inside a wgproto job with a configurable XD
timeout (default ~30s). Loading a SentenceTransformer model takes ~8-10s,
which may exceed the timeout.

Pattern for long-running ML operations:
1. Run as a persistent `irispython` process outside the wgproto lifecycle
2. Communicate via IRIS globals (e.g. `^EmbedQueue(reqId, "text") = text`)
3. Language=python method submits to queue and polls for result

See iris_vector_graph/docs/embedded-ml-pattern.md for a reference
implementation (the "embed_daemon" pattern).
"""

__all__ = ["EmbeddedConnection", "EmbeddedCursor"]

import sys as _sys


def _inline_params(sql: str, params: list) -> str:
    result = []
    param_idx = 0
    i = 0
    while i < len(sql):
        if sql[i] == '?' and (i == 0 or sql[i-1] != "'"):
            if param_idx >= len(params):
                raise IndexError(
                    f"Not enough params: need >{param_idx} but got {len(params)}"
                )
            v = params[param_idx]
            param_idx += 1
            if v is None:
                result.append("NULL")
            elif isinstance(v, bool):
                result.append("1" if v else "0")
            elif isinstance(v, int):
                result.append(str(v))
            elif isinstance(v, float):
                result.append(repr(v))
            else:
                result.append("'" + str(v).replace("'", "''") + "'")
        else:
            result.append(sql[i])
        i += 1
    return "".join(result)


def _ensure_embedded_iris_first():
    embedded_path = '/usr/irissys/lib/python'
    mgr_path = '/usr/irissys/mgr/python'
    iris_mod = _sys.modules.get('iris')
    if iris_mod is not None and hasattr(iris_mod, 'sql') and iris_mod.sql is not None:
        return
    changed = False
    for p in [mgr_path, embedded_path]:
        if p in _sys.path and _sys.path[0] != p:
            _sys.path.remove(p)
            _sys.path.insert(0, p)
            changed = True
        elif p not in _sys.path:
            _sys.path.insert(0, p)
            changed = True
    if changed:
        for mod in list(_sys.modules.keys()):
            if mod == 'iris' or mod.startswith('iris.'):
                del _sys.modules[mod]


def _require_iris_sql():
    try:
        _ensure_embedded_iris_first()
        import iris  # noqa: F401
        if not hasattr(iris, 'sql'):
            raise ImportError(
                "iris module found but has no iris.sql attribute — "
                "the pip intersystems_irispython package is shadowing the embedded iris module. "
                "Ensure /usr/irissys/lib/python is first in sys.path."
            )
        return iris.sql
    except ImportError as exc:
        raise ImportError(
            "iris_vector_graph.embedded requires the embedded iris module "
            "(available only when running inside IRIS as a Language=python method). "
            "For external callers use iris.connect() and pass the connection directly."
        ) from exc


def _is_ddtab_error(exc: Exception) -> bool:
    s = str(exc)
    return "<UNIMPLEMENTED>" in s or "ddtab" in s


def _sql_statement_execute(sql: str, params=None):
    import iris
    stmt = iris.cls("%SQL.Statement")._New()
    sc = stmt._Prepare(sql)
    if not sc:
        raise RuntimeError(f"%%SQL.Statement._Prepare failed sc={sc}")
    if params:
        rs = stmt._Execute(*params)
    else:
        rs = stmt._Execute()
    return _SqlStatementResultSet(rs)


class _SqlStatementResultSet:

    def __init__(self, rs):
        self._rs = rs
        self._done = False

    def columnCount(self):
        try:
            return int(self._rs._GetProperty("ColCount")) if self._rs else 0
        except Exception:
            return 0

    def columnName(self, i):
        try:
            return str(self._rs._GetProperty("MetaData")._GetProperty("columns")._GetAt(i)._GetProperty("colName"))
        except Exception:
            return f"col{i}"

    def __iter__(self):
        if self._rs is None or self._done:
            return
        try:
            while True:
                sc = int(str(self._rs._Next()))
                if sc == 0:
                    break
                row = []
                col_count = self.columnCount()
                for i in range(1, col_count + 1):
                    try:
                        val = self._rs._GetData(i)
                        row.append(None if val is None else val)
                    except Exception:
                        row.append(None)
                yield tuple(row)
        except Exception:
            pass
        self._done = True


class EmbeddedCursor:

    def __init__(self, iris_sql=None):
        self._iris_sql = iris_sql
        self._rs = None
        self._rows = None
        self._pos = 0
        self.description = None
        self.rowcount = -1

    def _get_iris_sql(self):
        if self._iris_sql is not None:
            return self._iris_sql
        return _require_iris_sql()

    def execute(self, sql, params=None):
        iris_sql = self._get_iris_sql()
        lowered = sql.strip().upper()
        if lowered in ("START TRANSACTION", "COMMIT", "ROLLBACK",
                       "BEGIN", "BEGIN TRANSACTION"):
            self._rs = None
            self._rows = None
            self.description = None
            self.rowcount = -1
            return

        try:
            stmt = iris_sql.prepare(sql)
            if params:
                self._rs = stmt.execute(*params)
            else:
                self._rs = stmt.execute()
        except Exception as exc1:
            if not _is_ddtab_error(exc1):
                raise
            try:
                self._rs = iris_sql.exec(_inline_params(sql, params or []))
            except Exception as exc2:
                if not _is_ddtab_error(exc2):
                    raise
                try:
                    self._rs = _sql_statement_execute(sql, params)
                except Exception as exc3:
                    raise RuntimeError(
                        f"All three embedded SQL paths failed.\n"
                        f"  prepare: {exc1}\n"
                        f"  exec: {exc2}\n"
                        f"  %%SQL.Statement: {exc3}"
                    ) from exc3

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
        iris_sql = self._get_iris_sql()
        try:
            stmt = iris_sql.prepare(sql)
            count = 0
            for params in seq:
                stmt.execute(*params)
                count += 1
        except Exception as exc:
            if not _is_ddtab_error(exc):
                raise
            count = 0
            for params in seq:
                try:
                    iris_sql.exec(_inline_params(sql, list(params)))
                except Exception as exc2:
                    if not _is_ddtab_error(exc2):
                        raise
                    _sql_statement_execute(sql, list(params))
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

    def __init__(self, iris_sql=None):
        self._iris_sql = iris_sql

    def cursor(self):
        return EmbeddedCursor(self._iris_sql)

    def commit(self):
        try:
            (_require_iris_sql() if self._iris_sql is None else self._iris_sql).exec("COMMIT")
        except Exception:
            pass

    def rollback(self):
        try:
            (_require_iris_sql() if self._iris_sql is None else self._iris_sql).exec("ROLLBACK")
        except Exception:
            pass

    def close(self):
        pass
