"""4.6.0: the FHIR E2E session says why it cannot set up, once.

Every FHIR E2E file shares one session fixture (`tests/e2e/fhir_conftest._fhir_session`). When its setup raised, pytest
re-raised the same exception in the setup of every test that used it: about 200 errors, and the reason was at the end
of a run that, on 2026-11-04, never reached the end. On a fresh container the namespace does not exist at all, and the
`fhir_conn_required` files (233) errored there too, where the rest skipped.

`open_session` probes in order and answers one of four states; `consumer_outcome` is what a consuming fixture does
with it. No IRIS here: each probe is a callable the test supplies.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import fhir_conftest as fc


class _Conn:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


def _open(**over):
    calls = []
    sys_conn, ns_conn = _Conn("%SYS"), _Conn(fc.NAMESPACE)

    def connect_sys():
        calls.append("connect_sys")
        return sys_conn

    def connect_ns():
        calls.append("connect_ns")
        return ns_conn

    def exists(conn):
        calls.append(("exists", conn.name))
        return True

    def endpoint(conn):
        calls.append(("endpoint", conn.name))
        return None

    def setup(conn):
        calls.append(("setup", conn.name))

    probes = dict(connect_sys=connect_sys, connect_ns=connect_ns, namespace_exists=exists,
                  endpoint_problem=endpoint, setup=setup)
    probes.update(over)
    session = fc.open_session(**probes)
    return session, calls, sys_conn, ns_conn


def test_a_ready_namespace_answers_its_connection_after_setup():
    session, calls, sys_conn, ns_conn = _open()
    assert session.state == fc.READY
    assert session.conn is ns_conn
    assert calls == ["connect_sys", ("exists", "%SYS"), "connect_ns", ("endpoint", "IVGFHIR"), ("setup", "IVGFHIR")]
    assert sys_conn.closed and not ns_conn.closed


def test_no_namespace_is_absent_and_names_the_recipe():
    session, calls, sys_conn, _ = _open(namespace_exists=lambda conn: False)
    assert session.state == fc.ABSENT
    assert session.conn is None
    assert "IVGFHIR" in session.reason and "CONTRIBUTING.md" in session.reason
    assert "connect_ns" not in calls  # a missing namespace answers Access Denied, which reads like a password fault
    assert sys_conn.closed


def test_an_unreachable_instance_is_unreachable():
    def refuse():
        raise RuntimeError("<COMMUNICATION LINK ERROR> Failed to connect to server")

    session, calls, _, _ = _open(connect_sys=refuse)
    assert session.state == fc.UNREACHABLE
    assert "COMMUNICATION LINK ERROR" in session.reason
    assert calls == []


def test_a_namespace_without_the_endpoint_is_absent_and_set_up_is_not_attempted():
    session, calls, _, ns_conn = _open(endpoint_problem=lambda conn: "IVGFHIR has no FHIR server")
    assert session.state == fc.ABSENT
    assert "IVGFHIR has no FHIR server" in session.reason and "CONTRIBUTING.md" in session.reason
    assert not any(c[0] == "setup" for c in calls if isinstance(c, tuple))
    assert ns_conn.closed


def test_a_failing_setup_is_broken_and_says_which_step_and_why():
    def setup(conn):
        raise fc.SetupStepError("initialize_schema", ValueError("Namespace IVGFHIR records RAG as its IVG Schema"))

    session, _, _, ns_conn = _open(setup=setup)
    assert session.state == fc.BROKEN
    assert session.conn is None and ns_conn.closed
    assert "initialize_schema" in session.reason
    assert "Namespace IVGFHIR records RAG as its IVG Schema" in session.reason
    assert "ValueError" in session.reason


def test_a_failing_setup_keeps_the_traceback_of_the_real_error():
    def boom():
        raise KeyError("missing")

    def setup(conn):
        try:
            boom()
        except KeyError as exc:
            raise fc.SetupStepError("reset_repository", exc) from exc

    session, _, _, _ = _open(setup=setup)
    assert "in boom" in session.detail and "KeyError" in session.detail


def test_endpoint_problem_reports_a_missing_fhir_server_table():
    class Cur:
        def execute(self, sql, params=None):
            raise RuntimeError("[SQLCODE: <-30>:<Table or view not found>]")

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

    assert "no FHIR server" in fc.endpoint_problem(Conn())


def test_endpoint_problem_reports_a_missing_endpoint_row():
    class Cur:
        def execute(self, sql, params=None):
            self.params = params

        def fetchone(self):
            return (0,)

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

    assert fc.ENDPOINT in fc.endpoint_problem(Conn())


def test_endpoint_problem_lets_another_sql_error_raise():
    class Cur:
        def execute(self, sql, params=None):
            raise RuntimeError("[SQLCODE: <-99>:<Privilege violation>]")

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

    with pytest.raises(RuntimeError, match="-99"):
        fc.endpoint_problem(Conn())


# ------------------------------------------------------------------ what a consuming fixture does


def _session(state, reason="why", detail="Traceback ...\nValueError: why"):
    return fc.FhirSession(conn=object() if state == fc.READY else None, state=state, reason=reason, detail=detail)


@pytest.mark.parametrize("required", [False, True])
def test_absent_skips_both_fixtures_as_on_a_fresh_container(required):
    action, message = fc.consumer_outcome(_session(fc.ABSENT, "no IVGFHIR"), required=required,
                                          skip_iris_tests=False, reported=set())
    assert action == "skip" and "no IVGFHIR" in message


def test_unreachable_skips_fhir_conn():
    assert fc.consumer_outcome(_session(fc.UNREACHABLE), required=False, skip_iris_tests=False,
                               reported=set())[0] == "skip"


def test_unreachable_fails_fhir_conn_required_unless_skip_iris_tests():
    assert fc.consumer_outcome(_session(fc.UNREACHABLE), required=True, skip_iris_tests=False,
                               reported=set())[0] == "fail"
    assert fc.consumer_outcome(_session(fc.UNREACHABLE), required=True, skip_iris_tests=True,
                               reported=set())[0] == "skip"


@pytest.mark.parametrize("required", [False, True])
def test_broken_fails_the_first_consumer_with_the_traceback_and_skips_the_rest_pointing_at_it(required):
    session = _session(fc.BROKEN, "IVGFHIR setup failed in deploy: compile errors", "Traceback ...\nAssertionError")
    reported: set = set()
    first = fc.consumer_outcome(session, required=required, skip_iris_tests=False, reported=reported)
    second = fc.consumer_outcome(session, required=required, skip_iris_tests=False, reported=reported)
    assert first[0] == "fail"
    assert "compile errors" in first[1] and "Traceback" in first[1]
    assert second[0] == "skip"
    assert "compile errors" in second[1] and "first" in second[1]


def test_broken_fails_even_under_skip_iris_tests():
    assert fc.consumer_outcome(_session(fc.BROKEN), required=False, skip_iris_tests=True,
                               reported=set())[0] == "fail"


def test_ready_answers_the_connection():
    session = _session(fc.READY)
    assert fc.consumer_outcome(session, required=True, skip_iris_tests=False, reported=set()) == ("conn", "")


def test_setup_runs_deploy_then_schema_then_reset():
    from tests.unit import _code

    t = _code.tokens(fc.setup_namespace)
    assert t.index("deploy") < t.index("initialize_schema") < t.index("reset_repository")


# ------------------------------------------------------------------ the per-test hook


class _Item:
    def __init__(self, *names):
        self.fixturenames = list(names)


def _with_session(monkeypatch, session):
    monkeypatch.setattr(fc, "_SESSION", [session])
    monkeypatch.setattr(fc, "_REPORTED", set())
    monkeypatch.setenv("SKIP_IRIS_TESTS", "false")


def test_hook_leaves_a_test_without_the_fhir_session_alone(monkeypatch):
    _with_session(monkeypatch, _session(fc.BROKEN))
    fc.pytest_runtest_setup(_Item("iris_connection"))  # no raise


def test_hook_fails_the_first_fhir_test_of_a_broken_session_and_skips_the_next(monkeypatch):
    _with_session(monkeypatch, _session(fc.BROKEN, "IVGFHIR setup failed in deploy: x"))
    with pytest.raises(pytest.fail.Exception, match="setup failed in deploy"):
        fc.pytest_runtest_setup(_Item("fhir_conn", "_fhir_session"))
    with pytest.raises(pytest.skip.Exception, match="setup failed in deploy"):
        fc.pytest_runtest_setup(_Item("fhir_conn_required", "_fhir_session"))


def test_hook_skips_a_required_fhir_test_on_a_container_without_ivgfhir(monkeypatch):
    _with_session(monkeypatch, _session(fc.ABSENT, "no IVGFHIR namespace"))
    with pytest.raises(pytest.skip.Exception, match="no IVGFHIR namespace"):
        fc.pytest_runtest_setup(_Item("genomics_loaded", "fhir_conn_required", "_fhir_session"))


def test_hook_lets_a_ready_session_through(monkeypatch):
    _with_session(monkeypatch, _session(fc.READY))
    fc.pytest_runtest_setup(_Item("fhir_conn_required", "_fhir_session"))


def test_e2e_conftest_registers_the_hook():
    import tests.e2e.conftest as e2e

    assert e2e.pytest_runtest_setup is fc.pytest_runtest_setup


# ------------------------------------------------------------------ deploy order (4.6.0 R084)


def test_deploy_loads_the_sql_procedure_forwarder_last():
    """Graph.KG.SqlProcs projects every SQL procedure the code classes projected before R084. Loaded in name order it
    compiled before Traversal, TemporalIndex and the rest after S, whose old copies in an upgraded IVGFHIR still
    projected DijkstraPath and backfill_temporal_rows: ERROR #5083, and every FHIR test errored at setup (e2e-full-3).
    tcp-deploy already loads it last."""
    order = [os.path.relpath(path, base).replace(os.sep, "/") for path, base in fc.deploy_sources()]
    assert order[-1] == "Graph/KG/SqlProcs.cls"
    assert order.count("Graph/KG/SqlProcs.cls") == 1
    assert "Graph/KG/Traversal.cls" in order and "IVGTest/FHIRLoad.cls" in order
    code = [rel for rel in order[:-1] if not rel.startswith("IVGTest/")]
    assert code == sorted(code)  # the rest keep their name order
