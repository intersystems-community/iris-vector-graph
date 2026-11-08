"""4.6.0 R306: every public graph algorithm, method and Cypher procedure, against an independent reference.

The defects newcomer walkthroughs found (closeness and eigenvector cut before ranking, nodes with no edge left out,
neighbourhood betweenness scored undirected, k-core ties in Python string order) passed a 20,000-test suite because
its tests asserted hand-picked cases or IVG's own captured output. This file loads seeded graphs with the shapes that
broke us (tests/oracle/graphs.py: nodes with no edge, self-loops, parallel edges, sinks, two-cycles, even cycles,
disconnected parts, ties, ids such as "9", "10", "-1", "07", quotes, spaces, unicode, a named graph next to the
default one) and compares each answer with tests/oracle/reference.py, networkx or the documented rule: the same ids,
scores within tolerance, highest first with ties by id in IRIS collation, and the same top-k cut. A disagreement
fails with the seed, the graph and both answers.

The seeded graphs never had a periodic strongly connected component, on which an unshifted power iteration cycles:
eigenvector centrality answered the round-3 walkthrough's graph (a directed 3-cycle with tails) by ``max_iter`` mod 3.
The fixed families of ``tests.oracle.graphs.periodic`` (directed 2-, 3- and 4-cycles, a cycle with tails, a bipartite
component, components of periods 2 and 3, the walkthrough's graph with and without its named graph) run through every
case too, with eigenvector centrality held to networkx's own ``eigenvector_centrality`` (its failure to converge
included), and every procedure and method taking a direction run "out", "in" and "both" against networkx's graph,
reversed graph and undirected graph, an unknown direction raising.

Runs in the scratch namespace ``IVG_NAMESPACE`` names (an IVG460*/IVG262* one with IVG installed: the R309 gate
makes one, or ``scripts/dev/make_scratch_namespace.py IVG460FXOA``), or in USER with ``IVG_ORACLE_USER=1``; it erases
that namespace's graph between seeds and never creates or drops it. On ``IVG_TEST_CONTAINER`` at ``IVG_PORT``. Every case runs
with the Arno library as the engine finds it (loaded on the enterprise container) and with ``IVG_DISABLE_ARNO=1``
(the algorithm cache off too, so no answer is the other mode's). ``IVG_ORACLE_SEEDS=n`` sets the number of seeds
(40, about a minute). The Arno library's Native Readers (PageRank, WCC, CDLP) read USER's ``^KG`` only, so they run
only in USER; ``IVG_ORACLE_ASCII=1`` keeps non-ASCII ids out, which the library answers with
stray characters (IVG then falls back to ObjectScript or Python, and the library goes unchecked).

    IVG_NAMESPACE=IVG460FXOA pytest tests/e2e/test_460_r306_algorithm_oracle_e2e.py
    IVG_NAMESPACE=USER IVG_ORACLE_USER=1 IVG_ORACLE_ASCII=1 pytest tests/e2e/test_460_r306_algorithm_oracle_e2e.py
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import pytest

from tests.e2e import ns262
from tests.oracle import reference as ref
from tests.oracle.compare import compare_order, compare_partition, compare_ranked, compare_values
from tests.oracle.graphs import NAMED_GRAPH, OracleGraph, by_seed, periodic

NS = os.environ.get("IVG_NAMESPACE", "")
_RUNS_HERE = NS.startswith(("IVG460", "IVG262")) or (NS == "USER" and os.environ.get("IVG_ORACLE_USER") == "1")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not _RUNS_HERE, reason="IVG_NAMESPACE must name an IVG460*/IVG262* scratch namespace "
                                              "(or USER, with IVG_ORACLE_USER=1): every case erases its graph"),
]

#: The generated seeds, then the periodic families (negative seeds; tests/oracle/graphs.py ``periodic``: directed
#: k-cycles, a cycle with tails, a bipartite component, two components of different periods, the round-3
#: walkthrough's graph with and without its named graph), which the generator never drew and on which an unshifted
#: power iteration cycles. ``IVG_ORACLE_PERIODIC=0`` leaves them out.
SEEDS = list(range(int(os.environ.get("IVG_ORACLE_SEEDS", "40")))) + (
    [] if os.environ.get("IVG_ORACLE_PERIODIC") == "0" else [g.seed for g in periodic()])
#: ``IVG_ORACLE_ASCII=1``: ids without non-ASCII characters, which the Arno library answers wrongly-encoded (IVG then
#: falls back and the library goes unchecked; tests/oracle/graphs.py)
ASCII = os.environ.get("IVG_ORACLE_ASCII", "") == "1"
TOP = 3


#: The seeds whose graph has a node with no edge, and those with a named graph holding edges.
EDGELESS_SEEDS = [s for s in SEEDS if by_seed(s, ascii_only=ASCII).isolated()]
NAMED_SEEDS = [s for s in SEEDS if by_seed(s, ascii_only=ASCII).has_named_edges]
#: The round-3 walkthrough's graph (without its named graph), when the periodic families run.
WALKTHROUGH_SEEDS = [s for s in SEEDS if s < 0 and by_seed(s).shapes[-1] == "walkthrough"]


def _seed_id(s: int) -> str:
    return f"seed{s}" if s >= 0 else by_seed(s).shapes[-1]


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", params=["arno", "no_arno"])
def mode(request):
    return request.param


@pytest.fixture
def env(mode, monkeypatch):
    if mode == "no_arno":
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    else:
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
        monkeypatch.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)
    return mode


_LOADED: Dict[str, object] = {"seed": None}
_ENGINES: Dict[str, object] = {}


def _engine(mode: str):
    """One connection and engine per mode, made with that mode's environment, so the Arno probe reads it."""
    if mode not in _ENGINES:
        from iris_vector_graph import IRISGraphEngine

        _ENGINES[mode] = IRISGraphEngine(ns262.connect(NS))
    return _ENGINES[mode]


