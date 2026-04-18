"""
Cypher-to-SQL Translation Artifacts

Classes for managing SQL generation from Cypher AST.
Supports multi-stage queries via Common Table Expressions (CTEs).
"""

from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional, Union
import logging
import json
from . import ast
from iris_vector_graph.security import validate_table_name, VALID_GRAPH_TABLES, sanitize_identifier

logger = logging.getLogger(__name__)

# Module-level schema prefix configuration
# Set to "Graph_KG" to use Graph_KG.nodes, Graph_KG.rdf_labels, etc.
# Set to "" (empty string) for unqualified table names
_schema_prefix: str = ""


def set_schema_prefix(prefix: str) -> None:
    """Set the schema prefix for all table references in generated SQL.
    
    Args:
        prefix: Schema name (e.g., "Graph_KG") or empty string for unqualified names
    """
    global _schema_prefix
    _schema_prefix = prefix


def get_schema_prefix() -> str:
    """Get the current schema prefix."""
    return _schema_prefix


def _table(name: str) -> str:
    """Return fully qualified table name with schema prefix if configured.
    
    Security: Validates name against VALID_GRAPH_TABLES allowlist to prevent
    SQL injection via table name manipulation.
    
    Args:
        name: Table name (must be in VALID_GRAPH_TABLES)
        
    Returns:
        Schema-qualified table name (e.g., "Graph_KG.nodes")
        
    Raises:
        ValueError: If name is not in the allowlist
    """
    # Validate against allowlist - raises ValueError if invalid
    validate_table_name(name)
    
    if _schema_prefix:
        return f"{_schema_prefix}.{name}"
    return name


def labels_subquery(node_expr: str) -> str:
    return f"(SELECT JSON_ARRAYAGG(label) FROM {_table('rdf_labels')} WHERE s = {node_expr})"


def properties_subquery(node_expr: str) -> str:
    # Stable string-based JSON aggregation.
    # We avoid native JSON_OBJECT in subqueries as it triggers an IRIS optimizer bug 
    # (looking for %QPAR in the local schema) in some versions (e.g. 2025.1).
    # We use minimal REPLACE calls for performance while ensuring valid JSON escaping.
    return (
        "(SELECT JSON_ARRAYAGG("
        "'{\"key\":\"' || REPLACE(REPLACE(\"key\", '\\', '\\\\'), '\"', '\\\"') || "
        "'\",\"value\":\"' || REPLACE(REPLACE(val, '\\', '\\\\'), '\"', '\\\"') || '\"}') "
        f"FROM {_table('rdf_props')} WHERE s = {node_expr})"
    )

@dataclass
class QueryMetadata:
    """Query execution metadata tracking."""
    estimated_rows: Optional[int] = None
    index_usage: List[str] = field(default_factory=list)
    optimization_applied: List[str] = field(default_factory=list)
    complexity_score: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class TemporalBound:
    ts_start: Any
    ts_end: Any
    rel_variable: str
    predicate: Optional[str]
    direction: str


class TemporalQueryRequiresEngine(ValueError):
    pass


@dataclass
class SQLQuery:
    """Generated SQL query with parameters and metadata."""
    sql: Union[str, List[str]]
    parameters: List[List[Any]] = field(default_factory=list)
    query_metadata: QueryMetadata = field(default_factory=QueryMetadata)
    is_transactional: bool = False
    var_length_paths: Optional[List[dict]] = None


class TranslationContext:
    """Stateful context for SQL generation across multiple query stages."""
    
    def __init__(self, parent: Optional['TranslationContext'] = None):
        self.variable_aliases: Dict[str, str] = {}
        if parent is not None:
            self.variable_aliases = parent.variable_aliases.copy()

        # Variables that are scalar (not graph nodes) — skip node expansion in RETURN
        self.scalar_variables: set = set() if parent is None else parent.scalar_variables.copy()

        # Named path registry: path variable → AST NamedPath + SQL aliases
        self.named_paths: Dict[str, ast.NamedPath] = {} if parent is None else parent.named_paths.copy()
        self.path_node_aliases: Dict[str, List[str]] = {} if parent is None else parent.path_node_aliases.copy()
        self.path_edge_aliases: Dict[str, List[str]] = {} if parent is None else parent.path_edge_aliases.copy()
        self.var_length_paths: List[dict] = [] if parent is None else parent.var_length_paths

        self.select_items: List[str] = []
        self.from_clauses: List[str] = []
        self.join_clauses: List[str] = []
        self.where_conditions: List[str] = []
        self.group_by_items: List[str] = []
        
        self.select_params: List[Any] = []
        self.join_params: List[Any] = []
        self.where_params: List[Any] = []
        
        self.dml_statements: List[tuple[str, List[Any]]] = []
        
        self.all_stage_params: List[Any] = [] if parent is None else parent.all_stage_params
        self._alias_counter: int = 0 if parent is None else parent._alias_counter
        self.stages: List[str] = [] if parent is None else parent.stages
        self.input_params: Dict[str, Any] = {} if parent is None else parent.input_params
        self.temporal_rel_ctes: Dict[str, str] = {} if parent is None else parent.temporal_rel_ctes.copy()
        self.temporal_derived: Dict[str, str] = {} if parent is None else parent.temporal_derived.copy()
        self.pending_where = None
        self.mapped_node_aliases: Dict[str, dict] = {} if parent is None else parent.mapped_node_aliases.copy()

    def next_alias(self, prefix: str = "t") -> str:
        alias = f"{prefix}{self._alias_counter}"
        self._alias_counter += 1
        return alias

    def register_variable(self, variable: str, prefix: str = "n") -> str:
        if variable not in self.variable_aliases:
            self.variable_aliases[variable] = self.next_alias(prefix)
        return self.variable_aliases[variable]

    def add_select_param(self, value: Any) -> str:
        self.select_params.append(value); return "?"

    def add_join_param(self, value: Any) -> str:
        self.join_params.append(value); return "?"

    def add_where_param(self, value: Any) -> str:
        self.where_params.append(value); return "?"

    def build_stage_sql(self, distinct: bool = False, select_override: Optional[str] = None) -> tuple[str, List[Any]]:
        select = select_override if select_override else f"SELECT {'DISTINCT ' if distinct else ''}{', '.join(self.select_items)}"
        parts = [select]
        if self.from_clauses:
            expanded = []
            for fc in self.from_clauses:
                if fc in self.temporal_derived:
                    expanded.append(f"({self.temporal_derived[fc]}) {fc}")
                else:
                    expanded.append(fc)
            parts.append(f"FROM {', '.join(expanded)}")
        expanded_joins = []
        for jc in self.join_clauses:
            for tname, tsql in self.temporal_derived.items():
                if f"JOIN {tname} " in jc or f"JOIN {tname}\n" in jc:
                    jc = jc.replace(f"JOIN {tname} ", f"JOIN ({tsql}) {tname} ")
                    jc = jc.replace(f"JOIN {tname}\n", f"JOIN ({tsql}) {tname}\n")
            expanded_joins.append(jc)
        if expanded_joins: parts.extend(expanded_joins)
        if self.where_conditions: parts.append(f"WHERE {' AND '.join(self.where_conditions)}")
        if self.group_by_items: parts.append(f"GROUP BY {', '.join(self.group_by_items)}")
        sql = "\n".join(parts)
        params = (self.select_params if not select_override else []) + self.join_params + self.where_params
        return sql, params


    def add_dml(self, sql: str, params: List[Any]):
        self.dml_statements.append((sql, params))


