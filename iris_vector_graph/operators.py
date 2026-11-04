import logging
from typing import List, Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)


class IRISGraphOperators:
    """The ``kg_*`` retrieval operators under their older calling conventions.

    Each method forwards to the ``IRISGraphEngine`` method of the same name on an engine
    this object creates over ``connection``, adapting the arguments where the old and new
    signatures differ. New code can call the engine directly.
    """

    def __init__(self, connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = connection
        self._engine = IRISGraphEngine(connection)

    def kg_KNN_VEC(self, query_vector, k=50, label_filter=None, graph=None,
                   model_key=None):
        """Same as ``IRISGraphEngine.kg_KNN_VEC``: up to ``k`` ``(node_id, cosine)`` pairs
        nearest ``query_vector`` in one graph's routed embedding table. ``dtype`` cannot be
        passed here; the engine's vector type is used."""
        return self._engine.kg_KNN_VEC(
            query_vector, k=k, label_filter=label_filter,
            graph=graph, model_key=model_key,
        )

    def kg_TXT(self, query_text, k=50, min_confidence=0, graph=None):
        """Same as ``IRISGraphEngine.kg_TXT``: full-text search over one graph's documents,
        as ``(node_id, score)`` pairs."""
        return self._engine.kg_TXT(
            query_text, k=k, min_confidence=min_confidence, graph=graph
        )

    def kg_RRF_FUSE(self, k=50, k1=200, k2=200, c=60, query_vector="[]", query_text="",
                    graph=None):
        """Same as ``IRISGraphEngine.kg_RRF_FUSE``, with defaults for every argument:
        reciprocal-rank fusion of a vector leg and a BM25 leg, as ``(node_id, rrf,
        vector_score, bm25_score)`` tuples."""
        return self._engine.kg_RRF_FUSE(k=k, k1=k1, k2=k2, c=c,
                                         query_vector=query_vector, query_text=query_text,
                                         graph=graph)

    def kg_GRAPH_PATH(self, src_id, pred1, pred2, max_hops=2):
        """Same as ``IRISGraphEngine.kg_GRAPH_PATH``: two-hop paths
        ``src_id -pred1-> b -pred2-> c``, one row per step."""
        return self._engine.kg_GRAPH_PATH(src_id, pred1, pred2, max_hops=max_hops)

    def kg_GRAPH_WALK(self, start_entity, max_depth=3, edge_types=None, max_results=100):
        """Same as ``IRISGraphEngine.kg_GRAPH_WALK``: a breadth-first walk outbound from
        ``start_entity``, up to ``max_depth`` hops."""
        return self._engine.kg_GRAPH_WALK(start_entity, max_depth=max_depth,
                                           edge_types=edge_types, max_results=max_results)

    def kg_GRAPH_WALK_TVF(self, start_entity, max_depth=3, edge_types=None, max_results=100):
        """Same as ``IRISGraphEngine.kg_GRAPH_WALK_TVF``, the older name of
        ``kg_GRAPH_WALK``."""
        return self._engine.kg_GRAPH_WALK_TVF(start_entity, max_depth=max_depth,
                                               edge_types=edge_types, max_results=max_results)

    def kg_NEIGHBORHOOD_EXPANSION(self, *args, **kwargs):
        """Same as ``IRISGraphEngine.kg_NEIGHBORHOOD_EXPANSION`` (arguments passed through):
        the seeds' one-hop outgoing edges whose ``confidence`` qualifier meets the
        threshold."""
        return self._engine.kg_NEIGHBORHOOD_EXPANSION(*args, **kwargs)

    def kg_VECTOR_GRAPH_SEARCH(self, query_vector, query_text=None, k=15,
                               k_vector=None, k_final=None, expansion_depth=1,
                               min_confidence=0.5, graph=None):
        """Same as ``IRISGraphEngine.kg_VECTOR_GRAPH_SEARCH``: vector hits, their one-hop
        neighbours and text hits, scored by vector similarity. The engine takes one ``k``:
        it is ``k_final`` if given, else ``k_vector``, else ``k``."""
        return self._engine.kg_VECTOR_GRAPH_SEARCH(
            query_vector=query_vector,
            query_text=query_text,
            k=k_final or k_vector or k,
            expansion_depth=expansion_depth,
            min_confidence=min_confidence,
            graph=graph,
        )

    def kg_PAGERANK(self, seed_entities=None, damping=0.85, max_iterations=20,
                    bidirectional=False, reverse_weight=1.0):
        """Same as ``IRISGraphEngine.kg_PAGERANK``: PageRank as ``(node_id, score)`` pairs.
        ``bidirectional`` and ``reverse_weight`` are accepted and not passed on, so the
        engine's defaults (directed, weight 1.0) always apply."""
        return self._engine.kg_PAGERANK(seed_entities=seed_entities, damping=damping,
                                         max_iterations=max_iterations)

    def kg_WCC(self, max_iterations=None):
        """Same as ``IRISGraphEngine.kg_WCC``: weakly connected components, as
        ``{node: component}``."""
        return self._engine.kg_WCC(max_iterations=max_iterations)

    def kg_CDLP(self, max_iterations=10):
        """Same as ``IRISGraphEngine.kg_CDLP``: communities by label propagation, as
        ``{node: community}``."""
        return self._engine.kg_CDLP(max_iterations=max_iterations)

    def kg_SUBGRAPH(self, seed_ids, k_hops=2, edge_types=None, include_properties=True,
                    include_embeddings=False, max_nodes=10000):
        """Same as ``IRISGraphEngine.kg_SUBGRAPH``: the subgraph within ``k_hops`` of
        ``seed_ids``, as a ``SubgraphData``."""
        return self._engine.kg_SUBGRAPH(seed_ids, k_hops=k_hops, edge_types=edge_types,
                                         include_properties=include_properties,
                                         include_embeddings=include_embeddings,
                                         max_nodes=max_nodes)

    def kg_PPR_GUIDED_SUBGRAPH(self, seed_ids, ppr_top_k=50, top_k=None,
                                k_hops=1, max_hops=None, damping=0.85,
                                max_iterations=10, edge_types=None, max_nodes=5000):
        """Same as ``IRISGraphEngine.kg_PPR_GUIDED_SUBGRAPH``: the subgraph around the nodes
        personalized PageRank ranks highest for ``seed_ids``. ``top_k``, when given,
        replaces ``ppr_top_k``, and ``max_hops`` replaces ``k_hops``."""
        return self._engine.kg_PPR_GUIDED_SUBGRAPH(
            seed_ids,
            ppr_top_k=top_k if top_k is not None else ppr_top_k,
            k_hops=max_hops if max_hops is not None else k_hops,
            damping=damping, max_iterations=max_iterations,
            edge_types=edge_types, max_nodes=max_nodes)

    def kg_NEIGHBORS(self, source_ids, predicate=None, direction="out",
                     distinct=True, chunk_size=500):
        """Same as ``IRISGraphEngine.kg_NEIGHBORS``: the node IDs one hop from
        ``source_ids``, along ``predicate`` edges if given."""
        return self._engine.kg_NEIGHBORS(source_ids, predicate=predicate,
                                          direction=direction, distinct=distinct,
                                          chunk_size=chunk_size)

    def kg_MENTIONS(self, source_ids, predicate="MENTIONS", direction="out"):
        """Same as ``IRISGraphEngine.kg_MENTIONS``: the IDs one hop away along ``MENTIONS``
        edges (or ``predicate``)."""
        return self._engine.kg_MENTIONS(source_ids, predicate=predicate, direction=direction)

    def kg_PPR(self, seed_entities, damping=0.85, max_iterations=20):
        """Same as ``IRISGraphEngine.kg_PPR``: personalized PageRank from
        ``seed_entities``, as ``(node_id, score)`` pairs, best first."""
        return self._engine.kg_PPR(seed_entities, damping=damping, max_iterations=max_iterations)

    def kg_RERANK(self, top_n, query_vector, query_text):
        """Same as ``IRISGraphEngine.kg_RERANK``: the ``top_n`` best nodes for a vector and
        a text query, fused by ``kg_RRF_FUSE``."""
        return self._engine.kg_RERANK(top_n, query_vector, query_text)
