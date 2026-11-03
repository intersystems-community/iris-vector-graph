#!/usr/bin/env python3
"""Build a namespace the way an old IVG release left it: that release's own code, run against a scratch namespace.

    python scripts/fixtures/build_release_namespace.py v4.5.1 IVG262UPG
    python scripts/fixtures/build_release_namespace.py v3.2.0 IVG262UPG
    python scripts/fixtures/build_release_namespace.py v4.5.1 IVG262UPG --keep     (upgrade in place: no wipe, no seed)

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


def _child(tag: str, ns: str, seed: bool) -> int:
    """Runs under the old release (PYTHONPATH on its worktree): its initialize_schema, then the seed."""
    import iris
    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine

    here = Path(iris_vector_graph.__file__).resolve()
    if RELEASES.resolve() not in here.parents:
        print(f"refusing: iris_vector_graph resolved to {here}, not the {tag} worktree", file=sys.stderr)
        return 2

    conn = iris.connect("localhost", PORT, ns, "_SYSTEM", "SYS")
    engine = IRISGraphEngine(conn, embedding_dimension=EMBEDDING_DIM, namespace=ns)
    engine.initialize_schema()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE ID = 'Graph.KG.PageRank'")
    if not int(cur.fetchone()[0]):
        _load_from_container(here.parents[1] / "iris_src" / "src", ns, tag)
        engine.initialize_schema()
    if seed:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from generate_old_snapshot import _seed

        written = _seed(engine)
        conn.commit()
        print(f"seeded: {', '.join(f'{k}={len(v)}' for k, v in written.items())}", file=sys.stderr)
    print(json.dumps({"tag": tag, "namespace": ns, "package": str(here.parent), **inventory(conn)}))
    conn.close()
    return 0


def _load_from_container(src: Path, ns: str, tag: str) -> None:
    """Copy a release's classes into the container and LoadDir them: what 3.2.0's own deploy expected done."""
    stage = f"/tmp/ivg262_src_{tag.replace('.', '_')}"
    subprocess.run(["docker", "exec", CONTAINER, "rm", "-rf", stage], check=True)
    subprocess.run(["docker", "cp", f"{src}/.", f"{CONTAINER}:{stage}"], check=True)
    script = f'set sc=$system.OBJ.LoadDir("{stage}","ck-d",,1) write "LOADED ",$system.Status.IsOK(sc),!\nhalt\n'
    out = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", ns],
        input=script, capture_output=True, text=True, timeout=900, check=True,
    ).stdout
    print(f"LoadDir {tag}: {out.strip().splitlines()[-2:] if out.strip() else out}", file=sys.stderr)


def build(tag: str, ns: str, keep: bool = False, seed: bool = True) -> dict:
    """Build `ns` at `tag` and return the inventory the old release left (the child's JSON line)."""
    if not ns.startswith("IVG262") or ns.upper() != ns:
        raise ValueError(f"refusing {ns!r}: only upper-case IVG262* scratch namespaces")
    tree = release_worktree(tag)
    if not keep:
        sys.path.insert(0, str(REPO / "scripts" / "dev"))
        import make_scratch_namespace

        make_scratch_namespace.recreate(ns)
    env = dict(os.environ, PYTHONPATH=str(tree), IVG_FORCE_CLASS_DEPLOY="1")
    args = [sys.executable, str(Path(__file__).resolve()), tag, ns, "--child"] + ([] if seed else ["--no-seed"])
    out = subprocess.run(args, env=env, capture_output=True, text=True, timeout=1800, cwd=str(tree))
    if out.returncode != 0:
        raise RuntimeError(f"{tag} in {ns} failed ({out.returncode}):\n{out.stderr[-3000:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tag")
    ap.add_argument("namespace")
    ap.add_argument("--keep", action="store_true", help="upgrade in place: no wipe, no seed")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        return _child(a.tag, a.namespace, seed=not a.no_seed)
    result = build(a.tag, a.namespace, keep=a.keep, seed=not (a.no_seed or a.keep))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
