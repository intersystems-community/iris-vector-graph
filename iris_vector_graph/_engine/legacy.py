"""The objects 4.5.x left in `SQLUser` and `User`: find them by the frozen manifest, copy their grants to the IVG
Schema's replacements, and remove them when the operator asks (spec 262, FR-010, FR-010a, FR-011, FR-012).

Nothing here matches a name by pattern. An object is IVG's to list, copy or remove only when its exact name is in
:data:`iris_vector_graph.legacy_manifest.LEGACY_MANIFEST`; an application's own `CY_MINE` in SQLUser is never touched.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST

logger = logging.getLogger(__name__)

LEGACY_SCHEMA = "SQLUser"
_IDENT = re.compile(r"[A-Za-z%][A-Za-z0-9_.%]*\Z")


def _rows(conn, sql: str, params=None) -> list:
    cur = conn.cursor()
    try:
        cur.execute(sql, params or [])
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _short(sql_name: str) -> str:
    return sql_name.split(".", 1)[1]


def _derived_class(entry: Dict[str, Any]) -> bool:
    """A function whose class `CREATE FUNCTION` generated (`User.func<NAME>`), as against a shipped class's SqlProc."""
    return entry["class_name"] == "User.func" + _short(entry["sql_name"]).replace("_", "")


def _defined_classes(conn, iris_obj, wanted: List[str]) -> set:
    """Which of ``wanted`` the class dictionary defines. In SQL; for a user refused that SELECT (ADMIN_GUIDE §3's
    reading role, whose ``status()`` reported "schema record probe failed" for it, 4.6.0 R202), through
    ``%Dictionary.ClassDefinition.IDKEYExists`` over the Native API, a method with no ``%`` (iris_vector_graph/
    _dictionary.py)."""
    from iris_vector_graph._dictionary import sql_refused

    marks = ", ".join("?" for _ in wanted)
    try:
        return {str(r[0]) for r in _rows(
            conn, f"SELECT Name FROM %Dictionary.ClassDefinition WHERE Name IN ({marks})", wanted)}
    except Exception as exc:
        if iris_obj is None or not sql_refused(exc):
            raise
        return {name for name in wanted
                if int(str(iris_obj.classMethodValue("%Dictionary.ClassDefinition", "IDKEYExists", name) or 0))}


