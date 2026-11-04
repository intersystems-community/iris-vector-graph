"""The IVG Schema: the one SQL schema in a namespace that holds every table, function, procedure and view IVG creates
(spec 262, ADR-0016, CONTEXT.md).

Its name is chosen once per namespace (``IRISGraphEngine(schema_prefix=...)``, else ``IVG_SCHEMA_PREFIX``, else
``Graph_KG``), validated before any database call, and recorded in ``^IVG.Deploy("schema")`` by the first
``initialize_schema()``. The ObjectScript classes never read the record: the deploy rewrites their sources for it
(:func:`rewrite_schema_text`), so their embedded SQL stays embedded (research R1).
"""

from __future__ import annotations

import contextlib
import os
import re
from datetime import datetime, timezone
from typing import Mapping, Optional

DEFAULT_SCHEMA = "Graph_KG"
#: The variable IVG reads. Never ``IRIS_SCHEMA_PREFIX``: iris-vector-rag reads that one, in the same process.
ENV_VAR = "IVG_SCHEMA_PREFIX"
#: ``(global, subscript)`` of the record, beside the deploy fingerprint.
RECORD = ("^IVG.Deploy", "schema")
#: IVG's code packages: a schema whose package is one of these, or under one, would put table classes among them.
CODE_PACKAGES = ("Graph.KG", "IVG", "IVGC")

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


class IVGSchemaError(RuntimeError):
    """A namespace's IVG Schema cannot be used as asked: it was set up by an earlier release and not upgraded, or it
    is recorded under another name than the engine's (FR-006, FR-006a), or setup cannot tell which schema is IVG's."""

    def __init__(self, message: str, *, recorded: Optional[str] = None, requested: Optional[str] = None):
        super().__init__(message)
        self.recorded = recorded
        self.requested = requested

    @classmethod
    def not_upgraded(cls, namespace: str) -> "IVGSchemaError":
        return cls(
            f"Namespace {namespace} was set up by an earlier IVG release and has not been upgraded for this one: it "
            "has no IVG Schema record, or lacks this release's SQL objects. Run initialize_schema() on it (see what it "
            "would do first with initialize_schema(dry_run=True)). Nothing was run."
        )

    @classmethod
    def mismatch(cls, recorded: str, requested: str, namespace: str) -> "IVGSchemaError":
        return cls(
            f"Namespace {namespace} records {recorded} as its IVG Schema, and this engine asks for {requested}. A "
            f"namespace holds one IVG Schema per namespace: IVG's globals (^KG, ^NKG, ^IVG.*) belong to the namespace, "
            f"so a second schema would share them. Build the engine with schema_prefix={recorded!r}, or use a separate "
            "namespace for the other schema. Nothing was written.",
            recorded=recorded,
            requested=requested,
        )

    @classmethod
    def unrecorded_schemas(cls, held: Mapping[str, Mapping[str, int]], requested: str,
                           namespace: str) -> "IVGSchemaError":
        """No record, and IVG's tables are in another schema than ``requested``, or in several, with no name given
        (FR-009). ``held``: schema -> {"nodes": n, "rdf_edges": m}."""
        listed = "; ".join(f"{s} ({c.get('nodes', 0)} nodes, {c.get('rdf_edges', 0)} edges)" for s, c in held.items())
        if len(held) == 1:
            only = next(iter(held))
            advice = (f"To upgrade it, build the engine with schema_prefix={only!r} (or set IVG_SCHEMA_PREFIX={only}) "
                      f"and run initialize_schema() again; to start a new IVG Schema {requested} beside it, name it "
                      f"explicitly with schema_prefix={requested!r}, or use a separate namespace.")
        else:
            advice = ("Name the schema to keep with schema_prefix= (or IVG_SCHEMA_PREFIX) and run initialize_schema() "
                      "again: it records that one and leaves the others' tables as they are, for you to move or drop "
                      "(docs/migration/v4.6.0.md gives the SQL).")
        return cls(
            f"Namespace {namespace} has no IVG Schema record, and holds IVG tables in {listed}. A namespace holds one "
            f"IVG Schema per namespace, and this engine asks for {requested}, which was not named explicitly. "
            f"{advice} Nothing was recorded.",
            requested=requested,
        )

    @classmethod
    def no_privilege(cls, user: str, schema: str, privilege: str, nothing_created: bool = True) -> "IVGSchemaError":
        outcome = ("Nothing was created." if nothing_created else
                   "Setup stopped at that statement; what it created before stays, and a re-run once the privilege is "
                   "granted finishes it.")
        return cls(
            f"initialize_schema() cannot create IVG's SQL objects in schema {schema}: user {user} does not hold the SQL "
            f"privilege {privilege}. Setup needs %CREATE_TABLE, %CREATE_FUNCTION and %CREATE_PROCEDURE (GRANT them to "
            f"the user or one of its roles), or run it as a user who holds them. {outcome}",
            requested=schema,
        )


