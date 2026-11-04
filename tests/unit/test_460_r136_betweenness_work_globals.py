"""4.6.0 R136: the betweenness work areas do not outlive a killed run.

`^bcParSrc`, `^bcParScore` and `^bcBitAdj` are subscripted by the parent's `$Job` and cannot be process-private: the
Brandes workers run in other processes (`%SYSTEM.WorkMgr` queues `Graph.KG.NKGAccel.BetweennessWorker`), and read
`^bcParSrc` and `^bcBitAdj` and write `^bcParScore` there. A run cleared only its own `$Job`'s subtree, so a run killed
mid-way left its subtree for good: the next run is another process with another `$Job`. Each entry point now first
removes every subtree whose process is gone (`^$JOB`), and its error paths remove its own. The live half (a planted
subtree is gone after the next run, and a hand-worked score) is tests/e2e/test_460_r136_betweenness_work_globals_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "NKGAccelCentrality.cls").read_text()
WORK = ("^bcParSrc", "^bcParScore", "^bcBitAdj")


def _method(name: str) -> str:
    start = SRC.index(f"ClassMethod {name}(")
    end = SRC.find("\nClassMethod ", start + 1)
    return SRC[start:end if end > 0 else None]


def test_the_workers_run_in_other_processes_so_the_areas_stay_shared():
    """Checked before choosing: a process-private global is invisible to a WorkMgr worker."""
    assert 'queue.Queue("##class(Graph.KG.NKGAccel).BetweennessWorker"' in _method("BetweennessGlobalParallel")
    worker = _method("BetweennessWorker")
    assert "^bcParSrc(parentPid" in worker and "^bcBitAdj(parentPid" in worker and "^bcParScore(parentPid" in worker


def test_the_cleanup_removes_only_the_subtrees_of_processes_that_are_gone():
    body = _method("ClearStaleWork")
    for g in WORK:
        assert f"$Order({g}(" in body, g
    assert "$Data(^$JOB(" in body
    assert "$Job" in body  # never its own run's subtree


@pytest.mark.parametrize("entry", ["BetweennessGlobalParallel", "BetweennessNeighborhoodOS"])
def test_each_entry_point_cleans_up_first(entry):
    body = _method(entry)
    clear = body.index("..ClearStaleWork()")
    first_write = min(body.index(f"Set {g}(") if f"Set {g}(" in body else len(body) for g in WORK)
    first_write = min(first_write, body.index("..BuildBitAdj(") if "..BuildBitAdj(" in body else len(body))
    first_write = min(first_write, body.index("Set $BIT(^bcBitAdj") if "Set $BIT(^bcBitAdj" in body else len(body))
    assert clear < first_write


@pytest.mark.parametrize("entry", ["BetweennessGlobalParallel", "BetweennessNeighborhoodOS"])
def test_every_answer_leaves_no_work_area_of_its_own(entry):
    """Each Quit or Return after the areas are written is preceded by a kill of all three."""
    body = _method(entry)
    start = body.index("..ClearStaleWork()")
    exits = [m.start() for m in re.finditer(r"\b(?:Quit|Return) \"", body) if m.start() > start]
    assert exits
    for pos in exits:
        before = body[:pos]
        last_kill = before.rindex("..ClearWork(parentPid)") if "..ClearWork(parentPid)" in before else -1
        assert last_kill > start, f"{entry}: an exit at offset {pos} does not clear the work areas"
