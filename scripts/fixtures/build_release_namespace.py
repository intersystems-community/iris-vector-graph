#!/usr/bin/env python3
"""Build a namespace the way an old IVG release left it: that release's own code, run against a scratch namespace.

    python scripts/fixtures/build_release_namespace.py v4.5.1 IVG262UPG
    python scripts/fixtures/build_release_namespace.py v3.2.0 IVG262UPG
    python scripts/fixtures/build_release_namespace.py v4.5.1 IVG262UPG --keep     (upgrade in place: no wipe, no seed)

`run_ledger_commits(tag, ns, schema_prefix)` then enables the ledger and commits two changesets through that release's
own engine, for an upgrade test that needs ledger history (4.6.0 R114).

An upgrade test is only as good as its starting point, and the only exact source of what a release created is the
release (TESTING_POLICY: legacy installs are built from frozen DDL or real old releases). So this checks `<tag>` out into
`~/.cache/ivg-bench/releases/<tag>` (a git worktree, made once), and runs that tag's own `initialize_schema()` in `<NS>`
with the worktree first on PYTHONPATH, then the fixed seed of `generate_old_snapshot.py`. A release whose
`initialize_schema()` cannot send its classes over the connection (3.2.0 loads them from a server directory) gets them
copied into the container and loaded from there.

Without `--keep` the namespace is dropped and created anew first (IVG262* only), so nothing of an earlier run or of
the current checkout is in it. The last line printed is a JSON summary of what the release left in `SQLUser`, the `User`
package, the top-level package and each schema's tables; `inventory()` is the same probe for callers.

Container and port: IVG_TEST_CONTAINER (ivg-iris-enterprise) and IVG_PORT (31972).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RELEASES = Path.home() / ".cache" / "ivg-bench" / "releases"
PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
#: The seed's vector width (generate_old_snapshot.EMBEDDING_DIM).
EMBEDDING_DIM = 8


def release_worktree(tag: str) -> Path:
    """`~/.cache/ivg-bench/releases/<tag>`, a detached worktree of `tag`, created on first use."""
    path = RELEASES / tag
    if not (path / "iris_vector_graph" / "__init__.py").is_file():
        RELEASES.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(path), tag], check=True)
    return path


def _rows(cursor, sql: str, params=None) -> list:
    cursor.execute(sql, params or [])
    return [tuple(r) for r in cursor.fetchall()]


def inventory(conn) -> dict:
    """What the namespace holds outside IVG's code packages, where an operator would trip over it.

    `routines` and `views`: every SQL function, procedure and view in `SQLUser`. `classes`: every class in the `User`
    package or in no package. `tables`: base-table names per schema (system schemas left out).
    """
    cur = conn.cursor()
    routines = sorted(
        (str(t), str(n))
        for t, n in _rows(
            cur,
            "SELECT ROUTINE_TYPE, ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'SQLUser'",
        )
    )
    views = sorted(
        str(r[0]) for r in _rows(cur, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = 'SQLUser'")
    )
    names = [str(r[0]) for r in _rows(cur, "SELECT Name FROM %Dictionary.ClassDefinition")]
    classes = sorted(n for n in names if not n.startswith("%") and (n.startswith("User.") or "." not in n))
    tables: dict = {}
    for schema, table in _rows(
        cur, "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'"
    ):
        schema = str(schema)
        if schema.startswith(("%", "Ens", "HS_", "INFORMATION_SCHEMA")):
            continue
        tables.setdefault(schema, []).append(str(table))
    return {
        "routines": [list(r) for r in routines],
        "views": views,
        "classes": classes,
        "tables": {k: sorted(v) for k, v in sorted(tables.items())},
    }


#: Starts the line a child's result is on. An old release's setup writes to this process's stdout too (v4.0.0 echoes its
#: class loads, and its last write has no newline), so the result is found by its mark, not by being the last line.
RESULT_MARK = "IVG-BUILD-RESULT "


def emit_result(obj) -> None:
    """Print `obj` as the child's result: on a line of its own, after whatever the release printed."""
    print("\n" + RESULT_MARK + json.dumps(obj), flush=True)


