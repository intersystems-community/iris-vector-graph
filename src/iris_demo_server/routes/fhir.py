"""FHIR repository graph search demo (spec 231).

A FHIR repository projected as one named graph: pick a concept, expand it over the
concept graph, resolve it to the Conditions and labs that carry its codes, and rank
patients and clinicians with personalized PageRank over the FHIR graph. A condition
added through the FHIR service reaches the graph at the next sync.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from fasthtml.common import *

from ..services import fhir_demo_data as data

MAX_HOPS = 3
_PATIENT_KEY = re.compile(r"^Patient/[A-Za-z0-9\-.]{1,64}$")
_RESOURCE_KEY = re.compile(r"^[A-Z][A-Za-z]{1,40}/[A-Za-z0-9\-.]{1,64}$")
_EVIDENCE = {"diagnoses": ["exact", "narrower"], "all": None}

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "diabetes": {
        "title": "Diabetes, every form",
        "concepts": [data.DIABETES],
        "hops": 2,
        "evidence": "diagnoses",
        "blurb": "The broad concept carries no codes; two hops of expansion reach type 2 DM and diabetic nephropathy.",
    },
    "kidney": {
        "title": "Kidney disease + labs",
        "concepts": [data.CKD],
        "hops": 1,
        "evidence": "all",
        "blurb": "Include eGFR results crosswalked as related, not only diagnoses.",
    },
    "cardio": {
        "title": "Cardiovascular",
        "concepts": [data.CARDIOVASCULAR],
        "hops": 1,
        "evidence": "diagnoses",
        "blurb": "Hypertension and heart failure under one grouping concept.",
    },
    "cardiorenal": {
        "title": "Cardiorenal",
        "concepts": [data.CHF, data.CKD],
        "hops": 0,
        "evidence": "diagnoses",
        "blurb": "Two concepts seeded together: patients with both rank highest.",
    },
    "asthma": {
        "title": "Asthma",
        "concepts": [data.ASTHMA],
        "hops": 0,
        "evidence": "diagnoses",
        "blurb": "A small cohort, and the pulmonologist who sees it.",
    },
}

_client = None


def _build_client():
    from ..services.fhir_graph_client import FHIRGraphDemoClient

    return FHIRGraphDemoClient.from_env()


def set_fhir_client(client) -> None:
    """Install a client (tests), or `None` to build one from the environment."""
    global _client
    _client = client


def get_fhir_client():
    global _client
    if _client is None:
        _client = _build_client()
    return _client


class BadInput(ValueError):
    pass


def parse_search(body: Dict[str, Any]) -> Dict[str, Any]:
    concepts = body.get("concepts") or []
    if isinstance(concepts, str):
        concepts = [concepts]
    concepts = list(dict.fromkeys(concepts))
    if not concepts:
        raise BadInput("pick at least one concept")
    unknown = [c for c in concepts if c not in data.CONCEPTS]
    if unknown:
        raise BadInput(f"unknown concept {unknown[0]!r}")
    try:
        hops = int(body.get("hops", 1))
    except (TypeError, ValueError):
        raise BadInput("hops must be a number")
    if not 0 <= hops <= MAX_HOPS:
        raise BadInput(f"hops must be 0..{MAX_HOPS}")
    evidence = body.get("evidence", "diagnoses")
    if evidence not in _EVIDENCE:
        raise BadInput("evidence must be 'diagnoses' or 'all'")
    return {"concepts": concepts, "hops": hops, "relations": _EVIDENCE[evidence], "top_k": 10}


CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: linear-gradient(135deg, #1d4e89 0%, #00b2ca 100%);
    min-height: 100vh; padding: 2rem;
}
.container { max-width: 1400px; margin: 0 auto; }
.header, .panel {
    background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);
}
.header { padding: 2rem; margin-bottom: 2rem; }
.header h1 { color: #1d4e89; font-size: 2.3rem; margin-bottom: 0.5rem; }
.header p { color: #555; font-size: 1.05rem; }
.header code { background: #eef4fb; padding: 0.1rem 0.4rem; border-radius: 4px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-top: 1rem; }
.stat-card { background: #f7fafc; padding: 1rem; border-radius: 8px; border-left: 4px solid #1d4e89; }
.stat-card .label { color: #718096; font-size: 0.8rem; text-transform: uppercase; }
.stat-card .value { color: #2d3748; font-size: 1.6rem; font-weight: bold; margin-top: 0.25rem; }
.demo-grid { display: grid; grid-template-columns: 5fr 7fr; gap: 2rem; margin-bottom: 2rem; }
@media (max-width: 1100px) { .demo-grid { grid-template-columns: 1fr; } }
.panel { padding: 1.5rem; }
.panel h2 { color: #1d4e89; margin-bottom: 1rem; font-size: 1.3rem; }
.panel h3 { color: #2d3748; margin: 1rem 0 0.5rem; font-size: 1rem; }
.scenarios { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1rem; }
button, .btn {
    background: #1d4e89; color: white; border: none; padding: 0.55rem 1rem; border-radius: 6px;
    cursor: pointer; font-size: 0.9rem;
}
button:hover { background: #163c6a; }
button.secondary { background: #e2e8f0; color: #1d4e89; }
button.secondary:hover { background: #cbd5e0; }
.concepts label { display: block; padding: 0.2rem 0; color: #2d3748; }
.concepts .cid { color: #a0aec0; font-size: 0.8rem; font-family: monospace; }
.row { display: flex; gap: 1.5rem; align-items: center; margin: 0.75rem 0; flex-wrap: wrap; }
select, input[type=text] { padding: 0.4rem; border: 1px solid #cbd5e0; border-radius: 6px; }
.blurb { color: #4a5568; font-size: 0.9rem; margin: 0.5rem 0; font-style: italic; }
.pipeline { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
.stage { flex: 1; min-width: 120px; background: #eef4fb; border-radius: 8px; padding: 0.6rem 0.8rem; }
.stage .name { font-size: 0.75rem; color: #718096; text-transform: uppercase; }
.stage .ms { font-size: 1.2rem; font-weight: bold; color: #1d4e89; }
.stage .what { font-size: 0.8rem; color: #4a5568; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th { text-align: left; color: #718096; font-weight: 600; font-size: 0.75rem; text-transform: uppercase; padding: 0.4rem; }
td { padding: 0.45rem 0.4rem; border-top: 1px solid #edf2f7; vertical-align: top; }
tr.clickable:hover { background: #f7fafc; cursor: pointer; }
.key { font-family: monospace; font-size: 0.78rem; color: #718096; }
.chip { display: inline-block; background: #e6fffa; color: #234e52; border-radius: 12px; padding: 0.1rem 0.5rem;
        margin: 0.1rem; font-size: 0.75rem; }
.chip.lab { background: #fefcbf; color: #744210; }
.score { font-family: monospace; }
.error { background: #fff5f5; color: #c53030; padding: 1rem; border-radius: 8px; border-left: 4px solid #c53030; }
.ok { background: #f0fff4; color: #276749; padding: 0.75rem; border-radius: 8px; border-left: 4px solid #38a169; }
.muted { color: #a0aec0; }
#viz { width: 100%; height: 480px; background: #fafcff; border-radius: 8px; border: 1px solid #e2e8f0; }
.legend span { display: inline-block; margin-right: 1rem; font-size: 0.8rem; color: #4a5568; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 0.3rem; }
.modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); align-items: center; justify-content: center; }
.modal.open { display: flex; }
.modal > div { background: white; padding: 2rem; border-radius: 12px; max-width: 720px; }
.modal li { margin: 0.4rem 0 0.4rem 1.2rem; }
"""