def present_legacy_objects(conn, iris_obj, with_grants: bool = True) -> List[Dict[str, Any]]:
    """Every manifest entry found in the namespace, with its direct grants (an empty list each when ``with_grants``
    is False: a count needs no grant reads)."""
    routines = {str(r[0]).upper() for r in _rows(
        conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?", [LEGACY_SCHEMA])}
    views = {str(r[0]).upper() for r in _rows(
        conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = ?", [LEGACY_SCHEMA])}
    wanted = sorted({e["class_name"] for e in LEGACY_MANIFEST})
    classes = _defined_classes(conn, iris_obj, wanted)
    found = []
    for entry in LEGACY_MANIFEST:
        if entry["sql_name"]:
            name = _short(entry["sql_name"]).upper()
            present = name in (views if entry["kind"] == "view" else routines)
        else:
            present = entry["class_name"] in classes
        if not present:
            continue
        item = {
            "kind": entry["kind"], "sql_name": entry["sql_name"], "class_name": entry["class_name"],
            "replacement": entry["replacement"], "present": True, "removed": False,
            "class_present": entry["class_name"] in classes,
        }
        item["grants"] = direct_grants(iris_obj, entry["sql_name"]) if entry["sql_name"] and with_grants else []
        found.append(item)
    return found


def grants_on(iris_obj, sql_name: str) -> List[Dict[str, Any]]:
    """Every privilege on `sql_name` a user or role holds, read through `%Library.SQLCatalogPriv:SQLAllPrivileges` (a
    class query; INFORMATION_SCHEMA has no ROUTINE_PRIVILEGES on this build), each with ``via``, how it is held:
    ``Direct``, ``Schema Privilege`` (a grant on the object's schema), ``SuperUser`` or ``Owner Privilege``. A table
    privilege granted on some columns only is a row whose ``privilege`` is ``"None"``.

    Inside IRIS (irispython) ``%New`` is spelled ``_New`` (``%New`` raises SystemError there) and the result set's
    methods are plain attributes; the engine's ``EmbeddedIRIS`` passes method names through literally."""
    from iris_vector_graph._engine.class_deploy import _invoke
    from iris_vector_graph.embedded import EmbeddedIRIS

    new = "_New" if isinstance(iris_obj, EmbeddedIRIS) else "%New"
    rs = iris_obj.classMethodObject("%ResultSet", new, "%Library.SQLCatalogPriv:SQLAllPrivileges")
    _invoke(rs, "Execute", sql_name)
    n = int(_invoke(rs, "GetColumnCount"))
    cols = [str(_invoke(rs, "GetColumnName", i)) for i in range(1, n + 1)]
    out = []
    while int(_invoke(rs, "Next")):
        row = dict(zip(cols, (_invoke(rs, "GetData", i) for i in range(1, n + 1))))
        if str(row.get("OBJECT_NAME")).upper() != sql_name.upper():
            continue
        out.append({"subject": str(row["SUBJECT_NAME"]), "subject_type": str(row["SUBJECT_TYPE"]),
                    "privilege": str(row["PRIVILEGE_NAME"]),
                    "with_grant": str(row.get("HAS_GRANT_OPTION")).lower() in ("yes", "1"),
                    "via": str(row.get("GRANTED_VIA"))})
    return out


def direct_grants(iris_obj, sql_name: str) -> List[Dict[str, Any]]:
    """The privileges granted on `sql_name` directly to a user or role (research R4): :func:`grants_on`'s ``Direct``
    rows. Rows held via SuperUser, Owner Privilege or a schema grant are not grants made on the object."""
    return [{k: g[k] for k in ("subject", "subject_type", "privilege", "with_grant")}
            for g in grants_on(iris_obj, sql_name) if g["via"] == "Direct"]


def copy_grants(conn, items: List[Dict[str, Any]], schema: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Grant each direct privilege on a legacy object again on its replacement in `schema` (FR-010a). A view has no
    replacement (FR-008); its grants are reported with `replacement` empty and not copied."""
    copied = []
    for item in items:
        for g in item["grants"]:
            target = f"{schema}.{item['replacement']}" if item["replacement"] else ""
            record = {"object": item["sql_name"], "replacement": target, "subject": g["subject"],
                      "privilege": g["privilege"], "with_grant": g["with_grant"], "copied": False}
            if target and not dry_run:
                if not (_IDENT.match(g["subject"]) and _IDENT.match(target) and g["privilege"].isalpha()):
                    raise ValueError(f"refusing to copy a grant with an unexpected name: {record}")
                tail = " WITH GRANT OPTION" if g["with_grant"] else ""
                cur = conn.cursor()
                try:
                    cur.execute(f"GRANT {g['privilege']} ON {target} TO {g['subject']}{tail}")
                finally:
                    cur.close()
                record["copied"] = True
            copied.append(record)
    return copied


#: Read-path routines setup never grants by itself, whatever their bodies read (Tom's decision, 2026-11-04, R278).
READ_PATH_EXCLUDED_PREFIXES = ("CY_VLP_",)

#: What a routine's body may name and still read nothing outside the caller's SQL privileges: the dynamic objects and
#: arrays, the regular-expression matcher, exceptions, and dynamic SQL, which IRIS checks for the caller (a refused
#: %Prepare or fetch is SQLCODE -99). The dictionary class answers only whether a method is compiled.
_WITHIN_PRIVILEGES = frozenset({
    "%Library.DynamicArray", "%Library.DynamicObject", "%DynamicArray", "%DynamicObject", "%Regex.Matcher",
    "%Exception.General", "%Exception.StatusException", "%Exception.SQL", "%SQL.Statement",
    "%Dictionary.CompiledMethod",
})
_CLASS_REF = re.compile(r"##class\(([%\w.]+)\)")
_GLOBAL_REF = re.compile(r"\^[%\w(|]")
_DDL_FUNCTION = re.compile(r"CREATE OR REPLACE FUNCTION Graph_KG\.(\w+)\(")
_SQLPROC = re.compile(r"\nClassMethod \w+\([^\n]*\[[^\]\n]*SqlName = (\w+)[^\]\n]*SqlProc[^\]\n]*\]\s*\n\{(.*?)\n\}", re.S)


def _code(text: str) -> str:
    """`text` without its ObjectScript comments (`//` and `///` to the line's end, `;` at a line's start)."""
    lines = (re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())
    return "\n".join(line for line in lines if not line.lstrip().startswith(";"))


def _routine_bodies() -> Dict[str, str]:
    """``{routine: body}`` for every function setup's DDL creates and every SqlProc method ``Graph.KG.SqlProcs``
    projects. A procedure (``CREATE PROCEDURE``, a class query) is not a function and is not here. A SqlProc's body is
    its forwarder followed by the source of each IVG class it names."""
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources
    from iris_vector_graph.schema import GraphSchema

    bodies: Dict[str, str] = {}
    for ddl in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"):
        m = _DDL_FUNCTION.search(ddl)
        if m and "{" in ddl:
            bodies[m.group(1)] = ddl[ddl.index("{"):]
    class_dir = packaged_class_dir()
    if class_dir is not None:
        sources = read_class_sources(class_dir)
        for name, body in _SQLPROC.findall(sources.get("Graph.KG.SqlProcs", "")):
            named = [c for c in _CLASS_REF.findall(body) if c in sources]
            bodies[name] = "\n".join([body] + [sources[c] for c in named])
    return bodies


def _reads_within_privileges(name: str, bodies: Dict[str, str], seen: frozenset = frozenset()) -> bool:
    """Whether routine `name` reads nothing outside the caller's SQL privileges: its code names no global, and every
    class it names is in :data:`_WITHIN_PRIVILEGES` or is the class ``CREATE FUNCTION`` made for another such routine
    (``Graph.KG.funcCYSORTKEY`` for ``CY_SORT_KEY``). A body naming any other class (``Graph.KG.VarLength``) or a
    global (``^KG``) reads around them."""
    body = _code(bodies.get(name, ""))
    if not body or _GLOBAL_REF.search(body):
        return False
    generated = {"Graph.KG.func" + r.replace("_", ""): r for r in bodies}
    for cls in set(_CLASS_REF.findall(body)):
        if cls in _WITHIN_PRIVILEGES or generated.get(cls) == name or re.search(rf"^Class {re.escape(cls)} ", body,
                                                                                 re.M):
            continue  # a class whose source is part of the body (a SqlProc's target) is checked with it
        target = generated.get(cls)
        if target is None or target in seen or not _reads_within_privileges(target, bodies, seen | {name}):
            return False
    return True


def read_path_routines() -> List[str]:
    """The functions Cypher's translation may call that read nothing outside the caller's SQL privileges: each function
    setup creates, and each SqlProc function the IVG Schema's class (``Graph.KG.SqlProcs``) projects, whose body passes
    :func:`_reads_within_privileges`, less :data:`READ_PATH_EXCLUDED_PREFIXES`. Derived from the DDL and the class
    sources, so a routine a later release adds is on it unless it reads a global (4.6.0 R170, R278). The graph
    algorithms, the index searches and the edge scans read ``^KG``, ``^NKG`` or an index global and are not."""
    bodies = _routine_bodies()
    return sorted(n for n in bodies
                  if not n.startswith(READ_PATH_EXCLUDED_PREFIXES) and _reads_within_privileges(n, bodies))


#: 4.6.0 R278 (Tom, 2026-11-04): a grantee holding SELECT on this table, which every property read needs, gets EXECUTE
#: on the read path, as a holder of one read-path routine does (R170).
READ_PATH_SELECT_TABLE = "rdf_props"
#: How a privilege is held when it counts: granted on the object, or on its schema.
_HELD_VIA = ("Direct", "Schema Privilege")


def grant_read_path(conn, iris_obj, schema: str, dry_run: bool,
                    pending: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Give EXECUTE on every read-path routine (:func:`read_path_routines`) in `schema` to each grantee holding EXECUTE
    on one of them (4.6.0 R170) or SELECT on `schema`'s ``rdf_props`` (R278), granted on the object or on the schema,
    and return one record per grant: ``{"routine", "subject", "privilege", "granted", "because"}``; ``because`` is the
    privilege that qualified the grantee (``"EXECUTE on Graph_KG.CY_CMP"``, ``"SELECT on Graph_KG.rdf_props"``).

    Spec 262 copies each 4.5.x grant to its replacement (:func:`copy_grants`), and that alone left users who could run
    Cypher before the upgrade refused after it (-99): 4.6.0's translation also calls routines 4.5.x did not have (since
    R218 every ``RETURN n.prop`` reads through ``CY_TYPED``), which neither a role granted EXECUTE on what its 4.5.x
    queries called nor a user holding SELECT alone may run. The routines read nothing outside the caller's SQL
    privileges, so EXECUTE on them shows no row the grantee could not SELECT. Run on every setup, so a routine a later
    release adds reaches the same grantees; idempotent, as a routine already held (on the object or by a schema grant)
    is not granted again. EXECUTE on routines only, never a table. ``pending`` (the dry run): the legacy items whose
    grants the copy would carry, counted as held. A column-only grant on ``rdf_props`` does not qualify."""
    routines = read_path_routines()
    existing = {str(r[0]) for r in _rows(
        conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?", [schema])}
    present = set(routines) if dry_run else existing  # a dry run's setup would create them all
    if not present & set(routines):
        return []  # nothing to grant, so no grant is read
    held: Dict[str, set] = {}
    because: Dict[str, str] = {}
    for name in routines:
        if name not in existing:
            continue
        for g in grants_on(iris_obj, f"{schema}.{name}"):
            if g["privilege"].upper() == "EXECUTE" and g["via"] in _HELD_VIA:
                held.setdefault(g["subject"], set()).add(name)
                if g["via"] == "Direct":
                    because.setdefault(g["subject"], f"EXECUTE on {schema}.{name}")
    for item in pending or []:
        if item.get("replacement") in routines:
            for g in item["grants"]:
                if g["privilege"].upper() == "EXECUTE":
                    held.setdefault(g["subject"], set()).add(item["replacement"])
                    because.setdefault(g["subject"], f"EXECUTE on {item.get('sql_name') or item['replacement']}")
    table = f"{schema}.{READ_PATH_SELECT_TABLE}"
    for g in grants_on(iris_obj, table):
        if g["privilege"].upper() == "SELECT" and g["via"] in _HELD_VIA:
            held.setdefault(g["subject"], set())
            because.setdefault(g["subject"], f"SELECT on {table}")
    out = []
    for subject in sorted(because):  # a grantee holding the routines through a schema grant alone qualifies for none
        for name in routines:
            if name not in present or name in held[subject]:
                continue
            target = f"{schema}.{name}"
            if not (_IDENT.match(subject) and _IDENT.match(target)):
                raise ValueError(f"refusing to grant EXECUTE on {target} to an unexpected name: {subject!r}")
            if not dry_run:
                cur = conn.cursor()
                try:
                    cur.execute(f"GRANT EXECUTE ON {target} TO {subject}")
                finally:
                    cur.close()
            out.append({"routine": target, "subject": subject, "privilege": "EXECUTE", "granted": not dry_run,
                        "because": because[subject]})
    return out


#: The ledger's persistent tables. In 4.5.x their classes (`Graph.KG.LedgerRevision`, `Graph.KG.LedgerStats`) project
#: into `Graph_KG` whatever the engine's schema_prefix; in 4.6.0 they compile into the IVG Schema's package (R084).
LEDGER_TABLES = ("ledger_revisions", "ledger_stats")
_LEDGER_SOURCE = "Graph_KG"


def _columns(conn, schema: str, table: str) -> List[str]:
    return [str(r[0]) for r in _rows(
        conn, "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
              "ORDER BY ORDINAL_POSITION", [schema, table])]


def _row_count(conn, schema: str, table: str) -> int:
    return int(_rows(conn, f"SELECT COUNT(*) FROM {schema}.{table}")[0][0])


def copy_ledger_history(conn, schema: str, dry_run: bool) -> Dict[str, int]:
    """Copy the 4.5.x ledger tables' rows into the IVG Schema's own, when that schema is not `Graph_KG` and its
    tables are empty (4.6.0 R114): `{table: rows}` for each table copied, or that a run would copy.

    The ledger itself (`^IVG.Ledger`: the head, the records, the statement index) belongs to the namespace and is not
    moved; only the revision and counter rows were left behind in `Graph_KG`, so `history()` answered nothing and
    `verify()` found no row for the head. A table that already holds rows is never added to, so a second run copies
    nothing. The `Graph_KG` rows stay where they are.
    """
    from iris_vector_graph.schema_name import same_schema

    if same_schema(schema, _LEDGER_SOURCE):
        return {}
    copied: Dict[str, int] = {}
    for table in LEDGER_TABLES:
        source = _columns(conn, _LEDGER_SOURCE, table)
        if not source:
            continue
        have = _row_count(conn, _LEDGER_SOURCE, table)
        if not have:
            continue
        target = _columns(conn, schema, table)
        if target and _row_count(conn, schema, table):
            continue
        if dry_run:
            copied[table] = have
            continue
        if not target:
            raise RuntimeError(f"{schema}.{table} does not exist after setup; the ledger history in "
                               f"{_LEDGER_SOURCE}.{table} ({have} rows) was not copied")
        names = [c for c in source if c in target]
        if not all(_IDENT.match(c) for c in names):
            raise ValueError(f"refusing to copy {_LEDGER_SOURCE}.{table}: unexpected column names {names}")
        cols = ", ".join(names)
        cur = conn.cursor()
        try:
            cur.execute(f"INSERT INTO {schema}.{table} ({cols}) SELECT {cols} FROM {_LEDGER_SOURCE}.{table}")
        finally:
            cur.close()
        copied[table] = have
    return copied


def _delete_class(iris_obj, name: str) -> bool:
    status = iris_obj.classMethodValue("%SYSTEM.OBJ", "Delete", name, "-d")
    return bool(int(iris_obj.classMethodValue("%SYSTEM.Status", "IsOK", status)))


def remove_legacy_objects(conn, iris_obj, dry_run: bool = True) -> List[Dict[str, Any]]:
    """List (``dry_run=True``, the default) or remove the manifest objects present in the namespace.

    A view is dropped as a view; a function `CREATE FUNCTION` made is dropped as a function, which removes its
    generated class; a shipped class (`User.CypherFn`, `User.Exec`, the benchmark classes) is deleted as a class, once,
    which removes every SqlProc it projects.
    """
    items = present_legacy_objects(conn, iris_obj)
    if dry_run:
        return items
    classes_done = set()
    for item in items:
        if item["kind"] == "view":
            _drop(conn, f"DROP VIEW {item['sql_name']}")
            item["removed"] = True
        elif item["sql_name"] and _derived_class(item):
            if not _drop(conn, f"DROP FUNCTION {item['sql_name']}"):
                item["removed"] = _delete_class(iris_obj, item["class_name"])
            else:
                item["removed"] = True
        else:
            cls = item["class_name"]
            if cls not in classes_done:
                classes_done.add(cls)
                item["removed"] = _delete_class(iris_obj, cls)
            else:
                item["removed"] = True
    conn.commit()
    return items


def _drop(conn, sql: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return True
    except Exception as exc:  # the caller falls back to deleting the class, and reports what it removed
        logger.info("%s failed (%s); deleting the class instead", sql, exc)
        return False
    finally:
        cur.close()


def legacy_count(conn, iris_obj) -> int:
    return len(present_legacy_objects(conn, iris_obj, with_grants=False))


#: Namespaces whose leftovers notice this process has logged.
_LEFTOVERS_LOGGED: set = set()


def log_leftovers(count: int, namespace: str) -> None:
    """Log :func:`leftovers_message` at INFO, once per namespace per process.

    INFO, not WARNING: the objects are harmless and removing them is optional until 5.0, and 4.6.0 keeps WARNING for
    what an operator must act on. Once: every engine's ``initialize_schema()`` finds the same objects.
    """
    message = leftovers_message(count)
    if message and namespace not in _LEFTOVERS_LOGGED:
        _LEFTOVERS_LOGGED.add(namespace)
        logger.info("IVG setup: %s", message)


def leftovers_message(count: int) -> Optional[str]:
    if not count:
        return None
    return (f"This namespace still holds {count} object(s) IVG 4.5.x created in SQLUser and the User package; IVG no "
            "longer uses them, and SQL that calls them keeps working. engine.remove_legacy_objects() lists them, and "
            "remove_legacy_objects(dry_run=False) removes them.")