def parse_result(stdout: str):
    """The last result `emit_result` printed into `stdout`."""
    found = [line[len(RESULT_MARK):] for line in stdout.split("\n") if line.startswith(RESULT_MARK)]
    if not found:
        raise RuntimeError(f"no result line ({RESULT_MARK!r}) in the child's output: {stdout[-1500:]}")
    return json.loads(found[-1])


#: Where releases up to 4.4 look for their classes on the server before anything else.
SERVER_SRC = "/tmp/src/"


def redirect_server_src(schema_module, stage: str) -> None:
    """Point the release's own `LoadDir` (and the stale-file deletes around it) at `stage`, the tag's classes, instead
    of SERVER_SRC: on the shared test container that directory holds whatever a test run staged there last (another
    checkout's classes, beside files no release shipped together), so a "v4.0.0" namespace got those (4.6.0 R141).
    Only string arguments that start with SERVER_SRC change."""
    call = schema_module._call_classmethod
    staged = stage.rstrip("/") + "/"

    def _redirect(arg):
        if isinstance(arg, str) and arg.startswith(SERVER_SRC):
            return staged + arg[len(SERVER_SRC):]
        return arg

    def _call(conn_or_cursor, class_name, method_name, *args):
        return call(conn_or_cursor, class_name, method_name, *(_redirect(a) for a in args))

    schema_module._call_classmethod = _call


def _child(tag: str, ns: str, seed: bool, migrate: bool = False) -> int:
    """Runs under the old release (PYTHONPATH on its worktree): its initialize_schema, then the seed. With
    `migrate`, then its `upgrade_to_4_0_0(conn)`, the second half of that release's upgrade of a 3.x namespace."""
    import iris
    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine

    here = Path(iris_vector_graph.__file__).resolve()
    if RELEASES.resolve() not in here.parents:
        print(f"refusing: iris_vector_graph resolved to {here}, not the {tag} worktree", file=sys.stderr)
        return 2

    import iris_vector_graph.schema as release_schema

    if hasattr(release_schema, "_call_classmethod"):
        redirect_server_src(release_schema, _stage_release_src(here.parents[1] / "iris_src" / "src", tag))
    conn = iris.connect("localhost", PORT, ns, "_SYSTEM", "SYS")
    # IVG_BUILD_SCHEMA_PREFIX: the release's own schema_prefix (its tables there; 4.5.x put its functions in SQLUser)
    prefix = os.environ.get("IVG_BUILD_SCHEMA_PREFIX") or "Graph_KG"
    engine = IRISGraphEngine(conn, embedding_dimension=EMBEDDING_DIM, namespace=ns, schema_prefix=prefix)
    engine.initialize_schema()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE ID = 'Graph.KG.PageRank'")
    if not int(cur.fetchone()[0]):
        _load_from_container(here.parents[1] / "iris_src" / "src", ns, tag)
        engine.initialize_schema()
    if seed:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import generate_old_snapshot
        from generate_old_snapshot import _seed

        if os.environ.get("IVG_BUILD_NAMED_EDGES") == "0":
            # the seed without its `acme` edge: a 3.x release wrote it with no node row in acme, which a 4.x
            # release's own upgrade_to_4_0_0 cannot re-key before 4.6.0 (R076)
            generate_old_snapshot.NAMED_GRAPH_EDGES = []
        # Through a Graph_KG engine: 4.x's ObjectScript (the graph registration create_edge does) writes Graph_KG
        # whatever the prefix, so its API cannot seed another schema; the rows are copied there below.
        seeder = engine if prefix == "Graph_KG" else IRISGraphEngine(conn, embedding_dimension=EMBEDDING_DIM,
                                                                     namespace=ns)
        written = _seed(seeder)
        conn.commit()
        print(f"seeded: {', '.join(f'{k}={len(v)}' for k, v in written.items())}", file=sys.stderr)
    if prefix != "Graph_KG":
        _tables_in_prefix(conn, prefix)
        _copy_rows(conn, "Graph_KG", prefix)
        if os.environ.get("IVG_BUILD_ONLY_PREFIX") == "1":
            _drop_schema_tables(conn, "Graph_KG")
    if migrate:
        from iris_vector_graph.migrations import upgrade_to_4_0_0

        report = upgrade_to_4_0_0(conn)
        blocked = [f"{s.name}: {s.blocked_because}" for s in report.steps if s.blocked]
        if blocked:
            print(f"upgrade_to_4_0_0 blocked: {blocked}", file=sys.stderr)
            return 3
        print(f"upgrade_to_4_0_0: {[s.name for s in report.steps]}", file=sys.stderr)
    emit_result({"tag": tag, "namespace": ns, "package": str(here.parent), **inventory(conn)})
    conn.close()
    return 0