#: IRIS's text for SQLCODE -99: ``User X does not have %CREATE_TABLE privileges``.
_LACKS_PRIVILEGE = re.compile(r"does not have (%[A-Za-z_]+) privilege")


def missing_privilege(exc: BaseException) -> Optional[str]:
    """The SQL privilege an IRIS error (SQLCODE -99, privilege violation) says the user lacks; None for any other
    error."""
    text = str(exc)
    if "SQLCODE: <-99>" not in text and "Privilege violation" not in text:
        return None
    m = _LACKS_PRIVILEGE.search(text)
    return m.group(1) if m else "required for this statement"


def schema_package(name: str) -> str:
    """The class package IRIS compiles a schema's tables and functions into: ``_`` becomes ``.`` (research R2)."""
    return name.replace("_", ".")


def same_schema(a: Optional[str], b: Optional[str]) -> bool:
    """SQL schema names are not case-sensitive in IRIS: ``ivgalt`` and ``IVGALT`` are one schema."""
    return a is not None and b is not None and a.upper() == b.upper()


def validate_schema_name(name: str) -> str:
    """Return ``name`` if it may be an IVG Schema (``Graph_KG`` in its canonical case); raise ``ValueError`` naming
    the rule it breaks otherwise. No database call."""

    def bad(rule: str) -> ValueError:
        return ValueError(f"schema_prefix {name!r} is not a valid IVG Schema name: {rule}")

    if not isinstance(name, str) or name == "":
        raise bad("it is empty")
    if name.startswith("%"):
        raise bad("names starting % are reserved for InterSystems")
    if not _NAME.match(name):
        raise bad("it must start with a letter and hold only letters, digits and underscore")
    if name.upper() == "SQLUSER":
        raise bad("SQLUser is the default schema IVG keeps out of")
    if same_schema(name, DEFAULT_SCHEMA):
        return DEFAULT_SCHEMA
    package = schema_package(name)
    for code in CODE_PACKAGES:
        if package.upper() == code.upper() or package.upper().startswith(code.upper() + "."):
            raise bad(f"its class package {package} is, or is under, IVG's code package {code}")
    return name


def resolve_schema_name(arg: Optional[str], env: Optional[Mapping[str, str]] = None) -> str:
    """The engine's IVG Schema: ``arg`` if given, else ``IVG_SCHEMA_PREFIX`` if set and non-empty, else ``Graph_KG``;
    validated."""
    if env is None:
        env = os.environ
    name = arg if arg is not None else (env.get(ENV_VAR) or DEFAULT_SCHEMA)
    return validate_schema_name(name)


def read_record(native) -> Optional[str]:
    """The namespace's recorded IVG Schema, or None when nothing is recorded. ``native`` is an ``iris.IRIS`` object."""
    value = native.get(*RECORD)
    if value is None or str(value) == "":
        return None
    return str(value)


def write_record(native, name: str, version: str) -> None:
    """Record ``name`` as the namespace's IVG Schema, with when and by which IVG version."""
    native.set(name, *RECORD)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    native.set(f"{stamp} UTC {version}", *RECORD, "recorded")


# --------------------------------------------------------------------------- first access (FR-006, FR-006a)

#: The class `CREATE FUNCTION <schema>.CY_CMP` generates, which every 4.6.0 IVG Schema holds and no 4.5.x one does
#: (4.5.x made CY_CMP in the default schema, class `User.funcCYCMP`): the probe for "this release's SQL objects".
MARKER_FUNCTION_CLASS = "funcCYCMP"


