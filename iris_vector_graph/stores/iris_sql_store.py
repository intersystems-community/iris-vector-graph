import json
import logging
import os
import warnings
from typing import Any, Callable, Dict, List, Optional

from iris_vector_graph._engine.algorithms import DEFAULT_PPR_TOLERANCE
from iris_vector_graph._engine.temporal_only_warning import warn_empty_bfs
from iris_vector_graph import algo_cache, arno_readers
from iris_vector_graph._write_txn import is_duplicate, rollback
from iris_vector_graph.errors import GraphWriteError
from iris_vector_graph._validate import validate_graph_name
from iris_vector_graph.result import IVGResult
from iris_vector_graph.routing import graph_scope_predicate

from iris_vector_graph._dictionary import compiled_class_exists, compiled_method_exists
from iris_vector_graph.schema_name import DEFAULT_SCHEMA, guarded_connection
from iris_vector_graph.stores.arno_bridge import arno_disabled

logger = logging.getLogger(__name__)

_FULL_CAPABILITIES = {
    "native_sql": True,
    "bfs": True,
    "shortest_path": True,
    "weighted_shortest_path": True,
    "ppr": True,
    "pagerank": True,
    "wcc": True,
    "cdlp": True,
    "subgraph": True,
    "knn_vec": True,
    "temporal_edges": True,
    "temporal_window_query": True,
    "temporal_cypher": True,
    "temporal_aggregate": True,
    "degree_centrality": True,
    "betweenness": True,
    "closeness": True,
    "eigenvector": True,
    "leiden": True,
    "triangle_count": True,
    "scc": True,
    "k_core": True,
}

import re as _re_global

#: The refusal of a DELETE of a node that still has relationships. The TCK harness reads both phrases.
_DELETE_CONNECTED = (
    "ConstraintVerificationFailed: Cannot delete node with existing relationships. Use DETACH DELETE."
)


_ARNO_ABSENT_LOGGED = [False]


def _log_arno_absent_once() -> None:
    """The Arno library is optional and not distributed with IVG: said once per process, at DEBUG. Setup's summary
    line says it at INFO, and ``status().report()`` shows it (4.6.0 R207: setup logs one INFO line)."""
    if not _ARNO_ABSENT_LOGGED[0]:
        _ARNO_ABSENT_LOGGED[0] = True
        logger.debug("Arno library not loaded (optional, not distributed with IVG): algorithms run in ObjectScript")


_CACHE_READ_ONLY_LOGGED = [False]


def _log_cache_store_failure(exc: BaseException) -> None:
    """A cache write that failed. A user holding the database at R (ADMIN_GUIDE §3's reading role) cannot write
    ^IVG.AlgoCache, as documented, and every answer is still right: said once per process at INFO, not a WARNING
    per call (4.6.0 R202). Any other failure stays a WARNING."""
    if "<PROTECT>" in str(exc):
        if not _CACHE_READ_ONLY_LOGGED[0]:
            _CACHE_READ_ONLY_LOGGED[0] = True
            logger.info("Algorithm answers are not cached: this user may not write ^IVG.AlgoCache (%s); "
                        "the answers are unaffected", exc)
        return
    logger.warning("Algorithm cache store failed (%s); the answer is unaffected", exc)


def _fix_iris_json(raw3: str) -> str:
    return _re_global.sub(r"(?<=[:\[,])(\.\d)", r"0\1", raw3)


#: Sources the server's Brandes takes when the caller names no sample size
#: (``betweenness_centrality(sample_size=0)``): a Brandes-Pich approximation.
_BETWEENNESS_DEFAULT_SOURCES = 200


def _betweenness_sample_key(node_id: str):
    """Where a node falls in betweenness's sample order: the first 7 bytes of the SHA-1 of its UTF-8 bytes, big-endian,
    then its ID. Graph.KG.NKGAccelCentrality.BetweennessGlobalParallel orders the same way."""
    import hashlib

    return int.from_bytes(hashlib.sha1(node_id.encode("utf-8")).digest()[:7], "big"), node_id


def _betweenness_sources(nodes: list, sample_size: int) -> list:
    """The sources a betweenness run takes (4.6.0 R233): every node when sample_size is at or above their number, or 0
    on at most 200 nodes; otherwise sample_size (200 for 0) of them, the first by `_betweenness_sample_key`, a fixed
    pseudo-random sample, the server's. Before, the server took the first by ID and this walk a random.sample."""
    k = sample_size if sample_size > 0 else _BETWEENNESS_DEFAULT_SOURCES
    if k >= len(nodes):
        return list(nodes)
    return sorted(nodes, key=_betweenness_sample_key)[:k]


_CANONICAL_INT = _re_global.compile(r"-?(?:0|[1-9][0-9]*)")


def _collation_key(node_id) -> tuple:
    """Where ``node_id`` falls among IRIS subscripts: a canonical integer first, by value, then the rest as text. The
    ObjectScript tiers break a tie in ``^||rank(-score, id)`` this way."""
    s = str(node_id)
    return (0, int(s), "") if _CANONICAL_INT.fullmatch(s) else (1, 0, s)


def _ranked(scores, top_k: int) -> list:
    """``[[id, score], ...]`` from ``(id, score)`` pairs: highest score first, ties by id in IRIS collation (as the
    ObjectScript tiers), then the first ``top_k`` (0: all). A centrality walk's answer; node order is not id order
    (LazyKG yields sources, then sinks), and top_k cut before ranking kept nodes scoring below ones it dropped
    (4.6.0 R050)."""
    return _ranked_rows([[nid, sc] for nid, sc in scores], top_k)


def _ranked_rows(rows, top_k: int) -> list:
    """``rows`` (``[id, score, ...]``) highest score first, ties by id in IRIS collation, then the first ``top_k``
    (0: all). k_core and triangle_count on every route, and their procedures, rank this way (4.6.0 R050: ties went
    by Python string order, the procedures' by write order, so each ``topK`` cut kept other nodes)."""
    rows = sorted(rows, key=lambda r: (-r[1], _collation_key(r[0])))
    return rows[:top_k] if top_k > 0 else rows


#: Captured ids inlined per statement; a larger capture runs the statement per chunk.
_CAPTURED_ID_CHUNK = 500
_CAPTURE_PREFIX = "__capture_ids__ "
_IDS_TOKEN_RE = _re_global.compile(r"__IDS_(\w+?)__")
#: `__capture_pairs__ <key>\n<select id, other>`: a SET value read from another variable of the row (4.6.0 R187)
_PAIRS_PREFIX = "__capture_pairs__ "
_PAIRS_SUFFIX = "#pairs"
_PAIR_TOKEN_RE = _re_global.compile(r"__PAIR_(\w+?)__\[([^\]]+)\]")
_GRAPH_TOKEN_RE = _re_global.compile(r"graph_id = __GRAPH_(\w+?)__")


def _ids_literal(ids: list) -> str:
    """`'a', 'b'` for an IN list; `NULL` (matches nothing) for no ids."""
    if not ids:
        return "NULL"
    out = []
    for v in ids:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(str(v))
        else:
            out.append("'" + str(v).replace("'", "''") + "'")
    return ", ".join(out)


def _capture_rows(key: str, rows, captured: dict, captured_graphs: dict) -> None:
    """Record what a `__capture_ids__` select returned under `key`.

    One column is the ids. A graph-less node DELETE selects `graph_id` second, and
    its ids are also kept per graph, so each of its statements deletes the rows the
    MATCH bound and not every graph's row for the same id.
    """
    rows = [r for r in rows if r and r[0] is not None]
    captured[key] = list(dict.fromkeys(r[0] for r in rows))
    if rows and len(rows[0]) > 1:
        by_graph: dict = {}
        for r in rows:
            by_graph.setdefault(r[1], {})[r[0]] = None
        captured_graphs[key] = {g: list(ids) for g, ids in by_graph.items()}
    else:
        captured_graphs[key] = {}


def _graph_literal(graph) -> str:
    return "IS NULL" if graph is None else "= '" + str(graph).replace("'", "''") + "'"


def _expand_captured(stmt: str, captured: dict, captured_graphs: dict) -> list:
    """`_expand_captured_ids`, run once per graph for `graph_id = __GRAPH_<key>__`.

    The translator writes one graph token per statement, keyed like its ids. With
    nothing captured the statement still runs once and matches nothing.
    """
    keys = list(dict.fromkeys(_GRAPH_TOKEN_RE.findall(stmt)))
    if not keys:
        return _expand_captured_ids(stmt, captured)
    if len(keys) > 1:
        raise RuntimeError(f"one graph token per statement, got {keys}: {stmt[:200]}")
    key = keys[0]
    if key not in captured:
        raise RuntimeError(f"no ids captured under {key!r} before: {stmt[:200]}")
    groups = captured_graphs.get(key) or {}
    if not groups:
        return _expand_captured_ids(_GRAPH_TOKEN_RE.sub("graph_id = NULL", stmt), captured)
    out = []
    for graph, ids in groups.items():
        scoped = _GRAPH_TOKEN_RE.sub(lambda m, g=graph: "graph_id " + _graph_literal(g), stmt)
        out.extend(_expand_captured_ids(scoped, {**captured, key: ids}))
    return out


def _capture_pairs(key: str, rows, captured: dict) -> None:
    """Record what a `__capture_pairs__` select returned under `key`: its first column's ids (deduplicated, as
    `__IDS_<key>__` reads them) and, for each, the second column of its first row (`__PAIR_<key>__[col]`). A value a WITH
    projected is read through CY_TYPED and decoded to its type here (4.6.0 R285); an id carries no mark."""
    from iris_vector_graph.prop_values import decode_typed_text

    pairs: dict = {}
    for r in rows:
        if r and r[0] is not None and r[0] not in pairs:
            pairs[r[0]] = decode_typed_text(r[1]) if len(r) > 1 else None
    captured[key] = list(pairs)
    captured[key + _PAIRS_SUFFIX] = pairs


def _pair_kind(v) -> str:
    """The SQL type a pair's value is spelled at: one CASE holds one type (`_split_pair_kinds`)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "text"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float) or type(v).__name__ == "Decimal":
        return "float"
    return "text"


def _pair_value_sql(v) -> str:
    """A pair's second column in SQL: an id or a value a WITH projected (4.6.0 R242), typed as a Cypher SET of it stores
    it. An integer literal alone is stored as its text ('3'), and `2.0` as the integer 2."""
    kind = _pair_kind(v)
    if kind == "null":
        return "NULL"
    if kind == "float":
        return f"CAST('{float(v)!r}' AS DOUBLE)"
    if kind == "int":
        return f"CAST({int(v)} AS BIGINT)"
    return _ids_literal([v])


def _split_pair_kinds(stmt: str, captured: dict):
    """`stmt` once per type of the values a `__PAIR_<key>__[col]` reads, its ids those of that type; None for one type.

    A CASE takes one SQL type: integers and strings in one CASE stored the integer 3 as the text '3' (4.6.0 R242). A
    pair of node or edge ids has one type, and runs as it did.
    """
    keys = list(dict.fromkeys(m.group(1) for m in _PAIR_TOKEN_RE.finditer(stmt)))
    if len(keys) != 1 or keys[0] + _PAIRS_SUFFIX not in captured:
        return None
    key = keys[0]
    pairs = captured[key + _PAIRS_SUFFIX]
    groups: dict = {}
    for i in captured.get(key, []):
        groups.setdefault(_pair_kind(pairs.get(i)), []).append(i)
    if len(groups) < 2:
        return None
    return [{**captured, key: ids} for ids in groups.values()]


def _expand_pairs(stmt: str, captured: dict, ids_for) -> str:
    """`stmt` with every `__PAIR_<key>__[col]` replaced by `CASE col WHEN <id> THEN <pair> ... END` over the ids
    `ids_for(key)` names (the statement's chunk of them); NULL when there are none."""

    def _case(m):
        key, col = m.group(1), m.group(2)
        pairs = captured.get(key + _PAIRS_SUFFIX)
        if pairs is None:
            raise RuntimeError(f"no pairs captured under {key!r} before: {stmt[:200]}")
        whens = [f"WHEN {_ids_literal([i])} THEN {_pair_value_sql(pairs[i])}" for i in ids_for(key)]
        return f"(CASE {col} {' '.join(whens)} END)" if whens else "NULL"

    return _PAIR_TOKEN_RE.sub(_case, stmt) if "__PAIR_" in stmt else stmt


def _expand_captured_ids(stmt: str, captured: dict) -> list:
    """`stmt` with every `__IDS_<key>__` replaced by the ids captured under key.

    A DELETE clause captures the ids it touches once, before it writes anything
    (`__capture_ids__ <key>\\n<select>`); its later statements read the capture,
    so deleting a node's labels cannot hide the node from the statement that
    deletes its row. Returns one statement per chunk of ids: with several keys,
    each key is chunked in turn with the other keys' lists empty, which covers
    every id exactly once for the OR-of-IN-lists shapes the translator writes.
    """
    keys = list(dict.fromkeys(_IDS_TOKEN_RE.findall(stmt)))
    if not keys:
        return [stmt]
    for k in keys:
        if k not in captured:
            raise RuntimeError(f"no ids captured under {k!r} before: {stmt[:200]}")
    by_kind = _split_pair_kinds(stmt, captured)
    if by_kind is not None:
        return [s for part in by_kind for s in _expand_captured_ids(stmt, part)]
    if all(len(captured[k]) <= _CAPTURED_ID_CHUNK for k in keys):
        stmt = _expand_pairs(stmt, captured, lambda k: captured[k])
        return [_IDS_TOKEN_RE.sub(lambda m: _ids_literal(captured[m.group(1)]), stmt)]
    out = []
    for key in keys:
        ids = captured[key]
        for i in range(0, max(len(ids), 1), _CAPTURED_ID_CHUNK):
            chunk = ids[i : i + _CAPTURED_ID_CHUNK]
            chunked = _expand_pairs(stmt, captured, lambda k, c=chunk, key=key: c if k == key else [])
            out.append(
                _IDS_TOKEN_RE.sub(
                    lambda m, c=chunk, k=key: _ids_literal(c if m.group(1) == k else []),
                    chunked,
                )
            )
    return out


def _json_number(v):
    from decimal import Decimal

    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _column_sql_type(values: list) -> str:
    """The JSON_TABLE type that reads `values` back as the driver returned them."""
    nums = [_json_number(v) for v in values if v is not None]
    if nums and all(isinstance(v, int) and not isinstance(v, bool) for v in nums):
        return "INTEGER"
    if nums and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nums):
        return "DOUBLE"
    return "VARCHAR(32000)"


def _pinned_stage_sql(colnames: list, rows: list) -> str:
    """The body of `<stage> AS (…)` that reads back the rows `__pin_stage__ <stage>` read (4.6.0 R208).

    The stage was read before the writes that change what its MATCH selects; every later statement reads these rows
    instead of running it again. No rows: a stage of the same columns that selects nothing.
    """
    if not rows:
        return "SELECT " + ", ".join(f'CAST(NULL AS VARCHAR(1)) AS "{c}"' for c in colnames) + " WHERE 1 = 0"
    doc = json.dumps([{f"c{i}": _json_number(r[i]) for i in range(len(colnames))} for r in rows], default=str)
    columns = ", ".join(
        f"c{i} {_column_sql_type([r[i] for r in rows])} PATH '$.c{i}'" for i in range(len(colnames))
    )
    select = ", ".join(f's.c{i} AS "{c}"' for i, c in enumerate(colnames))
    return f"SELECT {select} FROM JSON_TABLE('" + doc.replace("'", "''") + f"', '$[*]' COLUMNS({columns})) s"


_PIN_STAGE_PREFIX = "__pin_stage__ "
#: `__IDSALL_<key>__`: every id captured under key in one IN list, for a statement that must run once (a RETURN; R211)
_IDS_ALL_RE = _re_global.compile(r"__IDSALL_(\w+?)__")


def _expand_all_ids(stmt, captured: dict):
    """`stmt` with every `__IDSALL_<key>__` replaced by all the ids captured under key, unchunked."""
    if not isinstance(stmt, str) or "__IDSALL_" not in stmt:
        return stmt

    def _ids(m):
        if m.group(1) not in captured:
            raise RuntimeError(f"no ids captured under {m.group(1)!r} before: {stmt[:200]}")
        return _ids_literal(captured[m.group(1)])

    return _IDS_ALL_RE.sub(_ids, stmt)


_PINNED_RE = _re_global.compile(r"(\w+) AS \(__PINNED_(\w+)__\)")


def _fill_pinned(stmt, pinned: dict):
    """`stmt` with every `<stage> AS (__PINNED_<stage>__)` reading the rows its pin read."""
    if not isinstance(stmt, str) or "__PINNED_" not in stmt:
        return stmt

    def _body(m):
        if m.group(2) not in pinned:
            raise RuntimeError(f"stage {m.group(2)!r} read before it was pinned: {stmt[:200]}")
        return f"{m.group(1)} AS ({_pinned_stage_sql(*pinned[m.group(2)])})"

    return _PINNED_RE.sub(_body, stmt)


def _decode_snapshot_typed(colnames: list, rows: list) -> tuple:
    """A stage snapshot's columns and rows with each `__typed_<col>` (the column through CY_TYPED) decoded into `<col>`
    and dropped (4.6.0 R285): the column as fetched is a number under driver 5.4.0 and IRIS's text under 5.4.1."""
    from iris_vector_graph.prop_values import SNAPSHOT_TYPED, decode_typed_text

    lower = [c.lower() for c in colnames]
    pairs = []
    for t, name in enumerate(lower):
        if name.startswith(SNAPSHOT_TYPED) and name[len(SNAPSHOT_TYPED):] in lower:
            pairs.append((lower.index(name[len(SNAPSHOT_TYPED):]), t))
    if not pairs:
        return colnames, rows
    drop = {t for _, t in pairs}
    out = []
    for r in rows:
        r = list(r)
        for base, t in pairs:
            r[base] = decode_typed_text(r[t])
        out.append([v for i, v in enumerate(r) if i not in drop])
    return [c for i, c in enumerate(colnames) if i not in drop], out


def _stage_snapshot_sql(stage: str, cols: list, colnames: list, rows: list) -> str:
    """The body of `{stage} AS (…)` that reads `cols` from rows captured before the DML.

    The other columns stay live (`{stage}__live`). The captured rows line up with
    the live ones on the stage's scalar columns (node ids), or trivially when the
    stage has one row; when they cannot line up, the stage stays live. A column
    holding node JSON stays live too: a node shows its post-SET properties.
    """
    from decimal import Decimal

    live = f"SELECT * FROM {stage}__live"
    idx = {c.lower(): i for i, c in enumerate(colnames)}

    def _is_json(v):
        return isinstance(v, str) and v[:1] in ("{", "[")

    def _is_node_json(v):
        return isinstance(v, str) and (v.startswith('{"_id"') or v.startswith('[{"_id"'))

    snap = [
        idx[c.lower()]
        for c in cols
        if c.lower() in idx and not any(_is_node_json(r[idx[c.lower()]]) for r in rows)
    ]
    if not snap:
        return live
    keys = [
        i
        for i in range(len(colnames))
        if i not in snap and rows and all(r[i] is not None and not _is_json(r[i]) for r in rows)
    ]
    if len(rows) > 1 and (not keys or len({tuple(r[i] for i in keys) for r in rows}) < len(rows)):
        return live

    def _jv(v):
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(v)
        return v

    def _sql_type(i):
        vals = [r[i] for r in rows if r[i] is not None]
        if vals and all(isinstance(v, (int, Decimal)) and not isinstance(v, bool) for v in vals):
            if all(_jv(v) == int(v) for v in vals):
                return "INTEGER"
        if vals and all(isinstance(v, (int, float, Decimal)) for v in vals):
            return "DOUBLE"
        return "VARCHAR(32000)"

    used = snap + keys
    doc = json.dumps([{f"k{i}": _jv(r[i]) for i in used} for r in rows], default=str)
    columns = ", ".join(f'"{colnames[i]}" {_sql_type(i)} PATH \'$.k{i}\'' for i in used)
    select = ", ".join(
        f'{"s" if i in snap else "l"}."{c}" AS "{c}"' for i, c in enumerate(colnames)
    )
    sql = (
        f"SELECT {select} FROM {stage}__live l CROSS JOIN JSON_TABLE("
        + "'"
        + doc.replace("'", "''")
        + "'"
        + f", '$[*]' COLUMNS({columns})) s"
    )
    if len(rows) > 1:
        sql += " WHERE " + " AND ".join(f'l."{colnames[i]}" = s."{colnames[i]}"' for i in keys)
    return sql