def _load(engine, g: OracleGraph) -> None:
    ns262.native(engine.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    for v in g.nodes:
        engine.create_node(v)
    for e in g.edges:
        engine.create_edge(e.s, e.p, e.o, weight=e.weight)
    for v in g.named_nodes:
        engine.create_node(v, graph=NAMED_GRAPH)
    for e in g.named_edges:
        engine.create_edge(e.s, e.p, e.o, weight=e.weight, graph=NAMED_GRAPH)
    engine.sync()


@pytest.fixture(scope="module", params=SEEDS, ids=_seed_id)
def graph(request) -> OracleGraph:
    return by_seed(request.param, ascii_only=ASCII)


@pytest.fixture
def engine(env, graph):
    e = _engine(env)
    if _LOADED["seed"] != graph.seed:
        _load(e, graph)
        _LOADED["seed"] = graph.seed
    assert e.node_count() == len(graph.nodes), "the graph did not load"
    return e


def teardown_module(module):
    for e in _ENGINES.values():
        try:
            ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
            e.conn.close()
        except Exception:  # noqa: BLE001 - best effort; the namespace is scratch
            pass
    _ENGINES.clear()


# ── helpers ───────────────────────────────────────────────────────────────────────────────────────────────────────


def cy(engine, q: str, **params):
    return engine.execute_cypher(q, parameters=params or None, raise_on_error=True)


def fail(g: OracleGraph, what: str, problems: List[str]) -> None:
    if problems:
        pytest.fail(
            f"{what} disagrees with the reference on seed {g.seed} (shapes {g.shapes}):\n  "
            + "\n  ".join(problems)
            + f"\ngraph:\n{g.literal()}",
            pytrace=False,
        )


def pairs(rows, id_key="id", score_key="score"):
    return [(r[id_key], r[score_key]) for r in rows]


def seeds_of(g: OracleGraph) -> List[str]:
    live = g.with_edges()
    return sorted(live, key=ref.collation_key)[:1] + sorted(live, key=ref.sample_key)[:1]


def _nkg_procedure_refused(engine, g: OracleGraph, q: str, **params) -> bool:
    """With a named graph holding edges, a procedure reading ^NKG raises ValueError (USER_GUIDE section 6)."""
    if not g.has_named_edges:
        return False
    with pytest.raises(ValueError):
        cy(engine, q, **params)
    return True


# ── PageRank ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _pagerank_tol(engine, graph) -> float:
    """The Arno library's PageRank (its Native Reader, USER only) also stops once converged, within about 2e-6 of
    the fixed count's scores (USER_GUIDE section 6); every other route runs the fixed count."""
    native = not graph.isolated() and engine._store._arno_route("pagerank") == "native"
    return 5e-6 if native else 1e-9


@pytest.mark.parametrize("iterations", [20, 200])
def test_kg_pagerank(engine, graph, iterations):
    got = engine.kg_PAGERANK() if iterations == 20 else engine.kg_PAGERANK(max_iterations=iterations)
    fail(graph, f"kg_PAGERANK(max_iterations={iterations})",
         compare_ranked(got, ref.pagerank_global(graph, max_iterations=iterations), tol=_pagerank_tol(engine, graph)))


def test_gds_pagerank_stream(engine, graph):
    q = "CALL gds.pageRank.stream('g', {dampingFactor: 0.85, maxIterations: 20}) YIELD nodeId, score"
    if graph.has_named_edges:
        with pytest.raises(ValueError):
            cy(engine, q)
        q = "USE GRAPH '' " + q
    rows = cy(engine, q).rows
    fail(graph, "gds.pageRank.stream", compare_ranked([(r[0], r[1]) for r in rows], ref.pagerank_global(graph),
                                                      tol=_pagerank_tol(engine, graph)))


@pytest.mark.parametrize("top_k", [None, TOP])
def test_personalized_pagerank(engine, graph, top_k):
    seeds = seeds_of(graph)
    got = engine.kg_PERSONALIZED_PAGERANK(seeds, tolerance=1e-10, max_iterations=1000, return_top_k=top_k)
    want = ref.personalized_pagerank(graph, seeds, tolerance=1e-10, max_iterations=1000)
    what = f"kg_PERSONALIZED_PAGERANK({seeds}, return_top_k={top_k})"
    if top_k is None:
        fail(graph, what, compare_values(got, want, tol=1e-7))
    else:  # the dict holds the cut, in rank order
        fail(graph, what, compare_ranked(list(got.items()), want, top_k=top_k, tol=1e-7))


@pytest.mark.parametrize("graph", EDGELESS_SEEDS, indirect=True, ids=_seed_id)
def test_personalized_pagerank_from_an_edgeless_seed(engine, graph):
    seed = graph.isolated()[0]
    got = engine.kg_PERSONALIZED_PAGERANK([seed], tolerance=1e-10, max_iterations=1000)
    want = ref.personalized_pagerank(graph, [seed], tolerance=1e-10, max_iterations=1000)
    fail(graph, f"kg_PERSONALIZED_PAGERANK([{seed!r}])", compare_values(got, want, tol=1e-6))


@pytest.mark.parametrize("graph", NAMED_SEEDS, indirect=True, ids=_seed_id)
def test_personalized_pagerank_named_graph(engine, graph):
    seeds = graph.named_nodes[:1]
    got = engine.kg_PERSONALIZED_PAGERANK(seeds, graph=NAMED_GRAPH, tolerance=1e-10, max_iterations=1000)
    want = ref.personalized_pagerank(graph, seeds, tolerance=1e-10, max_iterations=1000, named=True)
    fail(graph, f"kg_PERSONALIZED_PAGERANK({seeds}, graph={NAMED_GRAPH!r})", compare_values(got, want, tol=1e-6))


def test_kg_pagerank_with_seeds_is_ppr_after_20(engine, graph):
    seeds = seeds_of(graph)
    got = engine.kg_PAGERANK(seed_entities=seeds)
    # 20 iterations do not converge: the answer depends on the start, the seeds (the walk's, PageRank.Run)
    want = ref.personalized_pagerank(graph, seeds, max_iterations=20, start="seeds")
    fail(graph, f"kg_PAGERANK(seed_entities={seeds})", compare_values(got, want, tol=1e-6))


def test_ivg_ppr(engine, graph):
    seeds = seeds_of(graph)
    q = "CALL ivg.ppr($seeds, 0.85, 100) YIELD node, score RETURN node.node_id AS id, score"
    q = ("USE GRAPH '' " + q) if graph.has_named_edges else q
    got = {r[0]: r[1] for r in cy(engine, q, seeds=seeds).rows}
    want = ref.personalized_pagerank(graph, seeds, max_iterations=100)
    fail(graph, f"ivg.ppr({seeds})", compare_values(got, want, tol=1e-4))


# ── degree ────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("direction", ["out", "in", "both"])
@pytest.mark.parametrize("top_k", [0, TOP])
def test_degree_centrality(engine, graph, direction, top_k):
    want = ref.degree_centrality(graph, direction)
    got = engine.degree_centrality(direction=direction, top_k=top_k)
    problems = compare_ranked(pairs(got), {v: s for v, (s, _) in want.items()}, top_k=top_k, tol=1e-9)
    problems += [f"{r['id']!r}: degree {r['degree']} vs {want[r['id']][1]}" for r in got
                 if r["id"] in want and r["degree"] != want[r["id"]][1]]
    fail(graph, f"degree_centrality(direction={direction!r}, top_k={top_k})", problems)


def test_degree_centrality_predicate_and_named_graph(engine, graph):
    want = ref.degree_centrality(graph, "out", predicate="S")
    got = engine.degree_centrality(direction="out", predicate="S", top_k=0)
    fail(graph, "degree_centrality(predicate='S')", compare_ranked(pairs(got), {v: s for v, (s, _) in want.items()}))
    if graph.has_named_edges:
        want = ref.degree_centrality(graph, "out", named=True)
        got = engine.degree_centrality(direction="out", top_k=0, graph=NAMED_GRAPH)
        fail(graph, f"degree_centrality(graph={NAMED_GRAPH!r})",
             compare_ranked(pairs(got), {v: s for v, (s, _) in want.items()}))


@pytest.mark.parametrize("top_k", [10000, TOP])
def test_ivg_degree_centrality(engine, graph, top_k):
    q = ("CALL ivg.degreeCentrality({direction: 'both', topK: %d}) YIELD node, score, degree "
         "RETURN node.node_id AS id, score, degree" % top_k)
    want = {v: s for v, (s, _) in ref.degree_centrality(graph, "both").items()}
    rows = cy(engine, ("USE GRAPH '' " + q) if graph.has_named_edges else q).rows
    fail(graph, f"ivg.degreeCentrality(topK={top_k})", compare_ranked([(r[0], r[1]) for r in rows], want,
                                                                      top_k=top_k if top_k != 10000 else 0))
    if graph.has_named_edges:
        named = {v: s for v, (s, _) in ref.degree_centrality(graph, "both", named=True).items()}
        rows = cy(engine, f"USE GRAPH '{NAMED_GRAPH}' " + q).rows
        fail(graph, f"USE GRAPH {NAMED_GRAPH!r} ivg.degreeCentrality", compare_ranked(
            [(r[0], r[1]) for r in rows], named, top_k=top_k if top_k != 10000 else 0))
        # without USE GRAPH: each graph's own rows
        both = sorted((r[0], round(r[1], 9)) for r in cy(engine, q).rows)
        each = sorted([(v, round(s, 9)) for v, s in ref.ranked(want, top_k if top_k != 10000 else 0)]
                      + [(v, round(s, 9)) for v, s in ref.ranked(named, top_k if top_k != 10000 else 0)])
        fail(graph, "ivg.degreeCentrality without USE GRAPH", compare_order(both, each))


# ── betweenness ───────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("direction,max_hops,top_k", [("out", 0, 0), ("in", 0, 0), ("both", 0, 0),
                                                      ("out", 2, 0), ("both", 2, 0), ("out", 0, TOP)])
