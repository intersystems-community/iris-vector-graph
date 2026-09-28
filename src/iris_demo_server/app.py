"""FastHTML demo server application"""
from fasthtml.common import *
import os

# Import routes
from .routes.fraud import register_fraud_routes, scoring_mode
from .routes.biomedical import register_biomedical_routes
from .routes.fhir import register_fhir_routes

# Create FastHTML app
app = FastHTML(
    hdrs=(
        Script(src="https://unpkg.com/htmx.org@2.0.0"),
        Script(src="https://d3js.org/d3.v7.min.js"),
    ),
    debug=os.getenv("DEBUG", "false").lower() == "true"
)

# Register routes
register_fraud_routes(app)
register_biomedical_routes(app)
register_fhir_routes(app)


# Landing page
DEMOS = [
    {
        "key": "fraud", "href": "/fraud", "tag": "Financial services",
        "title": "Fraud scoring",
        "shows": "Score a card transaction, then look at the payer's device and merchant graph "
                 "and the bitemporal audit trail behind the score.",
        "needs": "The fraud API at FRAUD_API_URL, or DEMO_MODE=true for the amount-only heuristic.",
    },
    {
        "key": "bio", "href": "/bio", "tag": "Life sciences",
        "title": "Protein interaction graph",
        "shows": "Find proteins by name or annotation, draw an interaction network, and find "
                 "the shortest path between two proteins (GAPDH to LDHA through glycolysis).",
        "needs": "The seeded named graph bio:demo in IVGFHIR on ivg-iris-enterprise "
                 "(python -m iris_demo_server.services.bio_demo_data).",
    },
    {
        "key": "fhir", "href": "/fhir", "tag": "Healthcare",
        "title": "FHIR repository as a graph",
        "shows": "Search a live FHIR repository by clinical concept: expand the concept, resolve "
                 "codes to resources, rank patients with PageRank, and sync a change live.",
        "needs": "The HS.FHIRServer repository in IVGFHIR on ivg-iris-enterprise, seeded with the "
                 "200-patient demo cohort.",
    },
]