# ── BFS strategy protocol (spec-arch-4, internal seam) ─────────────────────
# Three adapters exist for BFS: Arno/NKG (Rust), ObjectScript, and SQL fallback.
# A formal internal seam makes each independently testable and adding a fourth
# path safe. The protocol is NOT exported — callers see only execute_bfs.
try:
    from typing import Protocol, runtime_checkable as _runtime_checkable

    @_runtime_checkable
    class _BfsStrategy(Protocol):
        def run(
            self,
            source_id: str,
            predicates: list,
            max_hops: int,
            direction: str,
            max_results: int,
            graph: Optional[str] = None,
        ) -> "IVGResult": ...

except Exception:
    # Fallback for Python < 3.8 (should not occur in practice)
    class _BfsStrategy:  # type: ignore[no-redef]
        pass


# Spec 236 FR-015: `kg_bfs_compute` is deliberately not adopted here. Adoption needs arno's
# `test_neg_int_keys` result (it answered `[]` on the probed build) and a separate decision.
class _ArnoBfsAdapter:
    """Arno/NKG Rust-accelerated BFS adapter.

    Reads ^NKG, whose entry point (Graph.KG.NKGAccel.BFSJson) takes neither a graph
    nor a direction, so _select_bfs_strategy only ever hands this adapter the default
    graph and an outbound traversal. Both arguments exist to satisfy the strategy
    protocol, and `direction` is accepted only so the ObjectScript fallback inside
    _run_arno_bfs can pass it on (spec 230, FR-031).
    """
    __slots__ = ("_s",)

    def __init__(self, store): self._s = store

    def run(self, source_id, predicates, max_hops, direction, max_results, graph=None):
        return self._s._run_arno_bfs(source_id, predicates, max_hops, direction, max_results)


class _ObjectScriptBfsAdapter:
    """ObjectScript BFSFastJsonSorted adapter with SQL fallback."""
    __slots__ = ("_s",)

    def __init__(self, store): self._s = store

    def run(self, source_id, predicates, max_hops, direction, max_results, graph=None):
        return self._s._run_objectscript_bfs(
            source_id, predicates, max_hops, direction, max_results, graph=graph
        )


#: What the two producers of a scored-node list call the node. `Graph.KG.PageRank`
#: says `id`; the Rust callout behind `Graph.KG.ArnoAccel` says `node`. Reading only
#: `id` gave every Arno row a blank id and a correct score, which no caller can use
#: and nothing reported as an error.
_NODE_KEYS = ("id", "node")


class UnreadableResultShape(Exception):
    """An accelerated or ObjectScript answer whose keys this reader does not know.

    Raised rather than worked around (spec 230, FR-017). The three readers below each
    used to coerce whatever they were handed into their declared columns, so a shape
    they did not recognise became rows nobody could question: a blank node ID with a
    real score, a summary's own keys as node IDs, an edge with no predicate. An
    exception is the only outcome a caller can act on, and the message names the keys
    that were actually present so the next shape can be added deliberately.
    """


class AcceleratedSummaryShape(UnreadableResultShape):
    """The accelerator answered a different question than the columns promise.

    `ArnoAccel.WCCJson` returns `{"components": 1, "largest": [...]}` — a count and
    the biggest few — where `Graph.KG.Algorithms.WCCJson` returns the node→component
    map the `id, component_id` columns describe. Same for CDLP, and `SubgraphJson`
    returns edges as `{"src", "dst", "type": "REL"}`, which has lost the predicate.

    A subclass, because this one is recoverable: the ObjectScript owner answers the
    promised question, so the reader falls back to it. Callers that only care that a
    shape could not be read catch `UnreadableResultShape` and get both.
    """


#: The keys that identify an accelerated summary rather than a per-node answer.
_SUMMARY_KEYS = ("components", "communities", "largest")


def _scored_node_rows(results) -> list:
    """`[{"id"|"node": ..., "score": ...}, ...]` → `[[node_id, score], ...]`.

    A row naming neither key raises: dropping it lost a score silently, and the only
    symptom was a result set shorter than the graph — which is indistinguishable
    from a graph with fewer scored nodes.
    """
    rows = []
    for r in results:
        if not isinstance(r, dict):
            raise UnreadableResultShape(
                f"a scored row is not a mapping: {r!r}"
            )
        node_id = next((r[k] for k in _NODE_KEYS if r.get(k)), None)
        if node_id is None:
            raise UnreadableResultShape(
                "a scored row names no node under "
                f"{' or '.join(_NODE_KEYS)}; its keys are {sorted(r)}"
            )
        rows.append([node_id, float(r.get("score", 0))])
    return rows


def _membership_rows(results, value_key: str) -> list:
    """A node→group answer → `[[node_id, group], ...]`.

    Two readable shapes, because the two producers disagree: `Graph.KG.Algorithms`
    answers with a map, and a row list naming the node under either spelling is
    equally readable. Anything else raises — including the accelerated summary,
    whose own keys (`components`, `largest`) would otherwise be emitted as node IDs.
    """
    if isinstance(results, dict):
        present = [k for k in _SUMMARY_KEYS if k in results]
        if present:
            raise AcceleratedSummaryShape(
                f"a summary, not a node→{value_key} map: it carries {present}"
            )
        return [[k, v] for k, v in results.items()]
    if isinstance(results, list):
        rows = []
        for r in results:
            if not isinstance(r, dict):
                raise UnreadableResultShape(
                    f"a membership row is not a mapping: {r!r}"
                )
            node_id = next((r[k] for k in _NODE_KEYS if r.get(k)), None)
            if node_id is None:
                raise UnreadableResultShape(
                    "a membership row names no node under "
                    f"{' or '.join(_NODE_KEYS)}; its keys are {sorted(r)}"
                )
            rows.append([node_id, r.get(value_key, 0)])
        return rows
    raise UnreadableResultShape(
        f"a node→{value_key} answer is neither a map nor a row list: {results!r}"
    )


def _subgraph_payload(result) -> tuple:
    """A subgraph answer → `(nodes, edges, properties, labels)`.

    The edges decide it. `Graph.KG.Subgraph` returns `{"s", "p", "o"}` triples, which
    is what a caller needs to know how two nodes are related; `ArnoAccel` returns
    `{"src", "dst", "type": "REL"}`, where `"REL"` is a placeholder and not a
    predicate. Reading the accelerated shape as the promised one would have had to
    invent `p`, so it raises and the caller falls back to the owner.
    """
    if not isinstance(result, dict):
        raise UnreadableResultShape(f"a subgraph answer is not a mapping: {result!r}")

    edges = result.get("edges") or []
    if not isinstance(edges, list):
        raise UnreadableResultShape(f"a subgraph's edges are not a list: {edges!r}")
    for edge in edges:
        if not isinstance(edge, dict):
            raise UnreadableResultShape(f"a subgraph edge is not a mapping: {edge!r}")
        if "s" in edge and "p" in edge and ("o" in edge or "o_id" in edge):
            continue
        if "src" in edge and "dst" in edge:
            raise AcceleratedSummaryShape(
                "a subgraph edge carries no predicate: its keys are "
                f"{sorted(edge)}, and 'type' holds a placeholder"
            )
        raise UnreadableResultShape(
            f"a subgraph edge names no (s, p, o) triple; its keys are {sorted(edge)}"
        )

    if "properties" not in result and "labels" not in result:
        # An edgeless accelerated answer looks readable above, and this is the other
        # thing it is missing. The owner's answer always carries both keys, even when
        # they are empty.
        raise AcceleratedSummaryShape(
            "a subgraph answer carries neither properties nor labels; its keys are "
            f"{sorted(result)}"
        )

    return (
        result.get("nodes", []),
        edges,
        result.get("properties", {}),
        result.get("labels", {}),
    )


class _SqlBfsFallbackAdapter:
    """Pure-SQL BFS adapter (fallback when ObjectScript unavailable)."""
    __slots__ = ("_s",)

    def __init__(self, store): self._s = store

    def run(self, source_id, predicates, max_hops, direction, max_results, graph=None):
        return self._s._sql_bfs_fallback(
            source_id, predicates, max_hops, direction, max_results, graph=graph
        )


def _skip_duplicate(cursor, sql: str, params: list) -> None:
    """Run one INSERT; a duplicate (-119, unique) is skipped, anything else raises."""
    try:
        cursor.execute(sql, params)
    except Exception as e:
        if not is_duplicate(e):
            raise


