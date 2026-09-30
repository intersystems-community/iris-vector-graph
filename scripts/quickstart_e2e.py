#!/usr/bin/env python3
"""Run what a new user runs, on a stock container, from the built wheel.

    python scripts/quickstart_e2e.py                 # CI: compose as shipped, localhost:1972
    python scripts/quickstart_e2e.py --isolated      # here: no published ports, own name

Stages (``--stages``, comma-separated; default all, in this order):

- ``readme``: the README "Run your first query" snippet, verbatim, in a clean venv with
  only the wheel installed. The last ``print(...)  # <literal>`` is the promise.
- ``quickstart``: every python block of ``docs/setup/QUICKSTART.md`` as one program;
  each ``Output:`` block is the promise for the python block before it.
- ``examples``: ``examples/demo_*.py`` against the same container.
- ``setup-iris``: ``scripts/setup_iris.py --no-start`` against it.
- ``upgrade``: a fresh container, the newest PyPI release at or below the wheel's
  version writes a graph, then the wheel is installed over it and must read it back.

Every stage but the old release's own install is a clean-install gate: an
``ERROR #nnnn`` line anywhere in its output fails it.

Stdlib only: this runs before anything is installed. Why it exists: 4.1.0 shipped with
the compose image dead on arrival, the wheel unable to deploy its classes on a stock
container, and a README promising output the engine never prints (2026-09-30).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
README_SECTION = "### 3. Run your first query"
CONNECT = re.compile(r'iris\.connect\("localhost",\s*1972\b')
ERROR_LINE = re.compile(r"ERROR #\d+[^\n]*")
FENCE = re.compile(r"^```([\w-]*)\s*\n(.*?)^```\s*$", re.S | re.M)
STAGES = ("readme", "quickstart", "examples", "setup-iris", "upgrade")


# ---------------------------------------------------------------- doc parsing


def python_blocks(markdown: str) -> List[str]:
    return [m.group(2) for m in FENCE.finditer(markdown) if m.group(1) == "python"]


def readme_snippet(readme: str) -> str:
    start = readme.find(README_SECTION)
    if start < 0:
        raise ValueError(f"README has no {README_SECTION!r} section")
    end = readme.find("\n## ", start)
    blocks = python_blocks(readme[start : end if end > 0 else None])
    if not blocks:
        raise ValueError(f"{README_SECTION!r} has no python block")
    return blocks[0]


def expected_print(code: str):
    """The literal after the last ``print(...)  # <expected>``."""
    promise = None
    for line in code.splitlines():
        m = re.match(r"\s*print\(.*\)\s*#\s*(.+)$", line)
        if m:
            promise = m.group(1).strip()
    if promise is None:
        raise ValueError("no print(...) line carries a '# <expected>' comment")
    return ast.literal_eval(promise)


def retarget(code: str, host: str, port: int) -> str:
    """Point the doc's ``iris.connect("localhost", 1972`` at ``host:port``."""
    if not CONNECT.search(code):
        raise ValueError('the doc no longer connects with iris.connect("localhost", 1972')
    return CONNECT.sub(f'iris.connect("{host}", {port}', code)


def marker(i: int) -> str:
    return f"@@IVG-QUICKSTART-BLOCK-{i}@@"


def doc_program(markdown: str) -> Tuple[str, Dict[int, str]]:
    """All python blocks as one program, and what the doc says each prints.

    A block's promise is the first fenced block after an ``Output:`` line, if that
    comes before the next python block.
    """
    program: List[str] = []
    expected: Dict[int, str] = {}
    index = -1
    awaiting = False
    pos = 0
    for m in FENCE.finditer(markdown):
        between = markdown[pos : m.start()]
        pos = m.end()
        lang, body = m.group(1), m.group(2)
        if lang == "python":
            index += 1
            program.append(body)
            program.append(f"print({marker(index)!r}, flush=True)\n")
            awaiting = False
            continue
        if index >= 0 and index not in expected and re.search(r"^Output:\s*$", between, re.M):
            awaiting = True
        if awaiting and lang in ("", "text"):
            expected[index] = body.strip()
            awaiting = False
    return "".join(program), expected