def test_betweenness(engine, graph, direction, max_hops, top_k):
    n = engine.node_count()
    got = engine.betweenness_centrality(sample_size=n, direction=direction, max_hops=max_hops, top_k=top_k)
    want = ref.betweenness(graph, direction, max_hops)
    fail(graph, f"betweenness_centrality(sample_size={n}, direction={direction!r}, max_hops={max_hops}, "
                f"top_k={top_k})", compare_ranked(pairs(got), want, top_k=top_k, omit_zero=True))


@pytest.mark.parametrize("top_k", [10000, TOP])
def test_ivg_betweenness_and_gds(engine, graph, top_k):
    n = len(graph.nodes)
    q = (f"CALL ivg.betweenness({{sampleSize: {n}, direction: 'both', topK: {top_k}}}) YIELD node, score "
         "RETURN node.node_id AS id, score")
    if _nkg_procedure_refused(engine, graph, q):
        return
    want = ref.betweenness(graph, "both")
    cut = top_k if top_k != 10000 else 0
    fail(graph, f"ivg.betweenness(topK={top_k})",
         compare_ranked([(r[0], r[1]) for r in cy(engine, q).rows], want, top_k=cut, omit_zero=True))
    g = f"CALL gds.betweenness.stream('g', {{sampleSize: {n}, topK: {cut}}}) YIELD nodeId, score"
    fail(graph, f"gds.betweenness.stream(topK={cut})",
         compare_ranked([(r[0], r[1]) for r in cy(engine, g).rows], ref.betweenness(graph), top_k=cut,
                        omit_zero=True))


