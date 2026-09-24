"""The FHIR graph demo's view of a FHIR repository projected as a named graph (spec 231).

Everything the page shows comes from three places, and the client keeps them apart:

- the graph (`Graph_KG.rdf_edges` / `rdf_labels` for one `graph_id`): topology only;
- the engine's spec 231 operators: expand, resolve, PPR;
- the repository's own `Rsrc` table, read live for names and codes, which the graph
  never copies.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from . import fhir_demo_data as data

_SCHEMA = re.compile(r"^[A-Za-z0-9_]+$")
_CHUNK = 200
_SPECIALTY = {f"Practitioner/{p['id']}": p["specialty"] for p in data.PRACTITIONERS}


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


class FHIRGraphDemoClient:
    def __init__(self, engine, graph: Optional[str] = None, concept_graph: str = data.CONCEPT_GRAPH):
        self.engine = engine
        self.conn = engine.conn
        self.concept_graph = concept_graph
        self.graph, rsrc_schema = self._registered(graph)
        if not _SCHEMA.match(rsrc_schema):
            raise ValueError(f"unexpected Rsrc schema {rsrc_schema!r}")
        self._rsrc = f"{rsrc_schema}.Rsrc"
        self._dispatch = None

    @classmethod
    def from_env(cls) -> "FHIRGraphDemoClient":
        import os

        return cls(data.make_engine(data.connect()), graph=os.getenv("IVG_FHIR_GRAPH") or None)

    # ------------------------------------------------------------------ SQL

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> List[tuple]:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, list(params))
            return [tuple(r) for r in cur.fetchall()]
        finally:
            cur.close()

    def _registered(self, graph: Optional[str]):
        rows = self._rows("SELECT graph_id, rsrc_schema FROM Graph_KG.fhir_graphs")
        if graph:
            rows = [r for r in rows if r[0] == graph]
        if not rows:
            raise LookupError(
                f"no registered FHIR graph{' ' + graph if graph else ''} in this namespace; "
                "run python -m iris_demo_server.services.fhir_demo_data"
            )
        if len(rows) > 1:
            raise LookupError(f"several FHIR graphs registered ({[r[0] for r in rows]}); set IVG_FHIR_GRAPH")
        return rows[0]

    def _chunked(self, sql: str, keys: Sequence[str], lead: Sequence[Any] = ()) -> List[tuple]:
        """`sql` holds one `{marks}` for an IN list; run it per chunk of keys."""
        out: List[tuple] = []
        keys = list(dict.fromkeys(keys))
        for i in range(0, len(keys), _CHUNK):
            part = keys[i : i + _CHUNK]
            out += self._rows(sql.format(marks=", ".join("?" * len(part))), [*lead, *part])
        return out

    def resources(self, keys: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Current JSON of each live resource, read from the repository."""
        out = {}
        rows = self._chunked(
            f"SELECT Key, ResourceString FROM {self._rsrc} WHERE Deleted = 0 AND Key IN ({{marks}})", keys
        )
        for key, text in rows:
            try:
                out[key] = json.loads(text) if text else {}
            except ValueError:
                out[key] = {}
        return out

    # ------------------------------------------------------------ overview

    def status(self) -> Dict[str, Any]:
        return self.engine.fhir_graph_status(self.graph)

    def stats(self) -> Dict[str, Any]:
        st = self.status()
        by_type = dict(
            self._rows(
                "SELECT label, COUNT(*) FROM Graph_KG.rdf_labels WHERE graph_id = ? GROUP BY label",
                [self.graph],
            )
        )
        unresolved = st.get("unresolved") or {}
        return {
            "graph": self.graph,
            "nodes": st.get("nodes", 0),
            "edges": st.get("edges", 0),
            "by_type": {k: int(v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])},
            "unresolved": sum(int(v) for v in unresolved.values()),
            "pending": st.get("pending", 0),
            "last_sync": st.get("last_sync", ""),
            "endpoint": (st.get("endpoints") or [""])[0],
        }

    # -------------------------------------------------------------- search

    def search(
        self,
        concepts: Sequence[str],
        hops: int = 1,
        relations: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """Expand, resolve, rank: the spec 231 pipeline one stage at a time, so the
        page can show what each stage found and what it cost."""
        started = time.perf_counter()
        expanded = (
            self.engine.fhir_expand_concepts(self.concept_graph, list(concepts), hops=hops)
            if hops
            else list(concepts)
        )
        expand_ms = _ms(started)

        started = time.perf_counter()
        seeds = self.engine.fhir_resolve_concepts(
            self.graph, self.concept_graph, expanded, relations=relations
        )
        resolve_ms = _ms(started)

        started = time.perf_counter()
        scores = (
            self.engine.kg_PERSONALIZED_PAGERANK(
                seeds,
                damping_factor=0.85,
                max_iterations=20,
                return_top_k=None,
                bidirectional=True,
                graph=self.graph,
            )
            if seeds
            else {}
        )
        ppr_ms = _ms(started)

        evidence = self._evidence(seeds)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        matched = [(k, s) for k, s in ranked if k.startswith("Patient/") and k in evidence][:top_k]
        related = [(k, s) for k, s in ranked if k.startswith("Patient/") and k not in evidence][:5]
        doctors = [(k, s) for k, s in ranked if k.startswith("Practitioner/")][:5]
        docs = self.resources([k for k, _ in matched + related + doctors])

        return {
            "concepts": list(concepts),
            "expanded": [{"id": c, "name": data.CONCEPTS.get(c, c)} for c in expanded],
            "seeds": len(seeds),
            "patients": [
                {"key": k, "name": data.display_name(docs.get(k, {})), "score": s, "evidence": evidence[k]}
                for k, s in matched
            ],
            "related": [
                {"key": k, "name": data.display_name(docs.get(k, {})), "score": s} for k, s in related
            ],
            "practitioners": [
                {
                    "key": k,
                    "name": data.display_name(docs.get(k, {})),
                    "score": s,
                    "specialty": _SPECIALTY.get(k, ""),
                }
                for k, s in doctors
            ],
            "timings": {"expand_ms": expand_ms, "resolve_ms": resolve_ms, "ppr_ms": ppr_ms},
        }

    def _evidence(self, seeds: Sequence[str]) -> Dict[str, List[Dict[str, str]]]:
        """patient key -> the seed resources that name them, with their codes."""
        if not seeds:
            return {}
        subjects = self._chunked(
            "SELECT s, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = 'subject' "
            "AND o_id %STARTSWITH 'Patient/' AND s IN ({marks})",
            seeds,
            lead=[self.graph],
        )
        docs = self.resources([s for s, _ in subjects])
        out: Dict[str, List[Dict[str, str]]] = {}
        for seed_key, patient in subjects:
            coding = ((docs.get(seed_key, {}).get("code") or {}).get("coding") or [{}])[0]
            out.setdefault(patient, []).append(
                {"key": seed_key, "code": coding.get("code", ""), "display": coding.get("display", "")}
            )
        return out

    # -------------------------------------------------------- neighbourhood

    def neighborhood(self, key: str, limit: int = 80) -> Dict[str, Any]:
        """The key, every resource one reference away, and the out-references of
        the clinical events among them (who saw the patient, where)."""
        first = self._rows(
            "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND (s = ? OR o_id = ?)",
            [self.graph, key, key],
        )
        ring = {s for s, _, _ in first} | {o for _, _, o in first}
        events = [k for k in ring if k.split("/")[0] in ("Encounter", "Condition", "Observation") and k != key]
        second = (
            self._chunked(
                "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ? AND s IN ({marks})",
                events,
                lead=[self.graph],
            )
            if events
            else []
        )
        pairs: Dict[tuple, List[str]] = {}
        for s, p, o in first + second:
            pairs.setdefault((s, o), [])
            if p not in pairs[(s, o)]:
                pairs[(s, o)].append(p)

        ids = [key] + sorted({n for pair in pairs for n in pair} - {key})
        ids = ids[:limit]
        keep = set(ids)
        docs = self.resources(ids)
        nodes = []
        for i in ids:
            rtype = i.split("/", 1)[0]
            doc = docs.get(i, {})
            label = data.display_name(doc)
            if not label and rtype == "Encounter":
                label = f"Visit {(doc.get('period') or {}).get('start', '')}".strip()
            if not label:
                coding = ((doc.get("code") or {}).get("coding") or [{}])[0]
                label = coding.get("display") or coding.get("code") or i
            nodes.append({"id": i, "type": rtype, "label": label, "center": i == key})
        links = [
            {"source": s, "target": o, "p": ", ".join(ps)}
            for (s, o), ps in pairs.items()
            if s in keep and o in keep
        ]
        return {"nodes": nodes, "links": links}

    # ----------------------------------------------------------------- live

    def dispatch(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._dispatch is None:
            self._dispatch = data.make_dispatch(self.conn)
        return self._dispatch(method, path, body)

    def add_condition(self, patient: str, code: str) -> Dict[str, Any]:
        """Write a Condition to the repository through the FHIR service. The graph
        does not see it until the next sync."""
        cid = data.live_condition_id(patient, code)
        resource = data.condition(
            cid, patient.split("/", 1)[1], code, None, _dt.date.today().isoformat()
        )
        out = data.put(lambda m, p, b: self.dispatch(m, p, b), resource)
        return {"key": f"Condition/{cid}", "status": out.get("status")}

    def sync(self) -> Dict[str, Any]:
        started = time.perf_counter()
        before = self.status().get("pending", 0)
        reply = data.sync_until_done(self.engine, self.graph)
        return {**reply, "applied": before, "ms": _ms(started)}
