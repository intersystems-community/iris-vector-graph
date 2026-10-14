#!/usr/bin/env python3
"""Build the two benchmark sides (spec 250).

Side A is the baseline (commit 6f17be0f), side B the alternative (default ref ``alt-integration``). Each side gets

* a source tree ``/tmp/ivg-src-<X>``: ``iris_vector_graph/``, ``iris_src/``, ``sql/``, ``config/`` from the side's ref
  (``git archive``), overlaid with ``tests/``, ``scripts/``, ``benchmarks/`` and ``pyproject.toml`` from the harness
  checkout so that both sides run identical benchmark code against their own library;
* a namespace with its own database on ``ivg-iris-enterprise`` (``IVGBENCHA`` / ``IVGBENCHB``), the side's
  ``Graph_KG`` schema, its ``Graph.KG.*`` classes compiled, and the Arno callout loaded.

Why a whole tree and not only a PYTHONPATH: ``pyproject.toml`` sets ``pythonpath = ["."]`` and ``tests/__init__.py``
exists, so pytest puts the repo root ahead of PYTHONPATH. Running with the side tree as the working directory makes
"the repo root" the side's own tree.

No database file or directory is ever deleted. Only namespaces named in ``sides.OWN_NAMESPACES`` are touched.

Usage::

    python scripts/bench/alt/setup_sides.py all [--alt-ref REF]
    python scripts/bench/alt/setup_sides.py {export,namespaces,schema,deploy,record,arno,status} [--sides A B]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.bench.alt import sides as sd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_CHECKOUT = Path("/Users/tdyar/ws/iris-vector-graph")
MAIN_DATA = MAIN_CHECKOUT / "data"
# Large untracked inputs of some suites, linked into each side tree when the main checkout has them.
EXTRA_LINKS = ("social_network-sf10-CsvBasic-LongDateFormatter",)
SIDE_PATHS = ("iris_vector_graph", "iris_src", "sql", "config")
OVERLAY_PATHS = ("tests", "scripts", "benchmarks", "pyproject.toml")
VENV_PYTHON = os.environ.get("IVG_BENCH_PYTHON", "/Users/tdyar/ws/iris-vector-graph/.venv/bin/python")
EMBEDDING_DIM = 768


# --------------------------------------------------------------------------- git


def _git(root, *args, **kw):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, **kw)


def resolve_commit(ref, repo_root=REPO_ROOT):
    return _git(repo_root, "rev-parse", "--verify", ref + "^{commit}").stdout.strip()


def _existing_paths(repo_root, ref, paths):
    names = set(_git(repo_root, "ls-tree", "--name-only", ref).stdout.split())
    return [p for p in paths if p in names]


def _archive_into(repo_root, ref, paths, dest):
    paths = _existing_paths(repo_root, ref, paths)
    if not paths:
        return
    g = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", ref, *paths], stdout=subprocess.PIPE
    )
    t = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=g.stdout, capture_output=True)
    g.stdout.close()
    if g.wait() != 0 or t.returncode != 0:
        raise RuntimeError(f"git archive {ref} {paths} failed: {t.stderr.decode()[:300]}")


def export_side_tree(ref, dest, repo_root=REPO_ROOT, overlay_ref="HEAD", data_dir=MAIN_DATA):
    """Create (or recreate) the side's source tree at ``dest``; returns the marker dict."""
    dest = Path(dest)
    marker_path = dest / sd.MARKER
    if dest.exists():
        if not marker_path.exists():
            raise RuntimeError(f"{dest} exists and is not a harness side tree (no {sd.MARKER}); refusing to wipe it")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    commit = resolve_commit(ref, repo_root)
    overlay_commit = resolve_commit(overlay_ref, repo_root)
    _archive_into(repo_root, commit, SIDE_PATHS, dest)
    # Replace the harness-owned dirs wholesale from the overlay (the side's own copies are not used).
    for p in OVERLAY_PATHS:
        target = dest / p
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    _archive_into(repo_root, overlay_commit, OVERLAY_PATHS, dest)
    if data_dir is not None and Path(data_dir).exists():
        (dest / "data").symlink_to(Path(data_dir))
        for name in EXTRA_LINKS:
            src = Path(data_dir).parent / name
            if src.exists():
                (dest / name).symlink_to(src)
    info = {"ref": ref, "commit": commit, "overlay_ref": overlay_ref, "overlay_commit": overlay_commit,
            "patches": apply_baseline_patches(dest, ref)}
    marker_path.write_text(json.dumps(info, indent=2) + "\n")
    return info


PATCH_DIR = Path(__file__).resolve().parent / "patches"


def apply_baseline_patches(dest, ref):
    """The baseline only: add the ObjectScript methods the benchmark's large-answer fallback reads (BFSFastStream,
    ReadBFSResultsStream, ReadLargeOutChunk). They change nothing unless the string call overflowed, and the alternative carries
    the same methods, so the 100,000-node traversals are measured on both sides. Returns the names of the patches applied."""
    if resolve_commit(ref) != resolve_commit(sd.BASELINE_REF):
        return []
    applied = []
    for patch in sorted(PATCH_DIR.glob("baseline-*.patch")):
        subprocess.run(["git", "apply", "-p1", str(patch)], cwd=dest, check=True)
        applied.append(patch.name)
    return applied