LANDING_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f4f6fb; color: #1f2937; }
header { background: linear-gradient(135deg, #1e3a8a 0%, #4338ca 60%, #6d28d9 100%);
         color: white; padding: 3rem 2rem 4.5rem; }
header .inner, main { max-width: 1150px; margin: 0 auto; }
header h1 { font-size: 2.4rem; letter-spacing: -0.02em; }
header p { margin-top: 0.75rem; font-size: 1.1rem; opacity: 0.9; max-width: 60rem; line-height: 1.5; }
main { padding: 0 2rem 3rem; margin-top: -2.5rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 1.5rem; }
.card { background: white; border-radius: 14px; padding: 1.75rem; display: flex; flex-direction: column;
        box-shadow: 0 10px 25px rgba(30, 41, 59, 0.08); border-top: 5px solid var(--accent); }
.card .tag { color: var(--accent); font-size: 0.8rem; font-weight: 700; text-transform: uppercase;
             letter-spacing: 0.06em; }
.card h2 { font-size: 1.4rem; margin: 0.35rem 0 0.75rem; }
.card p { line-height: 1.55; color: #4b5563; }
.card .needs { margin-top: 1rem; font-size: 0.875rem; background: #f8fafc; border-radius: 8px;
               padding: 0.75rem; color: #475569; }
.card .needs b { color: #1f2937; }
.status { margin-top: 1rem; font-size: 0.875rem; display: flex; align-items: center; gap: 0.5rem; }
.dot { width: 0.65rem; height: 0.65rem; border-radius: 50%; background: #cbd5e1; flex-shrink: 0; }
.status.ok .dot { background: #16a34a; } .status.warn .dot { background: #d97706; }
.status.down .dot { background: #dc2626; }
.card a.go { margin-top: auto; padding-top: 1.25rem; }
.card a.go span { display: inline-block; background: var(--accent); color: white; text-decoration: none;
                  padding: 0.6rem 1.1rem; border-radius: 8px; font-weight: 600; }
.card a.go:hover span { filter: brightness(1.1); }
.foot { margin-top: 2.5rem; color: #64748b; font-size: 0.9rem; line-height: 1.6; }
.foot code { background: #e2e8f0; padding: 0.1rem 0.35rem; border-radius: 4px; }
"""

ACCENTS = {"fraud": "#4f46e5", "bio": "#0f9488", "fhir": "#db2777"}


def demo_card(d):
    return Div(cls="card", style=f"--accent: {ACCENTS[d['key']]}")(
        Div(cls="tag")(d["tag"]),
        H2(d["title"]),
        P(d["shows"]),
        Div(cls="needs")(B("Needs: "), d["needs"]),
        Div(cls="status", id=f"status-{d['key']}", hx_get=f"/api/status/{d['key']}",
            hx_trigger="load", hx_swap="outerHTML")(Span(cls="dot"), "Checking backend..."),
        A(cls="go", href=d["href"])(Span("Open demo →")),
    )


def _status(key: str, level: str, text: str):
    return Div(cls=f"status {level}", id=f"status-{key}")(Span(cls="dot"), text)


def backend_status(key: str):
    if key == "fraud":
        mode = scoring_mode()
        return _status(key, "warn" if mode.startswith("Demo heuristic") else "ok", mode)
    if key == "bio":
        from .routes.biomedical import get_biomedical_client

        try:
            st = get_biomedical_client().stats()
        except Exception as e:  # noqa: BLE001 - shown to the presenter
            return _status(key, "down", f"Offline: {e}")
        if not st["proteins"]:
            return _status(key, "warn", "Connected, but bio:demo is empty: run the seed")
        return _status(key, "ok", f"Live: {st['proteins']} proteins, {st['interactions']} interactions")
    if key == "fhir":
        from .routes.fhir import get_fhir_client

        try:
            st = get_fhir_client().stats()
        except Exception as e:  # noqa: BLE001
            return _status(key, "down", f"Offline: {e}")
        patients = st.get("by_type", {}).get("Patient", 0)
        if not patients:
            return _status(key, "warn", "Connected, but the repository has no patients: seed the cohort")
        return _status(key, "ok", f"Live: {patients} patients, {st['nodes']} resources, {st['edges']} references")
    return None


@app.get("/api/status/{key}")
def demo_status(key: str):
    out = backend_status(key)
    if out is None:
        return Response(f"unknown demo {key!r}", status_code=404)
    return out


@app.get("/")
def homepage():
    """Landing page: what each demo shows, what it needs, and whether that is up."""
    return Html(
        Head(
            Title("IRIS Vector Graph demos"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Script(src="https://unpkg.com/htmx.org@2.0.0"),
            Style(LANDING_CSS),
        ),
        Body(
            Header(Div(cls="inner")(
                H1("IRIS Vector Graph demos"),
                P("Three small applications on one engine: graph, vector and SQL in InterSystems IRIS. "
                  "Each card says what the demo needs; the dot shows whether that backend answers right now."),
            )),
            Main(
                Div(cls="cards")(*[demo_card(d) for d in DEMOS]),
                Div(cls="foot")(
                    P("Every demo has a ", B("View Architecture"), " button with the path from page to IRIS. "
                      "Start the server with ", Code("uvicorn iris_demo_server.app:app --port 8200"),
                      " from ", Code("src/"), "; point the bio and FHIR demos at another namespace with ",
                      Code("IVG_BIO_NAMESPACE"), " / ", Code("IVG_FHIR_NAMESPACE"), "."),
                ),
            ),
        ),
    )


@app.get("/arch/fraud")
def arch_fraud():
    return Div(
        H2("Fraud Detection Architecture"),
        P("Real-time SQL Trigger Loop: transactions → IRIS SQL → fraud score → ^KG graph"),
        Ul(
            Li("Transaction ingest via REST API"),
            Li("Real-time SQL Trigger Loop scores each transaction"),
            Li("^KG temporal graph holds the transaction history"),
            Li("Bitemporal audit trail via ^KG('tout'/'tin') indexes"),
        )
    )


@app.get("/arch/bio")
def arch_bio():
    return Div(
        H2("Biomedical Graph Architecture"),
        P("Research UI (FastHTML) → graph-scoped SQL → protein interaction graph (bio:demo)"),
        Ul(
            Li("Research UI (FastHTML) serves protein search, networks and paths"),
            Li("Seed: 33 human proteins and 38 well-known interactions in named graph bio:demo"),
            Li("Search matches symbol, name and annotation text in Graph_KG.rdf_props (no embeddings in the seed)"),
            Li("Networks and shortest paths walk Graph_KG.rdf_edges, scoped to bio:demo"),
        )
    )


@app.get("/arch/fhir")
def arch_fhir():
    return Div(
        H2("FHIR Graph Architecture"),
        P("FHIR repository (HS.FHIRServer, JsonAdvSQL) → Graph.KG.FHIRGraph → named graph fhir:<NS>:<pkg>"),
        Ul(
            Li("IVG runs in the FHIR namespace; one node per live resource, one edge per indexed reference"),
            Li("SyncOnce follows the Rsrc and RsrcVer watermarks; no copy of codes or properties"),
            Li("fhir_expand_concepts walks the concept graph's narrower edges"),
            Li("fhir_resolve_concepts maps concepts to live resources through Graph_KG.code_crosswalk"),
            Li("kg_PERSONALIZED_PAGERANK ranks patients and clinicians inside the FHIR graph only"),
        )
    )


@app.get("/fraud")
def fraud_page():
    """Interactive fraud detection demo"""
    return Html(
        Head(
            Title("IRIS Fraud Detection Demo"),
            Script(src="https://unpkg.com/htmx.org@2.0.0"),
            Script(src="https://d3js.org/d3.v7.min.js"),
            Style("""
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    padding: 2rem;
                }
                .container { max-width: 1400px; margin: 0 auto; }
                .header {
                    background: white;
                    padding: 2rem;
                    border-radius: 12px;
                    margin-bottom: 2rem;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }
                .header h1 { color: #667eea; font-size: 2.5rem; margin-bottom: 0.5rem; }
                .header p { color: #666; font-size: 1.1rem; }
                .stats {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 1rem;
                    margin-top: 1rem;
                }
                .stat-card {
                    background: #f7fafc;
                    padding: 1rem;
                    border-radius: 8px;
                    border-left: 4px solid #667eea;
                }
                .stat-card .label { color: #718096; font-size: 0.875rem; text-transform: uppercase; }
                .stat-card .value { color: #2d3748; font-size: 1.75rem; font-weight: bold; margin-top: 0.25rem; }

                .demo-grid {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 2rem;
                }
                @media (max-width: 1200px) {
                    .demo-grid { grid-template-columns: 1fr; }
                }

                .panel {
                    background: white;
                    border-radius: 12px;
                    padding: 2rem;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }
                .panel h2 { color: #2d3748; margin-bottom: 1.5rem; }

                .scenarios {
                    display: grid;
                    gap: 0.75rem;
                    margin-bottom: 2rem;
                }
                .scenario-btn {
                    background: #f7fafc;
                    border: 2px solid #e2e8f0;
                    padding: 1rem;
                    border-radius: 8px;
                    cursor: pointer;
                    transition: all 0.2s;
                    text-align: left;
                }
                .scenario-btn:hover {
                    border-color: #667eea;
                    background: #edf2f7;
                }
                .scenario-btn .title { font-weight: 600; color: #2d3748; margin-bottom: 0.25rem; }
                .scenario-btn .desc { font-size: 0.875rem; color: #718096; }

                .form-group {
                    margin-bottom: 1.25rem;
                }
                .form-group label {
                    display: block;
                    color: #4a5568;
                    font-weight: 500;
                    margin-bottom: 0.5rem;
                }
                .form-group input, .form-group select {
                    width: 100%;
                    padding: 0.75rem;
                    border: 2px solid #e2e8f0;
                    border-radius: 6px;
                    font-size: 1rem;
                }
                .form-group input:focus, .form-group select:focus {
                    outline: none;
                    border-color: #667eea;
                }

                .btn-primary {
                    background: #667eea;
                    color: white;
                    border: none;
                    padding: 1rem 2rem;
                    border-radius: 6px;
                    font-size: 1rem;
                    font-weight: 600;
                    cursor: pointer;
                    width: 100%;
                    transition: background 0.2s;
                }
                .btn-primary:hover {
                    background: #5568d3;
                }

                #results { margin-top: 2rem; }
                .risk-badge {
                    display: inline-block;
                    padding: 0.5rem 1rem;
                    border-radius: 9999px;
                    font-weight: 600;
                    font-size: 0.875rem;
                    text-transform: uppercase;
                }
                .risk-low { background: #c6f6d5; color: #22543d; }
                .risk-medium { background: #feebc8; color: #7c2d12; }
                .risk-high { background: #fed7d7; color: #742a2a; }
                .risk-critical { background: #fc8181; color: #742a2a; }

                .metric-row {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 1rem;
                    margin: 1rem 0;
                }
                .metric {
                    background: #f7fafc;
                    padding: 1rem;
                    border-radius: 6px;
                }
                .metric .label { color: #718096; font-size: 0.875rem; }
                .metric .value { color: #2d3748; font-size: 1.5rem; font-weight: bold; margin-top: 0.25rem; }

                .factors {
                    background: #edf2f7;
                    padding: 1rem;
                    border-radius: 6px;
                    margin-top: 1rem;
                }
                .factors h4 { color: #2d3748; margin-bottom: 0.75rem; }
                .factors li { color: #4a5568; padding: 0.25rem 0; }

                #viz { min-height: 300px; margin-top: 1rem; }

                .audit-section {
                    margin-top: 2rem;
                    padding-top: 2rem;
                    border-top: 2px solid #e2e8f0;
                }
                .audit-section h3 {
                    color: #2d3748;
                    margin-bottom: 1rem;
                    font-size: 1.25rem;
                }
                .query-box {
                    background: #1a202c;
                    color: #68d391;
                    padding: 1rem;
                    border-radius: 6px;
                    font-family: 'Monaco', 'Courier New', monospace;
                    font-size: 0.875rem;
                    overflow-x: auto;
                    margin-bottom: 1rem;
                }
                .query-box .keyword { color: #63b3ed; }
                .query-box .string { color: #fbd38d; }

                .timeline-entry {
                    background: #f7fafc;
                    padding: 1rem;
                    border-left: 3px solid #667eea;
                    margin-bottom: 0.75rem;
                    border-radius: 4px;
                }
                .timeline-entry .time { color: #718096; font-size: 0.875rem; }
                .timeline-entry .action { color: #2d3748; font-weight: 600; margin-top: 0.25rem; }
                .timeline-entry .reason { color: #4a5568; font-size: 0.875rem; margin-top: 0.25rem; }

                #graph { width: 100%; height: 400px; }
                .node { cursor: pointer; }
                .node circle { stroke: #fff; stroke-width: 2px; }
                .node text { font-size: 11px; pointer-events: none; }
                .link { stroke: #999; stroke-opacity: 0.6; }
            """)
        ),
        Body(
            Div(cls="container")(
                # Header with stats
                Div(cls="header")(
                    H1("IRIS Fraud Detection"),
                    P("Score a transaction, then see the payer's device and merchant graph and its bitemporal audit trail"),
                    Button("View Architecture", cls="arch-btn",
                           hx_get="/arch/fraud", hx_target="#arch-modal", hx_swap="innerHTML"),
                    Div(id="arch-modal"),
                    Div(cls="stats")(
                        Div(cls="stat-card", id="fraud-mode")(
                            Div(cls="label")("Scoring backend"),
                            Div(cls="value", style="font-size: 1rem;")(scoring_mode())
                        ),
                    )
                ),

                # Main demo grid
                Div(cls="demo-grid")(
                    # Left panel: Input & scenarios
                    Div(cls="panel")(
                        H2("Transaction Scoring"),

                        # Demo scenarios
                        Div(cls="scenarios")(
                            Button(cls="scenario-btn", hx_get="/api/fraud/scenario/legitimate",
                                   hx_target="#txn-form", hx_swap="innerHTML")(
                                Div(cls="title")("💳 Legitimate Purchase"),
                                Div(cls="desc")("$150 coffee maker from regular merchant")
                            ),
                            Button(cls="scenario-btn", hx_get="/api/fraud/scenario/suspicious",
                                   hx_target="#txn-form", hx_swap="innerHTML")(
                                Div(cls="title")("⚠️ Suspicious Activity"),
                                Div(cls="desc")("$8,500 electronics from new merchant, foreign IP")
                            ),
                            Button(cls="scenario-btn", hx_get="/api/fraud/scenario/high_risk",
                                   hx_target="#txn-form", hx_swap="innerHTML")(
                                Div(cls="title")("🚨 High Risk Transaction"),
                                Div(cls="desc")("$25,000 crypto exchange, VPN, new device")
                            ),
                            Button(cls="scenario-btn", hx_get="/api/fraud/scenario/late_arrival",
                                   hx_target="#txn-form", hx_swap="innerHTML")(
                                Div(cls="title")("⏰ Late Arrival Detection"),
                                Div(cls="desc")("Transaction reported 72h after occurrence")
                            )
                        ),

                        # Transaction form
                        Div(id="txn-form")(
                            Form()(
                                Div(cls="form-group")(
                                    Label("Payer Account"),
                                    Input(name="payer", placeholder="acct:user_12345", value="acct:demo_user_001")
                                ),
                                Div(cls="form-group")(
                                    Label("Amount (USD)"),
                                    Input(name="amount", type="number", step="0.01", placeholder="1500.00", value="1500.00")
                                ),
                                Div(cls="form-group")(
                                    Label("Device"),
                                    Input(name="device", placeholder="dev:laptop_chrome", value="dev:laptop_chrome")
                                ),
                                Div(cls="form-group")(
                                    Label("Merchant"),
                                    Input(name="merchant", placeholder="merch:electronics_store", value="merch:amazon")
                                ),
                                Div(cls="form-group")(
                                    Label("IP Address"),
                                    Input(name="ip_address", placeholder="192.168.1.100", value="192.168.1.100")
                                ),
                                Button(cls="btn-primary", type="button",
                                       hx_post="/api/fraud/score",
                                       hx_include="closest form",
                                       hx_target="#results",
                                       hx_swap="innerHTML")("Score Transaction")
                            )
                        )
                    ),

                    # Right panel: Results & visualization
                    Div(cls="panel")(
                        H2("Results"),
                        Div(id="results")(
                            P(style="color: #718096; text-align: center; padding: 3rem 0;")(
                                "Select a scenario or enter transaction details to see fraud scoring results"
                            )
                        ),
                        Div(id="viz")
                    )
                )
            )
        )
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8200, log_level="info")