def _tables_in_prefix(conn, prefix: str) -> None:
    """4.x's `initialize_schema()` creates its tables in `Graph_KG` whatever the engine's `schema_prefix`, which only
    its queries follow: a consumer on another prefix (hipporag2-pipeline's `RAG`) ran the release's own base DDL in
    that schema. This does the same, with the release's DDL text, so its seed has tables to write."""
    import re

    from iris_vector_graph.schema import GraphSchema
    from iris_vector_graph.utils import _split_sql_statements

    sql = re.sub(r"\bGraph_KG\b", prefix, GraphSchema.get_base_schema_sql(embedding_dimension=EMBEDDING_DIM))
    cur = conn.cursor()
    failed = []
    for stmt in _split_sql_statements(sql):
        if not stmt.strip():
            continue
        try:
            cur.execute(stmt)
        except Exception as exc:  # the release's own setup tolerates the same: reported, and the seed then shows it
            failed.append(f"{stmt.strip()[:60]}: {str(exc)[:120]}")
    conn.commit()
    cur.close()
    print(f"{prefix} base DDL: {len(failed)} statement(s) failed: {failed[:5]}", file=sys.stderr)


#: Parents first: the foreign keys point from labels, props and edges at nodes.
_COPY_ORDER = ("nodes", "rdf_labels", "rdf_props", "rdf_edges")


def _copy_rows(conn, source: str, target: str) -> None:
    """Copy the graph tables' rows from `source` to `target` (columns both have, identity columns left out)."""
    cur = conn.cursor()
    copied = {}
    for table in _COPY_ORDER:
        cur.execute("SELECT COLUMN_NAME, IS_IDENTITY FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? "
                    "AND TABLE_NAME = ?", [source, table])
        src = {str(c): str(i) for c, i in cur.fetchall()}
        cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                    [target, table])
        cols = [c for c in (str(r[0]) for r in cur.fetchall()) if c in src and src[c] != "YES"]
        if not cols:
            continue
        names = ", ".join(f'"{c}"' for c in cols)
        cur.execute(f"INSERT INTO {target}.{table} ({names}) SELECT {names} FROM {source}.{table}")
        copied[table] = cur.rowcount
    conn.commit()
    cur.close()
    print(f"copied {source} -> {target}: {copied}", file=sys.stderr)


def _drop_schema_tables(conn, schema: str) -> None:
    """Drop every base table in `schema`, children before parents (up to ten passes). A table still there after the
    last pass raises the last DROP error."""
    cur = conn.cursor()
    # 4.x's SQLUser views read Graph_KG's tables, and IRIS refuses to drop a table a view reads (SQLCODE -321)
    for view in ("nodes", "rdf_edges", "rdf_labels", "rdf_props"):
        cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = 'SQLUser' AND TABLE_NAME = ?",
                    [view])
        if int(cur.fetchone()[0]):
            cur.execute(f"DROP VIEW SQLUser.{view}")

    def base_tables() -> list[str]:
        cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'",
                    [schema])
        return [str(r[0]) for r in cur.fetchall()]

    last_error: Exception | None = None
    for _ in range(10):
        names = base_tables()
        if not names:
            break
        for name in names:
            try:
                cur.execute(f"DROP TABLE {schema}.{name}")
            except Exception as exc:  # still referenced: the next pass, after its children are gone
                last_error = exc
    else:
        left = base_tables()
        if left:
            cur.close()
            if last_error is not None:
                raise last_error
            raise RuntimeError(f"{schema} tables still present after 10 drop passes: {left}")
    conn.commit()
    cur.close()


def _stage_release_src(src: Path, tag: str) -> str:
    """A fresh copy of a release's classes in the container (`/tmp/ivg262_src_<tag>`); its path."""
    stage = f"/tmp/ivg262_src_{tag.replace('.', '_')}"
    # As root: `docker cp` leaves the stage owned by root, so irisowner cannot clear an earlier run's copy.
    subprocess.run(["docker", "exec", "-u", "root", CONTAINER, "rm", "-rf", stage], check=True)
    subprocess.run(["docker", "cp", f"{src}/.", f"{CONTAINER}:{stage}"], check=True)
    return stage