def imported_package_path(tree, python=None):
    """Where ``iris_vector_graph`` resolves for a process with PYTHONPATH=<tree>, started elsewhere."""
    env = {**os.environ, "PYTHONPATH": str(tree)}
    code = "import importlib.util as u; print(u.find_spec('iris_vector_graph').origin)"
    out = subprocess.run(
        [python or sys.executable, "-c", code], cwd="/", env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


# --------------------------------------------------------------------------- container


def namespace_script(ns):
    """ObjectScript (run in %SYS) that creates namespace ``ns`` with its own database; idempotent, never deletes."""
    sd.check_namespace(ns)
    low = ns.lower()
    return f"""Set ns="{ns}", dir="/usr/irissys/mgr/{low}/"
If ##class(Config.Namespaces).Exists(ns) {{ Write "RESULT namespace {ns} exists",! Halt }}
If '##class(%File).DirectoryExists(dir) {{ Set ok=##class(%File).CreateDirectoryChain(dir) }}
If '##class(%File).Exists(dir_"IRIS.DAT") {{
  Set sc=##class(SYS.Database).CreateDatabase(dir)
  If $System.Status.IsError(sc) {{ Write "RESULT ERROR createdb ",$System.Status.GetErrorText(sc),! Halt }}
}}
If '##class(Config.Databases).Exists(ns) {{
  Set pdb("Directory")=dir
  Set sc=##class(Config.Databases).Create(ns,.pdb)
  If $System.Status.IsError(sc) {{ Write "RESULT ERROR config.databases ",$System.Status.GetErrorText(sc),! Halt }}
}}
Set pns("Globals")=ns, pns("Routines")=ns
Set sc=##class(Config.Namespaces).Create(ns,.pns)
If $System.Status.IsError(sc) {{ Write "RESULT ERROR config.namespaces ",$System.Status.GetErrorText(sc),! Halt }}
Write "RESULT namespace {ns} created",!
Halt
"""


def session(ns, script, container=sd.CONTAINER, timeout=1800):
    """Run ObjectScript in ``ns`` via ``iris session``; returns stdout."""
    p = subprocess.run(
        ["docker", "exec", "-i", container, "iris", "session", "IRIS", "-U", ns],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.stdout + p.stderr


def create_namespace(ns):
    out = session("%SYS", namespace_script(ns))
    res = [ln for ln in out.splitlines() if ln.startswith("RESULT")]
    if not res or "ERROR" in res[-1]:
        raise RuntimeError(f"namespace {ns}: {out[-600:]}")
    return res[-1]


def _side_python(side, code, extra_env=None):
    env = {**os.environ, "PYTHONPATH": side.tree, **(extra_env or {})}
    p = subprocess.run([VENV_PYTHON, "-c", code], cwd=side.tree, env=env, capture_output=True, text=True, timeout=1800)
    return p


def init_schema(side):
    sd.check_namespace(side.namespace)
    code = f"""
import iris, iris_vector_graph
print('PKG', iris_vector_graph.__file__)
from iris_vector_graph.engine import IRISGraphEngine
conn = iris.connect("{sd.HOST}", {sd.PORT}, "{side.namespace}", "{sd.USER}", "{sd.PASSWORD}")
e = IRISGraphEngine(conn, embedding_dimension={EMBEDDING_DIM})
print('SCHEMA', e.initialize_schema(auto_deploy_objectscript=False))
"""
    p = _side_python(side, code)
    if p.returncode != 0 or "SCHEMA" not in p.stdout:
        raise RuntimeError(f"initialize_schema({side.letter}) failed: {p.stderr[-800:]}")
    pkg = [ln for ln in p.stdout.splitlines() if ln.startswith("PKG")][0].split(" ", 1)[1]
    if not Path(pkg).resolve().is_relative_to(Path(side.tree).resolve()):
        raise RuntimeError(f"side {side.letter} imported {pkg}, outside {side.tree}")
    return pkg


def class_names(tree):
    """Class names declared by the side's iris_src/src files (the `Class X` line, not the path)."""
    root = Path(tree) / "iris_src" / "src"
    names = []
    for p in sorted(root.rglob("*.cls")):
        m = re.search(r"^Class\s+(\S+)", p.read_text(errors="replace"), re.M)
        names.append(m.group(1) if m else str(p.relative_to(root).with_suffix("")).replace(os.sep, "."))
    return sorted(set(names))


def deploy_classes(side, container=sd.CONTAINER):
    """docker cp the side's iris_src/src into the container and LoadDir it into the side's namespace."""
    sd.check_namespace(side.namespace)
    remote = f"/tmp/ivgbench-src-{side.letter}"
    subprocess.run(["docker", "exec", "-u", "root", container, "rm", "-rf", remote], check=True)
    subprocess.run(["docker", "exec", "-u", "root", container, "mkdir", "-p", remote], check=True)
    subprocess.run(["docker", "cp", f"{side.tree}/iris_src/src/.", f"{container}:{remote}/"], check=True)
    script = f"""Set sc=##class(%SYSTEM.OBJ).LoadDir("{remote}","ck",.err,1)
Write "RESULT load ",$Select($System.Status.IsOK(sc):"OK",1:"ERROR "_$System.Status.GetErrorText(sc)),!
Halt
"""
    # Like scripts/enterprise-container.sh compile-all, compile twice: the first pass can fail on
    # classes that depend on ones compiled later in the same pass (MCPToolSet on MCPTools).
    for attempt in (1, 2):
        out = session(side.namespace, script)
        res = [ln for ln in out.splitlines() if ln.startswith("RESULT")]
        if res and "RESULT load OK" in res[-1] and ("Detected" not in out or "Detected 0 errors" in out):
            return f"{res[-1]} (pass {attempt})"
    raise RuntimeError(f"deploy {side.letter}: {out[-1500:]}")


def source_hash(path):
    """Same digest tests/conftest.py::_source_hash uses to decide a class is already deployed."""
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:20]


def record_deployed(side):
    """Write ^IVGDeployed(ns, rel) for every class so the first pytest session does not recompile all of them.

    The conftest copies iris_src/src into the container and loads only classes whose hash differs from this record.
    """
    sd.check_namespace(side.namespace)
    root = Path(side.tree) / "iris_src" / "src"
    lines = []
    for p in sorted(root.rglob("*.cls")):
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        lines.append(f'Set ^IVGDeployed("{side.namespace}","{rel}")="{source_hash(p)}"')
    out = session(side.namespace, "\n".join(lines) + '\nWrite "RESULT recorded ' + str(len(lines)) + '",!\nHalt\n')
    res = [ln for ln in out.splitlines() if ln.startswith("RESULT")]
    return res[-1] if res else f"no result: {out[-300:]}"


def find_arno_so(container=sd.CONTAINER):
    """Path of the callout library inside the container (newest per-build copy, else the mounted one)."""
    out = subprocess.run(
        ["docker", "exec", container, "bash", "-c", "ls -t /tmp/libarno_tcp_*.so /tmp/libarno_callout.so 2>/dev/null"],
        capture_output=True,
        text=True,
    ).stdout.split()
    return out[0] if out else None


def load_arno(side, container=sd.CONTAINER):
    sd.check_namespace(side.namespace)
    so = find_arno_so(container)
    if not so:
        return "no libarno_callout.so in the container; Arno-dependent suites will skip"
    script = f"""Set ok=##class(Graph.KG.ArnoAccel).Load("{so}")
Do ##class(Graph.KG.NKGAccelLoader).Load("{so}")
Write "RESULT arno load=",ok," caps=",##class(Graph.KG.NKGAccel).Capabilities(),!
Halt
"""
    out = session(side.namespace, script)
    res = [ln for ln in out.splitlines() if ln.startswith("RESULT")]
    return res[-1] if res else f"arno load produced no result: {out[-400:]}"


def status(side):
    """Which of the side's classes are compiled in its namespace, and what the namespace holds."""
    names = class_names(side.tree)
    lst = ",".join(f'"{n}"' for n in names)
    script = f"""Set miss=0
For n={lst} {{ If '##class(%Dictionary.CompiledClass).%ExistsId(n) {{ Set miss=miss+1, m(miss)=n }} }}
Write "RESULT classes={len(names)} missing=",miss,!
For i=1:1:miss {{ Write "MISSING ",m(i),! }}
Write "RESULT dataset=",$Get(^IVGBench("dataset")),!
Halt
"""
    out = session(side.namespace, script)
    lines = [ln for ln in out.splitlines() if ln.startswith(("RESULT", "MISSING"))]
    # PageRankEmbedded (Language=python helper) is not compiled in the shared test container either;
    # scripts/enterprise-container.sh compile-all filters it out of its error check.
    return [ln + "  (known, same in USER)" if ln == "MISSING PageRankEmbedded" else ln for ln in lines]


# --------------------------------------------------------------------------- CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["all", "export", "namespaces", "schema", "deploy", "record", "arno", "status"])
    ap.add_argument("--sides", nargs="+", default=["A", "B"], choices=["A", "B"])
    ap.add_argument("--alt-ref", default=sd.DEFAULT_ALT_REF)
    args = ap.parse_args(argv)
    table = sd.with_alt_ref(args.alt_ref)
    steps = ["export", "namespaces", "schema", "deploy", "record", "arno", "status"] if args.step == "all" else [args.step]
    for letter in args.sides:
        side = table[letter]
        for step in steps:
            print(f"[{letter}] {step} ...", flush=True)
            if step == "export":
                print("   ", export_side_tree(side.ref, side.tree))
                print("    imports from", imported_package_path(side.tree, VENV_PYTHON))
            elif step == "namespaces":
                print("   ", create_namespace(side.namespace))
            elif step == "schema":
                print("    package", init_schema(side))
            elif step == "deploy":
                print("   ", deploy_classes(side))
            elif step == "record":
                print("   ", record_deployed(side))
            elif step == "arno":
                print("   ", load_arno(side))
            elif step == "status":
                for ln in status(side):
                    print("   ", ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
