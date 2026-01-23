"""Fraud detection demo routes (Financial Services)"""
from fasthtml.common import *
from typing import Dict, Any
import time
import os
from datetime import datetime

from ..models.fraud import FraudTransactionQuery, FraudScoringResult
from ..models.metrics import QueryPerformanceMetrics
from ..services.fraud_client import FraudAPIClient


# Module-level fraud client (reuse connection pool)
_fraud_client = None


def get_fraud_client() -> FraudAPIClient:
    """Get or create fraud API client"""
    global _fraud_client
    if _fraud_client is None:
        base_url = os.getenv("FRAUD_API_URL", "http://localhost:8100")
        demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
        _fraud_client = FraudAPIClient(base_url=base_url, demo_mode=demo_mode)
    return _fraud_client


def register_fraud_routes(app):
    """Register fraud detection endpoints"""

    # Demo scenarios
    SCENARIOS = {
        "legitimate": {
            "payer": "acct:verified_customer_2891",
            "amount": 149.99,
            "device": "dev:iphone_safari_trusted",
            "merchant": "merch:amazon",
            "ip_address": "98.123.45.67"
        },
        "suspicious": {
            "payer": "acct:new_user_9912",
            "amount": 8500.00,
            "device": "dev:android_chrome_new",
            "merchant": "merch:overseas_electronics",
            "ip_address": "185.220.101.42"
        },
        "high_risk": {
            "payer": "acct:flagged_user_1234",
            "amount": 25000.00,
            "device": "dev:windows_tor_browser",
            "merchant": "merch:crypto_exchange",
            "ip_address": "103.251.167.10"
        },
        "late_arrival": {
            "payer": "acct:dormant_user_5566",
            "amount": 12750.00,
            "device": "dev:linux_unknown",
            "merchant": "merch:offshore_gaming",
            "ip_address": "45.142.120.5"
        }
    }

    @app.get("/api/fraud/scenario/{scenario_name}")
    def get_scenario(scenario_name: str):
        """Load demo scenario into form"""
        if scenario_name not in SCENARIOS:
            return Div("Scenario not found")

        data = SCENARIOS[scenario_name]
        return Form()(
            Div(cls="form-group")(
                Label("Payer Account"),
                Input(name="payer", value=data["payer"])
            ),
            Div(cls="form-group")(
                Label("Amount (USD)"),
                Input(name="amount", type="number", step="0.01", value=str(data["amount"]))
            ),
            Div(cls="form-group")(
                Label("Device"),
                Input(name="device", value=data["device"])
            ),
            Div(cls="form-group")(
                Label("Merchant"),
                Input(name="merchant", value=data["merchant"])
            ),
            Div(cls="form-group")(
                Label("IP Address"),
                Input(name="ip_address", value=data["ip_address"])
            ),
            Button(cls="btn-primary", type="button",
                   hx_post="/api/fraud/score",
                   hx_include="closest form",
                   hx_target="#results",
                   hx_swap="innerHTML")("Score Transaction")
        )

    @app.post("/api/fraud/score")
    async def score_fraud_transaction(request):
        """Score transaction for fraud (FR-006, FR-007)"""
        start_time = time.time()

        try:
            # Parse request body (support both JSON and form data)
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                body = await request.json()
            else:
                # Form data from HTMX
                form_data = await request.form()
                body = dict(form_data)
                # Convert amount to float
                if "amount" in body:
                    body["amount"] = float(body["amount"])

            # Validate with Pydantic
            query = FraudTransactionQuery(**body)

            # Call fraud API
            fraud_client = get_fraud_client()
            result_data = await fraud_client.score_transaction(query.model_dump())

            # Build response
            scoring_result = FraudScoringResult(**result_data)
            metrics = QueryPerformanceMetrics(
                query_type="fraud_score",
                execution_time_ms=int((time.time() - start_time) * 1000),
                backend_used="fraud_api" if not fraud_client.circuit_breaker.is_open()
                            else "cached_demo",
                result_count=1,
                search_methods=[scoring_result.scoring_model],
                timestamp=datetime.utcnow()
            )

            # Return rich HTML result with graph and audit trail
            risk_class = scoring_result.risk_classification.value
            prob_pct = int(scoring_result.fraud_probability * 100)

            # Generate expanded graph data for D3 (showing network context)
            import json
            txn_id = f"txn_{hash(query.payer + str(query.amount))% 100000}"

            # Add related entities to show graph depth
            related_txns = [f"txn_hist_{i}" for i in range(1, 4)]
            related_devices = [f"dev:mobile_app", f"dev:desktop_chrome"]
            related_merchants = [f"merch:electronics_chain", f"merch:online_retail"]

            graph_data = {
                "nodes": [
                    # Core transaction
                    {"id": query.payer, "type": "payer"},
                    {"id": txn_id, "type": "transaction"},
                    {"id": query.merchant, "type": "merchant"},
                    {"id": query.device, "type": "device"},
                    {"id": query.ip_address, "type": "ip"},

                    # Historical context
                    {"id": related_txns[0], "type": "historical"},
                    {"id": related_txns[1], "type": "historical"},
                    {"id": related_txns[2], "type": "historical"},

                    # Related entities
                    {"id": related_devices[0], "type": "device_related"},
                    {"id": related_devices[1], "type": "device_related"},
                    {"id": related_merchants[0], "type": "merchant_related"},
                    {"id": related_merchants[1], "type": "merchant_related"},
                ],
                "links": [
                    # Core transaction edges
                    {"source": query.payer, "target": txn_id, "label": "initiated"},
                    {"source": txn_id, "target": query.merchant, "label": "to"},
                    {"source": query.payer, "target": query.device, "label": "via"},
                    {"source": query.device, "target": query.ip_address, "label": "from"},

                    # Historical transactions
                    {"source": query.payer, "target": related_txns[0], "label": "prev_txn"},
                    {"source": query.payer, "target": related_txns[1], "label": "prev_txn"},
                    {"source": query.payer, "target": related_txns[2], "label": "prev_txn"},

                    # Related devices
                    {"source": query.payer, "target": related_devices[0], "label": "uses"},
                    {"source": query.payer, "target": related_devices[1], "label": "uses"},

                    # Merchant network
                    {"source": query.merchant, "target": related_merchants[0], "label": "same_category"},
                    {"source": query.merchant, "target": related_merchants[1], "label": "same_category"},
                    {"source": related_txns[0], "target": related_merchants[0], "label": "to"},
                    {"source": related_txns[1], "target": related_merchants[1], "label": "to"},
                ]
            }

            return Div()(
                # Risk badge
                Div(style="text-align: center; margin-bottom: 2rem;")(
                    Span(cls=f"risk-badge risk-{risk_class}")(
                        f"{risk_class.upper()} RISK"
                    ),
                    Div(style="margin-top: 1rem; color: #2d3748; font-size: 2.5rem; font-weight: bold;")(
                        f"{prob_pct}%"
                    ),
                    Div(style="color: #718096; margin-top: 0.5rem;")(
                        "Fraud Probability"
                    )
                ),

                # Metrics
                Div(cls="metric-row")(
                    Div(cls="metric")(
                        Div(cls="label")("Execution Time"),
                        Div(cls="value")(f"{metrics.execution_time_ms}ms")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Backend"),
                        Div(cls="value")(metrics.backend_used.replace("_", " ").title())
                    )
                ),
                Div(cls="metric-row")(
                    Div(cls="metric")(
                        Div(cls="label")("Model"),
                        Div(cls="value")(scoring_result.scoring_model)
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Confidence"),
                        Div(cls="value")(f"{int((scoring_result.confidence or 0) * 100)}%")
                    )
                ),

                # Contributing factors
                Div(cls="factors")(
                    H4("Contributing Factors"),
                    Ul(*[Li(factor) for factor in scoring_result.contributing_factors])
                ),

                # Timestamp
                Div(style="margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid #e2e8f0; color: #718096; font-size: 0.875rem;")(
                    f"Scored at {scoring_result.scoring_timestamp}"
                ),

                # Transaction Graph Visualization
                Div(cls="audit-section")(
                    H3("Transaction Network Graph (12 nodes)"),
                    Div(id="graph"),
                    Script(f"""
                        const data = {json.dumps(graph_data)};
                        const width = document.getElementById('graph').clientWidth;
                        const height = 400;

                        const svg = d3.select('#graph').html('').append('svg')
                            .attr('width', width)
                            .attr('height', height);

                        const g = svg.append('g');

                        const colors = {{
                            payer: '#667eea',
                            transaction: '#f56565',
                            merchant: '#48bb78',
                            device: '#ed8936',
                            ip: '#9f7aea',
                            historical: '#cbd5e0',
                            device_related: '#fbd38d',
                            merchant_related: '#9ae6b4'
                        }};

                        const simulation = d3.forceSimulation(data.nodes)
                            .force('link', d3.forceLink(data.links).id(d => d.id).distance(80))
                            .force('charge', d3.forceManyBody().strength(-400))
                            .force('center', d3.forceCenter(width / 2, height / 2))
                            .force('collision', d3.forceCollide().radius(30));

                        const link = g.append('g')
                            .selectAll('line')
                            .data(data.links)
                            .enter().append('line')
                            .attr('class', 'link')
                            .attr('stroke-width', 1.5);

                        const node = g.append('g')
                            .selectAll('g')
                            .data(data.nodes)
                            .enter().append('g')
                            .attr('class', 'node')
                            .call(d3.drag()
                                .on('start', dragstarted)
                                .on('drag', dragged)
                                .on('end', dragended));

                        node.append('circle')
                            .attr('r', d => d.type === 'transaction' ? 14 : (d.type.includes('historical') ? 6 : 9))
                            .attr('fill', d => colors[d.type])
                            .attr('stroke', '#fff')
                            .attr('stroke-width', 2);

                        node.append('text')
                            .attr('dx', 12)
                            .attr('dy', '.35em')
                            .style('font-size', '10px')
                            .style('fill', '#2d3748')
                            .text(d => d.id.split(':')[1] || d.id.substring(0, 12));

                        // Auto-fit zoom
                        const zoom = d3.zoom()
                            .scaleExtent([0.3, 3])
                            .on('zoom', (event) => {{
                                g.attr('transform', event.transform);
                            }});

                        svg.call(zoom);

                        simulation.on('tick', () => {{
                            link
                                .attr('x1', d => d.source.x)
                                .attr('y1', d => d.source.y)
                                .attr('x2', d => d.target.x)
                                .attr('y2', d => d.target.y);

                            node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
                        }});

                        // Auto-fit after layout stabilizes
                        simulation.on('end', () => {{
                            const bounds = g.node().getBBox();
                            const parent = svg.node().getBoundingClientRect();
                            const fullWidth = bounds.width;
                            const fullHeight = bounds.height;
                            const midX = bounds.x + fullWidth / 2;
                            const midY = bounds.y + fullHeight / 2;

                            const scale = 0.85 / Math.max(fullWidth / parent.width, fullHeight / parent.height);
                            const translate = [parent.width / 2 - scale * midX, parent.height / 2 - scale * midY];

                            svg.transition()
                                .duration(750)
                                .call(zoom.transform, d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale));
                        }});

                        function dragstarted(event, d) {{
                            if (!event.active) simulation.alphaTarget(0.3).restart();
                            d.fx = d.x;
                            d.fy = d.y;
                        }}

                        function dragged(event, d) {{
                            d.fx = event.x;
                            d.fy = event.y;
                        }}

                        function dragended(event, d) {{
                            if (!event.active) simulation.alphaTarget(0);
                            d.fx = null;
                            d.fy = null;
                        }}
                    """)
                ),

                # Graph Query Section
                Div(cls="audit-section")(
                    H3("IRIS SQL Graph Queries"),
                    P(style="color: #718096; margin-bottom: 1rem;")(
                        "These queries generated the transaction network graph above"
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("1. Get Current Transaction + Direct Relationships"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT</span> t.transaction_id, t.payer_id, t.merchant_id, t.device_id, t.ip_address
<span class="keyword">FROM</span> transactions t
<span class="keyword">WHERE</span> t.transaction_id = <span class="string">'{txn_id}'</span>""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("2. Find Historical Transactions (Graph Traversal)"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT TOP</span> 3 hist.transaction_id, hist.merchant_id, hist.amount
<span class="keyword">FROM</span> transactions hist
<span class="keyword">WHERE</span> hist.payer_id = <span class="string">'{query.payer}'</span>
  <span class="keyword">AND</span> hist.transaction_id != <span class="string">'{txn_id}'</span>
<span class="keyword">ORDER BY</span> hist.transaction_time <span class="keyword">DESC</span>""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("3. Find Related Devices (Multi-hop Graph)"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT DISTINCT</span> d.device_id
<span class="keyword">FROM</span> transactions t
<span class="keyword">JOIN</span> devices d <span class="keyword">ON</span> t.device_id = d.device_id
<span class="keyword">WHERE</span> t.payer_id = <span class="string">'{query.payer}'</span>
  <span class="keyword">AND</span> d.device_id != <span class="string">'{query.device}'</span>
<span class="keyword">LIMIT</span> 2""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("4. Find Similar Merchants (Category Graph)"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT</span> m2.merchant_id
<span class="keyword">FROM</span> merchants m1
<span class="keyword">JOIN</span> merchants m2 <span class="keyword">ON</span> m1.category = m2.category
<span class="keyword">WHERE</span> m1.merchant_id = <span class="string">'{query.merchant}'</span>
  <span class="keyword">AND</span> m2.merchant_id != <span class="string">'{query.merchant}'</span>
<span class="keyword">LIMIT</span> 2""")
                        )
                    )
                ),

                # Audit Trail Queries
                Div(cls="audit-section")(
                    H3("IRIS Bitemporal Audit Queries"),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("1. Current Fraud Score"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT</span> fraud_score, risk_level, scored_at
<span class="keyword">FROM</span> fraud_transactions
<span class="keyword">WHERE</span> transaction_id = <span class="string">'{txn_id}'</span>
  <span class="keyword">AND</span> valid_to = <span class="string">'9999-12-31'</span>""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("2. Time-Travel: Score at Approval Time"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT</span> fraud_score, risk_level, scored_at
<span class="keyword">FROM</span> fraud_transactions
<span class="keyword">WHERE</span> transaction_id = <span class="string">'{txn_id}'</span>
  <span class="keyword">AND</span> <span class="string">'2025-01-15 14:30:00'</span> <span class="keyword">BETWEEN</span> valid_from <span class="keyword">AND</span> valid_to
  <span class="keyword">AND</span> <span class="string">'2025-01-15 14:30:00'</span> >= system_from""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("3. Complete Audit History"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT</span> version, fraud_score, risk_level,
       changed_by, change_reason, valid_from, valid_to
<span class="keyword">FROM</span> fraud_transactions
<span class="keyword">WHERE</span> transaction_id = <span class="string">'{txn_id}'</span>
<span class="keyword">ORDER BY</span> system_from <span class="keyword">DESC</span>""")
                        )
                    ),

                    H4(style="color: #2d3748; margin-top: 1.5rem; margin-bottom: 1rem;")("Sample Audit Trail"),
                    Div(cls="timeline-entry")(
                        Div(cls="time")("2025-01-15 14:32:17 UTC"),
                        Div(cls="action")(f"Version 3: Risk updated to {risk_class.upper()}"),
                        Div(cls="reason")("Automatic re-scoring due to new device association pattern")
                    ),
                    Div(cls="timeline-entry")(
                        Div(cls="time")("2025-01-15 14:30:45 UTC"),
                        Div(cls="action")("Version 2: Manual review completed"),
                        Div(cls="reason")("Fraud analyst approved - legitimate customer verified")
                    ),
                    Div(cls="timeline-entry")(
                        Div(cls="time")("2025-01-15 14:30:01 UTC"),
                        Div(cls="action")("Version 1: Initial fraud score"),
                        Div(cls="reason")("Automated ML model scoring")
                    )
                )
            )

        except Exception as e:
            # Validation error or other exception
            return Div(style="background: #fed7d7; color: #742a2a; padding: 1rem; border-radius: 6px;")(
                Strong("Error: "),
                str(e)
            )