def marker_class(schema: str) -> str:
    return f"{schema_package(schema)}.{MARKER_FUNCTION_CLASS}"


def native_namespace(native) -> str:
    """The namespace a Native API handle works in, for an error message; ``"this namespace"`` when it cannot say."""
    try:
        return str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) or "this namespace"
    except Exception:  # only the wording of a message depends on it
        return "this namespace"


def check_first_access(native, schema: str) -> None:
    """Raise :class:`IVGSchemaError` when an engine on ``schema`` must not touch this namespace: it records another
    IVG Schema (FR-006), or it has no record and lacks this release's objects in ``schema`` (FR-006a). Native API
    only, no SQL: one ``get`` when a record exists, a class probe after it when none does. The probe reads the class
    dictionary's global (``^oddDEF``): ``%Dictionary.ClassDefinition.%ExistsId`` is a %-method, not callable the same
    way from every host (iris_vector_graph/_dictionary.py)."""
    recorded = read_record(native)
    if recorded is not None:
        if not same_schema(recorded, schema):
            raise IVGSchemaError.mismatch(recorded, schema, native_namespace(native))
        return
    # Class names are case-sensitive and schema names are not: the package is the schema as first created, which
    # for a name given in another case is most often its upper-case form.
    if not any(int(native.isDefined("^oddDEF", marker_class(name)) or 0)
               for name in dict.fromkeys((schema, schema.upper()))):
        raise IVGSchemaError.not_upgraded(native_namespace(native))


def is_iris_connection(conn) -> bool:
    """Whether ``conn`` is an IRIS connection (the driver's, or IVG's in-process one), so it has a namespace whose
    record can be read. Anything else (a stand-in, a foreign DB-API object) is not checked: it has no Native API."""
    from iris_vector_graph.embedded import EmbeddedConnection

    raw = unwrap_connection(conn)
    if isinstance(raw, EmbeddedConnection):
        return True
    try:
        import iris
    except ImportError:  # pragma: no cover - the driver is a dependency
        return False
    cls = getattr(iris, "IRISConnection", None)
    return isinstance(cls, type) and isinstance(raw, cls)


class FirstAccessCheck:
    """The engine's first-access check, shared by the engine and its store (both read their connection through it).

    ``pending`` until the check passes; while pending, every read of the connection runs it again, so a refused engine
    stays refused and one whose namespace was upgraded meanwhile goes on. ``suspended()`` lets the calls that must
    work on a refused namespace (``initialize_schema``, ``status``, ``remove_legacy_objects``) reach the database.
    """

    __slots__ = ("_check", "pending", "_busy")

    def __init__(self, check):
        self._check = check
        self.pending = True
        self._busy = False

    def ensure(self) -> None:
        if self._busy:  # the check itself reads the connection (the Native API handle is made from it)
            return
        self._busy = True
        try:
            self._check()
        finally:
            self._busy = False
        self.pending = False

    def satisfied(self) -> None:
        self.pending = False

    @contextlib.contextmanager
    def suspended(self):
        busy, self._busy = self._busy, True
        try:
            yield
        finally:
            self._busy = busy

    def guard_methods(self, obj, exempt=frozenset()) -> None:
        """Run the check at the entry of every public method of ``obj`` (instance attributes shadowing the class's).

        The connection read alone is not enough: many IVG paths catch every exception and answer False or an empty
        result, which would turn the refusal into a silent wrong answer. Installed only on an engine its namespace
        refused; :meth:`release_methods` removes them once the check passes, so an accepted engine pays nothing.
        """
        import functools
        import inspect

        names = []
        for name in dir(type(obj)):
            if name.startswith("_") or name in exempt:
                continue
            raw = inspect.getattr_static(type(obj), name, None)
            if not inspect.isfunction(raw):  # properties, classmethods, staticmethods, data
                continue
            bound = getattr(obj, name)

            def wrapper(*args, __bound=bound, **kwargs):
                if self.pending:
                    self.ensure()
                return __bound(*args, **kwargs)

            setattr(obj, name, functools.wraps(bound)(wrapper))
            names.append(name)
        obj.__dict__["_schema_guarded_methods"] = names

    @staticmethod
    def release_methods(obj) -> None:
        for name in obj.__dict__.pop("_schema_guarded_methods", ()):
            obj.__dict__.pop(name, None)