def split_output(stdout: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    rest = stdout
    i = 0
    while True:
        tag = marker(i)
        at = rest.find(tag)
        if at < 0:
            return out
        out[i] = rest[:at].strip()
        rest = rest[at + len(tag) :]
        i += 1


def error_lines(output: str) -> List[str]:
    return [m.group(0).strip() for m in ERROR_LINE.finditer(output)]


def _version_key(v: str) -> Optional[Tuple[int, ...]]:
    if not re.fullmatch(r"\d+(\.\d+)*", v):
        return None
    return tuple(int(p) for p in v.split("."))


def previous_release(published: Sequence[str], wheel_version: str) -> str:
    ceiling = _version_key(wheel_version)
    if ceiling is None:
        raise ValueError(f"cannot read wheel version {wheel_version!r}")
    candidates = [(k, v) for v in published if (k := _version_key(v)) and k <= ceiling]
    if not candidates:
        raise ValueError(f"no published release at or below {wheel_version}")
    return max(candidates)[1]


def isolated_override(container_name: str) -> str:
    return textwrap.dedent(
        f"""\
        services:
          iris:
            container_name: {container_name}
            ports: !reset []
            network_mode: bridge
        """
    )


# ---------------------------------------------------------------- execution


class StageFailed(AssertionError):
    pass


def _run(cmd, *, check=True, capture=False, **kw) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run(
        [str(c) for c in cmd], text=True, capture_output=capture, **kw
    )
    if check and r.returncode != 0:
        detail = (r.stdout or "") + (r.stderr or "") if capture else ""
        raise StageFailed(f"exit {r.returncode}: {' '.join(map(str, cmd))}\n{detail}")
    return r


class Stack:
    """The repo's compose service, as shipped (CI) or without published ports (here)."""

    def __init__(self, work: Path, isolated: bool, pull: bool):
        self.isolated = isolated
        self.pull = pull
        self.project = f"ivgqs{os.getpid()}"
        self.files = [COMPOSE]
        self.container = "iris_vector_graph"
        if isolated:
            self.container = f"ivg-quickstart-e2e-{os.getpid()}"
            override = work / "compose.isolated.yml"
            override.write_text(isolated_override(self.container))
            self.files.append(override)

    def _compose(self, *args, **kw):
        cmd = ["docker", "compose", "-p", self.project]
        for f in self.files:
            cmd += ["-f", f]
        return _run(cmd + list(args), **kw)

    def up(self) -> Tuple[str, int]:
        if self.pull:
            self._compose("pull")
        self._compose("up", "-d", "--wait", "--wait-timeout", "300")
        if not self.isolated:
            return "localhost", 1972
        ip = _run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", self.container],
            capture=True,
        ).stdout.strip()
        return ip, 1972

    def down(self):
        self._compose("down", "-v", check=False)


def _venv(path: Path, python: str) -> Path:
    _run([python, "-m", "venv", path])
    py = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    return py


def _python(py: Path, script: Path, cwd: Path, env: Optional[dict] = None, timeout=1200):
    print(f"+ {py} {script}", flush=True)
    r = subprocess.run(
        [str(py), str(script)], cwd=cwd, text=True, capture_output=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})}, timeout=timeout,
    )
    sys.stdout.write(textwrap.indent(r.stdout + r.stderr, "    | "))
    return r


def _gate(name: str, r: subprocess.CompletedProcess):
    if r.returncode != 0:
        raise StageFailed(f"{name}: exit {r.returncode}")
    errors = error_lines(r.stdout + r.stderr)
    if errors:
        raise StageFailed(f"{name}: a clean install printed {errors}")


def stage_readme(py, work, host, port):
    code = retarget(readme_snippet((ROOT / "README.md").read_text()), host, port)
    want = expected_print(code)
    script = work / "readme_snippet.py"
    script.write_text(code)
    r = _python(py, script, work)
    _gate("readme", r)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    got = ast.literal_eval(lines[-1]) if lines else None
    if got != want:
        raise StageFailed(f"readme: prints {got!r}, README promises {want!r}")


