"""4.7.0: `scripts/dev/tcp_setup.py` makes and drops scratch namespaces over TCP, with no `iris session`.

Cortex XDR kills a process that pipes ObjectScript into `iris session` (through `docker exec` or ssh), so the setup
the release gates need runs through the Native API and a helper class, `IVGDev.Setup`, loaded into %SYS over TCP.

Offline: a fake Native API handle (only the names `iris.IRIS` has: classMethodValue, classMethodObject, lock, unlock)
keeps a small model of IRIS's namespaces, databases, resources and tasks, and answers `IVGDev.Setup`'s methods from
it. A contract test reads the class source, so the fake cannot call a method the class lacks, or with too many
arguments. The live checks are in the 4.7.0 report (make and drop on ivg-iris-enterprise).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from tests.helpers.fake_setup_iris import FakeIRIS, class_methods

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dev" / "tcp_setup.py"
CLASS = REPO / "scripts" / "dev" / "objectscript" / "IVGDev" / "Setup.cls"


@pytest.fixture(scope="module")
def ts():
    spec = importlib.util.spec_from_file_location("tcp_setup_470", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake():
    return FakeIRIS(loaded_hash="stale")


# ── the helper class ───────────────────────────────────────────────────────────


def test_every_method_the_module_calls_is_in_the_class(ts):
    """The module names IVGDev.Setup's methods as strings; each must be one the class source defines."""
    called = set(re.findall(r'_call\(\s*\w+,\s*"(\w+)"', SCRIPT.read_text()))
    called |= set(re.findall(r'HELPER,\s*"(\w+)"', SCRIPT.read_text()))
    assert called, "found no IVGDev.Setup calls in tcp_setup.py"
    assert called <= set(class_methods()), called - set(class_methods())


def test_the_class_lives_outside_the_shipped_sources(ts):
    assert ts.HELPER_SOURCE == CLASS
    assert "iris_src" not in CLASS.parts
    assert re.search(r"^Class IVGDev\.Setup\b", CLASS.read_text(), re.M)


def test_the_class_refuses_to_delete_a_name_not_starting_ivg():
    text = CLASS.read_text()
    for method in ("DeleteNamespace", "DeleteDatabase"):
        body = text.split(f"ClassMethod {method}(", 1)[1].split("\n}\n", 1)[0]
        assert 'If $Extract(pName, 1, 3) \'= "IVG"' in body, method


def test_the_helper_loads_when_absent(ts):
    native = FakeIRIS(loaded_hash=None)
    assert ts.ensure_helper(native) is True
    assert native.loaded_hash == ts.source_hash()
    assert native.loads == ["ck-d"]
    assert native.locks == [("lock", "^IVGDev.Setup"), ("unlock", "^IVGDev.Setup")]


def test_the_helper_reloads_when_its_source_changed(ts, fake):
    assert ts.ensure_helper(fake) is True
    assert fake.loaded_hash == ts.source_hash()


def test_the_helper_is_not_reloaded_when_current(ts):
    native = FakeIRIS(loaded_hash=None)
    native.loaded_hash = ts.source_hash()
    assert ts.ensure_helper(native) is False
    assert native.loads == []


def test_the_source_hash_is_stamped_into_the_loaded_text(ts):
    text = ts.helper_source()
    assert f'Parameter SOURCEHASH = "{ts.source_hash()}";' in text
    assert len(ts.source_hash()) >= 16


# ── make ───────────────────────────────────────────────────────────────────────


def test_make_one_database(ts, fake):
    assert ts.make_namespace(fake, "IVG470SU1") is True
    assert fake.namespaces == {"IVG470SU1": ("IVG470SU1", "IVG470SU1")}
    assert fake.databases == {"IVG470SU1": "/usr/irissys/mgr/ivg470su1/"}
    assert fake.resources == set()


def test_make_split_databases_on_resources_of_their_own(ts, fake):
    dbs = {"IVG470SPD": "%DB_IVG470SPD", "IVG470SPR": "%DB_IVG470SPR"}
    assert ts.make_namespace(fake, "IVG470SP", globals_db="IVG470SPD", routines_db="IVG470SPR", resources=dbs) is True
    assert fake.namespaces == {"IVG470SP": ("IVG470SPD", "IVG470SPR")}
    assert set(fake.databases) == {"IVG470SPD", "IVG470SPR"}
    assert fake.resources == {"%DB_IVG470SPD", "%DB_IVG470SPR"}
    creates = [c for c in fake.calls if c[0] == "CreateDatabase"]
    assert ("CreateDatabase", "IVG470SPD", "/usr/irissys/mgr/ivg470spd/", "%DB_IVG470SPD") in creates


def test_make_leaves_an_existing_namespace(ts, fake):
    fake.namespaces["IVG470SU1"] = ("USER", "USER")
    assert ts.make_namespace(fake, "IVG470SU1") is False
    assert [c[0] for c in fake.calls] == ["NamespaceExists"]


def test_a_failed_step_raises_naming_it(ts, fake):
    fake.fail["CreateDatabase"] = "FAIL cfgdb: ERROR #1"
    with pytest.raises(RuntimeError, match="IVG470SU1.*FAIL cfgdb: ERROR #1"):
        ts.make_namespace(fake, "IVG470SU1")
    assert "IVG470SU1" not in fake.namespaces