def translate_procedure_call(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    """Translate a CALL procedure into a CTE prepended to context.stages.

    Supported procedures:
    - ivg.vector.search(label, property, query_input, limit [, options])
    - ivg.neighbors(source_node_or_list, predicate, direction)
    - ivg.ppr(seed_node_or_list, alpha, max_iterations)
    - ivg.bm25.search(name, query, k)
    - ivg.ivf.search(name, query_vec, k, nprobe)
    """
    name = proc.procedure_name
    if name == "ivg.vector.search":
        _translate_vector_search(proc, context)
    elif name == "ivg.neighbors":
        _translate_neighbors(proc, context)
    elif name == "ivg.ppr":
        _translate_ppr(proc, context)
    elif name == "ivg.bm25.search":
        _translate_bm25_search(proc, context)
    elif name == "ivg.ivf.search":
        _translate_ivf_search(proc, context)
    else:
        raise ValueError(f"Unknown procedure: {name!r}. Supported: ivg.vector.search, ivg.neighbors, ivg.ppr, ivg.bm25.search, ivg.ivf.search")


def _resolve_arg(arg, context: TranslationContext, name: str, expected_type=None):
    """Resolve a procedure argument (literal, variable/parameter, or list)."""
    if isinstance(arg, ast.Literal):
        val = arg.value
        if isinstance(val, list):
            return [item.value if isinstance(item, ast.Literal) else item for item in val]
        return val
    elif isinstance(arg, ast.Variable):
        if arg.name in context.input_params:
            return context.input_params[arg.name]
        raise ValueError(f"{name}: parameter '${arg.name}' not found in params")
    raise ValueError(f"{name}: argument must be a literal or parameter")


def _translate_vector_search(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    """Translate ivg.vector.search into a VecSearch CTE.

    Mode 1 — pre-computed vector (list[float]): TO_VECTOR(?, DOUBLE)
    Mode 2 — text via IRIS EMBEDDING(): EMBEDDING(?, ?)
    Mode 3 — node ID (string, not a list): subquery (SELECT emb WHERE id = ?)
    """

    args = proc.arguments
    if len(args) < 4:
        raise ValueError(
            f"ivg.vector.search requires at least 4 arguments "
            f"(label, property, query_input, limit), got {len(args)}"
        )

    # Resolve label (arg 0) — must be a string literal
    label_arg = args[0]
    if not isinstance(label_arg, ast.Literal) or not isinstance(label_arg.value, str):
        raise ValueError("ivg.vector.search: first argument (label) must be a string literal")
    label = label_arg.value
    # Security: validate label against allowlist-based table name check (reuse same validator)
    validate_table_name("rdf_labels")  # warm validator cache
    # Label itself goes into SQL as a parameterized value, not as a table name — safe.

    # Resolve property (arg 1) — string literal, typically "embedding"
    prop_arg = args[1]
    if not isinstance(prop_arg, ast.Literal) or not isinstance(prop_arg.value, str):
        raise ValueError("ivg.vector.search: second argument (property) must be a string literal")
    # property name is unused in SQL (embedding column is always 'emb') but stored for future use

    # Resolve query_input (arg 2) — list[float] or str (for Mode 2)
    query_input_arg = args[2]
    if isinstance(query_input_arg, ast.Literal):
        raw = query_input_arg.value
        # Parser wraps list literals as list[ast.Literal]; unwrap to list[float]
        if isinstance(raw, list):
            query_input: Any = [item.value if isinstance(item, ast.Literal) else item for item in raw]
        else:
            query_input = raw
    elif isinstance(query_input_arg, ast.Variable):
        var_name = query_input_arg.name
        if var_name in context.input_params:
            query_input = context.input_params[var_name]
        else:
            raise ValueError(f"ivg.vector.search: parameter '${var_name}' not found in params")
    else:
        raise ValueError("ivg.vector.search: third argument (query_input) must be a literal or parameter")

    # Resolve limit (arg 3) — integer literal or parameter
    limit_arg = args[3]
    if isinstance(limit_arg, ast.Literal):
        limit_val = limit_arg.value
    elif isinstance(limit_arg, ast.Variable):
        var_name = limit_arg.name
        if var_name in context.input_params:
            limit_val = context.input_params[var_name]
        else:
            raise ValueError(f"ivg.vector.search: parameter '${var_name}' not found in params")
    else:
        raise ValueError("ivg.vector.search: fourth argument (limit) must be an integer literal or parameter")
    try:
        limit_int = int(limit_val)
    except (TypeError, ValueError):
        raise ValueError(f"ivg.vector.search: limit must be an integer, got {limit_val!r}")
    if limit_int <= 0:
        raise ValueError(f"ivg.vector.search: limit must be > 0, got {limit_int}")

    # Resolve options
    raw_options = proc.options or {}
    # Resolve any Literal-wrapped option values
    options: Dict[str, Any] = {}
    for k, v in raw_options.items():
        options[k] = v.value if isinstance(v, ast.Literal) else v

    similarity = options.get("similarity", "cosine")
    if similarity not in ("cosine", "dot_product"):
        raise ValueError(f"ivg.vector.search: similarity must be 'cosine' or 'dot_product', got {similarity!r}")

    vector_fn = "VECTOR_COSINE" if similarity == "cosine" else "VECTOR_DOT_PRODUCT"
    emb_table = _table("kg_NodeEmbeddings")
    labels_tbl = _table("rdf_labels")

    # Determine mode and build SQL expression + ordered params
    if isinstance(query_input, list):
        # Mode 1: pre-computed vector
        vec_json = json.dumps(query_input)
        similarity_expr = f"{vector_fn}(e.emb, TO_VECTOR(?, DOUBLE))"
        ordered_params: List[Any] = [vec_json, label]
        exclude_self = False
    elif isinstance(query_input, str):
        embedding_config: Optional[str] = options.get("embedding_config")
        if embedding_config:
            # Mode 2: text via IRIS EMBEDDING() function
            similarity_expr = f"{vector_fn}(e.emb, EMBEDDING(?, ?))"
            ordered_params = [query_input, embedding_config, label]
            exclude_self = False
        else:
            # Mode 3: node ID — subquery lets IRIS activate HNSW index
            similarity_expr = f"{vector_fn}(e.emb, (SELECT e2.emb FROM {emb_table} e2 WHERE e2.id = ?))"
            ordered_params = [query_input, label]
            exclude_self = True
    else:
        raise ValueError(
            f"ivg.vector.search: query_input must be a list[float] or str, got {type(query_input).__name__}"
        )

    # Build VecSearch CTE SQL
    cte_sql = (
        f"SELECT TOP {limit_int} e.id AS node, {similarity_expr} AS score\n"
        f"FROM {emb_table} e\n"
        f"JOIN {labels_tbl} lbl ON lbl.s = e.id AND lbl.label = ?\n"
    )
    if exclude_self:
        cte_sql += f"WHERE e.id != ?\n"
        ordered_params.append(query_input)
    cte_sql += f"ORDER BY score DESC"

    context.all_stage_params.extend(ordered_params)
    context.stages.insert(0, f"VecSearch AS (\n{cte_sql}\n)")

    # Pre-populate variable_aliases so subsequent MATCH/RETURN can resolve YIELD variables.
    # 'node' is the id column aliased as 'node'; 'score' is a scalar float — mark it accordingly.
    for item in proc.yield_items:
        context.variable_aliases[item] = "VecSearch"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_neighbors(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    """CALL ivg.neighbors($sources, 'MENTIONS', 'out') YIELD neighbor

    Args: source (str or list[str]), predicate (str, optional), direction ('out'/'in'/'both', default 'out')
    Yields: neighbor (node ID)
    """
    args = proc.arguments
    if len(args) < 1:
        raise ValueError("ivg.neighbors requires at least 1 argument (source_ids)")

    sources = _resolve_arg(args[0], context, "ivg.neighbors")
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list):
        raise ValueError(f"ivg.neighbors: source must be a string or list, got {type(sources).__name__}")

    predicate = _resolve_arg(args[1], context, "ivg.neighbors") if len(args) > 1 else None
    direction = _resolve_arg(args[2], context, "ivg.neighbors") if len(args) > 2 else "out"
    if direction not in ("out", "in", "both"):
        raise ValueError(f"ivg.neighbors: direction must be 'out', 'in', or 'both', got {direction!r}")

    edges_tbl = _table("rdf_edges")
    ph = ", ".join(["?"] * len(sources))
    parts = []

    if direction in ("out", "both"):
        sql = f"SELECT DISTINCT e.o_id AS neighbor FROM {edges_tbl} e WHERE e.s IN ({ph})"
        p = list(sources)
        if predicate:
            sql += " AND e.p = ?"
            p.append(predicate)
        parts.append((sql, p))

    if direction in ("in", "both"):
        sql = f"SELECT DISTINCT e.s AS neighbor FROM {edges_tbl} e WHERE e.o_id IN ({ph})"
        p = list(sources)
        if predicate:
            sql += " AND e.p = ?"
            p.append(predicate)
        parts.append((sql, p))

    if len(parts) == 1:
        cte_sql, cte_params = parts[0]
    else:
        cte_sql = " UNION ".join(sql for sql, _ in parts)
        cte_params = []
        for _, p in parts:
            cte_params.extend(p)

    context.all_stage_params.extend(cte_params)
    context.stages.insert(0, f"Neighbors AS (\n{cte_sql}\n)")

    for item in proc.yield_items:
        context.variable_aliases[item] = "Neighbors"


def _translate_ppr(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    """CALL ivg.ppr($seeds, 0.85, 20) YIELD node, score

    Generates SQL: SELECT Graph_KG.kg_PPR(?, ?, ?, 0, 1.0)
    Then wraps in JSON_TABLE to produce rows of (node, score).
    """
    args = proc.arguments
    if len(args) < 1:
        raise ValueError("ivg.ppr requires at least 1 argument (seed_ids)")

    seeds = _resolve_arg(args[0], context, "ivg.ppr")
    if isinstance(seeds, str):
        seeds = [seeds]
    if not isinstance(seeds, list):
        raise ValueError(f"ivg.ppr: seeds must be a string or list, got {type(seeds).__name__}")

    alpha = float(_resolve_arg(args[1], context, "ivg.ppr")) if len(args) > 1 else 0.85
    max_iter = int(_resolve_arg(args[2], context, "ivg.ppr")) if len(args) > 2 else 20

    seed_json = json.dumps(seeds)
    ppr_fn = f"{_schema_prefix}.kg_PPR" if _schema_prefix else "kg_PPR"

    cte_sql = (
        f"SELECT j.node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {ppr_fn}(?, ?, ?, 0, 1.0),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.all_stage_params.extend([seed_json, alpha, max_iter])
    context.stages.insert(0, f"PPR AS (\n{cte_sql}\n)")

    for item in proc.yield_items:
        context.variable_aliases[item] = "PPR"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_bm25_search(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    args = proc.arguments
    if len(args) < 3:
        raise ValueError("ivg.bm25.search requires 3 arguments: name, query, k")

    idx_name = _resolve_arg(args[0], context, "ivg.bm25.search")
    if not isinstance(idx_name, str):
        raise ValueError("ivg.bm25.search: first argument (name) must be a string literal")

    query = _resolve_arg(args[1], context, "ivg.bm25.search")
    k_val = _resolve_arg(args[2], context, "ivg.bm25.search")
    try:
        k_int = int(k_val)
    except (TypeError, ValueError):
        raise ValueError(f"ivg.bm25.search: third argument (k) must be an integer, got {k_val!r}")

    bm25_fn = f"{_schema_prefix}.kg_BM25" if _schema_prefix else "kg_BM25"
    safe_idx = idx_name.replace("'", "''")
    safe_query = str(query).replace("'", "''")
    cte_sql = (
        f"SELECT j.node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {bm25_fn}('{safe_idx}', '{safe_query}', {k_int}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"BM25 AS (\n{cte_sql}\n)")

    for item in proc.yield_items:
        context.variable_aliases[item] = "BM25"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_ivf_search(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    import base64
    import struct

    args = proc.arguments
    if len(args) < 4:
        raise ValueError("ivg.ivf.search requires 4 arguments: name, query_vec, k, nprobe")

    idx_name = _resolve_arg(args[0], context, "ivg.ivf.search")
    if not isinstance(idx_name, str):
        raise ValueError("ivg.ivf.search: first argument (name) must be a string literal")

    query_vec = _resolve_arg(args[1], context, "ivg.ivf.search")
    if not isinstance(query_vec, list):
        raise ValueError("ivg.ivf.search: second argument (query_vec) must be a list of floats")
    floats = [float(v) for v in query_vec]
    raw = struct.pack(f"{len(floats)}f", *floats)
    query_b64 = base64.b64encode(raw).decode()

    k_val = _resolve_arg(args[2], context, "ivg.ivf.search")
    try:
        k_int = int(k_val)
    except (TypeError, ValueError):
        raise ValueError(f"ivg.ivf.search: third argument (k) must be an integer, got {k_val!r}")

    nprobe_val = _resolve_arg(args[3], context, "ivg.ivf.search")
    try:
        nprobe_int = int(nprobe_val)
    except (TypeError, ValueError):
        raise ValueError(f"ivg.ivf.search: fourth argument (nprobe) must be an integer, got {nprobe_val!r}")

    ivf_fn = f"{_schema_prefix}.kg_IVF" if _schema_prefix else "kg_IVF"
    safe_idx = idx_name.replace("'", "''")
    safe_b64 = query_b64.replace("'", "''")
    cte_sql = (
        f"SELECT j.node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {ivf_fn}('{safe_idx}', '{safe_b64}', {k_int}, {nprobe_int}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"IVF AS (\n{cte_sql}\n)")

    for item in proc.yield_items:
        context.variable_aliases[item] = "IVF"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


_TEMPORAL_TS_OPS = {
    ast.BooleanOperator.GREATER_THAN_OR_EQUAL,
    ast.BooleanOperator.LESS_THAN_OR_EQUAL,
    ast.BooleanOperator.GREATER_THAN,
    ast.BooleanOperator.LESS_THAN,
    ast.BooleanOperator.EQUALS,
}


def _extract_temporal_bounds(where_expr, rel_var: str, params: dict):
    if where_expr is None:
        return None
    return _walk_for_temporal(where_expr, rel_var, params)


def _resolve_ts_value(expr, params: dict):
    if isinstance(expr, ast.Literal):
        return expr.value
    if hasattr(ast, 'Parameter') and isinstance(expr, ast.Parameter):
        return params.get(expr.name)
    if isinstance(expr, ast.Variable):
        return params.get(expr.name)
    return None


def _walk_for_temporal(expr, rel_var: str, params: dict):
    if not isinstance(expr, ast.BooleanExpression):
        return None

    op = expr.operator

    if op == ast.BooleanOperator.OR:
        for operand in expr.operands:
            if isinstance(operand, ast.BooleanExpression) and operand.operands:
                left = operand.operands[0]
                if isinstance(left, ast.PropertyReference) and left.variable == rel_var and left.property_name == "ts":
                    raise ValueError(
                        f"Temporal r.ts OR conditions are not supported. "
                        f"Use AND to combine timestamp bounds."
                    )
        return None

    if op == ast.BooleanOperator.AND:
        ts_start = None
        ts_end = None
        found = False
        for operand in expr.operands:
            result = _walk_for_temporal(operand, rel_var, params)
            if result is not None:
                found = True
                if result.ts_start is not None and ts_start is None:
                    ts_start = result.ts_start
                if result.ts_end is not None and ts_end is None:
                    ts_end = result.ts_end
        if found:
            return TemporalBound(
                ts_start=ts_start,
                ts_end=ts_end,
                rel_variable=rel_var,
                predicate=None,
                direction="out",
            )
        return None

    if op in _TEMPORAL_TS_OPS and len(expr.operands) >= 2:
        left, right = expr.operands[0], expr.operands[1]
        if isinstance(left, ast.PropertyReference) and left.variable == rel_var and left.property_name == "ts":
            val = _resolve_ts_value(right, params)
            if op in (ast.BooleanOperator.GREATER_THAN_OR_EQUAL, ast.BooleanOperator.GREATER_THAN):
                return TemporalBound(ts_start=val, ts_end=None, rel_variable=rel_var, predicate=None, direction="out")
            if op in (ast.BooleanOperator.LESS_THAN_OR_EQUAL, ast.BooleanOperator.LESS_THAN):
                return TemporalBound(ts_start=None, ts_end=val, rel_variable=rel_var, predicate=None, direction="out")
            if op == ast.BooleanOperator.EQUALS:
                return TemporalBound(ts_start=val, ts_end=val, rel_variable=rel_var, predicate=None, direction="out")

    return None


def _build_temporal_cte(edges: list, cte_name: str, metadata) -> str:
    _LIMIT = 10_000
    if not edges:
        return "SELECT NULL AS s, NULL AS p, NULL AS o, NULL AS ts, NULL AS weight FROM (SELECT 1) __empty WHERE 1=0"
    if len(edges) > _LIMIT:
        metadata.warnings.append(
            f"temporal result truncated to {_LIMIT:,} edges — "
            f"narrow the time window or use get_edges_in_window()"
        )
        edges = edges[:_LIMIT]
    rows = []
    for e in edges:
        s = str(e.get("s", e.get("source", ""))).replace("'", "''")
        p = str(e.get("p", e.get("predicate", ""))).replace("'", "''")
        o = str(e.get("o", e.get("target", ""))).replace("'", "''")
        ts = int(e.get("ts", e.get("timestamp", 0)))
        w = float(e.get("w", e.get("weight", 1.0)))
        rows.append(f"SELECT '{s}' AS s, '{p}' AS p, '{o}' AS o, {ts} AS ts, {w} AS weight")
    return " UNION ALL ".join(rows)

def _remove_ts_conditions_from_where(context, rel_var: str):
    kept = []
    for cond in context.where_conditions:
        if f".ts" in cond and rel_var in cond:
            continue
        kept.append(cond)
    context.where_conditions = kept

def translate_to_sql(cypher_query: ast.CypherQuery, params: Optional[Dict[str, Any]] = None, engine=None) -> SQLQuery:
    if getattr(cypher_query, "union_queries", None):
        branches = [cypher_query] + [uq["query"] for uq in cypher_query.union_queries]
        all_flags = [False] + [uq["all"] for uq in cypher_query.union_queries]
        sqls = []
        all_params = []
        for branch in branches:
            branch_copy = ast.CypherQuery(
                query_parts=branch.query_parts,
                return_clause=branch.return_clause,
                order_by_clause=branch.order_by_clause,
                skip=branch.skip,
                limit=branch.limit,
                procedure_call=branch.procedure_call,
            )
            branch_copy.union_queries = []
            r = translate_to_sql(branch_copy, params)
            sqls.append(r.sql if isinstance(r.sql, str) else "\n".join(r.sql))
            all_params.extend(r.parameters)
        sep = " UNION ALL " if any(all_flags[1:]) else " UNION "
        combined = sep.join(f"({s})" for s in sqls)
        flat_params = []
        for p_list in all_params:
            flat_params.extend(p_list)
        return SQLQuery(sql=combined, parameters=[flat_params])

    context = TranslationContext()
    context.input_params = params or {}
    context._engine = engine
    metadata = QueryMetadata()
    context._metadata = metadata
    is_transactional = False

    # Handle CALL procedure — emits a CTE before any MATCH stages
    if cypher_query.procedure_call is not None:
        translate_procedure_call(cypher_query.procedure_call, context)
        if not cypher_query.query_parts:
            cte_name = context.stages[0].split(" AS ")[0].strip() if context.stages else "VecSearch"
            context.from_clauses.append(cte_name)

    for i, part in enumerate(cypher_query.query_parts):
        context.select_items, context.from_clauses, context.join_clauses = [], [], []
        context.where_conditions, context.group_by_items = [], []
        context.select_params, context.join_params, context.where_params = [], [], []
        if i > 0:
            context.from_clauses.append(f"Stage{i}")
        elif cypher_query.procedure_call is not None:
            cte_name = context.stages[0].split(" AS ")[0].strip() if context.stages else "VecSearch"
            context.from_clauses.append(cte_name)
        for clause in part.clauses:
            if isinstance(clause, ast.WhereClause):
                context.pending_where = clause.expression
                break
        for clause in part.clauses:
            if isinstance(clause, ast.MatchClause): translate_match_clause(clause, context, metadata)
            elif isinstance(clause, ast.UnwindClause): translate_unwind_clause(clause, context)
            elif isinstance(clause, ast.SubqueryCall): translate_subquery_call(clause, context, metadata)
            elif isinstance(clause, ast.UpdatingClause):
                is_transactional = True
                translate_updating_clause(clause, context, metadata)
            elif isinstance(clause, ast.WhereClause): translate_where_clause(clause, context)
        if part.with_clause:
            translate_with_clause(part.with_clause, context)
            sql, stage_params = context.build_stage_sql(part.with_clause.distinct)
            context.all_stage_params.extend(stage_params)
            context.stages.append(f"Stage{i+1} AS (\n{sql}\n)")
            new_aliases = {}
            for item in part.with_clause.items:
                alias = item.alias or (item.expression.name if isinstance(item.expression, ast.Variable) else None)
                if alias: new_aliases[alias] = f"Stage{i+1}"
            context.variable_aliases = new_aliases

    # 2. Final stage (RETURN)
    # If the last QueryPart had a WITH clause, we must select from that CTE stage.
    # Otherwise, we continue with the context of the last QueryPart (e.g. current MATCH joins).
    last_part_had_with = cypher_query.query_parts[-1].with_clause is not None if cypher_query.query_parts else False
    if context.stages and last_part_had_with:
        context.select_items, context.select_params = [], []
        context.from_clauses, context.join_clauses, context.join_params = [f"Stage{len(context.stages)}"], [], []
        context.where_conditions, context.where_params = [], []
    
    if cypher_query.return_clause: translate_return_clause(cypher_query.return_clause, context)
    
    # Process ORDER BY BEFORE building SQL to ensure JOINs are included
    order_by_items = preprocess_order_by(cypher_query, context)
    
    if is_transactional:
        stmts, all_params = [], []
        for s, p in context.dml_statements:
            stmts.append(s); all_params.append(p)
        sql = None
        if cypher_query.return_clause:
            sql, p = context.build_stage_sql(cypher_query.return_clause.distinct)
            sql = apply_pagination(sql, cypher_query, context, order_by_items)
        all_ctes = [c for c in getattr(context, "cte_clauses", []) if not any(td in c for td in context.temporal_derived)] + context.stages
        if all_ctes and sql is not None:
            sql = "WITH " + ",\n".join(all_ctes) + "\n" + sql
            all_params.append(context.all_stage_params + p)
        elif sql is not None:
            all_params.append(p)
        if sql is not None:
            stmts.append(sql)
        return SQLQuery(sql=stmts, parameters=all_params, query_metadata=metadata, is_transactional=True)
    else:
        sql, p = context.build_stage_sql(cypher_query.return_clause.distinct if cypher_query.return_clause else False)
        sql = apply_pagination(sql, cypher_query, context, order_by_items)
        vl = context.var_length_paths or None

        if vl and (vl[0].get("shortest") or vl[0].get("all_shortest")) and cypher_query.return_clause:
            path_funcs = []
            path_var = vl[0].get("target_var") or vl[0].get("source_var")
            named_path_vars = {np.variable for np in (cypher_query.query_parts[0].clauses[0].named_paths
                                                       if cypher_query.query_parts else [])}
            for item in cypher_query.return_clause.items:
                expr = item.expression
                if isinstance(expr, ast.Variable) and expr.name in named_path_vars:
                    path_funcs.append("path")
                elif isinstance(expr, ast.FunctionCall) and expr.function_name.lower() in ("length", "nodes", "relationships"):
                    if expr.arguments and isinstance(expr.arguments[0], ast.Variable):
                        if expr.arguments[0].name in named_path_vars:
                            path_funcs.append(expr.function_name.lower())
            if path_funcs:
                vl[0]["return_path_funcs"] = path_funcs

        all_ctes = [c for c in getattr(context, "cte_clauses", []) if not any(td in c for td in context.temporal_derived)] + context.stages
        if all_ctes:
            sql = "WITH " + ",\n".join(all_ctes) + "\n" + sql
            return SQLQuery(sql=sql, parameters=[context.all_stage_params + p], query_metadata=metadata, var_length_paths=vl)
        return SQLQuery(sql=sql, parameters=[p], query_metadata=metadata, var_length_paths=vl)


def preprocess_order_by(query: ast.CypherQuery, context: TranslationContext) -> list:
    if not query.order_by_clause:
        return []
    items = []
    select_aliases = set()
    if query.return_clause:
        for item in query.return_clause.items:
            if item.alias:
                select_aliases.add(item.alias)
    for item in query.order_by_clause.items:
        try:
            expr = translate_expression(item.expression, context, segment="where")
        except ValueError:
            if isinstance(item.expression, ast.Variable) and item.expression.name in select_aliases:
                expr = sanitize_identifier(item.expression.name)
            else:
                raise
        items.append(f"{expr} {'ASC' if item.ascending else 'DESC'}")
    return items


def _resolve_pagination_value(value, context: TranslationContext) -> Optional[int]:
    """Resolve a SKIP/LIMIT value that may be an integer literal or a parameter variable."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, ast.Variable):
        resolved = context.input_params.get(value.name)
        if resolved is None:
            raise ValueError(f"Parameter '${value.name}' used in SKIP/LIMIT but not provided in params dict")
        return int(resolved)
    return int(value)


def apply_pagination(sql: str, query: ast.CypherQuery, context: TranslationContext, order_by_items: list = None) -> str:
    if order_by_items:
        sql += f"\nORDER BY {', '.join(order_by_items)}"
    limit = _resolve_pagination_value(query.limit, context)
    skip = _resolve_pagination_value(query.skip, context)
    if limit is not None: sql += f"\nLIMIT {limit}"
    if skip is not None: sql += f"\nOFFSET {skip}"
    return sql


def translate_updating_clause(upd, context, metadata):
    if isinstance(upd, ast.CreateClause): translate_create_clause(upd, context, metadata)
    elif isinstance(upd, ast.DeleteClause): translate_delete_clause(upd, context, metadata)
    elif isinstance(upd, ast.MergeClause): translate_merge_clause(upd, context, metadata)
    elif isinstance(upd, ast.SetClause): translate_set_clause(upd, context, metadata)
    elif isinstance(upd, ast.RemoveClause): translate_remove_clause(upd, context, metadata)


def translate_unwind_clause(unwind, context):
    expr = translate_expression(unwind.expression, context, segment="join")
    if isinstance(unwind.expression, ast.Variable) and unwind.expression.name in context.input_params:
        val = context.input_params[unwind.expression.name]
        if isinstance(val, list): context.join_params[-1] = json.dumps(val)
    alias = context.register_variable(unwind.alias, prefix="u")
    context.from_clauses.append(f"JSON_TABLE({expr}, '$[*]' COLUMNS ({unwind.alias} VARCHAR(1000) PATH '$')) {alias}")


def translate_create_clause(create, context, metadata):
    for node in create.pattern.nodes:
        if node.variable and node.variable in context.variable_aliases: continue
        node_id_expr = node.properties.get("id") or node.properties.get("node_id")
        if node_id_expr is None: raise ValueError("CREATE node requires an 'id' property")
        
        if isinstance(node_id_expr, ast.Variable):
            var_alias = context.variable_aliases.get(node_id_expr.name)
            if not var_alias: raise ValueError(f"Undefined: {node_id_expr.name}")
            
            # 1.1 Insert into nodes
            # We need to capture the parameters specifically for this rowset subquery
            sql, p = context.build_stage_sql(select_override=f"SELECT {var_alias}.{node_id_expr.name} AS node_id")
            # Filter parameters to only include those referenced in the SQL subquery
            # Since build_stage_sql just concatenates all params, and we used ? for all, 
            # we need to ensure the number of ? matches len(p).
            # Our current build_stage_sql is correct because it only includes params for the current stage.
            context.add_dml(f"INSERT INTO {_table('nodes')} (node_id) SELECT t.node_id FROM ({sql}) AS t WHERE NOT EXISTS (SELECT 1 FROM {_table('nodes')} WHERE node_id = t.node_id)", p)
            for label in node.labels:
                context.add_dml(f"INSERT INTO {_table('rdf_labels')} (s, label) SELECT t.node_id, ? FROM ({sql}) AS t WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE s = t.node_id AND label = ?)", [label] + p + [label])
        else:
            node_id = node_id_expr.value if isinstance(node_id_expr, ast.Literal) else node_id_expr
            context.add_dml(f"INSERT INTO {_table('nodes')} (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM {_table('nodes')} WHERE node_id = ?)", [node_id, node_id])
            for label in node.labels:
                context.add_dml(f"INSERT INTO {_table('rdf_labels')} (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE s = ? AND label = ?)", [node_id, label, node_id, label])
            for k, v in node.properties.items():
                val = v.value if isinstance(v, ast.Literal) else v
                # Property 'id' or 'node_id' is stored in both nodes table AND rdf_props
                # for Cypher query consistency.
                context.add_dml(f"INSERT INTO {_table('rdf_props')} (s, \"key\", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_props')} WHERE s = ? AND \"key\" = ?)", [node_id, k, val, node_id, k])
        if node.variable: context.register_variable(node.variable)
    
    for i, rel in enumerate(create.pattern.relationships):
        source_node, target_node = create.pattern.nodes[i], create.pattern.nodes[i+1]
        s_id_expr = source_node.properties.get("id") or source_node.properties.get("node_id")
        t_id_expr = target_node.properties.get("id") or target_node.properties.get("node_id")
        s_id = s_id_expr.value if isinstance(s_id_expr, ast.Literal) else s_id_expr if not isinstance(s_id_expr, ast.Variable) else None
        t_id = t_id_expr.value if isinstance(t_id_expr, ast.Literal) else t_id_expr if not isinstance(t_id_expr, ast.Variable) else None
        if s_id and t_id:
            for rt in rel.types: context.add_dml(f"INSERT INTO {_table('rdf_edges')} (s, p, o_id) VALUES (?, ?, ?)", [s_id, rt, t_id])
        else:
            s_alias = context.variable_aliases.get(source_node.variable) if source_node.variable else None
            t_alias = context.variable_aliases.get(target_node.variable) if target_node.variable else None
            s_expr, s_p = ("?", [s_id]) if s_id else (f"{s_alias}.{source_node.variable}" if s_alias and s_alias.startswith('Stage') else f"{s_alias}.node_id", [])
            t_expr, t_p = ("?", [t_id]) if t_id else (f"{t_alias}.{target_node.variable}" if t_alias and t_alias.startswith('Stage') else f"{t_alias}.node_id", [])
            for rt in rel.types:
                sql, p = context.build_stage_sql(select_override=f"SELECT {s_expr}, ?, {t_expr}")
                context.add_dml(f"INSERT INTO {_table('rdf_edges')} (s, p, o_id) {sql}", s_p + [rt] + t_p + p)


def translate_delete_clause(delete, context, metadata):
    for var in delete.expressions:
        alias = context.variable_aliases.get(var.name)
        if not alias: raise ValueError(f"Undefined: {var.name}")
        subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
        if delete.detach: context.add_dml(f"DELETE FROM {_table('rdf_edges')} WHERE s IN ({subquery}) OR o_id IN ({subquery})", subparams + subparams)
        if not alias.startswith('e'):
            context.add_dml(f"DELETE FROM {_table('rdf_labels')} WHERE s IN ({subquery})", subparams)
            context.add_dml(f"DELETE FROM {_table('rdf_props')} WHERE s IN ({subquery})", subparams)
            context.add_dml(f"DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({subquery})", subparams)
            context.add_dml(f"DELETE FROM {_table('nodes')} WHERE node_id IN ({subquery})", subparams)
        else: context.add_dml(f"DELETE FROM {_table('rdf_edges')} WHERE s = (SELECT s FROM {_table('rdf_edges')} {alias}) AND p = (SELECT p FROM {_table('rdf_edges')} {alias}) AND o_id = (SELECT o_id FROM {_table('rdf_edges')} {alias})", [])


def translate_merge_clause(merge, context, metadata):
    translate_create_clause(ast.CreateClause(merge.pattern), context, metadata)
    for action, is_create in [(merge.on_create, True), (merge.on_match, False)]:
        if action:
            for item in action.items:
                if isinstance(item, ast.SetItem) and isinstance(item.expression, ast.PropertyReference):
                    node_id = context.variable_aliases.get(item.expression.variable)
                    k, v = item.expression.property_name, item.value
                    val = v.value if isinstance(v, ast.Literal) else v
                    if is_create: context.add_dml(f"INSERT INTO {_table('rdf_props')} (s, \"key\", val) SELECT node_id, ?, ? FROM {_table('nodes')} WHERE node_id = ? AND NOT EXISTS (SELECT 1 FROM {_table('rdf_props')} WHERE s = ? AND \"key\" = ?)", [k, val, node_id, node_id, k])
                    else: context.add_dml(f"UPDATE {_table('rdf_props')} SET val = ? WHERE s = ? AND \"key\" = ?", [val, node_id, k])


def translate_set_clause(set_cl, context, metadata):
    for item in set_cl.items:
        if isinstance(item.expression, ast.PropertyReference):
            alias, k, v = context.variable_aliases.get(item.expression.variable), item.expression.property_name, item.value
            val = v.value if isinstance(v, ast.Literal) else v
            subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
            context.add_dml(f"UPDATE {_table('rdf_props')} SET val = ? WHERE s IN ({subquery}) AND \"key\" = ?", [val] + subparams + [k])
            context.add_dml(f"INSERT INTO {_table('rdf_props')} (s, \"key\", val) SELECT node_id, ?, ? FROM {_table('nodes')} WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM {_table('rdf_props')} WHERE s = {_table('nodes')}.node_id AND \"key\" = ?)", [k, val] + subparams + [k])
        elif isinstance(item.expression, ast.Variable):
            alias, label = context.variable_aliases.get(item.expression.name), str(item.value.value if isinstance(item.value, ast.Literal) else item.value)
            subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
            context.add_dml(f"INSERT INTO {_table('rdf_labels')} (s, label) SELECT node_id, ? FROM {_table('nodes')} WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE s = {_table('nodes')}.node_id AND label = ?)", [label] + subparams + [label])


def translate_remove_clause(remove, context, metadata):
    for item in remove.items:
        if isinstance(item.expression, ast.PropertyReference):
            alias, k = context.variable_aliases.get(item.expression.variable), item.expression.property_name
            subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
            context.add_dml(f"DELETE FROM {_table('rdf_props')} WHERE s IN ({subquery}) AND \"key\" = ?", subparams + [k])


def translate_match_clause(match_clause, context, metadata):
    for pattern in match_clause.patterns:
        if not pattern.nodes: continue
        translate_node_pattern(pattern.nodes[0], context, metadata, optional=match_clause.optional)
        for i, rel in enumerate(pattern.relationships):
            translate_relationship_pattern(rel, pattern.nodes[i], pattern.nodes[i+1], context, metadata, optional=match_clause.optional)
            translate_node_pattern(pattern.nodes[i+1], context, metadata, optional=match_clause.optional)

    for np in match_clause.named_paths:
        context.named_paths[np.variable] = np
        node_aliases = [
            context.variable_aliases.get(n.variable, f"n{i}")
            for i, n in enumerate(np.pattern.nodes)
        ]
        edge_aliases = [
            context.variable_aliases.get(r.variable, f"e{i}")
            for i, r in enumerate(np.pattern.relationships)
        ]
        context.path_node_aliases[np.variable] = node_aliases
        context.path_edge_aliases[np.variable] = edge_aliases


def translate_subquery_call(subquery: ast.SubqueryCall, context: TranslationContext, metadata):
    inner = subquery.inner_query
    is_correlated = len(subquery.import_variables) > 0

    if is_correlated:
        if not inner.return_clause or len(inner.return_clause.items) != 1:
            raise ValueError("Correlated subquery Phase 1 requires exactly one RETURN column (scalar)")

        ret_item = inner.return_clause.items[0]
        alias = ret_item.alias or "sub_result"

        child_ctx = TranslationContext()
        child_ctx.input_params = context.input_params
        child_ctx._alias_counter = context._alias_counter

        for var in subquery.import_variables:
            if var not in context.variable_aliases:
                raise ValueError(f"Imported variable '{var}' is not defined in outer scope")
            child_ctx.variable_aliases[var] = context.variable_aliases[var]

        for part in inner.query_parts:
            for clause in part.clauses:
                if isinstance(clause, ast.MatchClause):
                    translate_match_clause(clause, child_ctx, metadata)
                elif isinstance(clause, ast.WhereClause):
                    translate_where_clause(clause, child_ctx)

        inner_expr = translate_expression(ret_item.expression, child_ctx, segment="select")

        inner_sql_parts = [f"SELECT {inner_expr}"]
        if child_ctx.from_clauses:
            inner_sql_parts.append(f"FROM {', '.join(child_ctx.from_clauses)}")
            if child_ctx.join_clauses:
                inner_sql_parts.extend(child_ctx.join_clauses)
        elif child_ctx.join_clauses:
            first_join = child_ctx.join_clauses[0].replace("JOIN ", "", 1).replace("CROSS JOIN ", "", 1)
            on_idx = first_join.find(" ON ")
            if on_idx > 0:
                from_part = first_join[:on_idx]
                on_part = first_join[on_idx + 4:]
                inner_sql_parts.append(f"FROM {from_part}")
                if child_ctx.join_clauses[1:]:
                    inner_sql_parts.extend(child_ctx.join_clauses[1:])
                if child_ctx.where_conditions:
                    child_ctx.where_conditions.insert(0, on_part)
                else:
                    child_ctx.where_conditions.append(on_part)
            else:
                inner_sql_parts.append(f"FROM {first_join}")
                if child_ctx.join_clauses[1:]:
                    inner_sql_parts.extend(child_ctx.join_clauses[1:])
        if child_ctx.where_conditions:
            inner_sql_parts.append(f"WHERE {' AND '.join(child_ctx.where_conditions)}")

        scalar_sql = "\n".join(inner_sql_parts)
        all_params = child_ctx.select_params + child_ctx.join_params + child_ctx.where_params
        for p in all_params:
            context.select_params.append(p)

        context.select_items.append(f"COALESCE(({scalar_sql}), 0) AS {alias}")
        context.scalar_variables.add(alias)
        context.variable_aliases[alias] = "scalar"
    else:
        child_ctx = TranslationContext()
        child_ctx.input_params = context.input_params

        for part in inner.query_parts:
            for clause in part.clauses:
                if isinstance(clause, ast.MatchClause):
                    translate_match_clause(clause, child_ctx, metadata)
                elif isinstance(clause, ast.WhereClause):
                    translate_where_clause(clause, child_ctx)
                elif isinstance(clause, ast.UnwindClause):
                    translate_unwind_clause(clause, child_ctx)

        if inner.return_clause:
            translate_return_clause(inner.return_clause, child_ctx)

        inner_sql, inner_params = child_ctx.build_stage_sql(
            inner.return_clause.distinct if inner.return_clause else False
        )

        cte_name = f"SubQuery{len(context.stages)}"
        context.all_stage_params.extend(inner_params)
        context.stages.append(f"{cte_name} AS (\n{inner_sql}\n)")

        if not context.from_clauses:
            context.from_clauses.append(cte_name)
        else:
            context.join_clauses.append(f"CROSS JOIN {cte_name}")

        if inner.return_clause:
            for item in inner.return_clause.items:
                alias = item.alias
                if alias is None:
                    if isinstance(item.expression, ast.Variable):
                        alias = item.expression.name
                    elif isinstance(item.expression, ast.PropertyReference):
                        alias = f"{item.expression.variable}_{item.expression.property_name}"
                    elif isinstance(item.expression, (ast.AggregationFunction, ast.FunctionCall)):
                        alias = f"{item.expression.function_name}_res"
                if alias:
                    context.variable_aliases[alias] = cte_name
                    context.scalar_variables.add(alias)


def translate_node_pattern(node, context, metadata, optional=False):
    if node.variable and node.variable in context.variable_aliases:
        return
    alias = context.register_variable(node.variable) if node.variable else context.next_alias("n")
    jt = "LEFT OUTER JOIN" if optional else "JOIN"

    engine = getattr(context, "_engine", None)
    if engine and node.labels:
        for label in node.labels:
            mapping = engine.get_table_mapping(label)
            if mapping:
                sql_table = sanitize_identifier(mapping["sql_table"])
                context.mapped_node_aliases[alias] = mapping
                if not context.from_clauses:
                    context.from_clauses.append(f"{sql_table} {alias}")
                elif not any(alias in fc for fc in context.from_clauses):
                    context.join_clauses.append(f"{jt} {sql_table} {alias} ON 1=1")
                for k, v in node.properties.items():
                    val_sql = translate_expression(v, context, segment="where")
                    context.where_conditions.append(f"{alias}.{sanitize_identifier(k)} = {val_sql}")
                return

    nodes_tbl = _table('nodes')
    if not context.from_clauses: context.from_clauses.append(f"{nodes_tbl} {alias}")
    elif f"{nodes_tbl} {alias}" not in context.from_clauses and not any(alias in j for j in context.join_clauses): context.join_clauses.append(f"CROSS JOIN {nodes_tbl} {alias}")
    for label in node.labels:
        l_alias = context.next_alias("l")
        context.join_clauses.append(f"{jt} {_table('rdf_labels')} {l_alias} ON {l_alias}.s = {alias}.node_id AND {l_alias}.label = {context.add_join_param(label)}")
        if not optional: context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
    for k, v in node.properties.items():
        val_sql = translate_expression(v, context, segment="where")
        if k in ("node_id", "id"):
            context.where_conditions.append(f"{alias}.node_id = {val_sql}")
        else:
            p_alias = context.next_alias("p")
            context.join_clauses.append(
                f"{jt} {_table('rdf_props')} {p_alias} "
                f"ON {p_alias}.s = {alias}.node_id AND {p_alias}.\"key\" = {context.add_join_param(k)}"
            )
            if optional:
                context.where_conditions.append(f"({p_alias}.s IS NULL OR {p_alias}.val = {val_sql})")
            else:
                context.where_conditions.append(f"{p_alias}.val = {val_sql}")


def translate_relationship_pattern(rel, source_node, target_node, context, metadata, optional=False):
    if rel.variable_length is not None:
        source_alias = context.variable_aliases.get(source_node.variable, "")
        target_alias = context.register_variable(target_node.variable)

        def _resolve_id_param(node):
            id_val = node.properties.get("id")
            if id_val is None:
                return None
            if isinstance(id_val, ast.Variable):
                return f"${id_val.name}"
            if isinstance(id_val, ast.Literal):
                return str(id_val.value)
            if isinstance(id_val, str):
                return id_val
            return str(id_val)

        src_id_param = _resolve_id_param(source_node)
        dst_id_param = _resolve_id_param(target_node)

        direction_str = "both" if rel.direction == ast.Direction.BOTH else "out"

        context.var_length_paths.append({
            "source_var": source_node.variable,
            "source_alias": source_alias,
            "target_var": target_node.variable,
            "target_alias": target_alias,
            "types": rel.types or [],
            "direction": direction_str,
            "min_hops": rel.variable_length.min_hops,
            "max_hops": rel.variable_length.max_hops,
            "shortest": rel.variable_length.shortest,
            "all_shortest": rel.variable_length.all_shortest,
            "src_id_param": src_id_param,
            "dst_id_param": dst_id_param,
            "return_path_funcs": [],
        })
        if not context.from_clauses:
            context.from_clauses.append(f"{_table('nodes')} {target_alias}")
        else:
            context.join_clauses.append(f"JOIN {_table('nodes')} {target_alias} ON 1=1")
        return
    if source_node.variable is None:
        source_alias = context.next_alias("n")
        joined = any(source_alias in fc for fc in context.from_clauses + context.join_clauses)
        if not joined:
            if not context.from_clauses:
                context.from_clauses.append(f"{_table('nodes')} {source_alias}")
            else:
                context.join_clauses.append(f"JOIN {_table('nodes')} {source_alias} ON 1=1")
    else:
        source_alias = context.variable_aliases.get(source_node.variable)
    is_new_target = target_node.variable not in context.variable_aliases
    target_alias = context.register_variable(target_node.variable)
    edge_alias = context.register_variable(rel.variable, prefix="e") if rel.variable else context.next_alias("e")

    def _node_col(variable, alias):
        if alias.startswith('Stage') or alias == 'VecSearch':
            return variable
        return "node_id"

    direction = "in" if rel.direction == ast.Direction.INCOMING else "out"

    if rel.variable and context.pending_where is not None:
        tb = _extract_temporal_bounds(context.pending_where, rel.variable, context.input_params)
        if tb is not None:
            engine = getattr(context, "_engine", None)
            if engine is None:
                raise TemporalQueryRequiresEngine(
                    f"Temporal WHERE {rel.variable}.ts filter detected but no engine was provided. "
                    f"Pass engine=self when calling translate_to_sql() from execute_cypher()."
                )
            tb.direction = direction
            predicate_filter = rel.types[0] if rel.types and len(rel.types) == 1 else ""
            src_node_id = None
            if source_alias and not source_alias.startswith("Stage"):
                bound_src = source_node.variable
                if bound_src:
                    src_val = context.input_params.get(bound_src)
                    if src_val:
                        src_node_id = src_val
            source_filter = src_node_id or ""
            ts_start = tb.ts_start if tb.ts_start is not None else 0
            ts_end = tb.ts_end if tb.ts_end is not None else 9_999_999_999
            edges = engine.get_edges_in_window(
                source_filter, predicate_filter, ts_start, ts_end, direction=tb.direction
            )
            cte_name = f"tc{edge_alias}"
            cte_sql = _build_temporal_cte(edges, cte_name, metadata)
            if not hasattr(context, "cte_clauses"):
                context.cte_clauses = []
            context.cte_clauses.append(f"{cte_name}(s, p, o, ts, weight) AS ({cte_sql})")
            context.temporal_rel_ctes[rel.variable] = cte_name
            context.temporal_derived[cte_name] = cte_sql
            context.temporal_rel_ctes[rel.variable] = cte_name

            if not hasattr(context, "temporal_node_col"):
                context.temporal_node_col = {}

            if direction == "out":
                src_col_in_cte, tgt_col_in_cte = "s", "o"
            else:
                src_col_in_cte, tgt_col_in_cte = "o", "s"

            context.temporal_node_col[source_node.variable] = src_col_in_cte
            context.temporal_node_col[target_node.variable] = tgt_col_in_cte
            context.variable_aliases[source_node.variable] = cte_name
            context.variable_aliases[target_node.variable] = cte_name

            new_from = []
            for fc in context.from_clauses:
                if source_alias in fc and _table("nodes") in fc:
                    new_from.append(cte_name)
                else:
                    new_from.append(fc)
            if not new_from or cte_name not in new_from:
                new_from = [cte_name] + [f for f in new_from if f != cte_name]
            context.from_clauses = new_from

            new_joins = []
            for jc in context.join_clauses:
                if f"{source_alias}.node_id" in jc or f"{_table('nodes')} {source_alias}" in jc:
                    continue
                new_joins.append(jc)
            context.join_clauses = new_joins

            _remove_ts_conditions_from_where(context, rel.variable)
            return

    if rel.types and len(rel.types) == 1:
        engine = getattr(context, "_engine", None)
        src_label = next((lbl for lbl in source_node.labels), None) if source_node.labels else None
        tgt_label = next((lbl for lbl in target_node.labels), None) if target_node.labels else None
        if engine and src_label and tgt_label:
            rel_map = engine.get_rel_mapping(src_label, rel.types[0], tgt_label)
            if rel_map:
                src_mapping = engine.get_table_mapping(src_label)
                tgt_mapping = engine.get_table_mapping(tgt_label)
                if src_mapping and tgt_mapping:
                    jt = "LEFT OUTER JOIN" if optional else "JOIN"
                    tgt_tbl = sanitize_identifier(tgt_mapping["sql_table"])
                    tgt_id_col = tgt_mapping["id_column"]
                    src_id_col = src_mapping["id_column"]
                    if rel_map.get("target_fk"):
                        tfk = sanitize_identifier(rel_map["target_fk"])
                        context.join_clauses.append(
                            f"{jt} {tgt_tbl} {target_alias} ON {target_alias}.{tfk} = {source_alias}.{src_id_col}"
                        )
                    elif rel_map.get("via_table"):
                        via_tbl = sanitize_identifier(rel_map["via_table"])
                        vs = sanitize_identifier(rel_map["via_source"])
                        vt = sanitize_identifier(rel_map["via_target"])
                        via_alias = context.next_alias("vj")
                        context.join_clauses.append(
                            f"{jt} {via_tbl} {via_alias} ON {via_alias}.{vs} = {source_alias}.{src_id_col}"
                        )
                        context.join_clauses.append(
                            f"{jt} {tgt_tbl} {target_alias} ON {target_alias}.{tgt_id_col} = {via_alias}.{vt}"
                        )
                    context.mapped_node_aliases[target_alias] = tgt_mapping
                    return

    s_col = _node_col(source_node.variable, source_alias)
    t_col = _node_col(target_node.variable, target_alias)
    jt = "LEFT OUTER JOIN" if optional else "JOIN"
    
    if rel.direction == ast.Direction.OUTGOING:
        edge_cond, target_on = f"{edge_alias}.s = {source_alias}.{s_col}", f"{target_alias}.{t_col} = {edge_alias}.o_id"
    elif rel.direction == ast.Direction.INCOMING:
        edge_cond, target_on = f"{edge_alias}.o_id = {source_alias}.{s_col}", f"{target_alias}.{t_col} = {edge_alias}.s"
    else:
        edge_cond = f"({edge_alias}.s = {source_alias}.{s_col} OR {edge_alias}.o_id = {source_alias}.{s_col})"
        target_on = f"({target_alias}.{t_col} = {edge_alias}.o_id OR {target_alias}.{t_col} = {edge_alias}.s)"
        
    if rel.types:
        if len(rel.types) == 1: edge_cond += f" AND {edge_alias}.p = {context.add_join_param(rel.types[0])}"
        else: edge_cond += f" AND {edge_alias}.p IN ({', '.join([context.add_join_param(t) for t in rel.types])})"
        
    context.join_clauses.append(f"{jt} {_table('rdf_edges')} {edge_alias} ON {edge_cond}")

    if is_new_target and not target_alias.startswith('Stage'):
        context.join_clauses.append(f"{jt} {_table('nodes')} {target_alias} ON {target_on}")
    else:
        # If target node is already joined, add the connection as a WHERE condition
        context.where_conditions.append(target_on)

    # Apply inline property filters from source and target nodes.
    # These are silently dropped without this block — e.g. MATCH (t)-[:R]->(c {id: 'x'})
    # returns all nodes instead of filtering, because the relationship path never applies them.
    for prop_node, prop_alias in ((source_node, source_alias), (target_node, target_alias)):
        if not prop_node.properties:
            continue
        for k, v in prop_node.properties.items():
            val_sql = translate_expression(v, context, segment="where")
            if k in ("node_id", "id"):
                context.where_conditions.append(f"{prop_alias}.node_id = {val_sql}")
            else:
                p_alias = context.next_alias("p")
                context.join_clauses.append(
                    f"{jt} {_table('rdf_props')} {p_alias} "
                    f"ON {p_alias}.s = {prop_alias}.node_id AND {p_alias}.\"key\" = {context.add_join_param(k)}"
                )
                context.where_conditions.append(f"{p_alias}.val = {val_sql}")


def translate_where_clause(where, context):
    context.where_conditions.append(translate_boolean_expression(where.expression, context))


def _is_temporal_ts_condition(expr, context) -> bool:
    if not isinstance(expr, ast.BooleanExpression): return False
    if expr.operator not in _TEMPORAL_TS_OPS: return False
    if not expr.operands: return False
    left = expr.operands[0]
    return (
        isinstance(left, ast.PropertyReference)
        and left.property_name == "ts"
        and left.variable in context.temporal_rel_ctes
    )


def translate_boolean_expression(expr, context) -> str:
    if isinstance(expr, ast.ExistsExpression):
        pat = expr.pattern
        if pat.relationships:
            rel = pat.relationships[0]
            src_node = pat.nodes[0]
            tgt_node = pat.nodes[1] if len(pat.nodes) > 1 else None
            src_bound = src_node.variable and src_node.variable in context.variable_aliases
            tgt_bound = tgt_node and tgt_node.variable and tgt_node.variable in context.variable_aliases
            edge_alias = f"_ex{len(context.variable_aliases) + 1}"
            if tgt_bound:
                tgt_ref = context.variable_aliases[tgt_node.variable]
                cond = f"{edge_alias}.o_id = {tgt_ref}.node_id"
            elif src_bound:
                src_ref = context.variable_aliases[src_node.variable]
                cond = f"{edge_alias}.s = {src_ref}.node_id"
            else:
                cond = "1=1"
            if rel.types:
                cond += f" AND {edge_alias}.p = '{rel.types[0]}'"
            sub = f"SELECT 1 FROM {_table('rdf_edges')} {edge_alias} WHERE {cond}"
            prefix = "NOT " if expr.negated else ""
            return f"{prefix}EXISTS ({sub})"
        return "1=1"
    if not isinstance(expr, ast.BooleanExpression): return translate_expression(expr, context, segment="where")
    op = expr.operator
    if op == ast.BooleanOperator.AND:
        parts = []
        for o in expr.operands:
            if _is_temporal_ts_condition(o, context):
                continue
            parts.append(translate_boolean_expression(o, context))
        return "(" + " AND ".join(parts) + ")" if parts else "1=1"
    if op == ast.BooleanOperator.OR: return "(" + " OR ".join(translate_boolean_expression(o, context) for o in expr.operands) + ")"
    if op == ast.BooleanOperator.NOT: return f"NOT ({translate_boolean_expression(expr.operands[0], context)})"
    left_expr = expr.operands[0]
    right_expr = expr.operands[1] if len(expr.operands) > 1 else None
    left = translate_expression(left_expr, context, segment="where")
    if op == ast.BooleanOperator.IS_NULL: return f"{left} IS NULL"
    if op == ast.BooleanOperator.IS_NOT_NULL: return f"{left} IS NOT NULL"
    right = translate_expression(right_expr, context, segment="where")
    if op in (
        ast.BooleanOperator.LESS_THAN,
        ast.BooleanOperator.LESS_THAN_OR_EQUAL,
        ast.BooleanOperator.GREATER_THAN,
        ast.BooleanOperator.GREATER_THAN_OR_EQUAL,
    ):
        if isinstance(left_expr, ast.PropertyReference):
            left = f"CAST({left} AS DOUBLE)"
        if isinstance(right_expr, ast.PropertyReference):
            right = f"CAST({right} AS DOUBLE)"
    if op == ast.BooleanOperator.EQUALS: return f"{left} = {right}"
    if op == ast.BooleanOperator.NOT_EQUALS: return f"{left} <> {right}"
    if op == ast.BooleanOperator.LESS_THAN: return f"{left} < {right}"
    if op == ast.BooleanOperator.LESS_THAN_OR_EQUAL: return f"{left} <= {right}"
    if op == ast.BooleanOperator.GREATER_THAN: return f"{left} > {right}"
    if op == ast.BooleanOperator.GREATER_THAN_OR_EQUAL: return f"{left} >= {right}"
    if op == ast.BooleanOperator.STARTS_WITH: return f"{left} LIKE ({right} || '%')"
    if op == ast.BooleanOperator.ENDS_WITH: return f"{left} LIKE ('%' || {right})"
    if op == ast.BooleanOperator.CONTAINS: return f"{left} LIKE ('%' || {right} || '%')"
    if op == ast.BooleanOperator.IN: return f"{left} IN {right}"
    raise ValueError(f"Unsupported operator: {op}")


def _inline_literal(expr) -> Optional[str]:
    if expr is None:
        return None
    if isinstance(expr, ast.Literal):
        v = expr.value
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return str(v)
        return f"'{str(v)}'"
    return None


def translate_expression(expr, context, segment="select") -> str:

    if isinstance(expr, ast.CaseExpression):
        parts = ["CASE"]
        if expr.test_expression is not None:
            parts.append(translate_expression(expr.test_expression, context, segment))
        for wc in expr.when_clauses:
            if isinstance(wc.condition, ast.BooleanExpression):
                cond = translate_boolean_expression(wc.condition, context)
            else:
                cond = translate_expression(wc.condition, context, segment)
            res = _inline_literal(wc.result)
            if res is None:
                res = translate_expression(wc.result, context, segment)
            parts.append(f"WHEN {cond} THEN {res}")
        else_res = _inline_literal(expr.else_result) if expr.else_result is not None else None
        if else_res is None and expr.else_result is not None:
            else_res = translate_expression(expr.else_result, context, segment)
        if else_res is not None:
            parts.append(f"ELSE {else_res}")
        parts.append("END")
        return " ".join(parts)
    if isinstance(expr, ast.PropertyReference):
        alias = context.variable_aliases.get(expr.variable)
        if not alias: raise ValueError(f"Undefined: {expr.variable}")
        cte_alias = context.temporal_rel_ctes.get(expr.variable)
        if cte_alias is not None:
            if expr.property_name == "ts":
                return f"{cte_alias}.ts"
            if expr.property_name in ("weight", "w"):
                return f"{cte_alias}.weight"
        temporal_node_col = getattr(context, "temporal_node_col", {})
        if expr.variable in temporal_node_col:
            col = temporal_node_col[expr.variable]
            cte_name = context.variable_aliases[expr.variable]
            if expr.property_name in ("id", "node_id"):
                return f"{cte_name}.{col}"
        if expr.property_name in ("ts", "weight", "w") and expr.variable not in context.temporal_rel_ctes:
            if alias and alias.startswith("e"):
                m = getattr(context, "_metadata", None)
                if m is not None:
                    m.warnings.append(
                        f"{expr.variable}.{expr.property_name} in RETURN without WHERE {expr.variable}.ts filter "
                        f"— {expr.property_name} will be NULL. Add WHERE {expr.variable}.ts >= $start AND "
                        f"{expr.variable}.ts <= $end for temporal routing."
                    )
                return "NULL"
        if alias in context.mapped_node_aliases:
            mapping = context.mapped_node_aliases[alias]
            if expr.property_name in ("id", "node_id"):
                return f"{alias}.{sanitize_identifier(mapping['id_column'])}"
            return f"{alias}.{sanitize_identifier(expr.property_name)}"
        if alias.startswith('Stage'):
            if expr.property_name in ("node_id", "id"): return f"{alias}.{expr.variable}"
            return f"{alias}.{expr.variable}_{expr.property_name}"
        if expr.property_name in ("node_id", "id"): return f"{alias}.node_id"
        p_alias = context.next_alias("p")
        context.join_clauses.append(f"LEFT JOIN {_table('rdf_props')} {p_alias} ON {p_alias}.s = {alias}.node_id AND {p_alias}.\"key\" = {context.add_join_param(expr.property_name)}")
        return f"{p_alias}.val"
    if isinstance(expr, ast.Variable):
        alias = context.variable_aliases.get(expr.name)
        if not alias:
            if expr.name in context.input_params:
                v = context.input_params[expr.name]
                if segment == "select": return context.add_select_param(v)
                if segment == "join": return context.add_join_param(v)
                return context.add_where_param(v)
            raise ValueError(f"Undefined: {expr.name}")
        if alias.startswith('Stage'): return f"{alias}.{expr.name}"
        if alias.startswith('e'): return f"{alias}.p"
        if expr.name in context.scalar_variables:
            if alias == "scalar":
                return expr.name
            return f"{alias}.{expr.name}"
        if alias in context.mapped_node_aliases:
            mapping = context.mapped_node_aliases[alias]
            return f"{alias}.{sanitize_identifier(mapping['id_column'])}"
        return f"{alias}.node_id"
    if isinstance(expr, ast.Literal):
        v = expr.value
        # Boolean/NULL literals translate to SQL constants — no parameterization needed
        if v is True: return "1"
        if v is False: return "0"
        if v is None: return "NULL"
        if segment == "select": return context.add_select_param(v)
        if segment == "join": return context.add_join_param(v)
        return context.add_where_param(v)
    if isinstance(expr, ast.AggregationFunction):
        arg = translate_expression(expr.argument, context, segment=segment) if expr.argument else "*"
        fn = "JSON_ARRAYAGG" if expr.function_name.upper() == "COLLECT" else expr.function_name.upper()
        return f"{fn}({'DISTINCT ' if expr.distinct else ''}{arg})"
    if isinstance(expr, ast.FunctionCall):
        fn = expr.function_name.lower()

        if fn in ("length", "nodes", "relationships") and len(expr.arguments) == 1:
            arg = expr.arguments[0]
            if isinstance(arg, ast.Variable) and arg.name in context.named_paths:
                path_var = arg.name
                if fn == "length":
                    return str(len(context.named_paths[path_var].pattern.relationships))
                elif fn == "nodes":
                    aliases = context.path_node_aliases[path_var]
                    return f"JSON_ARRAY({', '.join(f'{a}.node_id' for a in aliases)})"
                else:
                    aliases = context.path_edge_aliases[path_var]
                    return f"JSON_ARRAY({', '.join(f'{a}.p' for a in aliases)})"
            elif isinstance(arg, ast.Variable) and arg.name not in context.named_paths:
                if fn in ("nodes", "relationships"):
                    raise ValueError(f"'{arg.name}' is not a named path variable")

        fn, args = expr.function_name.lower(), [translate_expression(arg, context, segment=segment) for arg in expr.arguments]
        if fn in ("id", "type"): return args[0] if args else "NULL"
        if fn == "labels": return labels_subquery(args[0] if args else "NULL")
        if fn == "properties": return properties_subquery(args[0] if args else "NULL")
        # Cypher → SQL function name mapping
        _CYPHER_FN_MAP = {
            "tolower": "LOWER",
            "toupper": "UPPER",
            "trim": "TRIM",
            "ltrim": "LTRIM",
            "rtrim": "RTRIM",
            "tostring": "CAST",   # handled below
            "tointeger": "CAST",  # handled below
            "tofloat": "CAST",    # handled below
            "size": "LENGTH",
            "length": "LENGTH",
            "substring": "SUBSTRING",
            "left": "LEFT",
            "right": "RIGHT",
            "split": "STRTOK_TO_TABLE",  # IRIS equivalent
            "replace": "REPLACE",
            "reverse": "REVERSE",
            "abs": "ABS",
            "ceil": "CEILING",
            "floor": "FLOOR",
            "round": "ROUND",
            "sqrt": "SQRT",
            "sign": "SIGN",
            "coalesce": "COALESCE",
            "nullif": "NULLIF",
            "exists": "EXISTS",
            "keys": "NULL",  # not directly supported
            "toboolean": "CASE WHEN",  # handled below
        }
        sql_fn = _CYPHER_FN_MAP.get(fn, fn.upper())
        if fn == "tointeger":  return f"CAST({args[0]} AS INTEGER)"
        if fn == "tofloat":    return f"CAST({args[0]} AS DOUBLE)"
        if fn == "tostring":   return f"CAST({args[0]} AS VARCHAR(4096))"
        if fn == "toboolean":  return f"CASE WHEN LOWER({args[0]}) IN ('true','1','yes','y') THEN 1 ELSE 0 END"
        return f"{sql_fn}({', '.join(args)})"
    return "NULL"


def translate_return_clause(ret, context):
    has_agg = any(isinstance(i.expression, ast.AggregationFunction) for i in ret.items)
    for item in ret.items:
        if isinstance(item.expression, ast.Variable):
            var_name = item.expression.name
            if var_name in context.named_paths:
                alias = item.alias or var_name
                node_aliases = context.path_node_aliases[var_name]
                edge_aliases = context.path_edge_aliases[var_name]
                nodes_arr = ", ".join(f"{a}.node_id" for a in node_aliases)
                rels_arr = ", ".join(f"{a}.p" for a in edge_aliases)
                json_expr = f"'{{\"nodes\":' || JSON_ARRAY({nodes_arr}) || ',\"rels\":' || JSON_ARRAY({rels_arr}) || '}}'"
                context.select_items.append(f"{json_expr} AS {alias}")
                continue
            alias_name = context.variable_aliases.get(var_name)
            is_scalar = var_name in context.scalar_variables
            if alias_name == "scalar":
                continue
            if alias_name and not alias_name.startswith("e") and not is_scalar:
                prefix = item.alias or var_name
                node_expr = f"{alias_name}.{var_name}" if alias_name.startswith("Stage") or alias_name in ("VecSearch", "BM25", "PPR") else f"{alias_name}.node_id"
                context.select_items.append(f"{node_expr} AS {prefix}_id")
                context.select_items.append(f"{labels_subquery(node_expr)} AS {prefix}_labels")
                context.select_items.append(f"{properties_subquery(node_expr)} AS {prefix}_props")
                continue
        sql = translate_expression(item.expression, context, segment="select")
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference): alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable): alias = item.expression.name
            elif isinstance(item.expression, (ast.AggregationFunction, ast.FunctionCall)): alias = f"{item.expression.function_name}_res"
        if alias: context.select_items.append(f"{sql} AS {alias.replace('.', '_')}")
        else: context.select_items.append(sql)
        if has_agg and not isinstance(item.expression, ast.AggregationFunction): context.group_by_items.append(sql)


def translate_with_clause(with_clause, context):
    has_agg = any(isinstance(i.expression, ast.AggregationFunction) for i in with_clause.items)
    for item in with_clause.items:
        sql = translate_expression(item.expression, context, segment="select")
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference): alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable): alias = item.expression.name
            elif isinstance(item.expression, ast.AggregationFunction): alias = f"{item.expression.function_name}"
        if alias is None: alias = context.next_alias("v")
        context.select_items.append(f"{sql} AS {alias.replace('.', '_')}")
        if has_agg and not isinstance(item.expression, ast.AggregationFunction): context.group_by_items.append(sql)
    if with_clause.where_clause: context.where_conditions.append(translate_boolean_expression(with_clause.where_clause.expression, context))
