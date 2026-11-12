"""4.7.0: nothing in scripts/ or tests/ runs ObjectScript through `iris session`; the ones that still do are listed.

Cortex XDR, on Tom's laptop and on the Mac Studio, kills any process that pipes ObjectScript into `iris session`
(through `docker exec` or ssh), and repeated blocks become a SOC ticket. IVG's test tooling did exactly that to make
scratch namespaces, so the release gates could not run. Namespaces and databases are now made over TCP
(scripts/dev/tcp_setup.py), and this test keeps the pattern from coming back: a file that starts running `iris
session` fails it. `STILL_PIPING` lists the files that did so when this test was written; it may only shrink, and an
entry whose file no longer pipes fails too, so the list is kept true. `DATA` lists files where the words are test
input that nothing runs.

What counts, read from the code (comments and docstrings, which only describe, do not):
- Python: an argument list with "iris", "session" next to each other or naming `irissession`; or a string, not a
  docstring, that names `iris session` / `irissession` together with docker, ssh, a pipe, a here-document or `-U`.
- Shell and anything else: a line, not a comment, that names `iris session` or `irissession`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("scripts", "tests")
_WORDS = re.compile(r"\biris session\b|\birissession\b", re.I)
_COMMANDISH = re.compile(r"docker|ssh|\||<<|-U")

#: Files whose `iris session` text is input to a checker or a fixture of one; nothing runs it.
DATA = {
    "tests/unit/test_no_iris_session_pipes.py": "this test's own samples",
    "tests/unit/test_doctest_harness.py": "command lines the harness's refusal check is fed",
    "tests/unit/test_no_hardcoded_user_namespace.py": "a sample source the USER-namespace checker is fed",
    "tests/unit/test_docs_consistency_checker.py": "sample docs text for the docs checker",
    "tests/unit/test_460_r197_test_container_refusal.py": "asserts test-container.sh's text",
}

#: Files that ran ObjectScript through `iris session` when this test was written (4.7.0). Shrink only: each is ported
#: to TCP and removed from here (4.7.0 phase 2: a TCP runner for "these lines in namespace X").
STILL_PIPING = {
    "scripts/bench/alt/setup_sides.py": "bench sides setup",
    "scripts/bench/alt/snb_bulk.py": "SNB bulk loader progress lines",
    "scripts/bench/aws/bootstrap.sh": "AWS bootstrap health probe (on the cloud host)",
    "scripts/bench/nkg_swap/run_scale.py": "NKG swap bench",
    "scripts/deploy_objectscript.py": "legacy class loader",
    "scripts/deploy_production.py": "legacy production setup",
    "scripts/fixtures/build_release_namespace.py": "LoadDir of an old release's classes",
    "scripts/install-embedded-deps.sh": "embedded deps check in USER",
    "scripts/line_coverage/run_gate.sh": "line-coverage gate (Cov.cls load and dumps)",
    "scripts/test-container.sh": "Community container class load",
    "tests/e2e/test_252_fresh_namespace_e2e.py": "%SYS setup",
    "tests/e2e/test_262_inventory.py": "inventory reads",
    "tests/e2e/test_460_r050_dry_run_plan_e2e.py": "%SYS setup",
    "tests/e2e/test_460_r200_rrf_fuse_e2e.py": "ObjectScript caller",
    "tests/e2e/test_460_r229_webapp_missing_routine_e2e.py": "ObjectScript caller",
    "tests/e2e/test_460_r265_vector_procedures_route_e2e.py": "ObjectScript caller",
    "tests/e2e/test_460_r279_dynamic_sql_refusals_e2e.py": "%SYS setup",
    "tests/e2e/test_460_r280_sql_global_readers_privilege_e2e.py": "%SYS setup",
    "tests/e2e/test_460_r288_arno_callout_allowlist_e2e.py": "%SYS setup",
    "tests/e2e/test_460_r289_sql_writers_privilege_e2e.py": "%SYS setup",
    "tests/e2e/test_460_r293_legacy_arno_revoke_e2e.py": "%SYS setup",
    "tests/e2e/test_460_sec_audit_e2e.py": "ObjectScript caller",
    "tests/e2e/test_embedded_irispython_e2e.py": "ObjectScript caller",
    "tests/e2e/test_embedded_wgproto_e2e.py": "irissession here-document",
    "tests/e2e/test_execution_contexts_new.py": "IVG.CypherEngine from a terminal",
    "tests/e2e/test_fhirgraph_failures_e2e.py": "ObjectScript caller",
    "tests/e2e/test_upgrade_dry_run_2x.py": "%SYS setup",
    "tests/integration/test_044_bm25_incremental.py": "ObjectScript caller",
    "tests/integration/test_adjacency_from_seed.py": "ObjectScript caller",
    "tests/integration/test_cypher_engine_objectscript.py": "ObjectScript caller",
    "tests/integration/test_ivg400_rescue_resume_live.py": "%SYS setup",
    "tests/integration/test_ledger_lock_and_chunks.py": "ObjectScript caller",
    "tests/integration/test_mcp_tools.py": "ObjectScript caller",
    "tests/integration/test_namespace_integration.py": "%SYS namespace setup",
    "tests/integration/test_nkg_accel_no_library.py": "ObjectScript caller",
    "tests/integration/test_rest_service_handlers.py": "ObjectScript caller",
    "tests/integration/test_small_helper_classes.py": "ObjectScript caller",
    "tests/integration/test_vecindex_trees.py": "ObjectScript caller",
}


def _docstring_nodes(tree: ast.AST) -> set:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for stmt in body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    ids.add(id(stmt.value))
    return ids


def python_pipes(source: str) -> list:
    """Line numbers where `source` runs `iris session` (see the module docstring)."""
    tree = ast.parse(source)
    docs = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Call)):
            elts = node.args if isinstance(node, ast.Call) else node.elts
            words = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            pairs = any(a == "iris" and b == "session" for a, b in zip(words, words[1:]))
            if pairs or any(w.endswith("irissession") for w in words):
                hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if _WORDS.search(node.value) and _COMMANDISH.search(node.value):
                hits.append(node.lineno)
        elif isinstance(node, ast.JoinedStr) and id(node) not in docs:
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if _WORDS.search(text) and _COMMANDISH.search(text):
                hits.append(node.lineno)
    return sorted(set(hits))


def shell_pipes(source: str) -> list:
    return [n for n, line in enumerate(source.splitlines(), 1)
            if not line.lstrip().startswith("#") and _WORDS.search(line)]


def pipes(path: Path) -> list:
    text = path.read_text(errors="replace")
    if path.suffix == ".py":
        return python_pipes(text)
    return shell_pipes(text)


def _files():
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in (".py", ".sh", ".bash", ".cpf", ".yml", ".yaml", ".toml", ".cfg") or path.name == "Makefile":
                yield path


@pytest.fixture(scope="module")
def found() -> dict:
    return {str(p.relative_to(REPO)): hits for p in _files() if (hits := pipes(p))}


def test_no_new_file_runs_iris_session(found):
    new = {f: h for f, h in found.items() if f not in STILL_PIPING and f not in DATA}
    assert not new, (
        "these files run ObjectScript through `iris session`, which Cortex XDR kills; use scripts/dev/tcp_setup.py "
        f"or the Native API over TCP instead: {new}"
    )


def test_the_still_piping_list_only_shrinks(found):
    """A listed file that no longer pipes is removed from the list, so the list says what is left."""
    stale = sorted(f for f in STILL_PIPING if f not in found)
    assert not stale, f"no longer run `iris session`; remove from STILL_PIPING: {stale}"
    assert not set(STILL_PIPING) & set(DATA)


def test_the_namespace_setup_no_longer_pipes(found):
    for path in ("scripts/dev/make_scratch_namespace.py", "scripts/dev/tcp_setup.py", "scripts/tck/setup_namespace.sh",
                 "tests/e2e/gate.py", "tests/conftest.py"):
        assert path not in found and path not in STILL_PIPING, path


# ── the detector itself ────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", [
    'subprocess.run(["docker", "exec", "-i", c, "iris", "session", "IRIS", "-U", "%SYS"], input=s)',
    'subprocess.run(("docker", "exec", c, "/usr/irissys/bin/irissession", "IRIS"), input=s)',
    'os.system("echo halt | docker exec -i c iris session IRIS")',
    'cmd = f"printf x | iris session iris -U {ns}"',
    'run(["ssh", "studio", "iris session IRIS -U USER < x"])',
    '_docker("exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", ns, input=script)',
])
def test_python_shapes_that_run_a_session(code):
    assert python_pipes(code) == [1]


@pytest.mark.parametrize("code", [
    '"""Docs say: `docker exec -i c iris session IRIS -U USER`."""',
    "def f():\n    '''iris session | pipe, described'''\n",
    '# docker exec -i c iris session IRIS',
    'msg = "opened an iris session terminal"',
    'subprocess.run(["docker", "exec", c, "ls", "/tmp"])',
])
def test_python_text_that_only_describes(code):
    assert python_pipes(code) == []


@pytest.mark.parametrize("line, hit", [
    ('docker exec -i "$c" iris session IRIS -U %SYS <<EOF', True),
    ("printf 'halt\\n' | iris session iris -U \"$NS\"", True),
    ("session() { docker exec -i \"$C\" iris session IRIS -U \"$1\"; }", True),
    ("# via `docker exec ... iris session IRIS -U %SYS`", False),
    ('"$py" "$repo/scripts/dev/tcp_setup.py" make "$ns"', False),
])
def test_shell_lines(line, hit):
    assert bool(shell_pipes(line)) is hit


# ── the gate's own recipe ──────────────────────────────────────────────────────


def test_the_gate_messages_name_no_session():
    from tests.e2e import gate

    for var, text in gate.SESSION_NAMESPACES.items():
        assert not _WORDS.search(text), (var, text)


def test_the_community_recipe_unexpires_by_cpf_merge():
    """The Community container (GitHub #13) gets its passwords cleared by a CPF merge file at start, not a session."""
    from tests.e2e import gate

    text = gate.SESSION_NAMESPACES["IVG_ISSUE13_NS"]
    assert "ISC_CPF_MERGE_FILE" in text and "scripts/dev/no-expire-passwords.cpf" in text
    cpf = (REPO / "scripts" / "dev" / "no-expire-passwords.cpf").read_text()
    assert "[Actions]" in cpf
    for user in ("_SYSTEM", "SuperUser"):
        assert f"ModifyUser:Name={user},ChangePassword=0,PasswordNeverExpires=1" in cpf
