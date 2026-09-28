# IRIS Vector Graph demo server

A FastHTML server with three demos on one IRIS engine: fraud scoring, a protein
interaction graph, and a FHIR repository searched as a graph. The landing page (`/`)
lists them, says what each needs, and checks each backend after the page loads, so a
dead database never stalls it.

## Quick start

```bash
scripts/enterprise-container.sh up      # ivg-iris-enterprise, port 31972
cd src
PYTHONPATH=.:.. python -m iris_demo_server.services.fhir_demo_data   # FHIR cohort
PYTHONPATH=.:.. python -m iris_demo_server.services.bio_demo_data    # bio:demo graph
DEMO_MODE=true python -m uvicorn iris_demo_server.app:app --port 8200
open http://localhost:8200
```

Both seeds are idempotent. Each demo page has a **View Architecture** button with the
path from page to IRIS.

## The demos

### Fraud scoring (`/fraud`)

Scores a card transaction through the fraud API at `FRAUD_API_URL`. With
`DEMO_MODE=true`, or when the API is unreachable, the score comes from a heuristic on the
amount alone, and the page says so. The transaction graph under each score is
illustrative. See [docs/demos/FRAUD_DEMO.md](../../docs/demos/FRAUD_DEMO.md).

### Protein interaction graph (`/bio`)

Reads the seeded named graph `bio:demo` (33 proteins, 38 interactions): name and
annotation search (text matching; the graph has no embeddings), interaction networks,
and shortest paths such as GAPDH to LDHA through glycolysis. See
[docs/demos/BIOMEDICAL_DEMO.md](../../docs/demos/BIOMEDICAL_DEMO.md).

### FHIR repository as a graph (`/fhir`)

A FHIR repository (spec 231) is exposed as the named graph `fhir:<NS>:<package>`.
Search it by clinical concept:

- **Expand**: `fhir_expand_concepts` walks the `narrower` edges in the concept
  graph `concepts:demo`. _Diabetes mellitus_ has no codes of its own, so a search
  with 0 hops finds nobody.
- **Resolve**: `fhir_resolve_concepts` maps concepts to live Conditions and
  Observations through `Graph_KG.code_crosswalk`. ICD-10 codes are `exact` and
  lab LOINC codes are `related`, so the evidence toggle changes the seed set.
- **Rank**: bidirectional `kg_PERSONALIZED_PAGERANK` on `graph=` returns
  patients (each with the codes that matched) and practitioners. Patients reached
  only through a shared clinician are listed separately.
- **Neighbourhood**: click a patient to draw its encounters, conditions, labs and
  clinicians (D3).
- **Live**: write a Condition through the FHIR service. The graph shows it only
  after **Sync** applies the repository watermark.

Names and codes are read live from the repository's `Rsrc` table. The graph holds
topology only.

Seed the cohort into the FHIR namespace once. The namespace is the one that
`tests/e2e/fhir_conftest.py` installs, and the seed is 200 synthetic patients,
885 resources, PUT with fixed IDs so a rerun converges:

```bash
# add --deploy-ivg on a fresh namespace
PYTHONPATH=src:. python -m iris_demo_server.services.fhir_demo_data
PYTHONPATH=src:. uvicorn iris_demo_server.app:app --port 8200 && open http://localhost:8200/fhir
```

## Project structure

```text
iris_demo_server/
├── app.py          # FastHTML app, landing page, /api/status/{fraud|bio|fhir}
├── models/         # Pydantic models (fraud, biomedical, metrics, session)
├── routes/         # fraud.py, biomedical.py, fhir.py
└── services/       # fraud_client, iris_biomedical_client, bio_demo_data,
                    # fhir_graph_client, fhir_demo_data
```

## Environment variables

```bash
# Fraud: scoring service, or the amount heuristic
FRAUD_API_URL=http://localhost:8100
DEMO_MODE=true

# FHIR graph demo (defaults: localhost 31972 IVGFHIR _SYSTEM/SYS)
IVG_FHIR_HOST=localhost IVG_FHIR_PORT=31972 IVG_FHIR_NAMESPACE=IVGFHIR
IVG_FHIR_USER=_SYSTEM IVG_FHIR_PASSWORD=SYS
# default derives from the namespace
IVG_FHIR_ENDPOINT=/csp/healthshare/ivgfhir/fhir/r4
# only if several FHIR graphs are registered
IVG_FHIR_GRAPH=fhir:IVGFHIR:X0001

# Biomedical demo: IVG_BIO_* first, then IVG_FHIR_*, then the defaults above
IVG_BIO_HOST=localhost IVG_BIO_PORT=31972 IVG_BIO_NAMESPACE=IVGFHIR
IVG_BIO_USER=_SYSTEM IVG_BIO_PASSWORD=SYS
```

## Testing

```bash
export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
# pages and seeds, no IRIS needed
pytest tests/unit/test_demo_pages.py tests/unit/test_bio_demo_data.py \
       tests/unit/test_fhir_demo_data.py tests/unit/test_fhir_demo_routes.py
# live IRIS
pytest tests/e2e/test_bio_demo_e2e.py tests/e2e/test_fhir_demo_e2e.py
# all three demos in headless Chromium
playwright install chromium-headless-shell   # once
pytest tests/e2e/test_demo_browser_e2e.py
```

The browser tests start the server on a free port with `DEMO_MODE=true`, seed both
graphs, and click through each scenario. HTMX and D3 load from CDNs, so they need network
access.