# ── drop ───────────────────────────────────────────────────────────────────────


def test_drop_removes_tasks_namespace_and_its_own_database(ts, fake):
    ts.make_namespace(fake, "IVG470SU1")
    fake.tasks.update({"7": "IVG470SU1", "8": "OTHER"})
    assert ts.drop_namespace(fake, "IVG470SU1", own=("IVG470SU1",)) is True
    assert fake.namespaces == {} and fake.databases == {}
    assert fake.tasks == {"8": "OTHER"}
    order = [c[0] for c in fake.calls if c[0].startswith("Delete")]
    assert order == ["DeleteTask", "DeleteNamespace", "DeleteDatabase"]
    assert ("DeleteDatabase", "IVG470SU1", 0) in fake.calls  # the namespace's own name: no resource of its own


def test_drop_split_deletes_both_databases_and_their_resources(ts, fake):
    dbs = {"IVG470SPD": "%DB_IVG470SPD", "IVG470SPR": "%DB_IVG470SPR"}
    ts.make_namespace(fake, "IVG470SP", globals_db="IVG470SPD", routines_db="IVG470SPR", resources=dbs)
    assert ts.drop_namespace(fake, "IVG470SP", own=("IVG470SP", "IVG470SPD", "IVG470SPR")) is True
    assert fake.databases == {} and fake.resources == set()
    assert ("DeleteDatabase", "IVG470SPD", 1) in fake.calls and ("DeleteDatabase", "IVG470SPR", 1) in fake.calls


def test_drop_never_deletes_a_database_it_did_not_make(ts, fake):
    fake.namespaces["IVG470MAP"] = ("USER", "IVG470MAP")
    fake.databases.update({"USER": "/usr/irissys/mgr/user/", "IVG470MAP": "/usr/irissys/mgr/ivg470map/"})
    assert ts.drop_namespace(fake, "IVG470MAP", own=("IVG470MAP",)) is True
    assert fake.databases == {"USER": "/usr/irissys/mgr/user/"}
    assert not any(c[0] == "DeleteDatabase" and c[1] == "USER" for c in fake.calls)


def test_drop_of_an_absent_namespace(ts, fake):
    assert ts.drop_namespace(fake, "IVG470SU1", own=("IVG470SU1",)) is False
    assert [c[0] for c in fake.calls] == ["NamespaceExists"]


def test_a_failed_task_delete_stops_the_drop(ts, fake):
    ts.make_namespace(fake, "IVG470SU1")
    fake.tasks["7"] = "IVG470SU1"
    fake.fail["DeleteTask"] = "FAIL task: locked"
    with pytest.raises(RuntimeError, match="FAIL task: locked"):
        ts.drop_namespace(fake, "IVG470SU1", own=("IVG470SU1",))
    assert "IVG470SU1" in fake.namespaces


def test_unexpire(ts, fake):
    ts.unexpire_passwords(fake)
    assert fake.calls[-1] == ("UnExpireUserPasswords", "*")


# ── connection ─────────────────────────────────────────────────────────────────


def test_connects_to_sys_on_the_test_port_by_tcp(ts, monkeypatch):
    seen = {}

    class Conn:
        def close(self):
            seen["closed"] = True

    fake_iris = type(sys)("iris")
    fake_iris.createConnection = lambda *a, **k: seen.update(args=a, kwargs=k) or Conn()
    fake_iris.createIRIS = lambda conn: FakeIRIS(loaded_hash=ts.source_hash())
    monkeypatch.setitem(sys.modules, "iris", fake_iris)
    monkeypatch.setenv("IVG_PORT", "31972")
    with ts.sys_native() as native:
        assert isinstance(native, FakeIRIS)
    assert seen["kwargs"]["namespace"] == "%SYS"
    assert seen["kwargs"]["port"] == 31972 and seen["kwargs"]["hostname"] == "127.0.0.1"
    assert seen["closed"]


def test_cli_make_has_no_prefix_rule_but_refuses_user(ts, monkeypatch):
    """setup_namespace.sh --create (any alphanumeric name but USER and %SYS) calls `tcp_setup.py make NS`."""
    made = []
    monkeypatch.setattr(ts, "sys_native", _fake_ctx(FakeIRIS(loaded_hash="x")))
    monkeypatch.setattr(ts, "make_namespace", lambda native, ns: made.append(ns) or True)
    assert ts.main(["make", "TCKA"]) == 0
    assert ts.main(["make", "USER"]) == 2
    assert ts.main(["make", "%SYS"]) == 2
    assert ts.main(["make", "bad-name"]) == 2
    assert made == ["TCKA"]


def test_setup_namespace_sh_creates_over_tcp():
    """scripts/tck/setup_namespace.sh --create runs `tcp_setup.py make NS` with the repo's interpreter, not a
    session; its interface (NS, --create, USER refused) is unchanged."""
    text = (REPO / "scripts" / "tck" / "setup_namespace.sh").read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "iris session" not in code and "<<EOF" not in code
    assert '"$py" "$repo/scripts/dev/tcp_setup.py" make "$ns"' in code
    assert 'case "$ns" in USER|%SYS|"")' in code


def _fake_ctx(native):
    import contextlib

    @contextlib.contextmanager
    def ctx(*a, **k):
        yield native

    return ctx
