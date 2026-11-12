"""Spec 262 T053 (US5, FR-015, FR-016) and 4.6.0 R080: what the wheel ships of the ObjectScript classes.

The benchmark classes, `User.Exec` (Xecute on any string, called only by tests) and `iris.vector.graph.GraphOperators`
(SQL procedures nothing calls; only tests called its methods) are not in the wheel: they live in `iris_src/dev`, which
tests deploy themselves (`tests/helpers/dev_classes.py`). `Graph.KG.Service` (a REST dispatcher) and
`Graph.KG.FHIRGraphSyncTask` (a Task Manager task) still ship; each is inert until a web application or a task
schedule names it.

The wheel is built from this checkout with `uv build --wheel` (what `scripts/ci-parity.sh` builds).

4.6.0 R104: the Arno reader proof (`docs/dev/arno-reader-proof.json`) ships in the wheel, and `arno_readers` reads it
from there: without it an installed IVG found no proof and sent every algorithm, PageRank included, to its Owner.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PREFIX = "iris_vector_graph/iris_src/src/"
RETIRED = ("Graph/KG/Benchmark.cls", "Graph/KG/BenchFormat.cls", "User.Exec.cls", "iris/vector/graph/GraphOperators.cls")
SHIPPED = ("Graph/KG/Service.cls", "Graph/KG/FHIRGraphSyncTask.cls")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("uv is not on PATH; the wheel check needs it (scripts/ci-parity.sh builds with it too)")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run([uv, "build", "-q", "--wheel", "--out-dir", str(out)], cwd=REPO, check=True, timeout=600)
    (built,) = out.glob("*.whl")
    return built


@pytest.fixture(scope="module")
def wheel_names(wheel) -> set:
    with zipfile.ZipFile(wheel) as zf:
        return {n[len(PREFIX):] for n in zf.namelist() if n.startswith(PREFIX)}


def test_the_wheel_carries_the_arno_reader_proof(wheel):
    with zipfile.ZipFile(wheel) as zf:
        shipped = json.loads(zf.read("iris_vector_graph/_data/arno-reader-proof.json"))
    assert shipped == json.loads((REPO / "docs" / "dev" / "arno-reader-proof.json").read_text())


def test_an_installed_wheel_routes_pagerank_by_its_own_proof(wheel, tmp_path):
    """The wheel unpacked into a directory of its own (what an install puts in site-packages) and imported from there,
    with IVG_ARNO_PROOF unset: the proof it reads is the shipped one, PageRank's minimum reader revision 2 (the only
    algorithm the 4.6.0 proof lists), and WCC has none."""
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import iris_vector_graph.arno_readers as r, json; "
        "p = r.load_proof(); "
        "print(json.dumps([r.__file__, str(r._DEFAULT_PROOF), p.min_revision('pagerank'), p.min_revision('wcc')]))"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("IVG_ARNO_PROOF", "PYTHONPATH")}
    out = subprocess.run([sys.executable, "-I", "-c", probe, str(site)], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=120)
    assert out.returncode == 0, out.stderr
    module_file, proof_path, pagerank, wcc = json.loads(out.stdout.strip().splitlines()[-1])
    assert module_file.startswith(str(site)), module_file
    assert proof_path == str(site / "iris_vector_graph" / "_data" / "arno-reader-proof.json")
    assert (pagerank, wcc) == (2, None)


def test_the_test_setup_helper_is_not_in_the_wheel(wheel):
    """4.7.0: IVGDev.Setup (scripts/dev/objectscript, loaded into %SYS by scripts/dev/tcp_setup.py to make and drop
    scratch namespaces over TCP) is test tooling only."""
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert not [n for n in names if "IVGDev" in n or "tcp_setup" in n or "no-expire-passwords" in n], names


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