TYPE_COLORS = {
    "Patient": "#1d4e89",
    "Practitioner": "#d53f8c",
    "Condition": "#dd6b20",
    "Observation": "#d69e2e",
    "Encounter": "#38a169",
    "Organization": "#718096",
}

VIZ_JS = """
const TYPE_COLORS = %s;
function showHood(key) {
  fetch('/api/fhir/neighborhood/' + key).then(r => r.json()).then(g => drawHood(key, g));
}
function drawHood(key, g) {
  const el = document.getElementById('viz');
  el.innerHTML = '';
  document.getElementById('viz-title').textContent = key;
  const w = el.clientWidth, h = el.clientHeight;
  const svg = d3.select(el).append('svg').attr('width', w).attr('height', h);
  const sim = d3.forceSimulation(g.nodes)
    .force('link', d3.forceLink(g.links).id(d => d.id).distance(90))
    .force('charge', d3.forceManyBody().strength(-260))
    .force('center', d3.forceCenter(w / 2, h / 2));
  const link = svg.append('g').selectAll('line').data(g.links).enter().append('line')
    .attr('stroke', '#cbd5e0').attr('stroke-width', 1.5);
  const ltext = svg.append('g').selectAll('text').data(g.links).enter().append('text')
    .text(d => d.p).attr('font-size', 9).attr('fill', '#a0aec0');
  const node = svg.append('g').selectAll('g').data(g.nodes).enter().append('g')
    .style('cursor', d => d.type === 'Patient' || d.type === 'Practitioner' ? 'pointer' : 'default')
    .on('click', (e, d) => { if (!d.center && (d.type === 'Patient' || d.type === 'Practitioner')) showHood(d.id); })
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.append('circle').attr('r', d => d.center ? 14 : 8)
    .attr('fill', d => TYPE_COLORS[d.type] || '#a0aec0').attr('stroke', '#fff').attr('stroke-width', 2);
  node.append('text').text(d => d.label).attr('x', 12).attr('y', 4).attr('font-size', 11).attr('fill', '#2d3748');
  node.append('title').text(d => d.id);
  sim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    ltext.attr('x', d => (d.source.x + d.target.x) / 2).attr('y', d => (d.source.y + d.target.y) / 2);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
}
"""


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def stats_cards(stats: Dict[str, Any]):
    by_type = stats.get("by_type", {})
    cards = [
        ("Graph nodes", stats.get("nodes")),
        ("Graph edges", stats.get("edges")),
        ("Patients", by_type.get("Patient", 0)),
        ("Conditions", by_type.get("Condition", 0)),
        ("Observations", by_type.get("Observation", 0)),
        ("Unresolved refs", stats.get("unresolved", 0)),
        ("Pending changes", stats.get("pending", 0)),
    ]
    return Div(
        *[Div(Div(label, cls="label"), Div(_fmt(v), cls="value"), cls="stat-card") for label, v in cards],
        cls="stats",
        id="fhir-stats",
        hx_get="/api/fhir/stats",
        hx_trigger="fhir-stats from:body",
        hx_swap="outerHTML",
    )


