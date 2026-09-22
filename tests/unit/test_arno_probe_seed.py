"""`_detect_arno`'s smoke probe must ask about a node the database has.

Spec 213 added the probe for a real reason: `Capabilities` can report
`rust_callout: true` from a compiled class while the `.so` will not load in this
server process, and half-failing later is worse than falling back to ObjectScript
up front. But the probe asked `BFSJson` about the literal `__ivg_arno_probe__`,
which no database contains, and `Graph.KG.NKGAccelTraversal.BFSJson` answers an
absent seed with a value `%DynamicArray.%FromJSON` cannot parse:

    <THROW> *%Exception.General Parsing error 3 Line 1 Offset 1

So the probe threw on every install, and `_detect_arno` concluded the callout was
unrunnable and disabled Arno — permanently, for a working library. Measured on
ivg-iris-enterprise: the same call with a seed that exists returns
`SORTED:94833_bfs`. Every Python caller was silently taking the ObjectScript path.

`Graph.KG.NKGAccel.GetFirstNKGNode` is the seed the benchmark harness and three
e2e files already use for exactly this reason. These tests mock `_iris_obj`, so
they need no container.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

REAL_SEED = "arno_0"

CAPS = (
    '{"rust_callout":true,"algorithms":["ppr"],'
    '"rust_algorithms":["pagerank","wcc","cdlp","bfs"],"bfs":true,"nkg_data":true}'
)

#: What BFSJson raises for a seed that is not in ^NKG — the callout ran.
ABSENT_SEED_THROW = "<THROW> *%Exception.General Parsing error 3 Line 1 Offset 1 "

#: What it raises when the library itself cannot be used — the callout did not run.
UNLOADABLE_THROW = "<DYNAMIC LIBRARY LOAD>"


def _make_store() -> IRISGraphStore:
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = None
    store._arno_capabilities = {}
    store._nkg_dirty = False
    return store


def _iris_obj(*, first_node=REAL_SEED, bfs=None):
    """An iris_obj that looks like a healthy enterprise container.

    `bfs` is called with the seed BFSJson was given, so a test can make the
    outcome depend on which seed the probe chose.
    """
    calls = []

    def side_effect(cls, method, *args):
        calls.append((cls, method, args))
        if cls == "%Dictionary.CompiledClass" and method == "%ExistsId":
            return 1
        if method == "IsAvailable":
            return 1
        if method == "Load":
            return 1
        if method == "GetLibPath":
            return "/tmp/libarno_callout.so"
        if method == "Capabilities":
            return CAPS
        if method == "GetFirstNKGNode":
            return first_node
        if method == "BFSJson":
            return bfs(args[0]) if bfs else "[]"
        return ""

    obj = MagicMock()
    obj.classMethodValue.side_effect = side_effect
    return obj, calls


def _bfs_seeds(calls):
    return [args[0] for cls, method, args in calls if method == "BFSJson"]


def test_the_probe_seeds_from_a_node_the_database_has():
    store = _make_store()
    obj, calls = _iris_obj()

    with patch.object(store, "_iris_obj", return_value=obj):
        store._detect_arno()

    assert _bfs_seeds(calls) == [REAL_SEED], (
        "the smoke probe asked BFSJson about a node the database does not have, "
        f"so it can only ever throw: {_bfs_seeds(calls)}"
    )


def test_a_working_callout_stays_enabled():
    """The whole point: a library that runs must not be disabled.

    BFSJson here throws for any seed except the one `^NKG` actually holds, which
    is what the enterprise container does.
    """
    store = _make_store()

    def bfs(seed):
        if seed != REAL_SEED:
            raise RuntimeError(ABSENT_SEED_THROW)
        return "SORTED:94833_bfs"

    obj, _ = _iris_obj(bfs=bfs)

    with patch.object(store, "_iris_obj", return_value=obj):
        assert store._detect_arno() is True, (
            "a runnable callout was reported unrunnable, so every BFS, PageRank "
            "and WCC silently falls back to ObjectScript"
        )
    assert store._arno_capabilities.get("bfs") is True


def test_an_unrunnable_callout_is_still_disabled():
    """Spec 213's protection has to survive the fix."""
    store = _make_store()

    def bfs(seed):
        raise RuntimeError(UNLOADABLE_THROW)

    obj, _ = _iris_obj(bfs=bfs)

    with patch.object(store, "_iris_obj", return_value=obj):
        assert store._detect_arno() is False
    assert store._arno_capabilities == {}


def test_an_empty_nkg_is_not_treated_as_a_broken_callout():
    """With no node to ask about, there is nothing to smoke and nothing to blame.

    `Capabilities` already reaches the library: `rust_callout` comes from
    `ArnoAccel.IsAvailable`, which resolves and calls the `.so`'s `version`
    function through `$ZF(-5)`. An unpopulated `^NKG` is reported on its own
    terms by `nkg_data`, and disabling Arno over it would mean a freshly loaded
    database could never turn acceleration on.
    """
    store = _make_store()
    obj, calls = _iris_obj(first_node="")

    with patch.object(store, "_iris_obj", return_value=obj):
        assert store._detect_arno() is True

    assert _bfs_seeds(calls) == [], "BFSJson was probed with no seed to probe with"