@pytest.mark.parametrize("top_k", [20, 2])
def test_betweenness_neighborhood(engine, graph, top_k):
    seed = seeds_of(graph)[0]
    got = engine.betweenness_centrality_neighborhood(seed, hops=2, top_k=top_k)
    fail(graph, f"betweenness_centrality_neighborhood({seed!r}, hops=2, top_k={top_k})",
         compare_ranked(pairs(got), ref.betweenness_neighborhood(graph, seed, 2), top_k=top_k, omit_zero=True))


@pytest.mark.parametrize("graph", EDGELESS_SEEDS, indirect=True, ids=_seed_id)
def test_betweenness_neighborhood_of_a_node_with_no_edge(engine, graph):
    seed = graph.isolated()[0]
    fail(graph, f"betweenness_centrality_neighborhood({seed!r})",
         compare_ranked(pairs(engine.betweenness_centrality_neighborhood(seed)),
                        ref.betweenness_neighborhood(graph, seed, 2), omit_zero=True))


# ── closeness ─────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("formula", ["harmonic", "classical"])
@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_closeness(engine, graph, formula, direction):
    got = engine.closeness_centrality(formula=formula, direction=direction, top_k=0)
    fail(graph, f"closeness_centrality(formula={formula!r}, direction={direction!r})",
         compare_ranked(pairs(got), ref.closeness(graph, formula, direction)))


def test_closeness_max_hops_and_top_k(engine, graph):
    got = engine.closeness_centrality(max_hops=2, top_k=TOP)
    fail(graph, f"closeness_centrality(max_hops=2, top_k={TOP})",
         compare_ranked(pairs(got), ref.closeness(graph, "harmonic", "out", 2), top_k=TOP))


@pytest.mark.parametrize("formula,direction,top_k", [("harmonic", "out", 10000), ("classical", "both", 10000),
                                                     ("harmonic", "both", TOP)])
def test_ivg_closeness(engine, graph, formula, direction, top_k):
    q = (f"CALL ivg.closeness({{formula: '{formula}', direction: '{direction}', topK: {top_k}}}) YIELD node, score "
         "RETURN node.node_id AS id, score")
    if _nkg_procedure_refused(engine, graph, q):
        return
    fail(graph, f"ivg.closeness({formula}, {direction}, topK={top_k})",
         compare_ranked([(r[0], r[1]) for r in cy(engine, q).rows], ref.closeness(graph, formula, direction),
                        top_k=top_k if top_k != 10000 else 0))


# ── eigenvector ───────────────────────────────────────────────────────────────────────────────────────────────────


def _eigenvector_problems(run, want, converged, top_k) -> List[str]:
    """``run()`` against networkx: its answer where networkx converges, `PowerIterationFailedConvergence` where
    networkx raises its own."""
    from iris_vector_graph import PowerIterationFailedConvergence

    try:
        got = run()
    except PowerIterationFailedConvergence as exc:
        return [] if not converged else [f"IVG raised {exc}; networkx converged: {ref.ranked(want)}"]
    if not converged:
        return [f"IVG answered {got[:5]}; networkx raises PowerIterationFailedConvergence"]
    return compare_ranked(got, want, top_k=top_k, tol=1e-6)


