"""Spec 235 US2: default PPR exclusions and `fhir_concept_ppr(group_by="patient")` on
the live IVGFHIR repository.

The grouping oracle is independent of `GroupByPatient`: the compartment edges and
their `via` qualifiers are read back from `Graph_KG.rdf_edges`, and each patient's
expected score is the sum of an uncapped, ungrouped run's scores over them.

The default-exclusion test compared each call with 4.1.0's answers (a capture in the
gitignored spec directory) until 4.6.0 R309: those were IVG's own answers, and the early
stop R270 removed was in them. Each call is now checked against `_ppr_reference`, the
walk `Graph.KG.PageRank.Run` documents, computed in Python over the graph's rows with the
call's exclusions.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph import FHIR_PPR_EXCLUDE
from scripts.fhir.bench_235 import top_codes
from tests.e2e import fhir235_load as ranking
from tests.e2e.fhir_conftest import GRAPH

pytestmark = [pytest.mark.e2e]
os.environ.setdefault("SKIP_IRIS_TESTS", "false")

PRED = "in_patient_compartment"
RUN = uuid.uuid4().hex[:8]
SYN_CG = f"t235rank:{RUN}"
SYN_IRI = f"t235rank:{RUN}:c0"
XW_SOURCE = "ivg235rank"
EVERYTHING = 10**6


def _rows(conn, sql, *args):
    cur = conn.cursor()
    try:
        cur.execute(sql, list(args))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def _key(r):
    return f"{r['resourceType']}/{r['id']}"


def _compartments(conn):
    """{resource: {patient: via}} over every compartment edge in GRAPH."""
    out = {}
    for s, o, q in _rows(
        conn, "SELECT s, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = ?", GRAPH, PRED
    ):
        out.setdefault(s, {})[o] = (json.loads(q) if q else {}).get("via") or []
    return out


def _expected(scores, comp, via=None):
    """{patient: (score, contributor keys)} from ungrouped scores."""
    want = {}
    for key, score in scores.items():
        if key.startswith("Patient/"):
            continue
        for patient, v in comp.get(key, {}).items():
            if via is None or set(v) & set(via):
                total, keys = want.get(patient, (0.0, set()))
                want[patient] = (total + score, keys | {key})
    return want


#: The definition the reference below is written from: `IRISGraphEngine.kg_PERSONALIZED_PAGERANK`'s docstring, and for
#: the exclusions docs/FHIR_GRAPH.md, "Ranking and anchors". It is not read from `Graph.KG.PageRank.Run`: an oracle
#: copied from the implementation agrees with its defects (4.6.0 R309).
PPR_DEFINITION = """
networkx's pagerank(G, alpha=damping_factor, personalization={seed: 1, ...}, tol=tolerance), G the graph as a
multigraph (an edge under each predicate counts, as does each keyed edge of a multigraph-mode triple). At each step
the walk restarts at the seeds (evenly) with probability 1 - damping_factor and otherwise follows an out-edge chosen
evenly; a node with no out-edges hands its score to the seeds, as networkx does, so the scores sum to 1. It stops
after max_iterations or once the scores' summed change in an iteration is below N * tolerance, N the nodes holding a
score (networkx's rule, whose N is every node of the graph).
bidirectional: Enable reverse edge traversal. reverse_edge_weight: Weight multiplier for reverse edges (default 1.0).
exclude_predicates: Edges left out of both the walk and every divisor, as "p" (any source) or "Type.p" (source key
starts Type/). None is the pre-235 walk, unchanged; a list, even empty, also lifts the 1000-row cap of the
ObjectScript path.
"""


def _excluded(exclude, source, pred) -> bool:
    """PPR_DEFINITION: "p" (any source) or "Type.p" (source key starts Type/)."""
    return any(e == pred or (e.endswith(f".{pred}") and source.startswith(e[: -len(pred) - 1] + "/"))
               for e in exclude or ())


def _walk_graph(conn, exclude, bidirectional=True):
    """GRAPH's rows as PPR_DEFINITION's G: one edge per row (a predicate and a keyed edge each count), the excluded
    left out, each kept edge also reversed when `bidirectional` (reverse_edge_weight 1)."""
    import networkx as nx

    g = nx.MultiDiGraph()
    for s, p, o in _rows(conn, "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", GRAPH):
        if o is None or _excluded(exclude, s, p):
            continue
        g.add_edge(s, o)
        if bidirectional:
            g.add_edge(o, s)
    return g


def _ppr_reference(conn, seeds, *, exclude, max_iterations, alpha=0.85, tol=1e-6, cross_check=True):
    """Personalized PageRank as PPR_DEFINITION states it, over `_walk_graph`: from the seeds evenly, each step the
    restart (1 - alpha) to the seeds, alpha of each node's score split evenly over its out-edges, a node with none
    handing alpha of its score to the seeds; stop after `max_iterations` or once the summed change is below
    N * `tol`, N the nodes holding a score. With `cross_check`, a converged answer is held to networkx's fixed point:
    a power iteration that moved by d in its last step is within alpha / (1 - alpha) * d of it (L1)."""
    g = _walk_graph(conn, exclude)
    seeds = sorted(set(seeds))
    tele = 1 / len(seeds)
    x = {k: tele for k in seeds}
    converged = False
    for _ in range(max_iterations):
        nxt, dangling = {}, 0.0
        for k, v in x.items():
            deg = g.out_degree(k) if k in g else 0
            if not deg:
                dangling += v
                continue
            for _, t in g.out_edges(k):
                nxt[t] = nxt.get(t, 0.0) + alpha * v / deg
        for k in seeds:
            nxt[k] = nxt.get(k, 0.0) + (1 - alpha + alpha * dangling) * tele
        change = sum(abs(v - x.get(k, 0.0)) for k, v in nxt.items()) + sum(abs(v) for k, v in x.items() if k not in nxt)
        held = sum(1 for v in nxt.values() if v)
        x = nxt
        if change < held * tol:
            converged = True
            break
    if cross_check and converged:
        import networkx as nx

        g.add_nodes_from(seeds)
        fixed = nx.pagerank(g, alpha=alpha, personalization={k: 1 for k in seeds}, tol=1e-13, max_iter=100000)
        l1 = sum(abs(x.get(k, 0.0) - v) for k, v in fixed.items())
        assert l1 <= alpha / (1 - alpha) * held * tol + 1e-9, f"the reference is {l1} from networkx's fixed point"
    return x


def _assert_scores(got, want, *, limit=None, abs_tol=1e-12):
    """`got` is `want`, or with `limit` its `limit` best: the same nodes, each score within `abs_tol`."""
    ranked = sorted(want.items(), key=lambda kv: -kv[1])
    if limit is None or len(ranked) <= limit:
        assert set(got) == set(want), sorted(set(got) ^ set(want))[:10]
    else:
        assert len(got) == limit
        floor = min(got.values())
        assert {k for k, v in ranked if v > floor + abs_tol} <= set(got)
    for k, v in got.items():
        assert v == pytest.approx(want[k], abs=abs_tol), (k, v, want[k])


def _seeds(engine, concept_graph, ids, hops, direction, params=None):
    """The resources `fhir_concept_ppr` walks from (its expansion and resolution, checked by the 231 and 233 files)."""
    concepts = engine.fhir_expand_concepts(concept_graph, ids, hops=hops, direction=direction) if hops else list(ids)
    return engine.fhir_resolve_concepts(GRAPH, concept_graph, concepts, params=params)


def test_default_ppr_is_the_walk_without_the_excluded_edges(fhir_conn_required, fhir_engine):
    """FR-010, SC-002: under the default exclusions (FHIR_PPR_EXCLUDE) a concept PPR walks every edge but the
    compartment and Provenance target/entity edges, and a direct `kg_PERSONALIZED_PAGERANK` with no exclusions walks
    every edge, the new compartment edges included. Each answer is checked against `_ppr_reference`."""
    from tests.e2e import test_231_fhir_concept_pipeline_e2e as t231
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, SEEDS

    handles = ranking.load(fhir_conn_required, fhir_engine)
    try:
        got = ranking.calls(fhir_engine, handles)
        default = dict(exclude=list(FHIR_PPR_EXCLUDE), max_iterations=20)

        seeds = _seeds(fhir_engine, t231.CG, t231.PARENTS[:3], 1, "out")
        assert seeds
        _assert_scores(got["ppr_231"]["parents3_out_all"], _ppr_reference(fhir_conn_required, seeds, **default))

        # 231's parents_out_top50: the seeds are the 1000 Condition/sc005-c* (the ten parents' one-hop expansion covers
        # all twenty codes). Each seed and its patient form a two-node component, so the walk oscillates and 20 steps
        # do not reach the fixed point 1/1850: by hand the 20th step gives each seed 0.0005583489737415337. 4.1.0
        # stopped earlier (a per-node change below 1e-4) and answered 0.0005004.
        sc005 = [f"Condition/sc005-c{i}" for i in range(1000)]
        g = _walk_graph(fhir_conn_required, FHIR_PPR_EXCLUDE)
        reach = {n for s in sc005 if s in g for n in g.successors(s)} | set(sc005)
        assert {n for n in reach if not n.split("/", 1)[-1].startswith("sc005-")} == set()
        want = _ppr_reference(fhir_conn_required, sc005, **default)
        top = got["ppr_231"]["parents_out_top50"]
        assert len(top) == 50
        top_score = max(want.values())
        assert top_score == pytest.approx(0.0005583489737415337, abs=1e-15)
        for key, score in top.items():
            assert want[key] == pytest.approx(top_score, abs=1e-15), key
            assert score == pytest.approx(want[key], abs=1e-12), (key, score, want[key])

        resolved = 0
        for iri, hops, direction in SEEDS:
            seeds = _seeds(fhir_engine, CONCEPT_GRAPH, [iri], hops, direction, ranking.PARAMS_233)
            if not seeds:
                # hgnc/1100 (BRCA1) has never resolved: the fixture's input holds no crosswalk row to it and no
                # Observation carrying it (genomics_fixture.crosswalk_rows and .expected over load_fixture() give 0
                # and 0), and 4.1.0's own answer for it (v4.1.0, 2d92da2d) held 0 scores. Nothing resolved: no walk.
                assert got["ppr_233"][iri] == {}, iri
                assert iri.endswith("/hgnc/1100"), f"{iri} resolved to nothing"
                continue
            resolved += 1
            _assert_scores(got["ppr_233"][iri], _ppr_reference(fhir_conn_required, seeds, **default))
        assert resolved >= 3, "the 233 seeds resolve to nothing: the check above compared empty answers"

        # no exclusions given: the pre-235 call, every edge walked, the top 1000 returned (PPR_DEFINITION's cap)
        prov = got["provenance_seed"]
        want = _ppr_reference(fhir_conn_required, [prov], exclude=None, max_iterations=100)
        assert any(k.startswith("Patient/") for k in want), "the compartment edges reach the patient"
        # the walk's scores; from 4.7.0 (row 9) the 1000 rows are filled with the graph's other nodes at 0.0
        prov_rows = got["ppr_provenance"]
        scored = {k: v for k, v in prov_rows.items() if v > 0}
        _assert_scores(scored, want, limit=1000)
        assert len(prov_rows) == 1000 or set(scored) == set(want)
        assert not {k for k, v in prov_rows.items() if v == 0} & set(want), "a node the walk scored answered 0.0"

        # compartment edges are counted nowhere in the graph's status, and the link report has no row for them
        plain = _rows(
            fhir_conn_required, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p <> ?", GRAPH, PRED
        )[0][0]
        assert got["status_edges"] == plain
        assert not [p for p in got["last_counts"]["params"] if p.endswith(f".{PRED}")]
        assert not [r for r in got["link_report"]["rows"] if r.get("param") == PRED]
    finally:
        ranking.teardown(fhir_conn_required, fhir_engine, handles)


# ------------------------------------------------------------------ grouping


@pytest.fixture(scope="module")
def cases(fhir_conn_required, fhir_engine, synthea_loaded, genomics_loaded):
    """[(name, concept graph, ids, kwargs)]: a Synthea condition concept crosswalked
    to the fixture's most frequent Condition code, and a 233 gene seed."""
    from tests.e2e.genomics_fixture import CONCEPT_GRAPH, ONTOLOGY, SEEDS, crosswalk_rows, load_fixture

    def clear():
        fhir_engine.erase_graph(SYN_CG)
        fhir_engine.erase_graph(CONCEPT_GRAPH)
        cur = fhir_conn_required.cursor()
        try:
            cur.execute("DELETE FROM Graph_KG.code_crosswalk WHERE source = ?", [XW_SOURCE])
            fhir_conn_required.commit()
        finally:
            cur.close()

    clear()
    try:
        (system, code), *_ = top_codes(synthea_loaded[1])
        fhir_engine.create_node(SYN_IRI, labels=["Concept"], graph=SYN_CG)
        fhir_engine.code_crosswalk_add(system, code, SYN_IRI, target_graph=SYN_CG, relation="exact", source=XW_SOURCE)
        fhir_engine.import_rdf(ONTOLOGY, graph=CONCEPT_GRAPH)
        for s, c, iri in crosswalk_rows(load_fixture())[0]:
            fhir_engine.code_crosswalk_add(s, c, iri, target_graph=CONCEPT_GRAPH, relation="exact", source=XW_SOURCE)
        gene, hops, direction = SEEDS[1]
        yield [
            ("synthea", SYN_CG, [SYN_IRI], {"hops": 0}),
            ("genomics", CONCEPT_GRAPH, [gene], {"hops": hops, "direction": direction, "params": ranking.PARAMS_233}),
        ]
    finally:
        clear()


@pytest.fixture(scope="module")
def runs(fhir_conn_required, fhir_engine, cases):
    """{name: (ungrouped uncapped scores, compartments)} per case."""
    comp = _compartments(fhir_conn_required)
    out = {}
    for name, cg, ids, kw in cases:
        scores = fhir_engine.fhir_concept_ppr(GRAPH, cg, ids, top_k=None, **kw)
        assert scores, name
        out[name] = scores
    return out, comp


def _grouped(fhir_engine, cases, name, **kw):
    _, cg, ids, base = next(c for c in cases if c[0] == name)
    return fhir_engine.fhir_concept_ppr(GRAPH, cg, ids, group_by="patient", **base, **kw)


@pytest.mark.parametrize("name", ["synthea", "genomics"])
def test_group_sum(fhir_engine, cases, runs, name):
    """Scenario 2, SC-003."""
    scores, comp = runs[0][name], runs[1]
    want = _expected(scores, comp)
    assert want, f"{name}: no compartment resource scored"
    out = _grouped(fhir_engine, cases, name, top_k=None)
    got = {p["patient"]: p for p in out["patients"]}
    assert set(got) == set(want)
    for patient, (total, keys) in want.items():
        p = got[patient]
        assert abs(p["score"] - total) < 1e-12, (patient, p["score"], total)
        assert p["contributors_total"] == len(keys)
        assert len(p["contributors"]) == min(5, len(keys))
        for c in p["contributors"]:
            assert c["key"] in keys and abs(c["score"] - scores[c["key"]]) < 1e-12
        cs = [c["score"] for c in p["contributors"]]
        assert cs == sorted(cs, reverse=True)
    ranked = [p["score"] for p in out["patients"]]
    assert ranked == sorted(ranked, reverse=True)
    two = {p["patient"]: p for p in _grouped(fhir_engine, cases, name, top_k=None, explain_top=2)["patients"]}
    assert all(len(p["contributors"]) <= 2 for p in two.values())
    print(f"\n{name} grouped top 10:", [(p["patient"], round(p["score"], 6)) for p in out["patients"][:10]])


@pytest.mark.parametrize("name", ["synthea", "genomics"])
def test_via_filter(fhir_engine, cases, runs, name):
    """Scenario 3."""
    scores, comp = runs[0][name], runs[1]
    want = _expected(scores, comp, via=["subject"])
    out = _grouped(fhir_engine, cases, name, via=["subject"], top_k=None, explain_top=EVERYTHING)
    got = {p["patient"]: p for p in out["patients"]}
    assert set(got) == set(want)
    for patient, p in got.items():
        assert abs(p["score"] - want[patient][0]) < 1e-12
        assert {c["key"] for c in p["contributors"]} == want[patient][1]
        assert all("subject" in c["via"] for c in p["contributors"])


@pytest.mark.parametrize("name", ["synthea", "genomics"])
def test_unattributed(fhir_engine, cases, runs, name):
    """Scenario 4. A resource in two compartments counts once here, so the check is
    on distinct contributor keys, not on the sum of `contributors_total`."""
    scores = runs[0][name]
    out = _grouped(fhir_engine, cases, name, top_k=None, explain_top=EVERYTHING)
    keys = {c["key"] for p in out["patients"] for c in p["contributors"]}
    assert out["unattributed"]["count"] + len(keys) == len(scores)
    mass = out["unattributed"]["score"] + sum(scores[k] for k in keys)
    assert abs(mass - sum(scores.values())) < 1e-9
    assert not any(k.startswith("Patient/") for k in keys)


# ------------------------------------------------------------------ Provenance


@pytest.fixture(scope="module")
def provenance_copy(fhir_conn_required, fhir_engine, synthea_loaded):
    """A scratch graph holding the Synthea run's edges minus compartment edges and
    Provenance target/entity."""
    keys = {_key(r) for r in synthea_loaded[1]}
    edges = [
        {"source_id": s, "predicate": p, "target_id": o}
        for s, p, o in _rows(fhir_conn_required, "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", GRAPH)
        if s in keys
        and p != PRED
        and not (s.startswith("Provenance/") and p in ("target", "entity"))
    ]
    g = f"t235prov:{RUN}"
    fhir_engine.erase_graph(g)
    try:
        # One edge at a time: bulk_create_edges would rebuild ^KG for the namespace.
        for n in sorted({e["source_id"] for e in edges} | {e["target_id"] for e in edges}):
            fhir_engine.create_node(n, labels=[n.split("/")[0]], graph=g)
        for e in edges:
            fhir_engine.create_edge(e["source_id"], e["predicate"], e["target_id"], graph=g)
        yield g, keys
    finally:
        fhir_engine.erase_graph(g)


def test_provenance_not_walked(fhir_engine, cases, provenance_copy):
    """Scenario 5, FR-009."""
    g, keys = provenance_copy
    seeds = fhir_engine.fhir_resolve_concepts(GRAPH, SYN_CG, [SYN_IRI])
    assert seeds and set(seeds) <= keys
    default = fhir_engine.fhir_concept_ppr(GRAPH, SYN_CG, [SYN_IRI], hops=0, top_k=None)
    # `[]` walks every edge of the copy and, like the default call, lifts the pre-235
    # 1000-row cap; max_iterations is fhir_concept_ppr's.
    copy = fhir_engine.kg_PERSONALIZED_PAGERANK(
        seeds, bidirectional=True, graph=g, return_top_k=None, max_iterations=20, exclude_predicates=[]
    )
    assert set(default) == set(copy), (len(default), len(copy), sorted(set(default) ^ set(copy))[:5])
    for k in default:
        assert abs(default[k] - copy[k]) < 1e-12, (k, default[k], copy[k])
    walked = fhir_engine.fhir_concept_ppr(GRAPH, SYN_CG, [SYN_IRI], hops=0, top_k=None, exclude_predicates=[])
    assert [k for k, v in walked.items() if k.startswith("Provenance/") and v > 0]
    assert FHIR_PPR_EXCLUDE == (PRED, "Provenance.target", "Provenance.entity")


def test_cypher_unaffected(fhir_engine, synthea_loaded):
    r = fhir_engine.execute_cypher(
        f"USE GRAPH '{GRAPH}' MATCH (pv:Provenance)-[:target]->(x) WHERE pv.id STARTS WITH $pfx RETURN pv.id, x.id",
        {"pfx": f"Provenance/{synthea_loaded[0]}-"},
    )
    rows = r.rows if hasattr(r, "rows") else r.get("rows", [])
    assert rows