def _load_from_container(src: Path, ns: str, tag: str) -> None:
    """Copy a release's classes into the container and LoadDir them: what 3.2.0's own deploy expected done."""
    stage = _stage_release_src(src, tag)
    script = f'set sc=$system.OBJ.LoadDir("{stage}","ck-d",,1) write "LOADED ",$system.Status.IsOK(sc),!\nhalt\n'
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", ns],
        input=script, capture_output=True, text=True, timeout=900, check=True,
    ).stdout
    print(f"LoadDir {tag}: {out.strip().splitlines()[-2:] if out.strip() else out}", file=sys.stderr)


def _cypher_child(tag: str, ns: str, user: str, password: str, query: str) -> int:
    """Runs under the old release: one Cypher query through its own engine, connected as `user`."""
    import iris
    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine

    here = Path(iris_vector_graph.__file__).resolve()
    if RELEASES.resolve() not in here.parents:
        print(f"refusing: iris_vector_graph resolved to {here}, not the {tag} worktree", file=sys.stderr)
        return 2
    conn = iris.connect("localhost", PORT, ns, user, password)
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=EMBEDDING_DIM, namespace=ns)
        try:
            r = engine.execute_cypher(query, {}, raise_on_error=True)
            out = {"rows": [[None if v is None else str(v) for v in row] for row in r.rows], "sql": r.sql or "",
                   "error": None}
        except Exception as exc:  # reported to the caller, which asserts on it
            out = {"rows": None, "sql": "", "error": str(exc)}
    finally:
        conn.close()
    emit_result(out)
    return 0


#: The two changesets `run_ledger_commits` commits after the genesis, by hand: the first creates r114:a and r114:b
#: (label R114) and r114:a -R114-> r114:b, the second sets r114:a's property `step` to "2".
LEDGER_NODES = ("r114:a", "r114:b")


def _ledger_child(tag: str, ns: str) -> int:
    """Runs under the old release: enable its ledger on an engine of IVG_BUILD_SCHEMA_PREFIX, commit the two
    changesets, and print the revisions its own ledger table holds as `[[seq, kind, op_count], ...]`."""
    import iris
    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.ledger import Changeset

    here = Path(iris_vector_graph.__file__).resolve()
    if RELEASES.resolve() not in here.parents:
        print(f"refusing: iris_vector_graph resolved to {here}, not the {tag} worktree", file=sys.stderr)
        return 2
    prefix = os.environ.get("IVG_BUILD_SCHEMA_PREFIX") or "Graph_KG"
    conn = iris.connect("localhost", PORT, ns, "_SYSTEM", "SYS")
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=EMBEDDING_DIM, namespace=ns, schema_prefix=prefix)
        engine.ledger.enable()
        first = Changeset(actor="r114", actor_type="system", message="two nodes and a relationship")
        for nid in LEDGER_NODES:
            first.create_node(nid, labels=["R114"])
        first.create_relationship(LEDGER_NODES[0], "R114", LEDGER_NODES[1])
        engine.ledger.commit(first)
        second = Changeset(actor="r114", actor_type="system", message="a property")
        second.set_property(LEDGER_NODES[0], "step", "2")
        engine.ledger.commit(second)
        cur = conn.cursor()
        # 4.x's ledger classes project into Graph_KG whatever the engine's prefix
        cur.execute("SELECT seq, kind, op_count FROM Graph_KG.ledger_revisions ORDER BY seq")
        revisions = [[int(r[0]), str(r[1]), int(r[2] or 0)] for r in cur.fetchall()]
    finally:
        conn.close()
    emit_result({"revisions": revisions})
    return 0


def run_ledger_commits(tag: str, ns: str, schema_prefix: str = "Graph_KG") -> dict:
    """Enable `tag`'s ledger on `ns` through an engine of `schema_prefix` and commit the two changesets above:
    `{"revisions": [[seq, kind, op_count], ...]}` as that release's ledger table holds them."""
    tree = release_worktree(tag)
    env = dict(os.environ, PYTHONPATH=str(tree), IVG_BUILD_SCHEMA_PREFIX=schema_prefix)
    args = [sys.executable, str(Path(__file__).resolve()), tag, ns, "--ledger"]
    out = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600, cwd=str(tree))
    if out.returncode != 0:
        raise RuntimeError(f"{tag} ledger commits in {ns} failed ({out.returncode}):\n{out.stderr[-3000:]}")
    return parse_result(out.stdout)


