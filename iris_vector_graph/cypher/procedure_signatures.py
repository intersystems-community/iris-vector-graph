"""What every procedure IVG answers to `CALL` yields: each column's name and Cypher type (4.6.0 R314).

One entry per procedure in the translator's registry (`translator._IVG_PROCEDURES`), the engine's handlers
(`IRISGraphEngine._SYSTEM_PROCEDURES`) and the gds.* shims (`_engine.query.GDS_SHIM_MAP`), spelled as the
documentation spells it. A unit test fails when any of those registries holds a procedure this one lacks, or the
reverse, and the per-procedure value tests are generated from it: a LIST column answers a Python list, a MAP or PATH
column a dict (a PATH's keys are in :data:`PATH_KEYS`), a scalar keeps its type, and a NODE column comes back as a
node does in `RETURN n`.

Types: STRING, INTEGER, FLOAT, BOOLEAN, LIST, MAP, NODE, RELATIONSHIP, PATH, ANY.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

TYPES = frozenset({"STRING", "INTEGER", "FLOAT", "BOOLEAN", "LIST", "MAP", "NODE", "RELATIONSHIP", "PATH", "ANY"})

#: the types whose value is a Python list or dict (the rest are scalars or a node's columns)
CONTAINER_TYPES = frozenset({"LIST", "MAP", "PATH"})

Signature = Tuple[Tuple[str, str], ...]

_NODE_SCORE: Signature = (("node", "NODE"), ("score", "FLOAT"))
_GDS_STREAM: Signature = (("nodeId", "STRING"), ("score", "FLOAT"))
_DIJKSTRA: Signature = (
    ("index", "INTEGER"), ("sourceNode", "STRING"), ("targetNode", "STRING"), ("totalCost", "FLOAT"),
    ("nodeIds", "LIST"), ("costs", "LIST"),
)
_CURRENT_USER: Signature = (("username", "STRING"), ("roles", "LIST"), ("flags", "LIST"))

#: procedure -> its yielded columns, in the order the procedure answers them
PROCEDURE_SIGNATURES: Dict[str, Signature] = {
    # translated to a CTE (translator._IVG_PROCEDURES); ivg.vector.search and ivg.shortestPath.weighted also have a
    # handler, which answers the call alone
    "ivg.vector.search": _NODE_SCORE,
    "ivg.neighbors": (("neighbor", "NODE"),),
    "ivg.ppr": _NODE_SCORE,
    "ivg.bm25.search": _NODE_SCORE,
    "ivg.ivf.search": _NODE_SCORE,
    "ivg.retrieve": (("node", "NODE"), ("rrf_score", "FLOAT")),
    "ivg.shortestPath.weighted": (("path", "PATH"), ("totalCost", "FLOAT")),
    "ivg.degreeCentrality": (("node", "NODE"), ("score", "FLOAT"), ("degree", "INTEGER")),
    "ivg.betweenness": _NODE_SCORE,
    "ivg.closeness": _NODE_SCORE,
    "ivg.eigenvector": _NODE_SCORE,
    "ivg.leiden": (("node", "NODE"), ("community", "INTEGER"), ("size", "INTEGER")),
    "ivg.triangleCount": (("node", "NODE"), ("triangles", "INTEGER"), ("lcc", "FLOAT")),
    "ivg.scc": (("node", "NODE"), ("component", "INTEGER"), ("size", "INTEGER")),
    "ivg.kcore": (("node", "NODE"), ("coreness", "INTEGER")),
    # answered by an engine handler (IRISGraphEngine._SYSTEM_PROCEDURES)
    "db.labels": (("label", "STRING"),),
    "db.relationshipTypes": (("relationshipType", "STRING"),),
    "db.propertyKeys": (("propertyKey", "STRING"),),
    "db.schema.visualization": (("nodes", "LIST"), ("relationships", "LIST")),
    "db.schema.nodeTypeProperties": (
        ("nodeType", "STRING"), ("nodeLabels", "LIST"), ("propertyName", "STRING"), ("propertyTypes", "LIST"),
        ("mandatory", "BOOLEAN"),
    ),
    "db.schema.relTypeProperties": (
        ("relType", "STRING"), ("propertyName", "STRING"), ("propertyTypes", "LIST"), ("mandatory", "BOOLEAN"),
    ),
    "dbms.components": (("name", "STRING"), ("versions", "LIST"), ("edition", "STRING")),
    "dbms.procedures": (
        ("name", "STRING"), ("signature", "STRING"), ("description", "STRING"), ("mode", "STRING"),
        ("admin", "BOOLEAN"), ("option", "MAP"), ("defaultBuiltInRoles", "STRING"), ("isDeprecated", "BOOLEAN"),
        ("worksOnSystem", "BOOLEAN"), ("argumentDescription", "LIST"),
    ),
    "dbms.functions": (
        ("name", "STRING"), ("signature", "STRING"), ("description", "STRING"), ("aggregating", "BOOLEAN"),
        ("defaultBuiltInRoles", "LIST"), ("isDeprecated", "BOOLEAN"), ("argumentDescription", "LIST"),
        ("returnDescription", "STRING"), ("category", "STRING"),
    ),
    "dbms.clientConfig": (("key", "STRING"), ("value", "STRING")),
    "dbms.security.showCurrentUser": _CURRENT_USER,
    "dbms.showCurrentUser": _CURRENT_USER,
    "dbms.queryJmx": (("name", "STRING"), ("description", "STRING"), ("attributes", "MAP")),
    "apoc.meta.data": (
        ("label", "STRING"), ("property", "STRING"), ("type", "STRING"), ("elementType", "STRING"),
        ("unique", "BOOLEAN"), ("index", "BOOLEAN"), ("existence", "BOOLEAN"),
    ),
    "apoc.meta.schema": (("value", "MAP"),),
    "gds.pageRank.stream": _GDS_STREAM,
    "gds.betweenness.stream": _GDS_STREAM,
    "gds.shortestPath.dijkstra.stream": _DIJKSTRA,
    "gds.shortestPath.dijkstra": _DIJKSTRA,
    # gds.* shims (GDS_SHIM_MAP): the ivg procedure's columns, called by the gds name
    "gds.louvain.stream": (("node", "NODE"), ("community", "INTEGER"), ("size", "INTEGER")),
    "gds.nodeSimilarity.stream": _NODE_SCORE,
}

#: a PATH column's keys and their types: the dict it answers holds exactly these
PATH_KEYS: Dict[Tuple[str, str], Dict[str, str]] = {
    ("ivg.shortestPath.weighted", "path"): {
        "nodes": "LIST", "rels": "LIST", "costs": "LIST", "length": "INTEGER", "totalCost": "FLOAT",
    },
}

_BY_LOWER = {name.lower(): name for name in PROCEDURE_SIGNATURES}


def signature(name: str) -> Optional[Signature]:
    """The yielded columns of procedure `name`, any case, or None for one IVG does not answer."""
    return PROCEDURE_SIGNATURES.get(_BY_LOWER.get(name.lower()))


def path_keys(name: str, column: str) -> Optional[Dict[str, str]]:
    """The keys of the dict PATH column `column` of procedure `name` answers, with their types."""
    return PATH_KEYS.get((_BY_LOWER.get(name.lower()), column))
