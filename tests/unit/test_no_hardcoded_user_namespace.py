"""R129: no test opens USER by name; tests use the run's namespace (IVG_NAMESPACE, default USER) or a scratch one.

`tests/e2e/test_236_reader_gate.py` connected to USER and erased it whatever IVG_NAMESPACE said, and
`tests/integration/test_ledger_lock_and_chunks.py` took the ledger head lock in USER, so in a scratch namespace the lock
test held a lock nobody else wanted and failed. A run pointed at a scratch namespace must leave USER, which another
suite may be using, alone.

The scan reads every test file's syntax tree for "USER" used as a connection target: a `namespace=` keyword, the
namespace argument of `connect(host, port, ns, ...)`, the word after `-U` in an `iris session` argument list, or
`-U USER` inside a string, or USER as the default of any other variable (`os.environ.get("IRIS_NAMESPACE", "USER")`,
R266). A default for IVG_NAMESPACE (`os.environ.get("IVG_NAMESPACE", "USER")`) follows the environment and is not a
target. Each entry of ALLOWED says why that test must name USER.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

#: "path:function" -> why it names USER. A reason starting "USER-only:" is a test that writes there; its file must
#: carry `requires_user_namespace` (tests/conftest.py) so it runs only when USER is the run's own namespace.
ALLOWED: dict[str, str] = {
    "tests/benchmarks/establish_baseline.py:setup_data":
        "a manual benchmark script, not a pytest test: pytest collects test_*.py only (pyproject python_files)",
    "tests/benchmarks/bench.py:<module>":
        "a manual benchmark script, not a pytest test: pytest collects test_*.py only; IRIS_NAMESPACE is its documented knob",
    "tests/benchmarks/bfs_benchmark.py:setup_iris":
        "a manual benchmark script, not a pytest test: pytest collects test_*.py only; IRIS_NAMESPACE is its documented knob",
    "tests/benchmarks/ldbc_full_loader.py:<module>":
        "a manual loader script, not a pytest test: pytest collects test_*.py only; IRIS_NAMESPACE is its documented knob",
    "tests/benchmarks/ic2_profile.py:<module>":
        "a manual profiling script, not a pytest test: pytest collects test_*.py only (pyproject python_files)",
    "tests/conftest.py:ivg_236_scratch":
        "reads ^ArnoAccel(lib_path) from USER so IVG236 loads the same library build; writes only in IVG236",
    "tests/e2e/test_arno_namespace_kept_e2e.py:other_ns":
        "reads ^ArnoAccel(lib_path) from USER; every call under test runs in the other namespace",
    "tests/integration/test_236_capabilities.py:"
    "test_the_sentinel_check_refuses_a_connection_that_is_not_in_the_scratch_namespace":
        "opens USER only to show the scratch sentinel check refuses it; nothing is written through it",
    "tests/e2e/test_embedded_wgproto_e2e.py:_docker_objectscript":
        "USER-only: irispython in the container starts in USER, so the class it compiles must be there too",
    "tests/unit/test_alt_bench_shim.py:test_install_wraps_connect_once":
        "a fake iris module: the call checks the bench shim rewrites USER to the bench namespace; no server",
    "tests/unit/test_doctest_harness.py:test_bash_refusal":
        "a script text the doc harness must refuse to run (a session in USER); nothing is executed",
    "tests/unit/test_engine_unit.py:TestConnectionSeam.test_from_connect_uses_dbapi_connect":
        "dbapi.connect is patched with a MagicMock; no server is reached",
}

_SESSION_U_USER = re.compile(r"(?:^|\s)-U\s*USER\b")
#: Calls that open a connection to the server. An engine or store built with `namespace="USER"` over a mock opens
#: nothing, so it is not a target.
_OPENERS = {"connect", "createConnection", "from_connect", "native"}


def _is_user(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == "USER"


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _targets(tree: ast.AST):
    """(lineno, enclosing function, what) for each "USER" used as a connection target."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def scope(node) -> str:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
                while node in parents:
                    node = parents[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.append(node.name)
                return ".".join(reversed(names))
        return "<module>"

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and _call_name(node) in ("get", "getenv") and len(node.args) == 2
                and _is_user(node.args[1]) and isinstance(node.args[0], ast.Constant)
                and node.args[0].value != "IVG_NAMESPACE"):
            # 4.6.0 R266: a namespace variable of its own (IRIS_NAMESPACE) defaulting to USER ignores the run's
            # IVG_NAMESPACE: test_230_graph_scoped_node_reads_e2e deployed classes into USER from a scratch run
            yield node.lineno, scope(node), f"{_call_name(node)}({node.args[0].value!r}, 'USER')"
        elif isinstance(node, ast.Call) and _call_name(node) in _OPENERS:
            for kw in node.keywords:
                if kw.arg == "namespace" and _is_user(kw.value):
                    yield node.lineno, scope(node), f"{_call_name(node)}(namespace='USER')"
            if len(node.args) >= 3 and _is_user(node.args[2]):
                yield node.lineno, scope(node), f"{_call_name(node)}(host, port, 'USER', ...)"
        elif isinstance(node, (ast.List, ast.Tuple)):
            elts = node.elts
            for a, b in zip(elts, elts[1:]):
                if isinstance(a, ast.Constant) and a.value == "-U" and _is_user(b):
                    yield node.lineno, scope(node), "['-U', 'USER']"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _SESSION_U_USER.search(node.value):
            yield node.lineno, scope(node), "'... -U USER ...'"


