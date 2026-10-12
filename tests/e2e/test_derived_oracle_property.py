"""Spike R9, as a live property test: a seeded random write sequence, the derived-store oracle after every step.

Operations, drawn from ``random.Random(seed)`` (hypothesis is not in the dev extra):

* insert: ``engine.create_edge`` (the real write path, which commits itself)
* delete: ``engine.delete_edge``
* rollback after insert: one adjacency write inside a Native transaction that is rolled back
* failed batch: two adjacency writes, a simulated failure, then rollback of both

The adjacency stores (``out``, ``in``) must agree with the rows after every step, in every mode. The advisory counters
(``deg``, ``degp``) are allowed to drift only in the lenient mode: ``$Increment`` is not undone by ``TROLLBACK`` (RISK
R1), so the strict mode is expected to fail on the rollback steps. That xfail is the current state, shown on purpose.

Needs IRIS (``iris_connection``). Marked ``e2e`` and ``requires_clean_isolation``: the oracle reads every graph, so run
it on a namespace without other suites' leftover edges. The lead runs it; the worktree does not.
"""

from __future__ import annotations

import random

import pytest

from iris_vector_graph import IRISGraphEngine
from tests.support.derived_oracle import ADJACENCY_KINDS, ADVISORY_KINDS, DerivedOracle

pytestmark = [pytest.mark.e2e, pytest.mark.requires_clean_isolation]

STEPS = 40
NODES = ["n0", "n1", "n2", "n3", "n4"]
PREDICATES = ["knows", "likes"]


def _run_sequence(seed, engine, conn, host, graph, allowed_advisory, violations_log):
    """Apply ``STEPS`` random operations, checking the oracle after each one. Returns the number of operations run."""
    rng = random.Random(seed)
    oracle = DerivedOracle(host, conn)
    present = set()

    for node in NODES:
        engine.create_node(node, graph=graph)

    for step in range(STEPS):
        op = rng.choice(["insert", "insert", "delete", "rollback_after_insert", "failed_batch"])
        s, p, o = rng.choice(NODES), rng.choice(PREDICATES), rng.choice(NODES)
        if op == "insert":
            engine.create_edge(s, p, o, graph=graph)
            present.add((s, p, o))
        elif op == "delete" and present:
            victim = rng.choice(sorted(present))
            engine.delete_edge(*victim, graph=graph)
            present.discard(victim)
        elif op == "rollback_after_insert":
            _rollback_after_insert(conn, host, graph, s, p, o)
        elif op == "failed_batch":
            _failed_batch(conn, host, graph, s, p, o)

        found = oracle.violations()
        violations_log.append((step, op, [(v.kind, v.store, v.key) for v in found]))
        adjacency = [v for v in found if v.kind in ADJACENCY_KINDS]
        assert adjacency == [], f"seed {seed}, step {step} ({op}): adjacency drift {adjacency}"
        advisory = [v for v in found if v.kind in ADVISORY_KINDS and v.kind not in allowed_advisory]
        assert advisory == [], f"seed {seed}, step {step} ({op}): counter drift {advisory}"
    return STEPS


def _rollback_after_insert(conn, host, graph, s, p, o):
    """One adjacency write inside a Native transaction that is rolled back.

    ``out``/``in`` are plain Sets and are undone; the ``$Increment`` counters are not (RISK R1). The transaction is on the
    Native handle on purpose: the engine's Native API handle is a dedicated connection (``_iris_obj``), so a SQL
    ``rollback()`` on the cursor's connection cannot undo what the Native API wrote. The rollbacks the engine does
    guarantee are the ones ObjectScript owns (Ledger.Commit, Eraser), which are Native-side transactions like this one.
    """
    native = host._native
    native.tStart()
    try:
        native.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0", graph)
    finally:
        native.tRollback()


def _failed_batch(conn, host, graph, s, p, o):
    """A batch of two adjacency writes that fails after the first: both are rolled back together."""
    native = host._native
    native.tStart()
    try:
        native.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0", graph)
        native.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", o, p, s, "1.0", graph)
        raise RuntimeError("the batch's third statement fails")
    except RuntimeError:
        pass  # by design
    finally:
        native.tRollback()


class _NativeHost:
    """The two calls the oracle and this test make, over ``iris.createIRIS(conn)``.

    The same Native API calls ``iris_vector_graph.host.ClientHost`` makes; kept local so this test does not depend on
    that module being on the branch.
    """

    def __init__(self, conn):
        import iris  # only in the e2e path, which has IRIS

        self._native = iris.createIRIS(conn)

    def get(self, name, *subs):
        return self._native.get(name, *subs)

    def next_subscript(self, name, *subs, reverse=False):
        return self._native.nextSubscript(reverse, name, *subs)

    def call(self, cls, method, *args):
        if method.startswith("%"):
            raise AssertionError("a % method must not be called from the test host")
        return self._native.classMethodValue(cls, method, *args)


@pytest.fixture
def graph_name(request):
    return f"oracle_prop_{request.node.name.replace('[', '_').replace(']', '')}"[:200]


@pytest.fixture
def engine_and_host(iris_connection):
    engine = IRISGraphEngine(iris_connection)
    host = _NativeHost(iris_connection)
    # Start from stores that match the rows: other suites leave ^KG residue, which the oracle would report.
    host._native.classMethodVoid("Graph.KG.Traversal", "BuildKG")
    yield engine, iris_connection, host
    try:
        iris_connection.rollback()
    except Exception:
        pass


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize(
    "allowed_advisory",
    [
        pytest.param(ADVISORY_KINDS, id="lenient"),
        pytest.param(
            frozenset(),
            id="strict",
            marks=pytest.mark.xfail(
                reason="RISK R1: $Increment counters are not undone by TROLLBACK, so deg/degp ghost entries remain",
                strict=False,
            ),
        ),
    ],
)
def test_adjacency_matches_rows_after_every_random_step(seed, allowed_advisory, engine_and_host, graph_name):
    engine, conn, host = engine_and_host
    log = []
    try:
        _run_sequence(seed, engine, conn, host, graph_name, allowed_advisory, log)
    finally:
        try:
            engine.drop_graph(graph_name)
        except Exception:
            pass
