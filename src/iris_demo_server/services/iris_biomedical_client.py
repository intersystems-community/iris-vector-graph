"""IRIS client for the biomedical demo: reads the seeded named graph `bio:demo`.

Every query is scoped to that graph through `Graph_KG.rdf_props` / `rdf_edges`, so a
same-named node elsewhere in the namespace never reaches the page. Proteins are
addressed by gene symbol (`TP53`); the graph stores them as `protein:TP53`.

Search is text over the seed's properties, not vector similarity: the demo graph has
no embeddings. A name match scores 1.0 for the exact symbol and 0.8 otherwise; a
function match scores the fraction of query terms the annotation contains.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..models.biomedical import (
    Interaction,
    InteractionNetwork,
    PathwayQuery,
    PathwayResult,
    Protein,
    ProteinSearchQuery,
    SimilaritySearchResult,
)
from . import bio_demo_data as bio

MAX_NETWORK_NODES = 500


def term_score(query_text: str, annotation: str) -> float:
    """Fraction of the query's terms found in the annotation (case-insensitive)."""
    terms = [t for t in query_text.lower().split() if t]
    if not terms:
        return 0.0
    text = (annotation or "").lower()
    return sum(1 for t in terms if t in text) / len(terms)


class IRISBiomedicalClient:
    def __init__(self, conn=None, graph: str = bio.GRAPH):
        self.conn = conn if conn is not None else bio.connect()
        self.graph = graph

    # ---------------------------------------------------------------- helpers

    def _rows(self, sql: str, params: Tuple = ()) -> List[Tuple]:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            cur.close()

    def _props(self, node_ids: List[str]) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for i in range(0, len(node_ids), 200):
            chunk = node_ids[i:i + 200]
            marks = ",".join("?" * len(chunk))
            for s, k, v in self._rows(
                f'SELECT s, "key", val FROM Graph_KG.rdf_props '
                f"WHERE graph_id = ? AND s IN ({marks})",
                (self.graph, *chunk),
            ):
                out.setdefault(s, {})[k] = v
        return out

    def _protein(self, nid: str, props: Dict[str, str]) -> Protein:
        sym = props.get("symbol") or bio.symbol(nid)
        return Protein(
            protein_id=sym,
            name=props.get("name") or sym,
            organism=props.get("organism") or bio.ORGANISM,
            function_description=props.get("annotation"),
        )

    def _edges_touching(self, node_ids: List[str]) -> List[Tuple[str, str, Optional[str]]]:
        seen, out = set(), []
        for i in range(0, len(node_ids), 200):
            chunk = node_ids[i:i + 200]
            marks = ",".join("?" * len(chunk))
            for s, o, q in self._rows(
                f"SELECT s, o_id, qualifiers FROM Graph_KG.rdf_edges "
                f"WHERE graph_id = ? AND p = ? AND (s IN ({marks}) OR o_id IN ({marks}))",
                (self.graph, bio.PREDICATE, *chunk, *chunk),
            ):
                if (s, o) not in seen:
                    seen.add((s, o))
                    out.append((s, o, q))
        return out

    @staticmethod
    def _qualifiers(raw: Optional[str]) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}

    def _interaction(self, s: str, o: str, raw: Optional[str]) -> Interaction:
        q = self._qualifiers(raw)
        return Interaction(
            source_protein_id=bio.symbol(s),
            target_protein_id=bio.symbol(o),
            interaction_type=q.get("type") or "binding",
            confidence_score=float(q.get("confidence", 0.5)),
            evidence="illustrative seed",
        )

    # ------------------------------------------------------------------ reads

    def stats(self) -> Dict[str, int]:
        n, e = bio.counts(self.conn)
        return {"proteins": n, "interactions": e}

    async def search_proteins(self, query: ProteinSearchQuery) -> SimilaritySearchResult:
        text = query.query_text.strip()
        if query.query_type.value == "name":
            like = f"%{text.upper()}%"
            rows = self._rows(
                'SELECT DISTINCT s FROM Graph_KG.rdf_props WHERE graph_id = ? '
                '''AND "key" IN ('symbol', 'name') AND UPPER(val) LIKE ?''',
                (self.graph, like),
            )
            ids = [r[0] for r in rows]
            props = self._props(ids)
            scored = [
                (1.0 if (props.get(i, {}).get("symbol") or "").upper() == text.upper() else 0.8, i)
                for i in ids
            ]
            method = "name_text_match"
        else:
            rows = self._rows(
                'SELECT s, val FROM Graph_KG.rdf_props WHERE graph_id = ? AND "key" = ?',
                (self.graph, "annotation"),
            )
            scored = [(term_score(text, v), s) for s, v in rows]
            scored = [x for x in scored if x[0] > 0]
            props = self._props([s for _, s in scored])
            method = "annotation_term_match"
        scored.sort(key=lambda x: (-x[0], bio.symbol(x[1])))
        scored = scored[: query.top_k]
        return SimilaritySearchResult(
            proteins=[self._protein(i, props.get(i, {})) for _, i in scored],
            similarity_scores=[round(s, 3) for s, _ in scored],
            search_method=method,
        )

    async def get_interaction_network(self, protein_id: str, expand_depth: int = 1) -> InteractionNetwork:
        center = bio.node_id(protein_id)
        props = self._props([center])
        if center not in props:
            raise RuntimeError(f"Protein {protein_id} not found in {self.graph}")
        frontier, nodes, edges = [center], {center}, []
        for _ in range(max(1, expand_depth)):
            if not frontier:
                break
            found = self._edges_touching(frontier)
            nxt = []
            for s, o, q in found:
                for n in (s, o):
                    if n not in nodes and len(nodes) < MAX_NETWORK_NODES:
                        nodes.add(n)
                        nxt.append(n)
            edges.extend(found)
            frontier = nxt
        props.update(self._props([n for n in nodes if n != center]))
        seen, links = set(), []
        for s, o, q in edges:
            if s in nodes and o in nodes and (s, o) not in seen:
                seen.add((s, o))
                links.append(self._interaction(s, o, q))
        return InteractionNetwork(
            nodes=[self._protein(n, props.get(n, {})) for n in sorted(nodes, key=lambda x: x != center)],
            edges=links,
            layout_hints={"force_strength": -200, "link_distance": 80},
        )

    async def find_pathway(self, query: PathwayQuery) -> PathwayResult:
        src, dst = bio.node_id(query.source_protein_id), bio.node_id(query.target_protein_id)
        props = self._props([src, dst])
        for nid, raw in ((src, query.source_protein_id), (dst, query.target_protein_id)):
            if nid not in props:
                raise RuntimeError(f"Protein {raw} not found in {self.graph}")
        path, hop_edges = self._bfs(src, dst, query.max_hops)
        if path is None:
            raise RuntimeError(
                f"No path from {query.source_protein_id} to {query.target_protein_id} "
                f"within {query.max_hops} hops")
        props.update(self._props(path))
        interactions = [self._interaction(*hop_edges[i]) for i in range(len(path) - 1)]
        conf = sum(i.confidence_score for i in interactions) / len(interactions)
        return PathwayResult(
            path=[bio.symbol(n) for n in path],
            intermediate_proteins=[self._protein(n, props.get(n, {})) for n in path],
            path_interactions=interactions,
            confidence=round(conf, 4),
        )

    def _bfs(self, src: str, dst: str, max_hops: int):
        """Undirected BFS; returns (path, [(s, o, qualifiers) per hop]) or (None, None)."""
        if src == dst:
            return None, None
        parent: Dict[str, Tuple[str, Tuple[str, str, Optional[str]]]] = {}
        seen, frontier = {src}, [src]
        for _ in range(max_hops):
            nxt, here_set = [], set(frontier)
            for s, o, q in self._edges_touching(frontier):
                for here, there in ((s, o), (o, s)):
                    if here in here_set and there not in seen:
                        seen.add(there)
                        parent[there] = (here, (s, o, q))
                        nxt.append(there)
            if dst in seen:
                path, hops, cur = [dst], [], dst
                while cur != src:
                    prev, edge = parent[cur]
                    hops.append(edge)
                    path.append(prev)
                    cur = prev
                return path[::-1], hops[::-1]
            frontier = sorted(nxt)
            if not frontier:
                break
        return None, None

    def close(self):
        if self.conn:
            self.conn.close()
