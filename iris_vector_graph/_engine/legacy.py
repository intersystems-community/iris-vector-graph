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


def direct_grants(iris_obj, sql_name: str) -> List[Dict[str, Any]]:
    """The privileges granted on `sql_name` directly to a user or role (research R4), read through
    `%Library.SQLCatalogPriv:SQLAllPrivileges` (a class query; INFORMATION_SCHEMA has no ROUTINE_PRIVILEGES on this
    build). Rows granted via SuperUser or Owner Privilege are not grants anyone made and are left out.

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
        if str(row.get("GRANTED_VIA")) != "Direct" or str(row.get("OBJECT_NAME")).upper() != sql_name.upper():
            continue
        out.append({"subject": str(row["SUBJECT_NAME"]), "subject_type": str(row["SUBJECT_TYPE"]),
                    "privilege": str(row["PRIVILEGE_NAME"]),
                    "with_grant": str(row.get("HAS_GRANT_OPTION")).lower() in ("yes", "1")})
    return out


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


#: Read-path routines setup never grants by itself. ``CY_VLP_ENDS`` walks ``^KG`` directly (Graph.KG.VarLength.Ends),
#: around SQL privileges: measured 2026-11-04, a caller with EXECUTE on it and no SELECT anywhere read a node's
#: neighbour. The variable-length readers stay out until they check privileges.
READ_PATH_EXCLUDED_PREFIXES = ("CY_VLP_",)


def read_path_routines() -> List[str]:
    """The routines the Cypher translation calls to read: every ``CY_*`` function setup creates and every
    ``CypherFn_*`` the IVG Schema's class (``Graph.KG.SqlProcs``) projects, less :data:`READ_PATH_EXCLUDED_PREFIXES`.
    Derived from the routine list and the class source, so a routine a later release adds is on it (4.6.0 R170)."""
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources
    from iris_vector_graph.schema import GraphSchema

    ddl = "\n".join(GraphSchema.get_procedures_sql_list(table_schema="Graph_KG"))
    names = set(re.findall(r"CREATE OR REPLACE FUNCTION Graph_KG\.(CY_\w+)\(", ddl))
    class_dir = packaged_class_dir()
    if class_dir is not None:
        source = read_class_sources(class_dir).get("Graph.KG.SqlProcs", "")
        names |= set(re.findall(r"SqlName = (CypherFn_\w+)", source))
    return sorted(n for n in names if not n.startswith(READ_PATH_EXCLUDED_PREFIXES))


def grant_read_path(conn, iris_obj, schema: str, dry_run: bool,
                    pending: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Give every grantee holding EXECUTE on one read-path routine (:func:`read_path_routines`) EXECUTE on the others
    in `schema`, and return one record per grant: ``{"routine", "subject", "privilege", "granted"}`` (4.6.0 R170).

    Spec 262 copies each 4.5.x grant to its replacement (:func:`copy_grants`), and that alone left a user who could run
    Cypher before the upgrade refused after it: 4.6.0's translation also calls routines 4.5.x did not have (since
    R218 every ``RETURN n.prop`` reads through ``CY_TYPED``). Run on every setup, so a routine a later release adds
    reaches the same grantees; idempotent, as a grant already held is not made again. EXECUTE on routines only, never
    a table. ``pending`` (the dry run): the legacy items whose grants the copy would carry, counted as held."""
    routines = read_path_routines()
    existing = {str(r[0]) for r in _rows(
        conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ?", [schema])}
    present = set(routines) if dry_run else existing  # a dry run's setup would create them all
    held: Dict[str, set] = {}
    for name in routines:
        if name not in existing:
            continue
        for g in direct_grants(iris_obj, f"{schema}.{name}"):
            if g["privilege"].upper() == "EXECUTE":
                held.setdefault(g["subject"], set()).add(name)
    for item in pending or []:
        if item.get("replacement") in routines:
            for g in item["grants"]:
                if g["privilege"].upper() == "EXECUTE":
                    held.setdefault(g["subject"], set()).add(item["replacement"])
    out = []
    for subject in sorted(held):
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
            out.append({"routine": target, "subject": subject, "privilege": "EXECUTE", "granted": not dry_run})
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
