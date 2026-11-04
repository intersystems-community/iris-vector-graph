"""Shared by spec 262's E2E files: scratch namespaces with their own database, what they hold, and a check that a
statement names nothing outside one schema.

The namespaces are IVG262ALT and IVG262UPG on the container `IVG_TEST_CONTAINER` (port `IVG_PORT`), dropped and created
anew by `scripts/dev/make_scratch_namespace.py` (which refuses any other name), so a test starts from nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")

for _p in (REPO / "scripts" / "dev", REPO / "scripts" / "fixtures"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def container_running() -> bool:
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "true"


requires_container = pytest.mark.skipif(not container_running(), reason=f"{CONTAINER} is not running")


def recreate(ns: str) -> None:
    import make_scratch_namespace

    make_scratch_namespace.recreate(ns)


def connect(ns: str):
    import iris

    return iris.connect("localhost", PORT, ns, "_SYSTEM", "SYS")


def connect_as(ns: str, user: str, password: str):
    import iris

    return iris.connect("localhost", PORT, ns, user, password)


def _sys(lines: list) -> str:
    """Run ObjectScript lines in %SYS (Security.* take arguments the Native API cannot pass)."""
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "%SYS"],
                         input=script, capture_output=True, text=True, timeout=120, check=True)
    return out.stdout


def _ours(name: str) -> str:
    if not re.fullmatch(r"ivg262_\w+", name):
        raise ValueError(f"refusing to manage {name!r}: spec 262's tests own only ivg262_* users and roles")
    return name


#: What a role needs to connect over SQL and read IVG262* namespaces' code, with no SQL privilege of its own.
CONNECT_RESOURCES = "%Service_SQL:U,%DB_%DEFAULT:RW,%DB_IRISLIB:R,%DB_IRISSYS:R"


def make_role(role: str, resources: str = CONNECT_RESOURCES) -> None:
    """Create `role` (ivg262_* only) anew holding `resources` and no SQL privilege."""
    out = _sys([f'if ##class(Security.Roles).Exists("{_ours(role)}") do ##class(Security.Roles).Delete("{role}")',
                f'set sc=##class(Security.Roles).Create("{role}","spec 262 test","{resources}") write "SC=",sc,!'])
    if "SC=1" not in out:
        raise RuntimeError(f"role {role}: {out.strip()[-300:]}")


def make_user(user: str, password: str, roles: str, ns: str) -> None:
    """Create `user` (ivg262_* only) anew with `roles` and default namespace `ns`."""
    out = _sys([f'if ##class(Security.Users).Exists("{_ours(user)}") do ##class(Security.Users).Delete("{user}")',
                f'set sc=##class(Security.Users).Create("{user}","{roles}","{password}","spec 262 test","{ns}") '
                'write "SC=",sc,!'])
    if "SC=1" not in out:
        raise RuntimeError(f"user {user}: {out.strip()[-300:]}")


def drop_users_and_roles(*names: str) -> None:
    """Delete each ivg262_* user or role that exists."""
    lines = []
    for n in names:
        _ours(n)
        lines += [f'if ##class(Security.Users).Exists("{n}") do ##class(Security.Users).Delete("{n}")',
                  f'if ##class(Security.Roles).Exists("{n}") do ##class(Security.Roles).Delete("{n}")']
    _sys(lines)


def namespace_snapshot(conn) -> dict:
    """What setup could create or change, read as `_SYSTEM`: schemas, tables, routines, views, classes, IVG's globals."""
    skip = ("%", "INFORMATION_SCHEMA")

    def keep(s):
        return not str(s).startswith(skip)

    nat = native(conn)
    return {
        "schemas": sorted(s for s in schemas(conn) if keep(s)),
        "tables": sorted((str(s), str(t)) for s, t in rows(conn, "SELECT TABLE_SCHEMA, TABLE_NAME FROM "
                                                                  "INFORMATION_SCHEMA.TABLES") if keep(s)),
        "routines": sorted((str(s), str(n)) for s, n in rows(conn, "SELECT ROUTINE_SCHEMA, ROUTINE_NAME FROM "
                                                                   "INFORMATION_SCHEMA.ROUTINES") if keep(s)),
        "classes": sorted(classes(conn)),
        "globals": {g: nat.isDefined(g) for g in ("^IVG.Deploy", "^KG", "^NKG", "^IVG.Ledger", "^IVG.AlgoCache")},
    }


def native(conn):
    import iris

    return iris.createIRIS(conn)


def rows(conn, sql: str, params=None) -> list:
    cur = conn.cursor()
    cur.execute(sql, params or [])
    out = [tuple(r) for r in cur.fetchall()]
    cur.close()
    return out


def routines(conn, schema: str) -> set:
    return {str(r[0]) for r in rows(conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES "
                                          "WHERE UPPER(ROUTINE_SCHEMA) = UPPER(?)", [schema])}


