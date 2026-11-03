"""The IVG Schema: the one SQL schema in a namespace that holds every table, function, procedure and view IVG creates
(spec 262, ADR-0016, CONTEXT.md).

Its name is chosen once per namespace (``IRISGraphEngine(schema_prefix=...)``, else ``IVG_SCHEMA_PREFIX``, else
``Graph_KG``), validated before any database call, and recorded in ``^IVG.Deploy("schema")`` by the first
``initialize_schema()``. The ObjectScript classes never read the record: the deploy rewrites their sources for it
(:func:`rewrite_schema_text`), so their embedded SQL stays embedded (research R1).
"""

from __future__ import annotations

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


# --------------------------------------------------------------------------- the rewrite (research R1)

#: The schema name as a token: ``Graph_KG.rdf_edges``, ``'Graph_KG'``, ``SqlSchemaName = Graph_KG``. Not a global
#: (``^Graph_KG``), not part of a longer name (``Graph_KG_x``, ``x.Graph_KG``).
_SCHEMA_TOKEN = re.compile(r"(?<![\w.%^])Graph_KG(?!\w)")
#: A table or function class compiled from the schema: ``Graph.KG.rdfedges``, ``Graph.KG.prockgKNNVEC``. IVG's code
#: classes all start upper case (``Graph.KG.Eraser``) and stay where they are; globals (``^Graph.KG.Meta``) too.
_TABLE_CLASS = re.compile(r"(?<![\w.%^])Graph\.KG\.(?=[a-z])")


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
    from iris_vector_graph.security import VALID_GRAPH_TABLES

    return {t.replace("_", "").lower() for t in VALID_GRAPH_TABLES}


def foreign_classes(conn, schema: str) -> list:
    """Classes in ``schema``'s package that IVG did not create (research R2): not a table class of IVG's, not a
    function or procedure class its DDL generates, not a routed embedding table. Empty for ``Graph_KG``, whose package
    is IVG's own."""
    if same_schema(schema, DEFAULT_SCHEMA):
        return []
    package = schema_package(schema)
    cur = conn.cursor()
    try:
        cur.execute("SELECT Name FROM %Dictionary.ClassDefinition WHERE Name %STARTSWITH ?", [package + "."])
        names = [str(r[0]) for r in cur.fetchall()]
    finally:
        cur.close()
    ours = _ivg_class_short_names()
    out = []
    for name in names:
        short = name[len(package) + 1:]
        if "." in short:
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
    text = _SCHEMA_TOKEN.sub(schema, text)
    return _TABLE_CLASS.sub(schema_package(schema) + ".", text)
