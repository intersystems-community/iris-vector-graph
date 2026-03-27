"""Data models for iris-vector-graph."""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class SubgraphData:
    """Complete bounded subgraph extracted via kg_SUBGRAPH.

    Contains all nodes, edges, properties, labels, and optionally embeddings
    within k hops of seed nodes.
    """
    nodes: List[str] = field(default_factory=list)
    edges: List[Tuple[str, str, str]] = field(default_factory=list)
    node_properties: Dict[str, Dict[str, str]] = field(default_factory=dict)
    node_labels: Dict[str, List[str]] = field(default_factory=dict)
    node_embeddings: Dict[str, List[float]] = field(default_factory=dict)
    seed_ids: List[str] = field(default_factory=list)


@dataclass
class PprGuidedSubgraphData:
    """PPR-pruned subgraph extracted via kg_PPR_GUIDED_SUBGRAPH.

    Uses Personalized PageRank to select the most relevant nodes before BFS,
    preventing exponential D^k blowup at k>=3.  Based on PPRGo (KDD 2020).

    alpha is teleport probability (NOT damping). damping = 1 - alpha.
    """
    nodes: List[str] = field(default_factory=list)
    edges: List[dict] = field(default_factory=list)
    ppr_scores: List[Tuple[str, float]] = field(default_factory=list)
    seed_ids: List[str] = field(default_factory=list)
    nodes_before_pruning: int = 0
    nodes_after_pruning: int = 0
