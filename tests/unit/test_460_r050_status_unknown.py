"""4.6.0 R050 round 2 (expert walkthrough W9): ``status().report()`` says it could not tell when a probe failed, rather
than "in sync" and "0 nodes", and a probe error names what it asked.

The walkthrough's two reports: on a namespace never set up (the first-access refusal, nothing probed) it printed
``0 nodes · 0 edges``, ``ObjectScript (Arno not loaded)`` and ``Sync state: in sync``; as a reader without the routines
database it printed ``in sync`` beside nine probe errors, one of them a bare ``adjacency probe failed: <SYNTAX>``. That
one was the fallback read ``isDefined(["KG", "out"])``: a list where the Native API takes the global's name, which the
server answers with ``<SYNTAX>`` (R199 fixed the same call in the predicate probe).
"""

from __future__ import annotations

from tests.unit.test_460_status_probes import _Registry, _native, _status

from iris_vector_graph.status import EngineStatus


def _lines(report: str) -> dict:
    out = {}
    for line in report.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and value.startswith(" "):
            out[key] = value.strip()
    return out


def test_a_status_that_could_not_look_reports_unknown():
    report = EngineStatus(unknown=["graph", "acceleration", "sync"], errors=["Namespace X has never been set up"]).report()
    lines = _lines(report)
    assert lines["Graph"].startswith("unknown") and "0 nodes" not in report
    assert lines["Acceleration"].startswith("unknown")
    assert lines["Sync state"].startswith("unknown") and "in sync" not in report
    assert "Namespace X has never been set up" in report


def test_a_status_that_looked_reports_what_it_saw():
    lines = _lines(EngineStatus().report())
    assert lines["Graph"].startswith("0 nodes") and lines["Sync state"] == "in sync"


def test_the_refused_namespace_probes_nothing_and_says_unknown(monkeypatch):
    from tests.unit.route_fakes_227 import engine_with

    engine = engine_with(_Registry(), embedding_dimension=4)
    monkeypatch.setattr(type(engine), "_status_schema",
                        lambda self: (None, "Namespace NEVERSET has never been set up for IVG", 0, None))
    status = engine.status()
    assert set(status.unknown) == {"graph", "acceleration", "sync"}
    assert "in sync" not in status.report()


def test_a_failed_row_count_makes_the_graph_unknown():
    registry = _Registry(fail_on={"COUNT(*) FROM Graph_KG.nodes": RuntimeError("[SQLCODE: <-99>]")})
    status, _ = _status(registry)
    assert "graph" in status.unknown
    assert _lines(status.report())["Graph"].startswith("unknown")


def _native_refusing_lists(**kwargs):
    native = _native(**kwargs)

    def is_defined(name, *subs):
        if not isinstance(name, str):
            raise RuntimeError("<SYNTAX>")  # the server's answer to a list where the global's name goes
        return 1

    native.isDefined.side_effect = is_defined
    return native


def test_the_adjacency_fallback_names_the_global_and_the_sync_state_is_unknown():
    status, _ = _status(native=_native_refusing_lists(fail={"KGEdgeCount": RuntimeError("<PROTECT> ^|^^/r/|Graph.KG")}))
    assert not [e for e in status.errors if "<SYNTAX>" in e], status.errors
    assert status.adjacency.kg_populated is True  # ^KG("out") answered
    assert "sync" in status.unknown
    assert _lines(status.report())["Sync state"].startswith("unknown")


def test_a_failed_fallback_says_what_it_read():
    native = _native(fail={"KGEdgeCount": RuntimeError("<PROTECT> a")})
    native.isDefined.side_effect = RuntimeError("<PROTECT> ^KG")
    status, _ = _status(native=native)
    assert any(e.startswith('adjacency probe failed (isDefined ^KG("out")): ') for e in status.errors), status.errors


def test_a_good_probe_leaves_nothing_unknown():
    status, _ = _status(native=_native(kg_edges=1))
    assert status.unknown == [], status.errors