class IRISGraphStore:
    def __init__(self, conn, namespace: Optional[str] = None, schema: Optional[str] = None):
        # The engine's first-access check (spec 262, FR-006), when its namespace refused it at construction: shared,
        # so a store method called directly is refused as the engine's own are.
        self._schema_guard = None
        self.conn = conn
        # The IVG Schema this store's SQL is qualified with: its engine's, else Graph_KG (spec 262). Per store, never
        # read from the process, so two engines on two schemas keep their own.
        self._schema = schema or DEFAULT_SCHEMA
        # The namespace the caller named, if any. The store works in the connection's, which _check_namespace
        # reads (4.6.0, NR11); until then the named one, else USER.
        self._namespace_arg = namespace
        self._namespace = namespace or "USER"
        self._namespace_checked: bool = False
        self._arno_available: Optional[bool] = None
        self._arno_capabilities: Dict[str, Any] = {}
        self._arno_reader_report: Optional[Dict[str, int]] = None
        self._arno_proof: Optional[arno_readers.Proof] = None

    conn = guarded_connection()

    @property
    def _ivg_schema(self) -> str:
        return getattr(self, "_schema", None) or DEFAULT_SCHEMA  # a store made without __init__ (tests): Graph_KG

    #: (graph_id, model_key) -> (qualified table, vector dtype), or (None, None) for a pair with no route. The engine
    #: that owns the store sets it to its own route reader (spec 227), which knows the engine's model identity;
    #: execute_knn_vec reads the legacy table only while it is unset (4.6.0, R073).
    knn_route: Optional[Callable[[str, Optional[str]], tuple]] = None

    # ── Namespace awareness (spec 212) ────────────────────────────────────────

    def _connection_namespace(self) -> Optional[str]:
        """The namespace the connection's server process is in: %SYSTEM.SYS.NameSpace(), else `conn.namespace`."""
        try:
            ns = self._iris_obj().classMethodValue("%SYSTEM.SYS", "NameSpace")
        except Exception as exc:
            logger.debug("%%SYSTEM.SYS.NameSpace() unavailable (%s); reading conn.namespace", exc)
            ns = getattr(self.conn, "namespace", None)
        return ns if isinstance(ns, str) and ns else None

    def _check_namespace(self) -> None:
        """Take the namespace from the connection, once per store (cached via _namespace_checked).

        The store works in the connection's namespace: the classes, tables and globals are there. A `namespace=`
        that names another one is a mistake to fix, and is a WARNING (an exception's warning with
        IVG_STRICT_NAMESPACE=1; IVG_IGNORE_NAMESPACE_CHECK=1 silences it). Before 4.6.0 the default
        `namespace="USER"` warned "Namespace mismatch" in every other namespace (NR11), and a probe of
        `SELECT $Data(^KG("out"))`, which IRIS SQL rejects (SQLCODE -12), never ran.
        """
        from iris_vector_graph.exceptions import NamespaceMismatchWarning

        if getattr(self, "_namespace_checked", False):
            return
        if not hasattr(self, "_namespace"):
            # Store created without __init__ (e.g. __new__ in tests) — skip probe
            return

        self._namespace_checked = True
        conn_ns = self._connection_namespace()
        if conn_ns is None:
            return
        named = getattr(self, "_namespace_arg", None)
        self._namespace = conn_ns
        if named and named.upper() != conn_ns.upper():
            if os.environ.get("IVG_IGNORE_NAMESPACE_CHECK", "").strip() == "1":
                return
            w = NamespaceMismatchWarning(
                conn_ns,
                named,
                hint=f"IVG works in the connection's namespace, '{conn_ns}': drop namespace= or connect to '{named}'.",
            )
            logger.warning(str(w))
            if os.environ.get("IVG_STRICT_NAMESPACE", "").strip() == "1":
                warnings.warn(w, stacklevel=2)

    # ── Internal helpers (moved from engine) ─────────────────────────────────

    def _iris_obj(self):
        import iris as _iris

        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedIRIS

        if isinstance(self.conn, EmbeddedConnection):
            return EmbeddedIRIS()
        return _iris.createIRIS(self.conn)

    def _named_graph_with_edges(self) -> Optional[str]:
        """A graph other than the default one with an edge in ``^KG("out")``, or None: when there is one, ``^NKG``
        holds its edges merged with the default graph's, and the ``^NKG`` tiers below are skipped (4.6.0 R184)."""
        from iris_vector_graph.stores.lazy_kg import named_graph_with_edges

        return named_graph_with_edges(self.conn)

    def _reload_arno_if_needed(self, iris_obj) -> bool:
        """Reload libarno_callout.so if IsAvailable() returns false.

        The path is ``IVG_ARNO_LIB`` when set, else the server resolves it (``Graph.KG.ArnoAccel.LibPath``: the
        ``^ArnoAccel("lib_path")`` the last load recorded, else the default), one order for both loaders (R132).
        Returns True if Arno is available after the attempt.
        """
        from iris_vector_graph.stores.arno_bridge import loader_args

        if bool(iris_obj.classMethodValue("Graph.KG.ArnoAccel", "IsAvailable")):
            return True
        # A (re)load may bring a different build: its reader report is read again.
        self._arno_reader_report = None
        return bool(iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", *loader_args()))

    # ── Native Reader routing (spec 236) ─────────────────────────────────────

    def _arno_readers(self) -> Dict[str, int]:
        """The loaded library's reader revisions; read once, again after a reload."""
        report = getattr(self, "_arno_reader_report", None)
        if report is None:
            report = arno_readers.read_reader_report(self)
            self._arno_reader_report = report
        return report

    def _arno_route(self, algorithm: str, graph: Optional[str] = "") -> str:
        """``"native"`` only when the library reports a revision the proof file accepts."""
        if not self._detect_arno():
            return arno_readers.OWNER
        # The library's readers walk USER's ^KG whatever namespace the caller is in
        # (measured on 1.1.0, spec 236), so any other namespace is the Owner's.
        if str(getattr(self, "_namespace", "USER")).upper() != "USER":
            return arno_readers.OWNER
        proof = getattr(self, "_arno_proof", None)
        if proof is None:
            proof = arno_readers.default_proof()
        return arno_readers.route(algorithm, self._arno_readers(), proof, graph)

    def _native_answer(self, algorithm: str, call: Callable[[], str], graph: Optional[str] = "") -> Optional[str]:
        """Text from the Native Reader, or ``None`` when the Owner should answer.

        ``None`` covers every reason to leave the reader: routing says Owner, the call
        raised, or it came back empty. The failure is logged; the caller never sees it.
        """
        if self._arno_route(algorithm, graph) != arno_readers.NATIVE:
            return None
        try:
            raw = call()
        except Exception as exc:  # noqa: BLE001 - the Owner answers instead
            logger.warning("Arno %s Native Reader failed (%s); the Owner answers", algorithm, exc)
            return None
        return raw or None

    # ── Shared result cache (spec 236, Story 4) ───────────────────────────────

    @staticmethod
    def _remember_large(memo: Dict[Any, Any], key: tuple, token: int, rows: list) -> None:
        """Keep a private copy of a large result, newest last, at most FRONT_MAX_ENTRIES."""
        memo.pop(key, None)
        memo[key] = (token, [list(r) for r in rows])
        while len(memo) > algo_cache.FRONT_MAX_ENTRIES:
            memo.pop(next(iter(memo)))

    def _cached_algorithm(
        self,
        algorithm: str,
        graph: Optional[str],
        params: Dict[str, Any],
        columns: List[str],
        compute: Callable[[], IVGResult],
    ) -> IVGResult:
        """``compute()``, or the stored answer when ``^KG("__version")`` has not moved.

        One round trip reads the token and the entry (``AlgoCache.Lookup``); on a miss
        the token read there goes back with the answer, so a write that lands during the
        computation leaves the entry stale rather than wrongly fresh. Any failure of the
        cache itself turns it off for this store and computes: the cache is an
        optimisation, never a dependency.
        """
        stats = self.__dict__.setdefault("_algo_cache_stats", {})
        if (
            getattr(self, "_algo_cache_off", False)
            or not algo_cache.enabled()
            or algorithm not in algo_cache.CACHEABLE
        ):
            stats["bypass"] = stats.get("bypass", 0) + 1
            return compute()
        gname = algo_cache.graph_name(graph)
        phash = algo_cache.param_hash(params)
        key = (gname, algorithm, phash)
        memo = self.__dict__.setdefault("_algo_cache_memo", {})
        try:
            front = memo.get(key)
            if front is not None:
                # A large result is held here, but never trusted on its own: the token
                # is re-read on every use, so this can only ever return what the shared
                # cache would have returned.
                if int(self._call_classmethod("Graph.KG.AlgoCache", "Token")) == front[0]:
                    memo[key] = memo.pop(key)  # most recently used last
                    stats["front_hit"] = stats.get("front_hit", 0) + 1
                    return IVGResult(columns=list(columns), rows=[list(r) for r in front[1]])
                memo.pop(key, None)
            found = str(self._call_classmethod("Graph.KG.AlgoCache", "Lookup", gname, algorithm, phash))
            if found.startswith("H"):
                head, _, body = found[1:].partition("|")
                rows = json.loads(body)
                stats["hit"] = stats.get("hit", 0) + 1
                if len(body) >= algo_cache.FRONT_MIN_CHARS:
                    self._remember_large(memo, key, int(head), rows)
                return IVGResult(columns=list(columns), rows=rows)
            token = int(found[1:] or 0)
        except Exception as exc:  # noqa: BLE001 - the cache must never break an algorithm
            logger.warning("Algorithm cache unavailable (%s); computing without it", exc)
            self._algo_cache_off = True
            return compute()
        stats["miss"] = stats.get("miss", 0) + 1
        result = compute()
        if result.error is None:
            payload = json.dumps(result.rows)
            if algo_cache.cacheable(algorithm, payload):
                try:
                    self._call_classmethod(
                        "Graph.KG.AlgoCache", "Put", gname, algorithm, phash, str(token), payload
                    )
                    stats["put"] = stats.get("put", 0) + 1
                    if len(payload) >= algo_cache.FRONT_MIN_CHARS:
                        self._remember_large(memo, key, token, result.rows)
                except Exception as exc:  # noqa: BLE001
                    _log_cache_store_failure(exc)
                    self._algo_cache_off = True
        return result

    def _detect_arno(self) -> bool:
        if self._arno_available is not None:
            return self._arno_available
        self._check_namespace()
        if arno_disabled():
            self._arno_available = False
            return False
        try:
            iris_obj = self._iris_obj()
            # Asked in SQL: %SYSTEM.OBJ.Exists is not callable through the Native API on all builds (spec
            # 213 finding) and a %-method is not safe from every host (iris_vector_graph/_dictionary.py).
            if not compiled_class_exists(self.conn, "Graph.KG.ArnoAccel", native=iris_obj):
                # Before initialize_schema() deploys it, which asks again (forget_arno)
                logger.debug(
                    "Arno class not found in namespace %s — Arno unavailable",
                    getattr(self, "_namespace", "USER"),
                )
                self._arno_available = False
                return False
            from iris_vector_graph.stores.arno_bridge import loader_args

            # R132: both loaders get IVG_ARNO_LIB, or resolve the same path on the server when it is unset
            load_args = loader_args()
            try:
                iris_obj.classMethodValue("Graph.KG.ArnoAccel", "Load", *load_args)
            except Exception:
                pass
            try:
                iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", *load_args)
            except Exception:
                pass
            self._reload_arno_if_needed(iris_obj)
            cap_json = iris_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities")
            self._arno_capabilities = json.loads(str(cap_json))
            if not self._arno_capabilities.get("rust_callout") and not iris_obj.classMethodValue(
                "Graph.KG.NKGAccel", "IsLoaded"
            ):
                # The classes are deployed and the library is not loadable here: the optional part, not distributed
                # with IVG. Every algorithm runs in ObjectScript (NR6).
                _log_arno_absent_once()
                self._arno_available = False
                self._arno_capabilities = {}
                return False
            # spec 213 finding: the class can be compiled and Capabilities can report
            # rust_callout=true while the .so is not loadable from this server process
            # (<DYNAMIC LIBRARY LOAD>). Smoke the callout once; if it cannot run, leave
            # Arno disabled so sync()/BFS use the ObjectScript path instead of half-failing.
            #
            # The seed has to be a node ^NKG actually holds. BFSJson answers an absent
            # seed with a value %DynamicArray.%FromJSON cannot parse — "<THROW>
            # *%Exception.General Parsing error 3 Line 1 Offset 1" — so probing with a
            # made-up ID threw on every install and disabled a working library.
            # GetFirstNKGNode is the seed the benchmark harness already uses; when it
            # comes back empty there is no node to ask about, and an unpopulated ^NKG
            # is reported on its own terms by nkg_data below rather than as a broken
            # callout. Capabilities has already reached the library by then in any
            # case: rust_callout comes from ArnoAccel.IsAvailable, which calls the
            # .so's version function through $ZF(-5).
            probe_seed = str(
                iris_obj.classMethodValue("Graph.KG.NKGAccel", "GetFirstNKGNode") or ""
            )
            if probe_seed:
                try:
                    iris_obj.classMethodValue(
                        "Graph.KG.NKGAccel", "BFSJson", probe_seed, "[]", 1, 1
                    )
                except Exception as probe_err:
                    logger.warning("Arno callout not runnable in this process (%s) — Arno disabled", probe_err)
                    self._arno_available = False
                    self._arno_capabilities = {}
                    return False
            self._arno_available = True
            if not self._arno_capabilities.get("nkg_data", False):
                logger.info("Arno loaded; ^NKG is empty until data arrives (engine.rebuild_nkg() builds it)")
        except Exception:
            self._arno_available = False
            self._arno_capabilities = {}
        return self._arno_available

    def _arno_call(self, cls: str, method: str, *args) -> str:
        from iris_vector_graph.stores.arno_bridge import ArnoError as _ArnoError

        iris_obj = self._iris_obj()
        if not self._reload_arno_if_needed(iris_obj):
            raise _ArnoError("arno not loaded and reload failed")
        raw = str(iris_obj.classMethodValue(cls, method, *args))
        if not raw.startswith("CHUNKED:"):
            return raw
        _, tag, n_str = raw.split(":", 2)
        n = int(n_str)
        return "".join(
            str(iris_obj.classMethodValue(cls, "ReadLargeOutChunk", tag, i))
            for i in range(1, n + 1)
        )

    def _call_classmethod(self, cls: str, method: str, *args):
        self._check_namespace()
        iris_obj = self._iris_obj()
        return iris_obj.classMethodValue(cls, method, *args)

    def _kg_PERSONALIZED_PAGERANK_python_fallback(self, seed_ids, damping=0.85, max_iterations=20):
        return []

    # `graph` is accepted and ignored here on purpose: these are the stub overrides
    # for a store that answers KNN through ObjectScript, and they return nothing at
    # all. Rejecting the keyword would turn a scoped call into a TypeError only on
    # the fallback path — the path nobody exercises until something else broke.
    def _kg_KNN_VEC_python_optimized(
        self, query_vector, k, label_filter=None, *, graph=None, model_key=None
    ):
        return IVGResult(columns=["id", "score"], rows=[])

    def _kg_KNN_VEC_client_side(
        self, query_vector, k, label_filter=None, *, graph=None
    ):
        return IVGResult(columns=["id", "score"], rows=[])

    def _khop_fallback(self, seed, hops, max_nodes):
        return {"nodes": [], "edges": []}

    # ── Node & Edge Reads ─────────────────────────────────────────────────────

    #: IRIS refuses a statement with about 1,000 bound parameters (SQLCODE -1 at
    #: `:%qpar(1000`), so an `IN (...)` list is read in chunks of this size.
    _IN_CHUNK = 500

    def get_nodes(self, node_ids: list, properties: Optional[list] = None, graph: Optional[str] = None) -> IVGResult:
        """Labels and the named properties of node_ids; graph=None reads them across graphs, a name reads that graph's."""
        if not node_ids:
            return IVGResult(columns=["id", "labels"], rows=[])
        scope, scope_args = ("", []) if graph is None else (" AND COALESCE(graph_id, '') = COALESCE(?, '')", [graph])
        cursor = self.conn.cursor()
        labels_by_id: Dict[str, List[str]] = {}
        props_by_id: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(node_ids), self._IN_CHUNK):
            chunk = node_ids[start : start + self._IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"SELECT s, label FROM {self._ivg_schema}.rdf_labels WHERE s IN ({placeholders}){scope}",
                chunk + scope_args,
            )
            for row in cursor.fetchall():
                labels_by_id.setdefault(row[0], []).append(row[1])

            if properties:
                cursor.execute(
                    f'SELECT s, "key", val FROM {self._ivg_schema}.rdf_props WHERE s IN ({placeholders}){scope}',
                    chunk + scope_args,
                )
                for row in cursor.fetchall():
                    nid, key, val = row[0], row[1], row[2]
                    if key in properties:
                        try:
                            parsed = json.loads(val) if val else None
                        except (json.JSONDecodeError, TypeError):
                            parsed = val
                        props_by_id.setdefault(nid, {})[key] = parsed

        cols = ["id", "labels"] + (properties or [])
        rows = []
        for nid in node_ids:
            labels_json = json.dumps(labels_by_id.get(nid, []))
            props = props_by_id.get(nid, {})
            row = [nid, labels_json] + [props.get(p) for p in (properties or [])]
            rows.append(row)
        return IVGResult(columns=cols, rows=rows)

    def get_node_labels(self, node_ids: list) -> IVGResult:
        if not node_ids:
            return IVGResult(columns=["id", "labels"], rows=[])
        result = self.get_nodes(node_ids, properties=[])
        return IVGResult(columns=["id", "labels"], rows=[[r[0], r[1]] for r in result.rows])

    def query_nodes(
        self,
        label_filter: Optional[str] = None,
        property_filters: Optional[dict] = None,
        return_properties: Optional[list] = None,
        limit: int = 0,
    ) -> IVGResult:
        cursor = self.conn.cursor()
        if label_filter:
            cursor.execute(
                f"SELECT DISTINCT s FROM {self._ivg_schema}.rdf_labels WHERE label = ?",
                [label_filter],
            )
            node_ids = [r[0] for r in cursor.fetchall()]
        else:
            # IRIS Python driver (ARM64) segfaults on FETCH FIRST ? ROWS ONLY with
            # a parameterized placeholder. Inline as literal integer.
            fetch_clause = f" FETCH FIRST {int(limit)} ROWS ONLY" if limit else ""
            cursor.execute(f"SELECT node_id FROM {self._ivg_schema}.nodes{fetch_clause}")
            node_ids = [r[0] for r in cursor.fetchall()]

        if property_filters:
            filtered = []
            for nid in node_ids:
                match = True
                for key, val in property_filters.items():
                    cursor.execute(
                        f'SELECT val FROM {self._ivg_schema}.rdf_props WHERE s = ? AND "key" = ?',
                        [nid, key],
                    )
                    row = cursor.fetchone()
                    if row is None:
                        match = False
                        break
                    try:
                        stored = json.loads(row[0]) if row[0] else None
                    except (json.JSONDecodeError, TypeError):
                        stored = row[0]
                    if stored != val:
                        match = False
                        break
                if match:
                    filtered.append(nid)
            node_ids = filtered

        if limit and len(node_ids) > limit:
            node_ids = node_ids[:limit]

        return self.get_nodes(node_ids, return_properties)

    # ── Mutations ─────────────────────────────────────────────────────────────

    def write_nodes(self, nodes: list) -> IVGResult:
        """Write node rows, labels and properties in one transaction.

        A duplicate row is skipped. Any other failure rolls the whole call back and
        raises GraphWriteError; before 4.5.0 it was logged and the node counted as
        written, with the rows before it committed.
        """
        _guard = getattr(self, "_ledger_guard", None)
        if _guard is not None:
            _guard.check_structural_write("store.write_nodes")
        cursor = self.conn.cursor()
        written = 0
        cursor.execute("START TRANSACTION")
        try:
            for node in nodes:
                nid = node.get("id") or node.get("node_id")
                if not nid:
                    continue
                _skip_duplicate(cursor, f"INSERT INTO {self._ivg_schema}.nodes (node_id) VALUES (?)", [nid])
                for label in node.get("labels") or []:
                    _skip_duplicate(cursor, f"INSERT INTO {self._ivg_schema}.rdf_labels (s, label) VALUES (?, ?)", [nid, label])
                for key, val in (node.get("properties") or {}).items():
                    val_str = json.dumps(val) if not isinstance(val, str) else f'"{val}"'
                    _skip_duplicate(
                        cursor, f'INSERT INTO {self._ivg_schema}.rdf_props (s, "key", val) VALUES (?, ?, ?)', [nid, key, val_str]
                    )
                written += 1
            cursor.execute("COMMIT")
        except Exception as e:
            rollback(cursor)
            raise GraphWriteError("store.write_nodes", e) from e
        return IVGResult(columns=["written"], rows=[[written]])

    def write_edges(self, edges: list) -> IVGResult:
        """Write edge rows (default graph) and their endpoint node rows in one transaction.

        A duplicate row is skipped. Any other failure rolls the whole call back and
        raises GraphWriteError; before 4.5.0 it was logged and the edge counted as
        written, with the endpoint rows and earlier edges committed.
        """
        _guard = getattr(self, "_ledger_guard", None)
        if _guard is not None:
            _guard.check_structural_write("store.write_edges")
        cursor = self.conn.cursor()
        written = 0
        cursor.execute("START TRANSACTION")
        try:
            for edge in edges:
                s = edge.get("source") or edge.get("s")
                p = edge.get("predicate") or edge.get("p")
                o = edge.get("target") or edge.get("o")
                if not (s and p and o):
                    continue
                # rdf_edges' foreign keys are composite since spec 227 — (graph_id, s)
                # and (graph_id, o_id) against nodes (graph_id, node_id) — so the
                # endpoints have to exist in this edge's graph, which on this path is
                # always the default one.
                for endpoint in (s, o):
                    _skip_duplicate(
                        cursor,
                        f"INSERT INTO {self._ivg_schema}.nodes (node_id, graph_id) SELECT ?, '' "
                        f"WHERE NOT EXISTS (SELECT 1 FROM {self._ivg_schema}.nodes "
                        "WHERE node_id = ? AND COALESCE(graph_id, '') = '')",
                        [endpoint, endpoint],
                    )
                _skip_duplicate(
                    cursor, f"INSERT INTO {self._ivg_schema}.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, '')", [s, p, o]
                )
                try:
                    # a pre-4.0 side table no current schema creates: absent, the insert fails and is not an error
                    cursor.execute(
                        f"INSERT INTO {self._ivg_schema}.rdf_edges_adjout (s, p, o_id) VALUES (?, ?, ?)",
                        [s, p, o],
                    )
                except Exception:
                    pass
                written += 1
            cursor.execute("COMMIT")
        except Exception as e:
            rollback(cursor)
            raise GraphWriteError("store.write_edges", e) from e
        return IVGResult(columns=["written"], rows=[[written]])

    # Ids per statement or per Eraser call. IRIS failed to prepare the old
    # unbounded ``IN`` list at 2000 parameters, and its edge statement doubled that.
    _DELETE_CHUNK = 500

    def _has_eraser_method(self, method: str) -> bool:
        """Whether ``Graph.KG.Eraser.<method>`` is compiled here. Probed once per method."""
        cache = self.__dict__.setdefault("_eraser_methods", {})
        if method not in cache:
            try:
                # native: a user refused the dictionary SELECT took the SQL path, without the Eraser (R206)
                cache[method] = compiled_method_exists(self.conn, "Graph.KG.Eraser", method,
                                                       native=self._iris_obj())
            except Exception as exc:
                logger.debug("Graph.KG.Eraser.%s taken as absent: %s", method, exc)
                cache[method] = False
        return cache[method]

    def delete_nodes(self, node_ids: list, *, graph: Optional[str] = None) -> IVGResult:
        """Delete nodes from ONE graph, with their labels, props, edges and embeddings.

        Args:
            node_ids: The ids to delete. Duplicates and empty ids are ignored; ids
                not present in ``graph`` are not an error and are not counted.
            graph: The graph to delete from. ``None`` and ``""`` both mean the
                default graph. Since the re-key to UNIQUE (graph_id, node_id) the
                same id can exist in several graphs; only this graph's copy goes.

        Returns:
            ``deleted``: the number of ``Graph_KG.nodes`` rows actually removed.

        With ``Graph.KG.Eraser`` deployed, each chunk is one ``EraseNodeIds`` call,
        which owns its transaction and also removes the ids' ``^KG`` adjacency,
        props and labels, bumps ``^KG("__version")`` and drops ``^NKG`` unless none of
        the ids (nor both ends of a removed edge) is interned in it. Without it
        (an install with no ObjectScript classes, hence no ``^KG`` to maintain) the
        same rows are deleted by chunked, graph-scoped SQL.
        """
        _guard = getattr(self, "_ledger_guard", None)
        if _guard is not None:
            _guard.check_structural_write("store.delete_nodes")
        canonical = validate_graph_name(graph)
        ids = list(dict.fromkeys(str(n) for n in node_ids or [] if n is not None and str(n) != ""))
        if not ids:
            return IVGResult(columns=["deleted"], rows=[[0]])
        chunks = [ids[i : i + self._DELETE_CHUNK] for i in range(0, len(ids), self._DELETE_CHUNK)]

        if self._has_eraser_method("EraseNodeIds"):
            # The Eraser runs on the Native API; rows this connection wrote but has
            # not committed are invisible to it on a separate connection.
            self._commit_for_eraser("store.delete_nodes")
            deleted = 0
            for chunk in chunks:
                deleted += int(
                    self._call_classmethod(
                        "Graph.KG.Eraser", "EraseNodeIds", canonical, json.dumps(chunk)
                    )
                    or 0
                )
            return IVGResult(columns=["deleted"], rows=[[deleted]])

        return self._delete_nodes_sql(chunks, canonical)

    def _delete_nodes_sql(self, chunks: list, canonical: str) -> IVGResult:
        scope = graph_scope_predicate()
        cursor = self.conn.cursor()

        def run(sql, params, optional=False):
            try:
                cursor.execute(sql, params)
            except Exception as e:
                msg = str(e).lower()
                if optional and ("not found" in msg or "-30" in msg or "does not exist" in msg):
                    return 0
                raise
            rc = getattr(cursor, "rowcount", 0)
            return rc if isinstance(rc, int) and rc > 0 else 0

        deleted = 0
        try:
            for chunk in chunks:
                ph = ",".join("?" * len(chunk))
                # Each endpoint on its own: `s IN (…) OR o_id IN (…)` doubled the
                # parameter count, and an OR defeats both indexes.
                for col in ("s", "o_id"):
                    run(
                        f"DELETE FROM {self._ivg_schema}.rdf_reifications WHERE edge_id IN ("
                        f"SELECT edge_id FROM {self._ivg_schema}.rdf_edges WHERE {col} IN ({ph}) AND {scope})",
                        chunk + [canonical],
                        optional=True,
                    )
                    run(
                        f"DELETE FROM {self._ivg_schema}.kg_EdgeEmbeddings WHERE {col} IN ({ph}) AND {scope}",
                        chunk + [canonical],
                        optional=True,
                    )
                    run(
                        f"DELETE FROM {self._ivg_schema}.rdf_edges WHERE {col} IN ({ph}) AND {scope}",
                        chunk + [canonical],
                    )
                run(f"DELETE FROM {self._ivg_schema}.rdf_labels WHERE s IN ({ph}) AND {scope}", chunk + [canonical])
                run(f"DELETE FROM {self._ivg_schema}.rdf_props WHERE s IN ({ph}) AND {scope}", chunk + [canonical])
                run(f"DELETE FROM {self._ivg_schema}.docs WHERE id IN ({ph}) AND {scope}", chunk + [canonical], optional=True)
                for table in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"):
                    run(
                        f"DELETE FROM {self._ivg_schema}.{table} WHERE node_id IN ({ph}) AND {scope}",
                        chunk + [canonical],
                        optional=True,
                    )
                deleted += run(
                    f"DELETE FROM {self._ivg_schema}.nodes WHERE node_id IN ({ph}) AND {scope}",
                    chunk + [canonical],
                )
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
        return IVGResult(columns=["deleted"], rows=[[deleted]])

    def delete_nodes_by_prefix(self, prefix: str, *, graph: Optional[str] = None) -> int:
        """Delete every node in ``graph`` whose id starts with ``prefix``.

        Runs ``Graph.KG.Eraser.EraseNodes`` in one transaction: labels, props,
        docs, embeddings, incident edges in that graph (both directions), their
        reifications and edge embeddings, and the ``^KG`` entries. Returns the
        ``Graph_KG.nodes`` rows removed.

        Raises:
            ValueError: for an empty prefix, which would mean the whole graph —
                use ``erase_graph`` for that.
            RuntimeError: when ``Graph.KG.Eraser.EraseNodes`` is not deployed.
        """
        _guard = getattr(self, "_ledger_guard", None)
        if _guard is not None:
            _guard.check_structural_write("store.delete_nodes_by_prefix")
        if not prefix:
            raise ValueError(
                "delete_nodes_by_prefix refuses an empty prefix, which would erase "
                "the whole graph; use erase_graph(graph) for that"
            )
        canonical = validate_graph_name(graph)
        if not self._has_eraser_method("EraseNodes"):
            raise RuntimeError(
                "Graph.KG.Eraser.EraseNodes is not deployed in this namespace; "
                "load the iris_src classes first"
            )
        self._commit_for_eraser("store.delete_nodes_by_prefix")
        return int(self._call_classmethod("Graph.KG.Eraser", "EraseNodes", canonical, str(prefix)) or 0)

    def _commit_for_eraser(self, operation: str) -> None:
        """Commit this connection's pending writes, so ``Graph.KG.Eraser`` (on the Native API's connection) sees them.

        A refused commit raises ``GraphWriteError`` naming ``operation``, before anything is erased. It was
        ``except Exception: pass``, and the Eraser then ran against a database without those writes (4.6.0 R276).
        """
        try:
            self.conn.commit()
        except Exception as e:
            raise GraphWriteError(
                operation, e, left="nothing erased, and this connection's earlier writes are not committed"
            ) from e

    def delete_edges(self, edges: list, *, graph: Optional[str] = None) -> IVGResult:
        """Delete ``(s, p, o)`` edges from ONE graph; returns the rows actually removed.

        With ``Graph.KG.Eraser`` deployed this also removes the edges'
        reifications, embeddings and ``^KG`` adjacency (``EraseEdges``).
        """
        _guard = getattr(self, "_ledger_guard", None)
        if _guard is not None:
            _guard.check_structural_write("store.delete_edges")
        canonical = validate_graph_name(graph)
        triples = list(
            dict.fromkeys((str(e[0]), str(e[1]), str(e[2])) for e in edges or [])
        )
        if not triples:
            return IVGResult(columns=["deleted"], rows=[[0]])

        if self._has_eraser_method("EraseEdges"):
            self._commit_for_eraser("store.delete_edges")
            deleted = 0
            for i in range(0, len(triples), self._DELETE_CHUNK):
                payload = json.dumps(
                    [{"s": s, "p": p, "o": o} for s, p, o in triples[i : i + self._DELETE_CHUNK]]
                )
                deleted += int(
                    self._call_classmethod("Graph.KG.Eraser", "EraseEdges", canonical, payload) or 0
                )
            return IVGResult(columns=["deleted"], rows=[[deleted]])

        scope = graph_scope_predicate()
        cursor = self.conn.cursor()
        deleted = 0
        try:
            for s, p, o in triples:
                cursor.execute(
                    f"DELETE FROM {self._ivg_schema}.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND {scope}",
                    [s, p, o, canonical],
                )
                rc = getattr(cursor, "rowcount", 0)
                deleted += rc if isinstance(rc, int) and rc > 0 else 0
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
        return IVGResult(columns=["deleted"], rows=[[deleted]])

    # ── SQL Passthrough ───────────────────────────────────────────────────────

    def execute_sql(self, sql: str, params: list, read_only: bool = True) -> IVGResult:
        cursor = self.conn.cursor()
        try:
            # No placeholders, no parameter list: a driver can reject `execute(sql, [])` against a statement
            # it has cached with a different parameter count ("Invalid number of parameters").
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            if "__rn" in cols:
                rn_idx = cols.index("__rn")
                cols = [c for c in cols if c != "__rn"]
                rows = [[v for i, v in enumerate(r) if i != rn_idx] for r in rows]
            return IVGResult(columns=cols, rows=[list(r) for r in rows], sql=sql, params=params)
        except Exception as e:
            # Keep the whole text: IRIS puts a failing UDF's %msg (where IVG names the
            # Cypher error kind) after ~200 characters of SQLCODE/Location preamble.
            err = str(e)
            logger.warning("execute_sql error: %s", err[:500])
            try:
                self.conn.rollback()
            except Exception:
                pass
            return IVGResult(columns=[], rows=[], sql=sql, params=params, error=err)

    @staticmethod
    def _stmt_is_dml(stmt: str) -> bool:
        """Return True if stmt is a DELETE/INSERT/UPDATE DML (not a SELECT)."""
        if stmt.startswith(
            (
                "__constraint_check_delete_connected__",
                "__capture_edge_hwm__",
                _CAPTURE_PREFIX,
                _PAIRS_PREFIX,
                _PIN_STAGE_PREFIX,
            )
        ):
            return False  # handled separately; not a data-mutating DML
        upper = stmt.lstrip().upper()
        # Statements may be prefixed by a CTE ("WITH … DELETE …")
        # Walk past any leading WITH clause to find the verb.
        if upper.startswith("WITH "):
            # Find the first DML verb after the CTE block.
            for verb in ("DELETE ", "INSERT ", "UPDATE "):
                if verb in upper:
                    return True
            return False
        return upper.startswith(("DELETE ", "INSERT ", "UPDATE "))

    def execute_transaction(self, stmts: list, params_list: list) -> IVGResult:
        cursor = self.conn.cursor()
        cursor.execute("START TRANSACTION")
        try:
            rows = []
            pre_captured_rows = None
            pre_captured_description = None

            # When a transactional query has DELETE NODES or REMOVE label followed
            # by a final SELECT, the SELECT must see the pre-delete snapshot.
            # Detect that pattern and run the SELECT first so its results reflect
            # the rows that are about to be deleted/unlabeled.
            #
            # Pre-snapshot is needed for:
            #   DELETE FROM nodes         — node deletion (nodes gone after DELETE)
            #   DELETE FROM rdf_labels    — label removal (MATCH by label fails after removal)
            #
            # Pre-snapshot is NOT needed for:
            #   DELETE FROM rdf_props     — property removal (nodes still exist, SELECT
            #                               should see the post-removal state where the
            #                               property is NULL, not the pre-removal value)
            import re as _del_re

            # Use re.search (not re.match) so CTE-prefixed DELETE statements like
            # "WITH Stage1 AS (...) DELETE FROM nodes ..." are also detected.
            # Include rdf_edges and rdf_labels so all label/relationship DELETE operations
            # trigger pre-snapshot. For rdf_labels: the outer MATCH JOINs use the deleted
            # labels as filter conditions, so the SELECT must run before DELETE. The
            # labels() function in the translator excludes removed labels from its result
            # via NOT IN (...) to return the post-deletion view.
            has_delete_dml = any(
                isinstance(s, str)
                and _del_re.search(
                    r"DELETE\s+FROM\s+(?:\w+\.)?(?:nodes|rdf_edges|rdf_labels)\b",
                    s,
                    _del_re.IGNORECASE,
                )
                and not s.startswith("__constraint_check_delete_connected__")
                and not s.startswith("__after_result__")
                and not s.startswith((_CAPTURE_PREFIX, _PAIRS_PREFIX))
                for s in stmts
            )
            last_is_select = (
                stmts
                and isinstance(stmts[-1], str)
                and not self._stmt_is_dml(stmts[-1])
                and not stmts[-1].startswith("__constraint_check_delete_connected__")
            )
            # `__snapshot_stage__ {spec}\n<sql>`: capture a WITH stage's computed
            # columns before the DML runs; `{stage} AS (__SNAPSHOT__)` in a later
            # statement reads them back (List12 [1]/[2]).
            stage_snaps: dict = {}

            def _fill_snapshots(sql):
                if not isinstance(sql, str) or "(__SNAPSHOT__)" not in sql:
                    return sql
                for m in _del_re.finditer(r"(\w+) AS \(__SNAPSHOT__\)", sql):
                    name = m.group(1)
                    body = (
                        _stage_snapshot_sql(name, *stage_snaps[name])
                        if name in stage_snaps
                        else f"SELECT * FROM {name}__live"
                    )
                    sql = sql.replace(f"{name} AS (__SNAPSHOT__)", f"{name} AS ({body})", 1)
                return sql

            def _snapshot(i, sql=None):
                spec_line, snap_sql = (sql or stmts[i])[len("__snapshot_stage__ ") :].split("\n", 1)
                spec = json.loads(spec_line)
                cursor.execute(snap_sql, params_list[i] if i < len(params_list) else [])
                snap_rows = [list(r) for r in cursor.fetchall()]
                snap_cols = [d[0] for d in cursor.description] if cursor.description else []
                snap_cols, snap_rows = _decode_snapshot_typed(snap_cols, snap_rows)
                stage_snaps[spec["stage"]] = (spec["cols"], snap_cols, snap_rows)

            for i, stmt in enumerate(stmts):
                # a stage kept by the ids an earlier write captured (`__IDSALL_`, R241) is read where it stands
                if isinstance(stmt, str) and stmt.startswith("__snapshot_stage__ ") and "__IDSALL_" not in stmt:
                    _snapshot(i)

            # `__pin_stage__ <stage>\n<select>`: a WITH stage read before the writes that change its MATCH; later
            # statements read `<stage> AS (__PINNED_<stage>__)` back from it (4.6.0 R208).
            pinned: dict = {}
            pinned_early: set = set()

            captured_ids: dict = {}
            captured_graphs: dict = {}
            captured_early: set = set()

            def _pin(i):
                name, pin_sql = stmts[i][len(_PIN_STAGE_PREFIX) :].split("\n", 1)
                p = params_list[i] if i < len(params_list) else []
                cursor.execute(_expand_all_ids(_fill_pinned(_fill_snapshots(pin_sql), pinned), captured_ids), p)
                pinned[name.strip()] = ([d[0] for d in cursor.description], [list(r) for r in cursor.fetchall()])

            if has_delete_dml and last_is_select:
                # the result is read ahead of the writes, so are the stages and captures it reads
                final = stmts[-1] if isinstance(stmts[-1], str) else ""
                wanted = set(_IDS_ALL_RE.findall(final))
                for j, s in enumerate(stmts):
                    if not isinstance(s, str):
                        continue
                    if "__PINNED_" in final and s.startswith(_PIN_STAGE_PREFIX):
                        _pin(j)
                        pinned_early.add(j)
                    elif s.startswith(_CAPTURE_PREFIX) and s[len(_CAPTURE_PREFIX) :].split("\n", 1)[0].strip() in wanted:
                        key, cap_sql = s[len(_CAPTURE_PREFIX) :].split("\n", 1)
                        cap_sql = _expand_all_ids(_fill_pinned(_fill_snapshots(cap_sql), pinned), captured_ids)
                        cursor.execute(cap_sql, params_list[j])
                        _capture_rows(key.strip(), cursor.fetchall(), captured_ids, captured_graphs)
                        captured_early.add(j)
                final_sql = _expand_all_ids(_fill_pinned(_fill_snapshots(stmts[-1]), pinned), captured_ids)
                final_params = params_list[-1] if len(params_list) >= len(stmts) else []
                cursor.execute(final_sql, final_params)
                pre_captured_rows = cursor.fetchall()
                pre_captured_description = cursor.description

            edge_hwm = None
            # `__after_result__` statements run once the result has been read: a
            # multigraph `DELETE t MERGE … RETURN` (spec 234) reads the matched rows
            # after MERGE wrote and before the DELETE removes what they matched.
            after_result = []
            for i, stmt in enumerate(stmts):
                p = params_list[i] if i < len(params_list) else []
                if isinstance(stmt, str) and stmt.startswith("__after_result__"):
                    after_result.append((stmt[len("__after_result__ ") :], p))
                    continue
                # `DELETE r CREATE …`: the translator runs the CREATE first and
                # bounds the DELETE by the edge_id high-water mark from before it.
                if isinstance(stmt, str) and stmt.startswith("__capture_edge_hwm__"):
                    cursor.execute(stmt[len("__capture_edge_hwm__ ") :], p)
                    hwm_row = cursor.fetchone()
                    edge_hwm = hwm_row[0] if hwm_row and hwm_row[0] is not None else 0
                    continue
                if edge_hwm is not None and "__EDGE_HWM__" in p:
                    p = [edge_hwm if v == "__EDGE_HWM__" else v for v in p]
                if isinstance(stmt, str) and stmt.startswith("__snapshot_stage__ "):
                    if "__IDSALL_" in stmt:
                        _snapshot(i, _expand_all_ids(stmt, captured_ids))
                    continue  # the others were captured before the loop
                if isinstance(stmt, str) and stmt.startswith(_PIN_STAGE_PREFIX):
                    if i not in pinned_early:
                        _pin(i)
                    continue
                # a stage kept by the ids an earlier write captured reads them here, a capture through it too (R241)
                stmt = _expand_all_ids(_fill_pinned(_fill_snapshots(stmt), pinned), captured_ids)
                # `__capture_ids__ <key>\n<select>`: the ids a DELETE clause
                # touches, read once before it writes (spec 229).
                if isinstance(stmt, str) and stmt.startswith(_CAPTURE_PREFIX):
                    if i in captured_early:
                        continue
                    key, cap_sql = stmt[len(_CAPTURE_PREFIX) :].split("\n", 1)
                    cursor.execute(cap_sql, p)
                    _capture_rows(key.strip(), cursor.fetchall(), captured_ids, captured_graphs)
                    continue
                if isinstance(stmt, str) and stmt.startswith(_PAIRS_PREFIX):
                    key, cap_sql = stmt[len(_PAIRS_PREFIX) :].split("\n", 1)
                    cursor.execute(cap_sql, p)
                    _capture_pairs(key.strip(), cursor.fetchall(), captured_ids)
                    continue
                if isinstance(stmt, str) and stmt.startswith(
                    "__constraint_check_delete_connected__"
                ):
                    actual_sql = stmt[len("__constraint_check_delete_connected__ ") :]
                    count = 0
                    for chunk_sql in _expand_captured(actual_sql, captured_ids, captured_graphs):
                        cursor.execute(chunk_sql, p)
                        count_row = cursor.fetchone()
                        count += (count_row[0] or 0) if count_row else 0
                    if count > 0:
                        raise GraphWriteError("execute_cypher", RuntimeError(_DELETE_CONNECTED), plain=True)
                    continue
                # Skip the final SELECT — already executed above for pre-snapshot.
                if pre_captured_rows is not None and i == len(stmts) - 1:
                    continue
                is_dml = isinstance(stmt, str) and self._stmt_is_dml(stmt)
                for chunk_sql in (
                    _expand_captured(stmt, captured_ids, captured_graphs)
                    if isinstance(stmt, str)
                    else [stmt]
                ):
                    cursor.execute(chunk_sql, p)
                # IRIS keeps the previous SELECT's description after a DML
                # statement; fetching on it is a null pointer exception.
                if cursor.description and not is_dml:
                    rows = cursor.fetchall()
            description = cursor.description
            for stmt, p in after_result:
                if edge_hwm is not None and "__EDGE_HWM__" in p:
                    p = [edge_hwm if v == "__EDGE_HWM__" else v for v in p]
                if stmt.startswith("__constraint_check_delete_connected__"):
                    actual_sql = stmt[len("__constraint_check_delete_connected__ ") :]
                    count = 0
                    for chunk_sql in _expand_captured(actual_sql, captured_ids, captured_graphs):
                        cursor.execute(chunk_sql, p)
                        count_row = cursor.fetchone()
                        count += (count_row[0] or 0) if count_row else 0
                    if count > 0:
                        raise GraphWriteError("execute_cypher", RuntimeError(_DELETE_CONNECTED), plain=True)
                    continue
                for chunk_sql in _expand_captured(stmt, captured_ids, captured_graphs):
                    cursor.execute(chunk_sql, p)
            self.conn.commit()

            if pre_captured_rows is not None:
                # Use the pre-delete snapshot as the result.
                desc = pre_captured_description
                snap_rows = pre_captured_rows
                cols = [d[0] for d in desc] if desc else []
                if "__rn" in cols:
                    rn_idx = cols.index("__rn")
                    cols = [c for c in cols if c != "__rn"]
                    snap_rows = [[v for j, v in enumerate(r) if j != rn_idx] for r in snap_rows]
                return IVGResult(columns=cols, rows=[list(r) for r in snap_rows])

            cols = [d[0] for d in description] if description else []
            # Strip internal ROW_NUMBER column injected by fetch-first-unsafe pagination
            if "__rn" in cols:
                rn_idx = cols.index("__rn")
                cols = [c for c in cols if c != "__rn"]
                if rows is not None:
                    rows = [[v for i, v in enumerate(r) if i != rn_idx] for r in rows]
            if rows is None:
                rows = []
            return IVGResult(columns=cols, rows=[list(r) for r in rows])
        except Exception as e:
            self.conn.rollback()
            # R221: a refused write is one documented type, GraphWriteError (a RuntimeError), raised after the
            # rollback with the cause kept; it was a bare Exception, or the driver's own type. A dropped connection
            # keeps its type, which the reconnect logic reads.
            if isinstance(e, (GraphWriteError, OSError)):
                raise
            raise GraphWriteError("execute_cypher", e) from e

    # ── Traversal ─────────────────────────────────────────────────────────────

    def _select_bfs_strategy(
        self, graph: Optional[str] = None, direction: str = "out"
    ) -> "_BfsStrategy":
        """Choose the BFS adapter based on available capabilities (internal seam).

        Two questions the Rust accelerator cannot be asked, so two reasons to decline it:

        A named graph never takes the Arno path: Graph.KG.NKGAccel.BFSJson has no
        graph argument, so the Rust accelerator would walk ^NKG across every graph.
        The default graph keeps it, which is where its published numbers were
        measured (spec 227).

        An inbound or undirected traversal never takes it either: BFSJson has no
        direction argument and walks ^NKG outbound only, so `MATCH (x)<-[r*1..1]-(y)`
        came back with x's *successors* — real edges, pointing the wrong way, with
        nothing reported. The ObjectScript adapter answers all three directions
        (spec 230, FR-031).
        """
        if direction and str(direction).lower() not in ("out", "outbound"):
            return _ObjectScriptBfsAdapter(self)
        # the default graph takes Arno only where it is the only graph with edges: ^NKG holds every graph's, and an
        # all-graphs read's default-graph pass reached the named graphs' nodes through it
        if not graph and self._detect_arno() and self._arno_capabilities.get("bfs") and self._only_default_graph():
            return _ArnoBfsAdapter(self)
        return _ObjectScriptBfsAdapter(self)

    def _only_default_graph(self) -> bool:
        """Whether the default graph (key 0) is the only one with adjacency (^KG("out")'s top level). False when it cannot
        be read: the graph-scoped adapter is the safe answer."""
        try:
            native = self._iris_obj()
            first = native.nextSubscript(False, "^KG", "out", "")
            if first in (None, ""):
                return True
            return str(first) == "0" and native.nextSubscript(False, "^KG", "out", first) in (None, "")
        except Exception:
            return False

    @warn_empty_bfs
    def execute_bfs(
        self,
        source_id: str,
        predicates: list,
        max_hops: int,
        direction: str,
        max_results: int,
        *,
        graph: Optional[str] = None,
    ) -> IVGResult:
        """Execute BFS via the selected strategy adapter, scoped to one graph."""
        strategy = self._select_bfs_strategy(graph, direction=direction)
        return strategy.run(source_id, predicates, max_hops, direction, max_results, graph)

    def execute_vl_ends(
        self,
        source_id: str,
        predicates: list,
        direction: str,
        min_hops: int,
        max_hops: int,
        *,
        graph: Optional[str] = None,
    ) -> IVGResult:
        """The far ends of a variable-length hop from ``source_id``: ``(id, hops)`` rows, each end once at the least
        length of a path that reaches it in ``min_hops..max_hops``.

        A path here uses no relationship twice (openCypher's relationship uniqueness), so an undirected walk does not
        come back over the edge it arrived by; a node reached again over another relationship (a cycle) is an end.
        ``Graph.KG.VarLength.Ends`` is the walk the native path and ``CY_VLP_ENDS`` run. BFS reports a node once per
        depth along any walk, which is what made ``(a)-[*1..3]-(b)`` from a chain's end return the start (4.6.0, N1).
        ``graph`` names one graph (None or ``""``: the default graph). Raises when the walk fails.
        """
        raw = str(
            self._call_classmethod(
                "Graph.KG.VarLength",
                "Ends",
                source_id,
                json.dumps(list(predicates)) if predicates else "",
                direction,
                int(min_hops),
                int(max_hops),
                0,
                graph or "",
            )
        )
        ends = json.loads(raw) if raw else []
        return IVGResult(columns=["id", "hops"], rows=[[str(e["t"]), int(e["l"])] for e in ends])

    def _run_arno_bfs(
        self, source_id: str, predicates: list, max_hops: int, direction: str, max_results: int
    ) -> IVGResult:
        """Arno/NKG Rust-accelerated BFS path. Falls back to ObjectScript on error."""
        import json as _json
        predicates_json = _json.dumps(predicates) if predicates else ""
        try:
            raw = self._arno_call(
                "Graph.KG.NKGAccel",
                "BFSJson",
                source_id,
                predicates_json,
                str(max_hops),
                str(max_results),
            )
            raw_val = raw if isinstance(raw, str) else str(raw)
            if raw_val.startswith("SORTED:"):
                tag = raw_val.split(":", 2)[1]
                if tag == "0":
                    return IVGResult(columns=["id", "hops", "pred"], rows=[])
                # `SORTED:` stages the hits in ^ArnoKG("bfs_r") and only the cursor
                # API reads them back. The old loop asked NKGAccel (which does not
                # own ReadBFSPage) for page *numbers* and concatenated the replies,
                # but ReadBFSPage takes (tag, cursorStep, cursorO, pageSize) and
                # returns an {"items", "done"} envelope — the concatenation was
                # never valid JSON, so this path returned nothing at all.
                from iris_vector_graph.engine import _bfs_stream_pages

                results = list(_bfs_stream_pages(self.conn, tag))
            else:
                results = _json.loads(raw_val) if raw_val else []
                if not isinstance(results, list):
                    results = []
            rows = [
                [
                    r.get("o", r.get("id", r.get("node_id", ""))),
                    r.get("step", r.get("hops", 0)),
                    r.get("pred", r.get("p", "")),
                ]
                for r in results
            ]
            return IVGResult(columns=["id", "hops", "pred"], rows=rows)
        except Exception as e:
            logger.warning("Arno BFS failed, falling back to ObjectScript: %s", e)
            return _ObjectScriptBfsAdapter(self).run(
                source_id, predicates, max_hops, direction, max_results
            )

    def _run_objectscript_bfs(
        self,
        source_id: str,
        predicates: list,
        max_hops: int,
        direction: str,
        max_results: int,
        graph: Optional[str] = None,
    ) -> IVGResult:
        """ObjectScript BFSFastJsonSorted path with SQL fallback.

        The graph goes over the wire as a *name* — `""` for the default graph —
        because Graph.KG.GraphKey.ForIndex() owns the ^KG subscript derivation
        (ADR-0003). Passing 0 from here would be a second implementation of it.
        """
        import json as _json
        predicates_json = _json.dumps(predicates) if predicates else ""
        try:
            bfs_json = str(
                self._call_classmethod(
                    "Graph.KG.Traversal",
                    "BFSFastJsonSorted",
                    source_id,
                    predicates_json,
                    str(max_hops),
                    "",
                    direction,
                    str(max_results),
                    graph or "",
                )
            )
        except Exception as e:
            logger.warning("BFS ObjectScript failed: %s", e)
            return self._sql_bfs_fallback(
                source_id, predicates, max_hops, direction, max_results, graph=graph
            )

        val = bfs_json if isinstance(bfs_json, str) else str(bfs_json)
        if val.startswith("SORTED:"):
            tag = val.split(":", 2)[1]
            if tag == "0":
                return self._sql_bfs_fallback(
                    source_id, predicates, max_hops, direction, max_results, graph=graph
                )
            from iris_vector_graph.engine import _bfs_stream_pages
            results = list(_bfs_stream_pages(self.conn, tag))
            rows = [
                [
                    r.get("o", r.get("id", "")),
                    r.get("step", r.get("hops", 0)),
                    r.get("pred", r.get("p", "")),
                ]
                for r in results
            ]
            return IVGResult(columns=["id", "hops", "pred"], rows=rows)

        try:
            results = _json.loads(val) if val else []
            if not isinstance(results, list):
                results = []
        except Exception:
            results = []

        rows = [
            [r.get("id", r.get("node_id", "")), r.get("hops", 0), r.get("pred", "")]
            for r in results
        ]
        return IVGResult(columns=["id", "hops", "pred"], rows=rows)

    def _sql_bfs_fallback(
        self,
        source_id: str,
        predicates: list,
        max_hops: int,
        direction: str,
        max_results: int,
        graph: Optional[str] = None,
    ) -> "IVGResult":
        """Walk Graph_KG.rdf_edges hop by hop, scoped to one graph.

        Every hop carries a graph predicate. Before spec 227 none of them did, and
        because _run_objectscript_bfs falls through to here whenever the adjacency
        walk returns nothing, a named-graph traversal quietly became a scan of every
        graph's edges — the leak the two-graph Gate 3 probe caught.

        COALESCE on both sides: `graph_id` is NOT NULL DEFAULT '' on a fresh install,
        but a row written before the column existed can still read as NULL, and an
        IRIS SQL empty VARCHAR comes back as $Char(0).
        """
        cursor = self.conn.cursor()
        graph_id = graph or ""
        visited: dict[str, int] = {source_id: 0}
        frontier: list[str] = [source_id]
        result_rows: list[list] = []
        graph_clause = " AND COALESCE(graph_id, '') = COALESCE(?, '')"
        for hop in range(1, max_hops + 1):
            if not frontier:
                break
            preds_clause = ""
            params = list(frontier)
            if predicates:
                placeholders = ",".join("?" * len(predicates))
                preds_clause = f" AND p IN ({placeholders})"
                params += predicates
            params.append(graph_id)
            placeholders_f = ",".join("?" * len(frontier))
            if direction in ("out", "outbound"):
                sql = f"SELECT DISTINCT o_id, p FROM {self._ivg_schema}.rdf_edges WHERE s IN ({placeholders_f}){preds_clause}{graph_clause}"
            elif direction in ("in", "inbound"):
                sql = f"SELECT DISTINCT s, p FROM {self._ivg_schema}.rdf_edges WHERE o_id IN ({placeholders_f}){preds_clause}{graph_clause}"
            else:
                sql_out = f"SELECT DISTINCT o_id AS nbr, p FROM {self._ivg_schema}.rdf_edges WHERE s IN ({placeholders_f}){preds_clause}{graph_clause}"
                sql_in = f"SELECT DISTINCT s AS nbr, p FROM {self._ivg_schema}.rdf_edges WHERE o_id IN ({placeholders_f}){preds_clause}{graph_clause}"
                cursor.execute(sql_out, params)
                nbrs_out = cursor.fetchall()
                cursor.execute(sql_in, params)
                nbrs_in = cursor.fetchall()
                nbrs = [(r[0], r[1]) for r in nbrs_out + nbrs_in]
                new_frontier = []
                for nbr, pred in nbrs:
                    if nbr not in visited:
                        visited[nbr] = hop
                        new_frontier.append(nbr)
                        result_rows.append([nbr, hop, pred])
                        if max_results and len(result_rows) >= max_results:
                            return IVGResult(columns=["id", "hops", "pred"], rows=result_rows)
                frontier = new_frontier
                continue
            cursor.execute(sql, params)
            new_frontier = []
            for nbr, pred in cursor.fetchall():
                if nbr not in visited:
                    visited[nbr] = hop
                    new_frontier.append(nbr)
                    result_rows.append([nbr, hop, pred])
                    if max_results and len(result_rows) >= max_results:
                        return IVGResult(columns=["id", "hops", "pred"], rows=result_rows)
            frontier = new_frontier
        return IVGResult(columns=["id", "hops", "pred"], rows=result_rows)

    def shortest_path_length(
        self, source_id: str, target_id: str, predicates: list, max_hops: int, direction: str
    ) -> int:
        """The length of a shortest path (-1 for none within max_hops), searched from both ends
        (Graph.KG.TraversalPaths.ShortestLength): what execute_shortest_path's paths' length is, without them."""
        import json as _json

        return int(
            self._call_classmethod(
                "Graph.KG.TraversalPaths",
                "ShortestLength",
                source_id,
                target_id,
                str(max_hops),
                _json.dumps(predicates) if predicates else "",
                direction,
            )
        )

    def execute_shortest_path(
        self,
        source_id: str,
        target_id: str,
        predicates: list,
        max_hops: int,
        direction: str,
        find_all: bool,
    ) -> IVGResult:
        import json as _json

        if direction == "in":
            # ShortestPathJson walks "out" and "both" only, so `(a)<-[*]-(b)` answered no path (4.6.0 R061): an
            # incoming path from a to b is an outgoing one from b to a, read backwards
            found = self.execute_shortest_path(target_id, source_id, predicates, max_hops, "out", find_all)
            rows = []
            for path_json, length in found.rows:
                path = _json.loads(path_json)
                path["nodes"] = path.get("nodes", [])[::-1]
                path["rels"] = path.get("rels", [])[::-1]
                rows.append([_json.dumps(path), length])
            return IVGResult(columns=found.columns, rows=rows)
        predicates_json = _json.dumps(predicates) if predicates else ""
        try:
            path_json = str(
                self._call_classmethod(
                    "Graph.KG.Traversal",
                    "ShortestPathJson",
                    source_id,
                    target_id,
                    str(max_hops),
                    predicates_json,
                    direction,
                    str(int(find_all)),
                )
            )
            paths_raw = _json.loads(path_json) if path_json else []
            if isinstance(paths_raw, dict):
                paths = [paths_raw]
            else:
                paths = paths_raw
            rows = [
                [_json.dumps(p), p.get("length", len(p.get("nodes", [])) - 1)] for p in paths if p
            ]
            return IVGResult(columns=["path", "length"], rows=rows)
        except Exception as e:
            logger.warning("ShortestPath failed: %s", e)
            return IVGResult(columns=["path", "length"], rows=[])

    # ── Analytics ─────────────────────────────────────────────────────────────

    def execute_ppr(
        self,
        seed_ids: list,
        damping: float,
        max_iterations: int,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
        *,
        graph: Optional[str] = None,
        exclude: Optional[list] = None,
        limit: Optional[int] = None,
        tolerance: float = DEFAULT_PPR_TOLERANCE,
    ) -> IVGResult:
        """Personalized PageRank from ``seed_ids`` as ``id``/``score`` rows: networkx's ``pagerank(G, alpha=damping,
        personalization=seeds, tol=tolerance)``, a node with no out-edges handing its score to the seeds. The walk
        stops after ``max_iterations`` or once the scores' summed change in an iteration is below N * ``tolerance``,
        N the nodes holding a score (4.6.0 R270: a fixed 1e-4 before)."""
        params = {
            "seeds": algo_cache.seed_key(seed_ids or []),
            "damping": damping,
            "max_iterations": max_iterations,
            "bidirectional": bool(bidirectional),
            "reverse_edge_weight": reverse_edge_weight,
            "exclude": None if exclude is None else sorted(set(exclude)),
            "limit": limit,
            "tolerance": float(tolerance),
        }
        return self._cached_algorithm(
            "ppr",
            graph,
            params,
            ["id", "score"],
            lambda: self._execute_ppr(
                seed_ids,
                damping,
                max_iterations,
                bidirectional,
                reverse_edge_weight,
                graph=graph,
                exclude=exclude,
                limit=limit,
                tolerance=tolerance,
            ),
        )

    def _execute_ppr(
        self,
        seed_ids: list,
        damping: float,
        max_iterations: int,
        bidirectional: bool = False,
        reverse_edge_weight: float = 1.0,
        *,
        graph: Optional[str] = None,
        exclude: Optional[list] = None,
        limit: Optional[int] = None,
        tolerance: float = DEFAULT_PPR_TOLERANCE,
    ) -> IVGResult:
        import json as _json

        from iris_vector_graph._validate import validate_graph_name

        if not seed_ids:
            raise ValueError("seed_ids must not be empty")
        # Spec 235's two trailing RunJson arguments go only when asked for, so the
        # default call is the pre-235 call; the tolerance only when it is not RunJson's
        # 1e-6 default, after those two at RunJson's own defaults (4.6.0 R270).
        custom_tol = float(tolerance) != DEFAULT_PPR_TOLERANCE
        extra = ()
        if exclude is not None or limit is not None or custom_tol:
            extra = (_json.dumps(list(exclude)) if exclude else "", str(1000 if limit is None else int(limit)))
        if custom_tol:
            extra += (repr(float(tolerance)),)
        graph_name = validate_graph_name(graph)
        seeds_json = _json.dumps(seed_ids)
        wants_reverse = bool(bidirectional) and reverse_edge_weight > 0
        try:
            # A Native Reader only when the request fits what it can answer. The reader
            # takes seeds, damping and iterations and has no reverse-edge parameters, so
            # routing a bidirectional request there returns forward-only scores with no
            # error — which is how `bidirectional=True` came to be silently ignored.
            #
            # Nor a named graph: the reader has no graph dimension and would answer from
            # every graph's adjacency (spec 231 FR-016, spec 236 FR-014a; `route` refuses
            # it). Nor an exclusion or a row limit: it has neither (spec 235). Nor a
            # tolerance but the default: it has its own stop rule (4.6.0 R270).
            raw = None
            if not wants_reverse and not exclude and limit is None and not custom_tol:
                raw = self._native_answer(
                    "ppr",
                    lambda: self._arno_call(
                        "Graph.KG.ArnoAccel", "PPRNativeJson", seeds_json, str(damping), str(max_iterations)
                    ),
                    graph_name,
                )
                if raw is not None:
                    try:
                        _scored_node_rows(_json.loads(raw))
                    except (UnreadableResultShape, ValueError) as exc:
                        logger.warning("PPR: Native Reader answer unreadable (%s); the Owner answers", exc)
                        raw = None
            if raw is None:
                # `RunJson`, not `PPRJson`: personalized PageRank is what
                # Graph.KG.PageRank calls it (PageRank.cls:16). `PPRJson` is the
                # Arno spelling and exists only on the accelerator classes, so this
                # branch used to raise <METHOD DOES NOT EXIST> and return no scores.
                #
                # All six arguments: `RunJson(seedJson, alpha, maxIter, bidir, revWeight,
                # pGraph)`. Passing the first three left the ObjectScript defaults
                # (`bidir = 0`) in place, so reverse traversal never happened however the
                # caller asked; omitting `pGraph` walks the default graph.
                raw = str(
                    self._call_classmethod(
                        "Graph.KG.PageRank",
                        "RunJson",
                        seeds_json,
                        str(damping),
                        str(max_iterations),
                        "1" if wants_reverse else "0",
                        str(float(reverse_edge_weight)),
                        graph_name,
                        *extra,
                    )
                )
            results = _json.loads(raw) if raw else []
            rows = _scored_node_rows(results)
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.warning("PPR failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def execute_pagerank(self, damping: float, max_iterations: int) -> IVGResult:
        return self._cached_algorithm(
            "pagerank",
            None,
            {"damping": damping, "max_iterations": max_iterations},
            ["id", "score"],
            lambda: self._execute_pagerank(damping, max_iterations),
        )

    def _execute_pagerank(self, damping: float, max_iterations: int) -> IVGResult:
        import json as _json

        try:
            rows = None
            raw = self._native_answer(
                "pagerank",
                lambda: self._arno_call(
                    "Graph.KG.ArnoAccel", "PageRankNativeJson", str(damping), str(max_iterations)
                ),
            )
            if raw is not None:
                try:
                    rows = _scored_node_rows(_json.loads(raw))
                except (UnreadableResultShape, ValueError) as exc:
                    logger.warning("PageRank: Native Reader answer unreadable (%s); the Owner answers", exc)
            if rows is None:
                raw = str(
                    self._call_classmethod(
                        "Graph.KG.PageRank", "PageRankGlobalJson", str(damping), str(max_iterations)
                    )
                )
                rows = _scored_node_rows(_json.loads(raw) if raw else [])
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.warning("PageRank failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def execute_wcc(self, max_iterations: Optional[int] = None) -> IVGResult:
        params = {} if max_iterations is None else {"max_iterations": int(max_iterations)}
        return self._cached_algorithm(
            "wcc", None, params, ["id", "component_id"], lambda: self._execute_wcc(max_iterations)
        )

    def _execute_wcc(self, max_iterations: Optional[int] = None) -> IVGResult:
        """Connected components as `id, component_id`, one row per node.

        `ArnoAccel.WCCJson` answers with a summary — a component count and the largest
        few — so the accelerated path falls back to `Graph.KG.Algorithms.WCCJson`,
        which answers the question these columns describe (spec 230, FR-017). Read as
        a map, the summary produced the rows `[["components", 1], ["largest", [...]]]`:
        a plausible-looking answer to a question nobody asked.

        ``max_iterations`` is the Owner's limit on label-spreading rounds (4.6.0 R101): when
        given, the Owner answers, so the limit means the same whether or not a Native
        Reader is proven, and a graph that needs more rounds is an error, not an answer.
        """
        import json as _json

        try:
            rows = None
            raw = None
            if max_iterations is None:
                raw = self._native_answer(
                    "wcc", lambda: self._arno_call("Graph.KG.ArnoAccel", "WCCNativeJson")
                )
            if raw is not None:
                try:
                    rows = _membership_rows(_json.loads(raw), "component_id")
                except (UnreadableResultShape, ValueError) as exc:
                    # One fallback, not a loop: the Owner's own answer is read strictly.
                    logger.warning("WCC: Native Reader answer unreadable (%s); the Owner answers", exc)
            if rows is None:
                limit = () if max_iterations is None else (str(int(max_iterations)),)
                raw = str(self._call_classmethod("Graph.KG.Algorithms", "WCCJson", *limit))
                rows = _membership_rows(_json.loads(raw) if raw else {}, "component_id")
            return IVGResult(columns=["id", "component_id"], rows=rows)
        except Exception as e:
            logger.warning("WCC failed: %s", e)
            return IVGResult(columns=["id", "component_id"], rows=[], error=str(e)[:200])

    def execute_cdlp(self, max_iterations: int) -> IVGResult:
        return self._cached_algorithm(
            "cdlp",
            None,
            {"max_iterations": max_iterations},
            ["id", "community_id"],
            lambda: self._execute_cdlp(max_iterations),
        )

    def _execute_cdlp(self, max_iterations: int) -> IVGResult:
        """Community labels as `id, community_id`, one row per node.

        Same shape mismatch as :meth:`execute_wcc`: `ArnoAccel.CDLPJson` answers
        `{"communities": 2, "largest": [...]}` (spec 230, FR-017).
        """
        import json as _json

        try:
            rows = None
            raw = self._native_answer(
                "cdlp",
                lambda: self._arno_call("Graph.KG.ArnoAccel", "CDLPNativeJson", str(max_iterations)),
            )
            if raw is not None:
                try:
                    rows = _membership_rows(_json.loads(raw), "community_id")
                except (UnreadableResultShape, ValueError) as exc:
                    logger.warning("CDLP: Native Reader answer unreadable (%s); the Owner answers", exc)
            if rows is None:
                raw = str(
                    self._call_classmethod("Graph.KG.Algorithms", "CDLPJson", str(max_iterations))
                )
                rows = _membership_rows(_json.loads(raw) if raw else {}, "community_id")
            return IVGResult(columns=["id", "community_id"], rows=rows)
        except Exception as e:
            logger.warning("CDLP failed: %s", e)
            return IVGResult(columns=["id", "community_id"], rows=[], error=str(e)[:200])

    def execute_subgraph(
        self, seed_ids: list, k_hops: int, edge_types: list, max_nodes: int
    ) -> IVGResult:
        import json as _json

        seeds_json = _json.dumps(seed_ids)
        edge_json = _json.dumps(edge_types) if edge_types else ""

        def _owner() -> str:
            # `Graph.KG.Subgraph` owns SubgraphJson (Subgraph.cls:6). Naming
            # PageRank here made every kg_SUBGRAPH call raise <METHOD DOES NOT
            # EXIST> and, with no `error` on the way out, report an empty
            # subgraph as a successful answer.
            return str(
                self._call_classmethod(
                    "Graph.KG.Subgraph",
                    "SubgraphJson",
                    seeds_json,
                    str(k_hops),
                    edge_json,
                    str(max_nodes),
                )
            )

        try:
            payload = None
            # The reader takes no edge-type filter, so a filtered request is the Owner's.
            raw = None
            if not edge_types:
                raw = self._native_answer(
                    "subgraph",
                    lambda: self._arno_call(
                        "Graph.KG.ArnoAccel", "SubgraphNativeJson", seeds_json, str(k_hops), str(max_nodes)
                    ),
                )
            if raw is not None:
                try:
                    payload = _subgraph_payload(_json.loads(raw))
                except (UnreadableResultShape, ValueError) as exc:
                    logger.warning("Subgraph: Native Reader answer unreadable (%s); the Owner answers", exc)
            if payload is None:
                raw = _owner()
                result = _json.loads(raw) if raw else {"nodes": [], "edges": []}
                # `SubgraphJson` answers with properties and labels too (Subgraph.cls:
                # 153-156). Dropping them here made `include_properties=True` return an
                # empty map on the store path, which reads as "these nodes have no
                # properties" rather than as a discarded answer.
                payload = _subgraph_payload(result)
            nodes, edges, properties, labels = payload
            return IVGResult(
                columns=["nodes", "edges", "properties", "labels"],
                rows=[
                    [
                        _json.dumps(nodes),
                        _json.dumps(edges),
                        _json.dumps(properties),
                        _json.dumps(labels),
                    ]
                ],
            )
        except Exception as e:
            logger.warning("Subgraph failed: %s", e)
            return IVGResult(
                columns=["nodes", "edges", "properties", "labels"],
                rows=[["[]", "[]", "{}", "{}"]],
                error=str(e)[:200],
            )

    def execute_knn_vec(
        self,
        query_vector: list,
        k: int,
        label_filter: Optional[str],
        *,
        graph: Optional[str] = None,
        model_key: Optional[str] = None,
    ) -> IVGResult:
        """Nearest neighbours inside one graph (spec 227).

        ``graph`` and ``model_key`` are keyword-only and both default to ``None``,
        which means the default graph ``''`` — never every graph (FR-004).

        Scoped SQL is the only path. There is no ``kg_KNN_VEC`` ClassMethod in the
        tree: the SQL procedure ``Graph_KG.kg_KNN_VEC`` is generated DDL, and the
        ObjectScript neighbour search lives on
        ``iris.vector.graph.GraphOperators.kgKNNVEC`` over the legacy
        ``kg_NodeEmbeddings_optimized`` copy. The call this method used to attempt
        first — ``Graph.KG.TemporalIndex.kg_KNN_VEC`` — never existed in any
        release, so it only ever logged a warning before the SQL below answered.

        The table is the one the ``(graph, model)`` pair is routed to, as for
        ``kg_KNN_VEC``: ``knn_route`` resolves it (4.6.0, R073). Reading
        ``kg_NodeEmbeddings`` for every pair scored a routed graph's query against
        another table's vectors (SQLCODE -257 at another width, other vectors at the
        same one). A pair with no route answers no neighbours (FR-013).
        """
        graph_id = graph or ""
        # `str()` on a non-number produces text IRIS parses as a vector of zeros, so
        # a caller that handed over AST nodes (or strings) got a plausible-looking
        # empty answer instead of a complaint. Refuse it here.
        try:
            query_vector = [float(x) for x in query_vector]
        except (TypeError, ValueError) as e:
            msg = f"query_vector must be numeric: {e}"
            logger.warning("KNN_VEC rejected: %s", msg)
            return IVGResult(columns=["id", "score"], rows=[], error=msg[:200])
        table, dtype = f"{self._ivg_schema}.kg_NodeEmbeddings", "DOUBLE"
        if self.knn_route is not None:
            table, dtype = self.knn_route(graph_id, model_key)
            if table is None:
                return IVGResult(columns=["id", "score"], rows=[])
        cursor = self.conn.cursor()
        vec_str = ",".join(str(x) for x in query_vector)
        # `label_filter` used to be handed to the (nonexistent) ObjectScript call and
        # dropped by the SQL that actually answered, so a labelled search scored every
        # node in the graph. Join rdf_labels instead, in the graph searched: on the node
        # id alone, a node labelled only in another graph passed the filter (4.6.0, R083).
        join = ""
        params: list = [f"[{vec_str}]"]
        if label_filter:
            join = (
                f"JOIN {self._ivg_schema}.rdf_labels l ON l.s = e.node_id AND l.label = ? "
                f"AND {graph_scope_predicate('l.graph_id')} "
            )
            params.extend([label_filter, graph_id])
        params.append(graph_id)
        try:
            cursor.execute(
                # `TO_VECTOR(?)` with no dtype is `SQLCODE -259` ("vectors of
                # different datatypes") against a VECTOR(DOUBLE, n) column — at query
                # open, so it fails even on an empty table. Declaring the width from
                # the query vector also makes a wrong-width vector error (ADR-0005)
                # rather than score a reshaped value.
                f"SELECT TOP {k} e.node_id, "
                f"VECTOR_COSINE(e.emb, TO_VECTOR(?, {dtype}, {len(query_vector)})) AS score "
                f"FROM {table} e "
                f"{join}"
                # A node can be registered before it is embedded, and
                # `VECTOR_COSINE` of a NULL vector is NULL, which `TOP k … ORDER BY
                # score DESC` returns like any other row — `float(None)` then raised
                # and the whole search reported zero neighbours.
                f"WHERE e.emb IS NOT NULL AND {graph_scope_predicate('e.graph_id')} "
                f"ORDER BY score DESC",
                params,
            )
            rows = [
                [r[0], float(r[1])] for r in cursor.fetchall() if r[1] is not None
            ]
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.warning("KNN_VEC scoped SQL failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    # ── Temporal Edges ────────────────────────────────────────────────────────

    def write_temporal_edge(
        self,
        source_id: str,
        predicate: str,
        target_id: str,
        timestamp: int,
        weight: float = 1.0,
        attrs: Optional[dict] = None,
        upsert: bool = False,
        suppress_reverse_index: bool = False,
        mode: str = "",
        graph: Optional[str] = None,
    ) -> IVGResult:
        import json as _json

        attrs_json = _json.dumps(attrs) if attrs else ""
        graph_id = validate_graph_name(graph)
        try:
            self._call_classmethod(
                "Graph.KG.TemporalIndex",
                "InsertEdge",
                graph_id,          # first param: graphId (spec-223)
                source_id,
                predicate,
                target_id,
                str(timestamp),
                str(weight),
                attrs_json,
                str(int(upsert)),
                str(int(suppress_reverse_index)),
                mode,
            )
        except Exception as e:
            logger.warning("write_temporal_edge failed: %s", e)
            return IVGResult(columns=[], rows=[], error=str(e)[:200])
        return IVGResult(columns=[], rows=[])

    def delete_temporal_edge(
        self,
        source_id: str,
        predicate: str,
        target_id: str,
        timestamp: int,
        graph: Optional[str] = None,
    ) -> bool:
        """Remove one timestamped edge and everything InsertEdge derived from it.

        Returns False when no edge existed at those coordinates, so a caller can
        tell a delete from a no-op. Errors propagate rather than being logged and
        swallowed: a partial temporal delete leaves aggregates describing edges
        that are gone, and that is not something to discover later from a drift
        report.
        """
        result = self._call_classmethod(
            "Graph.KG.TemporalIndex",
            "DeleteTemporalEdge",
            validate_graph_name(graph),
            str(int(timestamp)),
            source_id,
            predicate,
            target_id,
        )
        return str(result) == "1"

    def bulk_write_temporal_edges(
        self, edges: list, upsert: bool = False, suppress_reverse_index: bool = False,
        graph: Optional[str] = None, mode: str = "",
    ) -> IVGResult:
        import json as _json

        batch = []
        for edge in edges:
            item = {
                "s": edge.get("source", ""),
                "p": edge.get("predicate", ""),
                "o": edge.get("target", ""),
                "ts": edge.get("timestamp", 0),
                "w": float(edge.get("weight", 1.0)),
            }
            if suppress_reverse_index:
                item["sri"] = 1
            attrs = edge.get("attrs") or {}
            if attrs:
                item["attrs"] = attrs
            batch.append(item)

        columns = ["inserted", "collisions", "skipped"]
        if not batch:
            return IVGResult(columns=columns, rows=[[0, 0, 0]])

        try:
            # "written:collisions:skipped" (spec 254); a bare integer from a class older than it.
            raw = str(
                self._call_classmethod(
                    "Graph.KG.TemporalIndex",
                    "BulkInsertCounts",
                    validate_graph_name(graph),
                    _json.dumps(batch),
                    str(int(upsert)),
                    mode,
                )
            )
            counts = [int(x) for x in raw.split(":")] + [0, 0]
            return IVGResult(columns=columns, rows=[counts[:3]])
        except Exception as e:
            logger.warning("BulkInsert failed, falling back to per-edge: %s", e)
            inserted = 0
            first_error = None
            for edge in edges:
                # Keyword form from here on: the positional call dropped `graph`
                # and `mode`, so the fallback wrote to the default graph in
                # insert-only mode and still reported every edge as written.
                r = self.write_temporal_edge(
                    edge.get("source", ""),
                    edge.get("predicate", ""),
                    edge.get("target", ""),
                    edge.get("timestamp", 0),
                    float(edge.get("weight", 1.0)),
                    edge.get("attrs") or {},
                    upsert=upsert,
                    suppress_reverse_index=suppress_reverse_index,
                    mode=mode,
                    graph=graph,
                )
                if not r.error:
                    inserted += 1
                elif first_error is None:
                    first_error = r.error
            # InsertEdge returns 1 for a write, a skip and a collision alike, so this path counts
            # edges accepted without an error and cannot report collisions. An edge refused carries its error up
            # (R203): a count alone read as "written fewer", not "refused".
        return IVGResult(columns=["inserted"], rows=[[inserted]], error=first_error)

    def purge_bucket_range(
        self, bucket_start: int, bucket_end: int, graph: Optional[str] = None
    ) -> int:
        result = self._call_classmethod(
            "Graph.KG.TemporalIndex", "PurgeBucketRange",
            validate_graph_name(graph),
            bucket_start, bucket_end,
        )
        return int(str(result))

    def purge_raw_before(
        self, ts_end: int, ts_start: int = 0, graph: Optional[str] = None,
        retire_bare_nodes: bool = False,
    ):
        from iris_vector_graph._engine.temporal import PurgeResult

        ts_start = max(0, int(ts_start))
        if retire_bare_nodes:
            result = self._call_classmethod(
                "Graph.KG.TemporalIndex", "PurgeRawBefore",
                validate_graph_name(graph), str(ts_end), str(ts_start), 1,
            )
        else:
            # three arguments: a deployed class older than the option never sees a fourth
            result = self._call_classmethod(
                "Graph.KG.TemporalIndex", "PurgeRawBefore",
                validate_graph_name(graph), str(ts_end), str(ts_start),
            )
        # "deleted", "deleted:skipped" or, with retire_bare_nodes, "deleted:skipped:retired"
        parts = str(result).split(":")
        nums = [int(p) if p else 0 for p in parts] + [0, 0]
        return PurgeResult(nums[0], nums[1], nums[2])

    def erase_bare_nodes(
        self, graph: Optional[str] = None, batch_size: int = 10000,
        dry_run: bool = False, min_age_seconds: int = 0,
    ) -> Any:
        """Delete the bare nodes of one graph (see ``Graph.KG.Eraser.EraseBareNodes``)."""
        from iris_vector_graph._engine.temporal import BareNodesResult

        _guard = getattr(self, "_ledger_guard", None)
        if _guard is not None and not dry_run:
            _guard.check_structural_write("store.erase_bare_nodes")
        canonical = validate_graph_name(graph)
        if not self._has_eraser_method("EraseBareNodes"):
            raise RuntimeError(
                "Graph.KG.Eraser.EraseBareNodes is not deployed in this namespace; "
                "load the iris_src classes first"
            )
        self._commit_for_eraser("store.erase_bare_nodes")
        raw = self._call_classmethod(
            "Graph.KG.Eraser", "EraseBareNodes", canonical,
            max(1, int(batch_size)), 1 if dry_run else 0, max(0, int(min_age_seconds)),
        )
        d = json.loads(str(raw))
        return BareNodesResult(
            int(d["bare"]), int(d["erased"]), int(d["kept"]), int(d["batches"]), bool(d["dry_run"])
        )

    def intern_label_set(self, attrs_json: str) -> str:
        result = self._call_classmethod("Graph.KG.TemporalIndex", "InternLabelSet", attrs_json)
        if result is None:
            return ""
        return str(result)

    def resolve_label_set(self, hash_hex: str) -> str:
        result = self._call_classmethod("Graph.KG.TemporalIndex", "ResolveLabelSet", hash_hex)
        if result is None:
            return ""
        return str(result)

    def execute_temporal_window_query(
        self, source_id: str, predicate: str, ts_start: int, ts_end: int,
        direction: str = "out", graph: Optional[str] = None,
    ) -> IVGResult:
        import json as _json

        graph_id = graph if graph is not None else ""
        try:
            method = "QueryWindowInbound" if direction == "in" else "QueryWindow"
            result_json = str(
                self._call_classmethod(
                    "Graph.KG.TemporalIndex",
                    method,
                    graph_id,          # first param: graphId (spec-223)
                    source_id,
                    predicate,
                    str(ts_start),
                    str(ts_end),
                )
            )
            edges = _json.loads(result_json) if result_json else []
            rows = []
            for edge in edges:
                rows.append(
                    [
                        edge.get("s", source_id),
                        edge.get("p", predicate),
                        edge.get("o", ""),
                        edge.get("ts", 0),
                        edge.get("w", edge.get("weight", 1.0)),
                    ]
                )
            return IVGResult(
                columns=["source", "predicate", "target", "timestamp", "weight"],
                rows=rows,
            )
        except Exception as e:
            # Spec 255 FR-006: the error travels with the result. Answering no rows without it made a window past
            # QueryWindow's string limit look empty.
            logger.warning("execute_temporal_window_query failed: %s", e)
            return IVGResult(
                columns=["source", "predicate", "target", "timestamp", "weight"],
                rows=[],
                error=str(e) or type(e).__name__,
            )

    def execute_temporal_cypher(
        self,
        source_id: str,
        predicates: list,
        ts_start: int,
        ts_end: int,
        direction: str,
        max_hops: int,
        graph: Optional[str] = None,
    ) -> IVGResult:
        """Hop-by-hop walk of the temporal window, scoped to one graph.

        There is no `QueryWindowBFS` ClassMethod — not in this release and not in
        any earlier one — so this method used to raise on every call and answer
        every temporal Cypher window query with zero rows. The window readers that
        do exist are single-hop and graph-scoped (`TemporalIndex.QueryWindow` and
        `QueryWindowInbound`, both taking `graphId` first per spec 223), so the hop
        loop lives here.

        ``graph`` defaults to the default graph ``''`` — never every graph.
        """
        import json as _json

        graph_id = validate_graph_name(graph)
        preds = list(predicates) if predicates else [""]  # "" means any predicate
        dirs = ["out", "in"] if direction in ("both", "any") else [direction or "out"]
        readers = {"out": "QueryWindow", "in": "QueryWindowInbound"}

        visited = {source_id}
        frontier = [source_id]
        rows: list[list] = []
        try:
            for hop in range(1, max(int(max_hops), 0) + 1):
                next_frontier: list[str] = []
                for node in frontier:
                    for d in dirs:
                        reader = readers.get(d, "QueryWindow")
                        for pred in preds:
                            raw = str(
                                self._call_classmethod(
                                    "Graph.KG.TemporalIndex",
                                    reader,
                                    graph_id,
                                    node,
                                    pred,
                                    str(ts_start),
                                    str(ts_end),
                                )
                            )
                            edges = _json.loads(raw) if raw else []
                            for edge in edges:
                                # QueryWindow yields the edge as (s, p, o); the
                                # neighbour is the far end for the direction read.
                                nbr = edge.get("o") if d == "out" else edge.get("s")
                                if not nbr or nbr in visited:
                                    continue
                                visited.add(nbr)
                                next_frontier.append(nbr)
                                rows.append(
                                    [nbr, hop, edge.get("p", ""), edge.get("ts", 0)]
                                )
                frontier = next_frontier
                if not frontier:
                    break
            return IVGResult(columns=["id", "hops", "pred", "ts"], rows=rows)
        except Exception as e:
            logger.warning("execute_temporal_cypher failed: %s", e)
            return IVGResult(
                columns=["id", "hops", "pred", "ts"], rows=[], error=str(e)[:200]
            )

    def get_temporal_aggregate(
        self, source_id: str, predicate: str, metric: str, ts_start: int, ts_end: int,
        graph: Optional[str] = None,
    ) -> IVGResult:
        try:
            val = self._call_classmethod(
                "Graph.KG.TemporalIndex",
                "GetAggregate",
                validate_graph_name(graph),
                source_id,
                predicate,
                metric,
                str(ts_start),
                str(ts_end),
            )
            return IVGResult(columns=["value"], rows=[[float(str(val))]])
        except Exception as e:
            logger.warning("get_temporal_aggregate failed: %s", e)
            return IVGResult(columns=["value"], rows=[[0.0]])

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def capabilities(self) -> dict:
        caps = dict(_FULL_CAPABILITIES)
        if self._detect_arno():
            caps["bfs_arno"] = True
            # 4.6.0 R284: what the Rust callout answers (ArnoAccel.PPRNativeJson, WCCNativeJson). "algorithms" is
            # NKGAccel's own list, which held a "ppr" it no longer has and never held "wcc"
            rust = self._arno_capabilities.get("rust_algorithms", [])
            caps["ppr_arno"] = "ppr" in rust
            caps["wcc_arno"] = "wcc" in rust
        return caps

    def _count_query(self, sql: str, params: list = None) -> IVGResult:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params or [])
            row = cursor.fetchone()
            return IVGResult(columns=["count"], rows=[[int(row[0]) if row else 0]])
        except Exception as e:
            return IVGResult(columns=["count"], rows=[[0]], error=str(e)[:200])

    def _distinct_query(self, sql: str, col: str) -> IVGResult:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql)
            return IVGResult(columns=[col], rows=[[r[0]] for r in cursor.fetchall()])
        except Exception as e:
            return IVGResult(columns=[col], rows=[], error=str(e)[:200])

    def get_node_count(self, label: Optional[str] = None) -> IVGResult:
        if label:
            return self._count_query(
                f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_labels WHERE label = ?", [label]
            )
        return self._count_query(f"SELECT COUNT(*) FROM {self._ivg_schema}.nodes")

    def get_edge_count(self, predicate: Optional[str] = None) -> IVGResult:
        if predicate:
            return self._count_query(
                f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges WHERE p = ?", [predicate]
            )
        return self._count_query(f"SELECT COUNT(*) FROM {self._ivg_schema}.rdf_edges")

    def get_labels(self) -> IVGResult:
        return self._distinct_query(
            f"SELECT DISTINCT label FROM {self._ivg_schema}.rdf_labels ORDER BY label", "label"
        )

    def get_relationship_types(self) -> IVGResult:
        return self._distinct_query(
            f"SELECT DISTINCT p FROM {self._ivg_schema}.rdf_edges ORDER BY p", "relationshipType"
        )

    def _hnsw_indexes(self, table_name: str) -> List[tuple]:
        """The HNSW indexes IRIS holds on ``table_name``, as ``(name, properties)``.

        Delegates to :meth:`GraphSchema.hnsw_indexes`, the one owner of that dictionary
        read, so this report and ``SHOW INDEXES`` cannot disagree.
        """
        from iris_vector_graph.schema import GraphSchema

        cursor = self.conn.cursor()
        try:
            return GraphSchema.hnsw_indexes(cursor, table_name)
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def list_indexes(self) -> IVGResult:
        cols = ["name", "type", "state"]
        rows = []

        # HNSW vector indexes as the class dictionary holds them, and nothing when it holds
        # none (spec 226, FR-018). This used to append one `hnsw_node_embeddings` row
        # unconditionally, `ONLINE` when `Graph_KG.kg_NodeEmbeddings_optimized` had rows and
        # `NOT_BUILT` when it did not. A row count is not an index, and no index was ever
        # behind that row: both embedding tables key on a VARCHAR `id`, and IRIS refuses an
        # ANN index there (ERROR #7222).
        for table in (f"{self._ivg_schema}.kg_NodeEmbeddings", f"{self._ivg_schema}.kg_NodeEmbeddings_optimized"):
            for index_name, _properties in self._hnsw_indexes(table):
                rows.append([index_name, "VECTOR(HNSW)", "ONLINE"])

        from iris_vector_graph.schema import GraphSchema

        for name, index_type in GraphSchema.global_indexes(self._call_classmethod):
            rows.append([name, index_type, "ONLINE"])

        rows.append(["pk_nodes", "UNIQUE", "ONLINE"])
        rows.append(["pk_rdf_edges", "UNIQUE", "ONLINE"])
        return IVGResult(columns=cols, rows=rows)

    def server_info(self) -> IVGResult:
        # Before 4.6.0 R222: `SELECT %Version.GetVersion()`, which IRIS refuses, so always "unknown".
        from iris_vector_graph.schema import _call_classmethod

        try:
            iris_ver = str(_call_classmethod(self.conn, "%SYSTEM.Version", "GetVersion"))
        except Exception as e:
            logger.warning("server_info: could not read the IRIS version: %s", e)
            iris_ver = "unknown"
        try:
            from importlib.metadata import version as pkg_version

            ivg_ver = pkg_version("iris-vector-graph")
        except Exception:
            ivg_ver = "unknown"
        return IVGResult(
            columns=["iris_version", "ivg_version"],
            rows=[[iris_ver, ivg_ver]],
        )

    def execute_degree_centrality(self, direction: str, predicate: str, top_k: int, graph: str = "") -> IVGResult:
        import json as _json

        try:
            # The graph reaches the class only when it is named: DegreeCentralityJson read the default graph
            # whatever was asked (4.6.0 R144), and the default graph's call keeps its 3.x shape.
            graph_args = (graph,) if graph else ()
            raw = str(
                self._call_classmethod(
                    "Graph.KG.Centrality",
                    "DegreeCentralityJson",
                    direction,
                    predicate,
                    str(top_k),
                    *graph_args,
                )
            )
            results = _json.loads(raw) if raw else []
            rows = [
                [r.get("id", ""), float(r.get("score", 0)), int(r.get("degree", 0))]
                for r in results
            ]
            return IVGResult(columns=["id", "score", "degree"], rows=rows)
        except Exception as e:
            err_str = str(e)
            if "CLASS DOES NOT EXIST" in err_str or "DBSRV" in err_str:
                logger.info(
                    "DegreeCentrality classMethodValue blocked by Bug S; "
                    "falling back to gref-direct ^KG iteration"
                )
                try:
                    return self._degree_centrality_gref_fallback(direction, predicate, top_k, graph)
                except Exception as fallback_err:
                    logger.warning("DegreeCentrality gref fallback also failed: %s", fallback_err)
                    return IVGResult(
                        columns=["id", "score", "degree"],
                        rows=[],
                        error=str(fallback_err)[:200],
                    )
            logger.warning("DegreeCentrality failed: %s", e)
            return IVGResult(columns=["id", "score", "degree"], rows=[], error=err_str[:200])

    def _degree_centrality_gref_fallback(
        self, direction: str, predicate: str, top_k: int, graph: str = ""
    ) -> IVGResult:
        """Bug S workaround via LazyKG adapter (v1.99.0 retrofit).

        Reads ^KG directly via the IRIS Native API rather than
        ##class(Graph.KG.Centrality).DegreeCentralityJson, which fails with
        <CLASS DOES NOT EXIST> when %SYS.DBSRV's class cache rejects user-class
        XDCall lookups. Native API global access bypasses DBSRV entirely.
        See ENGINEERING_DEBT.md Bug S for diagnosis.
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=(direction in ("in", "both")), graph=graph or None)

        all_nodes = list(lkg.iter_nodes())
        node_count = len(all_nodes)
        if node_count == 0:
            return IVGResult(columns=["id", "score", "degree"], rows=[])
        norm = 1.0 / max(node_count - 1, 1)

        scored = []
        for node in all_nodes:
            if predicate == "":
                if direction == "out":
                    deg = lkg.degree(node)
                elif direction == "in":
                    deg = lkg.in_degree(node)
                else:
                    deg = lkg.degree(node) + lkg.in_degree(node)
            else:
                if direction == "out":
                    deg = lkg.degree_for_predicate(node, predicate)
                elif direction == "in":
                    deg = lkg.in_degree_for_predicate(node, predicate)
                else:
                    deg = lkg.degree_for_predicate(node, predicate) + lkg.in_degree_for_predicate(
                        node, predicate
                    )

            scored.append((node, deg * norm, deg))

        scored.sort(key=lambda r: -r[1])
        if top_k > 0:
            scored = scored[:top_k]

        return IVGResult(
            columns=["id", "score", "degree"],
            rows=[[nid, sc, dg] for (nid, sc, dg) in scored],
        )

    def execute_betweenness(
        self,
        sample_size: int,
        direction: str,
        max_hops: int,
        top_k: int,
        mem_budget_mb: int,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> IVGResult:
        try:
            return self._betweenness_gref(
                sample_size,
                direction,
                max_hops,
                top_k,
                mem_budget_mb,
                progress_callback,
            )
        except Exception as e:
            logger.warning("Betweenness failed: %s", e)
            return IVGResult(
                columns=["id", "score"],
                rows=[],
                error=str(e)[:200],
            )

    def execute_betweenness_neighborhood(
        self,
        seed: str,
        hops: int,
        sample_size: int,
        top_k: int,
    ) -> IVGResult:
        try:
            import json as _json

            import iris as _iris

            iris_obj = self._iris_obj()
            # the ObjectScript tier answers, Arno loaded or not (4.6.0 R050), so there is no fallback to warn of
            raw = str(
                iris_obj.classMethodValue(
                    "Graph.KG.NKGAccel",
                    "BetweennessNeighborhood",
                    seed,
                    hops,
                    sample_size,
                    top_k,
                )
            )
            if raw.startswith("OK:"):
                rows = [
                    [r.get("id", ""), float(r.get("score", 0.0))]
                    for r in sorted(
                        _json.loads(_fix_iris_json(raw[3:])), key=lambda x: -x.get("score", 0)
                    )
                ]
                if top_k > 0:
                    rows = rows[:top_k]
                return IVGResult(columns=["id", "score"], rows=rows)
            return IVGResult(columns=["id", "score"], rows=[], error=raw)
        except Exception as e:
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def _betweenness_gref(
        self,
        sample_size: int,
        direction: str,
        max_hops: int,
        top_k: int,
        mem_budget_mb: int,
        progress_callback: Optional[Callable[[int, int], None]],
        lkg=None,
    ) -> IVGResult:
        """Brandes (2001) Betweenness.

        Dispatch order:
          1. OS Brandes (%SYSTEM.WorkMgr 8-way parallel) — ~830ms ER(2000); exact, every
             direction and hop limit. Arno's kg_betweenness_global_v (~8ms) scores the graph as
             undirected and is not asked (tests/unit/test_411_betweenness_direction.py).
          2. Python LazyKG Brandes — a namespace where a named graph holds
             edges, or when tier 1 fails; very slow

        Tier 1 reads ^NKG, every graph's edges merged; LazyKG reads the default graph's ^KG. Where a named graph
        holds edges the two differ, and the answer is the default graph's (4.6.0 R184).
        """
        named = self._named_graph_with_edges()
        # Tier 1 honours direction ("out" and "in" score the same; "both" the undirected graph) and max_hops (4.6.0
        # R232: it walked out-edges with no limit, so "both" came here and max_hops was dropped). The skip is by design
        # and says nothing; a tier 1 that fails says so (R257).
        if named is None:
            tier1 = self._betweenness_tier1(sample_size, top_k, progress_callback, direction, max_hops)
            if isinstance(tier1, IVGResult):
                return tier1
            logger.warning(
                "betweenness_centrality: the ObjectScript tier (Graph.KG.NKGAccel.BetweennessGlobal) failed: %s; "
                "answering from the Python walk over ^KG, which is much slower",
                tier1,
            )
        return self._betweenness_lazy(sample_size, direction, max_hops, top_k, mem_budget_mb, progress_callback)

    def _betweenness_tier1(self, sample_size, top_k, progress_callback, direction="out", max_hops=0):
        """Tier 1's answer as an IVGResult, or the text of its failure (an exception, or an answer not ``OK:``)."""
        try:
            iris_obj = self._iris_obj()
            # The server takes sampleSize sources when 0 < sampleSize < n, else
            # maxSources when 0 < maxSources < n, else all n. A fixed maxSources=200
            # made sample_size >= n a 200-source approximation, not exact Brandes, so
            # the cap is the caller's sample_size when one is named; 0 keeps 200.
            max_sources = sample_size if sample_size > 0 else _BETWEENNESS_DEFAULT_SOURCES
            raw = str(
                iris_obj.classMethodValue(
                    "Graph.KG.NKGAccel",
                    "BetweennessGlobal",
                    sample_size,
                    top_k,
                    max_sources,
                    direction,
                    max_hops,
                )
            )
            if raw.startswith("OK:"):
                import json as _json

                parsed = _json.loads(raw[3:])
                rows = [
                    [r.get("id", ""), float(r.get("score", 0.0))]
                    for r in sorted(parsed, key=lambda x: -x.get("score", 0))
                ]
                if top_k > 0:
                    rows = rows[:top_k]
                if progress_callback:
                    total = len(parsed) or 1
                    progress_callback(total, total)
                return IVGResult(columns=["id", "score"], rows=rows)
            return f"it answered {raw[:200]!r}"
        except Exception as exc:
            return f"{type(exc).__name__}: {str(exc)[:300]}"

    def _betweenness_lazy(self, sample_size, direction, max_hops, top_k, mem_budget_mb, progress_callback):
        """Tier 2: Brandes in Python over ^KG (LazyKG)."""
        from iris_vector_graph.stores.lazy_kg import LazyKG

        iris_inst = self._iris_obj()
        # every node with an edge, sinks too, as ^NKG holds them: the population a sample is drawn from, and N
        lkg = LazyKG(self.conn, include_sinks=True)

        all_nodes = list(lkg.iter_nodes())
        if not all_nodes:
            return IVGResult(columns=["id", "score"], rows=[])

        sources = _betweenness_sources(all_nodes, sample_size)
        scaling = len(all_nodes) / len(sources)

        bc: Dict[str, float] = {n: 0.0 for n in all_nodes}
        skipped_sources = 0
        budget_subscripts = max(1, mem_budget_mb) * 10485

        if direction == "out":
            forward = lkg.out_neighbors
        elif direction == "in":
            forward = lkg.in_neighbors
        else:

            def forward(n: str) -> List[str]:
                seen = set()
                combined = []
                for x in list(lkg.out_neighbors(n)) + list(lkg.in_neighbors(n)):
                    if x not in seen:
                        seen.add(x)
                        combined.append(x)
                return combined

        n_total = len(sources)
        for idx, s in enumerate(sources):
            stack: List[str] = []
            preds: Dict[str, List[str]] = {}
            sigma: Dict[str, int] = {s: 1}
            dist: Dict[str, int] = {s: 0}
            queue = [s]

            preds_count = 0
            budget_exceeded = False

            while queue:
                next_queue: List[str] = []
                for v in queue:
                    stack.append(v)
                    if max_hops > 0 and dist[v] >= max_hops:
                        continue
                    for w in forward(v):
                        if w not in dist:
                            dist[w] = dist[v] + 1
                            sigma[w] = 0
                            preds[w] = []
                            next_queue.append(w)
                        if dist[w] == dist[v] + 1:
                            sigma[w] += sigma.get(v, 0)
                            preds.setdefault(w, []).append(v)
                            preds_count += 1
                            if preds_count > budget_subscripts:
                                budget_exceeded = True
                                break
                    if budget_exceeded:
                        break
                if budget_exceeded:
                    break
                queue = next_queue

            if budget_exceeded:
                skipped_sources += 1
                try:
                    import datetime as _dt

                    ts_key = _dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
                    iris_inst.set(
                        "BC mem budget exceeded", "^IVG.warnings", "centrality", ts_key, s
                    )
                except Exception:
                    pass
                if progress_callback:
                    progress_callback(idx + 1, n_total)
                continue

            delta: Dict[str, float] = {}
            while stack:
                w = stack.pop()
                for v in preds.get(w, ()):
                    if sigma.get(w, 0) > 0:
                        delta[v] = delta.get(v, 0.0) + (sigma.get(v, 0) / sigma[w]) * (
                            1.0 + delta.get(w, 0.0)
                        )
                if w != s:
                    bc[w] = bc.get(w, 0.0) + delta.get(w, 0.0)

            if progress_callback:
                progress_callback(idx + 1, n_total)

        # Undirected, Brandes from every source meets each pair of endpoints from both ends; networkx and igraph
        # count it once (4.6.0 R216: every "both" score was twice networkx's).
        if direction == "both":
            scaling /= 2.0
        if scaling != 1.0:
            for k in bc:
                bc[k] *= scaling

        rows = _ranked(bc.items(), top_k)

        if skipped_sources > 0:
            rows.append(["_meta", {"_approximate": True, "_skipped_sources": skipped_sources}])

        return IVGResult(columns=["id", "score"], rows=rows)

    def execute_closeness(
        self,
        formula: str,
        direction: str,
        max_hops: int,
        top_k: int,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> IVGResult:
        try:
            return self._closeness_gref(formula, direction, max_hops, top_k, progress_callback)
        except Exception as e:
            logger.warning("Closeness failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def _closeness_serverside(self, formula: str, top_k: int) -> Optional[IVGResult]:
        """Server-side Graph.KG.Communities.ClosenessJsonPy — full-graph harmonic/
        classical closeness via igraph in IRIS embedded Python (native multi-core C,
        no data transfer). Used only for max_hops=0 (full closeness). Returns None
        (caller falls back to the ObjectScript/LazyKG BFS) when igraph is absent.
        """
        try:
            import json as _json

            iris_obj = self._iris_obj()
            raw = str(
                iris_obj.classMethodValue(
                    "Graph.KG.Communities", "ClosenessJsonPy", formula, int(top_k)
                )
            )
            if not raw.startswith("OK:"):
                return None
            data = _json.loads(raw[3:])
            rows = [[r.get("id", ""), float(r.get("score", 0.0))] for r in data]
            return IVGResult(columns=["id", "score"], rows=rows)
        except Exception as e:
            logger.debug("Closeness server-side path unavailable, falling back: %s", str(e)[:120])
            return None

    def _closeness_gref(
        self,
        formula: str,
        direction: str,
        max_hops: int,
        top_k: int,
        progress_callback: Optional[Callable[[int, int], None]],
        lkg=None,
    ) -> IVGResult:
        """Closeness Centrality via LazyKG-backed Native API (Bug S workaround).

        Per-source BFS sums distances. Two formulas:
        - "harmonic" (default): score(v) = sum(1/d(v,u)) — robust to disconnection
        - "classical": score(v) = (n-1)/sum(d(v,u)) — returns 0 if any unreachable

        Harmonic is the raw sum, as the ObjectScript tiers (ClosenessGlobal, ClosenessGlobalMSBFS) answer it and
        networkx.harmonic_centrality does. This BFS divided it by n - 1, so the scale of an answer depended on which
        tier gave it, and since 4.6.0 R184 (this BFS answers where a named graph holds edges) on the namespace's
        other graphs.

        See spec 162 clarification 2 + research.md R2 (matches networkx.harmonic_centrality).
        v1.99.0: refactored from inline ^KG walk to LazyKG adapter.
        v2.0.0 spec 168: try ClosenessGlobal ObjectScript path first (1 round-trip).
        4.6.0 R184: not where a named graph holds edges, as those paths read ^NKG (every graph's edges merged); the
        BFS below reads the default graph's ^KG.
        """
        named = self._named_graph_with_edges()
        try:
            import json as _json

            import iris as _iris

            if named is not None:
                raise LookupError(f"^NKG holds graph {named!r}'s edges too")
            iris_obj = self._iris_obj()
            for _method in ("ClosenessGlobalMSBFS", "ClosenessGlobal"):
                raw = str(
                    iris_obj.classMethodValue(
                        "Graph.KG.NKGAccel",
                        _method,
                        formula,
                        direction,
                        max_hops,
                        top_k,
                    )
                )
                if raw.startswith("OK:"):
                    rows = [
                        [r.get("id", ""), float(r.get("score", 0.0))] for r in _json.loads(raw[3:])
                    ]
                    return IVGResult(columns=["id", "score"], rows=rows)
        except Exception:
            pass

        from iris_vector_graph.stores.lazy_kg import LazyKG

        # sinks too, whatever the direction: a sink scores 0 out but is one of the n in classical's n - 1, and the
        # ObjectScript tiers list it (4.6.0 R050: without it, classical "out" divided by one node too few)
        lkg = lkg if lkg is not None else LazyKG(self.conn, include_sinks=True)

        all_nodes = list(lkg.iter_nodes())
        if not all_nodes:
            return IVGResult(columns=["id", "score"], rows=[])

        if direction == "out":
            forward = lkg.out_neighbors
        elif direction == "in":
            forward = lkg.in_neighbors
        else:

            def forward(n: str) -> List[str]:
                seen_n = set()
                combined = []
                for x in list(lkg.out_neighbors(n)) + list(lkg.in_neighbors(n)):
                    if x not in seen_n:
                        seen_n.add(x)
                        combined.append(x)
                return combined

        n_total = len(all_nodes)
        scored: List[tuple] = []

        for idx, source in enumerate(all_nodes):
            dist: Dict[str, int] = {source: 0}
            queue = [source]
            while queue:
                next_queue: List[str] = []
                for v in queue:
                    if max_hops > 0 and dist[v] >= max_hops:
                        continue
                    for w in forward(v):
                        if w not in dist:
                            dist[w] = dist[v] + 1
                            next_queue.append(w)
                queue = next_queue

            reachable = [(node, d) for node, d in dist.items() if node != source and d > 0]

            if formula == "classical":
                if len(reachable) < n_total - 1:
                    score = 0.0
                else:
                    total_dist = sum(d for _, d in reachable)
                    score = (n_total - 1) / total_dist if total_dist > 0 else 0.0
            else:
                harmonic_sum = sum(1.0 / d for _, d in reachable)
                score = harmonic_sum

            scored.append((source, score))
            if progress_callback:
                progress_callback(idx + 1, n_total)

        return IVGResult(columns=["id", "score"], rows=_ranked(scored, top_k))

    def execute_eigenvector(
        self,
        max_iter: int,
        tol: float,
        top_k: int,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> IVGResult:
        try:
            return self._eigenvector_gref(max_iter, tol, top_k, progress_callback)
        except Exception as e:
            logger.warning("Eigenvector failed: %s", e)
            return IVGResult(columns=["id", "score"], rows=[], error=str(e)[:200])

    def _eigenvector_gref(
        self,
        max_iter: int,
        tol: float,
        top_k: int,
        progress_callback: Optional[Callable[[int, int], None]],
        lkg=None,
    ) -> IVGResult:
        """Eigenvector Centrality via LazyKG-backed Native API (Bug S workaround).

        Power iteration over RAW adjacency matrix A (NOT the transition matrix
        M = D^-1 A used by PageRank). For each iteration: x' = A·x, L2-normalize.
        Matches networkx.eigenvector_centrality_numpy semantics.

        See spec 162 research.md R2 (Eigenvector ≠ PageRank with α=1).
        v1.99.0: refactored from inline ^KG walk to LazyKG adapter.
        v2.0.0 spec 169: try EigenvectorGlobal ObjectScript path first (1 round-trip).
        4.6.0 R184: not where a named graph holds edges, as it reads ^NKG (every graph's edges merged); the power
        iteration below reads the default graph's ^KG.
        """
        # where a named graph has edges ^NKG holds them too: the default graph's ^KG is walked below (R184)
        if self._named_graph_with_edges() is None:
            try:
                raw = str(
                    self._iris_obj().classMethodValue("Graph.KG.NKGAccel", "EigenvectorGlobal", max_iter, tol, top_k)
                )
                if raw.startswith("OK:"):
                    import json as _json

                    rows = [
                        [r.get("id", ""), float(r.get("score", 0.0))]
                        for r in sorted(_json.loads(raw[3:]), key=lambda x: -x.get("score", 0))
                    ]
                    if top_k > 0:
                        rows = rows[:top_k]
                    return IVGResult(columns=["id", "score"], rows=rows)
            except Exception as exc:
                # said, not swallowed: an unreadable answer (4.6.0 R195: `.577` scores, no JSON) sent every call to
                # the walk below without a word
                logger.warning("Eigenvector: EigenvectorGlobal's answer was not used (%s); walking ^KG", exc)

        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = lkg if lkg is not None else LazyKG(self.conn, include_sinks=True)

        all_nodes = list(lkg.iter_nodes())
        n = len(all_nodes)
        if n == 0:
            return IVGResult(columns=["id", "score"], rows=[])

        out_neighbors: Dict[str, List[str]] = {
            node: list(lkg.out_neighbors(node)) for node in all_nodes
        }

        x: Dict[str, float] = {node: 1.0 / n for node in all_nodes}

        for it in range(max_iter):
            x_new: Dict[str, float] = {node: 0.0 for node in all_nodes}
            for u, neighbors in out_neighbors.items():
                xu = x.get(u, 0.0)
                if xu == 0.0:
                    continue
                for w in neighbors:
                    if w in x_new:
                        x_new[w] += xu

            norm_sq = sum(v * v for v in x_new.values())
            if norm_sq <= 0.0:
                break
            norm = norm_sq**0.5
            for k in x_new:
                x_new[k] /= norm

            max_delta = max(abs(x_new[k] - x[k]) for k in x)
            x = x_new

            if progress_callback:
                progress_callback(it + 1, max_iter)

            if max_delta < tol:
                break

        return IVGResult(columns=["id", "score"], rows=_ranked(x.items(), top_k))

    def execute_leiden(
        self,
        max_levels: int,
        gamma: float,
        tol: float,
        top_k: int,
        mem_budget_mb: int,
        random_seed: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> IVGResult:
        # The server-side path reads ^NKG, every graph's edges merged; Arno's upload and LazyKG read the default
        # graph's ^KG. Where a named graph holds edges only they answer the default graph (4.6.0 R184).
        if self._named_graph_with_edges() is None:
            srv = self._leiden_serverside(gamma, top_k, random_seed)
            if srv is not None:
                return srv
        if not arno_disabled():
            try:
                return self._leiden_arno(max_levels, gamma, tol, top_k, mem_budget_mb, random_seed)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError

                if not isinstance(e, ArnoError):
                    logger.warning("Leiden arno path raised non-ArnoError: %s", e)
        return self._leiden_lazykg(
            max_levels, gamma, tol, top_k, mem_budget_mb, random_seed, progress_callback
        )

    def _leiden_serverside(
        self, gamma: float, top_k: int, random_seed: Optional[int]
    ) -> Optional[IVGResult]:
        """Server-side Graph.KG.Communities.LeidenJsonAuto — canonical leidenalg in
        IRIS embedded Python (native multi-core, no data transfer) when igraph+
        leidenalg are installed in mgr/python; greedy ObjectScript otherwise.
        Returns None (caller falls back to LazyKG) if the path is unavailable.
        """
        try:
            import json as _json

            iris_obj = self._iris_obj()
            seed = -1 if random_seed is None else int(random_seed)
            raw = str(
                iris_obj.classMethodValue(
                    "Graph.KG.Communities",
                    "LeidenJsonAuto",
                    10,
                    float(gamma),
                    0.0001,
                    int(top_k),
                    256,
                    seed,
                )
            )
            if not raw.startswith("OK:"):
                return None
            data = _json.loads(raw[3:])
            rows = [
                [r.get("id", ""), int(r.get("community", 0)), int(r.get("size", 0))] for r in data
            ]
            return IVGResult(columns=["id", "community", "size"], rows=rows)
        except Exception as e:
            logger.debug("Leiden server-side path unavailable, falling back: %s", str(e)[:120])
            return None

    def _leiden_arno(
        self,
        max_levels: int,
        gamma: float,
        tol: float,
        top_k: int,
        mem_budget_mb: int,
        random_seed: Optional[int],
    ) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload.

        Asked at ``gamma == 1.0`` (modularity) only. Any other gamma is the Constant Potts Model (docs/COMMUNITIES.md);
        arno-callout's source runs leiden_rs's CPM there, but no container this release was tested on had Arno loaded,
        so the library's answer is unchecked and the call goes on to leidenalg (4.6.0 R231)."""
        from iris_vector_graph.stores.arno_bridge import (
            ArnoError,
            arno_call,
            build_kg_adjacency_chunked,
        )

        if abs(gamma - 1.0) > 1e-9:
            raise ArnoError(
                f"Arno's Leiden is not asked for gamma={gamma}: gamma != 1.0 is the Constant Potts Model, and Arno's "
                f"answer to it is unchecked (4.6.0 R231)"
            )

        seed_arg = -1 if random_seed is None else int(random_seed)
        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn, schema=self._ivg_schema)
        raw = arno_call(
            self.conn,
            "kg_leiden_run",
            int(max_levels),
            float(gamma),
            float(tol),
            int(top_k),
            int(mem_budget_mb),
            seed_arg,
            schema=self._ivg_schema,
        )
        import json as _json

        results = _json.loads(raw) if raw else []
        rows = [
            [r.get("id", ""), int(r.get("community", 0)), int(r.get("size", 0))] for r in results
        ]
        return IVGResult(columns=["id", "community", "size"], rows=rows)

    def _leiden_lazykg(
        self,
        max_levels: int,
        gamma: float,
        tol: float,
        top_k: int,
        mem_budget_mb: int,
        random_seed: Optional[int],
        progress_callback: Optional[Callable[[int, int], None]],
    ) -> IVGResult:
        """Spec 163 FR-025: LazyKG-backed Leiden community detection.

        Reads ^KG via LazyKG (direct gref, no class lookup), builds a symmetrized in-memory
        igraph.Graph, delegates to leidenalg (canonical Leiden, Traag 2019).

        Why leidenalg (not networkx.community.louvain_communities):
          1. networkx ships Louvain, NOT Leiden. Louvain caps at ARI ≈ 0.62
             on Zachary's karate club; Leiden routinely hits > 0.95.
          2. FR-005 explicitly requires Leiden; FR-007 requires ARI > 0.85.
          3. leidenalg is the reference implementation cited in the spec
             plan.md and used by Neo4j GDS, cdlib, and graph-tool.

        igraph + leidenalg are optional [full] extras; falls back to networkx
        Louvain if not installed (degraded quality, but produces valid output).

        Output community IDs remapped to contiguous 0..K-1 sorted by descending
        community size (community 0 = largest), per spec 163 Q3.
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes: List[str] = sorted(lkg.iter_nodes())
        if not all_nodes:
            return IVGResult(columns=["id", "community", "size"], rows=[])

        node_index: Dict[str, int] = {n: i for i, n in enumerate(all_nodes)}
        edge_set: set = set()
        for v in all_nodes:
            v_idx = node_index[v]
            out_neighbors = sorted(n for n in lkg.out_neighbors(v) if n != v and n in node_index)
            for w in out_neighbors:
                pair = (v_idx, node_index[w]) if v_idx < node_index[w] else (node_index[w], v_idx)
                edge_set.add(pair)
            in_neighbors = sorted(n for n in lkg.in_neighbors(v) if n != v and n in node_index)
            for u in in_neighbors:
                pair = (v_idx, node_index[u]) if v_idx < node_index[u] else (node_index[u], v_idx)
                edge_set.add(pair)

        if progress_callback is not None:
            progress_callback(1, 2)

        try:
            import igraph as _ig
            import leidenalg as _la

            G = _ig.Graph(n=len(all_nodes), edges=list(edge_set), directed=False)
            if abs(gamma - 1.0) < 1e-9:
                partition = _la.find_partition(
                    G,
                    _la.ModularityVertexPartition,
                    seed=random_seed if random_seed is not None else 0,
                    n_iterations=max_levels,
                )
            else:
                partition = _la.find_partition(
                    G,
                    _la.CPMVertexPartition,
                    resolution_parameter=gamma,
                    seed=random_seed if random_seed is not None else 0,
                    n_iterations=max_levels,
                )
            communities: List[List[str]] = [[all_nodes[idx] for idx in comm] for comm in partition]
        except ImportError:
            if abs(gamma - 1.0) > 1e-9:
                # networkx's Louvain `resolution` scales modularity's expected edges, another objective (4.6.0 R231)
                return IVGResult(
                    columns=["id", "community", "size"],
                    rows=[],
                    error=(
                        f"Leiden with gamma={gamma} is the Constant Potts Model (docs/COMMUNITIES.md), which needs "
                        f"python-igraph and leidenalg (the [communities] extra); networkx's Louvain, the fallback "
                        f"without them, has no CPM. Install them, or use gamma=1.0 (modularity)"
                    ),
                )
            try:
                import networkx as _nx

                graph = _nx.Graph()
                for nid in all_nodes:
                    graph.add_node(nid)
                for v_idx, w_idx in edge_set:
                    graph.add_edge(all_nodes[v_idx], all_nodes[w_idx])
                fallback_communities = _nx.community.louvain_communities(
                    graph,
                    threshold=tol,
                    seed=random_seed,
                )
                communities = [list(c) for c in fallback_communities]
            except ImportError:
                return IVGResult(
                    columns=["id", "community", "size"],
                    rows=[],
                    error="Leiden requires python-igraph+leidenalg (preferred) or networkx",
                )
        except Exception as e:
            return IVGResult(
                columns=["id", "community", "size"],
                rows=[],
                error=f"leidenalg failed: {str(e)[:200]}",
            )

        if progress_callback is not None:
            progress_callback(2, 2)

        sorted_communities = sorted(communities, key=len, reverse=True)
        final_label: Dict[str, int] = {}
        sizes: Dict[int, int] = {}
        for new_id, members in enumerate(sorted_communities):
            for m_node in members:
                final_label[m_node] = new_id
            sizes[new_id] = len(members)

        rows = [[node, final_label[node], sizes[final_label[node]]] for node in final_label]
        rows.sort(key=lambda r: (-r[2], r[1], r[0]))
        if top_k > 0:
            rows = rows[:top_k]

        return IVGResult(columns=["id", "community", "size"], rows=rows)

    def execute_triangle_count(
        self, top_k: int, progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> IVGResult:
        if not arno_disabled():
            try:
                return self._triangle_count_arno(top_k)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError

                if not isinstance(e, ArnoError):
                    logger.warning("TriangleCount arno path raised non-ArnoError: %s", e)
        return self._triangle_count_lazykg(top_k, progress_callback)

    def _triangle_count_arno(self, top_k: int) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call,
            build_kg_adjacency_chunked,
        )

        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn, schema=self._ivg_schema)
        # every node, ranked and cut here: the library's own cut breaks ties another way
        raw = arno_call(self.conn, "kg_triangle_count_run", 0, schema=self._ivg_schema)
        import json as _json

        results = _json.loads(raw) if raw else []
        rows = [
            [r.get("id", ""), int(r.get("triangles", 0)), float(r.get("lcc", 0.0))] for r in results
        ]
        return IVGResult(columns=["id", "triangles", "lcc"], rows=_ranked_rows(rows, top_k))

    def _triangle_count_lazykg(
        self, top_k: int, progress_callback: Optional[Callable[[int, int], None]]
    ) -> IVGResult:
        """Spec 163 FR-025: triangle count + LCC over symmetrized neighbors via LazyKG.

        For each node v, build N(v) = out_neighbors(v) ∪ in_neighbors(v) (skip
        self-loops, dedupe multi-edges). For each unordered pair (u, w) ∈ N(v),
        increment triangle count if u and w are also adjacent. LCC normalizes:
            lcc(v) = triangles(v) / C(|N(v)|, 2)   if |N(v)| ≥ 2 else 0.0

        Matches networkx.triangles(networkx.Graph(G_directed)) convention
        (spec 163 Q1 clarification: symmetrize edges).
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes = list(lkg.iter_nodes())
        n_total = len(all_nodes)

        neighbors: Dict[str, set] = {}
        for v in all_nodes:
            sym = set()
            for w in lkg.out_neighbors(v):
                if w != v:
                    sym.add(w)
            for u in lkg.in_neighbors(v):
                if u != v:
                    sym.add(u)
            neighbors[v] = sym

        scored: List[tuple] = []
        for idx, v in enumerate(all_nodes):
            if progress_callback is not None and idx % 100 == 0:
                progress_callback(idx, n_total)
            n_set = neighbors[v]
            triangles = 0
            n_list = sorted(n_set)
            for i in range(len(n_list)):
                u = n_list[i]
                u_neighbors = neighbors.get(u, set())
                for j in range(i + 1, len(n_list)):
                    w = n_list[j]
                    if w in u_neighbors:
                        triangles += 1
            k = len(n_set)
            lcc = (triangles / (k * (k - 1) / 2)) if k >= 2 else 0.0
            scored.append((v, triangles, lcc))

        if progress_callback is not None:
            progress_callback(n_total, n_total)

        return IVGResult(
            columns=["id", "triangles", "lcc"],
            rows=_ranked_rows([[v, t, l] for (v, t, l) in scored], top_k),
        )

    def execute_scc(
        self, top_k: int, progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> IVGResult:
        if not arno_disabled():
            try:
                return self._scc_arno(top_k)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError

                if not isinstance(e, ArnoError):
                    logger.warning("SCC arno path raised non-ArnoError: %s", e)
        return self._scc_lazykg(top_k, progress_callback)

    def _scc_arno(self, top_k: int) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call,
            build_kg_adjacency_chunked,
        )

        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn, schema=self._ivg_schema)
        raw = arno_call(self.conn, "kg_scc_run", int(top_k), schema=self._ivg_schema)
        import json as _json

        results = _json.loads(raw) if raw else []
        rows = [
            [r.get("id", ""), int(r.get("component", 0)), int(r.get("size", 0))] for r in results
        ]
        return IVGResult(columns=["id", "component", "size"], rows=rows)

    def _scc_lazykg(
        self, top_k: int, progress_callback: Optional[Callable[[int, int], None]]
    ) -> IVGResult:
        """Spec 163 FR-025: Strongly Connected Components via iterative Tarjan.

        Tarjan 1972: single DFS pass with low-link tracking. Iterative version
        uses explicit stack frames to avoid Python recursion limit (~1000 frames)
        on graphs with deep DFS chains (e.g., long directed paths in 100K+
        node graphs).

        Direction-aware: SCC is inherently DIRECTED; does NOT symmetrize
        adjacency (key difference from existing Algorithms.WCCJson which is
        undirected).

        Output component IDs remapped to contiguous 0..K-1 sorted by
        descending component size (component 0 = largest), per spec 163 FR-009.
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes = list(lkg.iter_nodes())
        n_total = len(all_nodes)
        if n_total == 0:
            return IVGResult(columns=["id", "component", "size"], rows=[])

        index: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on_stack: set = set()
        scc_stack: List[str] = []
        components: List[List[str]] = []
        counter = [0]

        for start in all_nodes:
            if start in index:
                continue
            work_stack: List[tuple] = [(start, iter(lkg.out_neighbors(start)), False)]
            index[start] = counter[0]
            lowlink[start] = counter[0]
            counter[0] += 1
            scc_stack.append(start)
            on_stack.add(start)

            while work_stack:
                v, it, _ = work_stack[-1]
                try:
                    w = next(it)
                    if w not in index:
                        index[w] = counter[0]
                        lowlink[w] = counter[0]
                        counter[0] += 1
                        scc_stack.append(w)
                        on_stack.add(w)
                        work_stack.append((w, iter(lkg.out_neighbors(w)), False))
                    elif w in on_stack:
                        lowlink[v] = min(lowlink[v], index[w])
                except StopIteration:
                    if lowlink[v] == index[v]:
                        comp: List[str] = []
                        while True:
                            u = scc_stack.pop()
                            on_stack.discard(u)
                            comp.append(u)
                            if u == v:
                                break
                        components.append(comp)
                    work_stack.pop()
                    if work_stack:
                        parent = work_stack[-1][0]
                        lowlink[parent] = min(lowlink[parent], lowlink[v])

            if progress_callback is not None:
                progress_callback(len(index), n_total)

        components.sort(key=len, reverse=True)
        rows: List[list] = []
        for comp_id, comp in enumerate(components):
            size = len(comp)
            for node in comp:
                rows.append([node, comp_id, size])

        rows.sort(key=lambda r: (-r[2], r[1], r[0]))
        if top_k > 0:
            rows = rows[:top_k]
        return IVGResult(columns=["id", "component", "size"], rows=rows)

    def execute_k_core(
        self, top_k: int, progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> IVGResult:
        if not arno_disabled():
            try:
                return self._k_core_arno(top_k)
            except Exception as e:
                from iris_vector_graph.stores.arno_bridge import ArnoError

                if not isinstance(e, ArnoError):
                    logger.warning("K-Core arno path raised non-ArnoError: %s", e)
        return self._k_core_lazykg(top_k, progress_callback)

    def _k_core_arno(self, top_k: int) -> IVGResult:
        """Spec 163 FR-024 arno path via chunked NKG-format adjacency upload."""
        from iris_vector_graph.stores.arno_bridge import (
            arno_call,
            build_kg_adjacency_chunked,
        )

        idx_to_node, _edge_count = build_kg_adjacency_chunked(self.conn, schema=self._ivg_schema)
        # every node, ranked and cut here: the library's own cut breaks ties another way
        raw = arno_call(self.conn, "kg_kcore_run", 0, schema=self._ivg_schema)
        import json as _json

        results = _json.loads(raw) if raw else []
        rows = [[r.get("id", ""), int(r.get("coreness", 0))] for r in results]
        return IVGResult(columns=["id", "coreness"], rows=_ranked_rows(rows, top_k))

    def _k_core_lazykg(
        self, top_k: int, progress_callback: Optional[Callable[[int, int], None]]
    ) -> IVGResult:
        """Spec 163 FR-025: K-Core decomposition via Batagelj-Zaversnik (2003).

        Linear-time bucket-sort algorithm:
          1. Build symmetrized adjacency (K-Core is inherently undirected;
             matches networkx convention). Skip self-loops, collapse multi-edges.
          2. Compute initial degrees, bucket nodes by degree.
          3. Repeatedly pop lowest-degree node v, record coreness=current k,
             decrement neighbors' effective degrees (move them to lower buckets).

        Complexity: O(V+E) amortized.

        Output sorted by descending coreness, ties by node_id in IRIS collation (_ranked_rows).
        """
        from iris_vector_graph.stores.lazy_kg import LazyKG

        lkg = LazyKG(self.conn, include_sinks=True)
        all_nodes = list(lkg.iter_nodes())
        n_total = len(all_nodes)
        if n_total == 0:
            return IVGResult(columns=["id", "coreness"], rows=[])

        adj: Dict[str, set] = {node: set() for node in all_nodes}
        for v in all_nodes:
            for w in lkg.out_neighbors(v):
                if w != v and w in adj:
                    adj[v].add(w)
                    adj[w].add(v)
            for u in lkg.in_neighbors(v):
                if u != v and u in adj:
                    adj[v].add(u)
                    adj[u].add(v)

        deg: Dict[str, int] = {v: len(neighbors) for v, neighbors in adj.items()}
        max_deg = max(deg.values()) if deg else 0
        buckets: List[List[str]] = [[] for _ in range(max_deg + 1)]
        for v, d in deg.items():
            buckets[d].append(v)
        in_bucket: Dict[str, int] = dict(deg)

        coreness: Dict[str, int] = {}
        for k in range(max_deg + 1):
            while buckets[k]:
                v = buckets[k].pop()
                if v in coreness:
                    continue
                if in_bucket.get(v) != k:
                    continue
                coreness[v] = k
                if progress_callback is not None and len(coreness) % 100 == 0:
                    progress_callback(len(coreness), n_total)
                for w in adj[v]:
                    if w in coreness:
                        continue
                    cur = in_bucket[w]
                    if cur > k:
                        new_d = cur - 1
                        in_bucket[w] = new_d
                        buckets[new_d].append(w)

        if progress_callback is not None:
            progress_callback(n_total, n_total)

        return IVGResult(columns=["id", "coreness"], rows=_ranked_rows([[n, c] for n, c in coreness.items()], top_k))

    def close(self) -> None:
        pass