@functools.lru_cache(maxsize=None)
def _hits() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(TESTS.rglob("*.py")):
        if path == Path(__file__).resolve():
            continue
        rel = path.relative_to(TESTS.parent).as_posix()
        for lineno, where, what in _targets(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            out.setdefault(f"{rel}:{where}", []).append(f"line {lineno}: {what}")
    return out


def test_no_test_names_user_as_its_namespace_without_a_reason():
    unexplained = {k: v for k, v in _hits().items() if k not in ALLOWED}
    assert unexplained == {}, "\n".join(f"{k}: {v}" for k, v in sorted(unexplained.items()))


def test_every_allowlist_entry_still_matches_and_has_a_reason():
    hits = _hits()
    assert [k for k in ALLOWED if k not in hits] == []
    assert [k for k, why in ALLOWED.items() if len(why.split()) < 6] == []


def test_a_test_that_writes_in_user_runs_only_when_user_is_the_run_s_namespace():
    unmarked = []
    for key, why in ALLOWED.items():
        if why.startswith("USER-only:"):
            tree = ast.parse((TESTS.parent / key.split(":", 1)[0]).read_text(encoding="utf-8"))
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            if "requires_user_namespace" not in names:
                unmarked.append(key)
    assert unmarked == []


def test_the_two_r129_files_follow_the_run_s_namespace():
    """The reader gate is USER-only (marked); the ledger lock is taken in TEST_NAMESPACE."""
    for rel, name in (("e2e/test_236_reader_gate.py", "requires_user_namespace"),
                      ("integration/test_ledger_lock_and_chunks.py", "TEST_NAMESPACE")):
        tree = ast.parse((TESTS / rel).read_text(encoding="utf-8"))
        assert name in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}, rel


def test_the_scan_finds_each_shape():
    tree = ast.parse(
        "import iris\n"
        "def a(): iris.dbapi.connect(hostname='h', port=1, namespace='USER')\n"
        "def b(): iris.connect('h', 1, 'USER', 'u', 'p')\n"
        "def c(): run(['docker', 'exec', 'c', 'iris', 'session', 'IRIS', '-U', 'USER'])\n"
        "def d(): run('docker exec -i c iris session IRIS -U USER')\n"
        "def e(): os.environ.get('IVG_NAMESPACE', 'USER')\n"
        "def f(): return ns == 'USER'\n"
        "def g(): os.environ.get('IRIS_NAMESPACE', 'USER')\n"
        "def h(): os.getenv('IRIS_NAMESPACE', 'USER')\n"
    )
    assert sorted(where for _, where, _ in _targets(tree)) == ["a", "b", "c", "d", "g", "h"]