def stage_quickstart(py, work, host, port):
    program, expected = doc_program((ROOT / "docs/setup/QUICKSTART.md").read_text())
    script = work / "quickstart_program.py"
    script.write_text(retarget(program, host, port))
    r = _python(py, script, work)
    _gate("quickstart", r)
    got = split_output(r.stdout)
    wrong = {i: (got.get(i), want) for i, want in expected.items() if got.get(i) != want}
    if wrong:
        raise StageFailed(
            "quickstart: output differs from the guide\n"
            + "\n".join(f"  block {i}: printed {g!r}, guide says {w!r}" for i, (g, w) in wrong.items())
        )


def stage_examples(py, work, container):
    # A copy, so `sys.path.insert(0, <parent>)` in each demo cannot put the checkout's
    # iris_vector_graph ahead of the wheel.
    shutil.copytree(ROOT / "examples", work / "examples",
                    ignore=shutil.ignore_patterns("__pycache__", "*.npy", "*.graphml"))
    (work / "examples" / "__init__.py").touch()
    failed = []
    for demo in sorted((work / "examples").glob("demo_*.py")):
        if demo.name == "demo_utils.py":
            continue
        r = _python(py, demo, work / "examples",
                    env={"IVG_TEST_CONTAINER": container, "NO_COLOR": "1"})
        try:
            _gate(demo.name, r)
        except StageFailed as exc:
            failed.append(str(exc))
    if failed:
        raise StageFailed("examples:\n  " + "\n  ".join(failed))


def stage_setup_iris(py, work, host, port):
    env_file = work / "setup_iris.env"
    shutil.copy(ROOT / "scripts" / "setup_iris.py", work / "setup_iris.py")
    print(f"+ setup_iris.py --no-start --host {host} --port {port}", flush=True)
    r = subprocess.run(
        [str(py), "setup_iris.py", "--no-start", "--host", host, "--port", str(port),
         "--env-file", str(env_file)],
        cwd=work, text=True, capture_output=True, timeout=1200,
    )
    sys.stdout.write(textwrap.indent(r.stdout + r.stderr, "    | "))
    _gate("setup-iris", r)
    if f"IRIS_PORT={port}" not in env_file.read_text():
        raise StageFailed(f"setup-iris: {env_file} does not name port {port}")


UPGRADE_SEED = """\
import json, iris
from iris_vector_graph.engine import IRISGraphEngine
conn = iris.connect({host!r}, {port}, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()
for i in range(20):
    engine.create_node(f"n{{i}}", labels=["Item"], properties={{"name": f"Item {{i}}", "rank": i}})
for i in range(19):
    engine.create_edge(f"n{{i}}", "NEXT", f"n{{i+1}}")
"""

UPGRADE_READ = """\
import json, iris
from iris_vector_graph.engine import IRISGraphEngine
conn = iris.connect({host!r}, {port}, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
{init}
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
edges = cur.fetchone()[0]
one = engine.execute_cypher(
    "MATCH (a {{node_id:$id}})-[:NEXT]->(b) RETURN b.name AS name", {{"id": "n3"}})
out = {{"nodes": engine.count_nodes(), "edges": edges, "one_hop": list(map(list, one["rows"]))}}
if {verify}:
    far = engine.execute_cypher(
        "MATCH (a {{node_id:$id}})-[:NEXT*1..3]->(b) RETURN b.node_id AS id ORDER BY id",
        {{"id": "n0"}})
    out["three_hops"] = [r[0] for r in far["rows"]]
    out["verify_ok"] = engine.verify_graph()["ok"]
print(json.dumps(out))
"""


def _pypi_versions() -> List[str]:
    with urllib.request.urlopen("https://pypi.org/pypi/iris-vector-graph/json", timeout=30) as f:
        return list(json.load(f)["releases"])


def _wheel_version(wheel: Path) -> str:
    return wheel.name.split("-")[1]


def install_over(py: str, wheel: Path) -> List[str]:
    """Install the wheel over whatever release is there, even one of the same version.

    An unbumped tree builds the version already published; without --force-reinstall
    pip calls it satisfied and the upgrade stage reads with the old code."""
    return [py, "-m", "pip", "install", "-q", "--force-reinstall", "--no-deps", str(wheel)]