#: (None, None): the defaults, networkx's (100, 1e-6); 999 and 1000: an answer that depends on max_iter mod 3 shows
@pytest.mark.parametrize("max_iter,tol,top_k", [(None, None, 0), (30, 1e-6, 0), (999, 1e-6, 0), (1000, 1e-6, 0),
                                              (1000, 1e-10, 0), (1000, 1e-10, TOP)])
def test_eigenvector(engine, graph, max_iter, tol, top_k):
    """networkx's ``eigenvector_centrality(G, max_iter, tol)``, its failure to converge included (USER_GUIDE
    section 6)."""
    kw = {} if max_iter is None else {"max_iter": max_iter, "tol": tol}
    want, converged = ref.eigenvector(graph, **kw)
    fail(graph, f"eigenvector_centrality({kw}, top_k={top_k})", _eigenvector_problems(
        lambda: pairs(engine.eigenvector_centrality(top_k=top_k, **kw)), want, converged, top_k))


@pytest.mark.parametrize("max_iter,top_k", [(None, 10000), (1000, 10000), (1000, TOP), (30, 10000)])
def test_ivg_eigenvector(engine, graph, max_iter, top_k):
    opts = f"maxIter: {max_iter}, tol: 1e-10, " if max_iter else ""
    q = (f"CALL ivg.eigenvector({{{opts}topK: {top_k}}}) YIELD node, score "
         "RETURN node.node_id AS id, score")
    if _nkg_procedure_refused(engine, graph, q):
        return
    want, converged = ref.eigenvector(graph, max_iter=max_iter, tol=1e-10) if max_iter else ref.eigenvector(graph)
    fail(graph, f"ivg.eigenvector({opts}topK={top_k})", _eigenvector_problems(
        lambda: [(r[0], r[1]) for r in cy(engine, q).rows], want, converged,
        top_k if top_k != 10000 else 0))


# ── communities and components ────────────────────────────────────────────────────────────────────────────────────


def _leiden_problems(g: OracleGraph, rows) -> List[str]:
    problems = []
    groups: Dict[int, set] = {}
    for r in rows:
        groups.setdefault(r["community"], set()).add(r["id"])
    ids = [r["id"] for r in rows]
    if sorted(ids, key=ref.collation_key) != sorted(g.nodes, key=ref.collation_key):
        problems.append(f"ids {sorted(ids, key=ref.collation_key)} vs nodes {sorted(g.nodes, key=ref.collation_key)}")
    for r in rows:
        if r["size"] != len(groups[r["community"]]):
            problems.append(f"{r['id']!r}: size {r['size']}, its community has {len(groups[r['community']])}")
    sizes = [len(groups[c]) for c in sorted(groups)]
    if sizes != sorted(sizes, reverse=True) or sorted(groups) != list(range(len(groups))):
        problems.append(f"communities not numbered 0.. largest first: {dict(sorted((c, len(s)) for c, s in groups.items()))}")
    if [r["community"] for r in rows] != sorted(r["community"] for r in rows):
        problems.append("rows not sorted by community")
    q, best = ref.modularity(g, groups.values()), ref.best_louvain_modularity(g)
    if q < best - 0.05:
        problems.append(f"modularity {q:.4f}, networkx Louvain reaches {best:.4f}")
    return problems + ([f"IVG communities {[sorted(s, key=ref.collation_key) for _, s in sorted(groups.items())]}"]
                       if problems else [])


def test_leiden(engine, graph):
    fail(graph, "leiden_communities(random_seed=42, top_k=0)",
         _leiden_problems(graph, engine.leiden_communities(random_seed=42, top_k=0)))


def test_ivg_leiden(engine, graph):
    q = ("CALL ivg.leiden({randomSeed: 42, topK: 10000}) YIELD node, community, size "
         "RETURN node.node_id AS id, community, size")
    if _nkg_procedure_refused(engine, graph, q):
        return
    rows = [{"id": r[0], "community": r[1], "size": r[2]} for r in cy(engine, q).rows]
    fail(graph, "ivg.leiden", _leiden_problems(graph, rows))


@pytest.mark.parametrize("top_k", [0, TOP])
def test_triangle_count(engine, graph, top_k):
    want = ref.triangles(graph)
    got = engine.triangle_count(top_k=top_k)
    problems = compare_order([r["id"] for r in got], ref.ranked_rows({v: t for v, (t, _) in want.items()}, top_k))
    problems += compare_values({r["id"]: (r["triangles"], round(r["lcc"], 9)) for r in got},
                               {v: (t, round(c, 9)) for v, (t, c) in want.items() if v in {r["id"] for r in got}})
    fail(graph, f"triangle_count(top_k={top_k})", problems)


@pytest.mark.parametrize("top_k", [0, TOP])
def test_k_core(engine, graph, top_k):
    want = ref.core_numbers(graph)
    got = engine.k_core(top_k=top_k)
    problems = compare_order([r["id"] for r in got], ref.ranked_rows(want, top_k))
    problems += compare_values({r["id"]: r["coreness"] for r in got},
                               {v: c for v, c in want.items() if v in {r["id"] for r in got}})
    fail(graph, f"k_core(top_k={top_k})", problems)


