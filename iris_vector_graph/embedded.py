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

Deprecation notice (v1.95.0+): prefer iris-embedded-python-wrapper which
provides the same auto-detection via iris.dbapi.connect(mode='embedded').
EmbeddedConnection is retained for backward compatibility and for contexts
where the wrapper is not available (e.g. older IRIS versions).

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


def _is_no_data(exc: Exception) -> bool:
    """SQLCODE 100: an UPDATE or DELETE that matched no row. A client cursor returns an empty
    result for it; ``iris.sql`` raises ``SQLError`` with an empty message."""
    return getattr(exc, "sqlcode", None) == 100 or getattr(exc.__cause__, "sqlcode", None) == 100


def _no_data_ok(fn, *args):
    """Call ``fn``; a no-row UPDATE/DELETE (SQLCODE 100) is not an error."""
    try:
        return fn(*args)
    except Exception as exc:
        if not _is_no_data(exc):
            raise
        return None


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


def _exec_inline(iris_sql, sql: str, params):
    """Run `sql` with its parameters written in as literals.

    ``iris.sql.exec`` raises ``SQLError`` with SQLCODE 100 and an empty message when an
    INSERT ... SELECT or an UPDATE / DELETE touches no row. A prepared statement reports that as
    a zero row count, and the callers (``_ensure_edge_endpoints``) read an error message to tell
    "already there" from a failure, so SQLCODE 100 is returned as no result here too."""
    try:
        return iris_sql.exec(_inline_params(sql, list(params)))
    except Exception as exc:
        if getattr(exc, "sqlcode", None) == 100:
            return None
        raise


def _description_from_metadata(rs):
    """Column names of an ``iris.sql`` result, read from the statement metadata.

    ``iris.sql`` results have no ``columnCount()`` / ``columnName()``; the names live on the
    underlying ``%SQL.StatementResult``. Without them every embedded Cypher result had rows and
    no columns. Returns None when the metadata cannot be read."""
    try:
        meta = rs.ResultSet._GetMetadata()
        columns = meta.columns
        count = int(columns.Count())
        if count <= 0:
            return None
        return [(str(columns.GetAt(i).colName), None, None, None, None, None, None) for i in range(1, count + 1)]
    except Exception:
        return None


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

        # The statement runs in this frame, as it did before SQLCODE 100 was handled: a result set
        # handed back out of a helper's frame made the wgproto Callin worker segfault (4.2.1).
        no_data = False
        try:
            try:
                if params and any(isinstance(p, str) and p == "" for p in params):
                    # An empty string bound as a parameter reaches the engine as SQL NULL, so a
                    # NOT NULL column holding '' (graph_id of the default graph) rejected every
                    # default-graph write. Inline the literals for these statements.
                    self._rs = _exec_inline(iris_sql, sql, params)
                else:
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
        except Exception as exc:
            if not _is_no_data(exc):
                raise
            self._rs = None
            no_data = True
        if self._rs is None and lowered.startswith(("UPDATE", "DELETE", "INSERT")):
            no_data = True  # _exec_inline already folded SQLCODE 100 into "no result"

        self._rows = None
        self._pos = 0

        # Populate description from result set metadata if available
        self.description = None
        self.rowcount = 0 if no_data else -1
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
            if self.description is None:
                self.description = _description_from_metadata(self._rs)

    def executemany(self, sql, seq):
        iris_sql = self._get_iris_sql()
        try:
            stmt = iris_sql.prepare(sql)
            count = 0
            for params in seq:
                if any(isinstance(p, str) and p == "" for p in params):
                    # same reason as execute(): a bound '' arrives as NULL
                    _no_data_ok(_exec_inline, iris_sql, sql, params)
                else:
                    _no_data_ok(stmt.execute, *params)
                count += 1
        except Exception as exc:
            if not _is_ddtab_error(exc):
                raise
            count = 0
            for params in seq:
                try:
                    _no_data_ok(iris_sql.exec, _inline_params(sql, list(params)))
                except Exception as exc2:
                    if not _is_ddtab_error(exc2):
                        raise
                    _no_data_ok(_sql_statement_execute, sql, list(params))
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


def _py_name(method):
    """Embedded Python spells a ``%`` system method with a leading underscore (``%New`` is ``_New``)."""
    return "_" + method[1:] if method.startswith("%") else method


class EmbeddedIRIS:
    """The slice of the Native API (``iris.createIRIS``) that class-method callers use, for a
    process running inside IRIS, where ``createIRIS`` is not available.

    ``classMethodValue`` / ``classMethodVoid`` / ``classMethodObject`` dispatch through
    ``iris.cls``. Without this an engine built on ``EmbeddedConnection`` failed every call
    that reaches ObjectScript (``rebuild_kg``, ``sync``, the BM25 and ledger clients).
    """

    def __init__(self, iris_module=None):
        self._iris = iris_module

    def _cls(self, name):
        iris = self._iris
        if iris is None:
            import iris as iris  # noqa: PLC0415 - only importable inside IRIS
        return iris.cls(name)

    def classMethodValue(self, cls, method, *args):
        return getattr(self._cls(cls), _py_name(method))(*args)

    def classMethodVoid(self, cls, method, *args):
        getattr(self._cls(cls), _py_name(method))(*args)

    def classMethodObject(self, cls, method, *args):
        return getattr(self._cls(cls), _py_name(method))(*args)

    def get(self, name, *subscripts):
        """A global node, ``None`` when undefined: ``get("^IVG.Deploy", "fingerprint")``."""
        return self._gref(name)[self._key(subscripts)]

    def set(self, value, name, *subscripts):
        self._gref(name)[self._key(subscripts)] = value

    def getString(self, name, *subscripts):
        """The node's value as text, ``None`` when undefined (the Native API's ``getString``)."""
        value = self.get(name, *subscripts)
        return None if value is None else str(value)

    def kill(self, name, *subscripts):
        """Kill the node and everything below it."""
        self._gref(name).kill(list(subscripts))

    def isDefined(self, name, *subscripts):
        """0 undefined, 1 value, 10 children only, 11 both (``$Data``)."""
        return int(self._gref(name).data(list(subscripts)))

    def nextSubscript(self, reverse, name, *subscripts):
        """The sibling after the last subscript (before it when ``reverse``); ``None`` at the end."""
        return self._gref(name).order(list(subscripts), -1 if reverse else 1)

    def _gref(self, name):
        iris = self._iris
        if iris is None:
            import iris as iris  # noqa: PLC0415 - only importable inside IRIS
        return iris.gref(name)

    @staticmethod
    def _key(subscripts):
        return subscripts[0] if len(subscripts) == 1 else tuple(subscripts)


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
