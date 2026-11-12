"""R148: `scripts/dev/make_scratch_namespace.py` name rules, `--no-install`, and installing from its own checkout.

Offline: the name rules are pure, `main` is driven with `make`/`drop`/`install_ivg` replaced, and the checkout check
starts a real interpreter with the environment the install uses and asks it where `iris_vector_graph` comes from.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dev" / "make_scratch_namespace.py"


@pytest.fixture(scope="module")
def msn():
    spec = importlib.util.spec_from_file_location("make_scratch_namespace_r148", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── names ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ns", ["IVG262ALT", "IVG262FX8", "IVG460FXA", "IVG460DC1", "IVG460INV", "IVG460DOC", "IVG463A", "IVG470A"])
def test_creatable(msn, ns):
    assert msn.may_create(ns)


@pytest.mark.parametrize("ns", ["USER", "IVGTEST", "IVGLEGACY", "ivg460fxa", "Ivg262ALT", "IVG26", "", "%SYS"])
def test_not_creatable(msn, ns):
    assert not msn.may_create(ns)


@pytest.mark.parametrize(
    "ns", ["IVG262ALT", "IVG262UPG", "IVG460INV", "IVG460FX", "IVG460FXA", "IVG460FX15", "IVG460DC", "IVG460DC1",
           "IVG460DC3", "IVG463A", "IVG463B", "IVG470A", "IVG470RPT"],
)
def test_droppable(msn, ns):
    assert msn.may_drop(ns)


@pytest.mark.parametrize(
    "ns",
    [
        "IVG460DOC",  # tests/docs/docs.toml's namespace: the doc harness's baseline lives there
        "IVG460ALT",  # tests/e2e/ns460alt.py's default
        "IVG460R072",
        "IVG460",
        "IVG460F",
        "IVG460D",
        "ivg460fxa",
        "ivg460inv",
        "Ivg460DC1",
        "USER",
        "%SYS",
        "IVGTEST",
        "IVGLEGACY",
        "",
    ],
)
def test_not_droppable(msn, ns):
    assert not msn.may_drop(ns)
    with pytest.raises(ValueError, match="refusing to drop"):
        msn.drop(ns)


def test_the_doc_harness_namespace_is_not_droppable(msn, monkeypatch):
    """Whatever docs.toml names (the shared doc-run namespace) the script never drops."""
    from tests.docs import harness

    monkeypatch.delenv("IVG_DOCTEST_NAMESPACE", raising=False)
    assert harness.load_config().namespace == "IVG460DOC"
    assert not msn.may_drop(harness.load_config().namespace)


# ── main ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def calls(msn, monkeypatch):
    seen: list = []
    monkeypatch.setattr(msn, "make", lambda ns: seen.append(("make", ns)) or True)
    monkeypatch.setattr(msn, "drop", lambda ns: seen.append(("drop", ns)) or True)
    monkeypatch.setattr(msn, "install_ivg", lambda ns: seen.append(("install", ns)))
    return seen


def test_main_installs_by_default(msn, calls):
    assert msn.main(["IVG460FXA"]) == 0
    assert calls == [("make", "IVG460FXA"), ("install", "IVG460FXA")]


def test_main_no_install(msn, calls):
    assert msn.main(["--no-install", "IVG460FXA", "IVG460FXB"]) == 0
    assert calls == [("make", "IVG460FXA"), ("make", "IVG460FXB")]


def test_main_no_install_after_names(msn, calls):
    assert msn.main(["IVG460FXA", "--no-install"]) == 0
    assert calls == [("make", "IVG460FXA")]


def test_main_refuses_a_bad_name_before_creating_anything(msn, calls):
    assert msn.main(["IVG460FXA", "USER"]) == 2
    assert calls == []


def test_main_drop(msn, calls):
    assert msn.main(["--drop", "IVG460FXA", "IVG460DC2"]) == 0
    assert calls == [("drop", "IVG460FXA"), ("drop", "IVG460DC2")]


def test_main_drop_refuses_before_dropping_anything(msn, calls):
    assert msn.main(["--drop", "IVG460FXA", "IVG460DOC"]) == 2
    assert calls == []


def test_main_no_names(msn, calls):
    assert msn.main([]) == 2
    assert msn.main(["--drop"]) == 2
    assert calls == []


# ── install source ─────────────────────────────────────────────────────────────


def test_install_runs_this_checkouts_package(msn, tmp_path):
    """The interpreter the install starts imports `iris_vector_graph` from the checkout the script is in, whatever
    the venv's editable install points at, and whatever directory the caller is in."""
    cmd, env, cwd = msn.install_command("IVG460FXA")
    assert Path(cwd) == REPO
    probe = subprocess.run(
        [sys.executable, "-c", "import iris_vector_graph; print(iris_vector_graph.__file__)"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, check=True,
    )
    assert Path(probe.stdout.strip()).resolve() == (REPO / "iris_vector_graph" / "__init__.py").resolve()
    assert env["IVG_NAMESPACE"] == "IVG460FXA"


def test_install_deploys_this_checkouts_classes(msn):
    """tcp-deploy globs `iris_src/src` relative to its working directory and is found by an absolute path."""
    deploy, _env, cwd = msn.deploy_command("IVG460FXA")
    assert Path(deploy[0]) == REPO / "scripts" / "enterprise-container.sh"
    assert deploy[1:] == ["tcp-deploy"]
    assert Path(cwd) == REPO


# ── make and drop over TCP (4.7.0: no `iris session`) ─────────────────────────


@pytest.fixture
def sysiris(msn, monkeypatch):
    """msn's TCP handle replaced by the fake %SYS (tests/helpers/fake_setup_iris.py)."""
    import contextlib

    from tests.helpers.fake_setup_iris import FakeIRIS

    native = FakeIRIS(loaded_hash="current")

    @contextlib.contextmanager
    def ctx(*a, **k):
        yield native

    monkeypatch.setattr(msn.tcp_setup, "sys_native", ctx)
    return native


def test_make_and_drop_go_through_tcp_setup_not_a_session(msn):
    text = SCRIPT.read_text()
    assert '"iris", "session"' not in text and "docker" not in text.split('"""', 2)[2].split("def _env")[0]
    assert Path(msn.tcp_setup.__file__) == REPO / "scripts" / "dev" / "tcp_setup.py"


def test_make_creates_namespace_on_a_database_of_its_own(msn, sysiris):
    assert msn.make("IVG470SU1") is True
    assert sysiris.namespaces == {"IVG470SU1": ("IVG470SU1", "IVG470SU1")}
    assert sysiris.databases == {"IVG470SU1": "/usr/irissys/mgr/ivg470su1/"}
    assert msn.make("IVG470SU1") is False


def test_make_split(msn, sysiris):
    assert msn.make("IVG470SP", split=True) is True
    assert sysiris.namespaces == {"IVG470SP": ("IVG470SPD", "IVG470SPR")}
    assert sysiris.resources == {"%DB_IVG470SPD", "%DB_IVG470SPR"}


def test_drop_removes_what_make_made_and_its_tasks(msn, sysiris):
    msn.make("IVG470SP", split=True)
    sysiris.tasks["3"] = "IVG470SP"
    assert msn.drop("IVG470SP") is True
    assert sysiris.namespaces == {} and sysiris.databases == {} and sysiris.resources == set()
    assert sysiris.tasks == {}
    assert msn.drop("IVG470SP") is False


def test_drop_leaves_a_mapped_database_it_did_not_make(msn, sysiris):
    sysiris.namespaces["IVG470MAP"] = ("USER", "USER")
    sysiris.databases["USER"] = "/usr/irissys/mgr/user/"
    assert msn.drop("IVG470MAP") is True
    assert sysiris.databases == {"USER": "/usr/irissys/mgr/user/"}


def test_drop_refuses_before_connecting(msn, monkeypatch):
    monkeypatch.setattr(msn.tcp_setup, "sys_native", lambda *a, **k: pytest.fail("connected"))
    with pytest.raises(ValueError, match="refusing to drop"):
        msn.drop("IVG460DOC")