def search_form(concepts: Optional[List[str]] = None, hops: int = 1, evidence: str = "diagnoses", blurb: str = ""):
    chosen = set(concepts or [data.TYPE2_DM])
    boxes = [
        Label(
            Input(type="checkbox", name="concepts", value=cid, checked=cid in chosen),
            f" {name} ",
            Span(cid, cls="cid"),
        )
        for cid, name in data.CONCEPTS.items()
    ]
    return Form(
        P(blurb, cls="blurb") if blurb else "",
        Div(*boxes, cls="concepts"),
        Div(
            Label(
                "Expand ",
                Select(
                    *[Option(f"{h} hop{'s' if h != 1 else ''}", value=str(h), selected=h == hops) for h in range(MAX_HOPS + 1)],
                    name="hops",
                ),
                " over ",
                Code("narrower"),
            ),
            Label(
                Select(
                    Option("diagnoses only (exact, narrower)", value="diagnoses", selected=evidence == "diagnoses"),
                    Option("diagnoses + related labs", value="all", selected=evidence == "all"),
                    name="evidence",
                ),
            ),
            cls="row",
        ),
        Button("Search the FHIR graph", type="button", hx_post="/api/fhir/search", hx_include="closest form",
               hx_target="#results", hx_swap="innerHTML", hx_indicator="#spinner"),
        Span(" running…", id="spinner", cls="htmx-indicator muted"),
        id="search-form",
    )


def _stage(name: str, ms: float, what: str):
    return Div(Div(name, cls="name"), Div(f"{ms:.1f} ms", cls="ms"), Div(what, cls="what"), cls="stage")


