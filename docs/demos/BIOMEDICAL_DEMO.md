# Biomedical Research Demo

The biomedical demo is a small protein interaction graph: find proteins by name or
annotation, draw a protein's interaction network, and find the shortest path between two
proteins. It runs against a seeded named graph, `bio:demo`, so every number on the page
comes from IRIS.

## Running the Demo

```bash
scripts/enterprise-container.sh up          # ivg-iris-enterprise, port 31972
cd src
PYTHONPATH=.:.. python -m iris_demo_server.services.bio_demo_data   # seed bio:demo
python -m uvicorn iris_demo_server.app:app --port 8200 --host 127.0.0.1
open http://localhost:8200/bio
```

The seed is idempotent: a graph that already holds exactly the seed is left alone, and
anything else in `bio:demo` is erased and reloaded. The landing page (`/`) shows whether
the graph is reachable and how many proteins and interactions it holds.

Connection settings come from `IVG_BIO_HOST`, `IVG_BIO_PORT`, `IVG_BIO_NAMESPACE`,
`IVG_BIO_USER` and `IVG_BIO_PASSWORD`, then the FHIR demo's `IVG_FHIR_*` variables, then
`localhost:31972`, namespace `IVGFHIR`, `_SYSTEM`/`SYS`.

## The seed graph

`src/iris_demo_server/services/bio_demo_data.py` holds 33 human proteins keyed by HGNC
gene symbol and 38 interactions in three neighbourhoods:

- the p53 network (TP53, MDM2, ATM, CHEK2, CDKN1A, BAX, BCL2 and others);
- the EGFR/RAS/MAPK kinase cascade and its approved inhibitors' targets (EGFR, ERBB2,
  KRAS, BRAF, MAP2K1, ABL1, JAK2, PIK3CA);
- glycolysis from GAPDH to LDHA, with HIF1A regulating it.

The interactions are well-known ones. The confidence values are illustrative, not STRING
scores.

```text
(:Protein {id: "protein:TP53", symbol, name, annotation, organism})
  -[:interacts_with {qualifiers: {"type": "inhibition", "confidence": 0.99}}]->
(:Protein {id: "protein:MDM2", ...})
```

Interaction types are `binding`, `phosphorylation`, `activation`, `inhibition`,
`regulation` and `pathway`. Search, networks and paths treat edges as undirected.

## Three scenarios

| Scenario          | Input                              | What the page shows                          |
| ----------------- | ---------------------------------- | -------------------------------------------- |
| Cancer protein    | Name search `TP53`                 | TP53 first (exact symbol), then its network  |
| Metabolic pathway | `GAPDH` → `LDHA`, max 5 hops       | GAPDH, PGK1, PGAM1, ENO1, PKM, LDHA (5 hops) |
| Drug target       | Function search `kinase inhibitor` | EGFR, ERBB2, KIT, ABL1, BRAF, JAK2 and more  |

### Search is text matching

The demo graph has no embeddings, so search does not use vector similarity:

- **Name search** matches the query against `symbol` and `name`, case-insensitive. An
  exact symbol scores 1.0; any other match scores 0.8.
- **Function search** scores each protein by the fraction of query terms its
  `annotation` contains. `kinase inhibitor` scores 1.0 for proteins whose annotation
  names both words and 0.5 for one.

The results panel shows the SQL it ran. Every query reads `Graph_KG.rdf_props` and
`Graph_KG.rdf_edges` with `graph_id = 'bio:demo'`, so a same-named node in another graph
of the namespace never reaches the page (`tests/e2e/test_bio_demo_e2e.py` checks this).

### Networks and paths

- **Network** is a breadth-first expansion from one protein, capped at 500 nodes.
- **Pathway** is an undirected breadth-first search up to `max_hops`. The path
  confidence is the mean of its interactions' confidence values. With `max_hops` below
  the shortest path length the page reports "No path ... within N hops".

## Interactive network visualization

The D3 force graph is interactive:

- **Click** a node to add its 1-hop neighbourhood (up to 500 nodes in total).
- **Drag** nodes to rearrange the layout.
- **Scroll** to zoom; **double-click** to reset the view.

Edge colour shows the interaction type: green for activation, red dashed for inhibition,
blue for binding, grey for the rest. Edge width grows with confidence.

## Architecture

```text
Browser (HTMX + D3 force graph)
    ↓ POST /api/bio/search · GET /api/bio/network/{symbol} · POST /api/bio/pathway
FastHTML routes (src/iris_demo_server/routes/biomedical.py)
    ↓
IRISBiomedicalClient (src/iris_demo_server/services/iris_biomedical_client.py)
    ↓ SQL over Graph_KG.rdf_props / rdf_edges, scoped to graph_id = 'bio:demo'
IRIS namespace IVGFHIR on ivg-iris-enterprise
```

## Tests

```bash
export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
pytest tests/unit/test_bio_demo_data.py tests/unit/test_demo_pages.py
pytest tests/e2e/test_bio_demo_e2e.py tests/e2e/test_demo_browser_e2e.py
```

The browser tests drive all three demos in headless Chromium (Playwright). They need
`playwright install chromium-headless-shell` once, and network access for the HTMX and
D3 CDNs.