def guarded_connection(doc: Optional[str] = None) -> property:
    """The ``conn`` property of the engine and its store: each read runs the owner's pending first-access check
    (``_schema_guard``) before returning the connection, which is kept as ``_conn``."""

    def get(obj):
        d = obj.__dict__
        guard = d.get("_schema_guard")
        if guard is not None and guard.pending:
            guard.ensure()
        try:
            return d["_conn"]
        except KeyError:
            raise AttributeError("conn") from None

    def set_(obj, value):
        obj.__dict__["_conn"] = value

    return property(get, set_, doc=doc)


#: What a namespace that refuses an engine still lets it do: the calls that inspect or put the namespace right.
EXEMPT_METHODS = frozenset({"initialize_schema", "status", "remove_legacy_objects", "close"})


# --------------------------------------------------------------------------- the rewrite (research R1)

#: The schema name as a token: ``Graph_KG.rdf_edges``, ``'Graph_KG'``. Not a global (``^Graph_KG``), not part of a
#: longer name (``Graph_KG_x``, ``x.Graph_KG``), and not a code class's ``SqlSchemaName = Graph_KG``: a class in
#: package ``Graph.KG`` declaring another schema maps that schema onto ``Graph.KG``, so setup's DDL in it compiled into
#: ``Graph.KG`` beside the ``Graph_KG`` table classes (4.6.0 R084).
_SCHEMA_TOKEN = re.compile(r"(?<![\w.%^])(?<!SqlSchemaName = )Graph_KG(?!\w)")
#: A table or function class compiled from the schema: ``Graph.KG.rdfedges``, ``Graph.KG.prockgKNNVEC``. IVG's code
#: classes all start upper case (``Graph.KG.Eraser``) and stay where they are; globals (``^Graph.KG.Meta``) too.
_TABLE_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.(?=[a-z])")
#: The schema classes (4.6.0 R084): the shipped classes that project SQL, so they compile into the IVG Schema's own
#: package. ``SqlProcs`` forwards every ``[ SqlProc ]`` to the code in ``Graph.KG``/``IVG``; the other two are the
#: ledger's persistent tables. They ship in ``Graph.KG`` (``Graph_KG``'s package) and the rewrite renames them, with
#: every reference to them, for any other schema: ``IVGALT.SqlProcs``. No package's SQL name is remapped.
SCHEMA_CLASSES = ("SqlProcs", "LedgerRevision", "LedgerStats")
#: The class parameter each schema class declares, so a class of the same name IVG did not make (an application's
#: table ``IVGALT.SqlProcs``) is never taken for one, nor loaded over.
SCHEMA_CLASS_MARKER = "IVGSCHEMACLASS"
#: The schema class that compiles after every other class: an upgraded namespace's code classes project the same SQL
#: names until they recompile without them (ERROR #5083, measured).
FORWARDER_CLASS = "SqlProcs"
_SCHEMA_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.(?=(?:" + "|".join(SCHEMA_CLASSES) + r")(?!\w))")


