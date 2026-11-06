"""4.6.0 R050 (expert walkthrough W1): `make_scratch_namespace.py --split` makes a namespace whose routines are in a
database of their own, the data/code split many sites run, for the reading-role test on it
(tests/e2e/test_460_r202_reading_role_e2e.py).

Offline: the ObjectScript the script would run is captured, not run. The namespace `<NS>` gets the globals database
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
    spec = importlib.util.spec_from_file_location("make_scratch_namespace_r050", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ran: list = []

    def objectscript(lines):
        ran.append(lines)
        return "CREATED" if any("CREATED" in line for line in lines) else "DROPPED"

    monkeypatch.setattr(module, "_objectscript", objectscript)
    module.ran = ran
    return module


def test_split_databases_are_named_after_the_namespace(msn):
    assert msn.split_databases("IVG460FXS") == {"globals": ("IVG460FXSD", "%DB_IVG460FXSD"),
                                                "routines": ("IVG460FXSR", "%DB_IVG460FXSR")}


def test_a_split_namespace_maps_globals_and_routines_to_two_databases_with_their_own_resources(msn):
    assert msn.make("IVG460FXS", split=True) is True
    (lines,) = msn.ran
    text = "\n".join(lines)
    assert 'p("Globals")="IVG460FXSD",p("Routines")="IVG460FXSR"' in text
    for db in ("IVG460FXSD", "IVG460FXSR"):
        assert f'Security.Resources).Create("%DB_{db}"' in text
        assert f'db.ResourceName="%DB_{db}"' in text
        assert f'Config.Databases).Create("{db}"' in text
        assert f"/usr/irissys/mgr/{db.lower()}/" in text


def test_a_plain_namespace_is_still_one_database(msn):
    assert msn.make("IVG460FXA") is True
    text = "\n".join(msn.ran[0])
    assert 'p("Globals")="IVG460FXA",p("Routines")="IVG460FXA"' in text
    assert "Security.Resources" not in text


def test_drop_removes_the_databases_the_namespace_maps_and_only_those_make_names(msn):
    """The databases are read from the namespace's own configuration, so a plain namespace that happens to be called
    `IVG460FXSD` keeps its database when `IVG460FXS` is dropped."""
    assert msn.drop("IVG460FXS") is True
    text = "\n".join(msn.ran[0])
    assert 'Config.Namespaces).Get("IVG460FXS",.p)' in text
    assert 'set dbs=$lb(p("Globals"),p("Routines"))' in text
    assert '$lb("IVG460FXS","IVG460FXSD","IVG460FXSR")' in text  # the only names it deletes
    assert 'Security.Resources).Delete("%DB_"_db)' in text and '(db\'="IVG460FXS")' in text


def test_main_split(msn, monkeypatch):
    seen = []
    monkeypatch.setattr(msn, "make", lambda ns, split=False: seen.append(("make", ns, split)) or True)
    monkeypatch.setattr(msn, "install_ivg", lambda ns: seen.append(("install", ns)))
    assert msn.main(["--split", "IVG460FXS"]) == 0
    assert seen == [("make", "IVG460FXS", True), ("install", "IVG460FXS")]


def test_main_refuses_split_with_drop(msn, monkeypatch):
    monkeypatch.setattr(msn, "drop", lambda ns: pytest.fail("dropped"))
    assert msn.main(["--drop", "--split", "IVG460FXS"]) == 2
