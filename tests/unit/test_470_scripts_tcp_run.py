"""4.7.0: the scripts that ran ObjectScript through `iris session` run it over TCP (scripts/dev/tcp_setup.py).

Cortex XDR kills any process that pipes ObjectScript into `iris session`, so each script's session helper now goes
through `tcp_setup.session` / `tcp_setup.run` (or, for a namespace or a class load, the Native API). Offline: each
helper is called with a fake runner and checked for what it sends (lines, namespace, port, timeout) and what it makes
of the answer. tests/unit/test_no_iris_session_pipes.py checks that none of them pipes any more.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Recorder:
    """Stands in for tcp_setup.session / tcp_setup.run: records each call, answers `output`."""

    def __init__(self, output="", failures=(), state="ended"):
        self.calls, self.output, self.failures, self.state = [], output, list(failures), state

    def session(self, lines, namespace, **kwargs):
        self.calls.append((lines, namespace, kwargs))
        return self.output

    def run(self, lines, namespace, **kwargs):
        self.calls.append((lines, namespace, kwargs))
        import tcp_setup

        return tcp_setup.RunResult(self.output, self.state, self.failures)


# --------------------------------------------------------------------------- scripts/bench/alt/setup_sides.py


@pytest.fixture
def ss():
    from scripts.bench.alt import setup_sides

    return setup_sides


def test_setup_sides_session_runs_the_script_over_tcp_on_the_bench_port(ss, monkeypatch):
    rec = Recorder("RESULT load OK\n")
    monkeypatch.setattr(ss.tcp_setup, "session", rec.session)
    assert ss.session("IVGBENCHA", 'Write "RESULT load OK",!\nHalt\n') == "RESULT load OK\n"
    lines, ns, kwargs = rec.calls[0]
    assert ns == "IVGBENCHA" and lines == 'Write "RESULT load OK",!\nHalt\n'
    assert kwargs["port"] == ss.sd.PORT and kwargs["timeout"] == 1800
    assert not kwargs.get("allow_sys")


class FakeSys:
    def __init__(self, exists):
        self.exists, self.made = exists, []

    @contextlib.contextmanager
    def sys_native(self, port=None):
        self.port = port
        yield "native"

    def make_namespace(self, native, ns):
        assert native == "native"
        self.made.append(ns)
        return not self.exists


@pytest.mark.parametrize("exists, said", [(False, "created"), (True, "exists")])
def test_create_namespace_makes_one_with_its_own_database_over_tcp(ss, monkeypatch, exists, said):
    fake = FakeSys(exists)
    monkeypatch.setattr(ss.tcp_setup, "sys_native", fake.sys_native)
    monkeypatch.setattr(ss.tcp_setup, "make_namespace", fake.make_namespace)
    assert ss.create_namespace("IVGBENCHA") == f"RESULT namespace IVGBENCHA {said}"
    assert fake.made == ["IVGBENCHA"] and fake.port == ss.sd.PORT


def test_create_namespace_refuses_a_foreign_namespace_before_connecting(ss, monkeypatch):
    monkeypatch.setattr(ss.tcp_setup, "sys_native", lambda *a, **k: pytest.fail("connected"))
    with pytest.raises(ValueError):
        ss.create_namespace("USER")


def test_setup_sides_never_deletes_a_namespace_or_database(ss):
    text = (REPO / "scripts" / "bench" / "alt" / "setup_sides.py").read_text()
    for forbidden in ("drop_namespace", "DeleteDatabase", "DeleteNamespace", "Config.Databases).Delete"):
        assert forbidden not in text


# --------------------------------------------------------------------------- scripts/bench/alt/snb_bulk.py


@pytest.fixture
def snb():
    from scripts.bench.alt import snb_bulk

    return snb_bulk


def test_snb_bulk_run_session_runs_over_tcp_and_echoes_its_progress_lines(snb, monkeypatch, capsys):
    rec = Recorder("PROGRESS 10\nnoise\nRESULT nodes 42\n")
    monkeypatch.setattr(snb.tcp_setup, "run", rec.run)
    lines = snb.run_session("SNB1A", 'Write "RESULT nodes ",42,!\nHalt\n', "nodes")
    assert lines == ["PROGRESS 10", "noise", "RESULT nodes 42"]
    assert snb.result_of(lines, "nodes") == "42"
    _, ns, kwargs = rec.calls[0]
    assert ns == "SNB1A" and kwargs["port"] == 31972
    assert kwargs["timeout"] >= 24 * 3600  # an SF10 load runs for hours
    out = capsys.readouterr().out
    assert "[nodes] PROGRESS 10" in out and "[nodes] RESULT nodes 42" in out and "noise" not in out


def test_snb_bulk_run_session_reports_a_line_that_raised(snb, monkeypatch, capsys):
    rec = Recorder("<UNDEFINED> *x\n", failures=[(1, "<UNDEFINED> *x")])
    monkeypatch.setattr(snb.tcp_setup, "run", rec.run)
    lines = snb.run_session("SNB1A", "Write x\n", "reset")
    assert lines[-1] == "ERROR line 1: <UNDEFINED> *x"
    assert "[reset] ERROR line 1: <UNDEFINED> *x" in capsys.readouterr().out


def test_snb_bulk_run_session_refuses_a_foreign_namespace(snb, monkeypatch):
    monkeypatch.setattr(snb.tcp_setup, "run", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(ValueError):
        snb.run_session("USER", "Write 1\n", "x")


# --------------------------------------------------------------------------- scripts/bench/nkg_swap/run_scale.py


@pytest.fixture
def scale():
    return _load("scripts/bench/nkg_swap/run_scale.py", "run_scale_470")


def test_run_scale_session_runs_in_the_lab_container_s_user_over_its_port(scale, monkeypatch):
    rec = Recorder('RESULT {"ok":1}\n')
    monkeypatch.setattr(scale.tcp_setup, "session", rec.session)
    lines = scale.session(41900, 'Write "RESULT ",{"ok":1}.%ToJSON(),!')
    assert scale.results(lines) == [{"ok": 1}]
    got_lines, ns, kwargs = rec.calls[0]
    assert ns == "USER" and kwargs["port"] == 41900 and kwargs["timeout"] == 3600
    assert got_lines == 'Write "RESULT ",{"ok":1}.%ToJSON(),!'


def test_run_scale_waits_for_the_lab_container_over_tcp(scale, monkeypatch):
    answers = iter([ConnectionRefusedError("not yet"), "READY\n"])

    def session(lines, ns, **kwargs):
        got = next(answers)
        if isinstance(got, Exception):
            raise got
        assert ns == "USER" and kwargs["port"] == 41900
        return got

    monkeypatch.setattr(scale.tcp_setup, "session", session)
    monkeypatch.setattr(scale.time, "sleep", lambda s: None)
    assert scale.wait_ready(41900) is True


# --------------------------------------------------------------------------- scripts/fixtures/build_release_namespace.py


def test_build_release_load_from_container_loads_the_staged_classes_over_tcp(monkeypatch):
    brn = _load("scripts/fixtures/build_release_namespace.py", "build_release_namespace_470")
    rec = Recorder("LOADED 1\n")
    monkeypatch.setattr(brn.tcp_setup, "session", rec.session)
    monkeypatch.setattr(brn, "_stage_release_src", lambda src, tag: "/tmp/ivg262_src_v3_2_0")
    brn._load_from_container(Path("/x"), "IVG470UPG", "v3.2.0")
    lines, ns, kwargs = rec.calls[0]
    assert ns == "IVG470UPG" and kwargs["port"] == brn.PORT and kwargs["timeout"] == 900 and kwargs["check"]
    assert 'LoadDir("/tmp/ivg262_src_v3_2_0","ck-d",,1)' in "\n".join(lines)


# --------------------------------------------------------------------------- scripts/deploy_objectscript.py


class FakeNative:
    """The Native API calls a class load makes: a stream written and loaded with LoadStream."""

    def __init__(self, status=1):
        self.status, self.loaded = status, []

    def classMethodObject(self, cls, method):
        assert (cls, method) == ("%Stream.GlobalCharacter", "%New")

        class Stream:
            text = ""

            def invoke(self, name, *args):
                if name == "Write":
                    self.text += args[0]

        self._stream = Stream()
        return self._stream

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
            self.loaded.append((args[0].text, args[1]))
            return self.status
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return "ERROR #5001: broken"
        raise AssertionError((cls, method))


def test_deploy_objectscript_loads_each_class_over_tcp(tmp_path):
    dob = _load("scripts/deploy_objectscript.py", "deploy_objectscript_470")
    a = tmp_path / "A.cls"
    a.write_text("Class A {}\n")
    native = FakeNative()
    assert dob.deploy_via_tcp(native, [a]) is True
    assert native.loaded == [("Class A {}\n", "ck")]


def test_deploy_objectscript_reports_a_class_that_did_not_load(tmp_path):
    dob = _load("scripts/deploy_objectscript.py", "deploy_objectscript_470b")
    a = tmp_path / "A.cls"
    a.write_text("Class A {}\n")
    assert dob.deploy_via_tcp(FakeNative(status="0 error"), [a]) is False


def test_deploy_objectscript_dry_run_loads_nothing(tmp_path):
    dob = _load("scripts/deploy_objectscript.py", "deploy_objectscript_470c")
    a = tmp_path / "A.cls"
    a.write_text("Class A {}\n")
    native = FakeNative()
    dob.deploy_via_tcp(native, [a], dry_run=True)
    assert native.loaded == []


# --------------------------------------------------------------------------- scripts/deploy_production.py


def test_deploy_production_full_mode_runs_the_sql_then_the_classes_over_tcp(monkeypatch):
    dp = _load("scripts/deploy_production.py", "deploy_production_470")
    calls = []
    monkeypatch.setattr(dp, "deploy_via_sql", lambda *a: calls.append(("sql", a)))
    monkeypatch.setattr(dp, "deploy_classes", lambda *a: calls.append(("classes", a)))
    monkeypatch.setattr(sys, "argv", ["deploy_production.py", "--host", "h", "--port", "1", "--namespace", "IVG470X"])
    dp.main()
    assert [c[0] for c in calls] == ["sql", "classes"]
    assert calls[0][1] == ("h", 1, "IVG470X", "_SYSTEM", "SYS", "User")
    assert calls[1][1] == ("h", 1, "IVG470X", "_SYSTEM", "SYS")


def test_deploy_production_docker_mode_is_the_full_deploy(monkeypatch):
    dp = _load("scripts/deploy_production.py", "deploy_production_470b")
    calls = []
    monkeypatch.setattr(dp, "deploy_via_sql", lambda *a: calls.append("sql"))
    monkeypatch.setattr(dp, "deploy_classes", lambda *a: calls.append("classes"))
    monkeypatch.setattr(sys, "argv", ["deploy_production.py", "--mode", "docker"])
    dp.main()
    assert calls == ["sql", "classes"]


def test_deploy_production_classes_raise_on_a_compile_error(monkeypatch):
    dp = _load("scripts/deploy_production.py", "deploy_production_470c")

    class Result:
        errors = ["Graph.KG.X: ERROR #5001"]

    class Conn:
        def close(self):
            self.closed = True

    conn = Conn()
    monkeypatch.setattr(dp, "_connect", lambda *a: conn)
    monkeypatch.setattr(dp, "_deploy_packaged_classes", lambda c: Result())
    with pytest.raises(RuntimeError, match="ERROR #5001"):
        dp.deploy_classes("h", 1, "IVG470X", "_SYSTEM", "SYS")
    assert conn.closed