def results_view(out: Dict[str, Any]):
    t = out.get("timings", {})
    expanded = out.get("expanded", [])
    pipeline = Div(
        _stage("1 · expand", t.get("expand_ms", 0), f"{len(expanded)} concept{'s' if len(expanded) != 1 else ''}"),
        _stage("2 · resolve", t.get("resolve_ms", 0), f"{out.get('seeds', 0)} FHIR resources"),
        _stage("3 · PPR", t.get("ppr_ms", 0), "ranked over the FHIR graph"),
        cls="pipeline",
    )
    chips = Div(*[Span(c["name"], cls="chip", title=c["id"]) for c in expanded])
    if not out.get("patients"):
        return Div(pipeline, chips, P(
            "No resource carries a code crosswalked to these concepts. "
            "A grouping concept has no codes of its own: try more hops.", cls="blurb"))

    def evidence_chips(ev):
        return [
            Span(e["code"], cls="chip lab" if e["key"].startswith("Observation/") else "chip", title=e.get("display", ""))
            for e in ev
        ]

    patients = Table(
        Tr(Th("#"), Th("Patient"), Th("Why"), Th("PPR score")),
        *[
            Tr(
                Td(str(i + 1)),
                Td(Div(p["name"] or p["key"]), Div(p["key"], cls="key")),
                Td(*evidence_chips(p.get("evidence", []))),
                Td(f"{p['score']:.5f}", cls="score"),
                cls="clickable",
                onclick=f"showHood('{p['key']}')",
            )
            for i, p in enumerate(out["patients"])
        ],
    )
    related = out.get("related") or []
    related_view = Div(
        H3("Near the cohort, no matching code"),
        P("Reached through shared encounters and clinicians. Candidates for review, not matches.", cls="blurb"),
        Table(*[
            Tr(Td(Div(p["name"] or p["key"]), Div(p["key"], cls="key")), Td(f"{p['score']:.5f}", cls="score"),
               cls="clickable", onclick=f"showHood('{p['key']}')")
            for p in related
        ]),
    ) if related else ""
    doctors = Table(
        Tr(Th("Clinician"), Th("Specialty"), Th("PPR score")),
        *[
            Tr(Td(Div(d["name"] or d["key"]), Div(d["key"], cls="key")), Td(d.get("specialty", "")),
               Td(f"{d['score']:.5f}", cls="score"), cls="clickable", onclick=f"showHood('{d['key']}')")
            for d in out.get("practitioners", [])
        ],
    )
    return Div(pipeline, chips, H3("Patients with the evidence"), patients, related_view,
               H3("Clinicians central to this cohort"), doctors)


def live_panel():
    options = [Option(f"{name} ({key})", value=key) for key, name in data.patients()[:40]]
    codes = [Option(f"{code} {display}", value=code) for code, (display, _) in data.ICD_CODES.items()]
    return Div(
        H2("Live: write to FHIR, then sync"),
        P("A Condition PUT through the FHIR service lands in the repository at once. "
          "The graph sees it at the next sync, which reads the repository's own change watermarks.", cls="blurb"),
        Form(
            Div(Select(*options, name="patient"), Select(*codes, name="code"), cls="row"),
            Div(
                Button("1 · Add condition", type="button", hx_post="/api/fhir/live/add", hx_include="closest form",
                       hx_target="#live-result", hx_swap="innerHTML"),
                Button("2 · Sync graph", type="button", cls="secondary", hx_post="/api/fhir/live/sync",
                       hx_target="#live-result", hx_swap="innerHTML"),
                cls="row",
            ),
        ),
        Div(id="live-result"),
        cls="panel",
    )


def unavailable(err: Exception):
    return Div(
        H2("FHIR namespace not reachable"),
        P(str(err)),
        P("Seed it with ", Code("PYTHONPATH=src python -m iris_demo_server.services.fhir_demo_data"),
          ", and point the demo at it with IVG_FHIR_HOST, IVG_FHIR_PORT and IVG_FHIR_NAMESPACE "
          "(defaults localhost, 31972, IVGFHIR)."),
        cls="error",
    )


def _html(component, status: int = 200, headers: Optional[Dict[str, str]] = None):
    return HTMLResponse(to_xml(component), status_code=status, headers=headers)


async def _body(request) -> Tuple[Dict[str, Any], bool]:
    if "application/json" in request.headers.get("content-type", ""):
        return await request.json(), True
    form = await request.form()
    body = {k: form.get(k) for k in form.keys()}
    body["concepts"] = form.getlist("concepts")
    return body, False