def _scc_problems(g: OracleGraph, rows) -> List[str]:
    problems = compare_partition({r["id"]: r["component"] for r in rows}, ref.scc_sets(g))
    sizes = {}
    for r in rows:
        sizes.setdefault(r["component"], set()).add(r["id"])
    problems += [f"{r['id']!r}: size {r['size']} vs {len(sizes[r['component']])}" for r in rows
                 if r["size"] != len(sizes[r["component"]])]
    order = [len(sizes[c]) for c in sorted(sizes)]
    if order != sorted(order, reverse=True):
        problems.append(f"component 0 is not the largest: sizes by component {order}")
    return problems


def test_scc(engine, graph):
    fail(graph, "strongly_connected_components(top_k=0)", _scc_problems(graph, engine.strongly_connected_components(top_k=0)))


# ivg.scc is not cut: its topK cuts after whole components, whose order the docs leave open for equal sizes
@pytest.mark.parametrize("proc,cols,top_k", [
    ("ivg.triangleCount", "triangles, lcc", 10000), ("ivg.triangleCount", "triangles, lcc", TOP),
    ("ivg.kcore", "coreness", 10000), ("ivg.kcore", "coreness", TOP), ("ivg.scc", "component, size", 10000),
])
def test_community_procedures(engine, graph, proc, cols, top_k):
    q = f"CALL {proc}({{topK: {top_k}}}) YIELD node, {cols} RETURN node.node_id AS id, {cols}"
    if _nkg_procedure_refused(engine, graph, q):
        return
    rows = cy(engine, q).rows
    cut = top_k if top_k != 10000 else 0
    if proc == "ivg.triangleCount":
        want = ref.triangles(graph)
        problems = compare_order([r[0] for r in rows], ref.ranked_rows({v: t for v, (t, _) in want.items()}, cut))
        problems += [f"{r[0]!r}: ({r[1]}, {r[2]:.6g}) vs {want[r[0]]}" for r in rows if r[0] in want
                     and (r[1] != want[r[0]][0] or abs(r[2] - want[r[0]][1]) > 1e-9)]
    elif proc == "ivg.kcore":
        want = ref.core_numbers(graph)
        problems = compare_order([r[0] for r in rows], ref.ranked_rows(want, cut))
        problems += [f"{r[0]!r}: {r[1]} vs {want.get(r[0])}" for r in rows if want.get(r[0]) != r[1]]
    else:
        problems = _scc_problems(graph, [{"id": r[0], "component": r[1], "size": r[2]} for r in rows])
    fail(graph, f"{proc}(topK={top_k})", problems)


def test_kg_wcc(engine, graph):
    fail(graph, "kg_WCC()", compare_values(engine.kg_WCC(), ref.wcc_labels(graph)))


@pytest.mark.parametrize("iterations", [10, 3])
def test_kg_cdlp(engine, graph, iterations):
    fail(graph, f"kg_CDLP(max_iterations={iterations})",
         compare_values(engine.kg_CDLP(max_iterations=iterations), ref.cdlp(graph, iterations)))


# ── shortest paths ────────────────────────────────────────────────────────────────────────────────────────────────


def _pairs_to_ask(g: OracleGraph):
    live = sorted(g.with_edges(), key=ref.sample_key)
    out = [(a, b) for a in live[:3] for b in live[-3:] if a != b]
    return out[:6]


def _path_nodes(value):
    v = json.loads(value) if isinstance(value, str) else value
    if isinstance(v, dict):
        return v.get("nodes") or v.get("nodeIds")
    return v


def test_cypher_shortest_path(engine, graph):
    problems = []
    for a, b in _pairs_to_ask(graph):
        want = ref.hop_distance(graph, a, b)
        q = "MATCH p = shortestPath((a {node_id: $a})-[*]->(b {node_id: $b})) RETURN length(p) AS hops, p"
        # the reference is the default graph's; with no USE GRAPH each graph answers its own path (4.6.2: g2's too)
        rows = cy(engine, ("USE GRAPH '' " + q) if graph.has_named_edges else q, a=a, b=b).rows
        if want is None:
            if rows:
                problems.append(f"{a!r}->{b!r}: IVG path {rows}, unreachable")
            continue
        if not rows or rows[0][0] != want:
            problems.append(f"{a!r}->{b!r}: IVG {rows[0][0] if rows else None} hops vs {want}")
        elif not ref.is_path(graph, _path_nodes(rows[0][1])):
            problems.append(f"{a!r}->{b!r}: {rows[0][1]} is not a path of the graph")
    fail(graph, "shortestPath", problems)


