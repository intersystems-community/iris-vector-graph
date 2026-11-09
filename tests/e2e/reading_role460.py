"""Shared by the 4.6.0 files that log in as a user holding ADMIN_GUIDE §3's roles (R202, R203, R206): a scratch
namespace set up with the default IVG Schema, and users made for one test and deleted after it.

The namespace is `IVG_NAMESPACE` on `IVG_TEST_CONTAINER` (port `IVG_PORT`): an IVG460*/IVG262* scratch namespace with a
database of its own, set up by `scripts/dev/make_scratch_namespace.py` (IVG Schema `Graph_KG`). The files erase its
default graph and write their own, so they never run against a namespace another suite reads.

Roles and users are instance-wide (they live in %SYS) and named `FIXES24_<tag>_ROLE` / `FIXES24_<tag>_USER`. The
database resource is looked up, never assumed: the compose image's `USER` has `%DB_USER`, a database made without one
has `%DB_%DEFAULT` (walkthrough 1 P5).
"""

from __future__ import annotations

import os
import subprocess

import pytest

PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
NS = os.environ.get("IVG_NAMESPACE", "")
SCHEMA = "Graph_KG"
PREFIX = "FIXES24"
PASSWORD = "Fixes24reader"
#: The width the scratch namespace was set up with (make_scratch_namespace.py).
DIM = 768


def _usable_namespace() -> bool:
    return NS.upper() == NS and NS.startswith(("IVG460", "IVG262"))


def container_running() -> bool:
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "true"


requires_namespace = [
    pytest.mark.skipif(not _usable_namespace(),
                       reason="IVG_NAMESPACE must name an IVG460*/IVG262* scratch namespace set up with Graph_KG"),
    pytest.mark.skipif(not container_running(), reason=f"{CONTAINER} is not running"),
]


def session(lines: list, namespace: str = "%SYS") -> str:
    script = "\n".join(lines + ["halt"]) + "\n"
    out = subprocess.run(["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", namespace],
                         input=script, capture_output=True, text=True, timeout=180)
    return out.stdout + out.stderr


def marked(out: str, tag: str) -> str:
    start, end = f"<<{tag}:", f":{tag}>>"
    assert start in out and end in out, out[-800:]
    return out.split(start, 1)[1].split(end, 1)[0]


def database_resource(namespace: str = "", which: str = "Globals") -> str:
    """The resource of the namespace's globals database (`which="Routines"`: its routines database), as an operator
    would look it up."""
    ns = namespace or NS
    out = session([
        f'do ##class(Config.Namespaces).Get("{ns}",.p) do ##class(Config.Databases).Get(p("{which}"),.d)',
        'set db=##class(SYS.Database).%OpenId(d("Directory")) write "<<RES:",db.ResourceName,":RES>>",!'])
    return marked(out, "RES")


def routines_resource(namespace: str = "") -> str:
    """The resource of the namespace's routines database: the globals database's own where the two are one."""
    return database_resource(namespace, "Routines")


def resources(*grants: str) -> str:
    """`grants` comma-joined, a resource named twice kept once, at its first privilege (on a namespace whose globals
    and routines share a database, both of ADMIN_GUIDE §3's database resources are the same one)."""
    seen: dict = {}
    for grant in grants:
        name, _, privilege = grant.partition(":")
        seen.setdefault(name, privilege)
    return ",".join(f"{name}:{privilege}" for name, privilege in seen.items())


def reading_resources(database: str = "R", global_access: bool = True, namespace: str = "") -> str:
    """ADMIN_GUIDE §3's reading role (database `R`) or its 'reading, fully' row (`RW`): the routines database at `R`
    in both (R050)."""
    grants = [f"{database_resource(namespace)}:{database}", f"{routines_resource(namespace)}:R",
              "%Native_ClassExecution:U"]
    return resources(*grants, *(["%Native_GlobalAccess:U"] if global_access else []))


def names(tag: str, prefix: str = PREFIX) -> tuple:
    return f"{prefix}_{tag}_ROLE", f"{prefix}_{tag}_USER"


