"""4.6.0 R050 (expert walkthrough W1): `make_scratch_namespace.py --split` makes a namespace whose routines are in a
database of their own, the data/code split many sites run, for the reading-role test on it
(tests/e2e/test_460_r202_reading_role_e2e.py).

Offline: the script's TCP handle (scripts/dev/tcp_setup.py, 4.7.0) is a fake %SYS that keeps a model of IRIS
(tests/helpers/fake_setup_iris.py). The namespace `<NS>` gets the globals database
`<NS>D` and the routines database `<NS>R`, each with a resource of its own (`%DB_<NS>D`, `%DB_<NS>R`): the walkthrough
saw `<PROTECT>` because those two resources differ. `drop` removes what either kind of `make` made.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dev" / "make_scratch_namespace.py"


@pytest.fixture
def msn(monkeypatch):
    import contextlib

    from tests.helpers.fake_setup_iris import FakeIRIS

    spec = importlib.util.spec_from_file_location("make_scratch_namespace_r050", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.iris = FakeIRIS(loaded_hash="current")

    @contextlib.contextmanager
    def ctx(*a, **k):
        yield module.iris

    monkeypatch.setattr(module.tcp_setup, "sys_native", ctx)
    return module


def test_split_databases_are_named_after_the_namespace(msn):
    assert msn.split_databases("IVG460FXS") == {"globals": ("IVG460FXSD", "%DB_IVG460FXSD"),
                                                "routines": ("IVG460FXSR", "%DB_IVG460FXSR")}


def test_a_split_namespace_maps_globals_and_routines_to_two_databases_with_their_own_resources(msn):
    assert msn.make("IVG460FXS", split=True) is True
    assert msn.iris.namespaces == {"IVG460FXS": ("IVG460FXSD", "IVG460FXSR")}
    assert msn.iris.resources == {"%DB_IVG460FXSD", "%DB_IVG460FXSR"}
    for db in ("IVG460FXSD", "IVG460FXSR"):
        assert ("CreateDatabase", db, f"/usr/irissys/mgr/{db.lower()}/", f"%DB_{db}") in msn.iris.calls


def test_a_plain_namespace_is_still_one_database(msn):
    assert msn.make("IVG460FXA") is True
    assert msn.iris.namespaces == {"IVG460FXA": ("IVG460FXA", "IVG460FXA")}
    assert msn.iris.resources == set()
    assert not [c for c in msn.iris.calls if c[0] == "CreateResource"]


def test_drop_removes_the_databases_the_namespace_maps_and_only_those_make_names(msn):
    """The databases are read from the namespace's own configuration, so a plain namespace that happens to be called
    `IVG460FXSD` keeps its database when `IVG460FXS` is dropped."""
    msn.make("IVG460FXS", split=True)
    assert msn.drop("IVG460FXS") is True
    assert msn.iris.databases == {} and msn.iris.resources == set()
    msn.make("IVG460FXS")
    msn.make("IVG460FXSD")
    assert msn.drop("IVG460FXS") is True
    assert msn.iris.namespaces == {"IVG460FXSD": ("IVG460FXSD", "IVG460FXSD")}
    assert set(msn.iris.databases) == {"IVG460FXSD"}


def test_main_split(msn, monkeypatch):
    seen = []
    monkeypatch.setattr(msn, "make", lambda ns, split=False: seen.append(("make", ns, split)) or True)
    monkeypatch.setattr(msn, "install_ivg", lambda ns: seen.append(("install", ns)))
    assert msn.main(["--split", "IVG460FXS"]) == 0
    assert seen == [("make", "IVG460FXS", True), ("install", "IVG460FXS")]


def test_main_refuses_split_with_drop(msn, monkeypatch):
    monkeypatch.setattr(msn, "drop", lambda ns: pytest.fail("dropped"))
    assert msn.main(["--drop", "--split", "IVG460FXS"]) == 2
