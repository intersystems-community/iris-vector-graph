"""4.6.0 R309: the E2E files that skip without a scratch namespace get one, so a plain `pytest tests/e2e` runs them.

Every "full E2E" of 4.6.0 ran without 98 files: 49 skip unless `IVG_NAMESPACE` starts IVG460F, 12 through
`reading_role460.requires_namespace`, 37 through `ns460alt.requires_namespace` (an IVG Schema of `RAG`). Each reads the
variable when it is imported, and the session's own namespace (tests/conftest.py, USER by default) must not move, so
`tests/e2e/gate.py` sets the variable for those modules only, while they are collected and while their tests run, to a
namespace made for this run from this checkout. These tests check which modules it picks, the names it gives, that
the environment is scoped to the module, and that namespaces are made once, lazily, and dropped at the end.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.e2e import gate

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"
PY = sys.executable


def _needs(name: str) -> set:
    return gate.needs((E2E / name).read_text())


# --------------------------------------------------------------------------- which modules, which namespace


@pytest.mark.parametrize("name, want", [
    ("test_460_r050_centrality_ranking_e2e.py", {"primary"}),        # NS.startswith("IVG460FX")
    ("test_460_r274_nkg_populated_e2e.py", {"primary"}),             # startswith(("IVG460FX", "IVG460FY"))
    ("test_460_r132_arno_lib_path_e2e.py", {"primary"}),             # pytest.skip in a fixture
    ("test_460_r206_reader_reads_e2e.py", {"primary"}),              # rr.requires_namespace
    ("test_460_r202_reading_role_e2e.py", {"primary", "split"}),
    ("test_460_r150_ledger_index_reload_e2e.py", {"rag"}),           # alt.requires_namespace
    ("test_460_r282_vlp_executor_graph_scope_e2e.py", {"rag"}),
    ("test_460_r050_dry_run_plan_e2e.py", {"dry_run"}),
    ("test_460_r299_first_setup_deploy_e2e.py", {"r299a", "r299b"}),
    ("test_arno_namespace_kept_e2e.py", {"secondary", "paros"}),  # and the degree scratch, if this run has one
    ("test_host_conformance.py", {"s236"}),                          # IVG_NS_IVG236 has no default (R287)
    ("test_460_r173_refusal_snapshot_e2e.py", {"r173"}),             # makes its own; the gate names it per run
    ("test_460_r265_bfsjson_chunked_namespace_e2e.py", {"r265bfs"}),  # skipped in USER, no run gave it another
    ("test_biomedical_demo.py", {"biodemo"}),                        # loads its own data (tests/e2e/own_data_ns.py)
    ("test_fraud_demo.py", {"frauddemo"}),
    ("test_lazy_node_resolution.py", {"lazynodes"}),
    ("test_large_output_chunked.py", {"chunked"}),
    ("test_index_protocol.py", set()),
])
def test_the_modules_and_the_namespaces_they_need(name, want):
    if not (E2E / name).exists():
        pytest.fail(f"{name} is gone; pick another example of its kind")
    assert _needs(name) == want


def _skip_reasons_naming_the_namespace(text: str) -> bool:
    return "IVG_NAMESPACE must name" in text or "set IVG_NAMESPACE to a scratch" in text


def test_every_module_that_skips_without_a_scratch_namespace_gets_one():
    """The classifier is checked against what the files say when they skip, so a new file is not missed."""
    missed = []
    for path in sorted(E2E.glob("test_*.py")):
        text = path.read_text()
        if _skip_reasons_naming_the_namespace(text) and not gate.needs(text) & {"primary", "rag"}:
            missed.append(path.name)
    assert missed == []


def test_the_counts_are_what_the_release_ran_without():
    groups = {"primary": 0, "rag": 0}
    for path in E2E.glob("test_*.py"):
        for g in gate.needs(path.read_text()) & set(groups):
            groups[g] += 1
    assert groups["primary"] >= 56 and groups["rag"] >= 37, groups


def test_no_module_needs_both_a_graph_kg_and_a_rag_namespace():
    both = [p.name for p in E2E.glob("test_*.py") if {"primary", "rag"} <= gate.needs(p.read_text())]
    assert both == []


def test_the_helpers_patched_are_every_helper_that_reads_the_namespace_at_import():
    reading = {f"tests.e2e.{p.stem}" for p in E2E.glob("*.py")
               if not p.name.startswith("test_") and p.name not in ("conftest.py", "gate.py")
               and 'os.environ.get("IVG_NAMESPACE"' in p.read_text()}
    assert reading == set(gate.HELPERS)


# --------------------------------------------------------------------------- names and environment


def _plan(env=None, **kw):
    kw.setdefault("available", lambda: True)
    return gate.GateNamespaces(REPO, run_id="AB12", env=env if env is not None else {}, **kw)


def test_the_per_run_names_are_scratch_names_the_drop_rule_accepts():
    sys.path.insert(0, str(REPO / "scripts" / "dev"))
    import make_scratch_namespace as msn

    names = _plan().names()
    assert names["primary"] == "IVG460FXGAB12P" and names["rag"] == "IVG460FXGAB12R"
    assert len(set(names.values())) == len(names)
    for n in names.values():
        assert msn.may_create(n) and msn.may_drop(n), n
        assert n.startswith("IVG460FX") and n.upper() == n


def test_a_namespace_the_caller_named_is_left_alone():
    plan = _plan(env={"IVG_NAMESPACE": "IVG460FXMINE", "IVG_SPLIT_NAMESPACE": "IVG460FXMYSPLIT"})
    assert plan.env_for({"primary", "split", "dry_run"}) == {"IVG_DRY_RUN_NS": "IVG460FXGAB12DRY"}
    assert set(plan.owned()) == set(plan.names().values()) - {"IVG460FXGAB12P", "IVG460FXGAB12R",
                                                               "IVG460FXGAB12X"}


def test_a_session_namespace_that_is_not_a_scratch_one_still_gets_the_gated_modules_a_scratch_one():
    plan = _plan(env={"IVG_NAMESPACE": "IVGFIX"})
    assert plan.env_for({"primary"}) == {"IVG_NAMESPACE": "IVG460FXGAB12P"}


@pytest.mark.parametrize("env", [{"IVG_GATE_NAMESPACES": "0"}, {"SKIP_IRIS_TESTS": "true"}])
def test_opting_out_provides_nothing(env):
    plan = _plan(env=env)
    assert plan.env_for({"primary", "rag", "split"}) == {}
    assert plan.owned() == []


def test_no_container_provides_nothing():
    assert _plan(available=lambda: False).env_for({"primary"}) == {}


def test_made_at_session_start_for_what_was_collected_and_dropped_at_the_end():
    """All at once before the first test, never mid-run: namespace creation and drops overlapping heavy test writes
    preceded an ivg-iris-enterprise freeze (2026-11-05)."""
    made, dropped = [], []
    plan = _plan(setup=lambda kind, ns: made.append((kind, ns)), drop=lambda ns: dropped.append(ns) or True)
    assert made == []
    plan.prepare({"primary", "dry_run", "rag", "split"})
    assert made == [("graph_kg", "IVG460FXGAB12P"), ("rag", "IVG460FXGAB12R"), ("split", "IVG460FXGAB12X")]
    plan.check({"primary"})
    plan.check({"rag", "dry_run"})
    assert len(made) == 3  # a test's setup only checks
    plan.finish()
    # what was prepared, the name-only ones too (their files make them); never a group no module asked for
    names = plan.names()
    assert sorted(dropped) == sorted(names[g] for g in ("primary", "dry_run", "rag", "split"))


def test_a_test_never_makes_a_namespace_itself():
    made = []
    plan = _plan(setup=lambda kind, ns: made.append(ns), drop=lambda ns: True)
    plan.prepare({"primary"})
    with pytest.raises(RuntimeError, match="not made at session start"):
        plan.check({"rag"})
    assert made == ["IVG460FXGAB12P"]


def test_a_failed_setup_fails_every_test_that_needs_it_with_the_cause():
    calls = []

    def boom(kind, ns):
        calls.append(ns)
        if kind == "graph_kg":
            raise RuntimeError("tcp-deploy said no")

    plan = _plan(setup=boom, drop=lambda ns: True)
    plan.prepare({"primary", "rag"})  # records, and goes on to the next
    for _ in range(2):
        with pytest.raises(RuntimeError, match="IVG460FXGAB12P.*tcp-deploy said no"):
            plan.check({"primary"})
    plan.check({"rag"})
    assert calls == ["IVG460FXGAB12P", "IVG460FXGAB12R"]  # each tried once


def test_keep_leaves_them():
    dropped = []
    plan = _plan(env={"IVG_GATE_KEEP_NAMESPACES": "1"}, setup=lambda k, n: None, drop=dropped.append)
    plan.prepare({"primary"})
    plan.finish()
    assert dropped == []
    assert plan.messages == ["R309 gate: kept ['IVG460FXGAB12P'] (IVG_GATE_KEEP_NAMESPACES=1)"]


# --------------------------------------------------------------------------- in a pytest run


def _project(tmp_path: Path) -> Path:
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    log = tmp_path / "made.log"
    (tmp_path / "conftest.py").write_text(textwrap.dedent(f"""
        from pathlib import Path
        from tests.e2e import gate

        LOG = Path({str(log)!r})

        def _log(line):
            with LOG.open("a") as fh:
                fh.write(line + "\\n")
            return True

        def pytest_configure(config):
            config.pluginmanager.register(gate.GateNamespaces(
                Path(str(config.rootpath)), run_id="CD34", available=lambda: True, helpers=(),
                setup=lambda kind, ns: _log(f"make {{kind}} {{ns}}"),
                drop=lambda ns: _log(f"drop {{ns}}")), "ivg-gate-namespaces")
    """))
    (tmp_path / "tests" / "e2e" / "test_a_gated.py").write_text(textwrap.dedent("""
        import os
        from pathlib import Path

        import pytest

        NS = os.environ.get("IVG_NAMESPACE", "")
        pytestmark = pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name one")

        @pytest.fixture(scope="module")
        def seen():
            return os.environ.get("IVG_NAMESPACE")

        def test_it_runs_in_its_namespace(seen):
            assert NS == seen == os.environ["IVG_NAMESPACE"] == "IVG460FXGCD34P"
            with (Path(__file__).parents[2] / "made.log").open("a") as fh:
                fh.write("run\\n")
    """))
    (tmp_path / "tests" / "e2e" / "test_b_plain.py").write_text(textwrap.dedent("""
        import os

        AT_IMPORT = os.environ.get("IVG_NAMESPACE")

        def test_the_session_namespace_is_untouched():
            assert AT_IMPORT is None and os.environ.get("IVG_NAMESPACE") is None
    """))
    return log


def _run(tmp_path: Path, *args: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IVG_", "SKIP_IRIS"))}
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rs", *args], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=120)


def test_in_a_run_the_gated_module_runs_in_its_namespace_and_the_other_sees_nothing(tmp_path):
    log = _project(tmp_path)
    out = _run(tmp_path, "tests/e2e")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "2 passed" in out.stdout, out.stdout
    assert log.read_text().splitlines() == ["make graph_kg IVG460FXGCD34P", "run", "drop IVG460FXGCD34P"]


def test_running_only_the_plain_module_makes_nothing(tmp_path):
    log = _project(tmp_path)
    out = _run(tmp_path, "tests/e2e/test_b_plain.py")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not log.exists()  # nothing made, nothing dropped: IRIS is not touched


@pytest.mark.parametrize("args", [("--collect-only", "tests/e2e"), ("-k", "plain", "tests/e2e")])
def test_collecting_only_or_deselecting_the_gated_module_makes_nothing(tmp_path, args):
    log = _project(tmp_path)
    out = _run(tmp_path, *args)
    assert out.returncode == 0, out.stdout + out.stderr
    assert not log.exists()


def test_the_root_conftest_registers_the_plugin():
    text = (REPO / "tests" / "conftest.py").read_text()
    assert "gate.register(config)" in text