def views(conn, schema: str) -> set:
    return {str(r[0]) for r in rows(conn, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS "
                                          "WHERE UPPER(TABLE_SCHEMA) = UPPER(?)", [schema])}


def schemas(conn) -> set:
    return {str(r[0]) for r in rows(conn, "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA")}


def classes(conn, prefix: str = "") -> set:
    names = {str(r[0]) for r in rows(conn, "SELECT Name FROM %Dictionary.ClassDefinition")}
    return {n for n in names if n.startswith(prefix) and not n.startswith("%")}


#: Every function IVG's SQL may call, by the names its DDL and classes give them.
_IVG_FN = re.compile(
    r"(?<![.\w])(CY_\w+|JSON_VALUE|JSON_ARRAY(?:GET|LENGTH)|JSON_KEYS|LIST_(?:CONCAT|HEAD|LAST|REVERSE|TAIL)|STR_SPLIT"
    r"|REGEX_MATCH|NEWID|RAND|CypherFn_\w+|Percentile_\w+|kg_\w+|ivg_arno_\w+)\s*\("
)
_IVG_TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(nodes|rdf_edges|rdf_labels|rdf_props|kg_\w+)\b(?!\.)")
_QUALIFIED = re.compile(r"(?<![\w.])([A-Za-z_%][\w%]*)\.((?:CY_|CypherFn_|JSON_|LIST_|STR_SPLIT|REGEX_MATCH|NEWID|RAND"
                        r"|Percentile_|kg_|ivg_arno_|nodes\b|rdf_)\w*)")


def names_outside(sql: str, schema: str) -> list:
    """What `sql` names that is IVG's and not in `schema`: unqualified IVG function calls or table reads, and IVG names
    qualified by another schema (`SQLUser.CY_CMP`, `IVG.Percentile_PDISC`)."""
    found = [f"unqualified {m}" for m in _IVG_FN.findall(sql)]
    found += [f"unqualified table {m}" for m in _IVG_TABLE.findall(sql)]
    found += [f"{q}.{n}" for q, n in _QUALIFIED.findall(sql) if q.upper() != schema.upper() and q[:1].isupper()
              and q.upper() in {"SQLUSER", "IVG", "GRAPH_KG", "IRIS_VECTOR_GRAPH"} - {schema.upper()}]
    return found


#: The namespace-wide globals IVG keeps (ADR-0016: they belong to the namespace, not to a schema).
IVG_GLOBALS = ("^IVG.Deploy", "^KG", "^NKG", "^IVG.Ledger", "^IVG.AlgoCache", "^IVG.Eraser")


def global_digest(nat, name: str, depth: int = 12) -> str:
    """sha256 of every valued node under `name` (subscripts and value as text): equal before and after means the
    global did not change."""
    import hashlib

    h = hashlib.sha256()

    def walk(prefix: tuple, remaining: int) -> None:
        if remaining <= 0:
            return
        current = nat.nextSubscript(False, name, *prefix, "")
        while current is not None and current != "":
            subs = prefix + (current,)
            if int(nat.isDefined(name, *subs)) in (1, 11):
                h.update(repr((subs, nat.getString(name, *subs))).encode())
            walk(subs, remaining - 1)
            current = nat.nextSubscript(False, name, *prefix, current)

    if int(nat.isDefined(name)) in (1, 11):
        h.update(repr(((), nat.getString(name))).encode())
    walk((), depth)
    return h.hexdigest()


def deep_snapshot(conn) -> dict:
    """`namespace_snapshot` plus every base table's row count outside the system schemas and a digest of each of
    IVG's globals: what "no row or global changed" is compared on."""
    snap = namespace_snapshot(conn)
    counts = {}
    for schema, table in snap["tables"]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", schema) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", table):
            continue
        try:
            counts[(schema, table)] = rows(conn, f'SELECT COUNT(*) FROM "{schema}"."{table}"')[0][0]
        except Exception as exc:  # a view the catalog lists among tables, or a table without SELECT: say which
            counts[(schema, table)] = f"unreadable: {type(exc).__name__}"
    nat = native(conn)
    snap["rows"] = counts
    snap["global_digests"] = {g: global_digest(nat, g) for g in IVG_GLOBALS}
    return snap


def subtree_digest(nat, name: str, *subs, depth: int = 12) -> str:
    """sha256 of every valued node under `name(*subs)`: a class's ^oddDEF entry, compared before and after."""
    import hashlib

    h = hashlib.sha256()

    def walk(prefix: tuple, remaining: int) -> None:
        if remaining <= 0:
            return
        current = nat.nextSubscript(False, name, *prefix, "")
        while current is not None and current != "":
            node = prefix + (current,)
            if int(nat.isDefined(name, *node)) in (1, 11):
                h.update(repr((node, nat.getString(name, *node))).encode())
            walk(node, remaining - 1)
            current = nat.nextSubscript(False, name, *prefix, current)

    walk(tuple(subs), depth)
    return h.hexdigest()