def register_fhir_routes(app):
    @app.get("/fhir")
    def fhir_page():
        try:
            client = get_fhir_client()
            stats = client.stats()
            error = None
        except Exception as exc:  # the page explains itself instead of a 500
            stats, error = {}, exc

        header = Div(
            H1("FHIR Repository as a Graph"),
            P("An ISC FHIR repository projected in place as one named graph. ",
              "Concepts expand over a concept graph, resolve through ", Code("code_crosswalk"),
              " to live Conditions and labs, and rank patients and clinicians with personalized PageRank. "
              "Names and codes are read from the repository; the graph holds topology only."),
            P(Code(stats.get("graph", "")), " · ", Code(stats.get("endpoint", "")),
              " · last sync ", stats.get("last_sync") or "never", cls="muted") if stats else "",
            stats_cards(stats) if stats else unavailable(error),
            Div(Button("Architecture", cls="secondary", hx_get="/arch/fhir", hx_target="#arch-body",
                       onclick="document.getElementById('arch').classList.add('open')"),
                style="margin-top:1rem"),
            cls="header",
        )
        search = Div(
            H2("Find patients by concept"),
            Div(*[
                Button(s["title"], cls="secondary", hx_get=f"/api/fhir/scenario/{name}",
                       hx_target="#search-form", hx_swap="outerHTML")
                for name, s in SCENARIOS.items()
            ], cls="scenarios"),
            search_form(),
            cls="panel",
        )
        results = Div(H2("Ranked results"), Div(P("Run a search or pick a scenario.", cls="muted"), id="results"),
                      cls="panel")
        viz = Div(
            H2("Neighbourhood ", Span(id="viz-title", cls="key")),
            Div(*[Span(I(style=f"background:{c}"), t) for t, c in TYPE_COLORS.items()], cls="legend"),
            Div(P("Click a patient or clinician to draw their references.", cls="muted",
                  style="padding:1rem"), id="viz"),
            cls="panel",
        )
        return Html(
            Head(
                Title("IRIS FHIR Graph Search Demo"),
                Meta(name="htmx-config", content='{"responseHandling":[{"code":".*","swap":true}]}'),
                Script(src="https://unpkg.com/htmx.org@2.0.0"),
                Script(src="https://d3js.org/d3.v7.min.js"),
                Style(CSS),
            ),
            Body(
                Div(
                    header,
                    Div(search, results, cls="demo-grid"),
                    Div(viz, live_panel(), cls="demo-grid"),
                    cls="container",
                ),
                Div(Div(Div(id="arch-body"),
                        Button("Close", onclick="document.getElementById('arch').classList.remove('open')"),
                        onclick="event.stopPropagation()"),
                    id="arch", cls="modal", onclick="this.classList.remove('open')"),
                Script(NotStr(VIZ_JS % json.dumps(TYPE_COLORS))),
            ),
        )

    @app.get("/api/fhir/stats")
    def fhir_stats():
        try:
            return _html(stats_cards(get_fhir_client().stats()))
        except Exception as exc:
            return _html(unavailable(exc), 503)

    @app.get("/api/fhir/scenario/{name}")
    def fhir_scenario(name: str):
        s = SCENARIOS.get(name)
        if not s:
            return _html(Div(f"no scenario {name!r}", cls="error"), 404)
        return _html(search_form(s["concepts"], s["hops"], s["evidence"], s["blurb"]))

    @app.post("/api/fhir/search")
    async def fhir_search(request):
        body, is_json = await _body(request)
        try:
            args = parse_search(body)
        except BadInput as exc:
            if is_json:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return _html(Div(str(exc), cls="error"), 400)
        try:
            out = get_fhir_client().search(args["concepts"], args["hops"], args["relations"], args["top_k"])
        except Exception as exc:
            if is_json:
                return JSONResponse({"error": str(exc)}, status_code=503)
            return _html(unavailable(exc), 503)
        if is_json:
            return JSONResponse(out)
        return _html(results_view(out))

    @app.get("/api/fhir/neighborhood/{key:path}")
    def fhir_neighborhood(key: str):
        if not _RESOURCE_KEY.match(key):
            return JSONResponse({"error": f"{key!r} is not a FHIR resource key"}, status_code=400)
        try:
            return JSONResponse(get_fhir_client().neighborhood(key))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @app.post("/api/fhir/live/add")
    async def fhir_live_add(request):
        body, _ = await _body(request)
        patient, code = body.get("patient") or "", body.get("code") or ""
        if not _PATIENT_KEY.match(patient):
            return _html(Div(f"{patient!r} is not a Patient key", cls="error"), 400)
        if code not in data.ICD_CODES:
            return _html(Div(f"{code!r} is not one of the demo's ICD-10 codes", cls="error"), 400)
        try:
            out = get_fhir_client().add_condition(patient, code)
        except Exception as exc:
            return _html(Div(str(exc), cls="error"), 503)
        return _html(
            Div(f"PUT {out['key']} → {out.get('status')}. The repository has it; the graph does not yet. "
                "Search now to see it missing, then sync.", cls="ok"),
            headers={"HX-Trigger": "fhir-stats"},
        )

    @app.post("/api/fhir/live/sync")
    def fhir_live_sync():
        try:
            out = get_fhir_client().sync()
        except Exception as exc:
            return _html(Div(str(exc), cls="error"), 503)
        if out.get("status") == "busy":
            return _html(Div("Another sync holds the graph; try again in a moment.", cls="error"), 409)
        return _html(
            Div(f"Synced {out.get('keys', 0)} changed resource(s) in {out.get('ms', 0):.0f} ms. "
                "Run the search again.", cls="ok"),
            headers={"HX-Trigger": "fhir-stats"},
        )