class SchemaCursor:
    """A DB-API cursor whose statements are rewritten for ``schema`` before they run (:func:`rewrite_schema_text`).

    For IVG's own setup DDL and catalog probes, which are written for ``Graph_KG`` (``GraphSchema``'s static helpers
    take a cursor and nothing else); no caller data is in that text. Everything else delegates.
    """

    def __init__(self, cursor, schema: str):
        self._cursor = cursor
        self._schema = schema

    def execute(self, sql, *args, **kwargs):
        return self._cursor.execute(rewrite_schema_text(sql, self._schema), *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        return self._cursor.executemany(rewrite_schema_text(sql, self._schema), *args, **kwargs)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class SchemaConnection:
    """A connection whose cursors are :class:`SchemaCursor` s; ``_connection`` is the real one, for the Native API."""

    def __init__(self, conn, schema: str):
        self._connection = getattr(conn, "_connection", conn)
        self._conn = conn
        self._schema = schema

    def cursor(self, *args, **kwargs):
        return SchemaCursor(self._conn.cursor(*args, **kwargs), self._schema)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def unwrap_connection(conn):
    """The connection a :class:`SchemaConnection` wraps (the Native API takes only a real one); else ``conn``."""
    return conn._conn if isinstance(conn, SchemaConnection) else conn


def in_schema(conn, schema: str):
    """``conn`` itself for ``Graph_KG``; a :class:`SchemaConnection` for any other IVG Schema."""
    if same_schema(schema, DEFAULT_SCHEMA):
        return conn
    return SchemaConnection(conn, schema)


def _ivg_class_short_names() -> set:
    """The class names of every table IVG creates: the validated table list and every table of the base DDL (which
    holds three more, `rdf_namespaces`, `table_mappings` and `relationship_mappings`, that 4.x made too)."""
    from iris_vector_graph.schema import GraphSchema
    from iris_vector_graph.security import VALID_GRAPH_TABLES

    ddl = re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?Graph_KG\.(\w+)", GraphSchema.get_base_schema_sql())
    return {t.replace("_", "").lower() for t in (*VALID_GRAPH_TABLES, *ddl)}


def foreign_classes(conn, schema: str) -> list:
    """Classes in ``schema``'s package that IVG did not create (research R2): not a table class of IVG's, not a
    function or procedure class its DDL generates, not a routed embedding table, not a schema class the deploy moved
    there (known by its marker parameter, not its name: R084). Empty for ``Graph_KG``, whose package is IVG's own."""
    if same_schema(schema, DEFAULT_SCHEMA):
        return []
    package = schema_package(schema)
    cur = conn.cursor()
    try:
        # Schema names are not case-sensitive: `ivgclash` is the schema whose package IRIS made `IVGCLASH`.
        cur.execute("SELECT Name FROM %Dictionary.ClassDefinition WHERE UPPER(Name) %STARTSWITH ?",
                    [package.upper() + "."])
        names = [str(r[0]) for r in cur.fetchall()]
        marked = set()
        if any(n[len(package) + 1:] in SCHEMA_CLASSES for n in names):
            cur.execute("SELECT parent FROM %Dictionary.ParameterDefinition WHERE Name = ? "
                        "AND UPPER(parent) %STARTSWITH ?", [SCHEMA_CLASS_MARKER, package.upper() + "."])
            marked = {str(r[0]) for r in cur.fetchall()}
    finally:
        cur.close()
    ours = _ivg_class_short_names()
    out = []
    for name in names:
        short = name[len(package) + 1:]
        if short in SCHEMA_CLASSES:
            if name not in marked:
                out.append(name)
        elif "." in short:
            # a class IRIS generates under a table's (an index's, `docs.hCBQuQ`) is the table's
            if short.split(".", 1)[0].lower() not in ours:
                out.append(name)
        elif not (short.lower() in ours or short.startswith(("func", "proc", "kgemb", "kgeemb"))):
            out.append(name)
    return sorted(out)


def refuse_package_clash(conn, schema: str) -> None:
    """Raise :class:`IVGSchemaError` before any DDL when ``schema``'s package already holds someone else's classes."""
    clash = foreign_classes(conn, schema)
    if clash:
        raise IVGSchemaError(
            f"schema_prefix {schema!r} compiles IVG's tables into class package {schema_package(schema)}, which "
            f"already holds classes IVG did not create ({', '.join(clash[:5])}). Choose another IVG Schema name. "
            "Nothing was created."
        )


def runs_in_ivg_schema(method):
    """Run an engine method with ``self.conn`` rewriting ``Graph_KG`` names for the engine's IVG Schema.

    For code whose SQL is IVG's own fixed text (the snapshot's table list, setup) and binds every value it carries."""
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        conn = self.conn
        self.conn = in_schema(conn, getattr(self, "_schema_prefix", DEFAULT_SCHEMA))
        try:
            return method(self, *args, **kwargs)
        finally:
            self.conn = conn

    return wrapper


def rewrite_schema_text(text: str, schema: str) -> str:
    """``text`` (a class source or a SQL statement) with IVG's SQL names moved from ``Graph_KG`` to ``schema``.

    The identity for ``Graph_KG``, so the default schema compiles exactly the shipped sources.
    """
    if same_schema(schema, DEFAULT_SCHEMA):
        return text
    package = schema_package(schema) + "."
    text = _SCHEMA_TOKEN.sub(schema, text)
    text = _SCHEMA_CLASS.sub(package, text)
    return _TABLE_CLASS.sub(package, text)
