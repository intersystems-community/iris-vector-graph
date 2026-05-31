"""Head-to-head benchmark: IVG vs Neo4j GDS vs networkx.

For each fixture graph, measures LOAD time and per-algorithm time across the
three engines and records a comparison table. Correctness is cross-checked via
Pearson (centrality) / community-count (Leiden) against networkx as reference.

Run:
    IVG_HEADTOHEAD=1 \
    NEO4J_URI=bolt://localhost:7688 NEO4J_USER=neo4j NEO4J_PASSWORD=ivgbenchpw \
    pytest tests/perf/test_head_to_head.py -s -p no:cacheprovider

Skips Neo4j legs if the driver/instance is unavailable; always runs IVG +
networkx. Writes JSON to benchmarks/head_to_head_<ts>.json.
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("IVG_HEADTOHEAD") != "1",
    reason="set IVG_HEADTOHEAD=1 to run the head-to-head benchmark",
)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7688")
NEO4J_AUTH = (os.environ.get("NEO4J_USER", "neo4j"),
              os.environ.get("NEO4J_PASSWORD", "ivgbenchpw"))


def _fixtures():
    import networkx as nx
    out = {}
    out["karate"] = nx.karate_club_graph()
    out["er_500"] = nx.gnp_random_graph(500, 0.02, seed=42)
    out["er_2000"] = nx.gnp_random_graph(2000, 0.005, seed=42)
    return out


def _pearson(a, b):
    keys = sorted(set(a) & set(b))
    if len(keys) < 2:
        return 1.0
    xa = [a[k] for k in keys]
    xb = [b[k] for k in keys]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    num = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    da = sum((x - ma) ** 2 for x in xa) ** 0.5
    db = sum((y - mb) ** 2 for y in xb) ** 0.5
    return num / (da * db) if da > 0 and db > 0 else 0.0


def _timed(fn):
    t = time.perf_counter()
    r = fn()
    return r, round((time.perf_counter() - t) * 1000, 1)



def _nx_run(G):
    import networkx as nx
    res = {}
    deg, res["degree_ms"] = _timed(lambda: dict(nx.degree_centrality(G)))
    k = None if G.number_of_nodes() <= 600 else 200
    btw, res["betweenness_ms"] = _timed(lambda: nx.betweenness_centrality(G, k=k, normalized=False, seed=42))
    clo, res["closeness_ms"] = _timed(lambda: nx.harmonic_centrality(G))
    comm, res["leiden_ms"] = _timed(lambda: nx.community.louvain_communities(G, seed=42))
    res["_degree"] = deg
    res["_betweenness"] = btw
    res["_closeness"] = clo
    res["communities"] = len(comm)
    res["load_ms"] = 0.0
    return res



def _ivg_clean(engine):
    o = _ivg_native(engine)
    for g in ["^KG", "^NKG"]:
        try:
            o.kill(g)
        except Exception:
            pass
    cur = engine.conn.cursor()
    for t in ["Graph_KG.rdf_edges", "Graph_KG.rdf_labels",
              "Graph_KG.rdf_props", "Graph_KG.nodes"]:
        try:
            cur.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    engine.conn.commit()


def _ivg_native(engine):
    import iris
    return iris.createIRIS(engine.conn)


def _ivg_run(engine, G, prefix):
    res = {}
    _ivg_clean(engine)
    nodes = [{"id": f"{prefix}{n}", "labels": ["N"]} for n in G.nodes()]
    edges = [{"s": f"{prefix}{u}", "p": "R", "o": f"{prefix}{v}"} for u, v in G.edges()]

    def _load():
        engine.bulk_create_nodes(nodes)
        with engine.bulk_load_session() as s:
            s.add_edges(edges)
    _, res["load_ms"] = _timed(_load)

    deg, res["degree_ms"] = _timed(lambda: engine.degree_centrality(direction="both", top_k=0))
    res["_degree"] = {d["id"][len(prefix):]: d["score"] for d in deg}

    sample = 0 if G.number_of_nodes() <= 600 else 200
    btw, res["betweenness_ms"] = _timed(lambda: engine.betweenness_centrality(sample_size=sample, top_k=0))
    res["_betweenness"] = {d["id"][len(prefix):]: d["score"] for d in btw}
    res["betweenness_sample_size"] = sample

    clo, res["closeness_ms"] = _timed(lambda: engine.closeness_centrality(formula="harmonic", top_k=0))
    res["_closeness"] = {d["id"][len(prefix):]: d["score"] for d in clo}

    comm, res["leiden_ms"] = _timed(lambda: engine.leiden_communities(random_seed=42, top_k=0))
    res["communities"] = len({c["community"] for c in comm})
    return res



def _neo4j_run(driver, G, label):
    res = {}
    edges = [[str(u), str(v)] for u, v in G.edges()]

    def _load():
        with driver.session() as s:
            s.run(f"MATCH (n:{label}) DETACH DELETE n")
            s.run(
                f"UNWIND $edges AS e MERGE (x:{label} {{id:e[0]}}) "
                f"MERGE (y:{label} {{id:e[1]}}) MERGE (x)-[:R]->(y)",
                edges=edges,
            )
    _, res["load_ms"] = _timed(_load)

    with driver.session() as s:
        gname = f"g_{label}"
        s.run(f"CALL gds.graph.drop('{gname}', false) YIELD graphName")
        s.run(
            f"CALL gds.graph.project('{gname}', '{label}', "
            f"{{R:{{type:'R', orientation:'UNDIRECTED'}}}})"
        )

        def deg():
            return {r["id"]: r["score"] for r in s.run(
                f"CALL gds.degree.stream('{gname}') YIELD nodeId, score "
                f"RETURN gds.util.asNode(nodeId).id AS id, score")}
        d, res["degree_ms"] = _timed(deg)
        res["_degree"] = d

        def btw():
            return {r["id"]: r["score"] for r in s.run(
                f"CALL gds.betweenness.stream('{gname}') YIELD nodeId, score "
                f"RETURN gds.util.asNode(nodeId).id AS id, score")}
        b, res["betweenness_ms"] = _timed(btw)
        res["_betweenness"] = b

        def clo():
            return {r["id"]: r["score"] for r in s.run(
                f"CALL gds.closeness.stream('{gname}') YIELD nodeId, score "
                f"RETURN gds.util.asNode(nodeId).id AS id, score")}
        c, res["closeness_ms"] = _timed(clo)
        res["_closeness"] = c

        def leiden():
            rows = list(s.run(
                f"CALL gds.leiden.stream('{gname}', {{randomSeed:42}}) "
                f"YIELD communityId RETURN communityId"))
            return len({r["communityId"] for r in rows})
        ncomm, res["leiden_ms"] = _timed(leiden)
        res["communities"] = ncomm

        s.run(f"CALL gds.graph.drop('{gname}', false) YIELD graphName")
        s.run(f"MATCH (n:{label}) DETACH DELETE n")
    return res


def test_head_to_head(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    neo4j_driver = None
    try:
        from neo4j import GraphDatabase
        neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        neo4j_driver.verify_connectivity()
    except Exception as e:
        print(f"\n[neo4j unavailable, skipping that leg: {str(e)[:80]}]")
        neo4j_driver = None

    engine = IRISGraphEngine(iris_connection)
    fixtures = _fixtures()
    report = {"generated": datetime.now().isoformat(), "graphs": {}}

    for name, G in fixtures.items():
        n, m = G.number_of_nodes(), G.number_of_edges()
        print(f"\n=== {name}: {n} nodes, {m} edges ===")
        entry = {"nodes": n, "edges": m, "engines": {}}

        nxr = _nx_run(G)
        entry["engines"]["networkx"] = {k: v for k, v in nxr.items() if not k.startswith("_")}

        ivg = _ivg_run(engine, G, f"h2h_{name}_")
        ivg["degree_pearson_vs_nx"] = round(_pearson(ivg["_degree"], nxr["_degree"]), 3)
        ivg["betweenness_pearson_vs_nx"] = round(_pearson(ivg["_betweenness"], nxr["_betweenness"]), 3)
        ivg["closeness_pearson_vs_nx"] = round(_pearson(ivg["_closeness"], nxr["_closeness"]), 3)
        entry["engines"]["ivg"] = {k: v for k, v in ivg.items() if not k.startswith("_")}

        if neo4j_driver is not None:
            ng = _neo4j_run(neo4j_driver, G, f"H{name.replace('_','')}")
            ng["degree_pearson_vs_nx"] = round(_pearson(ng["_degree"], nxr["_degree"]), 3)
            ng["betweenness_pearson_vs_nx"] = round(_pearson(ng["_betweenness"], nxr["_betweenness"]), 3)
            entry["engines"]["neo4j_gds"] = {k: v for k, v in ng.items() if not k.startswith("_")}

        report["graphs"][name] = entry

        for eng_name, e in entry["engines"].items():
            print(f"  {eng_name:10s} load={e.get('load_ms',0):8.1f}ms "
                  f"deg={e.get('degree_ms',0):7.1f} btw={e.get('betweenness_ms',0):8.1f} "
                  f"clo={e.get('closeness_ms',0):7.1f} leiden={e.get('leiden_ms',0):7.1f} "
                  f"comms={e.get('communities','-')}")

    if neo4j_driver is not None:
        neo4j_driver.close()

    out = Path(__file__).resolve().parent.parent.parent / "benchmarks"
    out.mkdir(exist_ok=True)
    fp = out / f"head_to_head_{datetime.now():%Y%m%d_%H%M%S}.json"
    fp.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {fp}")

    for name, entry in report["graphs"].items():
        ivg = entry["engines"]["ivg"]
        assert ivg["degree_pearson_vs_nx"] > 0.85, f"{name} degree drift"
