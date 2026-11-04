"""Spec 262 T053 (US5, FR-015, FR-016) and 4.6.0 R080: what the wheel ships of the ObjectScript classes.

The benchmark classes, `User.Exec` (Xecute on any string, called only by tests) and `iris.vector.graph.GraphOperators`
(SQL procedures nothing calls; only tests called its methods) are not in the wheel: they live in `iris_src/dev`, which
tests deploy themselves (`tests/helpers/dev_classes.py`). `Graph.KG.Service` (a REST dispatcher) and
`Graph.KG.FHIRGraphSyncTask` (a Task Manager task) still ship; each is inert until a web application or a task
schedule names it.

The wheel is built from this checkout with `uv build --wheel` (what `scripts/ci-parity.sh` builds).
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PREFIX = "iris_vector_graph/iris_src/src/"
RETIRED = ("Graph/KG/Benchmark.cls", "Graph/KG/BenchFormat.cls", "User.Exec.cls", "iris/vector/graph/GraphOperators.cls")
SHIPPED = ("Graph/KG/Service.cls", "Graph/KG/FHIRGraphSyncTask.cls")


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> set:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("uv is not on PATH; the wheel check needs it (scripts/ci-parity.sh builds with it too)")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run([uv, "build", "-q", "--wheel", "--out-dir", str(out)], cwd=REPO, check=True, timeout=600)
    (wheel,) = out.glob("*.whl")
    with zipfile.ZipFile(wheel) as zf:
        return {n[len(PREFIX):] for n in zf.namelist() if n.startswith(PREFIX)}


def test_the_wheel_carries_the_classes(wheel_names):
    assert len([n for n in wheel_names if n.startswith("Graph/KG/") and n.endswith(".cls")]) > 50


@pytest.mark.parametrize("name", RETIRED)
def test_a_retired_class_is_not_in_the_wheel(wheel_names, name):
    assert name not in wheel_names


@pytest.mark.parametrize("name", SHIPPED)
def test_the_service_and_the_task_still_ship(wheel_names, name):
    assert name in wheel_names


def test_the_retired_classes_are_kept_for_the_tests_in_iris_src_dev():
    dev = REPO / "iris_src" / "dev"
    assert sorted(str(p.relative_to(dev)) for p in dev.rglob("*.cls")) == sorted(RETIRED)