def connect(user: str = "_SYSTEM", password: str = "SYS", namespace: str = ""):
    import iris

    return iris.connect("localhost", PORT, namespace or NS, user, password)


def make_reader(tag: str, resources: str, sql_privileges: tuple = ("SELECT", "EXECUTE"), prefix: str = PREFIX,
                namespace: str = "") -> str:
    """A role holding `resources` and, on the IVG Schema, `sql_privileges`, and a user holding the role; returns the
    user name. One left over from an earlier run is dropped first. `prefix` names them (FIXES24 by default);
    `namespace` (IVG_NAMESPACE by default) is where the grants are made and the user's default namespace."""
    drop_reader(tag, prefix, namespace)
    role, user = names(tag, prefix)
    out = session([
        f'set sc=##class(Security.Roles).Create("{role}","4.6.0 reading-role test","{resources}")',
        'write "<<ROLE:",$system.Status.GetErrorText(sc),":ROLE>>",!',
        f'set sc=##class(Security.Users).Create("{user}","{role}","{PASSWORD}","4.6.0 reading-role test",'
        f'"{namespace or NS}")',
        'write "<<USER:",$system.Status.GetErrorText(sc),":USER>>",!'])
    assert marked(out, "ROLE") == "" and marked(out, "USER") == "", out
    conn = connect(namespace=namespace)
    try:
        cur = conn.cursor()
        for priv in sql_privileges:
            cur.execute(f"GRANT {priv} ON SCHEMA {SCHEMA} TO {role}")
        conn.commit()
    finally:
        conn.close()
    return user


def drop_reader(tag: str, prefix: str = PREFIX, namespace: str = "") -> None:
    role, user = names(tag, prefix)
    conn = connect(namespace=namespace)
    try:
        cur = conn.cursor()
        for priv in ("SELECT", "EXECUTE", "INSERT", "UPDATE", "DELETE"):
            try:
                cur.execute(f"REVOKE {priv} ON SCHEMA {SCHEMA} FROM {role}")
            except Exception as exc:  # the role does not exist, or never held it
                assert "-" in str(exc), exc
        conn.commit()
    finally:
        conn.close()
    session([f'if ##class(Security.Users).Exists("{user}") do ##class(Security.Users).Delete("{user}")',
             f'if ##class(Security.Roles).Exists("{role}") do ##class(Security.Roles).Delete("{role}")'])


def seed(engine) -> None:
    """The default graph a -> b -> c -> d (KNOWS), each node labelled P with a name; nothing else in it.

    Every graph is erased first: a named graph another module left with edges (test_460_r192's r192g) makes
    betweenness skip its ObjectScript tier, whose WARNING at R test_460_r202 reads (the 4.6.4 gate, b5b6dabf)."""
    engine.erase_all()
    for n in "abcd":
        assert engine.create_node(n, labels=["P"], properties={"name": n.upper()}) is True
    for s, o in (("a", "b"), ("b", "c"), ("c", "d")):
        assert engine.create_edge(s, "KNOWS", o) is True


def state(conn) -> dict:
    """Row counts of the tables a write reaches, the qualifiers of a -> b, and the temporal and queue globals."""
    import iris

    cur = conn.cursor()
    out = {}
    for t in ("nodes", "rdf_edges", "rdf_labels", "rdf_props", "rdf_reifications", "kg_NodeEmbeddings"):
        cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.{t}")
        out[t] = int(cur.fetchone()[0])
    cur.execute(f"SELECT qualifiers FROM {SCHEMA}.rdf_edges WHERE s = 'a' AND p = 'KNOWS' AND o_id = 'b'")
    out["ab_qualifiers"] = [r[0] for r in cur.fetchall()]
    nat = iris.createIRIS(conn)
    out["tout"] = int(nat.isDefined("^KG", "tout") or 0)
    out["EmbedQueue"] = int(nat.isDefined("^EmbedQueue") or 0)
    return out
