"""FastHTML demo server application"""
from fasthtml.common import *
import os

# Import routes
from .routes.fraud import register_fraud_routes

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


# Homepage
@app.get("/")
def homepage():
    """Demo homepage"""
    return Html(
        Head(
            Title("IRIS Interactive Demo"),
        ),
        Body(
            H1("IRIS Capabilities Demo"),
            P("Interactive demonstration server showcasing IRIS for Financial Services and Biomedical Research."),
            Div(
                H2("Financial Services - Fraud Detection"),
                P("Real-time fraud scoring with 130M transactions, bitemporal audit trails."),
                A("View fraud demo", href="/fraud"),
            ),
            Div(
                H2("Biomedical Research - Protein Networks"),
                P("Vector similarity search, pathway queries, network visualization."),
                A("View biomedical demo", href="/bio"),
            ),
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
                    P("Real-time fraud scoring powered by 130M transaction graph with bitemporal audit trails"),
                    Div(cls="stats")(
                        Div(cls="stat-card")(
                            Div(cls="label")("Total Transactions"),
                            Div(cls="value")("130M")
                        ),
                        Div(cls="stat-card")(
                            Div(cls="label")("Avg Response Time"),
                            Div(cls="value")("< 100ms")
                        ),
                        Div(cls="stat-card")(
                            Div(cls="label")("Detection Accuracy"),
                            Div(cls="value")("94.2%")
                        ),
                        Div(cls="stat-card")(
                            Div(cls="label")("False Positive Rate"),
                            Div(cls="value")("0.8%")
                        )
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
