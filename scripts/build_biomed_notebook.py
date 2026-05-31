"""Generate the flagship biomedical notebook (docs/notebooks/biomed_drkg_showcase.ipynb)
showcasing IVG v2.0.0 on the DRKG (97K nodes / 5.87M edges) at real biomed scale:
graph stats, centrality (influential genes), community detection (Leiden clusters),
and hybrid vector+graph search. Run after scripts/load_drkg.py has populated ivg-iris.
"""
import nbformat as nbf
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "notebooks" / "biomed_drkg_showcase.ipynb"


def md(text):
    return nbf.v4.new_markdown_cell(text)


def code(text):
    return nbf.v4.new_code_cell(text)


def build():
    nb = nbf.v4.new_notebook()
    nb.cells = [
        md(
            "# IVG v2.0.0 — Biomedical Knowledge Graph at Scale (DRKG)\n\n"
            "This notebook runs InterSystems IRIS Vector Graph against the **Drug "
            "Repurposing Knowledge Graph** ([DRKG](https://github.com/gnn4dr/DRKG), "
            "Apache-2.0):\n\n"
            "- **97,238 nodes** across 13 biomedical types (Gene, Compound, Disease, "
            "Anatomy, Pathway, ...)\n"
            "- **5,874,261 edges** (107 relation types from 6 source databases)\n"
            "- **400-dim TransE knowledge-graph embeddings** (optional, for hybrid search)\n\n"
            "It demonstrates the v2.0.0 graph-analytics suite end-to-end on a real "
            "biomedical graph: **centrality** (which genes are most influential?), "
            "**community detection** (which drugs/genes/diseases cluster together?), "
            "and **hybrid vector+graph** retrieval.\n\n"
            "> Prereq: load DRKG into the `ivg-iris` container first:\n"
            "> ```bash\n"
            "> python scripts/load_drkg.py --embeddings\n"
            "> ```"
        ),
        md("## 1. Connect and inspect scale"),
        code(
            "import iris\n"
            "from iris_vector_graph.engine import IRISGraphEngine\n\n"
            "# Connect to ivg-iris (use the container IP or localhost as appropriate)\n"
            "conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')\n"
            "engine = IRISGraphEngine(conn, embedding_dimension=400)\n\n"
            "status = engine.status()\n"
            "print(status)"
        ),
        md(
            "The concept-first status (spec 180) shows graph size, vector/full-text "
            "index state, and sync state — without exposing internal `^`-globals. "
            "For IRIS-developer internals, use `engine.status(internals=True)`."
        ),
        md(
            "## 2. Centrality — which genes are most influential?\n\n"
            "Degree centrality ranks nodes by connection count. On DRKG, the top "
            "genes by out-degree are the most-studied / most-connected in the "
            "literature graph."
        ),
        code(
            "deg = engine.degree_centrality(direction='both', top_k=15)\n"
            "print('Top 15 nodes by degree centrality:')\n"
            "for r in deg:\n"
            "    print(f\"  {r['id']:<28}  degree={r['score']:.0f}\")"
        ),
        md(
            "**Betweenness** finds bridge nodes — entities that connect otherwise-"
            "separate regions of the graph (often multi-pathway genes or "
            "broad-spectrum compounds). Uses sampled Brandes for tractability at "
            "this scale."
        ),
        code(
            "btw = engine.betweenness_centrality(sample_size=500, top_k=15)\n"
            "print('Top 15 bridge nodes (sampled betweenness):')\n"
            "for r in btw:\n"
            "    print(f\"  {r['id']:<28}  betweenness={r['score']:.4f}\")"
        ),
        md(
            "## 3. Community detection — drug/gene/disease clusters\n\n"
            "Leiden finds densely-connected communities. On a container with "
            "`igraph`+`leidenalg` in embedded Python, this runs the **canonical** "
            "leidenalg algorithm server-side (spec 185); otherwise it falls back to "
            "a pure-ObjectScript greedy partition. Both return the same shape."
        ),
        code(
            "comms = engine.leiden_communities(random_seed=42, top_k=0)\n"
            "from collections import Counter\n"
            "sizes = Counter(c['community'] for c in comms)\n"
            "print(f'Found {len(sizes)} communities over {len(comms):,} nodes')\n"
            "print('Largest 10 communities (id: size):')\n"
            "for cid, sz in sizes.most_common(10):\n"
            "    print(f'  community {cid}: {sz:,} nodes')"
        ),
        code(
            "# Inspect the node-type composition of the largest community\n"
            "biggest = sizes.most_common(1)[0][0]\n"
            "members = [c['id'] for c in comms if c['community'] == biggest]\n"
            "type_mix = Counter(m.split('::', 1)[0] for m in members)\n"
            "print(f'Largest community ({len(members):,} nodes) type mix:')\n"
            "for t, n in type_mix.most_common():\n"
            "    print(f'  {t:<22} {n:,}')"
        ),
        md(
            "## 4. Hybrid vector + graph — find genes like TP53 near a disease\n\n"
            "The canonical drug-repurposing query: *given a disease, find genes "
            "similar to a known target within N hops.* This fuses TransE vector "
            "similarity with graph proximity. (Requires `--embeddings` at load time.)"
        ),
        code(
            "# Requires embeddings loaded. TP53 = Gene::7157 in DRKG.\n"
            "if engine.status().ready_for_vector_search:\n"
            "    tp53 = engine.get_embedding('Gene::7157')\n"
            "    if tp53:\n"
            "        similar = engine.search_nodes_by_vector(tp53['embedding'], top_k=10)\n"
            "        print('Genes/entities most similar to TP53 (TransE cosine):')\n"
            "        for r in similar:\n"
            "            print(f\"  {r.get('id','?'):<28} score={r.get('score',0):.3f}\")\n"
            "    else:\n"
            "        print('TP53 embedding not found — check entity id / load --embeddings')\n"
            "else:\n"
            "    print('No vector index — run scripts/load_drkg.py --embeddings to enable this section')"
        ),
        md(
            "## 5. Cypher — the same analytics from a query\n\n"
            "Every algorithm is also a Cypher procedure (`CALL ivg.*`), so the same "
            "analytics run from the query surface (and server-side via the SQL "
            "function path, no Python client required)."
        ),
        code(
            "result = engine.execute_cypher(\n"
            "    \"CALL ivg.degreeCentrality({topK: 10}) YIELD node, score, degree \"\n"
            "    \"RETURN node, score, degree ORDER BY score DESC LIMIT 10\"\n"
            ")\n"
            "print('columns:', result.columns)\n"
            "for row in result.rows:\n"
            "    print(' ', row)"
        ),
        md(
            "---\n\n"
            "**What this demonstrates for v2.0.0:** the full graph-analytics suite "
            "(degree/betweenness/closeness/eigenvector centrality + leiden/triangle/"
            "scc/kcore community detection + hybrid vector search) running on a "
            "**97K-node / 5.87M-edge real biomedical KG** — via both the Python API "
            "and the Cypher `CALL` surface. See `docs/performance/DRKG_SCALE.md` for "
            "recorded timings."
        ),
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w") as f:
        nbf.write(nb, f)
    print(f"wrote {OUT} ({len(nb.cells)} cells)")


if __name__ == "__main__":
    build()
