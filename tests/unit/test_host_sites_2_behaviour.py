"""Representative migrated sites, pinned on a scripted Native handle (package host-sites-2, T005).

Spec: specs/238-alt-host-sites-2/spec.md. These pin what the caller sees after a site moves onto the Host: the same
ObjectScript method and global are touched, with the same arguments, and the chunked reply is reassembled.
"""

from __future__ import annotations

import sys
import types

import pytest

from iris_vector_graph.engine import IRISGraphEngine


class ScriptedNative:
    """A Native API handle: class methods from a table, globals in a dict, ``nextSubscript`` from a script."""

    def __init__(self, replies=None, next_script=None):
        self.replies = replies or {}
        self.calls = []
        self.kills = []
        self.data = {}
        self.next_calls = []
        self.next_script = list(next_script or [])

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        reply = self.replies.get((cls, method))
        return reply(*args) if callable(reply) else reply

    def kill(self, name, *subs):
        self.kills.append((name, subs))

    def get(self, name, *subs):
        return self.data.get((name, subs))

    def nextSubscript(self, reverse, name, *subs):
        self.next_calls.append((reverse, name, subs))
        return self.next_script.pop(0)


@pytest.fixture
def native(monkeypatch):
    handle = ScriptedNative()
    fake = types.ModuleType("iris")
    fake.createIRIS = lambda conn: handle
    monkeypatch.setitem(sys.modules, "iris", fake)
    return handle


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = object()
    eng._connection_params = None
    eng._native_conn = None
    return eng


def test_neighborhood_drop_kills_the_nkg_scratch_global_through_the_host(native):
    _engine()._neighborhood_index_drop("idx")

    assert native.kills == [("^NKG", ("q",))]


def test_arno_call_reassembles_a_chunked_reply_through_the_host(native):
    cls = "Graph.KG.ArnoAccel"
    native.replies[(cls, "PPRJson")] = lambda *a: "CHUNKED:tag:2"
    native.replies[(cls, "ReadLargeOutChunk")] = lambda tag, i: ["ab", "cd"][i - 1]

    assert _engine()._arno_call(cls, "PPRJson", "x") == "abcd"
    assert native.calls[-2:] == [
        (cls, "ReadLargeOutChunk", ("tag", 1)),
        (cls, "ReadLargeOutChunk", ("tag", 2)),
    ]


def test_centrality_warnings_walk_the_warning_global_in_order(native):
    """The Native API takes the reverse flag first in ``nextSubscript``; the Host takes it as a keyword."""
    W = "^IVG.warnings"
    native.next_script = ["t1", "s1", None, None]
    native.data[(W, ("centrality", "t1", "s1"))] = "why"

    out = _engine().get_centrality_warnings()

    assert out == [{"timestamp": "t1", "source": "s1", "reason": "why"}]
    assert native.next_calls == [
        (False, W, ("centrality", "")),
        (False, W, ("centrality", "t1", "")),
        (False, W, ("centrality", "t1", "s1")),
        (False, W, ("centrality", "t1")),
    ]