def stage_upgrade(stack_factory, python, work, wheel, from_version):
    old = from_version or previous_release(_pypi_versions(), _wheel_version(wheel))
    print(f"== upgrade: iris-vector-graph {old} -> {wheel.name}", flush=True)
    stack = stack_factory()
    try:
        host, port = stack.up()
        py = _venv(work / "venv-upgrade", python)
        _run([py, "-m", "pip", "install", "-q", f"iris-vector-graph=={old}"])
        seed = work / "upgrade_seed.py"
        seed.write_text(UPGRADE_SEED.format(host=host, port=port))
        r = _python(py, seed, work)
        if r.returncode != 0:  # the old release's own noise is not this gate's to judge
            raise StageFailed(f"upgrade: {old} could not write the graph")
        read = work / "upgrade_read.py"
        read.write_text(UPGRADE_READ.format(host=host, port=port, init="", verify=False))
        before = json.loads(_python(py, read, work).stdout.strip().splitlines()[-1])

        _run(install_over(py, wheel))
        _run([py, "-m", "pip", "install", "-q", str(wheel)])  # any dependency it added
        read.write_text(UPGRADE_READ.format(
            host=host, port=port, init="engine.initialize_schema()", verify=True))
        r = _python(py, read, work)
        _gate("upgrade", r)
        after = json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        stack.down()
    problems = [k for k in ("nodes", "edges", "one_hop") if after[k] != before[k]]
    if problems:
        raise StageFailed(f"upgrade: {problems} changed: before {before}, after {after}")
    if after["three_hops"] != ["n1", "n2", "n3"]:
        raise StageFailed(f"upgrade: 3-hop read after upgrade is {after['three_hops']}")
    if not after["verify_ok"]:
        raise StageFailed("upgrade: verify_graph() is not ok after the upgrade")


def build_wheel(out: Path) -> Path:
    if shutil.which("uv"):
        _run(["uv", "build", "--wheel", "-o", out], cwd=ROOT)
    else:
        _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", out, ROOT])
    (wheel,) = out.glob("iris_vector_graph-*.whl")
    return wheel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--wheel", type=Path, help="default: build one from the checkout")
    ap.add_argument("--isolated", action="store_true",
                    help="no published ports, own container name; connect by container IP")
    ap.add_argument("--no-pull", action="store_true", help="use the cached image")
    ap.add_argument("--from-version", help="upgrade stage: release to start from")
    ap.add_argument("--python", default=sys.executable, help="interpreter for the venvs")
    args = ap.parse_args(argv)
    stages = [s for s in args.stages.split(",") if s]
    unknown = set(stages) - set(STAGES)
    if unknown:
        ap.error(f"unknown stages {sorted(unknown)}")

    work = Path(tempfile.mkdtemp(prefix="ivg-quickstart-"))
    print(f"work dir: {work}", flush=True)
    wheel = args.wheel.resolve() if args.wheel else build_wheel(work / "dist")
    make_stack = lambda: Stack(work, args.isolated, pull=not args.no_pull)  # noqa: E731

    results: Dict[str, str] = {}
    shared = [s for s in stages if s != "upgrade"]
    if shared:
        stack = make_stack()
        try:
            host, port = stack.up()
            py = _venv(work / "venv", args.python)
            _run([py, "-m", "pip", "install", "-q", wheel])
            for s in shared:
                if s == "examples":
                    _run([py, "-m", "pip", "install", "-q", f"{wheel}[rdf]"])
                start = time.monotonic()
                try:
                    {
                        "readme": lambda: stage_readme(py, work, host, port),
                        "quickstart": lambda: stage_quickstart(py, work, host, port),
                        "examples": lambda: stage_examples(py, work, stack.container),
                        "setup-iris": lambda: stage_setup_iris(py, work, host, port),
                    }[s]()
                    results[s] = f"PASS ({time.monotonic() - start:.0f}s)"
                except StageFailed as exc:
                    results[s] = f"FAIL: {exc}"
        finally:
            stack.down()
    if "upgrade" in stages:
        try:
            stage_upgrade(make_stack, args.python, work, wheel, args.from_version)
            results["upgrade"] = "PASS"
        except StageFailed as exc:
            results["upgrade"] = f"FAIL: {exc}"

    print("\n== quickstart e2e")
    for s in stages:
        print(f"  {s:11} {results.get(s, 'not run')}")
    return 0 if all(r.startswith("PASS") for r in results.values()) and results else 1


if __name__ == "__main__":
    sys.exit(main())