def run_cypher(tag: str, ns: str, query: str, user: str = "_SYSTEM", password: str = "SYS") -> dict:
    """Run `query` through `tag`'s own engine on `ns`, connected as `user`: `{"rows", "sql", "error"}` (rows as text).

    For a check that the SQL an old release sends still runs, under a user's own privileges, before an upgrade."""
    tree = release_worktree(tag)
    env = dict(os.environ, PYTHONPATH=str(tree), IVG_RUN_CYPHER_USER=user, IVG_RUN_CYPHER_PASSWORD=password,
               IVG_RUN_CYPHER_QUERY=query, IVG_NATIVE_CYPHER="0")
    args = [sys.executable, str(Path(__file__).resolve()), tag, ns, "--cypher"]
    out = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600, cwd=str(tree))
    if out.returncode != 0:
        raise RuntimeError(f"{tag} Cypher in {ns} failed ({out.returncode}):\n{out.stderr[-3000:]}")
    return parse_result(out.stdout)


def build(tag: str, ns: str, keep: bool = False, seed: bool = True, migrate: bool = False,
          schema_prefix: str = "Graph_KG", only_prefix: bool = False, named_edges: bool = True) -> dict:
    """Build `ns` at `tag` and return the inventory the old release left (the child's JSON line). `migrate` also runs
    the tag's `upgrade_to_4_0_0` (a 4.x tag upgrading a 3.x namespace kept with `keep`). `schema_prefix` is the
    release engine's own: its queries go there, and so do its base tables, created with the release's DDL text the
    way a consumer on that prefix had to (`_tables_in_prefix`); the release's own setup also made `Graph_KG`'s, which
    `only_prefix` drops, for a namespace whose IVG tables are in that one schema alone. With `keep`, a second build
    adds a second schema's tables. `named_edges=False` seeds without the `acme` edge (see `_child`)."""
    if not ns.startswith("IVG262") or ns.upper() != ns:
        raise ValueError(f"refusing {ns!r}: only upper-case IVG262* scratch namespaces")
    tree = release_worktree(tag)
    if not keep:
        sys.path.insert(0, str(REPO / "scripts" / "dev"))
        import make_scratch_namespace

        make_scratch_namespace.recreate(ns)
    env = dict(os.environ, PYTHONPATH=str(tree), IVG_FORCE_CLASS_DEPLOY="1", IVG_BUILD_SCHEMA_PREFIX=schema_prefix,
               IVG_BUILD_ONLY_PREFIX="1" if only_prefix else "0", IVG_BUILD_NAMED_EDGES="1" if named_edges else "0")
    args = [sys.executable, str(Path(__file__).resolve()), tag, ns, "--child"]
    args += ([] if seed else ["--no-seed"]) + (["--migrate"] if migrate else [])
    out = subprocess.run(args, env=env, capture_output=True, text=True, timeout=1800, cwd=str(tree))
    if out.returncode != 0:
        raise RuntimeError(f"{tag} in {ns} failed ({out.returncode}):\n{out.stderr[-3000:]}")
    return parse_result(out.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tag")
    ap.add_argument("namespace")
    ap.add_argument("--keep", action="store_true", help="upgrade in place: no wipe, no seed")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--migrate", action="store_true", help="also run the tag's upgrade_to_4_0_0")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--cypher", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ledger", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.ledger:
        return _ledger_child(a.tag, a.namespace)
    if a.cypher:
        return _cypher_child(a.tag, a.namespace, os.environ["IVG_RUN_CYPHER_USER"],
                             os.environ["IVG_RUN_CYPHER_PASSWORD"], os.environ["IVG_RUN_CYPHER_QUERY"])
    if a.child:
        return _child(a.tag, a.namespace, seed=not a.no_seed, migrate=a.migrate)
    result = build(a.tag, a.namespace, keep=a.keep, seed=not (a.no_seed or a.keep), migrate=a.migrate)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
