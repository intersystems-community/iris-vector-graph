"""4.7.0: the r308 files purge their namespace's cached queries, so their namespace is theirs alone.

test_460_r308_crosstype_e2e (and the differential, the abs-cache and comparison-threshold files) call
`%SYSTEM.SQL.Purge` before each case. A statement another file prepares in that namespace at the same time loses the
cached-query class it was built on: measured on 4.7.0, a Purge every 0.2 s beside test_460_r228_path_list_elements_e2e
failed 8 of its tests at Prepare (SQLCODE -30, "Table valued function '%SQLCQ_<ns>.CLS6_CQ'(...) not found"; the
-400 <LIST>LoadTableFunction seen in a shared run is the same). In a gate run the four files get the r308 group's
namespace, which no other module reads and whose name no other group has; a caller who names it to a namespace another
group's variable names too (IVG_NS_IVG460FXOC=IVG_NAMESPACE, as one 4.7.0 worker did) is refused, for each r308 test,
with the reason, rather than left to break the other files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e import gate

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"
R308 = {"test_460_r308_abs_cached_e2e.py", "test_460_r308_comparison_threshold_e2e.py",
        "test_460_r308_crosstype_e2e.py", "test_460_r308_cypher_differential_e2e.py"}


def _plan(env=None, **kw):
    kw.setdefault("available", lambda: True)
    return gate.GateNamespaces(REPO, run_id="AB12", env=env if env is not None else {}, **kw)


def test_the_r308_group_is_exclusive():
    assert gate.GROUPS["r308"].exclusive
    assert [g for g, grp in gate.GROUPS.items() if grp.exclusive] == ["r308"]


def test_only_the_r308_files_read_its_namespace_and_they_read_no_other():
    readers = {p.name: gate.needs(p.read_text()) for p in sorted(E2E.glob("test_*.py"))}
    assert {n for n, g in readers.items() if "r308" in g} == R308
    for n in R308:
        assert readers[n] == {"r308"}, (n, readers[n])  # not the session's, nor a shared group's


def test_its_per_run_name_is_no_other_groups():
    names = _plan().names()
    assert [g for g, n in names.items() if n == names["r308"]] == ["r308"]


def test_a_caller_naming_it_as_the_session_namespace_is_refused_with_the_reason():
    plan = _plan(env={"IVG_NAMESPACE": "IVG470TIME", "IVG_NS_IVG460FXOC": "IVG470TIME"}, setup=lambda k, n: None)
    plan.prepare({"r308"})
    with pytest.raises(RuntimeError, match=r"IVG_NS_IVG460FXOC names IVG470TIME, as IVG_NAMESPACE does.*Purge"):
        plan.check({"r308"})


def test_a_caller_naming_it_as_another_groups_namespace_is_refused():
    plan = _plan(env={"IVG_NS_IVG460FXOC": "IVG470X", "IVG_NS_IVG460FXOV": "IVG470X"}, setup=lambda k, n: None)
    with pytest.raises(RuntimeError, match=r"IVG_NS_IVG460FXOC names IVG470X, as IVG_NS_IVG460FXOV does"):
        plan.check({"r308"})


def test_a_caller_naming_it_as_one_of_the_runs_own_namespaces_is_refused():
    plan = _plan(env={"IVG_NS_IVG460FXOC": "IVG460FXGAB12P"}, setup=lambda k, n: None)
    plan.prepare({"primary"})
    with pytest.raises(RuntimeError, match=r"IVG_NS_IVG460FXOC names IVG460FXGAB12P, as IVG_NAMESPACE does"):
        plan.check({"r308"})


def test_a_namespace_of_its_own_is_accepted():
    plan = _plan(env={"IVG_NAMESPACE": "IVG470TIME", "IVG_NS_IVG460FXOC": "IVG470OC"}, setup=lambda k, n: None)
    plan.check({"r308"})
    gated = _plan(setup=lambda k, n: None)
    gated.prepare({"r308", "primary"})
    gated.check({"r308"})


def test_a_shared_name_is_fine_for_a_group_that_is_not_exclusive():
    plan = _plan(env={"IVG_NAMESPACE": "IVG470TIME", "IVG_SPLIT_NAMESPACE": "IVG470TIME"}, setup=lambda k, n: None)
    plan.check({"split"})