def test_weighted_shortest_path(engine, graph):
    problems = []
    for a, b in _pairs_to_ask(graph):
        want = ref.weighted_cost(graph, a, b)
        q = "CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 0) YIELD path, totalCost RETURN path, totalCost"
        rows = cy(engine, ("USE GRAPH '' " + q) if graph.has_named_edges else q, a=a, b=b).rows
        if want is None:
            if rows:
                problems.append(f"ivg.shortestPath.weighted {a!r}->{b!r}: IVG {rows}, unreachable")
        elif not rows or abs(float(rows[0][1]) - want) > 1e-9:
            problems.append(f"ivg.shortestPath.weighted {a!r}->{b!r}: IVG {rows[0][1] if rows else None} vs {want}")
        q = ("CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: $a, targetNode: $b}) "
             "YIELD totalCost, nodeIds")
        if graph.has_named_edges:
            q = "USE GRAPH '' " + q
        rows = cy(engine, q, a=a, b=b).rows
        hops = ref.hop_distance(graph, a, b)
        if hops is None:
            if rows:
                problems.append(f"gds dijkstra {a!r}->{b!r}: IVG {rows}, unreachable")
        elif not rows or abs(float(rows[0][0]) - hops) > 1e-9:
            problems.append(f"gds dijkstra {a!r}->{b!r}: IVG {rows[0] if rows else None} vs {hops} hops")
        elif not ref.is_path(graph, _path_nodes(rows[0][1])):
            problems.append(f"gds dijkstra {a!r}->{b!r}: {rows[0][1]} is not a path")
    fail(graph, "weighted shortest paths", problems)


#: The three routes of CALL ivg.shortestPath.weighted: the engine's handler (the CALL and a RETURN of yielded names),
#: its cost-only search from both ends (YIELD totalCost: TraversalPaths.DijkstraCost), and the translator's CTE over
#: the DijkstraPath SQL function (any later clause).
_WEIGHTED_ROUTES = {
    "handler": "YIELD path, totalCost RETURN path, totalCost",
    "cost_only": "YIELD totalCost",
    "translated": "YIELD path, totalCost RETURN totalCost AS cost, path AS p",
}


def _weighted_cost_of(rows, route: str) -> Optional[float]:
    """The cost in ``route``'s first row (the handler's second column, the others' first); None for no row."""
    if not rows:
        return None
    return float(rows[0][1] if route == "handler" else rows[0][0])


@pytest.mark.parametrize("route", sorted(_WEIGHTED_ROUTES))
@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_weighted_shortest_path_each_direction(engine, graph, direction, route):
    """networkx's ``dijkstra_path_length`` on the graph ("out"), the reversed graph ("in") and the undirected graph
    ("both"). 4.6.0, round 3: "in" walked no edge, so a path against the edges answered no row."""
    problems = []
    pairs_asked = _pairs_to_ask(graph)
    for a, b in pairs_asked + [(b, a) for a, b in pairs_asked]:
        want = ref.weighted_cost(graph, a, b, direction)
        q = f"CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 0, '{direction}') " + _WEIGHTED_ROUTES[route]
        rows = cy(engine, ("USE GRAPH '' " + q) if graph.has_named_edges else q, a=a, b=b).rows
        cost = _weighted_cost_of(rows, route)
        if want is None:
            if rows:
                problems.append(f"{a!r}->{b!r}: IVG {rows}, no path")
        elif cost is None or abs(cost - want) > 1e-9:
            problems.append(f"{a!r}->{b!r}: IVG {cost} vs {want}")
        elif route != "cost_only":
            nodes = _path_nodes(rows[0][0] if route == "handler" else rows[0][1])
            if not (nodes and nodes[0] == a and nodes[-1] == b and ref.is_weighted_path(graph, nodes, direction)):
                problems.append(f"{a!r}->{b!r}: {nodes} is not a path walked {direction!r}")
    fail(graph, f"ivg.shortestPath.weighted(direction={direction!r}) [{route}]", problems)


@pytest.mark.parametrize("graph", WALKTHROUGH_SEEDS, indirect=True, ids=_seed_id)
@pytest.mark.parametrize("route", sorted(_WEIGHTED_ROUTES))
def test_weighted_shortest_path_against_the_edges_walkthrough(engine, graph, route):
    """The walkthrough's case: fay to ann against the edges is fay <- eve <- dan <- cat <- ben <- ann, cost
    2 + 1 + 1 + 1 + 1 = 6 (through ann -> eve, weight 5, it is 7); either way it is fay - den - cat - ann, 3; along
    the edges there is none; 'sideways' raises. The numbers are networkx's (and checked by hand here)."""
    q = "CALL ivg.shortestPath.weighted('fay', 'ann', 'weight', 9999, 10, '{d}') " + _WEIGHTED_ROUTES[route]
    for d, by_hand in (("in", 6.0), ("both", 3.0)):
        rows = cy(engine, q.format(d=d)).rows
        want = ref.weighted_cost(graph, "fay", "ann", d)
        got = _weighted_cost_of(rows, route)
        assert want == by_hand and got == pytest.approx(want), f"direction {d!r}: IVG {rows}, networkx {want}"
    assert not cy(engine, q.format(d="out")).rows  # no path along the edges
    with pytest.raises((ValueError, RuntimeError), match="direction"):
        cy(engine, q.format(d="sideways"))


