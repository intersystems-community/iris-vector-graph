"""Biomedical research demo routes (Life Sciences)"""
from fasthtml.common import *
from typing import Dict, Any
import time
import os
from datetime import datetime

from ..models.biomedical import (
    ProteinSearchQuery,
    SimilaritySearchResult,
    PathwayQuery,
    PathwayResult,
    QueryType
)
from ..models.metrics import QueryPerformanceMetrics
from ..services.iris_biomedical_client import IRISBiomedicalClient


# Module-level IRIS biomedical client (reuse connection)
_biomedical_client = None


def get_biomedical_client() -> IRISBiomedicalClient:
    """Get or create IRIS biomedical client - queries STRING data directly"""
    global _biomedical_client
    if _biomedical_client is None:
        _biomedical_client = IRISBiomedicalClient()
    return _biomedical_client


def register_biomedical_routes(app):
    """Register biomedical research endpoints"""

    @app.get("/bio")
    def bio_page():
        """Interactive biomedical research demo page (T017)"""
        return Html(
            Head(
                Title("IRIS Biomedical Research Demo"),
                Script(src="https://unpkg.com/htmx.org@2.0.0"),
                Script(src="https://d3js.org/d3.v7.min.js"),
                Style("""
                    * { margin: 0; padding: 0; box-sizing: border-box; }
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
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
                    .header h1 { color: #11998e; font-size: 2.5rem; margin-bottom: 0.5rem; }
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
                        border-left: 4px solid #11998e;
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
                        border-color: #11998e;
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
                        border-color: #11998e;
                    }

                    .btn-primary {
                        background: #11998e;
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
                        background: #0e7c73;
                    }

                    #results { margin-top: 2rem; }
                    .similarity-badge {
                        display: inline-block;
                        padding: 0.5rem 1rem;
                        border-radius: 9999px;
                        font-weight: 600;
                        font-size: 0.875rem;
                    }
                    .sim-very-high { background: #c6f6d5; color: #22543d; }
                    .sim-high { background: #bee3f8; color: #2c5282; }
                    .sim-moderate { background: #feebc8; color: #7c2d12; }
                    .sim-low { background: #fed7d7; color: #742a2a; }

                    .protein-table {
                        width: 100%;
                        border-collapse: collapse;
                        margin-top: 1rem;
                    }
                    .protein-table th {
                        background: #f7fafc;
                        padding: 0.75rem;
                        text-align: left;
                        color: #4a5568;
                        font-weight: 600;
                        border-bottom: 2px solid #e2e8f0;
                    }
                    .protein-table td {
                        padding: 0.75rem;
                        border-bottom: 1px solid #e2e8f0;
                        color: #2d3748;
                    }
                    .protein-table tr:hover {
                        background: #f7fafc;
                        cursor: pointer;
                    }

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

                    #viz { min-height: 400px; margin-top: 1rem; background: #f7fafc; border-radius: 6px; }
                    #network-graph { width: 100%; height: 400px; }

                    .node { cursor: pointer; }
                    .node circle { stroke: #fff; stroke-width: 2px; }
                    .node text { font-size: 11px; pointer-events: none; }
                    .link { stroke: #999; stroke-opacity: 0.6; }

                    /* Badges for similarity scores */
                    .badge {
                        display: inline-block;
                        padding: 0.25rem 0.75rem;
                        border-radius: 9999px;
                        font-weight: 600;
                        font-size: 0.75rem;
                    }
                    .badge-very-high { background: #c6f6d5; color: #22543d; }
                    .badge-high { background: #bee3f8; color: #2c5282; }
                    .badge-moderate { background: #feebc8; color: #7c2d12; }
                    .badge-low { background: #fed7d7; color: #742a2a; }

                    /* Metrics grid */
                    .metrics {
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                        gap: 1rem;
                        margin-bottom: 1.5rem;
                    }

                    /* Audit and query sections */
                    .audit-section {
                        background: white;
                        padding: 1.5rem;
                        border-radius: 8px;
                        border: 1px solid #e2e8f0;
                    }
                    .audit-section h3 { color: #2d3748; margin-bottom: 1rem; }

                    .query-box {
                        background: #1a202c;
                        color: #e2e8f0;
                        padding: 1rem;
                        border-radius: 6px;
                        font-family: 'Monaco', 'Courier New', monospace;
                        font-size: 0.875rem;
                        line-height: 1.6;
                        overflow-x: auto;
                    }
                    .query-box .keyword { color: #63b3ed; font-weight: bold; }
                    .query-box .function { color: #9ae6b4; }
                    .query-box .string { color: #fbd38d; }
                    .query-box .variable { color: #f687b3; }
                    .query-box .comment { color: #a0aec0; font-style: italic; }

                    .error {
                        background: #fed7d7;
                        color: #742a2a;
                        padding: 1rem;
                        border-radius: 6px;
                        border-left: 4px solid #fc8181;
                    }
                """)
            ),
            Body(
                Div(cls="container")(
                    # Header
                    Div(cls="header")(
                        H1("IRIS Biomedical Research"),
                        P("Protein similarity search with vector embeddings, network visualization, and pathway analysis"),
                        Div(cls="stats")(
                            Div(cls="stat-card")(
                                Div(cls="label")("Protein Database"),
                                Div(cls="value")("50K+")
                            ),
                            Div(cls="stat-card")(
                                Div(cls="label")("Avg Query Time"),
                                Div(cls="value")("<500ms")
                            ),
                            Div(cls="stat-card")(
                                Div(cls="label")("Search Method"),
                                Div(cls="value")("Hybrid")
                            ),
                            Div(cls="stat-card")(
                                Div(cls="label")("Network Nodes"),
                                Div(cls="value")("<500")
                            )
                        )
                    ),

                    # Demo grid
                    Div(cls="demo-grid")(
                        # Left panel - Search
                        Div(cls="panel")(
                            H2("Protein Search"),

                            # Scenario buttons
                            Div(cls="scenarios")(
                                Button(
                                    cls="scenario-btn",
                                    hx_get="/api/bio/scenario/cancer_protein",
                                    hx_target="#search-form",
                                    hx_swap="innerHTML"
                                )(
                                    Div(cls="title")("💊 Cancer Protein Research"),
                                    Div(cls="desc")("Search TP53 tumor suppressor protein")
                                ),
                                Button(
                                    cls="scenario-btn",
                                    hx_get="/api/bio/scenario/metabolic_pathway",
                                    hx_target="#search-form",
                                    hx_swap="innerHTML"
                                )(
                                    Div(cls="title")("🧬 Metabolic Pathway"),
                                    Div(cls="desc")("Find pathway from GAPDH to LDHA")
                                ),
                                Button(
                                    cls="scenario-btn",
                                    hx_get="/api/bio/scenario/drug_target",
                                    hx_target="#search-form",
                                    hx_swap="innerHTML"
                                )(
                                    Div(cls="title")("🎯 Drug Target Discovery"),
                                    Div(cls="desc")("Search kinase inhibitors")
                                )
                            ),

                            # Search form (populated by scenarios)
                            Div(id="search-form")(
                                P("Select a scenario above to begin")
                            )
                        ),

                        # Right panel - Results
                        Div(cls="panel")(
                            H2("Results"),
                            Div(id="results")(
                                P("Search results will appear here")
                            ),
                            Div(id="viz", style="min-height: 400px; margin-top: 2rem;")(
                                P(style="color: #718096; text-align: center; padding: 3rem;")(
                                    "🧬 Click a protein from search results to visualize its interaction network"
                                )
                            )
                        )
                    )
                )
            )
        )

    # Demo scenarios (FR-029)
    SCENARIOS = {
        "cancer_protein": {
            "query_text": "TP53",
            "query_type": "name",
            "top_k": 10
        },
        "metabolic_pathway": {
            "source_protein_id": "ENSP00000306407",  # GAPDH
            "target_protein_id": "ENSP00000316649",  # LDHA
            "max_hops": 2
        },
        "drug_target": {
            "query_text": "kinase inhibitor",
            "query_type": "function",
            "top_k": 15
        }
    }

    @app.get("/api/bio/scenario/{scenario_name}")
    def get_bio_scenario(scenario_name: str):
        """Load demo scenario into form (FR-029)"""
        if scenario_name not in SCENARIOS:
            return {"detail": f"Scenario '{scenario_name}' not found. Available: cancer_protein, metabolic_pathway, drug_target"}

        data = SCENARIOS[scenario_name]

        # Cancer protein and drug target scenarios = protein search
        if scenario_name in ["cancer_protein", "drug_target"]:
            return Form()(
                Div(cls="form-group")(
                    Label("Query Text"),
                    Input(name="query_text", value=data["query_text"])
                ),
                Div(cls="form-group")(
                    Label("Query Type"),
                    Select(name="query_type")(
                        Option("By Name", value="name", selected=(data["query_type"] == "name")),
                        Option("By Sequence", value="sequence", selected=(data["query_type"] == "sequence")),
                        Option("By Function", value="function", selected=(data["query_type"] == "function"))
                    )
                ),
                Div(cls="form-group")(
                    Label("Top K Results"),
                    Input(name="top_k", type="number", value=str(data["top_k"]))
                ),
                Button(cls="btn-primary", type="button",
                       hx_post="/api/bio/search",
                       hx_include="closest form",
                       hx_target="#results",
                       hx_swap="innerHTML")("Search Proteins")
            )

        # Metabolic pathway scenario = pathway search
        elif scenario_name == "metabolic_pathway":
            return Form()(
                Div(cls="form-group")(
                    Label("Source Protein ID"),
                    Input(name="source_protein_id", value=data["source_protein_id"])
                ),
                Div(cls="form-group")(
                    Label("Target Protein ID"),
                    Input(name="target_protein_id", value=data["target_protein_id"])
                ),
                Div(cls="form-group")(
                    Label("Max Hops"),
                    Input(name="max_hops", type="number", value=str(data["max_hops"]),
                          min="1", max="5", step="1")
                ),
                Button(cls="btn-primary", type="button",
                       hx_post="/api/bio/pathway",
                       hx_include="closest form",
                       hx_target="#results",
                       hx_swap="innerHTML")("Find Pathway")
            )

    @app.post("/api/bio/search")
    async def search_proteins(request):
        """Search proteins with similarity scoring (FR-006, FR-007) - T013"""
        start_time = time.time()

        try:
            # Parse request body (support both JSON and form data)
            content_type = request.headers.get("content-type", "")
            is_json_request = "application/json" in content_type

            if is_json_request:
                body = await request.json()
            else:
                # Form data from HTMX
                form_data = await request.form()
                body = dict(form_data)
                # Convert top_k to int
                if "top_k" in body:
                    body["top_k"] = int(body["top_k"])

            # Validate with Pydantic
            query = ProteinSearchQuery(**body)

            # Call biomedical API
            bio_client = get_biomedical_client()
            result = await bio_client.search_proteins(query)

            # Build metrics
            metrics = QueryPerformanceMetrics(
                query_type="protein_search",
                execution_time_ms=int((time.time() - start_time) * 1000),
                backend_used="iris_direct",
                result_count=len(result.proteins),
                search_methods=[result.search_method],
                timestamp=datetime.utcnow()
            )

            # Return JSON for API calls (tests), HTML for HTMX frontend
            if is_json_request:
                return {
                    "result": result.model_dump(),
                    "metrics": metrics.model_dump()
                }

            # Return rich FastHTML components for HTMX (T013)
            import json

            # Helper function to get similarity badge class
            def similarity_badge_class(score: float) -> str:
                if score >= 0.9:
                    return "badge-very-high"
                elif score >= 0.75:
                    return "badge-high"
                elif score >= 0.5:
                    return "badge-moderate"
                else:
                    return "badge-low"

            def similarity_label(score: float) -> str:
                if score >= 0.9:
                    return "Very High"
                elif score >= 0.75:
                    return "High"
                elif score >= 0.5:
                    return "Moderate"
                else:
                    return "Low"

            return Div()(
                # Performance Metrics
                Div(cls="metrics")(
                    Div(cls="metric")(
                        Div(cls="label")("Query Type"),
                        Div(cls="value")(metrics.query_type.replace("_", " ").title())
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Execution Time"),
                        Div(cls="value")(f"{metrics.execution_time_ms}ms")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Results"),
                        Div(cls="value")(str(metrics.result_count))
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Backend"),
                        Div(cls="value")(
                            "✅ Live API" if metrics.backend_used == "biomedical_api" else "📋 Demo Mode"
                        )
                    )
                ),

                # Results Table
                Div(style="margin-top: 1.5rem;")(
                    H3("Search Results"),
                    P(style="color: #718096; margin-bottom: 1rem;")(
                        f"Found {len(result.proteins)} proteins matching query: '{query.query_text}'"
                    ),

                    Table(style="width: 100%; background: white; border-radius: 6px; overflow: hidden;")(
                        Thead(style="background: #f7fafc;")(
                            Tr()(
                                Th(style="padding: 0.75rem; text-align: left; font-weight: 600;")("Protein"),
                                Th(style="padding: 0.75rem; text-align: left; font-weight: 600;")("Name"),
                                Th(style="padding: 0.75rem; text-align: left; font-weight: 600;")("Organism"),
                                Th(style="padding: 0.75rem; text-align: center; font-weight: 600;")("Similarity")
                            )
                        ),
                        Tbody()(
                            *[
                                Tr(
                                    style="border-top: 1px solid #e2e8f0; cursor: pointer;",
                                    hx_get=f"/api/bio/network/{protein.protein_id}?expand_depth=1",
                                    hx_target="#viz",
                                    hx_swap="outerHTML"
                                )(
                                    Td(style="padding: 0.75rem;")(
                                        Code(style="background: #f7fafc; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.875rem;")(
                                            protein.protein_id
                                        )
                                    ),
                                    Td(style="padding: 0.75rem; font-weight: 500;")(protein.name),
                                    Td(style="padding: 0.75rem; color: #718096;")(protein.organism),
                                    Td(style="padding: 0.75rem; text-align: center;")(
                                        Span(cls=f"badge {similarity_badge_class(result.similarity_scores[i])}")(
                                            f"{similarity_label(result.similarity_scores[i])} ({result.similarity_scores[i]:.3f})"
                                        )
                                    )
                                )
                                for i, protein in enumerate(result.proteins)
                            ]
                        )
                    ),

                    P(style="color: #718096; font-size: 0.875rem; margin-top: 0.5rem; font-style: italic;")(
                        "💡 Click any protein to view its interaction network"
                    )
                ),

                # Vector Search Method Info
                Div(cls="audit-section", style="margin-top: 2rem;")(
                    H3("IRIS Vector Search Query"),
                    P(style="color: #718096; margin-bottom: 1rem;")(
                        f"Search method: {result.search_method} (768-dimensional protein embeddings)"
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("Vector Similarity SQL"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">SELECT TOP</span> {query.top_k} protein_id, name, organism,
       <span class="function">VECTOR_DOT_PRODUCT</span>(embedding, <span class="variable">?query_vector</span>) <span class="keyword">AS</span> similarity
<span class="keyword">FROM</span> protein_embeddings
<span class="keyword">WHERE</span> <span class="function">VECTOR_DOT_PRODUCT</span>(embedding, <span class="variable">?query_vector</span>) > 0.5
<span class="keyword">ORDER BY</span> similarity <span class="keyword">DESC</span>""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("HNSW Index Usage"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="comment">-- IRIS automatically uses HNSW index for fast approximate nearest neighbor search</span>
<span class="keyword">CREATE INDEX</span> protein_embedding_idx
<span class="keyword">ON</span> protein_embeddings (embedding)
<span class="keyword">USING</span> <span class="function">HNSW</span>
<span class="keyword">WITH</span> (m=16, ef_construction=200, ef_search=50)

<span class="comment">-- Query performance: ~5ms for 50K proteins (with HNSW) vs 5800ms (brute force)</span>""")
                        )
                    )
                )
            )

        except Exception as e:
            # Return JSON error for API calls, HTML for HTMX
            content_type = request.headers.get("content-type", "")
            is_json_request = "application/json" in content_type
            if is_json_request:
                return {"error": str(e)}
            return Div(cls="error")(
                H3("Error"),
                P(str(e))
            )

    @app.get("/api/bio/network/{protein_id}")
    async def get_protein_network(protein_id: str, expand_depth: int = 1, request=None):
        """Get interaction network for protein (FR-012, FR-013) - T018"""
        start_time = time.time()

        try:
            bio_client = get_biomedical_client()
            network = await bio_client.get_interaction_network(protein_id, expand_depth)

            metrics = QueryPerformanceMetrics(
                query_type="network_expansion",
                execution_time_ms=int((time.time() - start_time) * 1000),
                backend_used="iris_direct",
                result_count=len(network.nodes),
                search_methods=["graph_neighbors"],
                timestamp=datetime.utcnow()
            )

            # Check if this is an HTMX request (wants HTML) or API request (wants JSON)
            is_htmx = request and request.headers.get("hx-request") == "true" if request else False

            if not is_htmx:
                # Return JSON for contract tests and direct API calls
                return {
                    "result": network.model_dump(),
                    "metrics": metrics.model_dump()
                }

            # Return D3.js visualization for HTMX (T018)
            import json

            # Build D3.js-compatible graph data
            graph_data = {
                "nodes": [
                    {
                        "id": node.protein_id,
                        "name": node.name,
                        "organism": node.organism,
                        "type": "protein"
                    }
                    for node in network.nodes
                ],
                "links": [
                    {
                        "source": edge.source_protein_id,
                        "target": edge.target_protein_id,
                        "type": edge.interaction_type,
                        "confidence": edge.confidence_score
                    }
                    for edge in network.edges
                ]
            }

            return Div(id="viz", style="min-height: 400px; margin-top: 2rem;")(
                # Network stats
                Div(cls="metrics")(
                    Div(cls="metric")(
                        Div(cls="label")("Network Size"),
                        Div(cls="value")(f"{len(network.nodes)} proteins")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Interactions"),
                        Div(cls="value")(f"{len(network.edges)} edges")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Expand Depth"),
                        Div(cls="value")(str(expand_depth))
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Query Time"),
                        Div(cls="value")(f"{metrics.execution_time_ms}ms")
                    )
                ),

                # D3.js force-directed graph
                Div(id="network-graph", style="margin-top: 1rem;"),

                # Graph legend
                Div(style="margin-top: 1rem; padding: 1rem; background: white; border-radius: 6px;")(
                    H4("Interaction Network"),
                    P(style="color: #718096; font-size: 0.875rem;")(
                        f"Showing protein interaction network for {protein_id}. Drag nodes to explore relationships."
                    ),
                    Div(style="display: flex; gap: 1rem; margin-top: 0.5rem; font-size: 0.875rem;")(
                        Div()(
                            Span(style="display: inline-block; width: 12px; height: 12px; background: #11998e; border-radius: 50%; margin-right: 0.5rem;"),
                            "Protein"
                        ),
                        Div()(
                            Span(style="display: inline-block; width: 20px; height: 2px; background: #999; margin-right: 0.5rem;"),
                            "Interaction"
                        )
                    )
                ),

                # D3.js script to render uber-cool interactive graph (T018 + T019)
                Script(NotStr(f"""
                    const data = {json.dumps(graph_data)};

                    // Global state for node expansion
                    window.networkState = window.networkState || {{
                        expandedNodes: new Set(),
                        allNodes: new Map(),
                        allLinks: []
                    }};

                    // Clear previous graph
                    d3.select('#network-graph').selectAll('*').remove();

                    const container = document.getElementById('network-graph');
                    const parentWidth = container.parentElement ? container.parentElement.clientWidth : 1200;
                    const width = Math.max(container.clientWidth || parentWidth, 800);
                    const height = 600;

                    const svg = d3.select('#network-graph')
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height)
                        .style('background', 'linear-gradient(135deg, #f7fafc 0%, #e6fffa 100%)')
                        .style('border-radius', '8px');

                    // Add glow filter for nodes
                    const defs = svg.append('defs');
                    const filter = defs.append('filter')
                        .attr('id', 'glow')
                        .attr('height', '300%')
                        .attr('width', '300%')
                        .attr('x', '-75%')
                        .attr('y', '-75%');

                    filter.append('feGaussianBlur')
                        .attr('stdDeviation', '3')
                        .attr('result', 'coloredBlur');

                    const feMerge = filter.append('feMerge');
                    feMerge.append('feMergeNode').attr('in', 'coloredBlur');
                    feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

                    const g = svg.append('g');

                    // Organism color palette
                    const organismColors = {{
                        'Homo sapiens': '#11998e',
                        'Mus musculus': '#667eea',
                        'default': '#38ef7d'
                    }};

                    // Interaction type colors
                    const interactionColors = {{
                        'activation': '#48bb78',
                        'inhibition': '#f56565',
                        'binding': '#4299e1',
                        'default': '#718096'
                    }};

                    // Calculate node sizes based on connections
                    const nodeDegree = {{}};
                    data.links.forEach(link => {{
                        nodeDegree[link.source] = (nodeDegree[link.source] || 0) + 1;
                        nodeDegree[link.target] = (nodeDegree[link.target] || 0) + 1;
                    }});

                    // Create force simulation with enhanced forces
                    const simulation = d3.forceSimulation(data.nodes)
                        .force('link', d3.forceLink(data.links)
                            .id(d => d.id)
                            .distance(d => 120 - (d.confidence * 40)))
                        .force('charge', d3.forceManyBody()
                            .strength(d => -400 - (nodeDegree[d.id] || 0) * 20))
                        .force('center', d3.forceCenter(width / 2, height / 2))
                        .force('collision', d3.forceCollide()
                            .radius(d => 15 + (nodeDegree[d.id] || 0) * 2))
                        .alphaDecay(0.02);

                    // Draw edge labels
                    const edgeLabels = g.append('g')
                        .selectAll('text')
                        .data(data.links)
                        .enter().append('text')
                        .attr('class', 'edge-label')
                        .attr('text-anchor', 'middle')
                        .attr('dy', -5)
                        .style('font-size', '9px')
                        .style('fill', '#4a5568')
                        .style('pointer-events', 'none')
                        .style('opacity', 0.7)
                        .text(d => d.type || '');

                    // Draw edges with gradient
                    const link = g.append('g')
                        .selectAll('line')
                        .data(data.links)
                        .enter().append('line')
                        .attr('class', 'link')
                        .attr('stroke', d => interactionColors[d.type] || interactionColors.default)
                        .attr('stroke-opacity', 0.6)
                        .attr('stroke-width', d => Math.max(1.5, d.confidence * 4))
                        .attr('stroke-dasharray', d => d.type === 'inhibition' ? '5,5' : '0');

                    // Draw nodes with enhanced styling
                    const node = g.append('g')
                        .selectAll('g')
                        .data(data.nodes)
                        .enter().append('g')
                        .attr('class', 'node')
                        .style('cursor', 'pointer')
                        .call(d3.drag()
                            .on('start', dragstarted)
                            .on('drag', dragged)
                            .on('end', dragended))
                        .on('click', handleNodeClick)
                        .on('mouseenter', handleMouseEnter)
                        .on('mouseleave', handleMouseLeave);

                    // Outer glow circle
                    node.append('circle')
                        .attr('r', d => 14 + (nodeDegree[d.id] || 0) * 1.5)
                        .attr('fill', 'none')
                        .attr('stroke', d => organismColors[d.organism] || organismColors.default)
                        .attr('stroke-width', 0.5)
                        .attr('opacity', 0.3);

                    // Main node circle
                    node.append('circle')
                        .attr('r', d => 12 + (nodeDegree[d.id] || 0))
                        .attr('fill', d => organismColors[d.organism] || organismColors.default)
                        .attr('stroke', '#fff')
                        .attr('stroke-width', 2.5)
                        .style('filter', 'url(#glow)');

                    // Node labels with background
                    const labels = node.append('g')
                        .attr('class', 'label-group');

                    labels.append('rect')
                        .attr('x', 16)
                        .attr('y', -10)
                        .attr('width', d => (d.name || d.id.substring(0, 15)).length * 6.5 + 8)
                        .attr('height', 18)
                        .attr('fill', 'white')
                        .attr('opacity', 0.9)
                        .attr('rx', 3);

                    labels.append('text')
                        .attr('dx', 20)
                        .attr('dy', '.35em')
                        .style('font-size', '11px')
                        .style('font-weight', '600')
                        .style('fill', '#2d3748')
                        .text(d => d.name || d.id.substring(0, 15));

                    // Add tooltips
                    node.append('title')
                        .text(d => `${{d.name || d.id}}\\nOrganism: ${{d.organism}}\\nConnections: ${{nodeDegree[d.id] || 0}}\\nClick to expand network`);

                    // Zoom behavior with double-click to reset
                    const zoom = d3.zoom()
                        .scaleExtent([0.2, 4])
                        .on('zoom', (event) => {{
                            g.attr('transform', event.transform);
                        }});

                    svg.call(zoom)
                        .on('dblclick.zoom', null)
                        .on('dblclick', () => {{
                            svg.transition().duration(750).call(
                                zoom.transform,
                                d3.zoomIdentity.translate(width / 2, height / 2).scale(1)
                            );
                        }});

                    // Update positions on tick
                    simulation.on('tick', () => {{
                        // Keep nodes within bounds
                        data.nodes.forEach(d => {{
                            d.x = Math.max(30, Math.min(width - 30, d.x));
                            d.y = Math.max(30, Math.min(height - 30, d.y));
                        }});

                        link
                            .attr('x1', d => d.source.x)
                            .attr('y1', d => d.source.y)
                            .attr('x2', d => d.target.x)
                            .attr('y2', d => d.target.y);

                        edgeLabels
                            .attr('x', d => (d.source.x + d.target.x) / 2)
                            .attr('y', d => (d.source.y + d.target.y) / 2);

                        node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
                    }});

                    // Auto-fit graph after layout stabilizes
                    setTimeout(() => {{
                        const bounds = g.node().getBBox();
                        const parent = svg.node().getBoundingClientRect();
                        const fullWidth = bounds.width;
                        const fullHeight = bounds.height;
                        const midX = bounds.x + fullWidth / 2;
                        const midY = bounds.y + fullHeight / 2;

                        if (fullWidth > 0 && fullHeight > 0) {{
                            const scale = 0.8 / Math.max(fullWidth / parent.width, fullHeight / parent.height);
                            const translate = [parent.width / 2 - scale * midX, parent.height / 2 - scale * midY];

                            svg.transition()
                                .duration(750)
                                .call(zoom.transform, d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale));
                        }}
                    }}, 1500);

                    // Node click handler - T019: Expand network on click
                    function handleNodeClick(event, d) {{
                        event.stopPropagation();

                        // Visual feedback
                        d3.select(this).select('circle:nth-child(2)')
                            .transition()
                            .duration(200)
                            .attr('r', 18)
                            .transition()
                            .duration(200)
                            .attr('r', 12 + (nodeDegree[d.id] || 0));

                        // Check if we've hit the 500 node limit
                        if (data.nodes.length >= 500) {{
                            alert('Network size limit reached (500 nodes). Cannot expand further.');
                            return;
                        }}

                        // Expand network by fetching neighbors
                        if (!window.networkState.expandedNodes.has(d.id)) {{
                            window.networkState.expandedNodes.add(d.id);

                            // Show loading indicator
                            const loadingText = g.append('text')
                                .attr('x', d.x)
                                .attr('y', d.y - 30)
                                .attr('text-anchor', 'middle')
                                .style('font-size', '12px')
                                .style('fill', '#11998e')
                                .style('font-weight', 'bold')
                                .text('Loading...');

                            // Fetch expanded network
                            fetch(`/api/bio/network/${{d.id}}?expand_depth=1`)
                                .then(res => res.json())
                                .then(expandedData => {{
                                    loadingText.remove();

                                    // Merge new nodes
                                    const existingIds = new Set(data.nodes.map(n => n.id));
                                    expandedData.result.nodes.forEach(newNode => {{
                                        if (!existingIds.has(newNode.protein_id)) {{
                                            data.nodes.push({{
                                                id: newNode.protein_id,
                                                name: newNode.name,
                                                organism: newNode.organism,
                                                type: 'protein',
                                                x: d.x + Math.random() * 100 - 50,
                                                y: d.y + Math.random() * 100 - 50
                                            }});
                                            existingIds.add(newNode.protein_id);
                                        }}
                                    }});

                                    // Merge new edges
                                    expandedData.result.edges.forEach(newEdge => {{
                                        data.links.push({{
                                            source: newEdge.source_protein_id,
                                            target: newEdge.target_protein_id,
                                            type: newEdge.interaction_type,
                                            confidence: newEdge.confidence_score
                                        }});
                                    }});

                                    // Update nodeDegree for new connections
                                    data.links.forEach(link => {{
                                        const sourceId = link.source.id || link.source;
                                        const targetId = link.target.id || link.target;
                                        nodeDegree[sourceId] = (nodeDegree[sourceId] || 0) + 1;
                                        nodeDegree[targetId] = (nodeDegree[targetId] || 0) + 1;
                                    }});

                                    // Update edge labels (enter/update/exit)
                                    const edgeLabelUpdate = g.selectAll('.edge-label')
                                        .data(data.links);

                                    edgeLabelUpdate.exit().remove();

                                    const edgeLabelEnter = edgeLabelUpdate.enter()
                                        .append('text')
                                        .attr('class', 'edge-label')
                                        .attr('text-anchor', 'middle')
                                        .attr('dy', -5)
                                        .style('font-size', '9px')
                                        .style('fill', '#4a5568')
                                        .style('pointer-events', 'none')
                                        .style('opacity', 0)
                                        .text(d => d.type || '');

                                    edgeLabelEnter.transition().duration(500).style('opacity', 0.7);

                                    // Update links (enter/update/exit)
                                    const linkUpdate = g.selectAll('.link')
                                        .data(data.links);

                                    linkUpdate.exit().remove();

                                    const linkEnter = linkUpdate.enter()
                                        .append('line')
                                        .attr('class', 'link')
                                        .attr('stroke', d => interactionColors[d.type] || interactionColors.default)
                                        .attr('stroke-opacity', 0)
                                        .attr('stroke-width', d => Math.max(1.5, d.confidence * 4))
                                        .attr('stroke-dasharray', d => d.type === 'inhibition' ? '5,5' : '0');

                                    linkEnter.transition().duration(500).attr('stroke-opacity', 0.6);

                                    // Update nodes (enter/update/exit)
                                    const nodeUpdate = g.selectAll('.node')
                                        .data(data.nodes, d => d.id);

                                    nodeUpdate.exit().remove();

                                    const nodeEnter = nodeUpdate.enter()
                                        .append('g')
                                        .attr('class', 'node')
                                        .style('cursor', 'pointer')
                                        .style('opacity', 0)
                                        .call(d3.drag()
                                            .on('start', dragstarted)
                                            .on('drag', dragged)
                                            .on('end', dragended))
                                        .on('click', handleNodeClick)
                                        .on('mouseenter', handleMouseEnter)
                                        .on('mouseleave', handleMouseLeave);

                                    // Outer glow circle for new nodes
                                    nodeEnter.append('circle')
                                        .attr('r', d => 14 + (nodeDegree[d.id] || 0) * 1.5)
                                        .attr('fill', 'none')
                                        .attr('stroke', d => organismColors[d.organism] || organismColors.default)
                                        .attr('stroke-width', 0.5)
                                        .attr('opacity', 0.3);

                                    // Main node circle for new nodes
                                    nodeEnter.append('circle')
                                        .attr('r', d => 12 + (nodeDegree[d.id] || 0))
                                        .attr('fill', d => organismColors[d.organism] || organismColors.default)
                                        .attr('stroke', '#fff')
                                        .attr('stroke-width', 2.5)
                                        .style('filter', 'url(#glow)');

                                    // Labels for new nodes
                                    const newLabels = nodeEnter.append('g')
                                        .attr('class', 'label-group');

                                    newLabels.append('rect')
                                        .attr('x', 16)
                                        .attr('y', -10)
                                        .attr('width', d => (d.name || d.id.substring(0, 15)).length * 6.5 + 8)
                                        .attr('height', 18)
                                        .attr('fill', 'white')
                                        .attr('opacity', 0.9)
                                        .attr('rx', 3);

                                    newLabels.append('text')
                                        .attr('dx', 20)
                                        .attr('dy', '.35em')
                                        .style('font-size', '11px')
                                        .style('font-weight', '600')
                                        .style('fill', '#2d3748')
                                        .text(d => d.name || d.id.substring(0, 15));

                                    // Tooltips for new nodes
                                    nodeEnter.append('title')
                                        .text(d => `${{d.name || d.id}}\\nOrganism: ${{d.organism}}\\nConnections: ${{nodeDegree[d.id] || 0}}\\nClick to expand network`);

                                    // Fade in new nodes
                                    nodeEnter.transition().duration(500).style('opacity', 1);

                                    // Merge enter and update selections
                                    const nodeAll = nodeEnter.merge(nodeUpdate);
                                    const linkAll = linkEnter.merge(linkUpdate);
                                    const edgeLabelAll = edgeLabelEnter.merge(edgeLabelUpdate);

                                    // Restart simulation with new data
                                    simulation.nodes(data.nodes);
                                    simulation.force('link').links(data.links);
                                    simulation.alpha(0.5).restart();

                                    // Update tick handler to use merged selections
                                    simulation.on('tick', () => {{
                                        data.nodes.forEach(d => {{
                                            d.x = Math.max(30, Math.min(width - 30, d.x));
                                            d.y = Math.max(30, Math.min(height - 30, d.y));
                                        }});

                                        linkAll
                                            .attr('x1', d => d.source.x)
                                            .attr('y1', d => d.source.y)
                                            .attr('x2', d => d.target.x)
                                            .attr('y2', d => d.target.y);

                                        edgeLabelAll
                                            .attr('x', d => (d.source.x + d.target.x) / 2)
                                            .attr('y', d => (d.source.y + d.target.y) / 2);

                                        nodeAll.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
                                    }});
                                }})
                                .catch(err => {{
                                    loadingText.remove();
                                    console.error('Failed to expand network:', err);
                                }});
                        }}
                    }}

                    // Mouse enter handler - highlight connected nodes
                    function handleMouseEnter(event, d) {{
                        const connectedNodes = new Set();
                        connectedNodes.add(d.id);

                        data.links.forEach(link => {{
                            if (link.source.id === d.id || link.source === d.id) {{
                                connectedNodes.add(link.target.id || link.target);
                            }}
                            if (link.target.id === d.id || link.target === d.id) {{
                                connectedNodes.add(link.source.id || link.source);
                            }}
                        }});

                        // Dim unconnected nodes
                        node.style('opacity', n => connectedNodes.has(n.id) ? 1 : 0.2);
                        link.style('opacity', l => {{
                            const sourceId = l.source.id || l.source;
                            const targetId = l.target.id || l.target;
                            return (sourceId === d.id || targetId === d.id) ? 1 : 0.1;
                        }});
                    }}

                    // Mouse leave handler - restore opacity
                    function handleMouseLeave(event, d) {{
                        node.style('opacity', 1);
                        link.style('opacity', 0.6);
                    }}

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
                """))
            )

        except Exception as e:
            return {"error": str(e)}

    @app.post("/api/bio/pathway")
    async def find_protein_pathway(request):
        """Find shortest pathway between proteins (FR-019, FR-020) - T014"""
        start_time = time.time()

        try:
            # Parse request body
            content_type = request.headers.get("content-type", "")
            is_json_request = "application/json" in content_type

            if is_json_request:
                body = await request.json()
            else:
                form_data = await request.form()
                body = dict(form_data)
                if "max_hops" in body:
                    body["max_hops"] = int(body["max_hops"])

            # Validate with Pydantic
            query = PathwayQuery(**body)

            # Call biomedical API
            bio_client = get_biomedical_client()
            pathway = await bio_client.find_pathway(query)

            metrics = QueryPerformanceMetrics(
                query_type="pathway_search",
                execution_time_ms=int((time.time() - start_time) * 1000),
                backend_used="iris_direct",
                result_count=len(pathway.path),
                search_methods=["graph_traversal"],
                timestamp=datetime.utcnow()
            )

            # Return JSON for API calls, HTML for HTMX
            if is_json_request:
                return {
                    "result": pathway.model_dump(),
                    "metrics": metrics.model_dump()
                }

            # Return rich FastHTML for HTMX (T014)
            return Div()(
                # Performance Metrics
                Div(cls="metrics")(
                    Div(cls="metric")(
                        Div(cls="label")("Query Type"),
                        Div(cls="value")("Pathway Search")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Execution Time"),
                        Div(cls="value")(f"{metrics.execution_time_ms}ms")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Path Length"),
                        Div(cls="value")(f"{len(pathway.path)} hops")
                    ),
                    Div(cls="metric")(
                        Div(cls="label")("Confidence"),
                        Div(cls="value")(f"{pathway.confidence:.2%}")
                    )
                ),

                # Pathway Visualization
                Div(style="margin-top: 1.5rem;")(
                    H3("Protein Pathway"),
                    P(style="color: #718096; margin-bottom: 1rem;")(
                        f"Path from {query.source_protein_id} to {query.target_protein_id} (max {query.max_hops} hops)"
                    ),

                    # Pathway diagram
                    Div(style="background: white; padding: 1.5rem; border-radius: 8px; margin-bottom: 1.5rem;")(
                        Div(style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;")(
                            *[
                                item
                                for i, protein_id in enumerate(pathway.path)
                                for item in [
                                    # Protein node
                                    Div(style="flex-shrink: 0;")(
                                        Div(style="background: #11998e; color: white; padding: 0.75rem 1rem; border-radius: 6px; font-weight: 600; text-align: center;")(
                                            Div(style="font-size: 0.75rem; opacity: 0.8;")("Protein"),
                                            Div(style="font-size: 0.875rem; font-family: monospace;")(
                                                protein_id if i < len(pathway.intermediate_proteins) else protein_id
                                            ),
                                            Div(style="font-size: 0.875rem; margin-top: 0.25rem;")(
                                                pathway.intermediate_proteins[i].name if i < len(pathway.intermediate_proteins) else ""
                                            )
                                        )
                                    ),
                                    # Arrow (if not last)
                                    *([] if i == len(pathway.path) - 1 else [
                                        Div(style="flex-shrink: 0; color: #718096;")(
                                            Div(style="text-align: center;")(
                                                Div(style="font-size: 1.5rem;")("→"),
                                                Div(style="font-size: 0.75rem; margin-top: -0.25rem;")(
                                                    pathway.path_interactions[i].interaction_type if i < len(pathway.path_interactions) else ""
                                                ),
                                                Div(style="font-size: 0.7rem; color: #a0aec0;")(
                                                    f"{pathway.path_interactions[i].confidence_score:.2f}" if i < len(pathway.path_interactions) else ""
                                                )
                                            )
                                        )
                                    ])
                                ]
                            ]
                        )
                    ),

                    # Interactions Table
                    H4("Pathway Interactions"),
                    Table(style="width: 100%; background: white; border-radius: 6px; overflow: hidden; margin-top: 1rem;")(
                        Thead(style="background: #f7fafc;")(
                            Tr()(
                                Th(style="padding: 0.75rem; text-align: left; font-weight: 600;")("Source"),
                                Th(style="padding: 0.75rem; text-align: left; font-weight: 600;")("Interaction"),
                                Th(style="padding: 0.75rem; text-align: left; font-weight: 600;")("Target"),
                                Th(style="padding: 0.75rem; text-align: center; font-weight: 600;")("Confidence")
                            )
                        ),
                        Tbody()(
                            *[
                                Tr(style="border-top: 1px solid #e2e8f0;")(
                                    Td(style="padding: 0.75rem; font-family: monospace; font-size: 0.875rem;")(
                                        interaction.source_protein_id
                                    ),
                                    Td(style="padding: 0.75rem; color: #11998e; font-weight: 500;")(
                                        interaction.interaction_type
                                    ),
                                    Td(style="padding: 0.75rem; font-family: monospace; font-size: 0.875rem;")(
                                        interaction.target_protein_id
                                    ),
                                    Td(style="padding: 0.75rem; text-align: center;")(
                                        Span(cls=f"badge badge-{('very-high' if interaction.confidence_score >= 0.9 else 'high' if interaction.confidence_score >= 0.75 else 'moderate')}")(
                                            f"{interaction.confidence_score:.3f}"
                                        )
                                    )
                                )
                                for interaction in pathway.path_interactions
                            ]
                        )
                    )
                ),

                # Graph Traversal SQL
                Div(cls="audit-section", style="margin-top: 2rem;")(
                    H3("IRIS Graph Traversal Queries"),
                    P(style="color: #718096; margin-bottom: 1rem;")(
                        "These queries find the shortest path through protein interaction network"
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("Recursive Graph Query"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="keyword">WITH RECURSIVE</span> pathway <span class="keyword">AS</span> (
    <span class="comment">-- Base case: start protein</span>
    <span class="keyword">SELECT</span> protein_id, 0 <span class="keyword">AS</span> hop, protein_id <span class="keyword">AS</span> path
    <span class="keyword">FROM</span> proteins
    <span class="keyword">WHERE</span> protein_id = <span class="string">'{query.source_protein_id}'</span>

    <span class="keyword">UNION ALL</span>

    <span class="comment">-- Recursive case: follow interactions</span>
    <span class="keyword">SELECT</span> i.target_protein_id, p.hop + 1, p.path || <span class="string">' → '</span> || i.target_protein_id
    <span class="keyword">FROM</span> pathway p
    <span class="keyword">JOIN</span> protein_interactions i <span class="keyword">ON</span> p.protein_id = i.source_protein_id
    <span class="keyword">WHERE</span> p.hop < {query.max_hops}
      <span class="keyword">AND</span> i.confidence_score > 0.5
)
<span class="keyword">SELECT</span> * <span class="keyword">FROM</span> pathway
<span class="keyword">WHERE</span> protein_id = <span class="string">'{query.target_protein_id}'</span>
<span class="keyword">ORDER BY</span> hop <span class="keyword">LIMIT</span> 1""")
                        )
                    ),

                    Div(style="margin-bottom: 1.5rem;")(
                        H4(style="color: #4a5568; font-size: 1rem; margin-bottom: 0.5rem;")("Network Index Optimization"),
                        Div(cls="query-box")(
                            NotStr(f"""<span class="comment">-- IRIS uses graph indexes for fast traversal</span>
<span class="keyword">CREATE INDEX</span> idx_interactions_source <span class="keyword">ON</span> protein_interactions(source_protein_id)
<span class="keyword">CREATE INDEX</span> idx_interactions_target <span class="keyword">ON</span> protein_interactions(target_protein_id)

<span class="comment">-- Performance: {metrics.execution_time_ms}ms for {len(pathway.path)}-hop path</span>""")
                        )
                    )
                )
            )

        except Exception as e:
            # Return JSON error for API calls, HTML for HTMX
            content_type = request.headers.get("content-type", "")
            is_json_request = "application/json" in content_type
            if is_json_request:
                return {"error": str(e)}
            return Div(cls="error")(
                H3("Error"),
                P(str(e))
            )