# ── direction everywhere else ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_neighbors_each_direction(engine, graph, direction):
    """``CALL ivg.neighbors`` and ``kg_NEIGHBORS`` against networkx's successors / predecessors."""
    sources = seeds_of(graph)
    want = ref.neighbors(graph, sources, direction)
    q = "CALL ivg.neighbors($s, null, $d) YIELD neighbor RETURN DISTINCT neighbor"
    got = {r[0] for r in cy(engine, ("USE GRAPH '' " + q) if graph.has_named_edges else q, s=sources, d=direction).rows}
    problems = [] if got == want else [f"ivg.neighbors: IVG {sorted(got)} vs {sorted(want)}"]
    if not graph.has_named_edges:  # kg_NEIGHBORS reads every graph's edges
        got = set(engine.kg_NEIGHBORS(sources, direction=direction))
        problems += [] if got == want else [f"kg_NEIGHBORS: IVG {sorted(got)} vs {sorted(want)}"]
    fail(graph, f"neighbours of {sources} (direction={direction!r})", problems)


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_centrality_procedures_each_direction(engine, graph, direction):
    """``ivg.degreeCentrality``, ``ivg.closeness``, ``ivg.betweenness`` and ``gds.betweenness.stream`` in each
    direction against the reference (networkx on the graph, its reverse, its undirected view)."""
    n = len(graph.nodes)
    problems = []
    q = (f"CALL ivg.degreeCentrality({{direction: '{direction}'}}) YIELD node, score "
         "RETURN node.node_id AS id, score")
    rows = cy(engine, ("USE GRAPH '' " + q) if graph.has_named_edges else q).rows
    problems += compare_ranked([(r[0], r[1]) for r in rows],
                               {v: s for v, (s, _) in ref.degree_centrality(graph, direction).items()})
    procs = [
        (f"CALL ivg.closeness({{direction: '{direction}'}}) YIELD node, score RETURN node.node_id AS id, score",
         ref.closeness(graph, "harmonic", direction), False),
        (f"CALL ivg.betweenness({{sampleSize: {n}, direction: '{direction}'}}) YIELD node, score "
         "RETURN node.node_id AS id, score", ref.betweenness(graph, direction), True),
    ]
    for q, want, omit_zero in procs:
        if _nkg_procedure_refused(engine, graph, q):
            continue
        problems += compare_ranked([(r[0], r[1]) for r in cy(engine, q).rows], want, omit_zero=omit_zero)
    g = f"CALL gds.betweenness.stream('g', {{sampleSize: {n}, direction: '{direction}'}}) YIELD nodeId, score"
    if not graph.has_named_edges:
        problems += compare_ranked([(r[0], r[1]) for r in cy(engine, g).rows], ref.betweenness(graph, direction),
                                   omit_zero=True)
    fail(graph, f"centrality procedures (direction={direction!r})", problems)


@pytest.mark.parametrize("graph", WALKTHROUGH_SEEDS, indirect=True, ids=_seed_id)
@pytest.mark.parametrize("call", [
    lambda e: cy(e, "CALL ivg.degreeCentrality({direction: 'sideways'}) YIELD node, score RETURN node, score"),
    lambda e: cy(e, "CALL ivg.closeness({direction: 'sideways'}) YIELD node, score RETURN node, score"),
    lambda e: cy(e, "CALL ivg.betweenness({direction: 'sideways'}) YIELD node, score RETURN node, score"),
    lambda e: cy(e, "CALL ivg.neighbors(['ann'], null, 'sideways') YIELD neighbor RETURN neighbor"),
    lambda e: cy(e, "CALL gds.betweenness.stream('g', {direction: 'sideways'}) YIELD nodeId, score"),
    lambda e: e.degree_centrality(direction="sideways"),
    lambda e: e.closeness_centrality(direction="sideways"),
    lambda e: e.betweenness_centrality(direction="sideways"),
    lambda e: e.kg_NEIGHBORS(["ann"], direction="sideways"),
    lambda e: e.get_edges_in_window("ann", "KNOWS", 0, 10, direction="sideways"),
    # the SQL functions a caller can reach without the translator
    lambda e: _sql(e, "SELECT {schema}.kg_DegreeCentrality('sideways', '', 10, '')"),
    lambda e: _sql(e, "SELECT {schema}.kg_Closeness('harmonic', 'sideways', 0, 10)"),
    lambda e: _sql(e, "SELECT {schema}.DijkstraPath('fay', 'ann', 'weight', 9999, 10, 'sideways', '[]', '')"),
], ids=["ivg.degreeCentrality", "ivg.closeness", "ivg.betweenness", "ivg.neighbors", "gds.betweenness.stream",
        "degree_centrality", "closeness_centrality", "betweenness_centrality", "kg_NEIGHBORS", "get_edges_in_window",
        "kg_DegreeCentrality", "kg_Closeness", "DijkstraPath"])
def test_an_unknown_direction_raises(engine, graph, call):
    with pytest.raises(Exception, match="(?i)direction"):
        call(engine)


def _sql(engine, statement):
    """``statement`` with ``{schema}`` the engine's IVG Schema."""
    cur = engine.conn.cursor()
    try:
        cur.execute(statement.format(schema=engine._store._ivg_schema))
        return cur.fetchall()
    finally:
        cur.close()
