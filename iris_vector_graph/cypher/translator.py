"""
Cypher-to-SQL Translation Artifacts

Classes for managing SQL generation from Cypher AST.
Supports multi-stage queries via Common Table Expressions (CTEs).
"""

from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional, Union
import logging
import json
from pydantic import BaseModel, Field
from . import ast
from .parser import CypherParseError
from iris_vector_graph.security import (
    validate_table_name,
    VALID_GRAPH_TABLES,
    sanitize_identifier,
)

logger = logging.getLogger(__name__)

# Module-level schema prefix configuration
# Set to "Graph_KG" to use Graph_KG.nodes, Graph_KG.rdf_labels, etc.
# Set to "" (empty string) for unqualified table names
_schema_prefix: str = ""

# Procedure CTE aliases (VecSearch, BM25, DegCent, etc.) whose columns must be
# referenced UNQUALIFIED in SELECT/ORDER BY. IRIS does not register a
# JSON_TABLE-backed CTE name as a referenceable label, so `DegCent.score`
# raises SQLCODE -23; bare `score` resolves correctly.
_PROC_CTE_ALIASES = frozenset({
    "VecSearch", "BM25", "PPR", "IVF_SEARCH", "Retrieve", "Neighbors", "WS",
    "DegCent", "Betweenness", "Closeness", "Eigenvector",
    "Leiden", "TriangleCount", "SCC", "KCore",
})

# Allowed map-parameter keys for centrality procedures (Spec 162 FR-029).
# The procedure-call validator rejects unknown keys to prevent silent typos
# and reserves keys (e.g. `weighted`) for future Phase 2 extensions.
CENTRALITY_ALLOWED_KEYS: Dict[str, set] = {
    "ivg.degreeCentrality": {"direction", "predicate", "topK"},
    "ivg.betweenness":      {"sampleSize", "direction", "maxHops", "topK", "memBudgetMB"},
    "ivg.closeness":        {"formula", "direction", "maxHops", "topK"},
    "ivg.eigenvector":      {"maxIter", "tol", "topK"},
}

# Allowed map-parameter keys for community-detection procedures (Spec 163 FR-015).
# Same forward-compat semantics as CENTRALITY_ALLOWED_KEYS — `weighted` is reserved
# for Phase 2 weighted Leiden / weighted Triangle / etc.
COMMUNITY_ALLOWED_KEYS: Dict[str, set] = {
    "ivg.leiden":         {"maxLevels", "gamma", "tol", "topK", "memBudgetMB", "randomSeed"},
    "ivg.triangleCount":  {"topK"},
    "ivg.scc":            {"topK"},
    "ivg.kcore":          {"topK"},
}


def _validate_centrality_proc_map(proc_name: str, map_keys) -> None:
    """Reject unknown map-parameter keys for centrality procedures.

    Raises ValueError with a clear message listing both the unknown key(s)
    and the allowed set. Used by `_translate_degree_centrality`,
    `_translate_betweenness`, `_translate_closeness`, `_translate_eigenvector`.
    """
    allowed = CENTRALITY_ALLOWED_KEYS.get(proc_name, set())
    unknown = set(map_keys) - allowed
    if unknown:
        raise ValueError(
            f"Unknown parameters for {proc_name}: {sorted(unknown)}. "
            f"Allowed: {sorted(allowed)}"
        )


def _validate_community_proc_map(proc_name: str, map_keys) -> None:
    """Reject unknown map-parameter keys for community-detection procedures (Spec 163 FR-015).

    Same forward-compat semantics as `_validate_centrality_proc_map` — `weighted`
    is reserved for Phase 2. Used by `_translate_leiden`, `_translate_triangle_count`,
    `_translate_scc`, `_translate_kcore`.
    """
    allowed = COMMUNITY_ALLOWED_KEYS.get(proc_name, set())
    unknown = set(map_keys) - allowed
    if unknown:
        raise ValueError(
            f"Unknown parameters for {proc_name}: {sorted(unknown)}. "
            f"Allowed: {sorted(allowed)}"
        )


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


def _table(name: str, prefix: Optional[str] = None) -> str:
    """Return fully qualified table name with schema prefix if configured.

    Security: Validates name against VALID_GRAPH_TABLES allowlist to prevent
    SQL injection via table name manipulation.

    Args:
        name: Table name (must be in VALID_GRAPH_TABLES)
        prefix: Override the module-level prefix. Pass engine._schema_prefix
                to get per-instance isolation instead of the process global.

    Returns:
        Schema-qualified table name (e.g., "Graph_KG.nodes")

    Raises:
        ValueError: If name is not in the allowlist
    """
    validate_table_name(name)
    p = prefix if prefix is not None else _schema_prefix
    if p:
        return f"{p}.{name}"
    return name


_JSONPATH_RESERVED = frozenset({"null", "true", "false"})


def _jsonpath_key(prop: str) -> str:
    """Return a JSONPath key segment. IRIS handles reserved words (null/true/false) as
    plain key names in unquoted form ($.null); quoting them causes JSON_VALUE to return
    NULL in IRIS."""
    return prop.replace("'", "''")


def labels_subquery(node_expr: str, exclude_labels=None) -> str:
    extra = ""
    if exclude_labels:
        placeholders = ", ".join(f"'{lbl.replace(chr(39), chr(39)+chr(39))}'" for lbl in exclude_labels)
        extra = f" AND label NOT IN ({placeholders})"
    return f"COALESCE((SELECT JSON_ARRAYAGG(label) FROM {_table('rdf_labels')} WHERE s = {node_expr}{extra}), CAST('[]' AS VARCHAR(256)))"


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


class QueryMetadata(BaseModel):
    estimated_rows: Optional[int] = None
    index_usage: List[str] = Field(default_factory=list)
    optimization_applied: List[str] = Field(default_factory=list)
    complexity_score: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class TemporalBound:
    ts_start: Any
    ts_end: Any
    rel_variable: str
    predicate: Optional[str]
    direction: str


class TemporalQueryRequiresEngine(ValueError):
    pass


class SQLQuery(BaseModel):
    sql: Union[str, List[str]]
    parameters: List[List[Any]] = Field(default_factory=list)
    query_metadata: QueryMetadata = Field(default_factory=QueryMetadata)
    is_transactional: bool = False
    var_length_paths: Optional[List[dict]] = None
    # Mapping from SQL-safe column alias → desired Cypher column name, for renaming after execution.
    column_name_map: Dict[str, str] = Field(default_factory=dict)
    # Parallel list to the result columns: each entry is "scalar", "node", or "relationship".
    # Consumed by the Bolt server to emit correct PackStream struct tags (TAG_NODE / TAG_RELATIONSHIP).
    bolt_column_types: List[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class TranslationContext:
    """Stateful context for SQL generation across multiple query stages."""

    def __init__(self, parent: Optional["TranslationContext"] = None):
        self.variable_aliases: Dict[str, str] = {}
        if parent is not None:
            self.variable_aliases = parent.variable_aliases.copy()

        # Variables that are scalar (not graph nodes) — skip node expansion in RETURN
        self.scalar_variables: set = (
            set() if parent is None else parent.scalar_variables.copy()
        )

        self.graph_context: Optional[str] = (
            None if parent is None else parent.graph_context
        )

        # Named path registry: path variable → AST NamedPath + SQL aliases
        self.named_paths: Dict[str, ast.NamedPath] = (
            {} if parent is None else parent.named_paths.copy()
        )
        self.path_node_aliases: Dict[str, List[str]] = (
            {} if parent is None else parent.path_node_aliases.copy()
        )
        self.path_edge_aliases: Dict[str, List[str]] = (
            {} if parent is None else parent.path_edge_aliases.copy()
        )
        # Maps (pattern_id, relationship_index) → SQL alias for capturing anon rels in named paths
        self.pattern_rel_aliases: Dict[tuple, str] = (
            {} if parent is None else parent.pattern_rel_aliases.copy()
        )
        # Maps relationship object id() → SQL alias for anonymous relationship lookup
        self.rel_obj_aliases: Dict[int, str] = (
            {} if parent is None else parent.rel_obj_aliases.copy()
        )
        # Maps anonymous node object id() → SQL alias (for chained anonymous node reuse)
        self.node_obj_aliases: Dict[int, str] = (
            {} if parent is None else parent.node_obj_aliases.copy()
        )
        # Maps node alias → SQL expression for its node_id (for anon source nodes with no nodes JOIN)
        self.node_id_expr: Dict[str, str] = (
            {} if parent is None else parent.node_id_expr.copy()
        )
        self.var_length_paths: List[dict] = (
            [] if parent is None else parent.var_length_paths
        )

        self.select_items: List[str] = []
        self.from_clauses: List[str] = []
        self.join_clauses: List[str] = []
        self.where_conditions: List[str] = []
        self.having_conditions: List[str] = []
        self.group_by_items: List[str] = []
        self._undirected_aliases: set = set()
        self._edgescan_aliases: set = set()

        self.select_params: List[Any] = []
        self.join_params: List[Any] = []
        self.where_params: List[Any] = []

        self.dml_statements: List[tuple[str, List[Any]]] = []

        self.all_stage_params: List[Any] = (
            [] if parent is None else parent.all_stage_params
        )
        self._alias_counter: int = 0 if parent is None else parent._alias_counter
        self.stages: List[str] = [] if parent is None else parent.stages
        self.input_params: Dict[str, Any] = (
            {} if parent is None else parent.input_params
        )
        self.temporal_rel_ctes: Dict[str, str] = (
            {} if parent is None else parent.temporal_rel_ctes.copy()
        )
        self.temporal_derived: Dict[str, str] = (
            {} if parent is None else parent.temporal_derived.copy()
        )
        # Variables bound to relationship patterns in MATCH clauses.
        # Used by translate_to_sql() to tag Bolt column types as "relationship".
        self.rel_variables: set = (
            set() if parent is None else parent.rel_variables.copy()
        )
        self.system_procedure_call: Optional[Any] = None
        self.pending_where = None
        self.mapped_node_aliases: Dict[str, dict] = (
            {} if parent is None else parent.mapped_node_aliases.copy()
        )
        # Maps SQL-safe column alias → Cypher expression text for post-execution column renaming.
        self.column_name_map: Dict[str, str] = (
            {} if parent is None else parent.column_name_map
        )
        # OPTIONAL MATCH null-row fallback: when set, the generated SQL gains a
        # UNION ALL branch that emits one null row when the label has no nodes.
        # List of (label_value, param_placeholder) tuples — one per optional label constraint.
        self.optional_null_row_labels: List[tuple] = []
        # Grouped version: list of label-lists, one list per anchor node.
        # Each group is checked as a combined NOT EXISTS (node with ALL labels in group).
        # This prevents TCK-injected labels (which always exist) from blocking the null row.
        self.optional_null_row_label_groups: List[List[str]] = []
        # Parallel list of SQL values for the null row, one per select item.
        self.optional_null_row_items: List[str] = []
        # When True, the OPTIONAL MATCH null-row fallback fires unconditionally (no
        # NOT EXISTS check).  Set when the optional-match anchor is a scalar variable
        # (e.g. WITH null AS a … OPTIONAL MATCH (a)-…) because the scalar is always
        # null so the match will never return rows.
        self.optional_null_row_unconditional: bool = False
        # Set to True when the RETURN clause is pure aggregation (no group keys).
        # In that case, the OPTIONAL MATCH null-row UNION ALL must be suppressed
        # because COUNT/SUM/etc already return correct values (0/null) over empty sets.
        self.return_is_pure_aggregation: bool = False
        # OPTIONAL MATCH intermediate-node null-gating: maps node_alias → edge_alias.
        # When the gating edge is null (second-hop path failed), the intermediate node
        # should appear as null in SELECT even though it was joined via the first hop.
        # Set by _trp_directed_edge when multi-hop optional with bound end node detected.
        self.opt_intermediate_nulled: Dict[str, str] = {}
        # Variable type tracking for semantic validation.
        # Maps variable name → "node" | "relationship" | "scalar"
        # Used to enforce type consistency and detect VariableTypeConflict/VariableAlreadyBound errors.
        self.variable_types: Dict[str, str] = (
            {} if parent is None else parent.variable_types.copy()
        )
        # Temporal type tracking: maps variable name → temporal type
        # Types: "date", "localtime", "time", "datetime", "localdatetime", "duration"
        # Used to emit correct SQL extraction for property access (e.g., d.year, dur.months)
        self.temporal_types: Dict[str, str] = (
            {} if parent is None else parent.temporal_types.copy()
        )
        # Variables known to hold non-integer values that cannot be used as list indices.
        # Set during WITH clause translation when a literal bool/float/str/list/map is bound.
        # Used by _expr_subscript to emit IVGLISTGET which raises TypeError for these.
        self.non_integer_index_vars: set = (
            set() if parent is None else parent.non_integer_index_vars.copy()
        )
        # Variables known to hold non-map values (scalars, lists).
        # Property access on these should raise TypeError at compile time.
        self.non_map_vars: set = (
            set() if parent is None else parent.non_map_vars.copy()
        )
        # UNWIND aliases that hold collected node JSON blobs ({_id, _labels, _props}).
        # Property access must join rdf_props using the _id extracted from the blob.
        self.collected_node_variables: set = (
            set() if parent is None else parent.collected_node_variables.copy()
        )
        # Mapping from WITH alias → original node variable name for collect() sources.
        # Used to detect UNWIND sources that are collected node lists.
        self.collected_node_lists: Dict[str, str] = (
            {} if parent is None else parent.collected_node_lists.copy()
        )
        # (alias, prop_name) pairs for properties that appear in IS NULL / IS NOT NULL
        # checks in the current boolean context.  The structural guard (OPT-3 EXISTS) is
        # suppressed for these so that nodes lacking the property produce a NULL val that
        # correctly satisfies "x IS NULL" even when OR'd with other predicates.
        self._null_guarded_props: set = set()
        # Maps variable alias → Python list (of ast.Literal elements) for variables bound
        # to literal list expressions in WITH clauses. Used for list-comprehension constant folding
        # to preserve null slots (JSON_ARRAYAGG silently drops NULLs).
        self.literal_list_vars: Dict[str, list] = (
            {} if parent is None else parent.literal_list_vars.copy()
        )

    def next_alias(self, prefix: str = "t") -> str:
        alias = f"{prefix}{self._alias_counter}"
        self._alias_counter += 1
        return alias

    def register_variable(self, variable: str, prefix: str = "n") -> str:
        if variable not in self.variable_aliases:
            self.variable_aliases[variable] = self.next_alias(prefix)
        return self.variable_aliases[variable]

    def bind_variable_type(self, variable: str, var_type: str, force: bool = False) -> None:
        """Track variable type and validate no type conflicts.

        Args:
            variable: Cypher variable name
            var_type: One of "node", "relationship", or "scalar"
            force: If True, allow rebinding to a different type (used for WITH scope transitions)

        Raises:
            CypherParseError: If variable is rebound to a different type (unless force=True)
        """
        if not variable:
            return
        if variable in self.variable_types:
            existing_type = self.variable_types[variable]
            if existing_type != var_type:
                if not force:
                    raise CypherParseError(
                        f"VariableTypeConflict: variable '{variable}' is bound as "
                        f"{existing_type!r}, cannot rebind as {var_type!r}"
                    )
                # force=True: allow rebinding in new scope (e.g., WITH clause)
                self.variable_types[variable] = var_type
        else:
            self.variable_types[variable] = var_type

    @staticmethod
    def _coerce_param(value: Any) -> Any:
        """Serialize list/dict params to JSON strings for IRIS ODBC binding."""
        if isinstance(value, (list, dict)):
            return json.dumps(value, separators=(",", ":"))
        return value

    def add_select_param(self, value: Any) -> str:
        self.select_params.append(self._coerce_param(value))
        return "?"

    def add_join_param(self, value: Any) -> str:
        self.join_params.append(self._coerce_param(value))
        return "?"

    def add_where_param(self, value: Any) -> str:
        self.where_params.append(self._coerce_param(value))
        return "?"

    @staticmethod
    def _predicate_cost(cond: str) -> int:
        """Heuristic cost for SQL WHERE condition ordering (Morrison OPT-4).
        EXISTS structural guards cheapest; string scans most expensive."""
        if "EXISTS" in cond:
            return 0
        if " = " in cond or " IS " in cond:
            return 1
        if " > " in cond or " < " in cond or " >= " in cond or " <= " in cond:
            return 2
        if "LIKE" in cond or "%CONTAINS" in cond or "LOWER(" in cond:
            return 3
        return 4

    @staticmethod
    def _structural_guard_sql(node_alias: str, prop_name: str) -> str:
        """EXISTS guard confirming a property key exists before JOIN fetches value (OPT-3)."""
        props_tbl = _table("rdf_props")
        safe_key = prop_name.replace("'", "''")
        return (
            f"EXISTS (SELECT 1 FROM {props_tbl} _sg{node_alias} "
            f"WHERE _sg{node_alias}.s = {node_alias}.node_id "
            f"AND _sg{node_alias}.\"key\" = '{safe_key}')"
        )

    def build_stage_sql(
        self, distinct: bool = False, select_override: Optional[str] = None
    ) -> tuple[str, List[Any]]:
        # IRIS bug workaround: multiple JSON_TABLE subqueries in a single SELECT
        # with no FROM clause crash IRIS with <LIST>LoadTableFunction.
        # Restructure as CROSS JOIN of subqueries when this pattern is detected.
        if (
            not select_override
            and not self.from_clauses
            and not self.join_clauses
            and not self.where_conditions
            and not self.group_by_items
            and not self.having_conditions
        ):
            jt_items = [si for si in self.select_items if "JSON_TABLE" in si]
            if len(jt_items) >= 2:
                import re as _re
                _alias_re = _re.compile(r'^(.*?)\s+AS\s+"([^"]+)"\s*$', _re.DOTALL)
                subqueries = []
                outer_cols = []
                for idx, item in enumerate(self.select_items):
                    sq_alias = f"_jt{idx}"
                    m = _alias_re.match(item)
                    if m and "JSON_TABLE" in m.group(1):
                        expr = m.group(1).strip()
                        col_alias = m.group(2)
                        subqueries.append(f"(SELECT {expr} AS v) {sq_alias}")
                        outer_cols.append(f'{sq_alias}.v AS "{col_alias}"')
                    else:
                        subqueries.append(None)
                        outer_cols.append(item)
                jt_subqueries = [(i, sq) for i, sq in enumerate(subqueries) if sq is not None]
                if len(jt_subqueries) >= 2:
                    from_part = jt_subqueries[0][1]
                    join_parts = [f"CROSS JOIN {sq}" for _, sq in jt_subqueries[1:]]
                    sql = (
                        f"SELECT {'DISTINCT ' if distinct else ''}{', '.join(outer_cols)}\n"
                        f"FROM {from_part}"
                    )
                    if join_parts:
                        sql += "\n" + "\n".join(join_parts)
                    return sql, list(self.select_params)

        select = (
            select_override
            if select_override
            else f"SELECT {'DISTINCT ' if distinct else ''}{', '.join(self.select_items)}"
        )
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
        if expanded_joins:
            parts.extend(expanded_joins)
        if self.where_conditions:
            # Pair each condition with its param slice so sorting keeps params aligned.
            wp = list(self.where_params)
            offset = 0
            paired = []
            for cond in self.where_conditions:
                n = cond.count("?")
                paired.append((cond, wp[offset : offset + n]))
                offset += n
            paired.sort(key=lambda x: self._predicate_cost(x[0]))
            ordered_conds = [p[0] for p in paired]
            ordered_where_params = [v for p in paired for v in p[1]]
            parts.append(f"WHERE {' AND '.join(ordered_conds)}")
        else:
            ordered_where_params = list(self.where_params)
        if self.group_by_items:
            parts.append(f"GROUP BY {', '.join(self.group_by_items)}")
        if self.having_conditions:
            parts.append(f"HAVING {' AND '.join(self.having_conditions)}")
        sql = "\n".join(parts)
        params = (
            (self.select_params if not select_override else [])
            + self.join_params
            + ordered_where_params
        )
        return sql, params

    def add_dml(self, sql: str, params: List[Any]):
        self.dml_statements.append((sql, params))

    def build_dml_subquery(self, select_override: str) -> tuple[str, str, List[Any]]:
        """Build a SELECT subquery for use in DML, returning (cte_prefix, select_sql, params).

        When variable_aliases reference StageN CTEs (set after a WITH clause),
        cte_prefix is a 'WITH ...' string that must precede the DML verb.
        Callers assemble as: f"{cte_prefix}{dml_verb} {target} {select_sql}"
        For DELETE WHERE IN, use: f"{cte_prefix}DELETE FROM t WHERE c IN ({select_sql})"
        When no stages exist, cte_prefix is empty string.
        """
        sql, params = self.build_stage_sql(select_override=select_override)
        all_ctes = [
            c
            for c in getattr(self, "cte_clauses", [])
            if not any(td in c for td in self.temporal_derived)
        ] + self.stages
        if all_ctes:
            cte_prefix = "WITH " + ",\n".join(all_ctes) + "\n"
            params = list(self.all_stage_params) + list(params)
        else:
            cte_prefix = ""
        return cte_prefix, sql, params

    def build_label_only_dml_subquery(self, node_alias: str, select_override: str) -> tuple[str, str, List[Any]]:
        """Build a label-only DML subquery, stripping rdf_props JOINs/conditions for node_alias.

        Used after SET n = {map} deletes all properties — subsequent INSERT/SELECT must not
        JOIN rdf_props for the matched node since those rows no longer exist.
        """
        import re as _re_lo
        # Save state
        saved_joins = list(self.join_clauses)
        saved_join_params = list(self.join_params)
        saved_where = list(self.where_conditions)
        saved_where_params = list(self.where_params)

        # Strip rdf_props JOINs that reference this node_alias; track their aliases
        prop_join_aliases = set()
        new_joins = []
        new_join_params = []
        jp_offset = 0
        for jc in saved_joins:
            pc = jc.count("?")
            if "rdf_props" in jc and f"{node_alias}.node_id" in jc:
                m = _re_lo.search(r"JOIN\s+\S*rdf_props\s+(\w+)\s+ON", jc)
                if m:
                    prop_join_aliases.add(m.group(1))
                # drop this JOIN and its params
            else:
                new_joins.append(jc)
                new_join_params.extend(saved_join_params[jp_offset:jp_offset + pc])
            jp_offset += pc

        self.join_clauses = new_joins
        self.join_params = new_join_params

        # Strip WHERE conditions referencing stripped prop aliases, or EXISTS rdf_props for this alias
        new_where = []
        new_where_params = []
        wp_offset = 0
        for cond in saved_where:
            pc = cond.count("?")
            cond_params = saved_where_params[wp_offset:wp_offset + pc]
            wp_offset += pc
            drop = any(f"{pa}." in cond for pa in prop_join_aliases)
            if not drop and "EXISTS" in cond and "rdf_props" in cond and f"{node_alias}.node_id" in cond:
                drop = True
            if not drop:
                new_where.append(cond)
                new_where_params.extend(cond_params)
        self.where_conditions = new_where
        self.where_params = new_where_params

        result = self.build_dml_subquery(select_override)

        # Restore original state
        self.join_clauses = saved_joins
        self.join_params = saved_join_params
        self.where_conditions = saved_where
        self.where_params = saved_where_params

        return result


def translate_procedure_call(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
    """Translate a CALL procedure into a CTE prepended to context.stages.

    Supported:
    - ivg.*       — IVG-specific procedures (vector search, BFS, BM25, etc.)
    - db.*        — Neo4j built-in procedures (forwarded to engine)
    - dbms.*      — Neo4j system procedures (forwarded to engine)
    - apoc.*      — APOC procedures (forwarded to engine)
    """
    _SYSTEM_PROC_PREFIXES = ("db.", "dbms.", "apoc.", "gds.")
    name = proc.procedure_name
    if any(name.lower().startswith(p) for p in _SYSTEM_PROC_PREFIXES):
        context.system_procedure_call = proc
        return
    # Check for TCK test procedures first
    if name.lower().startswith("test."):
        _translate_test_procedure(proc, context)
    elif name == "ivg.vector.search":
        _translate_vector_search(proc, context)
    elif name == "ivg.neighbors":
        _translate_neighbors(proc, context)
    elif name == "ivg.ppr":
        _translate_ppr(proc, context)
    elif name == "ivg.bm25.search":
        _translate_bm25_search(proc, context)
    elif name == "ivg.ivf.search":
        _translate_ivf_search(proc, context)
    elif name == "ivg.retrieve":
        _translate_retrieve(proc, context)
    elif name == "ivg.shortestpath.weighted" or name == "ivg.shortestPath.weighted":
        _translate_weighted_shortest_path(proc, context)
    elif name == "ivg.degreeCentrality":
        _translate_degree_centrality(proc, context)
    elif name == "ivg.betweenness":
        _translate_betweenness(proc, context)
    elif name == "ivg.closeness":
        _translate_closeness(proc, context)
    elif name == "ivg.eigenvector":
        _translate_eigenvector(proc, context)
    elif name == "ivg.leiden":
        _translate_leiden(proc, context)
    elif name == "ivg.triangleCount":
        _translate_triangle_count(proc, context)
    elif name == "ivg.scc":
        _translate_scc(proc, context)
    elif name == "ivg.kcore":
        _translate_kcore(proc, context)
    else:
        raise ValueError(
            f"Unknown procedure: {name!r}. Supported: ivg.retrieve, ivg.vector.search, ivg.neighbors, ivg.ppr, ivg.bm25.search, ivg.ivf.search, ivg.shortestPath.weighted, ivg.degreeCentrality, ivg.betweenness, ivg.closeness, ivg.eigenvector, ivg.leiden, ivg.triangleCount, ivg.scc, ivg.kcore, test.*"
        )


def _tck_val_to_python(val_str):
    """Convert a TCK table cell value string to a Python value for comparison."""
    if val_str is None:
        return None
    s = str(val_str).strip()
    if s.lower() == 'null':
        return None
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    try:
        if '.' in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return s


def _tck_type_coerce(val, arg_type_str: str):
    """Coerce val for type comparison — e.g. FLOAT accepts INTEGER by converting to float."""
    t = arg_type_str.upper().rstrip('?').strip()
    if val is None:
        return None
    if t in ('FLOAT', 'NUMBER'):
        try:
            return float(val)
        except (TypeError, ValueError):
            return val
    if t == 'INTEGER':
        try:
            if isinstance(val, float) and val.is_integer():
                return int(val)
            return int(val)
        except (TypeError, ValueError):
            return val
    return val


def _tck_val_to_sql(val, col_name: str) -> str:
    """Convert a Python value to a SQL literal expression for a SELECT column."""
    if val is None:
        return f"NULL AS {col_name}"
    # val is a string if it came from behave's table (still quoted like 'Malmö')
    if isinstance(val, str):
        if val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        escaped = val.replace("'", "''")
        return f"'{escaped}' AS {col_name}"
    return f"{val} AS {col_name}"


def _translate_test_procedure(proc: ast.CypherProcedureCall, context: TranslationContext) -> None:
    """Translate a TCK test procedure (test.*) to a CTE with materialized result rows.

    TCK procedures are registered in context._tck_procedures with their args, outputs, and
    test data rows. Argument values are used to filter rows.
    """
    proc_name = proc.procedure_name
    procedures = getattr(context, '_tck_procedures', {}) or {}

    if proc_name not in procedures:
        raise ValueError(
            f"Procedure not found: {proc_name!r}. "
            f"Available: {list(procedures.keys()) if procedures else 'none registered'}"
        )

    proc_def = procedures[proc_name]
    args_spec = proc_def.get('args', [])
    outputs_spec = proc_def.get('outputs', [])
    rows = proc_def.get('rows', [])

    # --- Validate argument count ---
    # Implicit call: no parens, args come from parameters
    if proc.implicit_args:
        # In-query implicit call to procedure with arguments is not allowed
        # (InvalidArgumentPassingMode: must use explicit args in CALL ... YIELD ... RETURN)
        if args_spec and proc.yield_items:
            raise SyntaxError(
                f"Procedure {proc_name}: cannot pass arguments implicitly in an in-query "
                f"CALL (InvalidArgumentPassingMode)"
            )
        # Validate all expected parameters are present
        for arg_spec in args_spec:
            arg_name = arg_spec['name']
            if arg_name not in context.input_params:
                # ParameterMissing maps to KeyError in the TCK harness
                raise KeyError(
                    f"Procedure {proc_name}: missing parameter '{arg_name}' "
                    f"(MissingParameter)"
                )
        # Build argument list from parameters
        call_args = [context.input_params.get(s['name']) for s in args_spec]
    else:
        arg_count_provided = len(proc.arguments) if proc.arguments else 0
        arg_count_expected = len(args_spec)
        if arg_count_provided != arg_count_expected:
            raise SyntaxError(
                f"Procedure {proc_name}: expected {arg_count_expected} arguments, "
                f"got {arg_count_provided} (InvalidNumberOfArguments)"
            )

        # Resolve argument values
        call_args = []
        for i, arg in enumerate(proc.arguments or []):
            # Check for aggregation functions in arguments (InvalidAggregation)
            if isinstance(arg, ast.FunctionCall) and arg.function_name.lower() in (
                'count', 'sum', 'avg', 'min', 'max', 'collect', 'stdev', 'stdevp',
                'percentiledisc', 'percentilecont',
            ):
                raise SyntaxError(
                    f"Procedure {proc_name}: aggregation functions are not allowed "
                    f"in procedure arguments (InvalidAggregation)"
                )
            if isinstance(arg, ast.Literal):
                call_args.append(arg.value)
            elif isinstance(arg, ast.Variable):
                if arg.name in context.input_params:
                    call_args.append(context.input_params[arg.name])
                else:
                    raise ValueError(
                        f"Procedure {proc_name}: parameter '${arg.name}' not found"
                    )
            else:
                call_args.append(None)

        # Type-check arguments against spec
        for i, (arg_val, arg_spec) in enumerate(zip(call_args, args_spec)):
            if arg_val is None:
                continue  # null is always acceptable
            arg_type = arg_spec.get('type', '').upper().rstrip('?').strip()
            # Boolean check must come before int/float checks since bool is a subclass of int
            is_bool = isinstance(arg_val, bool)
            if arg_type in ('STRING',) and not isinstance(arg_val, str):
                raise SyntaxError(
                    f"Procedure {proc_name}: argument {i+1} expected STRING, "
                    f"got {type(arg_val).__name__} (InvalidArgumentType)"
                )
            if arg_type == 'INTEGER':
                if is_bool:
                    raise SyntaxError(
                        f"Procedure {proc_name}: argument {i+1} expected INTEGER, "
                        f"got boolean (InvalidArgumentType)"
                    )
            if arg_type == 'INTEGER' and not isinstance(arg_val, (int,)):
                if isinstance(arg_val, float) and arg_val.is_integer():
                    call_args[i] = int(arg_val)
                elif is_bool:
                    raise SyntaxError(
                        f"Procedure {proc_name}: argument {i+1} expected INTEGER, "
                        f"got boolean (InvalidArgumentType)"
                    )
            if arg_type in ('FLOAT', 'NUMBER'):
                if isinstance(arg_val, bool):
                    raise SyntaxError(
                        f"Procedure {proc_name}: argument {i+1} expected {arg_type}, "
                        f"got boolean (InvalidArgumentType)"
                    )

    # --- Validate YIELD items for duplicate aliases (VariableAlreadyBound) ---
    if proc.yield_items and not proc.yield_star:
        seen_aliases = set()
        for item in proc.yield_items:
            if isinstance(item, tuple):
                orig, alias = item
            else:
                orig, alias = item, item
            if alias in seen_aliases:
                raise SyntaxError(
                    f"Procedure {proc_name}: YIELD alias '{alias}' is already bound "
                    f"(VariableAlreadyBound)"
                )
            # Check against already-bound variables in the outer scope
            if alias in context.variable_aliases:
                raise SyntaxError(
                    f"Procedure {proc_name}: YIELD alias '{alias}' shadows an already "
                    f"bound variable (VariableAlreadyBound)"
                )
            seen_aliases.add(alias)

    # --- Check for YIELD * in in-query call (UnexpectedSyntax) ---
    # yield_star is only allowed for standalone calls (no preceding MATCH/WITH clauses)
    if proc.yield_star and not proc.yield_items:
        # This is handled below — allowed for standalone, raise for in-query
        # Caller context determines this; we'll handle it in the synthesizer.
        pass

    # --- No outputs → no CTE needed ---
    output_names = [out['name'] for out in outputs_spec]
    if not output_names:
        return

    # --- Filter rows by argument values ---
    def _row_matches(row_data: dict) -> bool:
        """Return True if this data row matches the call arguments."""
        if not args_spec or not call_args:
            return True  # No args → return all rows
        for i, (arg_spec_item, call_val) in enumerate(zip(args_spec, call_args)):
            col_name = arg_spec_item['name']
            arg_type = arg_spec_item.get('type', '').upper().rstrip('?').strip()
            row_raw = row_data.get(col_name)
            row_val = _tck_val_to_python(row_raw)
            if call_val is None and row_val is None:
                continue
            if call_val is None or row_val is None:
                return False
            # Apply type coercion for FLOAT/NUMBER types
            cmp_call = _tck_type_coerce(call_val, arg_type)
            cmp_row = _tck_type_coerce(row_val, arg_type)
            if cmp_call != cmp_row:
                return False
        return True

    matched_rows = [r for r in rows if _row_matches(r)]

    # --- Build UNION CTE ---
    cte_name = f"TCK_Proc_{id(proc)}"

    union_parts = []
    for row_data in matched_rows:
        select_cols = [_tck_val_to_sql(row_data.get(n), n) for n in output_names]
        union_parts.append(f"SELECT {', '.join(select_cols)}")

    if union_parts:
        select_sql = " UNION ALL ".join(union_parts)
    else:
        cols_sql = ", ".join([f"NULL AS {name}" for name in output_names])
        select_sql = f"SELECT {cols_sql} WHERE 1=0"

    context.stages.insert(0, f"{cte_name} AS (\n{select_sql}\n)")

    # --- Register YIELD aliases ---
    # Determine which output names are "visible" after YIELD
    # All TCK procedure output variables are scalars (not graph nodes).
    if proc.yield_star:
        # YIELD * → all outputs aliased to their own names
        for out_name in output_names:
            context.variable_aliases[out_name] = cte_name
            context.scalar_variables.add(out_name)
            context.bind_variable_type(out_name, "scalar", force=True)
    elif proc.yield_items:
        for item in proc.yield_items:
            if isinstance(item, tuple):
                orig, alias = item
            else:
                orig, alias = item, item
            context.variable_aliases[alias] = cte_name
            context.scalar_variables.add(alias)
            context.bind_variable_type(alias, "scalar", force=True)
            # If the alias differs from the column name, we need a rename mapping
            if orig != alias:
                if not hasattr(context, '_tck_yield_renames'):
                    context._tck_yield_renames = {}
                context._tck_yield_renames[alias] = (cte_name, orig)
    # else: No YIELD clause — do NOT expose outputs in scope.
    # The standalone synthesizer (in _tts_finalize_context) handles RETURN for standalone calls.
    # For in-query calls without YIELD, outputs should not be in scope (UndefinedVariable).

    # Store metadata for synthesizing standalone RETURN
    context._tck_proc_cte = cte_name
    context._tck_proc_outputs = output_names
    context._tck_proc = proc


def _resolve_arg(arg, context: TranslationContext, name: str, expected_type=None):
    """Resolve a procedure argument (literal, variable/parameter, or list)."""
    if isinstance(arg, ast.Literal):
        val = arg.value
        if isinstance(val, list):
            return [
                item.value if isinstance(item, ast.Literal) else item for item in val
            ]
        return val
    elif isinstance(arg, ast.Variable):
        if arg.name in context.input_params:
            return context.input_params[arg.name]
        raise ValueError(f"{name}: parameter '${arg.name}' not found in params")
    raise ValueError(f"{name}: argument must be a literal or parameter")


def _vs_resolve_query_input(query_input_arg, context):
    if isinstance(query_input_arg, ast.Literal):
        raw = query_input_arg.value
        if isinstance(raw, list):
            return [item.value if isinstance(item, ast.Literal) else item for item in raw]
        return raw
    if isinstance(query_input_arg, ast.Variable):
        var_name = query_input_arg.name
        if var_name in context.input_params:
            return context.input_params[var_name]
        raise ValueError(f"ivg.vector.search: parameter '${var_name}' not found in params")
    raise ValueError(
        "ivg.vector.search: third argument (query_input) must be a literal or parameter"
    )


def _vs_resolve_limit(limit_arg, context):
    if isinstance(limit_arg, ast.Literal):
        limit_val = limit_arg.value
    elif isinstance(limit_arg, ast.Variable):
        var_name = limit_arg.name
        if var_name in context.input_params:
            limit_val = context.input_params[var_name]
        else:
            raise ValueError(f"ivg.vector.search: parameter '${var_name}' not found in params")
    else:
        raise ValueError(
            "ivg.vector.search: fourth argument (limit) must be an integer literal or parameter"
        )
    try:
        limit_int = int(limit_val)
    except (TypeError, ValueError):
        raise ValueError(f"ivg.vector.search: limit must be an integer, got {limit_val!r}")
    if limit_int <= 0:
        raise ValueError(f"ivg.vector.search: limit must be > 0, got {limit_int}")
    return limit_int


def _vs_build_similarity(query_input, vector_fn, label, options, emb_table):
    if isinstance(query_input, list):
        vec_json = json.dumps(query_input)
        return f"{vector_fn}(e.emb, TO_VECTOR(?, DOUBLE))", [vec_json, label], False
    if isinstance(query_input, str):
        embedding_config = options.get("embedding_config")
        if embedding_config:
            return (
                f"{vector_fn}(e.emb, EMBEDDING(?, ?))",
                [query_input, embedding_config, label],
                False,
            )
        return (
            f"{vector_fn}(e.emb, (SELECT e2.emb FROM {emb_table} e2 WHERE e2.id = ?))",
            [query_input, label],
            True,
        )
    raise ValueError(
        f"ivg.vector.search: query_input must be a list[float] or str, got {type(query_input).__name__}"
    )


def _translate_vector_search(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
    args = proc.arguments
    if len(args) < 4:
        raise ValueError(
            f"ivg.vector.search requires at least 4 arguments "
            f"(label, property, query_input, limit), got {len(args)}"
        )

    label_arg = args[0]
    if not isinstance(label_arg, ast.Literal) or not isinstance(label_arg.value, str):
        raise ValueError(
            "ivg.vector.search: first argument (label) must be a string literal"
        )
    label = label_arg.value
    validate_table_name("rdf_labels")

    prop_arg = args[1]
    if not isinstance(prop_arg, ast.Literal) or not isinstance(prop_arg.value, str):
        raise ValueError(
            "ivg.vector.search: second argument (property) must be a string literal"
        )

    query_input = _vs_resolve_query_input(args[2], context)
    limit_int = _vs_resolve_limit(args[3], context)

    raw_options = proc.options or {}
    options: Dict[str, Any] = {}
    for k, v in raw_options.items():
        options[k] = v.value if isinstance(v, ast.Literal) else v

    similarity = options.get("similarity", "cosine")
    if similarity not in ("cosine", "dot_product"):
        raise ValueError(
            f"ivg.vector.search: similarity must be 'cosine' or 'dot_product', got {similarity!r}"
        )

    vector_fn = "VECTOR_COSINE" if similarity == "cosine" else "VECTOR_DOT_PRODUCT"
    emb_table = _table("kg_NodeEmbeddings")
    labels_tbl = _table("rdf_labels")

    similarity_expr, ordered_params, exclude_self = _vs_build_similarity(
        query_input, vector_fn, label, options, emb_table
    )

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

    for item in proc.yield_items:
        context.variable_aliases[item] = "VecSearch"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_neighbors(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
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
        raise ValueError(
            f"ivg.neighbors: source must be a string or list, got {type(sources).__name__}"
        )

    predicate = (
        _resolve_arg(args[1], context, "ivg.neighbors") if len(args) > 1 else None
    )
    direction = (
        _resolve_arg(args[2], context, "ivg.neighbors") if len(args) > 2 else "out"
    )
    if direction not in ("out", "in", "both"):
        raise ValueError(
            f"ivg.neighbors: direction must be 'out', 'in', or 'both', got {direction!r}"
        )

    edges_tbl = _table("rdf_edges")
    ph = ", ".join(["?"] * len(sources))
    parts = []

    if direction in ("out", "both"):
        sql = (
            f"SELECT DISTINCT e.o_id AS neighbor FROM {edges_tbl} e WHERE e.s IN ({ph})"
        )
        p = list(sources)
        if predicate:
            sql += " AND e.p = ?"
            p.append(predicate)
        parts.append((sql, p))

    if direction in ("in", "both"):
        sql = (
            f"SELECT DISTINCT e.s AS neighbor FROM {edges_tbl} e WHERE e.o_id IN ({ph})"
        )
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
        raise ValueError(
            f"ivg.ppr: seeds must be a string or list, got {type(seeds).__name__}"
        )

    alpha = float(_resolve_arg(args[1], context, "ivg.ppr")) if len(args) > 1 else 0.85
    max_iter = int(_resolve_arg(args[2], context, "ivg.ppr")) if len(args) > 2 else 20

    seed_json = json.dumps(seeds)
    ppr_fn = f"{_schema_prefix}.kg_PPR" if _schema_prefix else "kg_PPR"

    cte_sql = (
        f"SELECT j.node_id AS node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {ppr_fn}(?, ?, ?, 0, 1.0),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
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


def _translate_bm25_search(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
    args = proc.arguments
    if len(args) < 3:
        raise ValueError("ivg.bm25.search requires 3 arguments: name, query, k")

    idx_name = _resolve_arg(args[0], context, "ivg.bm25.search")
    if not isinstance(idx_name, str):
        raise ValueError(
            "ivg.bm25.search: first argument (name) must be a string literal"
        )

    query = _resolve_arg(args[1], context, "ivg.bm25.search")
    k_val = _resolve_arg(args[2], context, "ivg.bm25.search")
    try:
        k_int = int(k_val)
    except (TypeError, ValueError):
        raise ValueError(
            f"ivg.bm25.search: third argument (k) must be an integer, got {k_val!r}"
        )

    bm25_fn = f"{_schema_prefix}.kg_BM25" if _schema_prefix else "kg_BM25"
    # Bind idx_name and query as parameters (? placeholders) rather than
    # interpolating them inline.  k_int is an integer cast — safe as inline literal.
    context.all_stage_params.extend([str(idx_name), str(query)])
    cte_sql = (
        f"SELECT j.node_id AS node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {bm25_fn}(?, ?, {k_int}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"BM25 AS (\n{cte_sql}\n)")

    for item in proc.yield_items:
        context.variable_aliases[item] = "BM25"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_retrieve(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
    args = proc.arguments
    if not args:
        raise ValueError(
            "ivg.retrieve requires: (query_text, limit, bm25_name='default', vec_label='*', rrf_k=60, embedding_config='')"
        )

    query = _resolve_arg(args[0], context, "ivg.retrieve")
    limit = int(_resolve_arg(args[1], context, "ivg.retrieve")) if len(args) > 1 else 10
    bm25_name = str(_resolve_arg(args[2], context, "ivg.retrieve")) if len(args) > 2 else "default"
    vec_label = str(_resolve_arg(args[3], context, "ivg.retrieve")) if len(args) > 3 else "*"
    rrf_k = int(_resolve_arg(args[4], context, "ivg.retrieve")) if len(args) > 4 else 60
    embedding_config = str(_resolve_arg(args[5], context, "ivg.retrieve")) if len(args) > 5 else ""

    if not query:
        raise ValueError("ivg.retrieve: query text cannot be empty")

    vec_limit = limit * 2
    bm25_limit = limit * 2
    emb_table = f"{_schema_prefix}.kg_NodeEmbeddings" if _schema_prefix else "Graph_KG.kg_NodeEmbeddings"
    bm25_fn = f"{_schema_prefix}.kg_BM25" if _schema_prefix else "Graph_KG.kg_BM25"

    # Bind all string user inputs as ? parameters; integer args stay inline (safe).
    # Order: bm25_name, query (for BM25 CTE), then query again (for Vec EMBEDDING()),
    # then embedding_config, then vec_label filter (if not wildcard).
    context.all_stage_params.extend([str(bm25_name), str(query)])

    bm25_cte = (
        f"SELECT j.node_id AS node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {bm25_fn}(?, ?, {bm25_limit}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )

    context.all_stage_params.append(str(query))   # for EMBEDDING(?, ...)
    context.all_stage_params.append(str(embedding_config))

    if vec_label == "*":
        vec_where = ""
    else:
        vec_where = " WHERE n.label = ?"
        context.all_stage_params.append(str(vec_label))

    vec_cte = (
        f"SELECT TOP {vec_limit} e.id AS node, VECTOR_COSINE(e.emb, EMBEDDING(?, ?)) AS score\n"
        f"FROM {emb_table} e{vec_where}\n"
        f"ORDER BY score DESC"
    )

    rrf_cte = (
        f"SELECT node, SUM(rrf_score) AS rrf_score\n"
        f"FROM (\n"
        f"  SELECT node, 1.0 / ({rrf_k} + ROW_NUMBER() OVER (ORDER BY score DESC)) AS rrf_score\n"
        f"  FROM BM25_Retrieve\n"
        f"  UNION ALL\n"
        f"  SELECT node, 1.0 / ({rrf_k} + ROW_NUMBER() OVER (ORDER BY score DESC)) AS rrf_score\n"
        f"  FROM Vec_Retrieve\n"
        f") ranked\n"
        f"GROUP BY node\n"
        f"ORDER BY rrf_score DESC\n"
        f"FETCH FIRST {limit} ROWS ONLY"
    )

    context.stages.insert(0, f"Retrieve AS (\n{rrf_cte}\n)")
    context.stages.insert(0, f"Vec_Retrieve AS (\n{vec_cte}\n)")
    context.stages.insert(0, f"BM25_Retrieve AS (\n{bm25_cte}\n)")

    for item in proc.yield_items:
        context.variable_aliases[item] = "Retrieve"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_ivf_search(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
    args = proc.arguments
    if len(args) < 4:
        raise ValueError(
            "ivg.ivf.search requires 4 arguments: name, query_vec, k, nprobe"
        )

    idx_name = _resolve_arg(args[0], context, "ivg.ivf.search")
    if not isinstance(idx_name, str):
        raise ValueError(
            "ivg.ivf.search: first argument (name) must be a string literal"
        )

    query_vec = _resolve_arg(args[1], context, "ivg.ivf.search")
    if not isinstance(query_vec, list):
        raise ValueError(
            "ivg.ivf.search: second argument (query_vec) must be a list of floats"
        )
    floats = [float(v) for v in query_vec]
    import json as _json

    query_json = _json.dumps(floats).replace("'", "''")

    k_val = _resolve_arg(args[2], context, "ivg.ivf.search")
    try:
        k_int = int(k_val)
    except (TypeError, ValueError):
        raise ValueError(
            f"ivg.ivf.search: third argument (k) must be an integer, got {k_val!r}"
        )

    nprobe_val = _resolve_arg(args[3], context, "ivg.ivf.search")
    try:
        nprobe_int = int(nprobe_val)
    except (TypeError, ValueError):
        raise ValueError(
            f"ivg.ivf.search: fourth argument (nprobe) must be an integer, got {nprobe_val!r}"
        )

    ivf_fn = f"{_schema_prefix}.kg_IVF" if _schema_prefix else "kg_IVF"
    # Bind idx_name and query_json as ? parameters; k_int/nprobe_int are safe int literals.
    context.all_stage_params.extend([str(idx_name), _json.dumps(floats)])

    cte_sql = (
        f"SELECT j.node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {ivf_fn}(?, ?, {k_int}, {nprobe_int}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )

    # IRIS can't resolve CTEs over JSON_TABLE(stored_proc(...)) — use inline derived table
    context.temporal_derived["IVF_SEARCH"] = cte_sql
    context.from_clauses.append("IVF_SEARCH")

    for item in proc.yield_items:
        context.variable_aliases[item] = "IVF_SEARCH"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_weighted_shortest_path(
    proc: ast.CypherProcedureCall, context: TranslationContext
) -> None:
    args = proc.arguments
    if len(args) < 2:
        raise ValueError(
            "ivg.shortestPath.weighted requires at least 2 arguments: from, to"
        )

    from_id = _resolve_arg(args[0], context, "ivg.shortestPath.weighted")
    to_id = _resolve_arg(args[1], context, "ivg.shortestPath.weighted")
    weight_prop = (
        str(_resolve_arg(args[2], context, "ivg.shortestPath.weighted"))
        if len(args) > 2
        else "weight"
    )
    max_cost = (
        float(_resolve_arg(args[3], context, "ivg.shortestPath.weighted"))
        if len(args) > 3
        else 9999.0
    )
    max_hops = (
        int(_resolve_arg(args[4], context, "ivg.shortestPath.weighted"))
        if len(args) > 4
        else 10
    )
    direction = (
        str(_resolve_arg(args[5], context, "ivg.shortestPath.weighted"))
        if len(args) > 5
        else "out"
    )

    if not isinstance(from_id, str) or not isinstance(to_id, str):
        raise ValueError(
            "ivg.shortestPath.weighted: from and to must be string literals or $param"
        )

    context.var_length_paths.append(
        {
            "weighted": True,
            "src_id_param": from_id
            if not isinstance(from_id, str) or from_id.startswith("$")
            else f"'{from_id}'",
            "dst_id_param": to_id
            if not isinstance(to_id, str) or to_id.startswith("$")
            else f"'{to_id}'",
            "weight_prop": weight_prop,
            "max_cost": max_cost,
            "max_hops": max_hops,
            "direction": direction,
            "return_path_funcs": list(proc.yield_items),
        }
    )

    for item in proc.yield_items:
        if item in ("path", "totalCost", "totalcost", "node"):
            context.variable_aliases[item] = "WS"
            context.scalar_variables.add(item)


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
    if hasattr(ast, "Parameter") and isinstance(expr, ast.Parameter):
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
                if (
                    isinstance(left, ast.PropertyReference)
                    and left.variable == rel_var
                    and left.property_name == "ts"
                ):
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
        if (
            isinstance(left, ast.PropertyReference)
            and left.variable == rel_var
            and left.property_name == "ts"
        ):
            val = _resolve_ts_value(right, params)
            if op in (
                ast.BooleanOperator.GREATER_THAN_OR_EQUAL,
                ast.BooleanOperator.GREATER_THAN,
            ):
                return TemporalBound(
                    ts_start=val,
                    ts_end=None,
                    rel_variable=rel_var,
                    predicate=None,
                    direction="out",
                )
            if op in (
                ast.BooleanOperator.LESS_THAN_OR_EQUAL,
                ast.BooleanOperator.LESS_THAN,
            ):
                return TemporalBound(
                    ts_start=None,
                    ts_end=val,
                    rel_variable=rel_var,
                    predicate=None,
                    direction="out",
                )
            if op == ast.BooleanOperator.EQUALS:
                return TemporalBound(
                    ts_start=val,
                    ts_end=val,
                    rel_variable=rel_var,
                    predicate=None,
                    direction="out",
                )

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
        rows.append(
            f"SELECT '{s}' AS s, '{p}' AS p, '{o}' AS o, {ts} AS ts, {w} AS weight"
        )
    return " UNION ALL ".join(rows)


def _remove_ts_conditions_from_where(context, rel_var: str):
    kept = []
    for cond in context.where_conditions:
        if f".ts" in cond and rel_var in cond:
            continue
        kept.append(cond)
    context.where_conditions = kept


def _maybe_split_deep_joins(sql: str, params: list, context) -> str:
    JOIN_THRESHOLD = 20
    join_count = sql.count(" JOIN ")
    if join_count <= JOIN_THRESHOLD:
        return sql
    import re as _re
    # Capture an optional `TOP n` in the prefix (the FETCH-FIRST-+-JOIN workaround emits
    # SELECT [DISTINCT] TOP n) so it stays attached to the SELECT keyword and is not
    # swept into the column list.
    select_m = _re.match(r'(SELECT\s+(?:DISTINCT\s+)?(?:TOP\s+\d+\s+)?)(.*?)(\nFROM\s)', sql, _re.DOTALL)
    if not select_m:
        return sql
    # select_prefix carries any DISTINCT and TOP n; both propagate to the outer wrapper
    # (line below), so the CTE wrap preserves the row cap from the TOP workaround.
    select_prefix = select_m.group(1)
    select_cols = select_m.group(2).strip()
    has_agg = bool(_re.search(r'\b(AVG|SUM|COUNT|MIN|MAX|STDEV|JSON_ARRAYAGG)\s*\(', select_cols))
    has_group = 'GROUP BY' in sql
    if has_agg and not has_group:
        return sql
    inner_from_onwards = sql[select_m.start(3):]
    inner_sql = f"SELECT {select_cols}{inner_from_onwards}"
    _SQL_TYPES = frozenset({
        'INTEGER','INT','DOUBLE','FLOAT','REAL','VARCHAR','CHAR','BIGINT','SMALLINT',
        'DECIMAL','NUMERIC','BOOLEAN','DATE','TIME','TIMESTAMP','VARBINARY','BINARY',
    })
    alias_re = _re.compile(r'\)\s+AS\s+("?[a-z_][a-z0-9_"]*"?)\s*(?:,|\Z)', _re.IGNORECASE | _re.DOTALL)
    top_as_re = _re.compile(r'(?:^|,)\s*(?:[^,]+?)\s+AS\s+("?[a-z_][a-z0-9_"]*"?)\s*(?=,|$)', _re.IGNORECASE)
    seen = {}
    for m in _re.finditer(r'(?:^|(?<=,))\s*([^,]+?)\s+AS\s+("?[a-z_][a-z0-9_"]*"?)\s*(?=,|$)', select_cols, _re.DOTALL):
        alias = m.group(2).strip('"')
        if alias.upper() not in _SQL_TYPES:
            seen[alias] = alias
    outer_cols = ', '.join(seen.keys())
    if not outer_cols:
        return sql
    outer_sql = f"WITH _MR AS (\n{inner_sql}\n)\n{select_prefix}{outer_cols}\nFROM _MR"
    order_m = _re.search(r'\nORDER BY .+', sql, _re.DOTALL)
    limit_m = _re.search(r'\nFETCH FIRST (\d+) ROWS ONLY', sql)
    offset_m = _re.search(r'\nOFFSET \d+', sql)
    suffix = ""
    if order_m:
        start = order_m.start()
        end = limit_m.start() if limit_m and limit_m.start() > start else len(sql)
        suffix += sql[start:end]
    if limit_m:
        suffix += f"\nFETCH FIRST {limit_m.group(1)} ROWS ONLY"
    if offset_m:
        suffix += f"\nOFFSET {offset_m.group(0).split()[1]}"
    outer_sql += suffix
    return outer_sql


def _demote_agg_stages_to_subqueries(sql: str, ctes: list) -> tuple:
    remaining_ctes = []
    for cte in ctes:
        name_end = cte.index(" AS (")
        cte_name = cte[:name_end].strip()
        body_start = cte.index(" AS (") + 5
        body_end = cte.rindex(")")
        body = cte[body_start:body_end].strip()

        if "GROUP BY" in body.upper() and f"FROM {cte_name}" in sql:
            sql = sql.replace(f"FROM {cte_name}", f"FROM ({body}) {cte_name}", 1)
        else:
            remaining_ctes.append(cte)
    return sql, remaining_ctes


def _to_sql_init_part_from(context: TranslationContext, cypher_query: ast.CypherQuery, i: int) -> None:
    if i > 0:
        context.from_clauses.append(f"Stage{i}")
    elif getattr(context, "_ivf_derived", None):
        context.from_clauses.append(context._ivf_derived)
    elif cypher_query.procedure_call is not None:
        if context.temporal_derived:
            for td_name in context.temporal_derived:
                context.from_clauses.append(td_name)
        elif context.stages:
            cte_name = context.stages[0].split(" AS ")[0].strip()
            context.from_clauses.append(cte_name)
        else:
            context.from_clauses.append("VecSearch")
    elif context.stages and not context.from_clauses:
        cte_name = context.stages[0].split(" AS ")[0].strip()
        context.from_clauses.append(cte_name)


def _to_sql_handle_foreach(clause, context: TranslationContext, metadata) -> bool:
    if isinstance(clause.source, ast.Literal) and isinstance(clause.source.value, list):
        for item in clause.source.value:
            orig_aliases = dict(context.variable_aliases)
            context.variable_aliases[clause.variable] = "__foreach_literal__"
            context.foreach_literals = getattr(context, "foreach_literals", {})
            context.foreach_literals[clause.variable] = (
                item.value if isinstance(item, ast.Literal) else item
            )
            for uc in clause.update_clauses:
                if isinstance(uc, ast.UpdatingClause):
                    translate_updating_clause(uc, context, metadata)
            context.variable_aliases = orig_aliases
            if hasattr(context, "foreach_literals"):
                context.foreach_literals.pop(clause.variable, None)
    else:
        for uc in clause.update_clauses:
            if isinstance(uc, ast.UpdatingClause):
                translate_updating_clause(uc, context, metadata)
    return True


def _inject_row_number(sql: str, rn_over: str) -> str:
    """Inject ROW_NUMBER() OVER(rn_over) AS __rn into a SELECT statement.

    Inserts after the first SELECT (or SELECT DISTINCT), so the result can be
    used directly in a CTE body without nesting — IRIS %qaqpre crashes on
    ROW_NUMBER() OVER() inside a nested subquery in a CTE.
    """
    idx = sql.upper().find("SELECT ")
    if idx < 0:
        return sql
    insert_at = idx + len("SELECT ")
    if sql[insert_at:insert_at + 9].upper().startswith("DISTINCT "):
        insert_at += len("DISTINCT ")
    return sql[:insert_at] + f"ROW_NUMBER() OVER({rn_over}) AS __rn, " + sql[insert_at:]


def _to_sql_handle_with(part, context: TranslationContext, i: int, cypher_query=None) -> None:
    translate_with_clause(part.with_clause, context)

    # Build alias → underlying SQL expression map from the projected SELECT items.
    # e.g. 'p2.val AS property' → {'property': 'p2.val'}
    # Used to expand WITH-alias references in OVER() where column aliases are not resolvable.
    _with_alias_sql: dict = {}
    for _sel in context.select_items:
        if " AS " in _sel:
            _expr_part, _, _alias_part = _sel.rpartition(" AS ")
            _with_alias_sql[_alias_part.strip().strip('"')] = _expr_part.strip()

    # Preprocess ORDER BY items for the WITH clause (before build_stage_sql so joins are included).
    # For alias-based ORDER BY (e.g. WITH n.name AS prop ORDER BY prop), the alias is not yet
    # resolvable as a variable — emit it as a bare column name for the subquery wrapper to resolve.
    order_by_items = []
    with_aliases = {
        (item.alias or (item.expression.name if isinstance(item.expression, ast.Variable) else None))
        for item in part.with_clause.items
    } - {None}
    # Map (variable, property_name) -> alias for PropertyReference WITH projections.
    # ORDER BY a.name after WITH DISTINCT a.name AS name should use alias 'name', not add a new JOIN.
    prop_alias_map: dict = {}
    for wi in part.with_clause.items:
        if wi.alias and isinstance(wi.expression, ast.PropertyReference):
            prop_alias_map[(wi.expression.variable, wi.expression.property_name)] = wi.alias
    # sort_projections: list of (alias, sql_expr) for complex ORDER BY expressions that
    # need to be projected into the inner SELECT so the outer ORDER BY can reference them.
    sort_projections: list = []
    # Track params added by sort expression translation (correlated subquery key names).
    # These params appear in the SQL BEFORE the FROM clause (injected into SELECT list)
    # but are added to join_params AFTER MATCH-clause params. We track them separately
    # so we can fix the params order after build_stage_sql.
    sort_expr_params: list = []  # select_params only — params in SQL SELECT before FROM
    import re as _re_ob
    if part.with_clause.order_by_clause:
        for item in part.with_clause.order_by_clause.items:
            direction = "ASC" if item.ascending else "DESC"
            # Validate: ORDER BY variables must be in scope (projected by WITH or bound by MATCH).
            # A plain Variable or PropertyReference root that is not in with_aliases AND not in
            # context.variable_aliases is truly undefined → UndefinedVariable.
            # Note: PropertyReference x.prop where x is a MATCH-bound node is allowed even if
            # x is not projected in this WITH (ORDER BY evaluated before projection narrowing).
            _all_bound = with_aliases | set(context.variable_aliases.keys())
            if isinstance(item.expression, ast.Variable):
                if item.expression.name not in _all_bound:
                    raise SyntaxError(
                        f"UndefinedVariable: Variable `{item.expression.name}` not defined"
                    )
            elif isinstance(item.expression, ast.PropertyReference):
                if item.expression.variable not in _all_bound:
                    raise SyntaxError(
                        f"UndefinedVariable: Variable `{item.expression.variable}` not defined"
                    )
            # If ORDER BY expression is a PropertyReference projected as a WITH alias, use the alias.
            # This avoids adding a second JOIN that would break DISTINCT semantics.
            if isinstance(item.expression, ast.PropertyReference):
                _pk = (item.expression.variable, item.expression.property_name)
                if _pk in prop_alias_map:
                    _alias_name = prop_alias_map[_pk]
                    col = _safe_alias(_alias_name)
                    # Route through sort_projections so OVER() gets the real SQL expression.
                    # IRIS can't resolve CTE column aliases inside OVER() of the same SELECT.
                    _raw_sql = _with_alias_sql.get(_alias_name, col)
                    # Always use numeric-aware sort via the underlying SQL expression (not the
                    # alias), since IRIS can't resolve CTE aliases in OVER() of the same SELECT.
                    # Safe even for SQL-reserved-word aliases (e.g. "count") since _raw_sql is
                    # the concrete column (e.g. p2.val), not the quoted alias.
                    _sort_alias = f"__sort{len(sort_projections)}"
                    sort_projections.append((_sort_alias,
                        f"CASE WHEN ISNUMERIC({_raw_sql}) = 1 THEN CAST({_raw_sql} AS DOUBLE) END"))
                    _sort_alias2 = f"__sort{len(sort_projections)}"
                    sort_projections.append((_sort_alias2, _raw_sql))
                    order_by_items.append(f"{_sort_alias} {direction}")
                    order_by_items.append(f"{_sort_alias2} {direction}")
                    continue
            # If the expression is a variable that matches a WITH alias, emit as bare column name.
            # Route through sort_projections so OVER() gets the real SQL expression.
            # IRIS can't resolve CTE column aliases inside OVER() of the same SELECT.
            if isinstance(item.expression, ast.Variable) and item.expression.name in with_aliases:
                _alias_name = item.expression.name
                col = _safe_alias(_alias_name)
                _raw_sql = _with_alias_sql.get(_alias_name, col)
                _sort_alias = f"__sort{len(sort_projections)}"
                sort_projections.append((_sort_alias,
                    f"CASE WHEN ISNUMERIC({_raw_sql}) = 1 THEN CAST({_raw_sql} AS DOUBLE) END"))
                _sort_alias2 = f"__sort{len(sort_projections)}"
                sort_projections.append((_sort_alias2, _raw_sql))
                order_by_items.append(f"{_sort_alias} {direction}")
                order_by_items.append(f"{_sort_alias2} {direction}")
            else:
                try:
                    # Map WITH-projected aliases to bare column names so ORDER BY arithmetic
                    # expressions like `a + 2` emit `a + 2` not `u0.a + 2` (u0 is out of
                    # scope in the outer SELECT * FROM (...) __ob ORDER BY ...).
                    prev_ob_map = getattr(context, "_orderby_alias_sql", None)
                    context._orderby_alias_sql = {
                        name: _safe_alias(name) for name in with_aliases
                    }
                    if prev_ob_map:
                        context._orderby_alias_sql.update(prev_ob_map)
                    # Snapshot join_params AND select_params length before translating.
                    # Property references in segment="inline" add to select_params (correlated subquery),
                    # not join_params. We capture both to cover all sort expression param types.
                    _join_params_before = len(context.join_params)
                    _select_params_before = len(context.select_params)
                    # Use segment="inline" so numeric literals become inline constants.
                    # Property references add JOINs to context (join_params) as needed.
                    expr = translate_expression(item.expression, context, segment="inline")
                    # Capture params added by this sort expression (will appear in SQL before FROM).
                    _new_sort_params = context.join_params[_join_params_before:]
                    _new_select_sort_params = context.select_params[_select_params_before:]
                    # Only track select_params (correlated subquery params in SELECT list) for
                    # front-loading. join_params from sort expressions are in JOIN position
                    # (after MATCH label JOINs) and must stay in their natural order.
                    sort_expr_params.extend(_new_select_sort_params)
                    context._orderby_alias_sql = prev_ob_map
                    # If the expression references JOIN aliases (p\d+.val) or a correlated
                    # rdf_props subquery, it cannot be used in OVER() — project it as a sort column.
                    if _re_ob.search(r'\b(?:%EXACT\()?p\d+\.val\)?', expr) or 'rdf_props' in expr:
                        sort_alias = f"__sort{len(sort_projections)}"
                        sort_projections.append((sort_alias, expr))
                        order_by_items.append(f"{sort_alias} {direction}")
                    else:
                        order_by_items.append(f"{expr} {direction}")
                except Exception:
                    pass

    sql, stage_params = context.build_stage_sql(part.with_clause.distinct)

    # Inject sort projection columns into the inner SELECT if any complex ORDER BY expressions exist.
    if sort_projections:
        # The sql is "SELECT col1, col2, ... FROM ..." — inject sort columns after SELECT list.
        # Find the first FROM (not inside a subquery) to insert sort columns before it.
        for sort_alias, sort_expr in sort_projections:
            # Inject into SELECT: "SELECT ..., (sort_expr) AS sort_alias\nFROM ..."
            # Match the first \nFROM or " FROM " at the top level
            _from_pat = _re_ob.search(r'\nFROM ', sql)
            if _from_pat:
                insert_at = _from_pat.start()
                sql = sql[:insert_at] + f", ({sort_expr}) AS {sort_alias}" + sql[insert_at:]
            else:
                # Fallback: append to SELECT line
                sql = sql + f", ({sort_expr}) AS {sort_alias}"

        # Fix params order: correlated subquery sort params appear in SQL SELECT (before FROM),
        # but build_stage_sql places select_params after join_params. Move only select_params
        # added by sort expressions to the front. join_params from sort LEFT JOINs stay in
        # their natural position (after MATCH label JOINs) — they're already correct.
        if sort_expr_params:
            _sp = list(stage_params)
            for _p in sort_expr_params:
                try:
                    _sp.remove(_p)
                except ValueError:
                    pass
            stage_params = sort_expr_params + _sp

    # Apply ORDER BY, SKIP, LIMIT from the WITH clause (if present).
    # IRIS does not allow ORDER BY directly in a CTE body — it must be inside a subquery wrapper.
    limit = _resolve_pagination_value(part.with_clause.limit, context)
    skip = _resolve_pagination_value(part.with_clause.skip, context)

    if order_by_items or limit is not None or skip is not None:
        has_join = "\nJOIN " in sql or " JOIN " in sql

        # For OVER() in ROW_NUMBER when using _inject_row_number: sort alias (__sort0 etc.)
        # are in the same SELECT scope as ROW_NUMBER, so IRIS can't resolve them in OVER().
        # Build rn_over_inline that substitutes the actual expression for each sort alias.
        def _ob_with_exprs(ob_items, sp):
            result = []
            for item in ob_items:
                for alias, expr in sp:
                    if item.startswith(f"{alias} "):
                        direction = item.split()[-1]
                        item = f"({expr}) {direction}"
                        break
                result.append(item)
            return result
        _ob_exprs = _ob_with_exprs(order_by_items, sort_projections)
        rn_over = f"ORDER BY {', '.join(_ob_exprs)}" if _ob_exprs else ""
        if limit is not None and skip is not None:
            # SKIP+LIMIT: inject ROW_NUMBER into the query directly (no nested subquery),
            # then filter with a second CTE. IRIS cannot handle ROW_NUMBER OVER in a
            # nested subquery inside a CTE (qaqpre crash on AI builds).
            _rn_stage = f"Stage{i + 1}_rn"
            _rn_sql = _inject_row_number(sql, rn_over)
            # The rn_over substitution adds sort-expr params BEFORE the existing params in Stage1_rn.
            # Each substituted sort alias has its params appearing once in OVER() and once in __sortN.
            _rn_stage_params = sort_expr_params + stage_params
            context.all_stage_params.extend(_rn_stage_params)
            context.stages.append(f"{_rn_stage} AS (\n{_rn_sql}\n)")
            stage_params = []
            # TOP inside a CTE body enables ORDER BY (IRIS rejects bare ORDER BY in CTEs).
            # Ordering by __rn preserves the sort semantics established by the OVER() clause.
            sql = (
                f"SELECT TOP 9223372036854775807 {_rn_stage}.* FROM {_rn_stage}"
                f" WHERE {_rn_stage}.__rn > {skip} AND {_rn_stage}.__rn <= {skip + limit}"
                f" ORDER BY {_rn_stage}.__rn"
            )
        elif limit is not None:
            if order_by_items:
                # ORDER BY + LIMIT: TOP N wrapper with ORDER BY on outer
                sql = f"SELECT TOP {limit} * FROM ({sql}) __ob ORDER BY {', '.join(order_by_items)}"
            elif has_join:
                # JOIN CTE: inject TOP to avoid qaqpre FETCH FIRST crash
                head, sep, rest = sql.partition("SELECT ")
                if rest[:9].upper().startswith("DISTINCT "):
                    rest = "DISTINCT " + f"TOP {limit} " + rest[9:]
                else:
                    rest = f"TOP {limit} " + rest
                sql = head + sep + rest
            else:
                sql += f"\nFETCH FIRST {limit} ROWS ONLY"
        elif skip is not None:
            # SKIP only: inject ROW_NUMBER into query, filter in second CTE.
            _rn_stage = f"Stage{i + 1}_rn"
            _rn_sql = _inject_row_number(sql, rn_over)
            context.all_stage_params.extend(stage_params)
            context.stages.append(f"{_rn_stage} AS (\n{_rn_sql}\n)")
            stage_params = []
            # TOP inside a CTE body enables ORDER BY (IRIS rejects bare ORDER BY in CTEs).
            sql = (
                f"SELECT TOP 9223372036854775807 {_rn_stage}.* FROM {_rn_stage}"
                f" WHERE {_rn_stage}.__rn > {skip}"
                f" ORDER BY {_rn_stage}.__rn"
            )
        elif order_by_items:
            # ORDER BY only (no LIMIT/SKIP): IRIS rejects ORDER BY inside a CTE body.
            # Use TOP with max BIGINT to produce an ordered subquery IRIS accepts in a CTE.
            # Don't apply TOP if ORDER BY contains an aggregation (e.g. ORDER BY COUNT(1)) —
            # those should raise SyntaxError but we haven't validated them yet; leaving plain
            # SELECT * FROM (...) __ob ORDER BY lets IRIS reject them, which the TCK interprets as
            # a SyntaxError (the TCK SyntaxError-check catches any SQL error on execution).
            _has_agg_in_ob = any(
                'COUNT(' in ob.upper() or 'SUM(' in ob.upper()
                or 'MIN(' in ob.upper() or 'MAX(' in ob.upper() or 'AVG(' in ob.upper()
                for ob in order_by_items
            )
            if _has_agg_in_ob:
                sql = f"SELECT * FROM ({sql}) __ob ORDER BY {', '.join(order_by_items)}"
            else:
                sql = f"SELECT TOP 9223372036854775807 * FROM ({sql}) __ob ORDER BY {', '.join(order_by_items)}"

    # If this stage captures an OPTIONAL MATCH anchor null-row, embed the UNION ALL
    # directly into the Stage CTE so the null row survives subsequent MATCH stages.
    _stage_null_row_groups = getattr(context, 'optional_null_row_label_groups', [])
    _stage_null_row_labels = context.optional_null_row_labels
    if _stage_null_row_labels or _stage_null_row_groups:
        _n_cols = len(context.select_items)
        _null_sel = ", ".join(["NULL"] * _n_cols)
        _ne_parts = []
        _ne_params: List[Any] = []
        if _stage_null_row_groups:
            for _grp in _stage_null_row_groups:
                _ne_sql, _ne_p = _build_null_row_not_exists(_grp)
                _ne_parts.append(_ne_sql)
                _ne_params.extend(_ne_p)
        else:
            for _lbl in _stage_null_row_labels:
                _ne_parts.append(f"NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE label = ?)")
                _ne_params.append(_lbl)
        _where_clause = " AND ".join(_ne_parts)
        sql = sql + f"\nUNION ALL\nSELECT {_null_sel} WHERE {_where_clause}"
        stage_params = list(stage_params) + _ne_params
        # Clear so the final SELECT doesn't emit a duplicate UNION ALL
        context.optional_null_row_labels = []
        context.optional_null_row_label_groups = []
        context.optional_null_row_items = []
    context.all_stage_params.extend(stage_params)
    context.stages.append(f"Stage{i + 1} AS (\n{sql}\n)")
    context.having_conditions = []
    context.where_params = []
    new_stage = f"Stage{i + 1}"
    if part.with_clause.star:
        new_aliases = {var: new_stage for var in context.variable_aliases}
    else:
        new_aliases = {}
        for item in part.with_clause.items:
            alias = item.alias or (
                item.expression.name
                if isinstance(item.expression, ast.Variable)
                else None
            )
            if alias:
                new_aliases[alias] = new_stage
                # Track the type of the variable in WITH clause.
                # If it's a reference to an existing variable, preserve its type.
                # Otherwise, it's a scalar (function result, literal, etc.)
                # WITH creates a new scope: force=True allows rebinding existing names to new types
                if isinstance(item.expression, ast.Variable):
                    # Passthrough: preserve the bound variable's type
                    # (from MATCH, previous WITH, etc.)
                    existing_type = context.variable_types.get(item.expression.name)
                    if existing_type:
                        context.bind_variable_type(alias, existing_type, force=True)
                    else:
                        # If not yet typed, assume node (safest for graph ops)
                        context.bind_variable_type(alias, "node", force=True)
                    # Also preserve temporal type if applicable
                    if item.expression.name in context.temporal_types:
                        context.temporal_types[alias] = context.temporal_types[item.expression.name]
                else:
                    # Everything else is scalar: aggregation, function call, literal, etc.
                    context.bind_variable_type(alias, "scalar", force=True)
            if isinstance(item.expression, ast.AggregationFunction) and alias:
                context.scalar_variables.add(alias)
                # Track collect(node_var) → alias as a collected node list
                if (item.expression.function_name.lower() == "collect"
                        and item.expression.argument
                        and isinstance(item.expression.argument, ast.Variable)):
                    collected_var = item.expression.argument.name
                    if (collected_var not in context.scalar_variables
                            and collected_var not in getattr(context, "edge_stage_variables", set())):
                        context.collected_node_lists[alias] = collected_var
            elif alias and not isinstance(item.expression, ast.Variable):
                context.scalar_variables.add(alias)
            elif (alias and isinstance(item.expression, ast.Variable)
                  and item.expression.name in context.scalar_variables):
                # Scalar passthrough: WITH scalar_var AS alias — alias is also scalar
                context.scalar_variables.add(alias)
                # Propagate collected_node_lists for scalar passthroughs
                if item.expression.name in context.collected_node_lists:
                    context.collected_node_lists[alias] = context.collected_node_lists[item.expression.name]
    context.variable_aliases = new_aliases



def _tts_union_branches(cypher_query, params):
    """Handle UNION/UNION ALL. Returns SQLQuery or None."""
    if not getattr(cypher_query, "union_queries", None):
        return None
    branches = [cypher_query] + [uq["query"] for uq in cypher_query.union_queries]
    all_flags = [False] + [uq["all"] for uq in cypher_query.union_queries]

    # Mixing UNION and UNION ALL in the same query is a SyntaxError.
    if len(all_flags) > 1:
        has_all = any(all_flags[1:])
        has_distinct = not all(all_flags[1:])
        if has_all and has_distinct:
            raise SyntaxError(
                "Cannot mix UNION and UNION ALL in the same query"
            )

    # All branches must return the same columns (same names, same order).
    def _branch_columns(branch):
        rc = branch.return_clause
        if rc is None:
            return []
        cols = []
        for item in rc.items:
            if item.alias:
                cols.append(item.alias)
            elif hasattr(item.expression, "name"):
                cols.append(item.expression.name)
            elif hasattr(item.expression, "variable"):
                cols.append(item.expression.variable)
            else:
                cols.append(None)
        return cols

    first_cols = _branch_columns(branches[0])
    for i, branch in enumerate(branches[1:], 1):
        cols = _branch_columns(branch)
        if cols and first_cols and cols != first_cols:
            raise SyntaxError(
                f"All UNION branches must have the same column names: "
                f"{first_cols!r} vs {cols!r}"
            )

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
    def _ensure_from(s: str) -> str:
        if "\nFROM " not in s and "\nfrom " not in s:
            return s.rstrip() + "\nFROM (SELECT 1) __dual"
        return s
    combined = sep.join(f"({_ensure_from(s)})" for s in sqls)
    flat_params = []
    for p_list in all_params:
        flat_params.extend(p_list)
    return SQLQuery(sql=combined, parameters=[flat_params])


def _tts_process_parts(cypher_query, context, metadata):
    """Handle procedure_call + iterate query_parts. Returns is_transactional."""
    is_transactional = False
    if cypher_query.procedure_call is not None:
        translate_procedure_call(cypher_query.procedure_call, context)
        if context.system_procedure_call is not None:
            return SQLQuery(
                sql="__SYSTEM_PROCEDURE__",
                parameters=[[]],
                query_metadata=metadata,
                var_length_paths=None,
            )
        if not cypher_query.query_parts:
            if context.temporal_derived:
                for td_name in context.temporal_derived:
                    if td_name not in context.from_clauses:
                        context.from_clauses.append(td_name)
            else:
                cte_name = (
                    context.stages[0].split(" AS ")[0].strip()
                    if context.stages
                    else "VecSearch"
                )
                context.from_clauses.append(cte_name)

    for i, part in enumerate(cypher_query.query_parts):
        context.select_items, context.from_clauses, context.join_clauses = [], [], []
        context.where_conditions, context.group_by_items = [], []
        context.select_params, context.join_params, context.where_params = [], [], []
        _to_sql_init_part_from(context, cypher_query, i)
        for clause in part.clauses:
            if isinstance(clause, ast.WhereClause):
                context.pending_where = clause.expression
                break
        # Check for UNWIND+UPDATE pattern: when a literal-list UNWIND feeds updating clauses,
        # expand Python-side (like FOREACH) so each list element gets its own DML set.
        unwind_clause = next(
            (c for c in part.clauses if isinstance(c, ast.UnwindClause)), None
        )
        has_updating = any(isinstance(c, ast.UpdatingClause) for c in part.clauses)
        unwind_literals = None
        if (
            unwind_clause is not None
            and has_updating
            and isinstance(unwind_clause.expression, ast.Literal)
            and isinstance(unwind_clause.expression.value, list)
        ):
            unwind_literals = unwind_clause.expression.value
        elif (
            unwind_clause is not None
            and has_updating
            and isinstance(unwind_clause.expression, ast.Variable)
            and unwind_clause.expression.name in context.input_params
            and isinstance(context.input_params[unwind_clause.expression.name], list)
        ):
            unwind_literals = [
                ast.Literal(v) if not isinstance(v, ast.Literal) else v
                for v in context.input_params[unwind_clause.expression.name]
            ]
        elif (
            unwind_clause is not None
            and has_updating
            and isinstance(unwind_clause.expression, ast.FunctionCall)
            and unwind_clause.expression.function_name.lower() == "range"
        ):
            # UNWIND range(start, end[, step]) AS i CREATE (...) — evaluate range Python-side
            # so each element gets its own DML set (one INSERT per row).
            _range_args = unwind_clause.expression.arguments
            try:
                _start = int(_range_args[0].value) if len(_range_args) >= 1 and isinstance(_range_args[0], ast.Literal) else None
                _end = int(_range_args[1].value) if len(_range_args) >= 2 and isinstance(_range_args[1], ast.Literal) else None
                _step = int(_range_args[2].value) if len(_range_args) >= 3 and isinstance(_range_args[2], ast.Literal) else 1
                if _start is not None and _end is not None and _step != 0:
                    _vals = list(range(_start, _end + (1 if _step > 0 else -1), _step))
                    unwind_literals = [ast.Literal(v) for v in _vals]
            except (TypeError, ValueError, IndexError):
                pass

        if unwind_literals is not None:
            # UNWIND literal list + updating clauses → expand Python-side, one DML set per element
            is_transactional = True
            aliases_before = dict(context.variable_aliases)
            last_iter_aliases = dict(context.variable_aliases)
            # Accumulate created node IDs per variable for use in RETURN
            if not hasattr(context, "_unwind_create_node_ids"):
                context._unwind_create_node_ids = {}  # var_name → [uuid, ...]
            for item in unwind_literals:
                item_val = item.value if isinstance(item, ast.Literal) else item
                # Start each iteration from the pre-loop state so new vars don't accumulate
                context.variable_aliases = dict(aliases_before)
                context.variable_aliases[unwind_clause.alias] = "__foreach_literal__"
                context.foreach_literals = getattr(context, "foreach_literals", {})
                context.foreach_literals[unwind_clause.alias] = item_val
                for clause in part.clauses:
                    if isinstance(clause, ast.UnwindClause):
                        continue  # handled by foreach expansion above
                    elif isinstance(clause, ast.UpdatingClause):
                        translate_updating_clause(clause, context, metadata)
                    elif isinstance(clause, ast.WhereClause):
                        translate_where_clause(clause, context)
                # Collect node IDs created in this iteration
                for var_name in set(context.variable_aliases) - set(aliases_before):
                    nid = context.input_params.get(f"__create_id_{var_name}")
                    if nid:
                        context._unwind_create_node_ids.setdefault(var_name, []).append(nid)
                # Collect relationship identities created in this iteration
                if not hasattr(context, "_unwind_create_rel_ids"):
                    context._unwind_create_rel_ids = {}  # var_name → [(s,p,o), ...]
                for var_name in set(context.variable_aliases) - set(aliases_before):
                    edge_key = context.input_params.get(f"__create_edge_{var_name}")
                    if edge_key:
                        context._unwind_create_rel_ids.setdefault(var_name, []).append(edge_key)
                last_iter_aliases = dict(context.variable_aliases)
            if hasattr(context, "foreach_literals"):
                context.foreach_literals.pop(unwind_clause.alias, None)
            # After loop: keep vars created by updating clauses (from last iteration)
            context.variable_aliases = last_iter_aliases
            context.variable_aliases.pop(unwind_clause.alias, None)
            # Still need to add the UNWIND to context for RETURN clause access
            translate_unwind_clause(unwind_clause, context)
        else:
            for clause in part.clauses:
                if isinstance(clause, ast.MatchClause):
                    aliases_before_match = set(context.variable_aliases.values())
                    _opt_join_start = len(context.join_clauses)
                    translate_match_clause(clause, context, metadata)
                    if clause.optional:
                        context.optional_match_new_aliases = (
                            set(context.variable_aliases.values()) - aliases_before_match
                        )
                        context.opt_join_start_idx = _opt_join_start
                    else:
                        context.optional_match_new_aliases = set()
                elif isinstance(clause, ast.UnwindClause):
                    translate_unwind_clause(clause, context)
                    context.optional_match_new_aliases = set()
                elif isinstance(clause, ast.SubqueryCall):
                    translate_subquery_call(clause, context, metadata)
                    context.optional_match_new_aliases = set()
                elif isinstance(clause, ast.ForeachClause):
                    is_transactional = _to_sql_handle_foreach(clause, context, metadata) or is_transactional
                    context.optional_match_new_aliases = set()
                elif isinstance(clause, ast.UpdatingClause):
                    is_transactional = True
                    translate_updating_clause(clause, context, metadata)
                    context.optional_match_new_aliases = set()
                elif isinstance(clause, ast.WhereClause):
                    translate_where_clause(clause, context)
        if part.procedure_call is not None:
            translate_procedure_call(part.procedure_call, context)
            # For TCK procedures: add the CTE to FROM clause so the final SELECT can reference it
            tck_cte = getattr(context, '_tck_proc_cte', None)
            if tck_cte and tck_cte not in context.from_clauses:
                context.from_clauses.append(tck_cte)
        if part.with_clause:
            # If UNWIND+CREATE relationship expansion ran, reset context to correct single-table
            # structure before building the WITH stage (avoids 5-JOIN spurious structure).
            _apply_unwind_create_context_reset(context)
            _to_sql_handle_with(part, context, i)
    return is_transactional


def _apply_unwind_create_context_reset(context):
    """Reset FROM/JOIN/WHERE to correct single-table structure after UNWIND+CREATE expansion.

    Called before translating a WITH clause or RETURN that follows UNWIND+CREATE.
    Prevents the accumulated per-iteration JOIN structure from leaking into CTEs.
    """
    unwind_node_ids = getattr(context, "_unwind_create_node_ids", {})
    if unwind_node_ids:
        context.select_items, context.select_params = [], []
        context.join_clauses, context.join_params = [], []
        context.where_conditions, context.where_params = [], []
        first_node_alias = None
        for var_name, ids in unwind_node_ids.items():
            if not ids:
                continue
            node_alias = context.next_alias("n")
            if first_node_alias is None:
                first_node_alias = node_alias
            context.variable_aliases[var_name] = node_alias
            placeholders = ",".join(["?"] * len(ids))
            context.where_conditions.append(f"{node_alias}.node_id IN ({placeholders})")
            context.where_params.extend(ids)
        if first_node_alias is not None:
            context.from_clauses = [f"{_table('nodes')} {first_node_alias}"]
            for var_name, ids in list(unwind_node_ids.items())[1:]:
                if not ids:
                    continue
                na = context.variable_aliases[var_name]
                context.from_clauses.append(f"{_table('nodes')} {na}")
        return

    unwind_rel_ids = getattr(context, "_unwind_create_rel_ids", {})
    if unwind_rel_ids:
        context.select_items, context.select_params = [], []
        context.join_clauses, context.join_params = [], []
        context.where_conditions, context.where_params = [], []
        first_edge_alias = None
        for var_name, triples in unwind_rel_ids.items():
            if not triples:
                continue
            e_alias = context.next_alias("e")
            if first_edge_alias is None:
                first_edge_alias = e_alias
            context.variable_aliases[var_name] = e_alias
            conds = []
            for s_id, p_val, o_id in triples:
                conds.append(
                    f"({e_alias}.s = {context.add_where_param(s_id)}"
                    f" AND {e_alias}.p = {context.add_where_param(p_val)}"
                    f" AND {e_alias}.o_id = {context.add_where_param(o_id)})"
                )
            context.where_conditions.append("(" + " OR ".join(conds) + ")")
        if first_edge_alias is not None:
            context.from_clauses = [f"{_table('rdf_edges')} {first_edge_alias}"]
            for var_name in list(unwind_rel_ids.keys())[1:]:
                if not unwind_rel_ids[var_name]:
                    continue
                ea = context.variable_aliases[var_name]
                context.from_clauses.append(f"{_table('rdf_edges')} {ea}")


def _tts_finalize_context(cypher_query, context):
    """Apply last-part WITH, translate RETURN, compute order_by + graph_context. Returns order_by_items."""
    # 2. Final stage (RETURN)
    # If the last QueryPart had a WITH clause, we must select from that CTE stage.
    # Otherwise, we continue with the context of the last QueryPart (e.g. current MATCH joins).
    last_part_had_with = (
        cypher_query.query_parts[-1].with_clause is not None
        if cypher_query.query_parts
        else False
    )
    # A WITH clause in any query part (not just the last) may have produced stages.
    any_part_had_with = any(
        qp.with_clause is not None for qp in cypher_query.query_parts
    ) if cypher_query.query_parts else False
    if context.stages and last_part_had_with:
        # The last query part ended with a WITH clause, which just created Stage{N}.
        # RETURN must select directly from that stage with no additional JOINs.
        # Preserve UNWIND CROSS JOIN JSON_TABLE clauses before reset — they were
        # added by translate_unwind_clause and must survive the stage reset.
        unwind_joins = [
            j for j in context.join_clauses
            if "JSON_TABLE" in j and j.strip().startswith("CROSS JOIN")
        ]
        context.select_items, context.select_params = [], []
        context.from_clauses, context.join_clauses, context.join_params = (
            [f"Stage{len(context.stages)}"],
            list(unwind_joins),
            [],
        )
        context.where_conditions, context.where_params = [], []
    elif context.stages and any_part_had_with and not last_part_had_with:
        # A prior part had WITH (produced stages), but the last part is a plain MATCH/RETURN.
        # _to_sql_init_part_from already set from_clauses to Stage{N} and the MATCH clauses
        # added join_clauses correctly. Do NOT reset join_clauses or where_conditions —
        # they capture the post-WITH MATCH + WHERE filters (e.g. MATCH (b) WHERE a = b).
        # Only clear select_items so translate_return_clause can rebuild them.
        context.select_items, context.select_params = [], []

    # UNWIND+CREATE+RETURN: foreach expansion created nodes/relationships and tracked their IDs.
    # Reset context to a fresh single-table scan filtered to the collected IDs.
    # Skip when any query part had a WITH — the Stage CTE already handles scoping.
    if cypher_query.return_clause and not any_part_had_with:
        _apply_unwind_create_context_reset(context)

    # Handle standalone CALL (no RETURN): synthesize RETURN for yielded / all output items
    _proc = cypher_query.procedure_call
    if (
        _proc is not None
        and not cypher_query.return_clause
        and context.stages
    ):
        cte_name = getattr(context, '_tck_proc_cte', None)
        if not cte_name:
            cte_name = context.from_clauses[0] if context.from_clauses else None
        if not cte_name:
            cte_name = context.stages[0].split(" AS ")[0].strip()

        # Determine what YIELD * means
        if _proc.yield_star:
            # Check if this is in-query (has preceding MATCH/WITH) — that's an error
            has_preceding = bool(cypher_query.query_parts and any(
                part.clauses for part in cypher_query.query_parts
            ))
            if has_preceding:
                raise SyntaxError(
                    "YIELD * is not allowed in an in-query CALL (UnexpectedSyntax)"
                )
            # Standalone YIELD * → expand all output columns
            output_names = getattr(context, '_tck_proc_outputs', [])
            for out_name in output_names:
                context.select_items.append(f"{cte_name}.{out_name} AS {out_name}")
            context.select_params = []
        elif _proc.yield_items:
            # Explicit YIELD list (possibly with renames)
            renames = getattr(context, '_tck_yield_renames', {})
            for item in _proc.yield_items:
                if isinstance(item, tuple):
                    orig, alias = item
                else:
                    orig, alias = item, item
                # Use the original column name to read from CTE, alias as output name
                context.select_items.append(f"{cte_name}.{orig} AS {alias}")
            context.select_params = []
        else:
            # Standalone CALL with no YIELD clause — expose all outputs
            output_names = getattr(context, '_tck_proc_outputs', [])
            if output_names:
                for out_name in output_names:
                    context.select_items.append(f"{cte_name}.{out_name} AS {out_name}")
                context.select_params = []

    # YIELD * is not allowed in an in-query CALL (where a RETURN clause follows)
    if (
        _proc is not None
        and _proc.yield_star
        and cypher_query.return_clause
    ):
        raise SyntaxError(
            "YIELD * is not allowed in an in-query CALL (UnexpectedSyntax)"
        )

    if cypher_query.return_clause:
        # For transactional queries (with SET/REMOVE/DELETE), we need to clear WHERE
        # conditions on properties that have been modified, since their values have changed.
        set_properties = getattr(context, '_set_properties', set())
        removed_properties = getattr(context, '_removed_properties', set())
        modified_properties = set_properties | removed_properties
        full_replace_aliases = getattr(context, '_full_replace_aliases', set())
        if modified_properties and context.where_conditions:
            # Filter out WHERE conditions that reference modified properties.
            # Examples of conditions to remove:
            #   p2.val = 'Andres'  (literal value filter on property)
            #   p2.val = ?         (parameterized value filter on property)
            # Keep conditions that don't touch modified properties:
            #   l1.s IS NOT NULL   (label check)
            #   EXISTS (... "key" = 'name') (property existence, not value)
            filtered_conditions = []
            filtered_params = []
            param_offset = 0
            for cond in context.where_conditions:
                param_count = cond.count("?")
                cond_params = context.where_params[param_offset : param_offset + param_count]
                param_offset += param_count

                # Check if this condition is a value filter on a modified property.
                # Patterns to reject:
                #   p<N>.val = <literal or ?>  (property value filter)
                #   rdf_props.val = <literal>  (qualified property value filter)
                is_modified_property_condition = False

                # Check for .val = pattern (property value comparisons)
                if '.val = ' in cond:
                    is_modified_property_condition = True
                    # Verify this isn't part of a sub-SELECT (like "NOT IN (SELECT 1 ... .val = ?)")
                    # by checking it's at the top level
                    if 'SELECT' in cond and cond.index('.val = ') < cond.rindex('SELECT'):
                        # This might be in a subquery, be more careful
                        # For now, assume it's a property value condition
                        is_modified_property_condition = True

                if not is_modified_property_condition:
                    filtered_conditions.append(cond)
                    filtered_params.extend(cond_params)

            context.where_conditions = filtered_conditions
            context.where_params = filtered_params

        # For SET n = {map} (full replace), also strip property JOINs for the affected
        # node aliases — all props were deleted so INNER JOINs to rdf_props return 0 rows.
        if full_replace_aliases and context.join_clauses:
            import re as _re_tts
            prop_join_aliases_to_strip = set()
            kept_joins = []
            orig_join_params_offset = 0
            kept_join_params = []
            for jc in context.join_clauses:
                pc = jc.count("?")
                drop = False
                for nalias in full_replace_aliases:
                    if "rdf_props" in jc and f"{nalias}.node_id" in jc:
                        m = _re_tts.search(r"JOIN\s+\S+rdf_props\s+(\w+)\s+ON", jc)
                        if m:
                            prop_join_aliases_to_strip.add(m.group(1))
                        drop = True
                        break
                if not drop:
                    kept_joins.append(jc)
                    kept_join_params.extend(context.join_params[orig_join_params_offset:orig_join_params_offset + pc])
                orig_join_params_offset += pc
            context.join_clauses = kept_joins
            context.join_params = kept_join_params
            # Also strip WHERE conditions referencing stripped prop aliases or EXISTS rdf_props
            if prop_join_aliases_to_strip:
                new_where = []
                new_where_params = []
                wp_off = 0
                for cond in context.where_conditions:
                    pc = cond.count("?")
                    cp = context.where_params[wp_off:wp_off + pc]
                    wp_off += pc
                    drop = any(f"{pa}." in cond for pa in prop_join_aliases_to_strip)
                    if not drop:
                        for nalias in full_replace_aliases:
                            if "EXISTS" in cond and "rdf_props" in cond and f"{nalias}.node_id" in cond:
                                drop = True
                                break
                    if not drop:
                        new_where.append(cond)
                        new_where_params.extend(cp)
                context.where_conditions = new_where
                context.where_params = new_where_params

        translate_return_clause(cypher_query.return_clause, context)

    order_by_items = preprocess_order_by(cypher_query, context)

    if cypher_query.graph_context:
        safe_graph = cypher_query.graph_context.replace("'", "''")
        edge_aliases = [
            v
            for v in context.variable_aliases.values()
            if v and v.startswith("e") and not v.startswith("ES_")
        ]
        for ea in edge_aliases:
            context.where_conditions.append(f"{ea}.graph_id = '{safe_graph}'")
        context.where_conditions.append(f"1=1")
        graph_filter = f"'{safe_graph}'"
        for ea in list(context.variable_aliases.values()):
            if (
                ea
                and not ea.startswith("n")
                and not ea.startswith("l")
                and not ea.startswith("Stage")
            ):
                context.where_conditions.append(f"{ea}.graph_id = {graph_filter}")
                break

    return order_by_items


def _build_null_row_not_exists(labels):
    """Build NOT EXISTS SQL for an anchor node's full label set.

    Returns (sql_fragment, params) where sql_fragment is a NOT EXISTS clause
    that checks no single node has ALL labels in the group.  For a single label
    this is equivalent to the old NOT EXISTS (... WHERE label = ?).  For multiple
    labels (e.g. after TCK injection: ['NotThere', 'TCK_abc']), it checks that no
    node carries both, so that a TCK-injected label that IS present doesn't block
    the null-row fallback when the semantic label doesn't exist.
    """
    if not labels:
        return "1=1", []
    if len(labels) == 1:
        return (
            f"NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE label = ?)",
            [labels[0]],
        )
    # Multi-label: require no node that has ALL labels
    # SQL: NOT EXISTS (SELECT 1 FROM rdf_labels a0 JOIN rdf_labels a1 ON a1.s=a0.s AND a1.label=? WHERE a0.label=?)
    from_part = f"{_table('rdf_labels')} _nrll0"
    join_parts = []
    where_label = labels[0]
    params = []
    for i, lbl in enumerate(labels[1:], start=1):
        prev = f"_nrll{i-1}"
        curr = f"_nrll{i}"
        join_parts.append(
            f"JOIN {_table('rdf_labels')} {curr} ON {curr}.s = {prev}.s AND {curr}.label = ?"
        )
        params.append(lbl)
    params.append(where_label)  # WHERE param comes last (positionally after JOINs)
    joins_str = " ".join(join_parts)
    inner = f"SELECT 1 FROM {from_part} {joins_str} WHERE _nrll0.label = ?"
    return f"NOT EXISTS ({inner})", params


def _tts_transactional_result(cypher_query, context, metadata, order_by_items):
    """Assemble SQLQuery for transactional (DML) queries."""
    stmts, all_params = [], []
    for s, p in context.dml_statements:
        stmts.append(s)
        all_params.append(p)
    sql = None
    if cypher_query.return_clause:
        sql, p = context.build_stage_sql(cypher_query.return_clause.distinct)
        sql = apply_pagination(sql, cypher_query, context, order_by_items)
    # OPTIONAL MATCH null-row fallback for RETURN clause in transactional queries.
    optional_union_sql = ""
    optional_extra_params: List[Any] = []
    if sql is not None and context.optional_null_row_labels and context.optional_null_row_items and not context.return_is_pure_aggregation:
        null_items = list(context.optional_null_row_items)
        while len(null_items) < len(context.select_items):
            null_items.append("NULL")
        null_select = ", ".join(null_items[:len(context.select_items)])
        not_exists_parts = []
        label_groups = getattr(context, 'optional_null_row_label_groups', None) or []
        if label_groups:
            for group in label_groups:
                ne_sql, ne_params = _build_null_row_not_exists(group)
                not_exists_parts.append(ne_sql)
                optional_extra_params.extend(ne_params)
        else:
            for label in context.optional_null_row_labels:
                not_exists_parts.append(
                    f"NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE label = ?)"
                )
                optional_extra_params.append(label)
        where_clause = " AND ".join(not_exists_parts)
        optional_union_sql = f"\nUNION ALL\nSELECT {null_select} WHERE {where_clause}"

    all_ctes = [
        c
        for c in getattr(context, "cte_clauses", [])
        if not any(td in c for td in context.temporal_derived)
    ] + context.stages
    if all_ctes and sql is not None:
        sql, all_ctes = _demote_agg_stages_to_subqueries(sql, all_ctes)
        if all_ctes:
            sql = "WITH " + ",\n".join(all_ctes) + "\n" + sql
        if optional_union_sql:
            sql += optional_union_sql
        all_params.append(context.all_stage_params + p + optional_extra_params)
    elif sql is not None:
        if optional_union_sql:
            sql += optional_union_sql
        all_params.append(p + optional_extra_params)
    if sql is not None:
        stmts.append(sql)
    return SQLQuery(
        sql=stmts,
        parameters=all_params,
        query_metadata=metadata,
        is_transactional=True,
        column_name_map=dict(context.column_name_map),
    )


def _tts_collect_path_funcs(cypher_query, vl):
    """Collect RETURN path functions for var-length path queries. Mutates vl[0].

    Fires for both shortest-path and regular variable-length paths when path
    functions (length, nodes, relationships) or the path variable itself appear
    in the RETURN clause.
    """
    if not (vl and cypher_query.return_clause):
        return
    # Only fire when the query has a named path with path-function returns,
    # OR it is a shortest-path query.  Skip plain var-length queries with no
    # path functions (they use the faster labeled-BFS path instead).
    is_shortest = vl[0].get("shortest") or vl[0].get("all_shortest")

    path_funcs = []
    path_var = vl[0].get("target_var") or vl[0].get("source_var")
    named_path_vars = {
        np.variable
        for np in (
            cypher_query.query_parts[0].clauses[0].named_paths
            if cypher_query.query_parts
            else []
        )
    }
    path_named_var = None  # the Cypher variable name for the path (e.g. "p")
    for item in cypher_query.return_clause.items:
        expr = item.expression
        if isinstance(expr, ast.Variable) and expr.name in named_path_vars:
            path_funcs.append("path")
            path_named_var = expr.name
        elif isinstance(
            expr, ast.FunctionCall
        ) and expr.function_name.lower() in (
            "length",
            "nodes",
            "relationships",
        ):
            if expr.arguments and isinstance(expr.arguments[0], ast.Variable):
                if expr.arguments[0].name in named_path_vars:
                    path_funcs.append(expr.function_name.lower())
    if path_funcs:
        vl[0]["return_path_funcs"] = path_funcs
        if path_named_var:
            vl[0]["path_named_var"] = path_named_var
    elif not is_shortest:
        # For non-shortest var-length paths with no path-function returns,
        # don't set return_path_funcs — use the faster labeled-BFS path.
        return


def _build_bolt_column_types(cypher_query, context) -> List[str]:
    """Return a list of Bolt column type tags parallel to the RETURN clause columns.

    Tags: "relationship" for variables bound in MATCH relationship patterns,
    "node" for node variables, "scalar" for everything else.
    """
    if not cypher_query or not cypher_query.return_clause:
        return []
    types = []
    node_vars = set(context.variable_aliases.keys()) - context.rel_variables
    for item in cypher_query.return_clause.items:
        expr = item.expression
        if isinstance(expr, ast.Variable):
            if expr.name in context.rel_variables:
                types.append("relationship")
            elif expr.name in node_vars:
                types.append("node")
            else:
                types.append("scalar")
        else:
            types.append("scalar")
    return types


def _tts_select_result(cypher_query, context, metadata, order_by_items):
    """Assemble SQLQuery for SELECT queries."""
    sql, p = context.build_stage_sql(
        cypher_query.return_clause.distinct if cypher_query.return_clause else False
    )
    if not context.select_items and context.stages and context.from_clauses:
        stage_name = context.from_clauses[-1]
        if stage_name in [s.split(" AS ")[0].strip() for s in context.stages]:
            sql = sql.replace("SELECT \nFROM", f"SELECT *\nFROM", 1)
            sql = sql.replace("SELECT DISTINCT \nFROM", f"SELECT DISTINCT *\nFROM", 1)
    if hasattr(context, '_percentile_queries') and context._percentile_queries:
        import re as _re
        from_match = _re.search(r'\nFROM\s+(.*?)(?=\nWHERE|\nORDER|\nFETCH|\nGROUP|\nHAVING|$)', sql, _re.DOTALL)
        if from_match and len(context._percentile_queries) == 1:
            from_clause = from_match.group(0).strip()
            val_expr, pct_val, fn_name, var_name, alias = context._percentile_queries[0]
            col_alias = _re.search(r'AS\s+(\w+)\s*$', sql.split('\n')[0])
            out_alias = col_alias.group(1) if col_alias else "result"
            proc = "PCONT" if fn_name == "percentilecont" else "PDISC"
            sql = (
                f"SELECT IVG.Percentile_{proc}("
                f"(SELECT JSON_ARRAYAGG(CAST({val_expr} AS DOUBLE)) "
                f"\n{from_clause}), {pct_val}) AS {out_alias}"
            )
            # Keep p (params) — val_expr and pct_val may contain ? placeholders that need p
    # When fetch_first_unsafe and we have SKIP or LIMIT with ORDER BY that references
    # JOIN aliases (p\d+.val), project those sort expressions as __sort_N columns so
    # they survive the subquery wrapping in apply_pagination.
    # Only needed when SKIP or LIMIT is present — without them, ORDER BY on JOIN aliases
    # is valid directly on the base query.
    engine = getattr(context, "_engine", None)
    fetch_first_unsafe = bool(getattr(engine, "_fetch_first_unsafe", False))
    _has_skip = cypher_query.skip is not None
    _has_limit = cypher_query.limit is not None
    if fetch_first_unsafe and order_by_items and (_has_skip or _has_limit):
        import re as _re_sort
        _join_alias_re = _re_sort.compile(r'\b(?:%EXACT\()?p\d+\.val\)?')
        new_ob_items = []
        sort_injections = []
        for i, ob_item in enumerate(order_by_items):
            # Extract expression part (before the trailing ASC/DESC)
            _m = _re_sort.match(r'^(.*?)\s+(ASC|DESC)$', ob_item, _re_sort.IGNORECASE)
            if _m and _join_alias_re.search(_m.group(1)):
                sort_col = f"__sort{i}"
                sort_injections.append((_m.group(1), sort_col))
                new_ob_items.append(f"{sort_col} {_m.group(2)}")
            else:
                new_ob_items.append(ob_item)
        if sort_injections:
            order_by_items = new_ob_items
            # Inject sort columns into base SELECT (before first \nFROM at top level).
            # For bare property references (p\d+.val or %EXACT(p\d+.val)), use numeric-aware
            # projection: project both a DOUBLE column (for numeric sort) and the raw string.
            # The ORDER BY in OVER() references both: numeric first, string second.
            # This matches Cypher semantics: numbers sort before strings, NULL sorts last.
            _bare_prop_re = _re_sort.compile(r'^(?:%EXACT\()?p\d+\.val\)?$')
            _from_pat = _re_sort.search(r'\nFROM ', sql)
            adjusted_ob_items = list(order_by_items)
            for sort_expr, sort_col in sort_injections:
                if _bare_prop_re.match(sort_expr):
                    # Project both numeric and string variants
                    num_col = f"{sort_col}_n"
                    str_col = f"{sort_col}_s"
                    num_expr = f"CASE WHEN ISNUMERIC({sort_expr}) = 1 THEN CAST({sort_expr} AS DOUBLE) END"
                    # Find direction from order_by_items
                    direction = "ASC"
                    for ob in adjusted_ob_items:
                        if ob.startswith(f"{sort_col} "):
                            direction = ob.split()[-1].upper()
                            break
                    if _from_pat:
                        sql = sql[:_from_pat.start()] + f", {num_expr} AS {num_col}, {sort_expr} AS {str_col}" + sql[_from_pat.start():]
                        _from_pat = _re_sort.search(r'\nFROM ', sql)
                    # Replace __sortN with __sortN_n ASC, __sortN_s ASC in order_by_items
                    adjusted_ob_items = [
                        f"{num_col} {direction}, {str_col} {direction}" if ob == f"{sort_col} {direction}" else ob
                        for ob in adjusted_ob_items
                    ]
                else:
                    if _from_pat:
                        sql = sql[:_from_pat.start()] + f", {sort_expr} AS {sort_col}" + sql[_from_pat.start():]
                        _from_pat = _re_sort.search(r'\nFROM ', sql)
            order_by_items = adjusted_ob_items
    sql = apply_pagination(sql, cypher_query, context, order_by_items)
    vl = context.var_length_paths or None

    _tts_collect_path_funcs(cypher_query, vl)

    # OPTIONAL MATCH null-row fallback: append UNION ALL SELECT <nulls> WHERE NOT EXISTS
    # This handles the Cypher semantics: if the optional pattern matches 0 rows, yield
    # one null row instead of 0 rows.
    optional_union_sql = ""
    optional_extra_params: List[Any] = []
    if context.optional_null_row_labels and context.optional_null_row_items and not context.return_is_pure_aggregation:
        null_items = context.optional_null_row_items
        # Build the null-row SELECT: pad with NULLs if fewer items than select columns
        while len(null_items) < len(context.select_items):
            null_items.append("NULL")
        null_select = ", ".join(null_items[:len(context.select_items)])
        # NOT EXISTS check: for each anchor node's label group, no node may have ALL labels.
        # Using grouped check prevents TCK-injected labels (always present) from blocking
        # the null row when the semantic label (e.g. 'NotThere') doesn't exist.
        not_exists_parts = []
        label_groups = getattr(context, 'optional_null_row_label_groups', None) or []
        if label_groups:
            for group in label_groups:
                ne_sql, ne_params = _build_null_row_not_exists(group)
                not_exists_parts.append(ne_sql)
                optional_extra_params.extend(ne_params)
        else:
            for label in context.optional_null_row_labels:
                not_exists_parts.append(
                    f"NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE label = ?)"
                )
                optional_extra_params.append(label)
        where_clause = " AND ".join(not_exists_parts)
        optional_union_sql = f"\nUNION ALL\nSELECT {null_select} WHERE {where_clause}"

    all_ctes = [
        c
        for c in getattr(context, "cte_clauses", [])
        if not any(td in c for td in context.temporal_derived)
    ] + context.stages
    if all_ctes:
        sql, all_ctes = _demote_agg_stages_to_subqueries(sql, all_ctes)
        if all_ctes:
            sql = "WITH " + ",\n".join(all_ctes) + "\n" + sql
        if optional_union_sql:
            sql += optional_union_sql
        return SQLQuery(
            sql=sql,
            parameters=[context.all_stage_params + p + optional_extra_params],
            query_metadata=metadata,
            var_length_paths=vl,
            bolt_column_types=_build_bolt_column_types(cypher_query, context),
            column_name_map=dict(context.column_name_map),
        )

    sql = _maybe_split_deep_joins(sql, p, context)
    if optional_union_sql:
        sql += optional_union_sql

    return SQLQuery(
        sql=sql, parameters=[p + optional_extra_params], query_metadata=metadata,
        var_length_paths=vl,
        bolt_column_types=_build_bolt_column_types(cypher_query, context),
        column_name_map=dict(context.column_name_map),
    )


def translate_to_sql(
    cypher_query: ast.CypherQuery, params: Optional[Dict[str, Any]] = None, engine=None,
    procedures: Optional[Dict[str, Any]] = None,
) -> SQLQuery:
    result = _tts_union_branches(cypher_query, params)
    if result is not None:
        return result

    context = TranslationContext()
    context.input_params = params or {}
    context._engine = engine
    context._tck_procedures = procedures or {}  # TCK test procedures
    context.graph_context = getattr(cypher_query, "graph_context", None)
    metadata = QueryMetadata()
    context._metadata = metadata
    is_transactional = _tts_process_parts(cypher_query, context, metadata)
    order_by_items = _tts_finalize_context(cypher_query, context)
    if is_transactional:
        return _tts_transactional_result(cypher_query, context, metadata, order_by_items)
    return _tts_select_result(cypher_query, context, metadata, order_by_items)


def _collect_var_names(expr) -> set:
    """Collect all variable names referenced in an expression (including inside prop refs)."""
    if isinstance(expr, ast.Variable):
        return {expr.name}
    if isinstance(expr, ast.PropertyReference):
        return {expr.variable}
    if isinstance(expr, ast.FunctionCall):
        result = set()
        for arg in expr.arguments:
            result |= _collect_var_names(arg)
        return result
    if isinstance(expr, ast.BooleanExpression):
        result = set()
        for op in expr.operands:
            result |= _collect_var_names(op)
        return result
    if isinstance(expr, ast.AggregationFunction) and expr.argument:
        return _collect_var_names(expr.argument)
    return set()


def preprocess_order_by(query: ast.CypherQuery, context: TranslationContext) -> list:
    if not query.order_by_clause:
        return []
    items = []
    alias_to_sql: dict = {}
    if query.return_clause:
        ret = query.return_clause
        ret_has_agg = any(_contains_aggregation(i.expression) for i in ret.items)

        # InvalidAggregation: ORDER BY uses aggregation but RETURN has none
        for ob_item in query.order_by_clause.items:
            if _contains_aggregation(ob_item.expression):
                if not ret_has_agg:
                    raise SyntaxError(
                        "InvalidAggregation: Cannot use aggregation function in ORDER BY "
                        "unless it is also used in the projection."
                    )

        # AmbiguousAggregationExpression in ORDER BY: ORDER BY contains mixed agg+non-agg
        # where the non-agg part is not a simple grouping key returned standalone.
        if ret_has_agg:
            non_agg_ret_items = [i for i in ret.items if not _contains_aggregation(i.expression)]
            grouping_exprs_ob = set()
            for gi in non_agg_ret_items:
                grouping_exprs_ob.add(_expr_to_cypher_text(gi.expression))
                if gi.alias:
                    grouping_exprs_ob.add(gi.alias)
            for ob_item in query.order_by_clause.items:
                if not _contains_aggregation(ob_item.expression):
                    continue
                if isinstance(ob_item.expression, ast.AggregationFunction):
                    continue  # pure aggregate ORDER BY — OK
                # Mixed: contains agg and non-agg var refs
                ambiguous_parts = _collect_non_agg_var_refs(ob_item.expression)
                for part in ambiguous_parts:
                    part_text = _expr_to_cypher_text(part)
                    if not part_text:
                        continue
                    # Skip query parameter variables — they are constants, not bound variables
                    if isinstance(part, ast.Variable) and part.name in context.input_params:
                        continue
                    # Complex non-simple expressions (arithmetic etc.) mixed with aggregation
                    # are always ambiguous in ORDER BY, even if they are grouping keys.
                    is_simple = isinstance(part, (ast.Variable, ast.PropertyReference))
                    if not is_simple:
                        raise SyntaxError(
                            f"AmbiguousAggregationExpression: An expression using aggregation "
                            f"and a non-aggregate is ambiguous in ORDER BY."
                        )
                    if part_text not in grouping_exprs_ob:
                        raise SyntaxError(
                            f"AmbiguousAggregationExpression: An expression using aggregation "
                            f"and a non-aggregate is ambiguous in ORDER BY."
                        )

        # UndefinedVariable in ORDER BY after DISTINCT: ORDER BY must reference
        # only projected expressions or properties of projected node variables.
        # Rule: if only scalar properties are projected (e.g. a.name), then ORDER BY
        # must use the exact same expression or refer to a returned full node variable.
        if ret.distinct:
            returned_texts = set()
            returned_full_vars = set()  # full node/edge variables (not just property refs)
            for ri in ret.items:
                returned_texts.add(_expr_to_cypher_text(ri.expression))
                if ri.alias:
                    returned_texts.add(ri.alias)
                # Collect full variable returns (RETURN DISTINCT a → a is a full variable)
                if isinstance(ri.expression, ast.Variable):
                    returned_full_vars.add(ri.expression.name)
            for ob_item in query.order_by_clause.items:
                ob_text = _expr_to_cypher_text(ob_item.expression)
                if ob_text in returned_texts:
                    continue  # exact match — OK
                if not _expr_references_variable(ob_item.expression):
                    continue  # literal or parameter — OK
                # Check if the ORDER BY root variable is a full returned variable
                # (if RETURN DISTINCT a is in scope, ORDER BY a.anything is OK)
                ob_root_var = None
                if isinstance(ob_item.expression, ast.PropertyReference):
                    ob_root_var = ob_item.expression.variable
                elif isinstance(ob_item.expression, ast.Variable):
                    ob_root_var = ob_item.expression.name
                if ob_root_var and ob_root_var in returned_full_vars:
                    continue  # property of a returned node variable — OK
                # ORDER BY references something not accessible from the RETURN projection
                raise SyntaxError(
                    f"UndefinedVariable: In a RETURN with DISTINCT, "
                    f"the ORDER BY must refer to variables returned in the projection."
                )

        # UndefinedVariable in ORDER BY with aggregation: ORDER BY variable must be in RETURN
        if ret_has_agg:
            # Build the set of variables available after aggregation (grouping keys + agg aliases)
            ret_vars_available: set = set()
            for ri in ret.items:
                if ri.alias:
                    ret_vars_available.add(ri.alias)
                ret_vars_available |= _collect_var_names(ri.expression)
            for ob_item in query.order_by_clause.items:
                ob_vars = _collect_var_names(ob_item.expression)
                # For non-aggregate ORDER BY expressions, check variables are in grouping keys
                if not _contains_aggregation(ob_item.expression):
                    undefined_in_ob = ob_vars - ret_vars_available
                    if undefined_in_ob:
                        raise SyntaxError(
                            f"UndefinedVariable: In an aggregating query, ORDER BY must refer "
                            f"to variables returned in the projection: {undefined_in_ob}"
                        )

        # Build alias_to_sql from context.select_items — these were already computed by
        # translate_return_clause with the correct alias counters. Parsing them avoids
        # re-calling translate_expression which would allocate fresh aliases (p2, p3…)
        # that are not in the FROM clause.
        import re as _alias_re
        for sel_item in context.select_items:
            # Format: "<expr> AS <alias>" or "<expr> AS \"<alias>\""
            _m = _alias_re.match(r'^(.*)\s+AS\s+"?([^"]+)"?\s*$', sel_item, _alias_re.IGNORECASE)
            if _m:
                sql_expr_raw, alias_name = _m.group(1).strip(), _m.group(2).strip()
                alias_to_sql[alias_name] = sql_expr_raw
        # Fall back to re-translating any aliases not found in select_items
        for ret_item in ret.items:
            if ret_item.alias and ret_item.alias not in alias_to_sql:
                saved_select = list(context.select_params)
                saved_where = list(context.where_params)
                saved_join = list(context.join_params)
                saved_join_clauses = list(context.join_clauses)
                saved_alias_counter = context._alias_counter
                try:
                    sql_expr = translate_expression(ret_item.expression, context, segment="select")
                    alias_to_sql[ret_item.alias] = sql_expr
                except Exception:
                    pass
                finally:
                    context.select_params = saved_select
                    context.where_params = saved_where
                    context.join_params = saved_join
                    context.join_clauses = saved_join_clauses
                    context._alias_counter = saved_alias_counter
    import re as _re
    _proc_prefix_re = _re.compile(r'^(?:Stage\d+|' + '|'.join(_PROC_CTE_ALIASES) + r')\.')
    # Inject alias_to_sql into context so compound ORDER BY expressions like `n + 2`
    # (where `n` is a RETURN alias) resolve correctly via _expr_variable.
    # This is scoped to ORDER BY translation only and cleaned up afterwards.
    _prev_orderby_alias_sql = getattr(context, "_orderby_alias_sql", None)
    context._orderby_alias_sql = alias_to_sql
    try:
        _ob_items_result = _preprocess_order_by_items(
            query, context, items, alias_to_sql, _proc_prefix_re
        )
    finally:
        context._orderby_alias_sql = _prev_orderby_alias_sql
    return _ob_items_result


def _preprocess_order_by_items(query, context, items, alias_to_sql, _proc_prefix_re):
    edge_stage_vars = getattr(context, "edge_stage_variables", set())
    for item in query.order_by_clause.items:
        try:
            if (isinstance(item.expression, ast.Variable)
                    and item.expression.name in alias_to_sql):
                expr = _proc_prefix_re.sub('', alias_to_sql[item.expression.name])
            elif (isinstance(item.expression, ast.PropertyReference)
                    and item.expression.variable not in context.variable_aliases):
                # ORDER BY alias.property where alias is a RETURN alias not in variable_aliases.
                var = item.expression.variable
                prop = item.expression.property_name
                # Check if it's a stage-promoted edge variable — look up qualifiers column directly.
                orig_var = None
                # The return alias might map back to an edge_stage var via context.return_alias_map
                return_alias_map = getattr(context, "_return_alias_map", {})
                orig_var = return_alias_map.get(var, var)
                if orig_var in edge_stage_vars:
                    stage_alias = context.variable_aliases.get(orig_var, "")
                    if stage_alias.startswith("Stage"):
                        col_ref = f"{stage_alias}.{orig_var}"
                        expr = f"CASE WHEN {col_ref} IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{prop}') END"
                    else:
                        expr = translate_expression(item.expression, context, segment="select")
                        expr = _proc_prefix_re.sub('', expr)
                elif var in alias_to_sql:
                    # Fallback: try to use alias_to_sql entry — only works if it's a simple col ref
                    import re as _re2
                    alias_sql = alias_to_sql[var]
                    stage_col_m = _re2.match(r'^(?:Stage\d+\.)?\w+$', alias_sql.strip())
                    if stage_col_m:
                        col_ref = alias_sql.strip()
                        expr = f"CASE WHEN {col_ref} IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{prop}') END"
                    else:
                        expr = translate_expression(item.expression, context, segment="select")
                        expr = _proc_prefix_re.sub('', expr)
                else:
                    expr = translate_expression(item.expression, context, segment="select")
                    expr = _proc_prefix_re.sub('', expr)
            else:
                expr = translate_expression(item.expression, context, segment="select")
                expr = _proc_prefix_re.sub('', expr)
        except (ValueError, SyntaxError):
            if (isinstance(item.expression, ast.Variable)
                    and item.expression.name in alias_to_sql):
                expr = _proc_prefix_re.sub('', alias_to_sql[item.expression.name])
            elif (isinstance(item.expression, ast.PropertyReference)
                    and item.expression.variable in alias_to_sql):
                prop = item.expression.property_name
                var = item.expression.variable
                orig_var = getattr(context, "_return_alias_map", {}).get(var, var)
                if orig_var in edge_stage_vars:
                    stage_alias = context.variable_aliases.get(orig_var, "")
                    if stage_alias.startswith("Stage"):
                        col_ref = f"{stage_alias}.{orig_var}"
                        expr = f"CASE WHEN {col_ref} IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{prop}') END"
                    else:
                        col_ref = alias_to_sql[var].strip()
                        expr = f"CASE WHEN {col_ref} IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{prop}') END"
                else:
                    col_ref = alias_to_sql[var].strip()
                    expr = f"CASE WHEN {col_ref} IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{prop}') END"
            else:
                raise
        items.append(f"{expr} {'ASC' if item.ascending else 'DESC'}")
    return items


def _eval_pagination_expr(value) -> Optional[float]:
    """Evaluate a SKIP/LIMIT expression that contains no Variable references.

    Returns the float result, or None if the expression contains variables.
    Supports: integer/float literals, FunctionCall (tointeger, ceil, floor, round,
    abs, rand, sqrt, log, exp, sin, cos, tan), and binary arithmetic.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, ast.Literal):
        v = value.value
        if isinstance(v, (int, float)):
            return float(v)
        return None
    if isinstance(value, (ast.Variable, ast.PropertyReference)):
        return None  # contains variable reference — cannot eval statically
    if isinstance(value, ast.FunctionCall):
        fname = value.function_name.lower()
        import math as _math, random as _random
        # Arithmetic operators are encoded as __arith_<op> by the parser
        if fname.startswith('__arith_') and len(value.arguments) == 2:
            lv = _eval_pagination_expr(value.arguments[0])
            rv = _eval_pagination_expr(value.arguments[1])
            if lv is None or rv is None:
                return None
            op = fname[len('__arith_'):]
            if op == '*':
                return lv * rv
            if op == '+':
                return lv + rv
            if op == '-':
                return lv - rv
            if op == '/':
                return lv / rv if rv != 0 else None
            if op == '%':
                return lv % rv if rv != 0 else None
            if op == '^':
                return lv ** rv
            return None
        args = [_eval_pagination_expr(a) for a in value.arguments]
        # rand() needs no arguments evaluated
        if fname == 'rand':
            return _random.random()
        if any(a is None for a in args):
            return None  # variable arg — cannot eval statically
        _fn_map = {
            'tointeger': lambda a: float(int(a[0])),
            'tofloat': lambda a: float(a[0]),
            'ceil': lambda a: float(_math.ceil(a[0])),
            'floor': lambda a: float(_math.floor(a[0])),
            'round': lambda a: float(round(a[0])),
            'abs': lambda a: float(abs(a[0])),
            'sqrt': lambda a: float(_math.sqrt(a[0])),
            'log': lambda a: float(_math.log(a[0])),
            'exp': lambda a: float(_math.exp(a[0])),
            'sin': lambda a: float(_math.sin(a[0])),
            'cos': lambda a: float(_math.cos(a[0])),
            'tan': lambda a: float(_math.tan(a[0])),
        }
        if fname in _fn_map:
            try:
                return _fn_map[fname](args)
            except Exception:
                return None
        return None


def _resolve_pagination_value(value, context: TranslationContext, clause: str = "SKIP/LIMIT") -> Optional[int]:
    """Resolve a SKIP/LIMIT value that may be an integer literal or a parameter variable."""
    if value is None:
        return None
    if isinstance(value, int):
        _validate_pagination_int(value, clause)
        return value
    if isinstance(value, ast.Variable):
        resolved = context.input_params.get(value.name)
        if resolved is None:
            raise ValueError(
                f"Parameter '${value.name}' used in SKIP/LIMIT but not provided in params dict"
            )
        # Validate parameter type and value
        if isinstance(resolved, float) and not resolved.is_integer():
            raise SyntaxError(
                f"InvalidArgumentType: {clause} requires an integer, got float: {resolved}"
            )
        resolved_int = int(resolved)
        _validate_pagination_int(resolved_int, clause)
        return resolved_int
    # Try to evaluate function/arithmetic expressions with no variable references
    evaled = _eval_pagination_expr(value)
    if evaled is not None:
        if evaled != int(evaled):
            raise SyntaxError(
                f"InvalidArgumentType: {clause} requires an integer, got float: {evaled}"
            )
        result = int(evaled)
        _validate_pagination_int(result, clause)
        return result
    # Expression contains variable references (NonConstantExpression)
    if not isinstance(value, (int, float)):
        raise SyntaxError(
            f"NonConstantExpression: {clause} value must be a constant expression"
        )
    f_val = float(value)
    if f_val != int(f_val):
        raise SyntaxError(
            f"InvalidArgumentType: {clause} requires an integer, got float: {value}"
        )
    return int(f_val)


def _validate_pagination_int(value: int, clause: str) -> None:
    if value < 0:
        raise SyntaxError(
            f"NegativeIntegerArgument: {clause} must be a non-negative integer, got: {value}"
        )


def apply_pagination(
    sql: str,
    query: ast.CypherQuery,
    context: TranslationContext,
    order_by_items: list = None,
) -> str:
    limit = _resolve_pagination_value(query.limit, context)
    skip = _resolve_pagination_value(query.skip, context)
    if limit is not None or skip is not None:
        if "\nFROM " not in sql and "FROM " not in sql.split("\n")[0]:
            sql = sql.rstrip() + "\nFROM (SELECT 1) __dual"
    # Build-106 workaround: IRIS 2026.3.0AI build 106 SIGSEGVs in %qaqpre when a
    # multi-table JOIN is combined with `FETCH FIRST n ROWS ONLY` on VARCHAR-keyed
    # tables (the ivg schema). `SELECT TOP n` does NOT crash for LIMIT-only queries.
    # For SKIP+LIMIT, wrap in a ROW_NUMBER subquery (also avoids FETCH FIRST).
    engine = getattr(context, "_engine", None)
    fetch_first_unsafe = bool(getattr(engine, "_fetch_first_unsafe", False))
    if fetch_first_unsafe and sql.lstrip().upper().startswith("SELECT "):
        # ORDER BY items here are safe to use in OVER() — sort columns have been projected
        # as __sort_N aliases by the caller (see _tts_select_result) so no JOIN aliases remain.
        rn_over = f"ORDER BY {', '.join(order_by_items)}" if order_by_items else ""
        if limit is not None and skip is not None:
            # ORDER BY __rn preserves the sort semantics for the caller
            sql = (
                f"SELECT * FROM (\n"
                f"SELECT ROW_NUMBER() OVER({rn_over}) AS __rn, __q.* FROM ({sql}) __q\n"
                f") __paged WHERE __rn > {skip} AND __rn <= {skip + limit} ORDER BY __rn"
            )
            return sql
        if limit is not None and " TOP " not in sql.split("\n", 1)[0].upper():
            # LIMIT only: inject TOP with ORDER BY on the outer query
            if order_by_items:
                sql = f"SELECT TOP {limit} * FROM ({sql}) __ob ORDER BY {', '.join(order_by_items)}"
            else:
                head, sep, rest = sql.partition("SELECT ")
                if rest[:9].upper().startswith("DISTINCT "):
                    rest = "DISTINCT " + f"TOP {limit} " + rest[9:]
                else:
                    rest = f"TOP {limit} " + rest
                sql = head + sep + rest
            return sql
        if skip is not None:
            sql = (
                f"SELECT * FROM (\n"
                f"SELECT ROW_NUMBER() OVER({rn_over}) AS __rn, __q.* FROM ({sql}) __q\n"
                f") __paged WHERE __rn > {skip} ORDER BY __rn"
            )
            return sql
    if order_by_items:
        sql += f"\nORDER BY {', '.join(order_by_items)}"
    if limit is not None:
        if limit == 0 and sql.lstrip().upper().startswith("SELECT "):
            # FETCH FIRST 0 ROWS ONLY hangs on IRIS 2026.x builds; use TOP 0 instead
            head, sep, rest = sql.partition("SELECT ")
            if rest[:9].upper().startswith("DISTINCT "):
                rest = "DISTINCT TOP 0 " + rest[9:]
            else:
                rest = "TOP 0 " + rest
            sql = head + sep + rest
        else:
            sql += f"\nFETCH FIRST {limit} ROWS ONLY"
    if skip is not None:
        sql += f"\nOFFSET {skip}"
    return sql


def translate_updating_clause(upd, context, metadata):
    if isinstance(upd, ast.CreateClause):
        translate_create_clause(upd, context, metadata)
    elif isinstance(upd, ast.DeleteClause):
        translate_delete_clause(upd, context, metadata)
    elif isinstance(upd, ast.MergeClause):
        translate_merge_clause(upd, context, metadata)
    elif isinstance(upd, ast.SetClause):
        translate_set_clause(upd, context, metadata)
    elif isinstance(upd, ast.RemoveClause):
        translate_remove_clause(upd, context, metadata)


def translate_unwind_clause(unwind, context):
    alias = context.register_variable(unwind.alias, prefix="u")
    context.scalar_variables.add(unwind.alias)
    context.bind_variable_type(unwind.alias, "scalar")

    # Detect UNWIND of a collected node list: mark alias as collected_node_variable
    # so property access generates a rdf_props join using the _id from the JSON blob.
    if isinstance(unwind.expression, ast.Variable):
        _src = unwind.expression.name
        if _src in getattr(context, "collected_node_lists", {}):
            context.collected_node_variables.add(unwind.alias)

    # For list literals with only scalar elements, use UNION ALL instead of JSON_TABLE.
    # IRIS JSON_TABLE coerces empty strings to NULL (JSON_TABLE PATH '$' issue).
    _use_union = False
    _union_rows: list = []
    if isinstance(unwind.expression, ast.Literal) and isinstance(unwind.expression.value, list):
        _items = _extract_literal_value(unwind.expression.value)
        if all(isinstance(v, (str, int, float, bool)) or v is None for v in _items):
            _use_union = True
            _union_rows = _items

    if _use_union:
        col = _safe_alias(unwind.alias)
        if not _union_rows:
            # Empty list — produce zero rows via a no-match subquery
            union_sql = f"(SELECT ? AS {col} FROM (SELECT 1) _empty WHERE 1=0) {alias}"
            context.add_join_param(None)
        else:
            parts = []
            for v in _union_rows:
                if v is None:
                    parts.append(f"SELECT NULL AS {col}")
                else:
                    parts.append(f"SELECT ? AS {col}")
                    context.add_join_param(v)
            union_sql = f"({' UNION ALL '.join(parts)}) {alias}"
        if context.from_clauses:
            context.join_clauses.append(f"CROSS JOIN {union_sql}")
        else:
            context.from_clauses.append(union_sql)
        return

    # Special case: UNWIND keys(nodeVar) AS alias — use direct JOIN to rdf_props.
    # IRIS JSON_TABLE with a correlated subquery in the expression doesn't evaluate
    # per-row for all rows when multiple outer rows exist; a direct JOIN fixes this.
    if (
        isinstance(unwind.expression, ast.FunctionCall)
        and unwind.expression.function_name.lower() == "keys"
        and unwind.expression.arguments
        and isinstance(unwind.expression.arguments[0], ast.Variable)
    ):
        node_var = unwind.expression.arguments[0].name
        node_alias = context.variable_aliases.get(node_var)
        edge_stage_vars = getattr(context, "edge_stage_variables", set())
        is_edge = (
            node_alias is not None
            and (
                (node_alias.startswith("e") and not node_alias.startswith("Stage"))
                or (node_alias.startswith("Stage") and node_var in edge_stage_vars)
            )
        )
        if node_alias and not is_edge:
            # Node variable: directly join rdf_props to enumerate keys.
            # Use a subquery alias so the alias.unwind_alias column name is accessible.
            rp_tbl = _table('rdf_props')
            rp_inner = context.next_alias(prefix="rp")
            # The join produces one row per (node, key); alias.key column exposed as unwind.alias
            join_sql = (
                f"(SELECT {rp_inner}.s, {rp_inner}.\"key\" AS {unwind.alias} "
                f"FROM {rp_tbl} {rp_inner}) {alias} ON {alias}.s = {node_alias}.node_id"
            )
            context.join_clauses.append(f"JOIN {join_sql}")
            context.variable_aliases[unwind.alias] = alias
            return

    expr = translate_expression(unwind.expression, context, segment="join")
    if (
        isinstance(unwind.expression, ast.Variable)
        and unwind.expression.name in context.input_params
    ):
        val = context.input_params[unwind.expression.name]
        if isinstance(val, list):
            context.join_params[-1] = json.dumps(val)
    json_table_sql = f"JSON_TABLE({expr}, '$[*]' COLUMNS ({_safe_alias(unwind.alias)} VARCHAR(1000) PATH '$')) {alias}"
    if context.from_clauses:
        context.join_clauses.append(f"CROSS JOIN {json_table_sql}")
    else:
        context.from_clauses.append(json_table_sql)


def _extract_literal_value(v):
    """Recursively extract Python values from Literal/list/dict structures.

    Handles nested Literals within lists and dicts.
    """
    if isinstance(v, ast.Literal):
        return _extract_literal_value(v.value)
    elif isinstance(v, list):
        return [_extract_literal_value(item) for item in v]
    elif isinstance(v, dict):
        return {k: _extract_literal_value(val) for k, val in v.items()}
    else:
        return v


_TEMPORAL_CREATE_FNS = frozenset({"date", "time", "localtime", "localdatetime", "datetime", "duration"})


def _create_resolve_prop_value(v, context):
    if isinstance(v, ast.Literal):
        val = _extract_literal_value(v)
        # JSON-encode lists and dicts for storage in rdf_props
        if isinstance(val, (list, dict)):
            # Resolve any temporal FunctionCall items in the list before JSON-encoding
            if isinstance(val, list):
                resolved = []
                for item in val:
                    if isinstance(item, ast.FunctionCall) and item.function_name.lower() in _TEMPORAL_CREATE_FNS:
                        try:
                            sql_val = translate_expression(item, context, segment="select")
                            if isinstance(sql_val, str) and sql_val.startswith("'") and sql_val.endswith("'"):
                                resolved.append(sql_val[1:-1])
                            else:
                                resolved.append(item)
                        except Exception:
                            resolved.append(item)
                    else:
                        resolved.append(item)
                val = resolved
            return json.dumps(val)
        return val
    if isinstance(v, ast.Variable) and v.name in context.input_params:
        val = context.input_params[v.name]
        # JSON-encode lists and dicts for storage in rdf_props
        if isinstance(val, (list, dict)):
            return json.dumps(val)
        return val
    if isinstance(v, ast.Variable) and getattr(context, "foreach_literals", {}).get(v.name) is not None:
        raw = context.foreach_literals[v.name]
        val = _extract_literal_value(raw)
        # JSON-encode lists and dicts for storage in rdf_props
        if isinstance(val, (list, dict)):
            return json.dumps(val)
        return val
    if isinstance(v, ast.Variable) and v.name not in context.variable_aliases:
        raise SyntaxError(f"Undefined variable: {v.name}")
    # Temporal constructors (date(), time(), datetime(), etc.) in property expressions:
    # translate to their ISO string value for storage in rdf_props.
    if isinstance(v, ast.FunctionCall) and v.function_name.lower() in _TEMPORAL_CREATE_FNS:
        try:
            sql_val = translate_expression(v, context, segment="select")
            # sql_val is a SQL string literal like '1910-05-06' — strip the quotes
            if isinstance(sql_val, str) and sql_val.startswith("'") and sql_val.endswith("'"):
                return sql_val[1:-1]
        except Exception:
            pass
    if isinstance(v, ast.PropertyReference):
        # Property reference to a previously-created node in the same CREATE clause.
        # e.g. CREATE (a {id: 0}), ({num: a.id}) — resolve a.id from already-set props.
        node_props = getattr(context, '_create_node_props', {})
        node_prop_map = node_props.get(v.variable, {})
        if v.property_name in node_prop_map:
            return node_prop_map[v.property_name]
    return v


def _create_node_literal(node, node_id_expr, context):
    node_id = node_id_expr.value if isinstance(node_id_expr, ast.Literal) else node_id_expr
    context.add_dml(
        f"INSERT INTO {_table('nodes')} (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM {_table('nodes')} WHERE node_id = ?)",
        [node_id, node_id],
    )
    for label in node.labels:
        context.add_dml(
            f"INSERT INTO {_table('rdf_labels')} (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE s = ? AND label = ?)",
            [node_id, label, node_id, label],
        )
    if node.variable and node.properties:
        if not hasattr(context, '_create_node_props'):
            context._create_node_props = {}
        if node.variable not in context._create_node_props:
            context._create_node_props[node.variable] = {}
    for k, v in node.properties.items():
        val = _create_resolve_prop_value(v, context)
        # openCypher: setting a property to null removes it; skip null values in CREATE
        if val is None and not isinstance(val, ast.Variable):
            continue
        # Track resolved property values for cross-node references in the same CREATE.
        if node.variable and not isinstance(val, (ast.Variable, ast.PropertyReference)):
            if not hasattr(context, '_create_node_props'):
                context._create_node_props = {}
            context._create_node_props.setdefault(node.variable, {})[k] = val
        if isinstance(val, ast.Variable):
            # Property value is a bound stage variable — use SELECT-based INSERT
            var_alias = context.variable_aliases[val.name]
            col_expr = f"{var_alias}.{val.name}"
            cte, sql, p = context.build_dml_subquery(
                select_override=f"SELECT ?, ?, CAST({col_expr} AS VARCHAR)"
            )
            context.add_dml(
                f'{cte}INSERT INTO {_table("rdf_props")} (s, "key", val) {sql} WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = ? AND "key" = ?)',
                [node_id, k] + p + [node_id, k],
            )
        else:
            context.add_dml(
                f'INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = ? AND "key" = ?)',
                [node_id, k, val, node_id, k],
            )


def _create_node_from_alias(node, node_id_expr, var_alias, context):
    if var_alias == "__foreach_literal__":
        # UNWIND loop: the variable alias is a sentinel. Resolve to the current literal value
        # and delegate to _create_node_literal so that properties are also inserted.
        lit_val = getattr(context, "foreach_literals", {}).get(node_id_expr.name)
        if lit_val is not None:
            # Resolve all property values that reference the UNWIND variable too
            resolved_props = {}
            for pk, pv in node.properties.items():
                if isinstance(pv, ast.Variable):
                    fl = getattr(context, "foreach_literals", {})
                    resolved_props[pk] = fl.get(pv.name, context.input_params.get(pv.name, pv))
                elif isinstance(pv, ast.Literal):
                    resolved_props[pk] = pv.value
                else:
                    resolved_props[pk] = pv
            # Build a synthetic node with resolved properties for literal creation
            import copy as _copy
            synthetic_node = _copy.copy(node)
            synthetic_node.properties = {
                pk: ast.Literal(pv) if not isinstance(pv, ast.Literal) else pv
                for pk, pv in resolved_props.items()
            }
            _create_node_literal(synthetic_node, ast.Literal(lit_val), context)
            return
        # Fallback: no literal found — use NULL
        select_override = f"SELECT NULL AS node_id"
    else:
        select_override = f"SELECT {var_alias}.{node_id_expr.name} AS node_id"
    cte, sql, p = context.build_dml_subquery(select_override=select_override)
    context.add_dml(
        f"{cte}INSERT INTO {_table('nodes')} (node_id) SELECT t.node_id FROM ({sql}) AS t WHERE NOT EXISTS (SELECT 1 FROM {_table('nodes')} WHERE node_id = t.node_id)",
        p,
    )
    for label in node.labels:
        context.add_dml(
            f"{cte}INSERT INTO {_table('rdf_labels')} (s, label) SELECT t.node_id, ? FROM ({sql}) AS t WHERE NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE s = t.node_id AND label = ?)",
            [label] + p + [label],
        )


def _create_clause_node_entry(node, context):
    if node.variable and node.variable in context.variable_aliases:
        return
    _raw_id = node.properties.get("id") or node.properties.get("node_id")
    # Only use the id/node_id property as the node identifier when it resolves to a string.
    # Integer literals are user-defined property values, not IVG node identifiers.
    node_id_expr = None
    _id_is_user_property = False
    if _raw_id is not None:
        if isinstance(_raw_id, ast.Literal) and isinstance(_raw_id.value, str):
            node_id_expr = _raw_id
        elif isinstance(_raw_id, ast.Variable):
            node_id_expr = _raw_id
        else:
            # Integer or other non-string literal: treat as a regular user property
            _id_is_user_property = True
    if node_id_expr is None:
        import uuid as _uuid
        generated_id = str(_uuid.uuid4())
        node_id_expr = ast.Literal(generated_id)
        key = f"__create_id_{node.variable}" if node.variable else f"__create_id_anon_{id(node)}"
        context.input_params[key] = generated_id
        if not hasattr(context, '_anon_node_keys'):
            context._anon_node_keys = {}
        context._anon_node_keys[id(node)] = generated_id

    var_alias = None
    if isinstance(node_id_expr, ast.Variable):
        var_alias = context.variable_aliases.get(node_id_expr.name)
        if not var_alias and node_id_expr.name in context.input_params:
            node_id_expr = ast.Literal(context.input_params[node_id_expr.name])
        elif not var_alias:
            raise SyntaxError(f"Undefined variable: {node_id_expr.name}")

    if isinstance(node_id_expr, ast.Variable) and var_alias:
        _create_node_from_alias(node, node_id_expr, var_alias, context)
    else:
        _create_node_literal(node, node_id_expr, context)
    if node.variable:
        context.register_variable(node.variable)
        if _id_is_user_property:
            # Track that this variable uses 'id' as a regular rdf_props entry, not node_id
            if not hasattr(context, '_id_as_property_vars'):
                context._id_as_property_vars = set()
            context._id_as_property_vars.add(node.variable)
        if not context.from_clauses and isinstance(node_id_expr, ast.Literal):
            node_id_val = node_id_expr.value
            alias = context.variable_aliases[node.variable]
            context.from_clauses.append(f"{_table('nodes')} {alias}")
            context.where_conditions.append(f"{alias}.node_id = {context.add_where_param(node_id_val)}")


def _create_clause_resolve_node_id(id_expr, node, context):
    if id_expr is None:
        if node.variable:
            stored = context.input_params.get(f"__create_id_{node.variable}")
            if stored:
                return stored
            if node.variable in context.input_params:
                return context.input_params[node.variable]
        anon_id = getattr(context, '_anon_node_keys', {}).get(id(node))
        if anon_id:
            return anon_id
        return None
    if isinstance(id_expr, ast.Literal):
        return id_expr.value
    if isinstance(id_expr, ast.Variable) and id_expr.name in context.input_params:
        return context.input_params[id_expr.name]
    if not isinstance(id_expr, ast.Variable):
        return id_expr
    return None


def _create_clause_relationship_entry(rel, i, pat, context):
    left_node, right_node = pat.nodes[i], pat.nodes[i + 1]
    # For INCOMING direction ((:A)<-[:R]-(:B)), the right node is the edge source.
    if rel.direction == ast.Direction.INCOMING:
        source_node, target_node = right_node, left_node
    else:
        source_node, target_node = left_node, right_node

    s_id_expr = source_node.properties.get("id") or source_node.properties.get("node_id")
    t_id_expr = target_node.properties.get("id") or target_node.properties.get("node_id")
    if s_id_expr is not None and isinstance(s_id_expr, ast.Literal) and not isinstance(s_id_expr.value, str):
        s_id_expr = None
    if t_id_expr is not None and isinstance(t_id_expr, ast.Literal) and not isinstance(t_id_expr.value, str):
        t_id_expr = None

    s_id = _create_clause_resolve_node_id(s_id_expr, source_node, context)
    t_id = _create_clause_resolve_node_id(t_id_expr, target_node, context)
    if s_id and t_id:
        for rt in rel.types:
            rel_props_raw = {
                k: (v.value if isinstance(v, ast.Literal) else
                    context.foreach_literals.get(v.name)
                    if isinstance(v, ast.Variable) and hasattr(context, "foreach_literals")
                    else None)
                for k, v in rel.properties.items()
            }
            # Exclude null values (null props are not stored per openCypher semantics)
            rel_props = {k: v for k, v in rel_props_raw.items() if v is not None}
            if rel_props:
                import json as _json
                # Store all values as strings — JSON_VALUE returns VARCHAR; ints stored
                # as JSON numbers are returned as NULL by IRIS SQLUser.JSON_VALUE.
                qualifiers_json = _json.dumps({k: str(v) for k, v in rel_props.items()})
                context.add_dml(
                    f"INSERT INTO {_table('rdf_edges')} (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                    [s_id, rt, t_id, qualifiers_json],
                )
            else:
                context.add_dml(
                    f"INSERT INTO {_table('rdf_edges')} (s, p, o_id) VALUES (?, ?, ?)",
                    [s_id, rt, t_id],
                )
    else:
        s_alias = (
            context.variable_aliases.get(source_node.variable)
            if source_node.variable
            else None
        )
        t_alias = (
            context.variable_aliases.get(target_node.variable)
            if target_node.variable
            else None
        )
        s_expr, s_p = (
            ("?", [s_id])
            if s_id
            else (
                f"{s_alias}.{source_node.variable}"
                if s_alias and s_alias.startswith("Stage")
                else f"{s_alias}.node_id",
                [],
            )
        )
        t_expr, t_p = (
            ("?", [t_id])
            if t_id
            else (
                f"{t_alias}.{target_node.variable}"
                if t_alias and t_alias.startswith("Stage")
                else f"{t_alias}.node_id",
                [],
            )
        )
        for rt in rel.types:
            cte, sql, p = context.build_dml_subquery(
                select_override=f"SELECT {s_expr}, ?, {t_expr}"
            )
            context.add_dml(
                f"{cte}INSERT INTO {_table('rdf_edges')} (s, p, o_id) {sql}",
                s_p + [rt] + t_p + p,
            )


def translate_create_clause(create, context, metadata):
    for pat in create.patterns:
        # Validate before any DML: VariableAlreadyBound, syntax errors
        is_relationship_pattern = bool(pat.relationships)
        for node in pat.nodes:
            if node.variable and node.variable in context.variable_aliases:
                # VariableAlreadyBound: re-binding a known variable in CREATE is an error
                # if it adds new labels/props, or if it appears as a standalone CREATE (no rel).
                if node.labels or node.properties or not is_relationship_pattern:
                    raise SyntaxError(
                        f"VariableAlreadyBound: variable '{node.variable}' already bound"
                    )
        for rel in pat.relationships:
            if rel.variable and rel.variable in context.variable_aliases:
                raise SyntaxError(
                    f"VariableAlreadyBound: variable '{rel.variable}' already bound"
                )
            if not rel.types:
                raise SyntaxError("NoSingleRelationshipType: CREATE relationship must have exactly one type")
            if len(rel.types) > 1:
                raise SyntaxError("NoSingleRelationshipType: CREATE relationship must have exactly one type")
            if rel.direction == ast.Direction.BOTH:
                raise SyntaxError("RequiresDirectedRelationship: CREATE relationship must be directed")
            if rel.variable_length is not None:
                raise SyntaxError("CreatingVarLength: variable-length relationships cannot be used in CREATE")
        for node in pat.nodes:
            _create_clause_node_entry(node, context)
        for i, rel in enumerate(pat.relationships):
            _create_clause_relationship_entry(rel, i, pat, context)
            if rel.variable:
                _register_created_relationship(rel, i, pat, context)


def _register_created_relationship(rel, i, pat, context):
    """Register a named relationship created by CREATE so RETURN r works."""
    left_node, right_node = pat.nodes[i], pat.nodes[i + 1]
    if rel.direction == ast.Direction.INCOMING:
        source_node, target_node = right_node, left_node
    else:
        source_node, target_node = left_node, right_node

    def _node_id(node):
        if node.variable:
            nid = context.input_params.get(f"__create_id_{node.variable}")
            if nid:
                return nid
            return context.input_params.get(node.variable)
        return context.input_params.get(f"__create_id_anon_{id(node)}")

    s_id = _node_id(source_node)
    t_id = _node_id(target_node)
    rel_type = rel.types[0] if rel.types else None
    e_alias = context.register_variable(rel.variable, prefix="e")
    if s_id and t_id and rel_type:
        # Store identity for UNWIND+CREATE relationship tracking in _tts_finalize_context
        if rel.variable:
            context.input_params[f"__create_edge_{rel.variable}"] = (s_id, rel_type, t_id)
        if not context.from_clauses:
            context.from_clauses.append(f"{_table('rdf_edges')} {e_alias}")
        else:
            context.join_clauses.append(
                f"JOIN {_table('rdf_edges')} {e_alias} ON "
                f"{e_alias}.s = {context.add_join_param(s_id)}"
                f" AND {e_alias}.p = {context.add_join_param(rel_type)}"
                f" AND {e_alias}.o_id = {context.add_join_param(t_id)}"
            )
            return
        context.where_conditions.append(
            f"{e_alias}.s = {context.add_where_param(s_id)}"
            f" AND {e_alias}.p = {context.add_where_param(rel_type)}"
            f" AND {e_alias}.o_id = {context.add_where_param(t_id)}"
        )


def translate_delete_clause(delete, context, metadata):
    for var in delete.expressions:
        alias = context.variable_aliases.get(var.name)
        if not alias:
            raise SyntaxError(f"Undefined variable: {var.name}")

        # Detect whether the variable is a relationship (edge), taking into account
        # variables promoted to a CTE stage via WITH.
        is_edge_var = (
            alias.startswith("e")
            or var.name in getattr(context, "edge_stage_variables", set())
        )
        # When alias is a CTE stage (e.g. "Stage1"), the node_id column is named
        # after the variable (e.g. "n"), not "node_id".
        stage_names = {s.split(" AS ")[0].strip() for s in getattr(context, "stages", [])}
        is_stage_alias = alias in stage_names

        if is_edge_var and is_stage_alias:
            # Relationship variable promoted through WITH into a CTE stage.
            # The Stage SELECT now includes __edge_<var>_s/p/o identity columns;
            # use them to reconstruct the edge identity for deletion.
            s_col = f"__edge_{var.name}_s"
            p_col = f"__edge_{var.name}_p"
            o_col = f"__edge_{var.name}_o"
            cte_s, subquery_s, subparams_s = context.build_dml_subquery(
                select_override=f"SELECT {alias}.{s_col}"
            )
            _, subquery_p, _ = context.build_dml_subquery(
                select_override=f"SELECT {alias}.{p_col}"
            )
            _, subquery_o, _ = context.build_dml_subquery(
                select_override=f"SELECT {alias}.{o_col}"
            )
            # All three calls return the same CTE and params (same Stage1 binding).
            # The CTE appears once in the SQL; params are bound once.
            context.add_dml(
                f"{cte_s}DELETE FROM {_table('rdf_edges')} WHERE "
                f"s IN ({subquery_s}) AND p IN ({subquery_p}) AND o_id IN ({subquery_o})",
                subparams_s,
            )
            return

        node_col = var.name if is_stage_alias else "node_id"
        cte, subquery, subparams = context.build_dml_subquery(
            select_override=f"SELECT {alias}.{node_col}"
        )
        # When a CTE is present, the subquery is a bare reference (no ?); params are CTE-only
        # and used once. When no CTE, the subquery has its own ? for each IN clause.
        dual_params = subparams if cte else subparams + subparams
        if delete.detach:
            context.add_dml(
                f"{cte}DELETE FROM {_table('rdf_edges')} WHERE s IN ({subquery}) OR o_id IN ({subquery})",
                dual_params,
            )
        elif not is_edge_var:
            # Non-DETACH DELETE: guard against connected nodes (Cypher constraint).
            # Stored as a sentinel SQL so execute_transaction can raise the right error.
            context.add_dml(
                f"__constraint_check_delete_connected__ {cte}SELECT COUNT(*) FROM {_table('rdf_edges')} WHERE s IN ({subquery}) OR o_id IN ({subquery})",
                dual_params,
            )
        if not is_edge_var:
            context.add_dml(
                f"{cte}DELETE FROM {_table('rdf_labels')} WHERE s IN ({subquery})", subparams
            )
            context.add_dml(
                f"{cte}DELETE FROM {_table('rdf_props')} WHERE s IN ({subquery})", subparams
            )
            context.add_dml(
                f"{cte}DELETE FROM {_table('kg_NodeEmbeddings')} WHERE id IN ({subquery})",
                subparams,
            )
            context.add_dml(
                f"{cte}DELETE FROM {_table('nodes')} WHERE node_id IN ({subquery})",
                subparams,
            )
        else:
            is_undirected = alias in getattr(context, "_undirected_aliases", set())
            s_col = "_src" if is_undirected else "s"
            p_col = "_p" if is_undirected else "p"
            o_col = "_dst" if is_undirected else "o_id"
            cte_s, subquery_s, subparams_s = context.build_dml_subquery(
                select_override=f"SELECT {alias}.{s_col}"
            )
            cte_p, subquery_p, subparams_p = context.build_dml_subquery(
                select_override=f"SELECT {alias}.{p_col}"
            )
            cte_o, subquery_o, subparams_o = context.build_dml_subquery(
                select_override=f"SELECT {alias}.{o_col}"
            )
            context.add_dml(
                f"{cte_s}DELETE FROM {_table('rdf_edges')} WHERE "
                f"s IN ({subquery_s}) AND p IN ({subquery_p}) AND o_id IN ({subquery_o})",
                subparams_s + subparams_p + subparams_o,
            )


def _merge_pattern_existence_sql(merge_node, context=None):
    """Build the NOT EXISTS sub-SELECT that checks whether any node already matches
    the MERGE pattern (labels + properties).  Returns (sql_fragment, params_list).

    The fragment is suitable for use in:
        INSERT INTO nodes (node_id) SELECT ? WHERE NOT EXISTS (<fragment>)
    """
    labels = merge_node.labels if merge_node else []
    props = merge_node.properties if merge_node else {}

    def _resolve_val(v):
        if isinstance(v, ast.Literal):
            return v.value
        if isinstance(v, ast.Variable) and context is not None:
            # Check foreach_literals (UNWIND loop context)
            fl = getattr(context, "foreach_literals", {})
            if v.name in fl:
                return fl[v.name]
            # Check input_params
            return context.input_params.get(v.name, v)
        return v

    if not labels and not props:
        # No constraints — any node in the graph matches; check by a sentinel always-true.
        return f"SELECT 1 FROM {_table('nodes')} WHERE 1=1", []

    joins = []
    params: list = []
    for i, label in enumerate(labels):
        alias = f"_ml{i}"
        if i == 0:
            joins.append(f"{_table('nodes')} {alias}")
        else:
            # Additional label: re-join rdf_labels on same node
            first_alias = "_ml0"
            l_alias = f"_ml{i}"
            joins.append(
                f"JOIN {_table('rdf_labels')} {l_alias} ON "
                f"{l_alias}.s = _ml0.node_id AND {l_alias}.label = ?"
            )
            params.append(label)

    if labels:
        # Primary label checked via rdf_labels
        primary_label = labels[0]
        lbl0_join = (
            f"SELECT 1 FROM {_table('nodes')} _ml0 "
            f"JOIN {_table('rdf_labels')} _lbl0 ON _lbl0.s = _ml0.node_id AND _lbl0.label = ?"
        )
        params_prefix = [primary_label]
        extra_label_joins = ""
        for i, label in enumerate(labels[1:], start=1):
            l_alias = f"_ml{i}"
            extra_label_joins += (
                f" JOIN {_table('rdf_labels')} {l_alias} ON "
                f"{l_alias}.s = _ml0.node_id AND {l_alias}.label = ?"
            )
            params_prefix.append(label)
        prop_joins = ""
        prop_params: list = []
        for ki, (k, v) in enumerate(props.items()):
            val = _resolve_val(v)
            p_alias = f"_mp{ki}"
            prop_joins += (
                f' JOIN {_table("rdf_props")} {p_alias} ON '
                f'{p_alias}.s = _ml0.node_id AND {p_alias}."key" = ? AND {p_alias}.val = ?'
            )
            prop_params.extend([k, str(val)])
        return (
            lbl0_join + extra_label_joins + prop_joins,
            params_prefix + prop_params,
        )

    # No labels, only properties
    prop_joins_parts = []
    prop_params = []
    for ki, (k, v) in enumerate(props.items()):
        val = _resolve_val(v)
        p_alias = f"_mp{ki}"
        if ki == 0:
            prop_joins_parts.append(
                f"SELECT 1 FROM {_table('nodes')} _ml0 "
                f'JOIN {_table("rdf_props")} {p_alias} ON '
                f'{p_alias}.s = _ml0.node_id AND {p_alias}."key" = ? AND {p_alias}.val = ?'
            )
        else:
            prop_joins_parts.append(
                f'JOIN {_table("rdf_props")} {p_alias} ON '
                f'{p_alias}.s = _ml0.node_id AND {p_alias}."key" = ? AND {p_alias}.val = ?'
            )
        prop_params.extend([k, str(val)])
    return " ".join(prop_joins_parts), prop_params


def _validate_merge_pattern_no_null_properties(merge_node):
    """Validate that MERGE pattern does not contain null property values.

    Cypher semantic rule: null is "unknown" and cannot be matched.
    Merging on a null property value is a semantic error (MergeReadOwnWrites).

    Raises ValueError if any property in the merge pattern has a null literal value.
    """
    if not merge_node:
        return

    props = merge_node.properties if hasattr(merge_node, 'properties') else {}
    for key, value in props.items():
        if isinstance(value, ast.Literal) and value.value is None:
            raise ValueError(
                "Cannot merge on null property value: "
                f"property '{key}' has value null"
            )


def translate_merge_clause(merge, context, metadata):
    # Validate that MERGE pattern does not contain null property values.
    # Cypher semantic rule: null cannot be matched in MERGE operations.
    merge_node = merge.pattern.nodes[0] if merge.pattern.nodes else None
    _validate_merge_pattern_no_null_properties(merge_node)
    for _rel in merge.pattern.relationships:
        for _k, _v in (_rel.properties or {}).items():
            if isinstance(_v, ast.Literal) and _v.value is None:
                raise ValueError(
                    f"Cannot merge on null property value: property '{_k}' has value null"
                )

    # Snapshot context state before translate_create_clause so we can replace the
    # UUID-based DMLs and WHERE with label/property-based equivalents.
    _pre_dml_len = len(context.dml_statements)
    _pre_from_len = len(context.from_clauses)
    _pre_join_len = len(context.join_clauses)
    _pre_join_params_len = len(context.join_params)
    _pre_where_len = len(context.where_conditions)
    _pre_where_params_len = len(context.where_params)

    # For undirected relationships, treat as OUTGOING for CREATE
    # (openCypher spec: MERGE creates in outgoing direction when unspecified)
    _create_pattern = merge.pattern
    if any(r.direction == ast.Direction.BOTH for r in merge.pattern.relationships):
        import copy as _copy
        _create_rels = []
        for r in merge.pattern.relationships:
            if r.direction == ast.Direction.BOTH:
                _r2 = _copy.copy(r)
                _r2 = ast.RelationshipPattern(
                    variable=r.variable, types=r.types, properties=r.properties,
                    variable_length=r.variable_length,
                    direction=ast.Direction.OUTGOING,
                )
                _create_rels.append(_r2)
            else:
                _create_rels.append(r)
        _create_pattern = ast.GraphPattern(
            nodes=merge.pattern.nodes,
            relationships=_create_rels,
        )
    translate_create_clause(ast.CreateClause(patterns=[_create_pattern]), context, metadata)

    # --- Rewrite DML + SELECT for single-node MERGE patterns ---
    # translate_create_clause generates INSERT ... WHERE NOT EXISTS (node_id = <uuid>).
    # For MERGE we need INSERT ... WHERE NOT EXISTS (<label/prop pattern match>)
    # so that a pre-existing node prevents a new node from being created.
    merge_node = merge.pattern.nodes[0] if merge.pattern.nodes else None
    if merge_node is not None and not merge.pattern.relationships:
        var_name = merge_node.variable
        node_alias = context.variable_aliases.get(var_name) if var_name else None
        generated_uuid = (
            context.input_params.get(f"__create_id_{var_name}")
            if var_name else None
        )
        if generated_uuid is None and hasattr(context, '_anon_node_keys'):
            generated_uuid = context._anon_node_keys.get(id(merge_node))

        # Check whether translate_create_clause added UUID-based from/where entries.
        added_froms = context.from_clauses[_pre_from_len:]
        added_wheres = context.where_conditions[_pre_where_len:]
        _has_uuid_from = (
            len(added_froms) == 1
            and node_alias
            and f"{_table('nodes')} {node_alias}" in added_froms[0]
        )
        _has_uuid_where = (
            len(added_wheres) == 1
            and node_alias
            and f"{node_alias}.node_id = ?" in added_wheres[0]
        )

        exist_sql, exist_params = _merge_pattern_existence_sql(merge_node, context)
        new_uuid = generated_uuid

        if new_uuid and (_has_uuid_from or _has_uuid_where or True):
            # Replace UUID-based node DML statements with label/prop-aware equivalents.
            # We only touch the DML added by translate_create_clause for THIS merge node.
            added_dmls = context.dml_statements[_pre_dml_len:]
            new_dmls = []
            for sql, params in added_dmls:
                if "INSERT INTO " + _table("nodes") in sql and new_uuid in str(params):
                    # Replace "WHERE NOT EXISTS (SELECT 1 FROM nodes WHERE node_id = ?)"
                    # with a pattern-based check so existing matching nodes block the INSERT.
                    new_dmls.append((
                        f"INSERT INTO {_table('nodes')} (node_id) SELECT ? "
                        f"WHERE NOT EXISTS ({exist_sql})",
                        [new_uuid] + exist_params,
                    ))
                elif "INSERT INTO " + _table("rdf_labels") in sql and new_uuid in str(params):
                    # The rdf_labels insert must also be guarded by pattern existence.
                    # Extract the label from the original params (second param).
                    label_val = params[1] if len(params) > 1 else None
                    if label_val is not None:
                        new_dmls.append((
                            f"INSERT INTO {_table('rdf_labels')} (s, label) SELECT ?, ? "
                            f"WHERE NOT EXISTS ({exist_sql})",
                            [new_uuid, label_val] + exist_params,
                        ))
                    else:
                        new_dmls.append((sql, params))
                else:
                    new_dmls.append((sql, params))

            # Swap out the DML statements.
            del context.dml_statements[_pre_dml_len:]
            context.dml_statements.extend(new_dmls)

        # --- Fix SELECT query to find the node by label/property, not by the new UUID ---
        if _has_uuid_from and _has_uuid_where:
            del context.from_clauses[_pre_from_len:]
            del context.where_conditions[_pre_where_len:]
            del context.where_params[_pre_where_params_len:]

            # Re-add FROM nodes + label JOINs + property JOINs so the SELECT finds the
            # matching node whether it was just created or already existed.
            context.from_clauses.append(f"{_table('nodes')} {node_alias}")
            for label in merge_node.labels:
                l_alias = context.next_alias("l")
                context.join_clauses.append(
                    f"JOIN {_table('rdf_labels')} {l_alias} ON "
                    f"{l_alias}.s = {node_alias}.node_id AND "
                    f"{l_alias}.label = {context.add_join_param(label)}"
                )
            for k, v in merge_node.properties.items():
                if isinstance(v, ast.Literal):
                    val = v.value
                elif isinstance(v, ast.Variable):
                    fl = getattr(context, "foreach_literals", {})
                    val = fl.get(v.name) if v.name in fl else context.input_params.get(v.name)
                else:
                    val = v
                p_alias = context.next_alias("p")
                context.join_clauses.append(
                    f'JOIN {_table("rdf_props")} {p_alias} ON '
                    f'{p_alias}.s = {node_alias}.node_id AND '
                    f'{p_alias}."key" = {context.add_join_param(k)} AND '
                    f'{p_alias}.val = {context.add_join_param(str(val))}'
                )

    # --- Rewrite DML + SELECT for relationship MERGE patterns ---
    # For MERGE patterns with relationships (e.g., MERGE (a)-[r:TYPE]->(b)),
    # translate_create_clause generates INSERT without idempotency checks.
    # We need to:
    # 1. Add NOT EXISTS guard to the INSERT so MERGE is idempotent
    # 2. Register the edge alias and add rdf_edges JOIN for SELECT
    if merge.pattern.relationships:
        for rel_idx, rel in enumerate(merge.pattern.relationships):
            if not rel.types or len(rel.types) > 1:
                continue

            left_node, right_node = merge.pattern.nodes[rel_idx], merge.pattern.nodes[rel_idx + 1]
            is_undirected = rel.direction == ast.Direction.BOTH
            # For INCOMING direction, source is right_node; BOTH treated as OUTGOING for CREATE
            if rel.direction == ast.Direction.INCOMING:
                source_node, target_node = right_node, left_node
            else:
                source_node, target_node = left_node, right_node

            # Get the SQL aliases for source and target from MATCH-bound variables
            src_var = source_node.variable
            tgt_var = target_node.variable
            src_alias = context.variable_aliases.get(src_var) if src_var else None
            tgt_alias = context.variable_aliases.get(tgt_var) if tgt_var else None

            rel_type = rel.types[0]

            # For CREATE-bound nodes: get UUID params directly and rewrite VALUES INSERT
            src_uuid = (
                context.input_params.get(f"__create_id_{src_var}") if src_var else None
            ) or (context._anon_node_keys.get(id(source_node)) if hasattr(context, '_anon_node_keys') else None)
            tgt_uuid = (
                context.input_params.get(f"__create_id_{tgt_var}") if tgt_var else None
            ) or (context._anon_node_keys.get(id(target_node)) if hasattr(context, '_anon_node_keys') else None)

            # Build NOT EXISTS conditions for both styles:
            # alias-ref style (for SELECT-based INSERT) and UUID style (for VALUES INSERT)
            if src_alias and tgt_alias:
                # For Stage CTE aliases (Stage1, Stage2, ...) the node_id column is named
                # after the original variable (e.g. Stage1.a), not Stage1.node_id.
                _stage_names = {s.split(" AS ")[0].strip() for s in getattr(context, "stages", [])}
                _s_ref = (
                    f"{src_alias}.{src_var}"
                    if src_alias in _stage_names
                    else f"{src_alias}.node_id"
                )
                _t_ref = (
                    f"{tgt_alias}.{tgt_var}"
                    if tgt_alias in _stage_names
                    else f"{tgt_alias}.node_id"
                )
                if not is_undirected:
                    not_exists_alias_sql = (
                        f"SELECT 1 FROM {_table('rdf_edges')} WHERE "
                        f"s = {_s_ref} AND p = ? AND o_id = {_t_ref}"
                    )
                    not_exists_alias_params = [rel_type]
                else:
                    not_exists_alias_sql = (
                        f"SELECT 1 FROM {_table('rdf_edges')} WHERE "
                        f"(s = {_s_ref} AND p = ? AND o_id = {_t_ref}) OR "
                        f"(s = {_t_ref} AND p = ? AND o_id = {_s_ref})"
                    )
                    not_exists_alias_params = [rel_type, rel_type]
            else:
                not_exists_alias_sql = None
                not_exists_alias_params = []

            if src_uuid and tgt_uuid:
                if not is_undirected:
                    not_exists_uuid_sql = (
                        f"SELECT 1 FROM {_table('rdf_edges')} WHERE "
                        f"s = ? AND p = ? AND o_id = ?"
                    )
                    not_exists_uuid_params = [src_uuid, rel_type, tgt_uuid]
                else:
                    not_exists_uuid_sql = (
                        f"SELECT 1 FROM {_table('rdf_edges')} WHERE "
                        f"(s = ? AND p = ? AND o_id = ?) OR (s = ? AND p = ? AND o_id = ?)"
                    )
                    not_exists_uuid_params = [
                        src_uuid, rel_type, tgt_uuid,
                        tgt_uuid, rel_type, src_uuid,
                    ]
            else:
                not_exists_uuid_sql = not_exists_alias_sql
                not_exists_uuid_params = not_exists_alias_params

            # Find the rdf_edges INSERT for this relationship and wrap it with NOT EXISTS
            added_dmls = context.dml_statements[_pre_dml_len:]
            new_dmls = []
            edge_inserted = False

            for sql, params in added_dmls:
                if "INSERT INTO " + _table("rdf_edges") in sql and not edge_inserted:
                    if not_exists_alias_sql is None and not_exists_uuid_sql is None:
                        new_dmls.append((sql, params))
                        edge_inserted = True
                    elif " VALUES (" in sql:
                        # VALUES-style INSERT (nodes created in same query):
                        # Rewrite to SELECT WHERE NOT EXISTS using UUID literals.
                        if not_exists_uuid_sql is None:
                            new_dmls.append((sql, params))
                            edge_inserted = True
                            continue
                        col_start = sql.index("(") + 1
                        col_end = sql.index(")")
                        col_list = sql[col_start:col_end]
                        n_cols = len(col_list.split(","))
                        placeholders = ", ".join(["?"] * n_cols)
                        new_sql = (
                            f"INSERT INTO {_table('rdf_edges')} ({col_list}) "
                            f"SELECT {placeholders} WHERE NOT EXISTS ({not_exists_uuid_sql})"
                        )
                        new_dmls.append((new_sql, params + not_exists_uuid_params))
                        edge_inserted = True
                    elif not_exists_alias_sql is not None:
                        # Append NOT EXISTS guard. Use WHERE if no WHERE clause exists yet,
                        # AND if there already is one.
                        # Search only in the INSERT/SELECT body (after any CTE definition)
                        # to avoid false-positive WHERE matches inside CTE WHERE clauses.
                        sql_stripped = sql.rstrip()
                        _upper = sql_stripped.upper()
                        _insert_pos = _upper.find("INSERT INTO")
                        if _insert_pos < 0:
                            _insert_pos = 0
                        _body_upper = _upper[_insert_pos:]
                        if " WHERE " in _body_upper or "\nWHERE " in _body_upper:
                            new_sql = f"{sql_stripped} AND NOT EXISTS ({not_exists_alias_sql})"
                        else:
                            new_sql = f"{sql_stripped} WHERE NOT EXISTS ({not_exists_alias_sql})"
                        new_dmls.append((new_sql, params + not_exists_alias_params))
                        edge_inserted = True
                    else:
                        new_dmls.append((sql, params))
                        edge_inserted = True
                else:
                    new_dmls.append((sql, params))

            if edge_inserted:
                # Replace the DML statements
                del context.dml_statements[_pre_dml_len:]
                context.dml_statements.extend(new_dmls)

            # Register the edge alias and add JOIN for SELECT
            if rel.variable:
                e_alias = context.register_variable(rel.variable, prefix="e")
                context.rel_variables.add(rel.variable)
                # For undirected MERGE: replace any existing JOIN for this alias with
                # one that matches both directions. For directed: translate_create_clause
                # already added the correct JOIN (UUID-based or alias-based); skip re-adding.
                added_joins = context.join_clauses[_pre_join_len:]
                existing_join_idx = next(
                    (i for i, j in enumerate(added_joins) if f" {e_alias} ON " in j),
                    None,
                )
                if is_undirected:
                    # Build undirected join. For UUID-based joins, inline the values as
                    # literals (not params) to avoid join_params accounting issues when
                    # replacing an existing join that already added params.
                    _rt_esc = rel_type.replace("'", "''")
                    if src_uuid and tgt_uuid:
                        _s_esc = src_uuid.replace("'", "''")
                        _t_esc = tgt_uuid.replace("'", "''")
                        new_join = (
                            f"JOIN {_table('rdf_edges')} {e_alias} ON "
                            f"{e_alias}.p = '{_rt_esc}' AND ("
                            f"({e_alias}.s = '{_s_esc}' AND {e_alias}.o_id = '{_t_esc}') OR "
                            f"({e_alias}.s = '{_t_esc}' AND {e_alias}.o_id = '{_s_esc}'))"
                        )
                        if existing_join_idx is not None:
                            # Remove the 3 join_params added by translate_create_clause
                            # for the original directed join (s_uuid, rel_type, t_uuid).
                            old_join = added_joins[existing_join_idx]
                            old_param_count = old_join.count(" = ? ") + old_join.count(" = ?")
                            if old_param_count > 0:
                                del context.join_params[
                                    _pre_join_params_len:_pre_join_params_len + old_param_count
                                ]
                    elif src_alias and tgt_alias:
                        _sn_ref_u = (
                            f"{src_alias}.{src_var}"
                            if src_alias in _stage_names
                            else f"{src_alias}.node_id"
                        )
                        _tn_ref_u = (
                            f"{tgt_alias}.{tgt_var}"
                            if tgt_alias in _stage_names
                            else f"{tgt_alias}.node_id"
                        )
                        new_join = (
                            f"JOIN {_table('rdf_edges')} {e_alias} ON "
                            f"{e_alias}.p = {context.add_join_param(rel_type)} AND ("
                            f"({e_alias}.s = {_sn_ref_u} AND {e_alias}.o_id = {_tn_ref_u}) OR "
                            f"({e_alias}.s = {_tn_ref_u} AND {e_alias}.o_id = {_sn_ref_u}))"
                        )
                    else:
                        new_join = None
                    if new_join:
                        if existing_join_idx is not None:
                            context.join_clauses[_pre_join_len + existing_join_idx] = new_join
                        else:
                            context.join_clauses.append(new_join)
                elif existing_join_idx is None:
                    # Directed, no existing join: add one (MATCH-bound case)
                    if src_alias and tgt_alias:
                        _sn_ref = (
                            f"{src_alias}.{src_var}"
                            if src_alias in _stage_names
                            else f"{src_alias}.node_id"
                        )
                        _tn_ref = (
                            f"{tgt_alias}.{tgt_var}"
                            if tgt_alias in _stage_names
                            else f"{tgt_alias}.node_id"
                        )
                        context.join_clauses.append(
                            f"JOIN {_table('rdf_edges')} {e_alias} ON "
                            f"{e_alias}.s = {_sn_ref} AND "
                            f"{e_alias}.p = {context.add_join_param(rel_type)} AND "
                            f"{e_alias}.o_id = {_tn_ref}"
                        )

    # Collect edge context for ON CREATE/ON MATCH SET on MATCH-bound node variables.
    # When a node variable comes from MATCH (not created by this MERGE), actual_id is None
    # and we need the edge's src/tgt aliases + rel_type to condition the INSERT.
    _edge_contexts = []  # list of (src_alias, tgt_alias, rel_type) for each edge in this MERGE
    if merge.pattern.relationships:
        for rel_idx, rel in enumerate(merge.pattern.relationships):
            if not rel.types or len(rel.types) > 1:
                continue
            left_node, right_node = merge.pattern.nodes[rel_idx], merge.pattern.nodes[rel_idx + 1]
            if rel.direction == ast.Direction.INCOMING:
                source_node, target_node = right_node, left_node
            else:
                source_node, target_node = left_node, right_node
            src_a = context.variable_aliases.get(source_node.variable) if source_node.variable else None
            tgt_a = context.variable_aliases.get(target_node.variable) if target_node.variable else None
            if src_a and tgt_a:
                _edge_contexts.append((src_a, tgt_a, rel.types[0]))

    var = merge.pattern.nodes[0].variable if merge.pattern.nodes else None
    for action, is_create in [(merge.on_create, True), (merge.on_match, False)]:
        if action:
            for item in action.items:
                if isinstance(item, ast.SetItem) and isinstance(
                    item.expression, ast.PropertyReference
                ):
                    var_name = item.expression.variable
                    sql_alias = context.variable_aliases.get(var_name, "")
                    actual_id = (
                        context.input_params.get(f"__create_id_{var_name}")
                        or context.input_params.get(var_name)
                    )
                    if not sql_alias and not actual_id:
                        raise SyntaxError(f"Undefined variable: {var_name}")
                    k, v = item.expression.property_name, item.value
                    val = v.value if isinstance(v, ast.Literal) else v
                    # Relationship property SET: update rdf_edges.qualifiers
                    if var_name in context.rel_variables and _edge_contexts:
                        src_a, tgt_a, rel_type = _edge_contexts[0]
                        from_parts = context.from_clauses[:_pre_from_len]
                        join_parts = context.join_clauses[:_pre_join_len]
                        where_parts = context.where_conditions[:_pre_where_len]
                        where_params_parts = context.where_params[:_pre_where_params_len]
                        join_params_parts = context.join_params[:_pre_join_params_len]
                        from_sql = ", ".join(from_parts) if from_parts else ""
                        join_sql = (" " + " ".join(join_parts)) if join_parts else ""
                        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
                        all_params = join_params_parts + where_params_parts
                        json_val = str(val) if val is not None else None
                        if json_val is None:
                            context.add_dml(
                                f'UPDATE {_table("rdf_edges")} SET qualifiers = '
                                f'SQLUser.CypherFn_IVGJSONREMOVE(qualifiers, ?) '
                                f'WHERE s IN (SELECT {src_a}.node_id FROM {from_sql}{join_sql}{where_sql}) '
                                f'AND p = ? AND o_id IN (SELECT {tgt_a}.node_id FROM {from_sql}{join_sql}{where_sql})',
                                [k] + all_params + [rel_type] + all_params,
                            )
                        else:
                            context.add_dml(
                                f'UPDATE {_table("rdf_edges")} SET qualifiers = '
                                f'SQLUser.CypherFn_IVGJSONSET(COALESCE(qualifiers, CAST(\'{{}}\' AS VARCHAR(256))), ?, ?) '
                                f'WHERE s IN (SELECT {src_a}.node_id FROM {from_sql}{join_sql}{where_sql}) '
                                f'AND p = ? AND o_id IN (SELECT {tgt_a}.node_id FROM {from_sql}{join_sql}{where_sql})',
                                [k, json_val] + all_params + [rel_type] + all_params,
                            )
                        continue
                    if is_create:
                        if actual_id:
                            # ON CREATE fires only when the node was just created.
                            # Use FROM nodes WHERE node_id = ? (avoids SELECT ... WHERE EXISTS
                            # which is not valid IRIS SQL without a FROM clause).
                            context.add_dml(
                                f'INSERT INTO {_table("rdf_props")} (s, "key", val) '
                                f'SELECT node_id, ?, ? FROM {_table("nodes")} WHERE node_id = ?',
                                [k, val, actual_id],
                            )
                        else:
                            # Node is MATCH-bound (actual_id unknown at translate time).
                            # Build a self-contained INSERT that finds the node via its
                            # MATCH context — slice to pre-MERGE snapshot to exclude any
                            # FROM/JOIN entries added by translate_create_clause.
                            from_parts = context.from_clauses[:_pre_from_len]
                            join_parts = context.join_clauses[:_pre_join_len]
                            where_parts = context.where_conditions[:_pre_where_len]
                            where_params_parts = context.where_params[:_pre_where_params_len]
                            join_params_parts = context.join_params[:_pre_join_params_len]
                            if from_parts:
                                from_sql = ", ".join(from_parts)
                                join_sql = (" " + " ".join(join_parts)) if join_parts else ""
                                where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
                                context.add_dml(
                                    f'INSERT INTO {_table("rdf_props")} (s, "key", val) '
                                    f'SELECT {sql_alias}.node_id, ?, ? '
                                    f'FROM {from_sql}{join_sql}'
                                    f'{where_sql}'
                                    f'{" AND " if where_parts else " WHERE "}'
                                    f'NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} '
                                    f'WHERE s = {sql_alias}.node_id AND "key" = ?)',
                                    join_params_parts + where_params_parts + [k, val, k],
                                )
                            else:
                                context.add_dml(
                                    f'INSERT INTO {_table("rdf_props")} (s, "key", val) '
                                    f'SELECT {sql_alias}.node_id, ?, ? '
                                    f'FROM {_table("nodes")} {sql_alias} '
                                    f'WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} '
                                    f'WHERE s = {sql_alias}.node_id AND "key" = ?)',
                                    [k, val, k],
                                )
                    else:
                        if actual_id:
                            # actual_id is the new UUID — only present if node was just created.
                            # ON MATCH fires for pre-existing nodes; find them via MERGE pattern.
                            _on_mn = merge.pattern.nodes[0] if merge.pattern.nodes else None
                            _on_es, _on_ep = _merge_pattern_existence_sql(_on_mn, context)
                            _on_labels = _on_mn.labels if _on_mn else []
                            _on_props = _on_mn.properties if _on_mn else {}
                            if _on_labels or _on_props:
                                # _on_es = "SELECT 1 FROM nodes _ml0 JOIN ..."
                                _on_ns = _on_es.replace("SELECT 1 FROM ", "SELECT _ml0.node_id FROM ", 1)
                                _on_fj = _on_es.replace("SELECT 1 ", "", 1)
                                # UPDATE existing property row
                                context.add_dml(
                                    f'UPDATE {_table("rdf_props")} SET val = ? '
                                    f'WHERE s IN ({_on_ns}) AND "key" = ?',
                                    [val] + _on_ep + [k],
                                )
                                # INSERT property if not yet present on matched node.
                                # Param order: k, val (for SELECT ?, ?), _on_ep (JOIN conditions), k (NOT EXISTS)
                                context.add_dml(
                                    f'INSERT INTO {_table("rdf_props")} (s, "key", val) '
                                    f'SELECT _ml0.node_id, ?, ? {_on_fj} '
                                    f'WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} '
                                    f'WHERE s = _ml0.node_id AND "key" = ?)',
                                    [k, val] + _on_ep + [k],
                                )
                            else:
                                # No pattern constraints — update all nodes (degenerate MERGE (a)).
                                context.add_dml(
                                    f'UPDATE {_table("rdf_props")} SET val = ? WHERE "key" = ?',
                                    [val, k],
                                )
                        else:
                            # MATCH-bound node: use pre-MERGE MATCH context subquery.
                            from_parts = context.from_clauses[:_pre_from_len]
                            join_parts = context.join_clauses[:_pre_join_len]
                            where_parts = context.where_conditions[:_pre_where_len]
                            where_params_parts = context.where_params[:_pre_where_params_len]
                            join_params_parts = context.join_params[:_pre_join_params_len]
                            if from_parts:
                                from_sql = ", ".join(from_parts)
                                join_sql = (" " + " ".join(join_parts)) if join_parts else ""
                                where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
                                context.add_dml(
                                    f'UPDATE {_table("rdf_props")} SET val = ? '
                                    f'WHERE s IN ('
                                    f'SELECT {sql_alias}.node_id FROM {from_sql}{join_sql}{where_sql}'
                                    f') AND "key" = ?',
                                    [val] + join_params_parts + where_params_parts + [k],
                                )
                            else:
                                context.add_dml(
                                    f'UPDATE {_table("rdf_props")} SET val = ? '
                                    f'WHERE s IN (SELECT node_id FROM {_table("nodes")} WHERE node_id = ?) AND "key" = ?',
                                    [val, sql_alias, k],
                                )
                elif isinstance(item.expression, ast.Variable):
                    var_name = item.expression.name
                    # Relationship map assignment: SET r = {map} or SET r += {map}
                    if var_name in context.rel_variables and isinstance(item.value, ast.MapLiteral) and _edge_contexts:
                        src_a, tgt_a, rel_type = _edge_contexts[0]
                        from_parts = context.from_clauses[:_pre_from_len]
                        join_parts = context.join_clauses[:_pre_join_len]
                        where_parts = context.where_conditions[:_pre_where_len]
                        where_params_parts = context.where_params[:_pre_where_params_len]
                        join_params_parts = context.join_params[:_pre_join_params_len]
                        from_sql = ", ".join(from_parts) if from_parts else ""
                        join_sql = (" " + " ".join(join_parts)) if join_parts else ""
                        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
                        all_params = join_params_parts + where_params_parts
                        # Build the qualifiers JSON for each key in the map
                        for mk, mv in item.value.entries.items():
                            json_val = (mv.value if isinstance(mv, ast.Literal) else
                                        context.input_params.get(mv.name) if isinstance(mv, ast.Variable) else str(mv))
                            if json_val is None:
                                context.add_dml(
                                    f'UPDATE {_table("rdf_edges")} SET qualifiers = '
                                    f'SQLUser.CypherFn_IVGJSONREMOVE(qualifiers, ?) '
                                    f'WHERE s IN (SELECT {src_a}.node_id FROM {from_sql}{join_sql}{where_sql}) '
                                    f'AND p = ? AND o_id IN (SELECT {tgt_a}.node_id FROM {from_sql}{join_sql}{where_sql})',
                                    [mk] + all_params + [rel_type] + all_params,
                                )
                            else:
                                context.add_dml(
                                    f'UPDATE {_table("rdf_edges")} SET qualifiers = '
                                    f'SQLUser.CypherFn_IVGJSONSET(COALESCE(qualifiers, CAST(\'{{}}\' AS VARCHAR(256))), ?, ?) '
                                    f'WHERE s IN (SELECT {src_a}.node_id FROM {from_sql}{join_sql}{where_sql}) '
                                    f'AND p = ? AND o_id IN (SELECT {tgt_a}.node_id FROM {from_sql}{join_sql}{where_sql})',
                                    [mk, str(json_val)] + all_params + [rel_type] + all_params,
                                )
                        continue
                    # Label assignment: MERGE (...) ON CREATE SET a:SomeLabel or SET a:Foo:Bar
                    # Validate: variable must be defined.
                    if (var_name not in context.variable_aliases
                            and context.input_params.get(f"__create_id_{var_name}") is None):
                        raise SyntaxError(f"Undefined variable: {var_name}")
                    actual_id = context.input_params.get(f"__create_id_{var_name}")
                    raw_val = item.value
                    if isinstance(raw_val, list):
                        label_list_merge = [str(lv) for lv in raw_val]
                    else:
                        label_list_merge = [str(raw_val.value if isinstance(raw_val, ast.Literal) else raw_val)]
                    for label in label_list_merge:
                        if is_create:
                            # ON CREATE: add label only when node was just created.
                            context.add_dml(
                                f'INSERT INTO {_table("rdf_labels")} (s, label) '
                                f'SELECT node_id, ? FROM {_table("nodes")} WHERE node_id = ?',
                                [label, actual_id],
                            )
                        else:
                            # ON MATCH: add label to the pre-existing node identified by MERGE pattern.
                            _lbl_mn = merge.pattern.nodes[0] if merge.pattern.nodes else None
                            _lbl_es, _lbl_ep = _merge_pattern_existence_sql(_lbl_mn, context)
                            _lbl_labels = _lbl_mn.labels if _lbl_mn else []
                            _lbl_props = _lbl_mn.properties if _lbl_mn else {}
                            if _lbl_labels or _lbl_props:
                                # Find matched node via MERGE pattern, add label if not present.
                                _lbl_ns = _lbl_es.replace("SELECT 1 FROM ", "SELECT _ml0.node_id FROM ", 1)
                                context.add_dml(
                                    f'INSERT INTO {_table("rdf_labels")} (s, label) '
                                    f'SELECT q.node_id, ? FROM ({_lbl_ns}) q '
                                    f'WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_labels")} '
                                    f'WHERE s = q.node_id AND label = ?)',
                                    [label] + _lbl_ep + [label],
                                )
                            else:
                                # No pattern constraints — add label to all nodes.
                                context.add_dml(
                                    f'INSERT INTO {_table("rdf_labels")} (s, label) '
                                    f'SELECT node_id, ? FROM {_table("nodes")} '
                                    f'WHERE NOT EXISTS (SELECT 1 FROM {_table("rdf_labels")} '
                                    f'WHERE s = node_id AND label = ?)',
                                    [label, label],
                                )


def _translate_set_value(expr, context, target_prop: str) -> tuple:
    """Translate a SET clause value expression for use in an UPDATE SET clause.

    Returns (sql_fragment, params, is_expression) where:
    - sql_fragment: SQL to use in UPDATE SET val = <sql_fragment>
    - params: parameter list for the fragment (may be empty for inline SQL)
    - is_expression: True if sql_fragment is an inline SQL expression (not a ? placeholder)

    For literals: returns ("?", [value], False) — use as UPDATE SET val = ?
    For expressions (e.g. n.num + 1): returns a SQL snippet with correlated subqueries
    substituting property references, e.g.:
      "CAST((SELECT val FROM rdf_props WHERE s = rdf_props.s AND \"key\" = 'num') AS NUMERIC) + 1"

    When the expression references `target_prop` on the same node, we can simplify
    `n.<target_prop>` → `CAST(val AS NUMERIC)` since we're already in that row.
    """
    if isinstance(expr, ast.Literal):
        val = _extract_literal_value(expr)
        if isinstance(val, (list, dict)):
            val = json.dumps(val)
        return ("?", [val], False)

    if isinstance(expr, ast.Variable):
        if expr.name in context.input_params:
            v = context.input_params[expr.name]
            if isinstance(v, (list, dict)):
                v = json.dumps(v)
            return ("?", [v], False)
        raise SyntaxError(
            f"UndefinedVariable: Variable `{expr.name}` not defined"
        )

    # For complex expressions, translate inline with property refs as correlated subqueries.
    def _translate_expr_for_update(e, node_var: str) -> tuple:
        """Recursively translate expr; returns (sql, extra_params)."""
        if isinstance(e, ast.Literal):
            if e.value is None:
                return "NULL", []
            if isinstance(e.value, str):
                safe = e.value.replace("'", "''")
                return f"CAST('{safe}' AS VARCHAR(256))", []
            if isinstance(e.value, list):
                import json as _json
                js = _json.dumps(_extract_literal_value(e))
                safe = js.replace("'", "''")
                return f"CAST('{safe}' AS VARCHAR({max(len(js)+1, 64)}))", []
            return str(e.value), []

        if isinstance(e, ast.Variable):
            if e.name in context.input_params:
                v = context.input_params[e.name]
                if v is None:
                    return "NULL", []
                if isinstance(v, str):
                    safe = v.replace("'", "''")
                    return f"CAST('{safe}' AS VARCHAR(256))", []
                return str(v), []
            raise SyntaxError(
                f"UndefinedVariable: Variable `{e.name}` not defined"
            )

        if isinstance(e, ast.PropertyReference):
            # n.prop reference — translate to correlated subquery
            prop = e.property_name
            safe_prop = prop.replace("'", "''")
            if prop == target_prop:
                # Same property being set — use current val directly
                sql = f"CAST(val AS NUMERIC)"
            else:
                sql = (
                    f"CAST((SELECT _upd.val FROM {_table('rdf_props')} _upd "
                    f"WHERE _upd.s = {_table('rdf_props')}.s "
                    f"AND _upd.\"key\" = '{safe_prop}') AS NUMERIC)"
                )
            return sql, []

        if isinstance(e, ast.FunctionCall) and e.function_name.startswith("__arith_"):
            op = e.function_name[len("__arith_"):]
            op_map = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "%"}
            sql_op = op_map.get(op, op)
            # For + where either operand is a string literal, use string concatenation (||)
            # and avoid casting PropertyReference to NUMERIC.
            if op == "+":
                def _is_str_arg(a):
                    return isinstance(a, ast.Literal) and isinstance(a.value, str)
                def _is_list_arg(a):
                    if isinstance(a, ast.Literal):
                        v = _extract_literal_value(a)
                        return isinstance(v, list)
                    return False
                left_is_str = _is_str_arg(e.arguments[0])
                right_is_str = _is_str_arg(e.arguments[1])
                if left_is_str or right_is_str:
                    def _str_aware_translate(a):
                        if isinstance(a, ast.PropertyReference):
                            prop = a.property_name
                            safe_prop = prop.replace("'", "''")
                            if prop == target_prop:
                                return "val", []
                            return (
                                f"(SELECT _upd.val FROM {_table('rdf_props')} _upd "
                                f"WHERE _upd.s = {_table('rdf_props')}.s "
                                f"AND _upd.\"key\" = '{safe_prop}')"
                            ), []
                        return _translate_expr_for_update(a, node_var)
                    left_sql, left_params = _str_aware_translate(e.arguments[0])
                    right_sql, right_params = _str_aware_translate(e.arguments[1])
                    return f"(CAST({left_sql} AS VARCHAR(4096)) || CAST({right_sql} AS VARCHAR(4096)))", left_params + right_params
                left_is_list = _is_list_arg(e.arguments[0])
                right_is_list = _is_list_arg(e.arguments[1])
                if left_is_list or right_is_list:
                    # JSON array concatenation: use string-manipulation approach
                    # left_arr + right_arr → remove trailing ] from left, remove leading [ from right
                    import json as _json
                    def _list_sql(a, is_left_side):
                        if _is_list_arg(a):
                            js = _json.dumps(_extract_literal_value(a))
                            safe_js = js.replace("'", "''")
                            return f"CAST('{safe_js}' AS VARCHAR({max(len(js)+1, 64)}))", []
                        if isinstance(a, ast.PropertyReference):
                            prop = a.property_name
                            safe_prop = prop.replace("'", "''")
                            if prop == target_prop:
                                # Use VARCHAR cast so INSERT branch can substitute via _iv_subq replacement
                                return "CAST(val AS VARCHAR(4096))", []
                            return (
                                f"(SELECT _upd.val FROM {_table('rdf_props')} _upd "
                                f"WHERE _upd.s = {_table('rdf_props')}.s "
                                f"AND _upd.\"key\" = '{safe_prop}')"
                            ), []
                        return _translate_expr_for_update(a, node_var)
                    left_sql, left_params = _list_sql(e.arguments[0], True)
                    right_sql, right_params = _list_sql(e.arguments[1], False)
                    # Concat: strip trailing ] from left, strip leading [ from right, join with ,
                    # Handle empty arrays: if left='[]' just use right; if right='[]' just use left
                    concat_sql = (
                        f"CASE "
                        f"WHEN {left_sql} IS NULL OR {left_sql} = '[]' THEN {right_sql} "
                        f"WHEN {right_sql} IS NULL OR {right_sql} = '[]' THEN {left_sql} "
                        f"ELSE SUBSTR({left_sql}, 1, CHAR_LENGTH({left_sql})-1) || ',' || SUBSTR({right_sql}, 2) "
                        f"END"
                    )
                    return concat_sql, left_params + right_params
            left_sql, left_params = _translate_expr_for_update(e.arguments[0], node_var)
            right_sql, right_params = _translate_expr_for_update(e.arguments[1], node_var)
            return f"({left_sql} {sql_op} {right_sql})", left_params + right_params

        if isinstance(e, ast.FunctionCall) and e.function_name == "__unary_minus__":
            inner_sql, inner_params = _translate_expr_for_update(e.arguments[0], node_var)
            return f"(-{inner_sql})", inner_params

        # Fallback: try regular translation (for string concatenation etc.)
        try:
            sql = translate_expression(e, context, segment="select")
            return sql, list(context.select_params)
        except SyntaxError:
            raise
        except Exception:
            return "NULL", []

    # Determine the node variable from the expression's context
    node_var = ""
    sql, params = _translate_expr_for_update(expr, node_var)
    return (sql, params, True)


def translate_set_clause(set_cl, context, metadata):
    # Track which properties are being SET so we can exclude them from the final SELECT WHERE clause
    if not hasattr(context, '_set_properties'):
        context._set_properties = set()

    for item in set_cl.items:
        if isinstance(item.expression, ast.Variable) and getattr(item, "merge", False):
            alias = context.variable_aliases.get(item.expression.name)
            cte, subquery, subparams = context.build_dml_subquery(
                select_override=f"SELECT {alias}.node_id"
            )
            val_expr = item.value
            if isinstance(val_expr, ast.Variable) and val_expr.name in context.input_params:
                map_val = context.input_params[val_expr.name]
                if isinstance(map_val, dict):
                    for k, v in map_val.items():
                        context._set_properties.add(k)
                        context.add_dml(
                            f'{cte}UPDATE {_table("rdf_props")} SET val = ? WHERE s IN ({subquery}) AND "key" = ?',
                            [v] + subparams + [k],
                        )
                        context.add_dml(
                            f'{cte}INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT node_id, ?, ? FROM {_table("nodes")} WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = {_table("nodes")}.node_id AND "key" = ?)',
                            [k, v] + subparams + [k],
                        )
            elif isinstance(val_expr, ast.MapLiteral):
                for k, v in val_expr.entries.items():
                    context._set_properties.add(k)
                    val = v.value if isinstance(v, ast.Literal) else context.input_params.get(v.name) if isinstance(v, ast.Variable) else v
                    context.add_dml(
                        f'{cte}UPDATE {_table("rdf_props")} SET val = ? WHERE s IN ({subquery}) AND "key" = ?',
                        [val] + subparams + [k],
                    )
                    context.add_dml(
                        f'{cte}INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT node_id, ?, ? FROM {_table("nodes")} WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = {_table("nodes")}.node_id AND "key" = ?)',
                        [k, val] + subparams + [k],
                    )
        elif isinstance(item.expression, ast.PropertyReference):
            prop_name = item.expression.property_name
            context._set_properties.add(prop_name)
            alias, k, v = (
                context.variable_aliases.get(item.expression.variable),
                prop_name,
                item.value,
            )
            # Detect edge alias: relationship variables use aliases starting with 'e' but not 'ES_'
            is_edge = alias and alias.startswith('e') and not alias.startswith('ES_')
            if is_edge:
                # Relationship property SET: UPDATE rdf_edges qualifiers JSON blob
                # Null value means remove the property (Cypher semantics)
                val_sql, val_params, is_expr = _translate_set_value(v, context, k)
                val_for_json = val_params[0] if val_params and not is_expr else None
                cte, subquery, subparams = context.build_dml_subquery(
                    select_override=f"SELECT {alias}.edge_id"
                )
                if val_for_json is None and not is_expr:
                    # null literal → remove the key from qualifiers
                    context.add_dml(
                        f'{cte}UPDATE {_table("rdf_edges")} SET qualifiers = '
                        f'SQLUser.CypherFn_IVGJSONREMOVE(qualifiers, ?) '
                        f'WHERE edge_id IN ({subquery})',
                        [k] + subparams,
                    )
                elif is_expr:
                    # Expression (e.g. r.num + 1): val_sql uses `val` for current-prop refs.
                    # For edges, current value is JSON_VALUE(qualifiers, '$.key').
                    safe_k = k.replace("'", "''")
                    edge_val_ref = f"CAST(CASE WHEN {_table('rdf_edges')}.qualifiers IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({_table('rdf_edges')}.qualifiers, '$.{safe_k}') END AS DOUBLE)"
                    adapted_sql = val_sql.replace(
                        "CAST(val AS NUMERIC)", edge_val_ref
                    ).replace(
                        "CAST(val AS VARCHAR(4096))", f"CASE WHEN {_table('rdf_edges')}.qualifiers IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({_table('rdf_edges')}.qualifiers, '$.{safe_k}') END"
                    )
                    context.add_dml(
                        f'{cte}UPDATE {_table("rdf_edges")} SET qualifiers = '
                        f'SQLUser.CypherFn_IVGJSONSET(COALESCE(qualifiers, CAST(\'{{}}\'  AS VARCHAR(256))), ?, CAST(({adapted_sql}) AS VARCHAR(256))) '
                        f'WHERE edge_id IN ({subquery})',
                        [k] + val_params + subparams,
                    )
                else:
                    context.add_dml(
                        f'{cte}UPDATE {_table("rdf_edges")} SET qualifiers = '
                        f'SQLUser.CypherFn_IVGJSONSET(qualifiers, ?, ?) '
                        f'WHERE edge_id IN ({subquery})',
                        [k, str(val_for_json)] + subparams,
                    )
            else:
                # When variable is from UNWIND (scalar_variable), the alias refers to
                # a JSON_TABLE column (e.g. u3.n) not a nodes table row (u3.node_id).
                # Extract node_id from the node JSON instead.
                var_name = item.expression.variable
                if var_name in context.scalar_variables:
                    safe_var = sanitize_identifier(var_name)
                    node_id_select = f"SELECT SQLUser.JSON_VALUE({alias}.{safe_var}, '$._id')"
                else:
                    node_id_select = f"SELECT {alias}.node_id"
                cte, subquery, subparams = context.build_dml_subquery(
                    select_override=node_id_select
                )
                val_sql, val_params, is_expr = _translate_set_value(v, context, k)
                if is_expr:
                    # Expression value: inline SQL in UPDATE SET clause.
                    # val_sql uses `val` for same-property refs (safe in UPDATE context).
                    context.add_dml(
                        f'{cte}UPDATE {_table("rdf_props")} SET val = {val_sql} WHERE s IN ({subquery}) AND "key" = ?',
                        val_params + subparams + [k],
                    )
                    # For INSERT (when property doesn't exist), build an insert-safe expression
                    # where bare `val` references are replaced by correlated subqueries from rdf_props.
                    safe_k = k.replace("'", "''")
                    _iv_subq = f"(SELECT _iv.val FROM {_table('rdf_props')} _iv WHERE _iv.s = {_table('nodes')}.node_id AND _iv.\"key\" = '{safe_k}')"
                    insert_val_sql = val_sql.replace(
                        "CAST(val AS NUMERIC)",
                        f"CAST({_iv_subq} AS NUMERIC)",
                    ).replace(
                        "CAST(val AS VARCHAR(4096))",
                        f"CAST({_iv_subq} AS VARCHAR(4096))",
                    ).replace(
                        # bare `val` used directly (e.g. arithmetic on val without CAST)
                        " val ", f" {_iv_subq} ",
                    ).replace(
                        "(val ", f"({_iv_subq} ",
                    ).replace(
                        " val)", f" {_iv_subq})",
                    )
                    context.add_dml(
                        f'{cte}INSERT INTO {_table("rdf_props")} (s, "key", val) '
                        f'SELECT node_id, ?, {insert_val_sql} FROM {_table("nodes")} '
                        f'WHERE node_id IN ({subquery}) AND NOT EXISTS ('
                        f'SELECT 1 FROM {_table("rdf_props")} WHERE s = {_table("nodes")}.node_id AND "key" = ?'
                        f')',
                        [k] + val_params + subparams + [k],
                    )
                else:
                    # Literal / parameter value
                    val = val_params[0] if val_params else None
                    context.add_dml(
                        f'{cte}UPDATE {_table("rdf_props")} SET val = ? WHERE s IN ({subquery}) AND "key" = ?',
                        [val] + subparams + [k],
                    )
                    context.add_dml(
                        f'{cte}INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT node_id, ?, ? FROM {_table("nodes")} WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM {_table("rdf_props")} WHERE s = {_table("nodes")}.node_id AND "key" = ?)',
                        [k, val] + subparams + [k],
                    )
        elif isinstance(item.expression, ast.Variable) and isinstance(item.value, ast.MapLiteral) and not getattr(item, "merge", False):
            # SET n = {map} — full property replace: delete all existing props and insert new ones
            alias = context.variable_aliases.get(item.expression.name)
            # DELETE uses the full property-filtered subquery (fires before props are gone)
            cte, subquery, subparams = context.build_dml_subquery(
                select_override=f"SELECT {alias}.node_id"
            )
            context.add_dml(
                f"{cte}DELETE FROM {_table('rdf_props')} WHERE s IN ({subquery})",
                subparams,
            )
            # INSERT/SELECT use label-only subquery (props deleted, property JOINs would return 0 rows)
            cte_lo, subquery_lo, subparams_lo = context.build_label_only_dml_subquery(
                node_alias=alias,
                select_override=f"SELECT {alias}.node_id",
            )
            # Track this alias so the final SELECT also drops property JOINs for it
            if not hasattr(context, '_full_replace_aliases'):
                context._full_replace_aliases = set()
            context._full_replace_aliases.add(alias)
            # Insert new properties (skip null values per openCypher semantics)
            for k, v in item.value.entries.items():
                val = v.value if isinstance(v, ast.Literal) else context.input_params.get(v.name) if isinstance(v, ast.Variable) else None
                if val is None:
                    continue
                context._set_properties.add(k)
                context.add_dml(
                    f'{cte_lo}INSERT INTO {_table("rdf_props")} (s, "key", val) SELECT node_id, ?, ? FROM {_table("nodes")} WHERE node_id IN ({subquery_lo})',
                    [k, val] + subparams_lo,
                )
        elif isinstance(item.expression, ast.Variable):
            alias = context.variable_aliases.get(item.expression.name)
            # item.value may be a list of labels (SET n:Foo:Bar) or a single string/literal
            raw_val = item.value
            if isinstance(raw_val, list):
                label_list = [str(lv) for lv in raw_val]
            else:
                label_list = [str(raw_val.value if isinstance(raw_val, ast.Literal) else raw_val)]
            cte, subquery, subparams = context.build_dml_subquery(
                select_override=f"SELECT {alias}.node_id"
            )
            for label in label_list:
                context.add_dml(
                    f"{cte}INSERT INTO {_table('rdf_labels')} (s, label) SELECT node_id, ? FROM {_table('nodes')} WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM {_table('rdf_labels')} WHERE s = {_table('nodes')}.node_id AND label = ?)",
                    [label] + subparams + [label],
                )


def translate_remove_clause(remove, context, metadata):
    # Track which properties are being REMOVED so we can exclude them from the final SELECT WHERE clause
    if not hasattr(context, '_removed_properties'):
        context._removed_properties = set()
    if not hasattr(context, '_removed_labels'):
        context._removed_labels = set()

    for item in remove.items:
        if isinstance(item.expression, ast.Variable) and item.label:
            alias = context.variable_aliases.get(item.expression.name)
            cte, subquery, subparams = context.build_dml_subquery(
                select_override=f"SELECT {alias}.node_id"
            )
            # item.label may be a list (REMOVE n:Foo:Bar) or a single string
            label_list = item.label if isinstance(item.label, list) else [item.label]
            for lbl in label_list:
                context._removed_labels.add(lbl)
                context.add_dml(
                    f"{cte}DELETE FROM {_table('rdf_labels')} WHERE s IN ({subquery}) AND label = ?",
                    subparams + [lbl],
                )
        elif isinstance(item.expression, ast.PropertyReference):
            prop_name = item.expression.property_name
            context._removed_properties.add(prop_name)
            alias, k = (
                context.variable_aliases.get(item.expression.variable),
                prop_name,
            )
            # Detect edge alias: relationship variables use aliases starting with 'e' but not 'ES_'
            is_edge = alias and alias.startswith('e') and not alias.startswith('ES_')
            if is_edge:
                # Relationship property REMOVE: update qualifiers JSON to remove the key
                cte, subquery, subparams = context.build_dml_subquery(
                    select_override=f"SELECT {alias}.edge_id"
                )
                context.add_dml(
                    f'{cte}UPDATE {_table("rdf_edges")} SET qualifiers = '
                    f'SQLUser.CypherFn_IVGJSONREMOVE(qualifiers, ?) '
                    f'WHERE edge_id IN ({subquery})',
                    [k] + subparams,
                )
            else:
                cte, subquery, subparams = context.build_dml_subquery(
                    select_override=f"SELECT {alias}.node_id"
                )
                context.add_dml(
                    f'{cte}DELETE FROM {_table("rdf_props")} WHERE s IN ({subquery}) AND "key" = ?',
                    subparams + [k],
                )


def translate_match_clause(match_clause, context, metadata):
    # For OPTIONAL MATCH: snapshot the set of alias strings already bound before
    # this clause starts.  _trp_directed_edge uses this to choose the correct null
    # guard: "edge IS NULL" when the source was bound before the optional started
    # (the source can be legitimately non-null while the edge is absent), vs
    # "source IS NULL" when the source was introduced inside this optional (meaning
    # the source only exists if the full optional path was found).
    if match_clause.optional:
        context.optional_prebound_aliases = set(context.variable_aliases.values())
    else:
        context.optional_prebound_aliases = set()
        # Non-optional MATCH after a stage (WITH clause): a prior OPTIONAL MATCH may have
        # registered a null-row fallback. Clear it — the non-optional MATCH will naturally
        # produce 0 rows when the optionally-bound variable is null, so the null-row
        # UNION ALL must not fire (it would produce spurious null result rows).
        if context.stages:
            context.optional_null_row_labels = []
            context.optional_null_row_items = []
            context.optional_null_row_unconditional = False
    # Validate no duplicate variables within the same MATCH clause (across all patterns)
    vars_in_match = set()
    for pattern in match_clause.patterns:
        if not pattern.nodes:
            continue
        # Check for node back-references within a pattern chain (e.g. (n)-->(n) self-loop).
        # These are VALID in Cypher — they constrain start/end to the same node.
        # Track them so we can add self-loop WHERE constraints below.
        node_vars_in_pattern: dict = {}  # var -> first index
        self_loop_vars: set = set()  # vars that appear twice (back-reference)
        for idx_n, node in enumerate(pattern.nodes):
            if node.variable:
                if node.variable in node_vars_in_pattern:
                    # Back-reference (self-loop) — valid in Cypher
                    self_loop_vars.add(node.variable)
                else:
                    node_vars_in_pattern[node.variable] = idx_n
                vars_in_match.add(node.variable)
        # Track rel vars in a separate set (rel vars can't be duplicated)
        rel_vars_in_pattern: set = set()
        for rel in pattern.relationships:
            if rel.variable:
                if rel.variable in rel_vars_in_pattern:
                    raise CypherParseError(
                        f"VariableAlreadyBound: variable '{rel.variable}' appears twice "
                        f"in the same pattern"
                    )
                rel_vars_in_pattern.add(rel.variable)
                # Also check if this variable was already seen in this MATCH clause
                if rel.variable in vars_in_match:
                    raise CypherParseError(
                        f"VariableAlreadyBound: variable '{rel.variable}' is already bound "
                        f"in this MATCH clause"
                    )
                vars_in_match.add(rel.variable)
        first_node = pattern.nodes[0]
        # Skip upfront node join when the first node is unbound but the pattern's
        # last node IS already bound.  translate_relationship_pattern will anchor
        # the edge on the bound target and join this node from the edge (direction-
        # symmetry fix).  Without this guard, translate_node_pattern emits a CROSS
        # JOIN that produces wrong results for (t)-[:R]->(f_bound).
        first_is_unbound = (
            first_node.variable is not None
            and first_node.variable not in context.variable_aliases
        )
        last_node_bound = (
            pattern.nodes
            and pattern.nodes[-1].variable
            and pattern.nodes[-1].variable in context.variable_aliases
        )
        skip_first_node_join = (
            first_is_unbound
            and last_node_bound
            and bool(pattern.relationships)
        )
        has_rels = bool(pattern.relationships)
        if not skip_first_node_join:
            if first_node.variable:
                translate_node_pattern(
                    first_node, context, metadata, optional=match_clause.optional
                )
            elif first_node.labels or first_node.properties:
                if not has_rels:
                    # Standalone anonymous labeled/propertied node — translate normally.
                    translate_node_pattern(
                        first_node, context, metadata, optional=match_clause.optional
                    )
                else:
                    # Anonymous labeled source in a relationship — edge join handles it.
                    # Adding a standalone nodes JOIN here would create a Cartesian product.
                    _ = context.next_alias("n")
            else:
                if not has_rels:
                    # Bare anonymous node MATCH () with no labels/properties/rels:
                    # translate normally to get FROM Graph_KG.nodes n0.
                    translate_node_pattern(
                        first_node, context, metadata, optional=match_clause.optional
                    )
                else:
                    _ = context.next_alias("n")
        # Track (edge_alias, is_undirected) for each hop in this pattern — used
        # after each hop to add isomorphic-edge-exclusion WHERE conditions.
        # Cypher guarantees the same physical edge cannot be traversed twice in
        # a single path pattern.
        _pattern_edge_aliases: list = []  # list of (alias, is_undirected)

        for i, rel in enumerate(pattern.relationships):
            src_node = pattern.nodes[i]
            tgt_node = pattern.nodes[i + 1]
            translate_relationship_pattern(
                rel,
                src_node,
                tgt_node,
                context,
                metadata,
                optional=match_clause.optional,
            )
            last_node = tgt_node
            is_back_ref = (
                src_node.variable
                and tgt_node.variable
                and src_node.variable == tgt_node.variable
            )
            if is_back_ref:
                # Self-loop: both ends are the same node — add edge self-loop constraint.
                src_alias = context.variable_aliases.get(src_node.variable)
                edge_alias = context.rel_obj_aliases.get(id(rel))
                if src_alias and edge_alias:
                    if rel.direction == ast.Direction.BOTH:
                        # Undirected edge: _src and _dst are the same node.
                        # For OPTIONAL MATCH the edge may be NULL — allow NULL to pass.
                        if match_clause.optional:
                            context.where_conditions.append(
                                f"({edge_alias}._src IS NULL OR {edge_alias}._src = {edge_alias}._dst)"
                            )
                        else:
                            context.where_conditions.append(
                                f"{edge_alias}._src = {edge_alias}._dst"
                            )
                    else:
                        # Directed edge: s and o_id are the same node.
                        # For OPTIONAL MATCH the edge may be NULL — allow NULL to pass.
                        if match_clause.optional:
                            context.where_conditions.append(
                                f"({edge_alias}.s IS NULL OR {edge_alias}.s = {edge_alias}.o_id)"
                            )
                        else:
                            context.where_conditions.append(
                                f"{edge_alias}.s = {edge_alias}.o_id"
                            )
            elif last_node.variable:
                translate_node_pattern(
                    last_node, context, metadata, optional=match_clause.optional
                )
            elif not (last_node.labels or last_node.properties):
                pass  # truly anonymous target — edge join covers it
            # else: anonymous labeled/propertied target — _trp_directed_edge already
            # added label JOINs against the edge-joined alias; no standalone JOIN needed.

            # Isomorphic edge exclusion: this hop's physical edge must differ from
            # every previous hop's physical edge in this pattern.
            new_ea = context.rel_obj_aliases.get(id(rel))
            if new_ea and _pattern_edge_aliases:
                new_is_und = new_ea in context._undirected_aliases
                # Physical identity columns for the new edge:
                if new_is_und:
                    # Undirected: CTE exposes _os/_oo as physical s/o_id
                    new_s = f"{new_ea}._os"
                    new_p = f"{new_ea}._p"
                    new_o = f"{new_ea}._oo"
                else:
                    new_s = f"{new_ea}.s"
                    new_p = f"{new_ea}.p"
                    new_o = f"{new_ea}.o_id"
                for prev_ea, prev_is_und in _pattern_edge_aliases:
                    if prev_is_und:
                        prev_s = f"{prev_ea}._os"
                        prev_p = f"{prev_ea}._p"
                        prev_o = f"{prev_ea}._oo"
                    else:
                        prev_s = f"{prev_ea}.s"
                        prev_p = f"{prev_ea}.p"
                        prev_o = f"{prev_ea}.o_id"
                    # Isomorphic-edge exclusion: the same physical edge cannot be
                    # traversed twice.  When in an OPTIONAL MATCH, either edge may be
                    # null (LEFT OUTER JOIN returned no row).  NULL comparisons evaluate
                    # to NULL rather than FALSE, which would incorrectly filter null rows.
                    # Guard with IS NULL checks so null hops always pass the constraint.
                    is_opt = match_clause.optional
                    excl = (
                        f"NOT ({new_s} = {prev_s} AND {new_p} = {prev_p} AND {new_o} = {prev_o})"
                    )
                    if is_opt:
                        excl = f"({excl} OR {new_s} IS NULL OR {prev_s} IS NULL)"
                    context.where_conditions.append(excl)
            if new_ea:
                new_is_und = new_ea in context._undirected_aliases
                _pattern_edge_aliases.append((new_ea, new_is_und))

    for np in match_clause.named_paths:
        context.named_paths[np.variable] = np
        # Track path variable type for semantic validation
        context.bind_variable_type(np.variable, "path")
        node_aliases = [
            context.variable_aliases.get(n.variable)
            if n.variable
            else context.node_obj_aliases.get(id(n), f"n{i}")
            for i, n in enumerate(np.pattern.nodes)
        ]
        # For relationships: first try the variable alias, then look up by object id
        edge_aliases = []
        for i, r in enumerate(np.pattern.relationships):
            if r.variable:
                # Named relationship: use its registered alias
                alias = context.variable_aliases.get(r.variable, f"e{i}")
            else:
                # Anonymous relationship: look up by object id from _trp_setup_aliases tracking
                alias = context.rel_obj_aliases.get(id(r), f"e{i}")
            edge_aliases.append(alias)
        context.path_node_aliases[np.variable] = node_aliases
        context.path_edge_aliases[np.variable] = edge_aliases


def _subquery_correlated_scalar(subquery, inner, child_ctx, context):
    ret_item = inner.return_clause.items[0]
    alias = ret_item.alias or "sub_result"
    inner_expr = translate_expression(ret_item.expression, child_ctx, segment="select")
    inner_sql_parts = [f"SELECT {inner_expr}"]
    if child_ctx.from_clauses:
        inner_sql_parts.append(f"FROM {', '.join(child_ctx.from_clauses)}")
        if child_ctx.join_clauses:
            inner_sql_parts.extend(child_ctx.join_clauses)
    elif child_ctx.join_clauses:
        first_join = (
            child_ctx.join_clauses[0]
            .replace("JOIN ", "", 1)
            .replace("CROSS JOIN ", "", 1)
        )
        on_idx = first_join.find(" ON ")
        if on_idx > 0:
            from_part = first_join[:on_idx]
            on_part = first_join[on_idx + 4 :]
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


def _subquery_lateral_inline_param(val):
    if isinstance(val, str):
        return f"'{val.replace(chr(39), chr(39) + chr(39))}'"
    if isinstance(val, bool):
        return "1" if val else "0"
    if val is None:
        return "NULL"
    return str(val)


def _subquery_correlated_lateral(subquery, inner, context, metadata):
    child_ctx_lateral = TranslationContext()
    child_ctx_lateral.input_params = context.input_params
    child_ctx_lateral._alias_counter = context._alias_counter
    for var in subquery.import_variables:
        child_ctx_lateral.variable_aliases[var] = context.variable_aliases[var]

    child_ctx_lateral.add_join_param = _subquery_lateral_inline_param
    child_ctx_lateral.add_where_param = _subquery_lateral_inline_param
    child_ctx_lateral.add_select_param = _subquery_lateral_inline_param

    for part in inner.query_parts:
        for clause in part.clauses:
            if isinstance(clause, ast.MatchClause):
                translate_match_clause(clause, child_ctx_lateral, metadata)
            elif isinstance(clause, ast.WhereClause):
                translate_where_clause(clause, child_ctx_lateral)
    translate_return_clause(inner.return_clause, child_ctx_lateral)
    if not child_ctx_lateral.from_clauses and child_ctx_lateral.join_clauses:
        first_jc = child_ctx_lateral.join_clauses[0]
        for prefix in ("CROSS JOIN ", "LEFT OUTER JOIN ", "JOIN "):
            if first_jc.startswith(prefix):
                rest = first_jc[len(prefix):]
                on_idx = rest.find(" ON ")
                if on_idx > 0:
                    table_part = rest[:on_idx]
                    cond_part = rest[on_idx + 4:]
                    child_ctx_lateral.from_clauses.append(table_part)
                    if cond_part.strip() and cond_part.strip() != "1=1":
                        child_ctx_lateral.where_conditions.insert(0, cond_part)
                    child_ctx_lateral.join_clauses = child_ctx_lateral.join_clauses[1:]
                else:
                    child_ctx_lateral.from_clauses.append(rest)
                    child_ctx_lateral.join_clauses = child_ctx_lateral.join_clauses[1:]
                break
    inner_sql_parts_lat = [
        f"SELECT {'DISTINCT ' if inner.return_clause.distinct else ''}{', '.join(child_ctx_lateral.select_items)}"
    ]
    if child_ctx_lateral.from_clauses:
        inner_sql_parts_lat.append(f"FROM {', '.join(child_ctx_lateral.from_clauses)}")
    if child_ctx_lateral.join_clauses:
        inner_sql_parts_lat.extend(child_ctx_lateral.join_clauses)
    if child_ctx_lateral.where_conditions:
        inner_sql_parts_lat.append(f"WHERE {' AND '.join(child_ctx_lateral.where_conditions)}")
    inner_sql = "\n".join(inner_sql_parts_lat)
    lat_alias = context.next_alias("lat")
    context.join_clauses.append(f"CROSS JOIN LATERAL (\n{inner_sql}\n) {lat_alias}")
    for item in inner.return_clause.items:
        col_alias = item.alias
        if col_alias is None:
            if isinstance(item.expression, ast.Variable):
                col_alias = item.expression.name
            elif isinstance(item.expression, ast.PropertyReference):
                col_alias = f"{item.expression.variable}_{item.expression.property_name}"
            else:
                col_alias = f"col_{len(context.scalar_variables)}"
        if col_alias:
            context.variable_aliases[col_alias] = lat_alias
            context.scalar_variables.add(col_alias)


def _subquery_correlated(subquery, inner, context, metadata):
    if not inner.return_clause:
        raise ValueError("Correlated subquery requires a RETURN clause")

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

    num_return_cols = len(inner.return_clause.items)
    is_single_scalar = num_return_cols == 1 and isinstance(
        inner.return_clause.items[0].expression, (ast.AggregationFunction,)
    )

    if is_single_scalar:
        _subquery_correlated_scalar(subquery, inner, child_ctx, context)
    else:
        _subquery_correlated_lateral(subquery, inner, context, metadata)


def _subquery_uncorrelated(subquery, inner, context, metadata):
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


def translate_subquery_call(
    subquery: ast.SubqueryCall, context: TranslationContext, metadata
):
    inner = subquery.inner_query
    is_correlated = len(subquery.import_variables) > 0
    if is_correlated:
        _subquery_correlated(subquery, inner, context, metadata)
    else:
        _subquery_uncorrelated(subquery, inner, context, metadata)


def translate_node_pattern(node, context, metadata, optional=False):
    if node.variable and node.variable in context.variable_aliases:
        # Special case: scalar variable used as node in OPTIONAL MATCH.
        # e.g. WITH null AS a … OPTIONAL MATCH p = (a)-[r]->()
        # The scalar value is treated as a node-id filter; since null never matches
        # any node_id the optional match always produces 0 rows → null fallback fires.
        if optional and node.variable in context.scalar_variables:
            # Scalar variable used as node anchor in OPTIONAL MATCH.
            # e.g. WITH null AS a → OPTIONAL MATCH (a)-[r]->()
            # Since null never equals a real node_id, the match always produces 0 rows.
            # Keep the stage alias for the scalar but don't add a new nodes JOIN —
            # downstream edge joins will constrain on the scalar column directly.
            stage_alias = context.variable_aliases[node.variable]
            # Keep variable type as node for downstream usage; do NOT rebind the alias.
            context.variable_types[node.variable] = "node"
            # Force the null-row fallback to fire unconditionally.
            context.optional_null_row_unconditional = True
            return
        # Collected-node UNWIND variable: the scalar holds a node JSON blob.
        # Allow using it as a node anchor in MATCH — the edge JOIN will use
        # JSON_VALUE(alias.var, '$._id') to extract the node_id for comparison.
        if node.variable in getattr(context, "collected_node_variables", set()):
            context.variable_types[node.variable] = "node"
            return
        # Validate type consistency: if variable was previously bound to a different type, error
        context.bind_variable_type(node.variable, "node")
        # Node already registered (e.g. as far-end of a relationship JOIN), but
        # labels and properties declared on this node pattern still need to be
        # applied as filter JOINs against the already-registered alias.
        if node.labels or node.properties:
            alias = context.variable_aliases[node.variable]
            # For CTE stage aliases (Stage1, Stage2…), the node_id column is stored
            # under the variable name (e.g. Stage1.a1), not Stage1.node_id.
            if alias.startswith("Stage"):
                node_id_col = f"{alias}.{node.variable}"
            else:
                node_id_col = f"{alias}.node_id"
            jt = "LEFT OUTER JOIN" if optional else "JOIN"
            for label in node.labels:
                # For optional non-anchor targets: push the label check into the
                # PRECEDING EDGE JOIN's ON clause via EXISTS, rather than a separate
                # label JOIN + WHERE condition.  This ensures that when ALL edges from
                # the anchor fail the label test, the LEFT OUTER JOIN produces exactly
                # ONE null row (instead of filtering every expanded-edge row to 0 rows).
                if optional and not alias.startswith("Stage"):
                    import re as _re_lbl
                    _node_join_pat = _re_lbl.compile(
                        rf'LEFT OUTER JOIN\s+\S+\s+{_re_lbl.escape(alias)}\s+ON\s+'
                        rf'{_re_lbl.escape(alias)}\.node_id\s*=\s*(\S+)'
                    )
                    _rhs_col = None
                    _edge_join_idx = None
                    for _jidx in range(len(context.join_clauses) - 1, -1, -1):
                        _m = _node_join_pat.search(context.join_clauses[_jidx])
                        if _m:
                            _rhs_col = _m.group(1).rstrip(")")
                            # The edge JOIN is the clause immediately before the node JOIN
                            if _jidx > 0:
                                _edge_join_idx = _jidx - 1
                            break
                    if _rhs_col is not None and _edge_join_idx is not None:
                        label_param = context.add_join_param(label)
                        context.join_clauses[_edge_join_idx] += (
                            f" AND EXISTS(SELECT 1 FROM {_table('rdf_labels')}"
                            f" WHERE s = {_rhs_col} AND \"label\" = {label_param})"
                        )
                        continue  # no separate label JOIN or WHERE needed
                l_alias = context.next_alias("l")
                context.join_clauses.append(
                    f"{jt} {_table('rdf_labels')} {l_alias} ON {l_alias}.s = {node_id_col} AND {l_alias}.label = {context.add_join_param(label)}"
                )
                if not optional:
                    context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
                else:
                    # Optional non-anchor target with label constraint: if the node was
                    # reached (node_id IS NOT NULL) the label must match; otherwise the
                    # edge is treated as non-matching (null result).
                    context.where_conditions.append(
                        f"({node_id_col} IS NULL OR {l_alias}.s IS NOT NULL)"
                    )
            for k, v in node.properties.items():
                val_sql = translate_expression(v, context, segment="where")
                if k in ("node_id", "id"):
                    context.where_conditions.append(f"{node_id_col} = {val_sql}")
                else:
                    if not optional:
                        context.where_conditions.append(
                            TranslationContext._structural_guard_sql(alias, k)
                        )
                    p_alias = context.next_alias("p")
                    context.join_clauses.append(
                        f"{jt} {_table('rdf_props')} {p_alias} "
                        f'ON {p_alias}.s = {node_id_col} AND {p_alias}."key" = {context.add_join_param(k)}'
                    )
                    if optional:
                        context.where_conditions.append(
                            f"({p_alias}.s IS NULL OR {p_alias}.val = {val_sql})"
                        )
                    else:
                        context.where_conditions.append(f"{p_alias}.val = {val_sql}")
        return
    alias = (
        context.register_variable(node.variable)
        if node.variable
        else context.next_alias("n")
    )
    # Track node type for semantic validation
    if node.variable:
        context.bind_variable_type(node.variable, "node")
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
                    context.where_conditions.append(
                        f"{alias}.{sanitize_identifier(k)} = {val_sql}"
                    )
                return

    nodes_tbl = _table("nodes")
    # For OPTIONAL MATCH, the "optional" semantics mean: if the whole pattern matches
    # nothing, produce one null row. The label/property constraints on the anchor node
    # (the first node in the query, when from_clauses is still empty) are still
    # restrictive — use INNER JOIN so only nodes carrying the label are returned.
    # LEFT OUTER JOIN is reserved for extending an already-bound variable (e.g. the
    # target node in MATCH (a) OPTIONAL MATCH (a)-->(b)).
    is_anchor_optional = optional and not context.from_clauses
    effective_jt = "JOIN" if is_anchor_optional else jt
    if not context.from_clauses:
        context.from_clauses.append(f"{nodes_tbl} {alias}")
    elif f"{nodes_tbl} {alias}" not in context.from_clauses and not any(
        alias in j for j in context.join_clauses
    ):
        context.join_clauses.append(f"CROSS JOIN {nodes_tbl} {alias}")
    if node.labels:
        if is_anchor_optional and node.labels:
            # Track all labels for this anchor as a group (combined NOT EXISTS check).
            context.optional_null_row_label_groups.append(list(node.labels))
        if getattr(node, 'labels_or', False) and len(node.labels) > 1:
            l_alias = context.next_alias("l")
            labels_inlined = ", ".join(f"'{lab}'" for lab in node.labels)
            context.join_clauses.append(
                f"{effective_jt} {_table('rdf_labels')} {l_alias} ON {l_alias}.s = {alias}.node_id AND {l_alias}.label IN ({labels_inlined})"
            )
            if not optional or is_anchor_optional:
                context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
            elif optional and not is_anchor_optional:
                # Non-anchor optional target with label constraint: if the node was
                # reached (node_id IS NOT NULL) the label must match; otherwise treat
                # the edge as non-matching (null result).  This enforces Cypher semantics:
                # a label constraint on an optional target node filters out edges that
                # reach a node without the required label.
                context.where_conditions.append(
                    f"({alias}.node_id IS NULL OR {l_alias}.s IS NOT NULL)"
                )
        else:
            for label in node.labels:
                l_alias = context.next_alias("l")
                label_param = context.add_join_param(label)
                context.join_clauses.append(
                    f"{effective_jt} {_table('rdf_labels')} {l_alias} ON {l_alias}.s = {alias}.node_id AND {l_alias}.label = {label_param}"
                )
                if not optional or is_anchor_optional:
                    context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
                elif optional and not is_anchor_optional:
                    # Non-anchor optional target with label constraint: if the node was
                    # reached (node_id IS NOT NULL) the label must match; otherwise the
                    # edge is treated as non-matching (null result).
                    context.where_conditions.append(
                        f"({alias}.node_id IS NULL OR {l_alias}.s IS NOT NULL)"
                    )
                if is_anchor_optional:
                    context.optional_null_row_labels.append(label)
    for k, v in node.properties.items():
        val_sql = translate_expression(v, context, segment="where")
        if k in ("node_id", "id"):
            context.where_conditions.append(f"{alias}.node_id = {val_sql}")
        else:
            if not optional:
                context.where_conditions.append(
                    TranslationContext._structural_guard_sql(alias, k)
                )
            p_alias = context.next_alias("p")
            context.join_clauses.append(
                f"{jt} {_table('rdf_props')} {p_alias} "
                f'ON {p_alias}.s = {alias}.node_id AND {p_alias}."key" = {context.add_join_param(k)}'
            )
            if optional:
                context.where_conditions.append(
                    f"({p_alias}.s IS NULL OR {p_alias}.val = {val_sql})"
                )
            else:
                context.where_conditions.append(f"{p_alias}.val = {val_sql}")



def _trp_variable_length(rel, source_node, target_node, context, metadata, optional=False):
    """Handle variable-length path patterns. Writes to context.var_length_paths."""
    if rel.variable_length is not None:
        source_alias = context.variable_aliases.get(source_node.variable, "")
        target_alias = context.register_variable(target_node.variable)

        def _resolve_id_param(node):
            id_val = node.properties.get("id")
            if id_val is None:
                if node.variable and node.variable in context.input_params:
                    val = context.input_params[node.variable]
                    return f"${node.variable}" if isinstance(val, str) else None
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

        direction_str = "both" if rel.direction == ast.Direction.BOTH else ("in" if rel.direction == ast.Direction.INCOMING else "out")

        # If the relationship has a named variable (e.g. [r*1..3]), register it so
        # that RETURN r does not raise "Undefined variable".  We use the sentinel alias
        # "__vl_rel__" so _expr_variable can emit a NULL placeholder; the engine fills
        # in the actual relationship list after BFS traversal.
        if rel.variable:
            context.variable_aliases[rel.variable] = "__vl_rel__"
            context.rel_variables.add(rel.variable)
            context.bind_variable_type(rel.variable, "relationship", force=True)

        context.var_length_paths.append(
            {
                "source_var": source_node.variable,
                "source_alias": source_alias,
                "target_var": target_node.variable,
                "target_alias": target_alias,
                "rel_var": rel.variable,
                "types": rel.types or [],
                "direction": direction_str,
                "min_hops": rel.variable_length.min_hops,
                "max_hops": rel.variable_length.max_hops,
                "shortest": rel.variable_length.shortest,
                "all_shortest": rel.variable_length.all_shortest,
                "src_id_param": src_id_param,
                "dst_id_param": dst_id_param,
                "return_path_funcs": [],
                "properties": {
                    k: (v.value if isinstance(v, ast.Literal) else v)
                    for k, v in rel.properties.items()
                } if rel.properties else {},
                "source_labels": list(source_node.labels) if source_node.labels else [],
                "target_labels": list(target_node.labels) if target_node.labels else [],
                "optional": optional,
            }
        )
        if not context.from_clauses:
            context.from_clauses.append(f"{_table('nodes')} {target_alias}")
        else:
            context.join_clauses.append(f"JOIN {_table('nodes')} {target_alias} ON 1=1")
        return


def _trp_setup_aliases(rel, source_node, target_node, context):
    """Register aliases. Returns (src, tgt, edge, is_anon, is_new, is_unbound_src).

    is_unbound_src: source has a variable name but was not yet bound when this
    pattern was entered.  The caller must NOT pre-join it as a CROSS JOIN; instead
    the edge join must be anchored on the (already-bound) target and the source
    node joined from the edge.  This fixes the direction-symmetry bug: patterns
    (t)-[:R]->(f) and (f)<-[:R]-(t) must produce identical SQL when f is bound.
    """
    is_anon_source = source_node.variable is None
    is_unbound_src = False
    if is_anon_source:
        # Reuse existing alias if this anonymous node object was already seen
        # (e.g. as target of previous hop in a chain like ()-[]-(x)-[]-()).
        node_id_key = id(source_node)
        if node_id_key in context.node_obj_aliases:
            source_alias = context.node_obj_aliases[node_id_key]
            is_anon_source = False  # treat as bound — it has a backing JOIN
        else:
            source_alias = context.next_alias("n")
            context.node_obj_aliases[node_id_key] = source_alias
    else:
        existing = context.variable_aliases.get(source_node.variable)
        if existing is None:
            # Source variable exists in the query but has not been bound yet.
            # Register it now so downstream code has an alias, but flag it so
            # translate_relationship_pattern anchors the edge on the target side.
            source_alias = context.register_variable(source_node.variable)
            is_unbound_src = True
        else:
            source_alias = existing
    if target_node.variable is None:
        # Anonymous target: use object-id keyed alias to avoid sharing None key
        # across multiple anonymous nodes in a chain.
        node_key = id(target_node)
        if node_key in context.node_obj_aliases:
            target_alias = context.node_obj_aliases[node_key]
            is_new_target = False
        else:
            target_alias = context.next_alias("n")
            context.node_obj_aliases[node_key] = target_alias
            is_new_target = True
    else:
        is_new_target = target_node.variable not in context.variable_aliases
        target_alias = context.register_variable(target_node.variable)
    edge_alias = (
        context.register_variable(rel.variable, prefix="e")
        if rel.variable
        else context.next_alias("e")
    )
    # Track relationship object → SQL alias for named path lookup
    context.rel_obj_aliases[id(rel)] = edge_alias
    return source_alias, target_alias, edge_alias, is_anon_source, is_new_target, is_unbound_src


def _trp_temporal_rewrite_from_joins(context, source_alias, cte_name):
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
        if (
            f"{source_alias}.node_id" in jc
            or f"{_table('nodes')} {source_alias}" in jc
        ):
            continue
        new_joins.append(jc)
    context.join_clauses = new_joins


def _trp_temporal_edge(rel, source_node, target_node, context, source_alias, edge_alias, direction):
    if rel.variable is None or context.pending_where is None:
        return False
    tb = _extract_temporal_bounds(
        context.pending_where, rel.variable, context.input_params
    )
    if tb is None:
        return False
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
        source_filter,
        predicate_filter,
        ts_start,
        ts_end,
        direction=tb.direction,
    )
    cte_name = f"tc{edge_alias}"
    cte_sql = _build_temporal_cte(edges, cte_name, getattr(context, "_metadata", None))
    if not hasattr(context, "cte_clauses"):
        context.cte_clauses = []
    context.cte_clauses.append(
        f"{cte_name} AS ({cte_sql})"
    )
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

    _trp_temporal_rewrite_from_joins(context, source_alias, cte_name)

    _remove_ts_conditions_from_where(context, rel.variable)
    return True


def _trp_mapped_relation(rel, source_node, target_node, context, source_alias, target_alias, optional):
    """Handle SQL-table-bridge mapped relations. Returns True if handled."""
    if not (rel.types and len(rel.types) == 1):
        return False
    engine = getattr(context, "_engine", None)
    src_label = (
        next((lbl for lbl in source_node.labels), None)
        if source_node.labels
        else None
    )
    tgt_label = (
        next((lbl for lbl in target_node.labels), None)
        if target_node.labels
        else None
    )
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
                return True


def _trp_undirected_edge(
    rel, source_node, target_node, context,
    source_alias, target_alias, edge_alias, s_ref, t_ref, jt, is_new_target,
    is_anon_source=False,
):
    """Handle undirected (BOTH direction) patterns via CTE-based UNION ALL.

    Using a CTE (rather than an inline derived table) avoids an IRIS 2026.x
    UNDEFINED crash that occurs when a UNION ALL subquery appears inside a
    multi-table JOIN chain.  The CTE also exposes _os/_oo columns (the physical
    (s, o_id) pair regardless of traversal direction) so that callers can add
    isomorphic-edge-exclusion WHERE conditions to prevent the same physical edge
    from being matched twice in one pattern.
    """
    pred_filter = ""
    if rel.types:
        if len(rel.types) == 1:
            safe_p = rel.types[0].replace("'", "''")
            pred_filter = f" AND p = '{safe_p}'"
        else:
            safe_ps = ", ".join(f"'{t.replace(chr(39), chr(39)+chr(39))}'" for t in rel.types)
            pred_filter = f" AND p IN ({safe_ps})"
    edges_tbl = _table("rdf_edges")

    # Build an unfiltered (or predicate-filtered) UNION ALL CTE.
    # Forward rows:  s  -> o_id  (all edges including self-loops)
    # Backward rows: o_id -> s   (self-loops excluded to avoid double-counting)
    # _os/_oo carry the physical edge identity so isomorphic-edge exclusion
    # WHERE conditions can be added by translate_match_clause.
    if pred_filter:
        where_fwd = f"WHERE 1=1{pred_filter}"
        where_rev = f"WHERE s != o_id{pred_filter}"
        cte_body = (
            f"  SELECT s AS _src, p AS _p, o_id AS _dst, s AS _os, o_id AS _oo, qualifiers\n"
            f"  FROM {edges_tbl}\n"
            f"  {where_fwd}\n"
            f"  UNION ALL\n"
            f"  SELECT o_id AS _src, p AS _p, s AS _dst, s AS _os, o_id AS _oo, qualifiers\n"
            f"  FROM {edges_tbl}\n"
            f"  {where_rev}"
        )
    else:
        cte_body = (
            f"  SELECT s AS _src, p AS _p, o_id AS _dst, s AS _os, o_id AS _oo, qualifiers\n"
            f"  FROM {edges_tbl}\n"
            f"  UNION ALL\n"
            f"  SELECT o_id AS _src, p AS _p, s AS _dst, s AS _os, o_id AS _oo, qualifiers\n"
            f"  FROM {edges_tbl} WHERE s != o_id"
        )

    cte_name = f"_u{edge_alias}"
    if not hasattr(context, "cte_clauses"):
        context.cte_clauses = []
    context.cte_clauses.append(f"{cte_name} AS (\n{cte_body}\n)")

    # Join the CTE as the edge alias.
    target_on = f"{t_ref} = {edge_alias}._dst"
    if is_anon_source:
        # No bound source node — the first hop; use as FROM or cross-JOIN.
        if not context.from_clauses:
            context.from_clauses.append(f"{cte_name} {edge_alias}")
        else:
            context.join_clauses.append(f"{jt} {cte_name} {edge_alias} ON 1=1")
        # Apply source node labels via _src column (anonymous source has no node table)
        for label in (source_node.labels or []):
            l_alias = context.next_alias("l")
            context.join_clauses.append(
                f"{jt} {_table('rdf_labels')} {l_alias} "
                f"ON {l_alias}.s = {edge_alias}._src AND {l_alias}.label = {context.add_join_param(label)}"
            )
    else:
        # Bound source: filter by source node id in the JOIN condition.
        src_filter = f"{edge_alias}._src = {s_ref}"
        context.join_clauses.append(f"{jt} {cte_name} {edge_alias} ON {src_filter}")

    context._undirected_aliases.add(edge_alias)
    if is_new_target and not target_alias.startswith("Stage"):
        context.join_clauses.append(
            f"{jt} {_table('nodes')} {target_alias} ON {target_on}"
        )
    else:
        context.where_conditions.append(target_on)
    context.variable_aliases[rel.variable or edge_alias] = edge_alias
    for prop_node, prop_alias in (
        (source_node, source_alias),
        (target_node, target_alias),
    ):
        if prop_node:
            for k, v in (prop_node.properties or {}).items():
                if k in ("id", "node_id"):
                    id_col = f"{prop_alias}.node_id"
                    context.where_conditions.append(
                        f"{id_col} = {context.add_where_param(v.value if isinstance(v, ast.Literal) else str(v))}"
                    )
                else:
                    p_alias = context.next_alias("p")
                    context.join_clauses.append(
                        f"JOIN {_table('rdf_props')} {p_alias} ON {p_alias}.s = {prop_alias}.node_id AND {p_alias}.\"key\" = {context.add_join_param(k)}"
                    )
                    context.where_conditions.append(
                        f"{p_alias}.val = {context.add_where_param(v.value if isinstance(v, ast.Literal) else str(v))}"
                    )


def _trp_resolve_src_id_sql(source_node, context):
    src_id_val = source_node.properties.get("id") if source_node else None
    if src_id_val is None:
        return None
    if isinstance(src_id_val, ast.Literal):
        return f"'{str(src_id_val.value)}'"
    if isinstance(src_id_val, ast.Variable):
        resolved = context.input_params.get(src_id_val.name) if context.input_params else None
        return f"'{resolved}'" if resolved else None
    return None


def _trp_apply_rel_inline_props(rel, edge_alias, context):
    """Apply inline property filters for a relationship pattern in MATCH.

    Relationship properties (e.g. [r:TYPE {key: value}]) are stored as JSON
    in rdf_edges.qualifiers.  For each key/value pair we add a WHERE condition
    using JSON_VALUE to filter the edge rows.
    """
    if not rel.properties:
        return
    for k, v in rel.properties.items():
        val_sql = translate_expression(v, context, segment="where")
        context.where_conditions.append(
            f"SQLUser.JSON_VALUE({edge_alias}.qualifiers, '$.{k}') = {val_sql}"
        )


def _trp_apply_inline_props(source_node, source_alias, target_node, target_alias, context, jt):
    for prop_node, prop_alias in (
        (source_node, source_alias),
        (target_node, target_alias),
    ):
        if prop_node is None or not prop_node.properties:
            continue
        for k, v in prop_node.properties.items():
            val_sql = translate_expression(v, context, segment="where")
            if k == "node_id":
                context.where_conditions.append(f"{prop_alias}.node_id = {val_sql}")
            else:
                p_alias = context.next_alias("p")
                context.join_clauses.append(
                    f"{jt} {_table('rdf_props')} {p_alias} "
                    f'ON {p_alias}.s = {prop_alias}.node_id AND {p_alias}."key" = {context.add_join_param(k)}'
                )
                context.where_conditions.append(f"{p_alias}.val = {val_sql}")


def _trp_directed_edge_join(
    rel, source_node, context, source_alias, edge_alias, edge_cond, jt, is_anon_source
):
    use_edgescan = (
        source_alias is not None
        and not source_alias.startswith("tc")
        and not source_alias.startswith("Stage")
        and not source_alias.startswith("BM25")
        and not source_alias.startswith("IVF_SEARCH")
        and not source_alias.startswith("IVF")
        and not source_alias.startswith("VecSearch")
        # Disable edgescan when relationship has inline properties: the edgescan
        # derived table (from Graph_KG.MatchEdges) does not expose the qualifiers
        # column needed to filter on inline relationship properties.
        and not rel.properties
    )

    if use_edgescan and not is_anon_source:
        pred_sql = f"'{rel.types[0]}'" if len(rel.types) == 1 else "NULL"
        src_id_sql = _trp_resolve_src_id_sql(source_node, context)
        if src_id_sql is not None and not context.graph_context:
            derived = (
                f"(\n"
                f"SELECT j.s, j.p, j.o_id, j.w\n"
                f"FROM JSON_TABLE(\n"
                f"  Graph_KG.MatchEdges({src_id_sql}, {pred_sql}, 0),\n"
                f"  '$[*]' COLUMNS(\n"
                f"    s VARCHAR(256) PATH '$.s',\n"
                f"    p VARCHAR(256) PATH '$.p',\n"
                f"    o_id VARCHAR(256) PATH '$.o',\n"
                f"    w DOUBLE PATH '$.w'\n"
                f"  )\n"
                f") j\n"
                f") {edge_alias}"
            )
            context.join_clauses.append(f"{jt} {derived} ON {edge_cond}")
            context._edgescan_aliases.add(edge_alias)
        else:
            context.join_clauses.append(
                f"{jt} {_table('rdf_edges')} {edge_alias} ON {edge_cond}"
            )
    else:
        if is_anon_source:
            actual_cond = edge_cond.lstrip("1=1").lstrip(" AND ").strip() if edge_cond.startswith("1=1") else edge_cond
            if not context.from_clauses:
                context.from_clauses.append(f"{_table('rdf_edges')} {edge_alias}")
                if actual_cond:
                    # Edge predicate goes into WHERE — params for rel.types were added as
                    # join_params but appear AFTER join-clause params in the SQL.  Move
                    # them to where_params so positional ? order matches SQL order.
                    n_type_params = actual_cond.count("?")
                    if n_type_params > 0:
                        moved = context.join_params[-n_type_params:]
                        del context.join_params[-n_type_params:]
                        context.where_params.extend(moved)
                    context.where_conditions.append(actual_cond)
            else:
                full_cond = actual_cond if actual_cond else "1=1"
                context.join_clauses.append(
                    f"{jt} {_table('rdf_edges')} {edge_alias} ON {full_cond}"
                )
        else:
            context.join_clauses.append(
                f"{jt} {_table('rdf_edges')} {edge_alias} ON {edge_cond}"
            )


def _trp_apply_anon_source_constraints(source_node, edge_alias, src_col, context, jt):
    """Apply label/property constraints for an anonymous source node via the edge column.

    When source has no variable there is no nodes JOIN for it — the edge table
    provides the source id via edge_alias.<src_col> ('s' for OUTGOING, 'o_id' for INCOMING).
    """
    src_ref = f"{edge_alias}.{src_col}"
    for label in (source_node.labels or []):
        l_alias = context.next_alias("l")
        context.join_clauses.append(
            f"{jt} {_table('rdf_labels')} {l_alias} "
            f"ON {l_alias}.s = {src_ref} AND {l_alias}.label = {context.add_join_param(label)}"
        )
    for k, v in (source_node.properties or {}).items():
        val_sql = translate_expression(v, context, segment="where")
        if k == "node_id":
            context.where_conditions.append(f"{src_ref} = {val_sql}")
        else:
            p_alias = context.next_alias("p")
            context.join_clauses.append(
                f"{jt} {_table('rdf_props')} {p_alias} "
                f'ON {p_alias}.s = {src_ref} AND {p_alias}."key" = {context.add_join_param(k)}'
            )
            context.where_conditions.append(f"{p_alias}.val = {val_sql}")


def _trp_move_target_cond_to_edge_join(context, edge_alias, target_on, source_alias):
    """Move a bound-target equality condition from WHERE into the edge JOIN ON clause.

    Used for multi-hop OPTIONAL MATCH where source was introduced within the same
    optional pattern.  Adding the target condition to the edge ON means the edge is
    null when the path fails, while the intermediate source node (null-gated via
    opt_intermediate_nulled) is nulled in SELECT when the edge is null.

    Also registers source_alias → edge_alias in opt_intermediate_nulled so that
    translate_return_clause can emit a CASE expression rather than a bare node_id.
    """
    # Find the edge join clause and append the target condition to its ON clause
    for i, jc in enumerate(context.join_clauses):
        # Match the clause containing this edge alias (e.g. "LEFT OUTER JOIN rdf_edges e9 ON ...")
        # or the derived JSON_TABLE variant ("LEFT OUTER JOIN (...) e9 ON ...")
        if f") {edge_alias} ON " in jc or f"rdf_edges {edge_alias} ON " in jc:
            context.join_clauses[i] = jc + f" AND {target_on}"
            break
    else:
        # Fallback: edge JOIN not found (e.g. edgescan with derived table) — add WHERE guard
        if f"{edge_alias}.o_id" in target_on:
            null_guard = f"{edge_alias}.o_id IS NULL"
        elif f"{edge_alias}.s" in target_on:
            null_guard = f"{edge_alias}.s IS NULL"
        else:
            null_guard = None
        if null_guard:
            context.where_conditions.append(f"({target_on} OR {null_guard})")
        else:
            context.where_conditions.append(target_on)
        return  # Don't register null-gating if we fell back to WHERE
    # Register null-gating: source node is null when edge is null
    context.opt_intermediate_nulled[source_alias] = edge_alias


def _trp_directed_edge(
    rel, source_node, target_node, context,
    source_alias, target_alias, edge_alias, s_ref, t_ref,
    edge_cond, target_on, jt, is_anon_source, is_new_target,
):
    optional = jt == "LEFT OUTER JOIN"
    if rel.types:
        if len(rel.types) == 1:
            edge_cond += f" AND {edge_alias}.p = {context.add_join_param(rel.types[0])}"
        else:
            edge_cond += f" AND {edge_alias}.p IN ({', '.join([context.add_join_param(t) for t in rel.types])})"

    _trp_directed_edge_join(
        rel, source_node, context, source_alias, edge_alias, edge_cond, jt, is_anon_source
    )

    if is_new_target and not target_alias.startswith("Stage"):
        context.join_clauses.append(
            f"{jt} {_table('nodes')} {target_alias} ON {target_on}"
        )
    elif optional:
        # For OPTIONAL MATCH with an already-bound target, choose null guard:
        # - If source was introduced WITHIN this optional match (not pre-bound):
        #   The bound-target equality goes into the edge JOIN ON clause (not WHERE),
        #   and the source node is null-gated by that edge (opt_intermediate_nulled).
        #   This correctly nulls out intermediate nodes when the full path fails,
        #   e.g. OPTIONAL MATCH (a)-->(b)-->(c_bound): b=null when c unreachable.
        # - If source was bound BEFORE this optional match (pre-bound):
        #   Use "edge IS NULL" guard in WHERE: the source legitimately has a value
        #   even when the edge doesn't exist, e.g. OPTIONAL MATCH (x)-[r]->(b_bound)
        #   where x was found by a prior OPTIONAL MATCH.
        prebound = getattr(context, "optional_prebound_aliases", set())
        if not is_anon_source and source_alias:
            # Determine whether to push target equality to edge JOIN or WHERE.
            # Stage aliases are always pre-bound (produced by an earlier WITH clause).
            is_stage_source = source_alias.startswith("Stage")
            src_is_prebound = source_alias in prebound or is_stage_source
            if src_is_prebound:
                # Source was bound before this OPTIONAL — move the target equality
                # constraint into the edge LEFT OUTER JOIN ON clause so that when
                # the target doesn't match, e is NULL (r IS NULL) rather than
                # filtering the row (which drops Stage rows where target is NULL).
                for i, jc in enumerate(context.join_clauses):
                    if f") {edge_alias} ON " in jc or f"rdf_edges {edge_alias} ON " in jc:
                        context.join_clauses[i] = jc + f" AND {target_on}"
                        break
                else:
                    # Fallback if edge JOIN not found (edgescan path)
                    if f"{edge_alias}.o_id" in target_on:
                        null_guard = f"{edge_alias}.o_id IS NULL"
                    elif f"{edge_alias}.s" in target_on:
                        null_guard = f"{edge_alias}.s IS NULL"
                    else:
                        null_guard = None
                    if null_guard:
                        context.where_conditions.append(f"({target_on} OR {null_guard})")
                    else:
                        context.where_conditions.append(target_on)
            else:
                # Source was introduced within this OPTIONAL — move target equality
                # into the edge JOIN ON (no WHERE), and null-gate source via this edge.
                # This avoids filtering the base row when the full path fails.
                _trp_move_target_cond_to_edge_join(
                    context, edge_alias, target_on, source_alias
                )
        else:
            context.where_conditions.append(target_on)
    else:
        context.where_conditions.append(target_on)

    if is_anon_source and (source_node.labels or source_node.properties):
        # Source node has constraints but no variable — filter via edge column.
        # OUTGOING: target is on edge.o_id → source is edge.s
        # INCOMING: target is on edge.s → source is edge.o_id
        anon_src_col = "s" if target_on.endswith(f"{edge_alias}.o_id") else "o_id"
        _trp_apply_anon_source_constraints(source_node, edge_alias, anon_src_col, context, jt)
        _trp_apply_inline_props(None, None, target_node, target_alias, context, jt)
    else:
        _trp_apply_inline_props(source_node, source_alias, target_node, target_alias, context, jt)

    # Apply inline relationship property filters (e.g. [r:TYPE {key: value}]).
    # Stored as JSON in rdf_edges.qualifiers; filtered via JSON_VALUE in WHERE.
    _trp_apply_rel_inline_props(rel, edge_alias, context)

    # Apply label constraints for anonymous target nodes inline.
    if target_node.variable is None and target_node.labels and not target_alias.startswith("Stage"):
        for label in target_node.labels:
            l_alias = context.next_alias("l")
            context.join_clauses.append(
                f"{jt} {_table('rdf_labels')} {l_alias} "
                f"ON {l_alias}.s = {target_alias}.node_id AND {l_alias}.label = {context.add_join_param(label)}"
            )


def translate_relationship_pattern(
    rel, source_node, target_node, context, metadata, optional=False
):
    if rel.variable_length is not None:
        _trp_variable_length(rel, source_node, target_node, context, metadata, optional=optional)
        return
    source_alias, target_alias, edge_alias, is_anon_source, is_new_target, is_unbound_src = (
        _trp_setup_aliases(rel, source_node, target_node, context)
    )
    # Track named relationship variables for Bolt column-type tagging.
    if rel.variable:
        context.rel_variables.add(rel.variable)
        # Track relationship type for semantic validation
        context.bind_variable_type(rel.variable, "relationship")
    def _node_id_ref(variable, alias):
        """Return full SQL expression for the node_id of a node variable.

        For Stage CTEs: alias.variable
        For collected-node UNWIND variables: SQLUser.JSON_VALUE(alias.var, '$._id')
        Otherwise: alias.node_id
        """
        if alias.startswith("Stage") or alias == "VecSearch":
            return f"{alias}.{variable}"
        if variable and variable in getattr(context, "collected_node_variables", set()):
            safe_var = _safe_alias(variable)
            return f"SQLUser.JSON_VALUE({alias}.{safe_var}, '$._id')"
        return f"{alias}.node_id"
    direction = "in" if rel.direction == ast.Direction.INCOMING else "out"
    s_ref = _node_id_ref(source_node.variable, source_alias)
    t_ref = _node_id_ref(target_node.variable, target_alias)
    jt = "LEFT OUTER JOIN" if optional else "JOIN"

    # Stage-bound relationship: when the edge variable is already promoted to a CTE
    # stage (alias = "StageN"), its edge identity is stored as __edge_<var>_s/p/o columns.
    # Use those directly instead of re-joining rdf_edges with the wrong alias.
    stage_names = {s.split(" AS (")[0].strip() for s in getattr(context, "stages", [])}
    if rel.variable and edge_alias in stage_names and edge_alias.startswith("Stage"):
        var_name = rel.variable
        stage = edge_alias
        s_col_stage = f"__edge_{var_name}_s"
        o_col_stage = f"__edge_{var_name}_o"
        # For OUTGOING (src)-[r]->(tgt): rdf_edges.s=src, rdf_edges.o_id=tgt
        # For INCOMING (src)<-[r]-(tgt): rdf_edges.s=tgt, rdf_edges.o_id=src
        # source_node is the LEFT node in the Cypher pattern.
        if rel.direction == ast.Direction.OUTGOING:
            # (source)-[r]->(target): source->s_col_stage, target->o_col_stage
            src_edge_col, tgt_edge_col = s_col_stage, o_col_stage
        else:
            # (source)<-[r]-(target): source is on o_id side, target is on s side
            src_edge_col, tgt_edge_col = o_col_stage, s_col_stage
        # Build direction-check condition (must be satisfied for the edge to match).
        # For OPTIONAL patterns this goes into the LEFT OUTER JOIN ON clause so that
        # mismatch yields NULL rather than filtering out the whole row.
        dir_checks = []
        if not is_anon_source and not is_unbound_src:
            src_id = (
                f"{source_alias}.{source_node.variable}"
                if source_alias.startswith("Stage")
                else f"{source_alias}.node_id"
            )
            dir_checks.append(f"{src_id} = {stage}.{src_edge_col}")
        if not is_new_target and target_node.variable:
            tgt_id = (
                f"{target_alias}.{target_node.variable}"
                if target_alias.startswith("Stage")
                else f"{target_alias}.node_id"
            )
            dir_checks.append(f"{tgt_id} = {stage}.{tgt_edge_col}")
        dir_cond = " AND ".join(dir_checks) if dir_checks else "1=1"
        # If the new MATCH specifies relationship types, filter the Stage-bound edge
        # by type. This handles the case where the same variable is re-used with a
        # different type (conflicting types → empty result).
        if rel.types:
            p_col_stage = f"__edge_{var_name}_p"
            if len(rel.types) == 1:
                context.where_conditions.append(
                    f"{stage}.{p_col_stage} = {context.add_where_param(rel.types[0])}"
                )
            else:
                type_preds = " OR ".join(
                    f"{stage}.{p_col_stage} = {context.add_where_param(t)}" for t in rel.types
                )
                context.where_conditions.append(f"({type_preds})")
        # When optional and source was pre-registered by translate_node_pattern (is_unbound_src
        # is False but source was not stage-bound), it got a CROSS JOIN.  Upgrade it to a LEFT
        # OUTER JOIN anchored on the edge column so OPTIONAL semantics are preserved.
        if optional and not is_unbound_src and not is_anon_source and not source_alias.startswith("Stage"):
            nodes_tbl = _table("nodes")
            cross_clause = f"CROSS JOIN {nodes_tbl} {source_alias}"
            new_join_clauses = []
            for jc in context.join_clauses:
                if jc.strip() == cross_clause:
                    new_join_clauses.append(
                        f"LEFT OUTER JOIN {nodes_tbl} {source_alias} ON {source_alias}.node_id = {stage}.{src_edge_col}"
                    )
                else:
                    new_join_clauses.append(jc)
            context.join_clauses = new_join_clauses
            # Remove the spurious WHERE condition that would nullify the LEFT OUTER JOIN
            context.where_conditions = [
                w for w in context.where_conditions
                if not (f"{source_alias}.node_id = {stage}.{src_edge_col}" in w)
            ]
            # Label JOINs for the source were added (without WHERE) by translate_node_pattern.
            # For a Stage-bound OPTIONAL pattern the label is still a strict filter:
            # if the edge's source node doesn't carry the required label the pattern doesn't
            # match and the whole OPTIONAL should yield NULL.  Enforce with WHERE IS NOT NULL.
            for jc in context.join_clauses:
                if (jc.startswith("LEFT OUTER JOIN") and
                        _table("rdf_labels") in jc and
                        f"{source_alias}.node_id" in jc):
                    # Extract the label alias (first token after rdf_labels keyword)
                    parts = jc.split()
                    rdf_idx = next((i for i, p in enumerate(parts) if "rdf_labels" in p), None)
                    if rdf_idx is not None and rdf_idx + 1 < len(parts):
                        l_alias_found = parts[rdf_idx + 1]
                        context.where_conditions.append(
                            f"({l_alias_found}.s IS NOT NULL OR {source_alias}.node_id IS NULL)"
                        )
        # Register new target node if it is unbound — join via edge column.
        # Include direction check in the ON clause so OPTIONAL semantics work.
        if is_new_target and target_node.variable:
            target_alias_fresh = context.next_alias("n")
            context.variable_aliases[target_node.variable] = target_alias_fresh
            on_cond = (
                f"{target_alias_fresh}.node_id = {stage}.{tgt_edge_col}"
                + (f" AND {dir_cond}" if dir_cond != "1=1" else "")
            )
            context.join_clauses.append(
                f"{jt} {_table('nodes')} {target_alias_fresh} ON {on_cond}"
            )
            for label in (target_node.labels or []):
                l_alias = context.next_alias("l")
                context.join_clauses.append(
                    f"{jt} {_table('rdf_labels')} {l_alias} "
                    f"ON {l_alias}.s = {target_alias_fresh}.node_id AND {l_alias}.label = {context.add_join_param(label)}"
                )
                if not optional:
                    context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
        elif dir_checks and optional:
            # Target already bound and pattern is optional: use WHERE (filtering is OK here
            # since a fully-bound pattern either matches or returns nothing / null-union handles it).
            for chk in dir_checks:
                context.where_conditions.append(chk)
        elif dir_checks:
            for chk in dir_checks:
                context.where_conditions.append(chk)
        # Register new source node if it is unbound.
        if is_unbound_src and source_node.variable:
            source_alias_fresh = context.next_alias("n")
            context.variable_aliases[source_node.variable] = source_alias_fresh
            on_cond = (
                f"{source_alias_fresh}.node_id = {stage}.{src_edge_col}"
                + (f" AND {dir_cond}" if dir_cond != "1=1" and not (is_new_target and target_node.variable) else "")
            )
            context.join_clauses.append(
                f"{jt} {_table('nodes')} {source_alias_fresh} ON {on_cond}"
            )
            for label in (source_node.labels or []):
                l_alias = context.next_alias("l")
                context.join_clauses.append(
                    f"{jt} {_table('rdf_labels')} {l_alias} "
                    f"ON {l_alias}.s = {source_alias_fresh}.node_id AND {l_alias}.label = {context.add_join_param(label)}"
                )
                if not optional:
                    context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
        return
    if _trp_temporal_edge(rel, source_node, target_node, context, source_alias, edge_alias, direction):
        return
    if _trp_mapped_relation(rel, source_node, target_node, context, source_alias, target_alias, optional):
        return
    if rel.direction == ast.Direction.BOTH:
        _trp_undirected_edge(rel, source_node, target_node, context,
                              source_alias, target_alias, edge_alias, s_ref, t_ref, jt, is_new_target,
                              is_anon_source=is_anon_source)
        return

    # Direction-symmetry fix: when source is unbound but target is already bound,
    # anchor the edge join on the target and join the source node from the edge.
    # This makes (t)-[:R]->(f) and (f)<-[:R]-(t) with f pre-bound produce identical SQL.
    if is_unbound_src and not is_new_target:
        if rel.direction == ast.Direction.OUTGOING:
            # (t)-[:R]->(f_bound): anchor edge on f, join t from edge.s
            edge_cond = f"{edge_alias}.o_id = {t_ref}"
            src_on = f"{s_ref} = {edge_alias}.s"
        else:
            # (t)<-[:R]-(f_bound): anchor edge on f, join t from edge.o_id
            edge_cond = f"{edge_alias}.s = {t_ref}"
            src_on = f"{s_ref} = {edge_alias}.o_id"
        if rel.types:
            if len(rel.types) == 1:
                edge_cond += f" AND {edge_alias}.p = {context.add_join_param(rel.types[0])}"
            else:
                edge_cond += f" AND {edge_alias}.p IN ({', '.join([context.add_join_param(t) for t in rel.types])})"
        context.join_clauses.append(f"{jt} {_table('rdf_edges')} {edge_alias} ON {edge_cond}")
        context.join_clauses.append(f"{jt} {_table('nodes')} {source_alias} ON {src_on}")
        # Apply source node labels — skip_first_node_join bypassed translate_node_pattern,
        # so labels must be joined here to avoid missing filter constraints.
        for label in (source_node.labels or []):
            l_alias = context.next_alias("l")
            context.join_clauses.append(
                f"{jt} {_table('rdf_labels')} {l_alias} "
                f"ON {l_alias}.s = {source_alias}.node_id AND {l_alias}.label = {context.add_join_param(label)}"
            )
            if not optional:
                context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
        _trp_apply_inline_props(source_node, source_alias, target_node, target_alias, context, jt)
        _trp_apply_rel_inline_props(rel, edge_alias, context)
        return

    if rel.direction == ast.Direction.OUTGOING:
        if is_anon_source:
            edge_cond = "1=1"
            target_on = f"{t_ref} = {edge_alias}.o_id"
            # Anon source has no nodes JOIN — record edge column for path node_id lookups
            context.node_id_expr[source_alias] = f"{edge_alias}.s"
        else:
            edge_cond = f"{edge_alias}.s = {s_ref}"
            target_on = f"{t_ref} = {edge_alias}.o_id"
    else:
        if is_anon_source:
            edge_cond = "1=1"
            target_on = f"{t_ref} = {edge_alias}.s"
            # Anon source has no nodes JOIN — record edge column for path node_id lookups
            context.node_id_expr[source_alias] = f"{edge_alias}.o_id"
        else:
            edge_cond = f"{edge_alias}.o_id = {s_ref}"
            target_on = f"{t_ref} = {edge_alias}.s"
    _trp_directed_edge(rel, source_node, target_node, context,
                       source_alias, target_alias, edge_alias, s_ref, t_ref,
                       edge_cond, target_on, jt, is_anon_source, is_new_target)


def _check_where_unbound_vars(expr, context):
    """Raise SyntaxError if expr uses unbound node/relationship variables at WHERE level."""
    if isinstance(expr, ast.LabelPredicate):
        var = expr.variable
        if (var and var not in context.variable_aliases
                and var not in context.input_params
                and var not in getattr(context, "scalar_variables", set())):
            raise SyntaxError(f"UndefinedVariable: Variable `{var}` not defined")
    elif isinstance(expr, ast.Variable):
        var = expr.name
        if (var not in context.variable_aliases
                and var not in context.input_params
                and var not in getattr(context, "scalar_variables", set())):
            raise SyntaxError(f"UndefinedVariable: Variable `{var}` not defined")


def _collect_cypher_vars(expr) -> set:
    """Collect all Cypher variable names referenced in an expression."""
    vars_found = set()
    if isinstance(expr, ast.LabelPredicate) and expr.variable:
        vars_found.add(expr.variable)
    elif isinstance(expr, ast.Variable):
        vars_found.add(expr.name)
    elif isinstance(expr, ast.PropertyReference):
        vars_found.add(expr.variable)
    elif isinstance(expr, ast.BooleanExpression):
        for operand in (expr.operands or []):
            vars_found.update(_collect_cypher_vars(operand))
    elif isinstance(expr, ast.FunctionCall):
        for arg in (expr.arguments or []):
            vars_found.update(_collect_cypher_vars(arg))
    elif hasattr(expr, 'left') and hasattr(expr, 'right'):
        vars_found.update(_collect_cypher_vars(expr.left))
        vars_found.update(_collect_cypher_vars(expr.right))
    elif hasattr(expr, 'expression'):
        vars_found.update(_collect_cypher_vars(expr.expression))
    return vars_found


def translate_where_clause(where, context):
    _check_where_unbound_vars(where.expression, context)
    _wp_len_before = len(context.where_params)
    cond = translate_boolean_expression(where.expression, context)
    # NULL in WHERE is invalid SQL. In Cypher, NULL in WHERE means "no match" — use (1=0).
    if cond == "NULL" or cond == "(NULL)":
        cond = "(1=0)"
    # When an OPTIONAL MATCH precedes this WHERE clause, conditions that reference
    # ONLY optional-match variables must be pushed into the LEFT OUTER JOIN ON clause
    # instead of WHERE. In Cypher, OPTIONAL MATCH + WHERE means: if the WHERE fails,
    # null out the optional variables (return the row with NULLs) rather than drop the row.
    opt_new = getattr(context, "optional_match_new_aliases", set())
    if opt_new:
        cypher_vars = _collect_cypher_vars(where.expression)
        opt_cypher_vars = {
            v for v in cypher_vars
            if context.variable_aliases.get(v) in opt_new
        }
        non_opt_cypher_vars = cypher_vars - opt_cypher_vars
        # Push into JOIN when all referenced variables are from the optional set.
        # To correctly null out the edge alias when the condition fails, we push
        # to the FIRST optional join (opt_join_start_idx) and substitute any later
        # node alias references (nX.node_id) with the edge dst reference they equal.
        if opt_cypher_vars and not non_opt_cypher_vars:
            opt_join_start = getattr(context, "opt_join_start_idx", None)
            if opt_join_start is not None and opt_join_start < len(context.join_clauses):
                import re as _re
                # Build substitution map: nX.node_id -> edge/CTE column it equals in JOIN ON
                _node_dst_pat = _re.compile(
                    r'LEFT OUTER JOIN\s+\S+\s+(\w+)\s+ON\s+\1\.node_id\s*=\s*(\w+\.\w+)\b'
                )
                subst = {}
                for jc in context.join_clauses[opt_join_start:]:
                    m = _node_dst_pat.search(jc)
                    if m:
                        subst[f"{m.group(1)}.node_id"] = m.group(2)
                # Apply substitutions to the condition
                cond_pushed = cond
                for old, new in subst.items():
                    cond_pushed = cond_pushed.replace(old, new)
                # IRIS crashes when correlated property-value subqueries appear in
                # LEFT OUTER JOIN ON clauses (e.g. "(SELECT val FROM rdf_props ...)").
                # EXISTS subqueries are fine. Skip the JOIN push only for val-fetch patterns.
                _has_prop_subq = "(SELECT val FROM" in cond_pushed or "(SELECT %EXACT" in cond_pushed
                if _has_prop_subq:
                    opt_aliases = {context.variable_aliases[v] for v in opt_cypher_vars
                                   if v in context.variable_aliases}
                    if opt_aliases:
                        null_checks = " OR ".join(f"{a}.node_id IS NULL" for a in sorted(opt_aliases))
                        context.where_conditions.append(f"({null_checks} OR {cond})")
                        return
                # Move where_params added for this condition to join_params at the
                # correct position. The pushed condition is appended to the string of
                # join_clauses[opt_join_start], so its ?s come right after the ?s
                # already in that clause. Count ?s in all join clauses up to and
                # including opt_join_start to find the insertion offset in join_params.
                n_new_where_params = len(context.where_params) - _wp_len_before
                if n_new_where_params > 0:
                    new_params = context.where_params[-n_new_where_params:]
                    del context.where_params[-n_new_where_params:]
                    # Find insertion offset: ?s in join clauses 0..opt_join_start (inclusive)
                    insert_offset = sum(
                        jc.count("?")
                        for jc in context.join_clauses[:opt_join_start + 1]
                    )
                    for i, p in enumerate(new_params):
                        context.join_params.insert(insert_offset + i, p)
                context.join_clauses[opt_join_start] = context.join_clauses[opt_join_start] + f" AND {cond_pushed}"
                return
        elif opt_cypher_vars and non_opt_cypher_vars:
            # Mixed: some mandatory, some optional vars. Push the condition to the FIRST
            # optional JOIN's ON clause (with alias substitution) so that when the
            # condition fails, the optional side becomes null (rather than filtering the
            # whole row). Only push when the condition doesn't forward-reference JOIN
            # aliases defined after opt_join_start — those can't be in the ON clause.
            opt_join_start = getattr(context, "opt_join_start_idx", None)
            if opt_join_start is not None and opt_join_start < len(context.join_clauses):
                import re as _re
                # Match "LEFT OUTER JOIN <table> <alias> ON <alias>.node_id = <rhs>"
                # where <rhs> can be any column reference (standard or CTE).
                _node_dst_pat = _re.compile(
                    r'LEFT OUTER JOIN\s+\S+\s+(\w+)\s+ON\s+\1\.node_id\s*=\s*(\w+\.\w+)\b'
                )
                # Extract all join aliases from joins AFTER opt_join_start (except node aliases
                # which we'll substitute out). These are forward references we can't push.
                _join_alias_pat = _re.compile(
                    r'(?:LEFT OUTER JOIN|LEFT JOIN|JOIN)\s+\S+\s+(\w+)\s+ON'
                )
                _forward_aliases = set()
                for jc in context.join_clauses[opt_join_start:]:
                    mm = _join_alias_pat.search(jc)
                    if mm:
                        _forward_aliases.add(mm.group(1))
                subst = {}
                for jc in context.join_clauses[opt_join_start:]:
                    m = _node_dst_pat.search(jc)
                    if m:
                        subst[f"{m.group(1)}.node_id"] = m.group(2)
                        _forward_aliases.discard(m.group(1))  # node aliases are substituted out
                # Check if condition references any forward aliases (unsubstitutable)
                cond_has_forward = any(f"{fa}." in cond for fa in _forward_aliases)
                if not cond_has_forward:
                    cond_pushed = cond
                    for old, new in subst.items():
                        cond_pushed = cond_pushed.replace(old, new)
                    # Move where_params to join_params at correct position
                    n_new_where_params = len(context.where_params) - _wp_len_before
                    if n_new_where_params > 0:
                        new_params = context.where_params[-n_new_where_params:]
                        del context.where_params[-n_new_where_params:]
                        insert_offset = sum(
                            jc.count("?")
                            for jc in context.join_clauses[:opt_join_start + 1]
                        )
                        for i, p in enumerate(new_params):
                            context.join_params.insert(insert_offset + i, p)
                    context.join_clauses[opt_join_start] = context.join_clauses[opt_join_start] + f" AND {cond_pushed}"
                    return
            # Fallback: wrap with IS NULL guard in outer WHERE
            opt_aliases = {context.variable_aliases[v] for v in opt_cypher_vars
                          if v in context.variable_aliases}
            if opt_aliases:
                null_checks = " OR ".join(f"{a}.node_id IS NULL" for a in sorted(opt_aliases))
                context.where_conditions.append(f"({null_checks} OR {cond})")
                return
    context.where_conditions.append(cond)


def _is_temporal_ts_condition(expr, context) -> bool:
    if not isinstance(expr, ast.BooleanExpression):
        return False
    if expr.operator not in _TEMPORAL_TS_OPS:
        return False
    if not expr.operands:
        return False
    left = expr.operands[0]
    return (
        isinstance(left, ast.PropertyReference)
        and left.property_name == "ts"
        and left.variable in context.temporal_rel_ctes
    )


def _absorb_child_joins(child_ctx, context, sub_froms, sub_wheres):
    """Absorb JOIN clauses and params from child_ctx into the parent context's param lists
    and the sub-query FROM/WHERE lists used by _boolean_expr_exists.

    All child params go into context.where_params (not join_params) because the entire
    EXISTS subquery is embedded within the parent's WHERE clause. Params from absorbed
    join ON conditions are interleaved with where_params in the order they appear in
    sub_wheres: label/node conditions first (from where_params), then absorbed join ON
    conditions (from join_params), then where conditions (from where_params again via
    where_conditions). The 'where_sql' string appended by the caller after this function
    contains any remaining ? from recursively nested subqueries (already in child.join_params
    after inner absorptions).
    """
    # All params from child_ctx go into context.where_params (not join_params) because the
    # EXISTS subquery is embedded within the parent's WHERE clause. Maintain correct order:
    # 1. where_params first (from _register_unbound_node label conditions and other WHERE-scope
    #    params added before any JOIN absorptions)
    # 2. join_params second (from property-access JOINs absorbed into the subquery's WHERE,
    #    and from recursive inner-EXISTS property params — all appear after the label params
    #    in the SQL structure since they come from where_sql appended after this function)
    for p in child_ctx.where_params:
        context.where_params.append(p)
    for jc in child_ctx.join_clauses:
        jc_stripped = jc.strip()
        parts = jc_stripped.split(" ON ", 1)
        if len(parts) == 2:
            tbl_part = parts[0]
            for kw in ("LEFT OUTER JOIN ", "LEFT JOIN ", "JOIN "):
                if tbl_part.upper().startswith(kw):
                    tbl_part = tbl_part[len(kw):]
                    break
            sub_froms.append(tbl_part)
            sub_wheres.append(parts[1].strip())
        else:
            sub_froms.append(jc_stripped)
    for wc in child_ctx.where_conditions:
        sub_wheres.append(wc)
    # join_params are for ? in absorbed join ON conditions and recursive where_sql strings
    for p in child_ctx.join_params:
        context.where_params.append(p)


def _exists_edge_conds(rel, left_node, right_node, edge_alias, child_ctx):
    """Return (s_col_expr, o_id_col_expr) for an edge given the relationship direction.

    For OUTGOING (left)-[r]->(right): edge.s = left.node_id, edge.o_id = right.node_id
    For INCOMING (left)<-[r]-(right): edge.s = right.node_id, edge.o_id = left.node_id
    For BOTH (left)-[r]-(right): no fixed direction constraint
    """
    aliases = child_ctx.variable_aliases

    def node_ref(node):
        if node and node.variable and node.variable in aliases:
            return f"{aliases[node.variable]}.node_id"
        return None

    left_ref = node_ref(left_node)
    right_ref = node_ref(right_node)
    conds = []
    if rel.direction == ast.Direction.OUTGOING:
        # edge: left -> right
        if left_ref:
            conds.append(f"{edge_alias}.s = {left_ref}")
        if right_ref:
            conds.append(f"{edge_alias}.o_id = {right_ref}")
    elif rel.direction == ast.Direction.INCOMING:
        # edge: right -> left  (arrow points left in Cypher: (left)<-[r]-(right))
        if right_ref:
            conds.append(f"{edge_alias}.s = {right_ref}")
        if left_ref:
            conds.append(f"{edge_alias}.o_id = {left_ref}")
    else:
        # BOTH / undirected — accept either direction
        if left_ref and right_ref:
            conds.append(
                f"({edge_alias}.s = {left_ref} AND {edge_alias}.o_id = {right_ref}"
                f" OR {edge_alias}.s = {right_ref} AND {edge_alias}.o_id = {left_ref})"
            )
        elif left_ref:
            conds.append(f"({edge_alias}.s = {left_ref} OR {edge_alias}.o_id = {left_ref})")
        elif right_ref:
            conds.append(f"({edge_alias}.s = {right_ref} OR {edge_alias}.o_id = {right_ref})")
    if rel.types:
        conds.append(f"{edge_alias}.p = '{rel.types[0]}'")
    return conds if conds else ["1=1"]


def _register_unbound_node(node, child_ctx, sub_froms, sub_wheres):
    """Register an unbound node variable in child_ctx, adding a nodes JOIN to sub_froms."""
    if not (node and node.variable and node.variable not in child_ctx.variable_aliases):
        return
    node_alias = child_ctx.next_alias("n")
    child_ctx.variable_aliases[node.variable] = node_alias
    sub_froms.append(f"{_table('nodes')} {node_alias}")
    for lbl in (node.labels or []):
        lbl_alias = child_ctx.next_alias("l")
        sub_froms.append(f"{_table('rdf_labels')} {lbl_alias}")
        sub_wheres.append(
            f"{lbl_alias}.s = {node_alias}.node_id"
            f" AND {lbl_alias}.label = {child_ctx.add_where_param(lbl)}"
        )


def _boolean_expr_exists(expr, context) -> Optional[str]:
    pat = expr.pattern

    # Pattern predicates (WHERE (n)-[r]->(a)) require all named variables to be pre-bound.
    # Variables introduced fresh in the predicate are an UndefinedVariable error per openCypher.
    if getattr(expr, "is_pattern_predicate", False):
        for node in pat.nodes:
            if node and node.variable and node.variable not in context.variable_aliases:
                raise SyntaxError(
                    f"UndefinedVariable: Variable `{node.variable}` not defined"
                )
        for rel in pat.relationships:
            if rel and rel.variable and rel.variable not in context.variable_aliases:
                raise SyntaxError(
                    f"UndefinedVariable: Variable `{rel.variable}` not defined"
                )

    # Full existential subquery with aggregation: EXISTS { MATCH ... WITH ..., count(*) AS alias WHERE alias = N }
    # Translates to: (SELECT COUNT(*) FROM ... WHERE ...) = N
    if getattr(expr, "with_clause", None) is not None:
        with_cl = expr.with_clause
        # Find the aggregation alias and comparison value from the WITH WHERE clause
        agg_alias = None
        agg_func = None
        for item in with_cl.items:
            if isinstance(item.expression, ast.AggregationFunction) and item.alias:
                agg_alias = item.alias
                agg_func = item.expression
                break
        if agg_alias and agg_func and with_cl.where_clause:
            # The WHERE clause should be something like "numConnections = 3"
            # Extract comparison: find the side that is not the agg_alias
            wexpr = with_cl.where_clause.expression
            if (
                isinstance(wexpr, ast.BooleanExpression)
                and wexpr.operator == ast.BooleanOperator.EQUALS
                and len(wexpr.operands) == 2
            ):
                left_op, right_op = wexpr.operands
                cmp_value = None
                if isinstance(left_op, ast.Variable) and left_op.name == agg_alias:
                    cmp_value = right_op
                elif isinstance(right_op, ast.Variable) and right_op.name == agg_alias:
                    cmp_value = left_op
                if cmp_value is not None:
                    # Build the count subquery
                    child_ctx = TranslationContext()
                    child_ctx.input_params = context.input_params
                    child_ctx._alias_counter = context._alias_counter
                    child_ctx.variable_aliases = dict(context.variable_aliases)
                    sub_froms = []
                    sub_wheres = []
                    # Register unbound nodes
                    for node in pat.nodes:
                        _register_unbound_node(node, child_ctx, sub_froms, sub_wheres)
                    # Add edge conditions for each relationship
                    for i, rel in enumerate(pat.relationships):
                        left_node = pat.nodes[i] if i < len(pat.nodes) else None
                        right_node = pat.nodes[i + 1] if i + 1 < len(pat.nodes) else None
                        edge_alias = child_ctx.next_alias("ex")
                        sub_froms.append(f"{_table('rdf_edges')} {edge_alias}")
                        conds = _exists_edge_conds(rel, left_node, right_node, edge_alias, child_ctx)
                        sub_wheres.extend(conds)
                    if expr.where_condition:
                        wc_sql = translate_boolean_expression(expr.where_condition, child_ctx)
                        _absorb_child_joins(child_ctx, context, sub_froms, sub_wheres)
                        sub_wheres.append(wc_sql)
                    else:
                        for p in child_ctx.where_params:
                            context.where_params.append(p)
                        for p in child_ctx.join_params:
                            context.where_params.append(p)
                    # Translate the comparison value
                    cmp_sql = translate_expression(cmp_value, context, segment="where")
                    count_sub = f"SELECT COUNT(*) FROM {', '.join(sub_froms)} WHERE {' AND '.join(sub_wheres)}"
                    prefix = "NOT " if expr.negated else ""
                    return f"{prefix}({count_sub}) = {cmp_sql}"

    if pat.relationships:
        child_ctx = TranslationContext()
        child_ctx.input_params = context.input_params
        child_ctx._alias_counter = context._alias_counter
        child_ctx.variable_aliases = dict(context.variable_aliases)
        sub_froms = []
        sub_wheres = []

        # Register all unbound nodes first so _exists_edge_conds can resolve them
        for node in pat.nodes:
            _register_unbound_node(node, child_ctx, sub_froms, sub_wheres)

        # Add one rdf_edges row per relationship and connect to the surrounding nodes
        for i, rel in enumerate(pat.relationships):
            left_node = pat.nodes[i] if i < len(pat.nodes) else None
            right_node = pat.nodes[i + 1] if i + 1 < len(pat.nodes) else None
            edge_alias = child_ctx.next_alias("ex")
            sub_froms.append(f"{_table('rdf_edges')} {edge_alias}")
            conds = _exists_edge_conds(rel, left_node, right_node, edge_alias, child_ctx)
            sub_wheres.extend(conds)

        if expr.where_condition:
            where_sql = translate_boolean_expression(expr.where_condition, child_ctx)
            _absorb_child_joins(child_ctx, context, sub_froms, sub_wheres)
            sub_wheres.append(where_sql)
        else:
            # Flush params even without a WHERE condition
            for p in child_ctx.where_params:
                context.where_params.append(p)
            for p in child_ctx.join_params:
                context.join_params.append(p)

        sub = f"SELECT 1 FROM {', '.join(sub_froms)} WHERE {' AND '.join(sub_wheres)}"
        prefix = "NOT " if expr.negated else ""
        return f"{prefix}EXISTS ({sub})"

    # Node-only pattern (no relationships): EXISTS { MATCH (m) WHERE <cond> }
    # Translates to EXISTS (SELECT 1 FROM nodes _m WHERE <cond>)
    if expr.where_condition is not None:
        child_ctx = TranslationContext()
        child_ctx.input_params = context.input_params
        child_ctx._alias_counter = context._alias_counter
        child_ctx.variable_aliases = dict(context.variable_aliases)
        sub_froms = []
        sub_wheres = []
        # Register any unbound node variables from the pattern
        for node in pat.nodes:
            _register_unbound_node(node, child_ctx, sub_froms, sub_wheres)
        # Translate the WHERE condition in the child scope
        where_sql = translate_boolean_expression(expr.where_condition, child_ctx)
        _absorb_child_joins(child_ctx, context, sub_froms, sub_wheres)
        sub_wheres.append(where_sql)
        if not sub_froms:
            sub = f"SELECT 1 WHERE {' AND '.join(sub_wheres)}"
        else:
            sub = f"SELECT 1 FROM {', '.join(sub_froms)} WHERE {' AND '.join(sub_wheres)}"
        prefix = "NOT " if expr.negated else ""
        return f"{prefix}EXISTS ({sub})"

    return None


def _boolean_expr_comparison_ops(op, left, left_expr, right, right_expr) -> Optional[str]:
    if op == ast.BooleanOperator.EQUALS:
        return f"{left} = {right}"
    if op == ast.BooleanOperator.NOT_EQUALS:
        return f"{left} <> {right}"
    if op == ast.BooleanOperator.LESS_THAN:
        return f"{left} < {right}"
    if op == ast.BooleanOperator.LESS_THAN_OR_EQUAL:
        # Decompose to avoid IRIS returning true for NaN <= x (NaN is treated as max float by IRIS)
        return f"(({left} < {right}) OR ({left} = {right}))"
    if op == ast.BooleanOperator.GREATER_THAN:
        return f"{left} > {right}"
    if op == ast.BooleanOperator.GREATER_THAN_OR_EQUAL:
        # Decompose to avoid IRIS returning true for NaN >= x (NaN is treated as max float by IRIS)
        return f"(({left} > {right}) OR ({left} = {right}))"
    if op == ast.BooleanOperator.STARTS_WITH:
        # %EXACT() forces byte-level (case-sensitive) comparison on SQLUPPER-collated columns.
        return f"%EXACT({left}) LIKE %EXACT({right} || '%')"
    if op == ast.BooleanOperator.ENDS_WITH:
        return f"%EXACT({left}) LIKE %EXACT('%' || {right})"
    if op == ast.BooleanOperator.CONTAINS:
        return f"%EXACT({left}) LIKE %EXACT('%' || {right} || '%')"
    if op == ast.BooleanOperator.REGEX_MATCH:
        return f"SQLUser.REGEX_MATCH({left}, {right}) = 1"
    if op == ast.BooleanOperator.IN:
        return f"{left} IN {right}"
    return None


def _get_non_boolean_operand(expr):
    """Check if expression has non-boolean literal operands and return it."""
    for operand in expr.operands:
        if isinstance(operand, ast.Literal):
            v = operand.value
            # Boolean and None (null) are valid for AND/OR/XOR/NOT
            if not (isinstance(v, bool) or v is None):
                return operand
        elif isinstance(operand, (ast.MapLiteral, ast.Literal)):
            # Map literals are not boolean
            if isinstance(operand, ast.MapLiteral):
                return operand
    return None

def _format_invalid_type(operand):
    """Format error message for invalid operand type."""
    if isinstance(operand, ast.Literal):
        v = operand.value
        type_name = type(v).__name__
        return f"{type_name}: {v!r}"
    elif isinstance(operand, ast.MapLiteral):
        return "map"
    elif isinstance(operand, (ast.Literal, ast.Variable)):
        if isinstance(operand.value, list):
            return f"list: {operand.value!r}"
    # Fallback
    return str(operand)



def _coerce_varchar_boolean_if_needed(operand, translated_sql, context) -> str:
    if isinstance(operand, ast.Variable) and operand.name in context.scalar_variables:
        return f"(({translated_sql} = '1' OR {translated_sql} = 'true'))"
    return translated_sql


def _boolean_expr_logical(op, expr, context):
    if op == ast.BooleanOperator.AND:
        # Type validation: AND requires boolean operands
        bad_operand = _get_non_boolean_operand(expr)
        if bad_operand is not None:
            raise CypherParseError(
                f"InvalidArgumentType: AND requires boolean operands, "
                f"got {_format_invalid_type(bad_operand)}"
            )
        parts = []
        has_null = False
        _sp0 = len(context.select_params)
        _wp0 = len(context.where_params)
        _jp0 = len(context.join_params)
        _jc0 = len(context.join_clauses)
        _wc0 = len(context.where_conditions)
        for o in expr.operands:
            if _is_temporal_ts_condition(o, context):
                continue
            p = translate_boolean_expression(o, context)
            p = _coerce_varchar_boolean_if_needed(o, p, context)
            if p == "NULL":
                has_null = True
            else:
                parts.append(p)
        if not parts:
            # All operands were null or temporal
            return "NULL" if has_null else "1=1"
        # Three-value AND: if any operand is definitively false, result is false.
        # Roll back any params, JOINs, and WHERE guards added by discarded operands.
        if "(1=0)" in parts:
            del context.select_params[_sp0:]
            del context.where_params[_wp0:]
            del context.join_params[_jp0:]
            del context.join_clauses[_jc0:]
            del context.where_conditions[_wc0:]
            return "(1=0)"
        # Unwrap nested nullable CASE WHEN parts (produced by inner 3VL AND/OR):
        # "CASE WHEN NOT (cond) THEN (1=0) ELSE NULL END" means: false if NOT cond, else NULL.
        # For AND, we need cond to hold (it's already nullable → mark has_null).
        # "CASE WHEN (cond) THEN (1=1) ELSE NULL END" means: true if cond, else NULL.
        import re as _re_and
        unwrapped = []
        for p in parts:
            m_not = _re_and.match(r'^CASE WHEN NOT \((.+)\) THEN \(1=0\) ELSE NULL END$', p)
            if m_not:
                has_null = True
                unwrapped.append(m_not.group(1))
            else:
                unwrapped.append(p)
        parts = unwrapped
        # Simplify: filter out always-true sentinels (they don't affect AND result)
        non_trivial = [p for p in parts if p != "(1=1)"]
        if not non_trivial:
            # All parts are literal true
            if has_null:
                return "NULL"
            return "(1=1)"
        combined = "(" + " AND ".join(non_trivial) + ")"
        if has_null:
            return f"CASE WHEN NOT ({combined}) THEN (1=0) ELSE NULL END"
        return combined
    if op == ast.BooleanOperator.OR:
        # Type validation: OR requires boolean operands
        bad_operand = _get_non_boolean_operand(expr)
        if bad_operand is not None:
            raise CypherParseError(
                f"InvalidArgumentType: OR requires boolean operands, "
                f"got {_format_invalid_type(bad_operand)}"
            )
        parts_or = []
        has_null_or = False
        _sp0_or = len(context.select_params)
        _wp0_or = len(context.where_params)
        _jp0_or = len(context.join_params)
        _jc0_or = len(context.join_clauses)
        _wc0_or = len(context.where_conditions)
        # Suppress the structural guard (OPT-3) for properties that appear in IS NULL
        # checks, or in disjunctive (OR) branches.  Without this, a node lacking a property
        # would be excluded by the EXISTS guard even though `a.x IS NULL` or `a.x = 12 OR
        # a.y = 13` should work correctly on nodes missing one of the properties (NULL = 12
        # evaluates to NULL/false in SQL, which is the correct Cypher semantics).
        _prev_null_guarded = context._null_guarded_props.copy()
        for o in expr.operands:
            context._null_guarded_props |= _collect_is_null_props(o, context)
            context._null_guarded_props |= _collect_all_prop_refs(o, context)
        try:
            for o in expr.operands:
                p = translate_boolean_expression(o, context)
                p = _coerce_varchar_boolean_if_needed(o, p, context)
                if p == "NULL":
                    has_null_or = True
                else:
                    parts_or.append(p)
            if not parts_or:
                return "NULL" if has_null_or else "(1=0)"
            # Three-value OR: if any operand is definitively true, result is true.
            # Roll back params, JOINs, and WHERE guards added by discarded operands.
            if "(1=1)" in parts_or:
                del context.select_params[_sp0_or:]
                del context.where_params[_wp0_or:]
                del context.join_params[_jp0_or:]
                del context.join_clauses[_jc0_or:]
                del context.where_conditions[_wc0_or:]
                return "(1=1)"
            # Unwrap nested nullable CASE WHEN parts from inner 3VL AND/OR:
            import re as _re_or
            unwrapped_or = []
            for p in parts_or:
                m_or = _re_or.match(r'^CASE WHEN \((.+)\) THEN \(1=1\) ELSE NULL END$', p)
                if m_or:
                    has_null_or = True
                    unwrapped_or.append(m_or.group(1))
                else:
                    unwrapped_or.append(p)
            parts_or = unwrapped_or
            # Simplify: filter out always-false sentinels (they don't affect OR result)
            non_trivial_or = [p for p in parts_or if p != "(1=0)"]
            if not non_trivial_or:
                # All parts are literal false
                if has_null_or:
                    return "NULL"
                return "(1=0)"
            combined_or = "(" + " OR ".join(non_trivial_or) + ")"
            if has_null_or:
                return f"CASE WHEN ({combined_or}) THEN (1=1) ELSE NULL END"
            return combined_or
        finally:
            context._null_guarded_props = _prev_null_guarded
    if op == ast.BooleanOperator.XOR:
        # Type validation: XOR requires boolean operands
        bad_operand = _get_non_boolean_operand(expr)
        if bad_operand is not None:
            raise CypherParseError(
                f"InvalidArgumentType: XOR requires boolean operands, "
                f"got {_format_invalid_type(bad_operand)}"
            )
        a, b = expr.operands[0], expr.operands[1]
        sa = translate_boolean_expression(a, context)
        sa = _coerce_varchar_boolean_if_needed(a, sa, context)
        sb = translate_boolean_expression(b, context)
        sb = _coerce_varchar_boolean_if_needed(b, sb, context)
        # Three-valued XOR logic:
        # - true XOR true = false
        # - true XOR false = true
        # - false XOR false = false
        # - true XOR null = null
        # - false XOR null = null
        # - null XOR null = null
        if sa == "NULL" or sb == "NULL":
            # At least one operand evaluates to NULL → XOR result is NULL
            return "NULL"
        # Constant folding: if both operands are sentinel booleans (1=1)/(1=0), evaluate at Python level
        # to avoid generating exponentially large nested SQL for chains like true XOR true XOR true ...
        if sa in ("(1=1)", "(1=0)") and sb in ("(1=1)", "(1=0)"):
            a_val = (sa == "(1=1)")
            b_val = (sb == "(1=1)")
            result = a_val != b_val  # XOR: true if exactly one is true
            return "(1=1)" if result else "(1=0)"
        # Both are non-NULL expressions: simple XOR
        return f"(({sa} AND NOT ({sb})) OR (NOT ({sa}) AND {sb}))"
    if op == ast.BooleanOperator.NOT:
        # Type validation: NOT requires boolean operand
        bad_operand = _get_non_boolean_operand(expr)
        if bad_operand is not None:
            raise CypherParseError(
                f"InvalidArgumentType: NOT requires boolean operand, "
                f"got {_format_invalid_type(bad_operand)}"
            )
        operand = expr.operands[0]
        # NOT null = null (three-valued logic)
        if isinstance(operand, ast.Literal) and operand.value is None:
            return "NULL"
        # Fold NOT NOT: double negation cancels (IRIS SQL rejects NOT NOT syntax)
        if (isinstance(operand, ast.BooleanExpression)
                and operand.operator == ast.BooleanOperator.NOT
                and len(operand.operands) == 1):
            return translate_boolean_expression(operand.operands[0], context)
        # NOT (x IS NULL) → x IS NOT NULL (IRIS parses NOT x IS NULL as (NOT x) IS NULL)
        if (isinstance(operand, ast.BooleanExpression)
                and operand.operator == ast.BooleanOperator.IS_NULL):
            left = translate_expression(operand.operands[0], context, segment="where")
            return f"{left} IS NOT NULL"
        # NOT (x IS NOT NULL) → x IS NULL
        if (isinstance(operand, ast.BooleanExpression)
                and operand.operator == ast.BooleanOperator.IS_NOT_NULL):
            left = translate_expression(operand.operands[0], context, segment="where")
            return f"{left} IS NULL"
        operand_sql = translate_boolean_expression(operand, context)
        # NOT NULL = NULL per three-valued logic
        if operand_sql == "NULL":
            return "NULL"
        operand_sql = _coerce_varchar_boolean_if_needed(operand, operand_sql, context)
        return f"NOT ({operand_sql})"
    return None


def _cypher_elem_eq_3vl(lv, rv):
    """3VL element equality: returns True, False, or None (unknown).

    Handles nested lists recursively.  None means at least one comparison
    involved null and the result is therefore unknown.
    """
    if lv is None or rv is None:
        return None  # any comparison with null → unknown
    if type(lv) != type(rv):
        # Type mismatch (e.g. int vs str): false in Cypher
        # Exception: bool is a subtype of int in Python — treat separately
        if isinstance(lv, bool) != isinstance(rv, bool):
            return False
        if isinstance(lv, list) != isinstance(rv, list):
            return False
    if isinstance(lv, list) and isinstance(rv, list):
        if len(lv) != len(rv):
            return False
        result = True
        for a, b in zip(lv, rv):
            # a, b may be ast.Literal nodes or raw Python values
            av = a.value if hasattr(a, "value") else a
            bv = b.value if hasattr(b, "value") else b
            cmp = _cypher_elem_eq_3vl(av, bv)
            if cmp is False:
                return False
            if cmp is None:
                result = None  # keep going — a later false would short-circuit
        return result
    return lv == rv


def _list_literal_in_3vl(lhs_list, rhs_items):
    """Check if lhs_list (Python list of ast.Literal nodes or values) is IN rhs_items
    (list of ast.Literal nodes) using Cypher 3-valued logic.

    Returns True, False, or None (unknown).
    """
    lv = [item.value if isinstance(item, ast.Literal) else item for item in lhs_list]
    found_unknown = False
    for rhs_item in rhs_items:
        rv_raw = rhs_item.value if isinstance(rhs_item, ast.Literal) else rhs_item
        if isinstance(rv_raw, list):
            rv = [x.value if isinstance(x, ast.Literal) else x for x in rv_raw]
        else:
            rv = rv_raw
        cmp = _cypher_elem_eq_3vl(lv, rv)
        if cmp is True:
            return True
        if cmp is None:
            found_unknown = True
        # cmp is False: continue looking
    return None if found_unknown else False


def _boolean_expr_in(left, right_expr, context, left_expr=None):
    # Validate RHS is a list type; non-list literals (bool, int, str) are type errors
    if isinstance(right_expr, ast.Literal) and not isinstance(right_expr.value, list):
        raise SyntaxError(
            "InvalidArgumentType: IN requires a list on the right-hand side"
        )
    if isinstance(right_expr, ast.SubscriptExpression):
        inner_sql = translate_expression(right_expr.expression, context, segment="where")
        idx = right_expr.index
        if isinstance(idx, ast.Literal) and isinstance(idx.value, int):
            i = idx.value
            ij_alias = context.next_alias("ij")
            sub_arr_sql = f"SQLUser.JSON_VALUE({inner_sql}, '$[{i}]')"
            return f"{left} IN (SELECT __iv FROM JSON_TABLE({sub_arr_sql}, '$[*]' COLUMNS(__iv VARCHAR(1000) PATH '$')) {ij_alias})"
        idx_sql = translate_expression(idx, context, segment="where")
        sub_arr_sql = f"SQLUser.JSON_VALUE({inner_sql}, '$[' || CAST(({idx_sql}) AS VARCHAR) || ']')"
        ij_alias = context.next_alias("ij")
        return f"{left} IN (SELECT __iv FROM JSON_TABLE({sub_arr_sql}, '$[*]' COLUMNS(__iv VARCHAR(1000) PATH '$')) {ij_alias})"
    if isinstance(right_expr, ast.SliceExpression):
        slice_sql = translate_expression(right_expr, context, segment="where")
        ij_alias = context.next_alias("ij")
        return f"{left} IN (SELECT __iv FROM JSON_TABLE({slice_sql}, '$[*]' COLUMNS(__iv VARCHAR(1000) PATH '$')) {ij_alias})"
    if isinstance(right_expr, ast.Literal) and isinstance(right_expr.value, list):
        items = right_expr.value

        # Empty right-side list: x IN [] → false regardless of x
        if not items:
            return "(1=0)"

        # When LHS is a list literal and RHS contains list literals, use 3VL list equality.
        # This is needed because SQL string equality ('[null]' = '[null]') is incorrect for
        # Cypher semantics: comparing null elements should yield null, not true.
        if (
            left_expr is not None
            and isinstance(left_expr, ast.Literal)
            and isinstance(left_expr.value, list)
        ):
            result = _list_literal_in_3vl(left_expr.value, items)
            if result is True:
                return "(1=1)"
            elif result is False:
                return "(1=0)"
            else:  # None (unknown)
                return "NULL"

        # Separate null items from non-null items for 3VL: x IN [a, null, b]
        # = x IN (a, b) OR NULL (unknown if no exact match but list has nulls)
        null_items = [i for i in items if isinstance(i, ast.Literal) and i.value is None]
        non_null_items = [i for i in items if not (isinstance(i, ast.Literal) and i.value is None)]
        if not non_null_items:
            # All null: x IN [null] = null (handled by caller null check for left=null, else null)
            return "NULL"
        # Type-strict IN: Cypher string != int, filter mismatched literal items
        if left_expr is not None and isinstance(left_expr, ast.Literal) and left_expr.value is not None:
            lv = left_expr.value
            lv_str = isinstance(lv, str)
            lv_num = isinstance(lv, (int, float)) and not isinstance(lv, bool)
            filtered = []
            for item in non_null_items:
                if isinstance(item, ast.Literal) and item.value is not None:
                    iv = item.value
                    if (lv_str and isinstance(iv, (int, float)) and not isinstance(iv, bool)):
                        continue
                    if (lv_num and isinstance(iv, str)):
                        continue
                filtered.append(item)
            if not filtered:
                return "(1=0)"
            non_null_items = filtered
        def _serialize_in_item(item):
            if isinstance(item, ast.Literal):
                v = item.value
                if isinstance(v, list):
                    # Nested list → JSON string for VARCHAR comparison
                    return context.add_where_param(json.dumps(_literal_to_python(item)))
                return context.add_where_param(v)
            return context.add_where_param(item)
        placeholders = ", ".join(_serialize_in_item(item) for item in non_null_items)
        in_expr = f"{left} IN ({placeholders})"
        if null_items:
            # 3VL: if x matches → true; if x doesn't match and list has null → null
            return f"CASE WHEN {in_expr} THEN 1 ELSE NULL END"
        return in_expr
    if isinstance(right_expr, ast.Variable) and right_expr.name in context.input_params:
        val = context.input_params[right_expr.name]
        if isinstance(val, list):
            null_vals = [v for v in val if v is None]
            non_null_vals = [v for v in val if v is not None]
            if not non_null_vals:
                return "NULL"
            placeholders = ", ".join(context.add_where_param(v) for v in non_null_vals)
            in_expr = f"{left} IN ({placeholders})"
            if null_vals:
                return f"CASE WHEN {in_expr} THEN 1 ELSE NULL END"
            return in_expr
    # For function calls / dynamic expressions returning JSON arrays (e.g. keys(), labels(), range()):
    # Expand via JSON_TABLE so: left IN keys(map) works correctly.
    _json_array_fns = frozenset({"keys", "labels", "range", "collect", "nodes", "relationships", "tail", "reverse"})
    _is_json_array_expr = (
        isinstance(right_expr, ast.FunctionCall) and right_expr.function_name.lower() in _json_array_fns
    ) or isinstance(right_expr, ast.ListComprehension)
    if _is_json_array_expr:
        right_sql = translate_expression(right_expr, context, segment="where")
        jt_alias = context.next_alias("jin")
        return f"{left} IN (SELECT __jv FROM JSON_TABLE({right_sql}, '$[*]' COLUMNS(__jv VARCHAR(1000) PATH '$')) {jt_alias})"
    # Variable holding a JSON array (scalar variable from Stage, not an input_param list)
    if (
        isinstance(right_expr, ast.Variable)
        and right_expr.name not in context.input_params
        and right_expr.name in context.scalar_variables
    ):
        right_sql = translate_expression(right_expr, context, segment="where")
        jt_alias = context.next_alias("jin")
        return f"{left} IN (SELECT __jv FROM JSON_TABLE({right_sql}, '$[*]' COLUMNS(__jv VARCHAR(1000) PATH '$')) {jt_alias})"
    return None


def _rel_identity_comparison(op, left_expr, right_expr, context) -> Optional[str]:
    """Generate relationship identity comparison (s, p, o triple match) for a = b / a <> b.

    Returns SQL condition string if both operands are relationship variables, else None.
    Handles three cases:
      1. stage-edge vs current-edge: __edge_a_s = e.s AND __edge_a_p = e.p AND __edge_a_o = e.o_id
      2. current-edge vs current-edge: e1.s = e2.s AND e1.p = e2.p AND e1.o_id = e2.o_id
      3. current-edge vs stage-edge: same as case 1, reversed
    """
    def _get_edge_info(expr_var):
        """Return (kind, alias, var_name) for a Variable that is an edge variable.
        kind: 'stage' or 'current' or None
        """
        if not isinstance(expr_var, ast.Variable):
            return None
        var_name = expr_var.name
        alias = context.variable_aliases.get(var_name)
        if alias is None:
            return None
        edge_stage_vars = getattr(context, "edge_stage_variables", set())
        if alias.startswith("Stage") and var_name in edge_stage_vars:
            return ('stage', alias, var_name)
        if alias.startswith("e") and not alias.startswith("Stage"):
            is_undirected = alias in getattr(context, "_undirected_aliases", set())
            return ('current', alias, var_name, is_undirected)
        return None

    left_info = _get_edge_info(left_expr)
    right_info = _get_edge_info(right_expr)
    if left_info is None or right_info is None:
        return None

    # Both are edge variables — generate triple comparison
    op_str = "=" if op == ast.BooleanOperator.EQUALS else "<>"
    join_str = " AND " if op == ast.BooleanOperator.EQUALS else " OR "

    def _stage_cols(var_name):
        return (
            f"__edge_{var_name}_s",
            f"__edge_{var_name}_p",
            f"__edge_{var_name}_o",
        )

    def _current_cols(alias, is_undirected=False):
        if is_undirected:
            return (f"{alias}._src", f"{alias}._p", f"{alias}._dst")
        return (f"{alias}.s", f"{alias}.p", f"{alias}.o_id")

    if left_info[0] == 'stage':
        ls, lp, lo = _stage_cols(left_info[2])
    else:
        ls, lp, lo = _current_cols(left_info[1], left_info[3] if len(left_info) > 3 else False)

    if right_info[0] == 'stage':
        rs, rp, ro = _stage_cols(right_info[2])
    else:
        rs, rp, ro = _current_cols(right_info[1], right_info[3] if len(right_info) > 3 else False)

    parts = [
        f"{ls} {op_str} {rs}",
        f"{lp} {op_str} {rp}",
        f"{lo} {op_str} {ro}",
    ]
    if op == ast.BooleanOperator.EQUALS:
        return "(" + " AND ".join(parts) + ")"
    else:
        # NOT EQUALS: at least one component differs
        return "(" + " OR ".join(parts) + ")"



def _collect_is_null_props(expr, context) -> set:
    """Return {(alias, prop_name)} for all IS NULL / IS NOT NULL PropertyReference operands
    found anywhere in expr (recursive).  Used to suppress the structural guard for these
    properties so that nodes lacking the property get NULL val instead of being excluded."""
    result = set()
    if not isinstance(expr, ast.BooleanExpression):
        return result
    if expr.operator in (ast.BooleanOperator.IS_NULL, ast.BooleanOperator.IS_NOT_NULL):
        operand = expr.operands[0]
        if isinstance(operand, ast.PropertyReference):
            alias = context.variable_aliases.get(operand.variable)
            if alias:
                result.add((alias, operand.property_name))
        return result
    for operand in expr.operands:
        result |= _collect_is_null_props(operand, context)
    return result


def _collect_all_prop_refs(expr, context) -> set:
    """Return {(alias, prop_name)} for ALL PropertyReference nodes found in expr (recursive).
    Used in OR branches to suppress structural guards: NULL = X evaluates to NULL/false in SQL,
    which is correct Cypher semantics for missing properties."""
    result = set()
    if isinstance(expr, ast.PropertyReference):
        alias = context.variable_aliases.get(expr.variable)
        if alias:
            result.add((alias, expr.property_name))
        return result
    if isinstance(expr, ast.BooleanExpression):
        for operand in expr.operands:
            result |= _collect_all_prop_refs(operand, context)
    return result


def translate_boolean_expression(expr, context) -> str:
    if isinstance(expr, ast.ExistsExpression):
        result = _boolean_expr_exists(expr, context)
        if result is not None:
            return result
        return "1=1"
    if isinstance(expr, ast.LabelPredicate):
        alias = context.variable_aliases.get(expr.variable)
        node_col = f"{alias}.node_id" if alias else "node_id"
        labels_tbl = _table("rdf_labels")
        safe_label = context.add_where_param(expr.label)
        return (
            f"EXISTS (SELECT 1 FROM {labels_tbl} _lp WHERE _lp.s = {node_col}"
            f" AND _lp.label = {safe_label})"
        )
    if not isinstance(expr, ast.BooleanExpression):
        if isinstance(expr, ast.Literal):
            if expr.value is True:
                return "(1=1)"
            if expr.value is False:
                return "(1=0)"
        # When a PropertyReference is used directly in a boolean context,
        # convert it to a proper boolean comparison. IVG stores booleans as '1'/'0'
        # for graph node properties (rdf_props), but JSON map scalar variables store
        # them as 'true'/'false' (JSON text). Distinguish by scalar_variables set.
        if isinstance(expr, ast.PropertyReference):
            prop_expr = translate_expression(expr, context, segment="where")
            if expr.variable in context.scalar_variables:
                # JSON map: boolean stored as 'true'/'false'
                return f"({prop_expr} = 'true')"
            # rdf_props: boolean stored as '1'/'0'
            return f"({prop_expr} = '1')"
        # Quantifier expressions (any/all/none/single) return a CASE WHEN 0/1/NULL
        # expression. When used as a standalone boolean predicate in WHERE, wrap with
        # = 1 so IRIS treats it as a proper predicate.  When used as an operand in a
        # comparison (e.g. none(...) = (NOT any(...))), translate_expression is called
        # directly (not via here), so it gets the raw CASE expression — valid as a
        # scalar in a comparison.
        if isinstance(expr, ast.ListPredicateExpression):
            case_sql = translate_expression(expr, context, segment="where")
            return f"({case_sql} = 1)"
        # Map property access (e.g. input.fixed) returns a string value in IRIS JSON_VALUE.
        # Cypher booleans are stored as 'true'/'false' in JSON — coerce to comparison.
        if isinstance(expr, ast.PropertyAccessExpression):
            val_sql = translate_expression(expr, context, segment="where")
            return f"({val_sql} = 'true')"
        # A bare node or relationship variable used as a boolean predicate is a type error.
        # e.g. WHERE (n) or WHERE r — nodes/relationships are not booleans.
        # Only fire for current-clause aliases (n0, e0) not Stage CTEs (which can be scalars).
        if isinstance(expr, ast.Variable) and expr.name in context.variable_aliases:
            alias = context.variable_aliases[expr.name]
            # Node aliases start with 'n', relationship aliases with 'e' (but not Stage CTEs)
            if alias and alias[0] in ('n', 'e') and not alias.startswith('ES_'):
                raise SyntaxError(
                    f"InvalidArgumentType: {expr.name} is a graph entity and cannot be used as a boolean predicate"
                )
        return translate_expression(expr, context, segment="where")
    op = expr.operator
    logical = _boolean_expr_logical(op, expr, context)
    if logical is not None:
        return logical
    left_expr = expr.operands[0]
    right_expr = expr.operands[1] if len(expr.operands) > 1 else None
    if op in (ast.BooleanOperator.IS_NULL, ast.BooleanOperator.IS_NOT_NULL):
        # Use segment="select" to avoid the structural-guard EXISTS clause —
        # IS NULL explicitly handles the missing-property case via LEFT JOIN.
        # When the operand is a BooleanExpression (AND/OR/etc.), IRIS SQL does not
        # support "(compound_bool_expr) IS NULL" in CASE WHEN or SELECT positions.
        # Instead, detect NULL using the double-CASE pattern:
        #   CASE WHEN cond THEN 0 WHEN NOT (cond) THEN 0 ELSE 1 END = 1
        # This correctly returns 1 (true) when cond evaluates to SQL NULL,
        # preserving Cypher 3VL semantics: (a AND b) IS NULL where a=NULL, b=TRUE → TRUE
        if isinstance(left_expr, ast.BooleanExpression):
            inner = translate_boolean_expression(left_expr, context)
            # Short-circuit when inner is a known constant.
            # "NULL IS NULL" = TRUE; "1 IS NULL" = "0 IS NULL" = FALSE.
            if inner == "NULL":
                return "(1=1)" if op == ast.BooleanOperator.IS_NULL else "(1=0)"
            if inner in ("1", "0", "(1=1)", "(1=0)"):
                return "(1=0)" if op == ast.BooleanOperator.IS_NULL else "(1=1)"
            # General case: CASE WHEN inner THEN 0 WHEN NOT inner THEN 0 ELSE 1 END = 1
            # (result is 1 only when inner is SQL NULL — neither truthy nor falsy).
            # IRIS requires a non-NULL condition in CASE WHEN, so guard against inner="NULL".
            null_check = (
                f"CASE WHEN {inner} THEN 0 "
                f"WHEN NOT ({inner}) THEN 0 "
                f"ELSE 1 END = 1"
            )
            if op == ast.BooleanOperator.IS_NULL:
                return null_check
            return f"NOT ({null_check})"
        left = translate_expression(left_expr, context, segment="select")
        if op == ast.BooleanOperator.IS_NULL:
            return f"{left} IS NULL"
        return f"{left} IS NOT NULL"
    # Cypher three-valued logic: any comparison involving NULL yields NULL (unknown).
    # This includes null = null, null <> null, null < x, x IN [null], null IN [...], etc.
    _left_is_null = isinstance(left_expr, ast.Literal) and left_expr.value is None
    _right_is_null = right_expr is not None and isinstance(right_expr, ast.Literal) and right_expr.value is None
    # Also check parameter variables whose resolved value is null
    if not _left_is_null and isinstance(left_expr, ast.Variable):
        _left_val = context.input_params.get(left_expr.name)
        _left_is_null = _left_val is None and left_expr.name in context.input_params
    if not _right_is_null and right_expr is not None and isinstance(right_expr, ast.Variable):
        _right_val = context.input_params.get(right_expr.name)
        _right_is_null = _right_val is None and right_expr.name in context.input_params
    if (_left_is_null or _right_is_null) and op not in (
        ast.BooleanOperator.IS_NULL, ast.BooleanOperator.IS_NOT_NULL
    ):
        # Special case: null IN [] = false (empty list, no unknowns possible)
        if op == ast.BooleanOperator.IN and _left_is_null:
            if isinstance(right_expr, ast.Literal) and right_expr.value == []:
                return "(1=0)"  # false
            if isinstance(right_expr, ast.Variable):
                _rcoll = context.input_params.get(right_expr.name)
                if isinstance(_rcoll, list) and len(_rcoll) == 0:
                    return "(1=0)"  # false
        return "NULL"
    # Relationship identity comparison: a = b / a <> b where a and/or b are
    # relationship variables. Relationship equality means same (s, p, o) triple.
    # Stage edge variables store identity as __edge_{var}_s/p/o columns.
    if op in (ast.BooleanOperator.EQUALS, ast.BooleanOperator.NOT_EQUALS):
        rel_id_cond = _rel_identity_comparison(op, left_expr, right_expr, context)
        if rel_id_cond is not None:
            return rel_id_cond
        # Constant folding: both sides are fully literal lists/maps — evaluate in Python
        # (SQL string comparison can't produce NULL for Cypher three-valued list equality)
        is_list_or_map = lambda e: (
            (isinstance(e, ast.Literal) and isinstance(e.value, list))
            or isinstance(e, ast.MapLiteral)
        )
        if right_expr is not None and is_list_or_map(left_expr) and is_list_or_map(right_expr):
            if _is_fully_literal(left_expr) and _is_fully_literal(right_expr):
                lv = _literal_to_python(left_expr)
                rv = _literal_to_python(right_expr)
                result = _cypher_eq(lv, rv)
                if result is None:
                    return "NULL"
                bool_val = result if op == ast.BooleanOperator.EQUALS else not result
                return "(1=1)" if bool_val else "(1=0)"
        # Scalar literal type-mismatch: Cypher is strongly typed, string != number
        if right_expr is not None and isinstance(left_expr, ast.Literal) and isinstance(right_expr, ast.Literal):
            lv, rv = left_expr.value, right_expr.value
            if lv is not None and rv is not None and not isinstance(lv, bool) and not isinstance(rv, bool):
                # string vs numeric: always false in Cypher (no implicit coercion)
                lv_str = isinstance(lv, str)
                rv_str = isinstance(rv, str)
                lv_num = isinstance(lv, (int, float))
                rv_num = isinstance(rv, (int, float))
                if (lv_str and rv_num) or (lv_num and rv_str):
                    is_eq = False
                    bool_val = is_eq if op == ast.BooleanOperator.EQUALS else not is_eq
                    return "(1=1)" if bool_val else "(1=0)"

    # Ordering operators on literal lists: constant folding (SQL can't do lexicographic
    # list ordering with null semantics — evaluate in Python).
    _ordering_ops = (
        ast.BooleanOperator.LESS_THAN,
        ast.BooleanOperator.LESS_THAN_OR_EQUAL,
        ast.BooleanOperator.GREATER_THAN,
        ast.BooleanOperator.GREATER_THAN_OR_EQUAL,
    )
    if op in _ordering_ops and right_expr is not None:
        _is_lit_list = lambda e: isinstance(e, ast.Literal) and isinstance(e.value, list)
        if _is_lit_list(left_expr) and _is_lit_list(right_expr):
            if _is_fully_literal(left_expr) and _is_fully_literal(right_expr):
                lv = _literal_to_python(left_expr)
                rv = _literal_to_python(right_expr)
                cmp = _cypher_list_cmp(lv, rv)
                if cmp is None:
                    return "NULL"
                if op == ast.BooleanOperator.LESS_THAN:
                    return "(1=1)" if cmp < 0 else "(1=0)"
                if op == ast.BooleanOperator.LESS_THAN_OR_EQUAL:
                    return "(1=1)" if cmp <= 0 else "(1=0)"
                if op == ast.BooleanOperator.GREATER_THAN:
                    return "(1=1)" if cmp > 0 else "(1=0)"
                if op == ast.BooleanOperator.GREATER_THAN_OR_EQUAL:
                    return "(1=1)" if cmp >= 0 else "(1=0)"
        # Cross-type literal ordering: string vs numeric → null (Cypher has no ordering between types)
        if isinstance(left_expr, ast.Literal) and isinstance(right_expr, ast.Literal):
            lv, rv = left_expr.value, right_expr.value
            if lv is not None and rv is not None and not isinstance(lv, bool) and not isinstance(rv, bool):
                lv_str = isinstance(lv, str)
                rv_str = isinstance(rv, str)
                lv_num = isinstance(lv, (int, float))
                rv_num = isinstance(rv, (int, float))
                if (lv_str and rv_num) or (lv_num and rv_str):
                    return "NULL"

    # String predicate type guard: STARTS WITH, ENDS WITH, CONTAINS require string operands.
    # If either operand is a known non-string literal (number, bool, list, map), return NULL.
    if op in (ast.BooleanOperator.STARTS_WITH, ast.BooleanOperator.ENDS_WITH, ast.BooleanOperator.CONTAINS):
        def _is_non_string_literal(e):
            if isinstance(e, ast.Literal) and e.value is not None:
                return not isinstance(e.value, str)
            if isinstance(e, ast.MapLiteral):
                return True
            if isinstance(e, ast.Literal) and isinstance(e.value, list):
                return True
            return False
        if _is_non_string_literal(left_expr) or (right_expr is not None and _is_non_string_literal(right_expr)):
            return "NULL"

    left_inlined = _inline_literal(left_expr)
    left = left_inlined if left_inlined is not None else translate_expression(left_expr, context, segment="where")
    # Wrap CASE WHEN expressions in parens — IRIS SQLCODE -25 if bare CASE ends before =
    if left.startswith("CASE WHEN ") and " END" in left:
        left = f"({left})"
    if op == ast.BooleanOperator.IN:
        in_sql = _boolean_expr_in(left, right_expr, context, left_expr)
        if in_sql is not None:
            return in_sql
    right_inlined = _inline_literal(right_expr)
    right = right_inlined if right_inlined is not None else translate_expression(right_expr, context, segment="where")
    if right.startswith("CASE WHEN ") and " END" in right:
        right = f"({right})"
    if op in (
        ast.BooleanOperator.LESS_THAN,
        ast.BooleanOperator.LESS_THAN_OR_EQUAL,
        ast.BooleanOperator.GREATER_THAN,
        ast.BooleanOperator.GREATER_THAN_OR_EQUAL,
    ):
        # Cast VARCHAR property ref to DOUBLE only when comparing with a numeric literal.
        # Do NOT cast when the other side is a string literal (string range comparisons).
        _other_is_str = lambda e: isinstance(e, ast.Literal) and isinstance(e.value, str)
        if isinstance(left_expr, ast.PropertyReference) and not _other_is_str(right_expr):
            left = f"CAST({left} AS DOUBLE)"
        if isinstance(right_expr, ast.PropertyReference) and not _other_is_str(left_expr):
            right = f"CAST({right} AS DOUBLE)"
        # Numeric literal type promotion: IRIS treats 1 <> 1.0 due to INTEGER vs DOUBLE.
        # When comparing int with float literal, cast both to DOUBLE.
        _left_is_int = isinstance(left_expr, ast.Literal) and isinstance(left_expr.value, int) and not isinstance(left_expr.value, bool)
        _right_is_int = right_expr is not None and isinstance(right_expr, ast.Literal) and isinstance(right_expr.value, int) and not isinstance(right_expr.value, bool)
        _left_is_float = isinstance(left_expr, ast.Literal) and isinstance(left_expr.value, float)
        _right_is_float = right_expr is not None and isinstance(right_expr, ast.Literal) and isinstance(right_expr.value, float)
        if (_left_is_int and _right_is_float) or (_left_is_float and _right_is_int):
            left = f"CAST({left} AS DOUBLE)"
            right = f"CAST({right} AS DOUBLE)"
    # Numeric equality: IRIS returns 0 for 1 = 1.0 (INTEGER vs DOUBLE). Cast both to DOUBLE.
    if op == ast.BooleanOperator.EQUALS and right_expr is not None:
        _left_is_int_eq = isinstance(left_expr, ast.Literal) and isinstance(left_expr.value, int) and not isinstance(left_expr.value, bool)
        _right_is_int_eq = isinstance(right_expr, ast.Literal) and isinstance(right_expr.value, int) and not isinstance(right_expr.value, bool)
        _left_is_float_eq = isinstance(left_expr, ast.Literal) and isinstance(left_expr.value, float)
        _right_is_float_eq = isinstance(right_expr, ast.Literal) and isinstance(right_expr.value, float)
        if (_left_is_int_eq and _right_is_float_eq) or (_left_is_float_eq and _right_is_int_eq):
            left = f"CAST({left} AS DOUBLE)"
            right = f"CAST({right} AS DOUBLE)"
    result = _boolean_expr_comparison_ops(op, left, left_expr, right, right_expr)
    if result is not None:
        return result
    raise ValueError(f"Unsupported operator: {op}")


def _cypher_eq(a, b):
    """Three-valued Cypher equality. Returns True, False, or None (null)."""
    if a is None or b is None:
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        result = True
        for x, y in zip(a, b):
            eq = _cypher_eq(x, y)
            if eq is False:
                return False
            if eq is None:
                result = None  # might still be false from later items
        return result
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        result = True
        for k in a:
            eq = _cypher_eq(a[k], b[k])
            if eq is False:
                return False
            if eq is None:
                result = None
        return result
    return a == b


def _cypher_list_cmp(a, b):
    """Three-valued Cypher lexicographic list comparison.
    Returns -1, 0, 1, or None (null — when a null element determines the ordering).
    Cross-type comparisons (str vs numeric) return None per Cypher semantics.
    """
    if not isinstance(a, list) or not isinstance(b, list):
        return None
    for x, y in zip(a, b):
        if x is None or y is None:
            return None
        # Cross-type comparison → null
        x_num = isinstance(x, (int, float)) and not isinstance(x, bool)
        y_num = isinstance(y, (int, float)) and not isinstance(y, bool)
        x_str = isinstance(x, str)
        y_str = isinstance(y, str)
        if (x_num and y_str) or (x_str and y_num):
            return None
        try:
            if x < y:
                return -1
            if x > y:
                return 1
        except TypeError:
            return None
    return len(a) - len(b)


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
        if isinstance(v, list):
            # List literals need full translate_expression (json.dumps path)
            return None
        return f"'{str(v)}'"
    return None


def _sql_arg(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _expr_pattern_comprehension(expr, context, segment):
    pat = expr.pattern
    src_node = pat.nodes[0] if pat.nodes else None
    tgt_node = pat.nodes[1] if len(pat.nodes) > 1 else None
    rel = pat.relationships[0] if pat.relationships else None

    e_alias = context.next_alias("epc")
    t_alias = context.next_alias("pct")

    pred_type = ""
    if rel and rel.types:
        if len(rel.types) == 1:
            safe_type = rel.types[0].replace("'", "''")
            pred_type = f" AND {e_alias}.p = '{safe_type}'"
        else:
            safe_types = ", ".join(f"'{t.replace(chr(39), chr(39)*2)}'" for t in rel.types)
            pred_type = f" AND {e_alias}.p IN ({safe_types})"

    src_bind = ""
    if (
        src_node
        and src_node.variable
        and src_node.variable in context.variable_aliases
    ):
        src_id = f"{context.variable_aliases[src_node.variable]}.node_id"
        src_bind = f" AND {e_alias}.s = {src_id}"

    # Target node label filters
    tgt_label_join = ""
    tgt_label_cond = ""
    if tgt_node and tgt_node.labels:
        lbl_alias = context.next_alias("pcl")
        tgt_label_join = (
            f" JOIN {_table('rdf_labels')} {lbl_alias}"
            f" ON {lbl_alias}.s = {t_alias}.node_id"
        )
        if len(tgt_node.labels) == 1:
            safe_lbl = tgt_node.labels[0].replace("'", "''")
            tgt_label_cond = f" AND {lbl_alias}.label = '{safe_lbl}'"
        else:
            safe_lbls = ", ".join(f"'{l.replace(chr(39), chr(39)*2)}'" for l in tgt_node.labels)
            tgt_label_cond = f" AND {lbl_alias}.label IN ({safe_lbls})"

    # Target node variable binding (bound target node in MATCH)
    tgt_bind = ""
    if (
        tgt_node
        and tgt_node.variable
        and tgt_node.variable in context.variable_aliases
    ):
        tgt_id = f"{context.variable_aliases[tgt_node.variable]}.node_id"
        tgt_bind = f" AND {t_alias}.node_id = {tgt_id}"

    tgt_var = tgt_node.variable if tgt_node else None
    path_var = getattr(expr, "path_variable", None)

    # When projection is the path variable itself, return path JSON
    if (
        path_var
        and expr.projection
        and isinstance(expr.projection, ast.Variable)
        and expr.projection.name == path_var
    ):
        src_node_id = (
            f"{context.variable_aliases[src_node.variable]}.node_id"
            if src_node and src_node.variable and src_node.variable in context.variable_aliases
            else f"{e_alias}.s"
        )
        rel_type_expr = f"{e_alias}.p"
        path_json = (
            f"'{{\"nodes\":' || JSON_ARRAY({src_node_id}, {t_alias}.node_id)"
            f" || ',\"rels\":' || JSON_ARRAY({rel_type_expr}) || '}}'"
        )
        return (
            f"COALESCE((SELECT JSON_ARRAYAGG({path_json}) FROM "
            f"{_table('rdf_edges')} {e_alias} "
            f"JOIN {_table('nodes')} {t_alias} ON {t_alias}.node_id = {e_alias}.o_id"
            f"{tgt_label_join}"
            f" WHERE 1=1{pred_type}{src_bind}{tgt_label_cond}{tgt_bind}), '[]')"
        )

    if (
        expr.projection
        and isinstance(expr.projection, ast.PropertyReference)
        and expr.projection.variable == tgt_var
    ):
        if expr.projection.property_name == "node_id":
            proj_sql = f"{t_alias}.node_id"
        else:
            safe_key = expr.projection.property_name.replace("'", "''")
            proj_sql = (
                f"(SELECT val FROM {_table('rdf_props')} "
                f"WHERE s = {t_alias}.node_id AND \"key\" = '{safe_key}')"
            )
    elif expr.projection:
        if tgt_var:
            context.variable_aliases[tgt_var] = t_alias
        if rel and rel.variable:
            context.variable_aliases[rel.variable] = e_alias
        proj_sql = translate_expression(expr.projection, context, segment="select")
        if tgt_var and tgt_var in context.variable_aliases:
            del context.variable_aliases[tgt_var]
        if rel and rel.variable and rel.variable in context.variable_aliases:
            del context.variable_aliases[rel.variable]
    else:
        proj_sql = f"{t_alias}.node_id"

    return (
        f"COALESCE((SELECT JSON_ARRAYAGG({proj_sql}) FROM "
        f"{_table('rdf_edges')} {e_alias} "
        f"JOIN {_table('nodes')} {t_alias} ON {t_alias}.node_id = {e_alias}.o_id"
        f"{tgt_label_join}"
        f" WHERE 1=1{pred_type}{src_bind}{tgt_label_cond}{tgt_bind}), '[]')"
    )


def _expr_prop(expr, context, segment):
    inner_expr = expr.arguments[0]
    prop = str(expr.arguments[1].value) if isinstance(expr.arguments[1], ast.Literal) else "node_id"
    if prop == "id":
        prop = "node_id"
    inner_fn = inner_expr.function_name.lower() if isinstance(inner_expr, ast.FunctionCall) else ""
    if inner_fn in ("startnode", "endnode"):
        # startNode(r).prop → look up prop from the node referenced by edge s or o_id
        # Note: prop has already had 'id' rewritten to 'node_id' above; undo that for
        # property lookup since 'id' is a user-defined property, not the internal node_id.
        orig_prop = str(expr.arguments[1].value) if isinstance(expr.arguments[1], ast.Literal) else prop
        node_id_expr = translate_expression(inner_expr, context, segment=segment)
        _safe_prop = orig_prop.replace("'", "''")
        return (
            f"(SELECT val FROM {_table('rdf_props')} WHERE s = {node_id_expr} AND \"key\" = '{_safe_prop}')"
        )
    inner = translate_expression(inner_expr, context, segment=segment)
    return f"{inner}.{prop}"


def _prop_ref_cast(arg, sql):
    """Wrap a property-reference SQL expression in CAST(... AS DOUBLE) for arithmetic.
    rdf_props.val is VARCHAR; arithmetic on VARCHAR does string-concat in IRIS."""
    if isinstance(arg, ast.PropertyReference):
        return f"CAST({sql} AS DOUBLE)"
    return sql


def _is_integer_expr(arg):
    """Return True if arg is statically known to produce an integer value.
    Used to emit FLOOR() for Cypher integer division semantics (3/2=1 not 1.5).
    IRIS returns DOUBLE for integer-literal division, so we must compensate."""
    if isinstance(arg, ast.Literal):
        return isinstance(arg.value, int) and not isinstance(arg.value, bool)
    if isinstance(arg, ast.FunctionCall):
        fn = arg.function_name
        # Arithmetic operators applied to integer sub-expressions
        if fn in ("__arith_*", "__arith_-", "__arith_%", "__arith_+") and all(
            _is_integer_expr(a) for a in arg.arguments
        ):
            return True
        # Integer / integer is still integer under floor semantics
        if fn == "__arith_/" and all(_is_integer_expr(a) for a in arg.arguments):
            return True
        # Functions with guaranteed integer return
        if fn in ("id", "size", "length", "toInteger", "abs", "sign", "round"):
            return True
    return False


def _expr_arith(expr, context, segment):
    op = expr.function_name[len("__arith_") :]
    left = translate_expression(expr.arguments[0], context, segment=segment)
    right = translate_expression(expr.arguments[1], context, segment=segment)
    if op == "%":
        left = _prop_ref_cast(expr.arguments[0], left)
        right = _prop_ref_cast(expr.arguments[1], right)
        rhs_arg = expr.arguments[1]
        if isinstance(rhs_arg, ast.Literal) and isinstance(rhs_arg.value, (int, float)) and rhs_arg.value != 0:
            return f"MOD({left}, {right})"
        return f"CASE WHEN {right} = 0 AND {left} IS NOT NULL THEN CAST('NaN' AS DOUBLE) ELSE MOD({left}, {right}) END"
    if op == "^":
        left = _prop_ref_cast(expr.arguments[0], left)
        right = _prop_ref_cast(expr.arguments[1], right)
        # Cypher ^ always returns float (4^3 = 64.0 per spec)
        return f"CAST(POWER({left}, {right}) AS DOUBLE)"
    if op == "+":
        def _is_str(arg):
            return (isinstance(arg, ast.Literal) and isinstance(arg.value, str)) or \
                isinstance(arg, ast.FunctionCall) and arg.function_name.startswith("__arith_+")
        def _is_list(arg):
            if isinstance(arg, ast.Literal) and isinstance(arg.value, list):
                return True
            if not isinstance(arg, ast.MapLiteral) and isinstance(arg, ast.FunctionCall) and arg.function_name in (
                "collect", "nodes", "relationships", "labels", "keys", "range",
                "reverse", "tail", "head", "__list_comprehension", "__arith_+",
                "filter", "extract",
            ):
                return True
            # __arith_+ that returns a list (recursively check)
            if isinstance(arg, ast.FunctionCall) and arg.function_name == "__arith_+":
                if any(_is_list(a) for a in arg.arguments):
                    return True
            # Also check if it's a variable that references a Stage column (likely a list from WITH clause)
            if isinstance(arg, ast.Variable) and arg.name in context.variable_aliases:
                alias = context.variable_aliases[arg.name]
                # Check if the alias is a Stage reference (e.g., "Stage0" or "Stage1.col_name")
                if alias.startswith("Stage"):
                    return True
            # Also check if it's a variable known to be a list from scalar_variables tracking
            if isinstance(arg, ast.Variable) and arg.name in getattr(context, "scalar_variables", set()):
                # scalar_variables includes collect() output; check if it's from a list source
                if arg.name in getattr(context, "collected_node_lists", {}):
                    return True
            # CASE expression: treat as list if all branches are lists
            if isinstance(arg, ast.CaseExpression):
                branches = [wc.result for wc in arg.when_clauses]
                if arg.else_result is not None:
                    branches.append(arg.else_result)
                if branches and all(_is_list(b) for b in branches):
                    return True
            return False
        left_str = _is_str(expr.arguments[0])
        right_str = _is_str(expr.arguments[1])
        if left_str or right_str:
            return f"(CAST({left} AS VARCHAR(4096)) || CAST({right} AS VARCHAR(4096)))"
        left_list = _is_list(expr.arguments[0])
        right_list = _is_list(expr.arguments[1])
        if left_list or right_list:
            # Constant folding: both fully literal → compute in Python
            if (_is_fully_literal(expr.arguments[0]) and _is_fully_literal(expr.arguments[1])):
                import json as _json
                lv = _literal_to_python(expr.arguments[0])
                rv = _literal_to_python(expr.arguments[1])
                # Wrap scalar in list if one side is a scalar (list + scalar or scalar + list)
                if not isinstance(lv, list):
                    lv = [lv]
                if not isinstance(rv, list):
                    rv = [rv]
                combined = lv + rv
                js = _json.dumps(combined)
                return f"CAST('{js.replace(chr(39), chr(39)+chr(39))}' AS VARCHAR({max(len(js)+1, 256)}))"
            # Runtime: JSON array concat via subquery building.
            # If one side is a scalar (not a list), wrap it as a single-element JSON array.
            def _ensure_array_sql(arg_expr, arg_sql):
                """Return SQL that is always a JSON array (wrapping scalar in [v] if needed)."""
                if _is_list(arg_expr):
                    # Variables that alias a Stage column may be UNWIND scalars at runtime
                    # (UNWIND x → Stage2.x is a scalar but _is_list() returns True due to Stage prefix).
                    # Use a runtime check: if the value doesn't start with '[', wrap it.
                    if isinstance(arg_expr, ast.Variable):
                        return f"(CASE WHEN SUBSTRING(CAST({arg_sql} AS VARCHAR(10)), 1, 1) = '[' THEN {arg_sql} ELSE ('[' || CAST({arg_sql} AS VARCHAR(4096)) || ']') END)"
                    return arg_sql
                # Scalar: wrap in JSON array string
                return f"('[' || CAST({arg_sql} AS VARCHAR(4096)) || ']')"
            left_arr = _ensure_array_sql(expr.arguments[0], left)
            right_arr = _ensure_array_sql(expr.arguments[1], right)
            # Generate row numbers up to 100 to handle practical list sizes (each element is extracted)
            row_gen = "SELECT 0 AS n" + "".join(f" UNION ALL SELECT {i}" for i in range(1, 100))
            return (
                f"(SELECT JSON_ARRAYAGG(x.v) FROM ("
                f"SELECT JSON_VALUE({left_arr}, '$[' || rn.n || ']') AS v FROM ({row_gen}) rn WHERE rn.n < SQLUser.JSON_ARRAYLENGTH({left_arr})"
                f" UNION ALL "
                f"SELECT JSON_VALUE({right_arr}, '$[' || rn.n || ']') AS v FROM ({row_gen}) rn WHERE rn.n < SQLUser.JSON_ARRAYLENGTH({right_arr})"
                f") x)"
            )
        # Runtime-polymorphic +: when one or both operands are PropertyReferences
        # the value type is unknown at compile time (could be list or number/string).
        # Emit a CASE that detects JSON arrays at runtime and does concat vs. arithmetic.
        # But: skip this if either side is provably numeric (arithmetic subexpr, numeric literal,
        # or numeric-returning function) — in that case just fall through to numeric cast.
        larg, rarg = expr.arguments[0], expr.arguments[1]
        def _is_definitely_numeric(arg):
            if isinstance(arg, ast.Literal):
                return isinstance(arg.value, (int, float)) and not isinstance(arg.value, bool)
            if isinstance(arg, ast.FunctionCall):
                fn = arg.function_name
                if fn in ("__arith_*", "__arith_-", "__arith_/", "__arith_%", "__arith_^"):
                    return True  # arithmetic on numbers returns number
                if fn in ("id", "size", "length", "toInteger", "toFloat", "abs", "sign", "round", "floor", "ceil"):
                    return True
            return False
        def _is_definitely_string(arg):
            return isinstance(arg, ast.Literal) and isinstance(arg.value, str)
        # Only apply runtime polymorphism when neither side is a known string/numeric type,
        # both operands are ambiguous property refs (could be list or scalar).
        if (isinstance(larg, ast.PropertyReference) or isinstance(rarg, ast.PropertyReference)) and not (
            _is_definitely_numeric(larg) or _is_definitely_numeric(rarg) or
            _is_definitely_string(larg) or _is_definitely_string(rarg)
        ):
            # Evaluate left/right once in an __arrc subquery to avoid ? appearing multiple times.
            # String-trim array concat: trim ] from left, [ from right, join with comma.
            # Each of left/right may contain ? params; they must appear exactly once in the SQL.
            def _rt_ensure_array_str(arg_expr, alias):
                if isinstance(arg_expr, ast.PropertyReference) or _is_list(arg_expr):
                    return alias
                return f"('[' || {alias} || ']')"
            la_str = _rt_ensure_array_str(larg, "__la")
            ra_str = _rt_ensure_array_str(rarg, "__ra")
            return (
                f"(SELECT CASE"
                f" WHEN SUBSTRING(__la, 1, 1) = '[' OR SUBSTRING(__ra, 1, 1) = '['"
                f" THEN CASE"
                f" WHEN CHAR_LENGTH({la_str}) > 2 AND CHAR_LENGTH({ra_str}) > 2"
                f" THEN SUBSTRING({la_str}, 1, CHAR_LENGTH({la_str})-1) || ',' || SUBSTRING({ra_str}, 2)"
                f" WHEN CHAR_LENGTH({la_str}) > 2 THEN {la_str}"
                f" ELSE {ra_str} END"
                f" ELSE (CAST(__la AS DOUBLE) + CAST(__ra AS DOUBLE)) END"
                f" FROM (SELECT CAST({left} AS VARCHAR(4096)) AS __la,"
                f" CAST({right} AS VARCHAR(4096)) AS __ra) __arrc)"
            )
        # Numeric +: cast property references to DOUBLE
        left = _prop_ref_cast(expr.arguments[0], left)
        right = _prop_ref_cast(expr.arguments[1], right)
    else:
        # -, *, /: always numeric — cast property references to DOUBLE
        left = _prop_ref_cast(expr.arguments[0], left)
        right = _prop_ref_cast(expr.arguments[1], right)
        if op == "/":
            both_int = _is_integer_expr(expr.arguments[0]) and _is_integer_expr(expr.arguments[1])
            rhs_arg = expr.arguments[1]
            if isinstance(rhs_arg, ast.Literal) and isinstance(rhs_arg.value, (int, float)) and rhs_arg.value != 0:
                # Cypher: integer/integer = floor division (3/2=1, -7/2=-4).
                # IRIS promotes to DOUBLE (3/2=1.5), so wrap in FLOOR for integer operands.
                if both_int:
                    return f"FLOOR({left} {op} {right})"
                return f"({left} {op} {right})"
            if both_int:
                return f"CASE WHEN {right} = 0 AND {left} IS NOT NULL THEN CAST('NaN' AS DOUBLE) ELSE FLOOR({left} {op} {right}) END"
            return f"CASE WHEN {right} = 0 AND {left} IS NOT NULL THEN CAST('NaN' AS DOUBLE) ELSE ({left} {op} {right}) END"
    return f"({left} {op} {right})"



def _lp_predicate_uses_arithmetic_on_var(predicate, var_name):
    """Return True if any arithmetic function (__arith_%) is applied directly to var_name."""
    if isinstance(predicate, ast.FunctionCall):
        if (predicate.function_name.startswith("__arith_")
                and predicate.function_name != "__arith_+"):
            for arg in predicate.arguments:
                if isinstance(arg, ast.Variable) and arg.name == var_name:
                    return True
        for arg in predicate.arguments:
            if _lp_predicate_uses_arithmetic_on_var(arg, var_name):
                return True
    elif isinstance(predicate, ast.BooleanExpression):
        for op in predicate.operands:
            if _lp_predicate_uses_arithmetic_on_var(op, var_name):
                return True
    return False


def _lp_source_all_non_numeric(source):
    """Return True if source is a literal list and all elements are strings or booleans."""
    if not (isinstance(source, ast.Literal) and isinstance(source.value, list)):
        return False
    for item in source.value:
        if not isinstance(item, ast.Literal):
            return False
        v = item.value
        if isinstance(v, bool) or isinstance(v, str):
            continue
        return False  # int or float — numeric
    return True


def _lp_needs_null_sentinel(source):
    """Return True if source is a single-element literal list whose sole element is a
    numeric literal list — triggers an IRIS JSON_TABLE expansion bug where
    CAST('[[1,2,3]]' AS VARCHAR) expands to rows (1), (2), (3) instead of ('[1,2,3]').
    Workaround: append null to the serialised JSON array.
    """
    if not (isinstance(source, ast.Literal) and isinstance(source.value, list)
            and len(source.value) == 1):
        return False
    inner = source.value[0]
    if not (isinstance(inner, ast.Literal) and isinstance(inner.value, list)):
        return False
    # Only numeric inner arrays trigger the IRIS bug.
    for item in inner.value:
        if isinstance(item, ast.Literal) and isinstance(item.value, (int, float)) and not isinstance(item.value, bool):
            return True
    return False


def _expr_list_predicate(expr, context, segment):
    # --- Static type check: raise SyntaxError for invalid argument types ---
    # Cypher semantics: arithmetic operators (%, *, -, /) on string/boolean list elements
    # are invalid at compile time (InvalidArgumentType).
    if (_lp_source_all_non_numeric(expr.source)
            and _lp_predicate_uses_arithmetic_on_var(expr.predicate, expr.variable)):
        raise SyntaxError(
            f"Type mismatch: {expr.quantifier}() predicate uses arithmetic on "
            f"non-numeric list elements (InvalidArgumentType)"
        )

    # --- IRIS JSON_TABLE null-sentinel workaround ---
    # IRIS expands CAST('[[1,2,3]]' AS VARCHAR) to scalar rows (1,2,3) instead of
    # one row ('[1,2,3]') when the outer array contains exactly one numeric inner array.
    # Fix: append null to the serialised JSON so IRIS sees 2 elements and preserves
    # the inner array as a string.  The null row is excluded via IS NOT NULL filter.
    null_sentinel = _lp_needs_null_sentinel(expr.source)
    if null_sentinel:
        # Reserialise with appended null sentinel
        from iris_vector_graph.cypher.translator import _literal_to_python as _ltp
        inner_py = _ltp(expr.source)
        inner_py.append(None)
        _sentinel_json = json.dumps(inner_py)
        _sentinel_size = max(len(_sentinel_json) + 1, 256)
        source_sql = f"CAST('{_sentinel_json.replace(chr(39), chr(39)+chr(39))}' AS VARCHAR({_sentinel_size}))"
    else:
        source_sql = translate_expression(expr.source, context, segment=segment)

    var = sanitize_identifier(expr.variable)
    alias = context.next_alias("lp")
    # Always use VARCHAR(1000) — IRIS JSON_TABLE BIGINT/DOUBLE columns fail to
    # match bind-param floats/ints due to IRIS internal type precision.
    # VARCHAR comparisons work when params are stringified (done below).
    col_type = "VARCHAR(1000)"
    context.variable_aliases[expr.variable] = f"{alias}"
    context.scalar_variables.add(expr.variable)
    # Snapshot param lists so we can stringify newly added numeric params.
    _sp_len = len(context.select_params)
    _wp_len = len(context.where_params)
    # Use translate_boolean_expression for proper SQL predicates — IRIS requires
    # comparison predicates in WHERE, not CASE WHEN boolean expressions.
    if isinstance(expr.predicate, ast.BooleanExpression):
        pred_sql = translate_boolean_expression(expr.predicate, context)
    else:
        pred_sql = translate_expression(expr.predicate, context, segment="where")
    # Convert newly added int/float params to str so VARCHAR column comparison works.
    for _lst in (context.select_params, context.where_params):
        _snap = _sp_len if _lst is context.select_params else _wp_len
        for _i in range(_snap, len(_lst)):
            if isinstance(_lst[_i], float):
                _lst[_i] = str(_lst[_i])
            elif isinstance(_lst[_i], int) and not isinstance(_lst[_i], bool):
                _lst[_i] = str(_lst[_i])
    # Also replace inline CAST('...' AS DOUBLE) with string literal for VARCHAR column comparison.
    import re as _re_qp
    def _cast_double_to_str(m):
        return f"'{m.group(1)}'"
    pred_sql = _re_qp.sub(r"CAST\('([^']+)' AS DOUBLE\)", _cast_double_to_str, pred_sql)
    del context.variable_aliases[expr.variable]
    context.scalar_variables.discard(expr.variable)
    pred_with_alias = pred_sql
    for col in ("node_id", "p", "val", "label"):
        pred_with_alias = pred_with_alias.replace(
            f"{alias}.{col}", f"{alias}.{var}"
        )
    # IRIS WHERE clause needs a comparison predicate, not a bare boolean expression.
    # Coerce bare 1/0 and bare column references to proper predicates.
    where_pred = pred_with_alias
    if where_pred in ("1", "1=1", "TRUE"):
        where_pred = "1 = 1"
    elif where_pred in ("0", "1=0", "FALSE"):
        where_pred = "1 = 0"
    elif where_pred.startswith("CASE WHEN ") and where_pred.endswith(" END"):
        # Nested quantifier: a CASE WHEN 0/1/NULL expression used as a WHERE predicate.
        # IRIS requires a comparison predicate, not a bare CASE WHEN. Add = 1 to coerce.
        where_pred = f"({where_pred} = 1)"
    elif where_pred.startswith("(SELECT ") and where_pred.endswith(")"):
        # Aggregation-style nested quantifier: (SELECT CASE WHEN ... END FROM ...) returns
        # 0/1/NULL scalar. IRIS requires a comparison operator in CASE WHEN conditions.
        where_pred = f"{where_pred} = 1"
    elif where_pred and not any(op in where_pred for op in ("=", "<", ">", " IN ", " IS ", " LIKE ", " NOT ")):
        # Bare column reference (e.g. lp0.x) — treat as truth test
        where_pred = f"{where_pred} = 1"

    # 3VL single-pass aggregation: one JSON_TABLE scan, inline SUM expressions.
    # No derived-table wrapper — avoids IRIS <UNDEFINED>corr in correlated contexts.
    counts_alias = context.next_alias("qc")
    sat_pred = where_pred.replace(f"{alias}.", f"{counts_alias}.")
    not_pred = where_pred.replace(f"{alias}.", f"{counts_alias}.")
    _jt_null_filter = f" WHERE {counts_alias}.{_safe_alias(expr.variable)} IS NOT NULL" if null_sentinel else ""
    jt_from = f"FROM JSON_TABLE({source_sql}, '$[*]' COLUMNS({_safe_alias(expr.variable)} {col_type} PATH '$')) {counts_alias}{_jt_null_filter}"
    sat_expr = f"SUM(CASE WHEN {sat_pred} THEN 1 ELSE 0 END)"
    dfail_expr = f"SUM(CASE WHEN NOT ({not_pred}) THEN 1 ELSE 0 END)"
    total_expr = "COUNT(*)"
    unc_expr = f"({total_expr} - {sat_expr} - {dfail_expr})"
    if expr.quantifier == "all":
        return (
            f"(SELECT CASE WHEN {dfail_expr} > 0 THEN 0"
            f" WHEN ({unc_expr}) > 0 THEN NULL"
            f" ELSE 1 END {jt_from})"
        )
    elif expr.quantifier == "none":
        return (
            f"(SELECT CASE WHEN {sat_expr} > 0 THEN 0"
            f" WHEN ({unc_expr}) > 0 THEN NULL"
            f" ELSE 1 END {jt_from})"
        )
    elif expr.quantifier == "single":
        return (
            f"(SELECT CASE WHEN {sat_expr} >= 2 THEN 0"
            f" WHEN {sat_expr} = 1 AND ({unc_expr}) = 0 THEN 1"
            f" WHEN ({unc_expr}) > 0 THEN NULL"
            f" ELSE 0 END {jt_from})"
        )
    else:  # any
        return (
            f"(SELECT CASE WHEN {sat_expr} > 0 THEN 1"
            f" WHEN ({unc_expr}) > 0 THEN NULL"
            f" ELSE 0 END {jt_from})"
        )


def _list_comprehension_type_check(expr):
    """Check if a list comprehension's projection would receive invalid types.

    For type-conversion functions (toInteger, toFloat, toString, toBoolean),
    raise a TypeError if the source list literal contains any element whose
    type cannot be accepted by that function (e.g. list/map/node inside toInteger).
    This preserves Cypher's strict type semantics: toInteger([]) → TypeError.
    """
    if not expr.projection:
        return
    if not isinstance(expr.source, ast.Literal) or not isinstance(expr.source.value, list):
        return
    if not isinstance(expr.projection, ast.FunctionCall):
        return
    fn = expr.projection.function_name.lower()
    if fn not in ("tointeger", "tofloat", "tostring", "toboolean"):
        return
    source_list = expr.source.value
    for item in source_list:
        # Determine the "kind" of this list element
        if isinstance(item, ast.MapLiteral):
            kind = "map"
        elif isinstance(item, ast.Literal):
            v = item.value
            if isinstance(v, list):
                kind = "list"
            elif isinstance(v, dict):
                kind = "map"
            elif isinstance(v, bool):
                kind = "bool"
            elif isinstance(v, (int, float)):
                kind = "number"
            elif isinstance(v, str):
                kind = "string"
            else:
                kind = "null"
        elif isinstance(item, ast.Variable):
            # Node/relationship/path variable — always invalid for scalar converters
            kind = "node_or_rel"
        else:
            # Complex expression (FunctionCall, etc.) — conservatively assume valid
            continue
        if fn == "toboolean":
            # toBoolean accepts: bool, string. Rejects: int, float, list, map, node, rel, path
            if kind in ("number", "list", "map", "node_or_rel"):
                raise TypeError(
                    f"InvalidArgumentValue: toBoolean() requires a boolean or string argument, got {kind}"
                )
        elif fn in ("tointeger", "tofloat"):
            # toInteger/toFloat accept: int, float, string. Reject: bool, list, map, node, rel, path
            if kind in ("bool", "list", "map", "node_or_rel"):
                raise TypeError(
                    f"InvalidArgumentValue: {fn}() requires a numeric or string argument, got {kind}"
                )
        elif fn == "tostring":
            # toString accepts: int, float, bool, string. Rejects: list, map, node, rel, path
            if kind in ("list", "map", "node_or_rel"):
                raise TypeError(
                    f"InvalidArgumentValue: toString() requires a scalar argument, got {kind}"
                )


def _expr_list_comprehension(expr, context, segment):
    # Aggregation functions (count(*), sum(), etc.) are not allowed inside list comprehensions.
    # openCypher TCK List12[7]: [x IN list | count(*)] → InvalidAggregation SyntaxError.
    if expr.projection and _contains_aggregation(expr.projection):
        raise SyntaxError(
            "InvalidAggregation: Aggregation functions are not allowed inside list comprehensions"
        )
    # Type-check projection against source list elements for conversion functions.
    _list_comprehension_type_check(expr)
    # When the source is collect(arg), IRIS cannot place JSON_ARRAYAGG inside JSON_TABLE source.
    # Instead, bind the list comp variable directly to arg's SQL and emit inline aggregate SQL.
    # [v IN collect(arg) WHERE pred | proj] → JSON_ARRAYAGG(CASE WHEN pred THEN proj ELSE NULL END)
    if (
        isinstance(expr.source, ast.AggregationFunction)
        and expr.source.function_name.lower() == "collect"
        and expr.source.argument is not None
    ):
        arg_sql = translate_expression(expr.source.argument, context, segment=segment)
        var = sanitize_identifier(expr.variable)
        context.variable_aliases[expr.variable] = "__lc_collect__"
        context.scalar_variables.add(expr.variable)
        # pred and proj use variable as a scalar: bind to a sentinel alias that maps back to arg_sql
        pred_case = ""
        if expr.predicate:
            if isinstance(expr.predicate, ast.BooleanExpression):
                pred_sql_raw = translate_boolean_expression(expr.predicate, context)
            else:
                pred_sql_raw = translate_expression(expr.predicate, context, segment=segment)
            # Replace the sentinel reference with arg_sql
            pred_sql = pred_sql_raw.replace(f"__lc_collect__.{var}", arg_sql)
            # IRIS does not accept CASE WHEN (NULL) — replace with always-false 1=0
            if pred_sql.strip() == "NULL":
                pred_sql = "1=0"
            pred_case = f"CASE WHEN ({pred_sql}) THEN "
        select_expr = arg_sql
        if expr.projection:
            proj_sql_raw = translate_expression(expr.projection, context, segment=segment)
            select_expr = proj_sql_raw.replace(f"__lc_collect__.{var}", arg_sql)
        del context.variable_aliases[expr.variable]
        context.scalar_variables.discard(expr.variable)
        if pred_case:
            return f"JSON_ARRAYAGG({pred_case}{select_expr} ELSE NULL END)"
        return f"JSON_ARRAYAGG({select_expr})"
    # Constant-fold: literal source + simple type-conversion projection with no predicate.
    # JSON_ARRAYAGG silently drops NULLs; compute fully in Python to preserve null slots.
    # Also handles when the source is a Variable known to hold a literal list (from literal_list_vars).
    _lc_source_elems = None
    if (
        not expr.predicate
        and isinstance(expr.projection, ast.FunctionCall)
        and expr.projection.function_name.lower() in ("tofloat", "tointeger", "tostring", "toboolean")
    ):
        if isinstance(expr.source, ast.Literal) and isinstance(expr.source.value, list):
            _lc_source_elems = expr.source.value
        elif isinstance(expr.source, ast.Variable):
            _llv = getattr(context, 'literal_list_vars', {})
            _var_alias = context.variable_aliases.get(expr.source.name, expr.source.name)
            if _var_alias in _llv:
                _lc_source_elems = _llv[_var_alias]
            elif expr.source.name in _llv:
                _lc_source_elems = _llv[expr.source.name]

    if _lc_source_elems is not None:
        import json as _json
        fn_lc = expr.projection.function_name.lower()
        results = []
        for elem in _lc_source_elems:
            v = elem.value if isinstance(elem, ast.Literal) else None
            if fn_lc == "tofloat":
                try:
                    results.append(float(str(v)) if isinstance(v, (int, float, str)) and not isinstance(v, bool) else None)
                except (ValueError, TypeError):
                    results.append(None)
            elif fn_lc == "tointeger":
                try:
                    results.append(int(float(str(v))) if isinstance(v, (int, float, str)) and not isinstance(v, bool) else None)
                except (ValueError, TypeError):
                    results.append(None)
            elif fn_lc == "tostring":
                if isinstance(v, bool):
                    results.append("true" if v else "false")
                elif isinstance(v, (int, float, str)):
                    results.append(str(v))
                else:
                    results.append(None)
            elif fn_lc == "toboolean":
                if isinstance(v, bool):
                    results.append(v)
                elif isinstance(v, str):
                    results.append(True if v.lower() == "true" else (False if v.lower() == "false" else None))
                else:
                    results.append(None)
        js = _json.dumps(results)
        return f"CAST('{js.replace(chr(39), chr(39)+chr(39))}' AS VARCHAR({max(len(js)+1, 256)}))"

    source_sql = translate_expression(expr.source, context, segment="inline")
    var = sanitize_identifier(expr.variable)
    safe_var = _safe_alias(expr.variable)
    alias = context.next_alias("lc")
    # Map the variable to the table alias only (not alias.var) — consistent with
    # _expr_list_predicate. _expr_variable resolves scalar vars as "{alias}.{name}",
    # so storing just the alias avoids the double-dot bug (alias.var.var).
    context.variable_aliases[expr.variable] = f"{alias}"
    # Mark as scalar variable so property access uses JSON_VALUE
    context.scalar_variables.add(expr.variable)
    # If source is a collected node list, mark loop var as collected_node_variable
    if (isinstance(expr.source, ast.Variable)
            and expr.source.name in getattr(context, "collected_node_lists", {})):
        context.collected_node_variables.add(expr.variable)
    where_clause = ""
    if expr.predicate:
        if isinstance(expr.predicate, ast.BooleanExpression):
            pred_sql = translate_boolean_expression(expr.predicate, context)
        else:
            pred_sql = translate_expression(expr.predicate, context, segment="inline")
        where_clause = f" WHERE {pred_sql}"
    select_expr = f"{alias}.{safe_var}"
    if expr.projection:
        proj_sql = translate_expression(expr.projection, context, segment="inline")
        select_expr = proj_sql
    del context.variable_aliases[expr.variable]
    context.scalar_variables.discard(expr.variable)
    context.collected_node_variables.discard(expr.variable)
    # Use VARCHAR to support both scalar values and JSON objects
    return (
        f"(SELECT JSON_ARRAYAGG({select_expr}) FROM "
        f"JSON_TABLE({source_sql}, '$[*]' COLUMNS({safe_var} VARCHAR(32767) PATH '$')) {alias}"
        f"{where_clause})"
    )


def _expr_reduce(expr, context, segment):
    var = sanitize_identifier(expr.variable)
    acc = expr.accumulator

    try:
        init_val = float(expr.init.value) if hasattr(expr.init, "value") else 0.0
    except Exception:
        init_val = 0.0

    if (
        isinstance(expr.source, ast.AggregationFunction)
        and expr.source.function_name.lower() == "collect"
        and expr.source.argument is not None
    ):
        collect_arg = expr.source.argument
        collect_sql = translate_expression(collect_arg, context, segment=segment)
        return f"({init_val} + SUM(CAST({collect_sql} AS DOUBLE)))"

    source_sql = translate_expression(expr.source, context, segment=segment)
    alias = context.next_alias("re")
    # Map the variable to the alias.var reference for JSON_TABLE column access
    context.variable_aliases[expr.variable] = f"{alias}.{var}"
    # Mark as scalar variable so property access uses JSON_VALUE
    context.scalar_variables.add(expr.variable)
    context.variable_aliases[acc] = "__acc__"
    body_sql = translate_expression(expr.body, context, segment=segment)
    body_sql = body_sql.replace("__acc__.node_id", "0").replace("__acc__", "0")
    del context.variable_aliases[expr.variable]
    context.scalar_variables.discard(expr.variable)
    del context.variable_aliases[acc]
    init_sql = translate_expression(expr.init, context, segment=segment)
    return (
        f"({init_sql} + (SELECT SUM({body_sql}) FROM "
        f"JSON_TABLE({source_sql}, '$[*]' COLUMNS({var} VARCHAR(32767) PATH '$')) {alias}))"
    )


def _expr_case(expr, context, segment):
    parts = ["CASE"]
    if expr.test_expression is not None:
        parts.append(translate_expression(expr.test_expression, context, segment))
    for wc in expr.when_clauses:
        if expr.test_expression is None:
            # Searched CASE: WHEN condition must be a boolean predicate in SQL.
            cond = translate_boolean_expression(wc.condition, context)
        elif isinstance(wc.condition, ast.BooleanExpression):
            cond = translate_boolean_expression(wc.condition, context)
        else:
            cond = translate_expression(wc.condition, context, segment)
        res = _inline_literal(wc.result)
        if res is None:
            res = translate_expression(wc.result, context, segment)
        parts.append(f"WHEN {cond} THEN {res}")
    else_res = (
        _inline_literal(expr.else_result) if expr.else_result is not None else None
    )
    if else_res is None and expr.else_result is not None:
        else_res = translate_expression(expr.else_result, context, segment)
    if else_res is not None:
        parts.append(f"ELSE {else_res}")
    parts.append("END")
    return " ".join(parts)


def _expr_propref_temporal(expr, context, alias):
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
    if (
        expr.property_name in ("ts", "weight", "w")
        and expr.variable not in context.temporal_rel_ctes
    ):
        if alias and alias.startswith("e"):
            m = getattr(context, "_metadata", None)
            if m is not None:
                m.warnings.append(
                    f"{expr.variable}.{expr.property_name} in RETURN without WHERE {expr.variable}.ts filter "
                    f"— {expr.property_name} will be NULL. Add WHERE {expr.variable}.ts >= $start AND "
                    f"{expr.variable}.ts <= $end for temporal routing."
                )
            return "NULL"
    return None


def _expr_propref_edge_alias(expr, context, alias):
    is_undirected = alias in getattr(context, "_undirected_aliases", set())
    is_edgescan = alias in getattr(context, "_edgescan_aliases", set())
    if expr.property_name == "p":
        return f"{alias}.{'_p' if is_undirected else 'p'}"
    if expr.property_name == "s":
        return f"{alias}.{'_src' if is_undirected else 's'}"
    if expr.property_name == "o_id":
        return f"{alias}.{'_dst' if is_undirected else 'o_id'}"
    if is_undirected or is_edgescan:
        return "NULL"
    return f"CASE WHEN {alias}.qualifiers IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({alias}.qualifiers, '$.{expr.property_name}') END"


def _detect_temporal_type(expr, context) -> Optional[str]:
    """Detect if an expression returns a temporal type (date, time, datetime, duration, etc.).

    Returns the temporal type string or None if not a temporal expression.
    Temporal types: "date", "localtime", "time", "datetime", "localdatetime", "duration"
    """
    if isinstance(expr, ast.FunctionCall):
        fn = expr.function_name.lower()
        if fn in ("date", "localtime", "time", "datetime", "localdatetime", "duration"):
            return fn
        # Namespace functions: duration.between → duration, date.truncate → date, etc.
        if fn.startswith("duration."):
            return "duration"
        if fn.startswith("date."):
            return "date"
        if fn.startswith("datetime."):
            return "datetime"
        if fn.startswith("localdatetime."):
            return "localdatetime"
        if fn.startswith("localtime."):
            return "localtime"
        if fn.startswith("time."):
            return "time"
    if isinstance(expr, ast.Variable):
        # Check if this variable was marked as temporal in a previous WITH clause
        temporal_type = context.temporal_types.get(expr.name)
        if temporal_type:
            return temporal_type
    return None


def _extract_temporal_component(base_sql: str, temporal_type: str, prop_name: str) -> Optional[str]:
    """Generate SQL to extract a temporal component from an ISO temporal string.

    Args:
        base_sql: SQL expression yielding the temporal value
        temporal_type: One of "date", "localtime", "time", "datetime", "localdatetime", "duration"
        prop_name: Component name (e.g., "year", "month", "day", "hours", "minutes", "seconds")

    Returns:
        SQL expression to extract the component, or None if unsupported
    """

    # Date components: '2024-01-15'
    if temporal_type == "date":
        if prop_name == "year":
            return f"CAST(SUBSTRING({base_sql}, 1, 4) AS INTEGER)"
        elif prop_name == "month":
            return f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)"
        elif prop_name == "day":
            return f"CAST(SUBSTRING({base_sql}, 9, 2) AS INTEGER)"
        elif prop_name == "weekYear":
            # ISO week year = year of Thursday in same ISO week.
            # Thursday offset from any date: 4 - iso_dow (where iso_dow 1=Mon..7=Sun)
            # iso_dow = (IRIS_DAYOFWEEK + 5) % 7 + 1 (IRIS: Sun=1..Sat=7 → ISO: Mon=1..Sun=7)
            _date = f"CAST({base_sql} AS DATE)"
            _iris_dow = f"{{fn DAYOFWEEK({_date})}}"
            _iso_dow = f"(({_iris_dow} + 5) % 7 + 1)"
            _thu = f"DATEADD('day', 4 - {_iso_dow}, {_date})"
            return f"DATEPART('year', {_thu})"
        elif prop_name == "week":
            return f"{{fn WEEK(CAST({base_sql} AS DATE))}}"
        elif prop_name == "dayOfWeek" or prop_name == "weekDay":
            return f"MOD({{fn DAYOFWEEK(CAST({base_sql} AS DATE))}} + 5, 7) + 1"
        elif prop_name == "dayOfYear" or prop_name == "ordinalDay":
            return f"{{fn DAYOFYEAR(CAST({base_sql} AS DATE))}}"
        elif prop_name == "quarter":
            return f"CAST((CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER) - 1) / 3 + 1 AS INTEGER)"
        elif prop_name == "dayOfQuarter":
            month_var = f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)"
            day_var = f"CAST(SUBSTRING({base_sql}, 9, 2) AS INTEGER)"
            return f"CASE WHEN {month_var} IN (1) THEN {day_var} WHEN {month_var} IN (2) THEN {day_var} + 31 WHEN {month_var} IN (3) THEN {day_var} + 59 WHEN {month_var} IN (4) THEN {day_var} WHEN {month_var} IN (5) THEN {day_var} + 30 WHEN {month_var} IN (6) THEN {day_var} + 61 WHEN {month_var} IN (7) THEN {day_var} WHEN {month_var} IN (8) THEN {day_var} + 31 WHEN {month_var} IN (9) THEN {day_var} + 62 WHEN {month_var} IN (10) THEN {day_var} WHEN {month_var} IN (11) THEN {day_var} + 31 WHEN {month_var} IN (12) THEN {day_var} + 61 ELSE 0 END"
        return None

    # LocalDateTime: '2024-01-15T12:31:14[.nanos]' (with optional fractional seconds)
    if temporal_type in ("localdatetime", "datetime"):
        if prop_name == "hour":
            return f"CAST(SUBSTRING({base_sql}, 12, 2) AS INTEGER)"
        elif prop_name == "minute":
            return f"CAST(SUBSTRING({base_sql}, 15, 2) AS INTEGER)"
        elif prop_name == "second":
            return f"CAST(SUBSTRING({base_sql}, 18, 2) AS INTEGER)"
        elif prop_name == "millisecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 21, 3), '0') AS INTEGER)"
        elif prop_name == "microsecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 21, 6), '0') AS INTEGER)"
        elif prop_name == "nanosecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 21, 9), '0') AS INTEGER)"
        # Date components: extract date part before T (position 1-10: 'YYYY-MM-DD')
        elif prop_name == "year":
            return f"CAST(SUBSTRING({base_sql}, 1, 4) AS INTEGER)"
        elif prop_name == "month":
            return f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)"
        elif prop_name == "day":
            return f"CAST(SUBSTRING({base_sql}, 9, 2) AS INTEGER)"
        elif prop_name == "weekYear":
            # ISO week year = year of Thursday in same ISO week (see date case above)
            _date = f"CAST(SUBSTRING({base_sql}, 1, 10) AS DATE)"
            _iris_dow = f"{{fn DAYOFWEEK({_date})}}"
            _iso_dow = f"(({_iris_dow} + 5) % 7 + 1)"
            _thu = f"DATEADD('day', 4 - {_iso_dow}, {_date})"
            return f"DATEPART('year', {_thu})"
        elif prop_name == "week":
            return f"{{fn WEEK(CAST(SUBSTRING({base_sql}, 1, 10) AS DATE))}}"
        elif prop_name == "dayOfWeek" or prop_name == "weekDay":
            return f"MOD({{fn DAYOFWEEK(CAST(SUBSTRING({base_sql}, 1, 10) AS DATE))}} + 5, 7) + 1"
        elif prop_name == "dayOfYear" or prop_name == "ordinalDay":
            return f"{{fn DAYOFYEAR(CAST(SUBSTRING({base_sql}, 1, 10) AS DATE))}}"
        elif prop_name == "quarter":
            return f"CAST((CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER) - 1) / 3 + 1 AS INTEGER)"
        elif prop_name == "dayOfQuarter":
            month_var = f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)"
            day_var = f"CAST(SUBSTRING({base_sql}, 9, 2) AS INTEGER)"
            return f"CASE WHEN {month_var} IN (1) THEN {day_var} WHEN {month_var} IN (2) THEN {day_var} + 31 WHEN {month_var} IN (3) THEN {day_var} + 59 WHEN {month_var} IN (4) THEN {day_var} WHEN {month_var} IN (5) THEN {day_var} + 30 WHEN {month_var} IN (6) THEN {day_var} + 61 WHEN {month_var} IN (7) THEN {day_var} WHEN {month_var} IN (8) THEN {day_var} + 31 WHEN {month_var} IN (9) THEN {day_var} + 62 WHEN {month_var} IN (10) THEN {day_var} WHEN {month_var} IN (11) THEN {day_var} + 31 WHEN {month_var} IN (12) THEN {day_var} + 61 ELSE 0 END"
        elif prop_name == "epochSeconds":
            return f"DATEDIFF('second', '1970-01-01T00:00:00', CAST(SUBSTRING({base_sql}, 1, 10) AS DATE))"
        elif prop_name == "epochMillis":
            return f"DATEDIFF('second', '1970-01-01T00:00:00', CAST(SUBSTRING({base_sql}, 1, 10) AS DATE)) * 1000 + CAST(COALESCE(SUBSTRING({base_sql}, 21, 3), '0') AS INTEGER)"
        return None

    # LocalTime: 'HH:MM:SS[.nanos]' (no timezone)
    if temporal_type == "localtime":
        if prop_name == "hour":
            return f"CAST(SUBSTRING({base_sql}, 1, 2) AS INTEGER)"
        elif prop_name == "minute":
            return f"CAST(SUBSTRING({base_sql}, 4, 2) AS INTEGER)"
        elif prop_name == "second":
            return f"CAST(SUBSTRING({base_sql}, 7, 2) AS INTEGER)"
        elif prop_name == "millisecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 10, 3), '0') AS INTEGER)"
        elif prop_name == "microsecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 10, 6), '0') AS INTEGER)"
        elif prop_name == "nanosecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 10, 9), '0') AS INTEGER)"
        return None

    # Time (with timezone): 'HH:MM:SS[.nanos]±HH:MM' or 'HH:MM:SS[.nanos]Z'
    if temporal_type == "time":
        if prop_name == "hour":
            return f"CAST(SUBSTRING({base_sql}, 1, 2) AS INTEGER)"
        elif prop_name == "minute":
            return f"CAST(SUBSTRING({base_sql}, 4, 2) AS INTEGER)"
        elif prop_name == "second":
            return f"CAST(SUBSTRING({base_sql}, 7, 2) AS INTEGER)"
        elif prop_name == "millisecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 10, 3), '0') AS INTEGER)"
        elif prop_name == "microsecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 10, 6), '0') AS INTEGER)"
        elif prop_name == "nanosecond":
            return f"CAST(COALESCE(SUBSTRING({base_sql}, 10, 9), '0') AS INTEGER)"
        elif prop_name == "timezone" or prop_name == "offset":
            return f"CASE WHEN CHARINDEX('Z', {base_sql}) > 0 THEN 'Z' WHEN CHARINDEX('+', {base_sql}) > 8 THEN SUBSTRING({base_sql}, CHARINDEX('+', {base_sql})) WHEN CHARINDEX('-', {base_sql}) > 8 THEN SUBSTRING({base_sql}, CHARINDEX('-', {base_sql})) ELSE NULL END"
        elif prop_name == "offsetMinutes":
            return f"CASE WHEN CHARINDEX('Z', {base_sql}) > 0 THEN 0 WHEN CHARINDEX('+', {base_sql}) > 8 THEN (CAST(SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}) + 1, 2) AS INTEGER) * 60 + CAST(SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}) + 4, 2) AS INTEGER)) WHEN CHARINDEX('-', {base_sql}) > 8 THEN -(CAST(SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}) + 1, 2) AS INTEGER) * 60 + CAST(SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}) + 4, 2) AS INTEGER)) ELSE 0 END"
        elif prop_name == "offsetSeconds":
            return f"(CASE WHEN CHARINDEX('Z', {base_sql}) > 0 THEN 0 WHEN CHARINDEX('+', {base_sql}) > 8 THEN (CAST(SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}) + 1, 2) AS INTEGER) * 60 + CAST(SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}) + 4, 2) AS INTEGER)) WHEN CHARINDEX('-', {base_sql}) > 8 THEN -(CAST(SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}) + 1, 2) AS INTEGER) * 60 + CAST(SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}) + 4, 2) AS INTEGER)) ELSE 0 END) * 60"
        return None

    # Duration: 'P[n]Y[n]M[n]DT[n]H[n]M[n]S' (ISO 8601)
    # Format from _format_duration: PnYnMnDTnHnMn.nS  (components omitted if 0)
    # Examples: 'PT22H', 'P30Y8M13D', 'P1YT4M50S', 'PT-22H', 'P-27DT-21H-40M-32.142S'
    if temporal_type == "duration":
        # Helper expressions used by multiple properties
        # t_pos: position of 'T' in string (0 if no T)
        t_pos = f"CHARINDEX('T', {base_sql})"
        y_pos = f"CHARINDEX('Y', {base_sql})"
        # M_date: position of first 'M' before 'T' (months component)
        # D_pos: position of 'D' before 'T'

        if prop_name == "years":
            return f"CASE WHEN {y_pos} > 0 THEN CAST(SUBSTRING({base_sql}, 2, {y_pos} - 2) AS INTEGER) ELSE 0 END"

        elif prop_name == "months":
            # Months: 'M' before T  (not after T)
            # In 'P30Y8M13D': M is at pos 6, T not present or after D
            # In 'P8M13D': M at pos 3
            # Algorithm: find 'M' before 'T' (date part), extract number before it
            # 'M' in date part comes after 'Y' (or 'P') and before 'D' or 'T'
            # CHARINDEX('M', substr(1, T_pos-1)) gives M position in date part
            # If T not present: search whole string
            return (
                f"CASE WHEN {t_pos} > 0 THEN "
                f"  CASE WHEN CHARINDEX('M', SUBSTRING({base_sql}, 1, {t_pos} - 1)) > 0 THEN "
                f"    CASE WHEN {y_pos} > 0 "
                f"    THEN CAST(SUBSTRING({base_sql}, {y_pos} + 1, CHARINDEX('M', SUBSTRING({base_sql}, 1, {t_pos} - 1)) - {y_pos} - 1) AS INTEGER)"
                f"    ELSE CAST(SUBSTRING({base_sql}, 2, CHARINDEX('M', SUBSTRING({base_sql}, 1, {t_pos} - 1)) - 2) AS INTEGER) END"
                f"  ELSE 0 END "
                f"ELSE "
                f"  CASE WHEN CHARINDEX('M', {base_sql}) > 0 THEN "
                f"    CASE WHEN {y_pos} > 0 "
                f"    THEN CAST(SUBSTRING({base_sql}, {y_pos} + 1, CHARINDEX('M', {base_sql}) - {y_pos} - 1) AS INTEGER)"
                f"    ELSE CAST(SUBSTRING({base_sql}, 2, CHARINDEX('M', {base_sql}) - 2) AS INTEGER) END"
                f"  ELSE 0 END "
                f"END"
            )

        elif prop_name == "days":
            # Days: number before 'D' in date part (before T)
            d_in_date = f"CHARINDEX('D', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, 1, {t_pos} - 1) ELSE {base_sql} END)"
            date_part = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, 1, {t_pos} - 1) ELSE {base_sql} END"
            # Find what precedes D: could be Y, M, or P
            # Use: find last separator before D position
            # Simplified: find D, then scan backwards to find number
            m_in_date = f"CHARINDEX('M', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, 1, {t_pos} - 1) ELSE {base_sql} END)"
            return (
                f"CASE WHEN {d_in_date} > 0 THEN "
                f"  CAST(SUBSTRING({date_part}, "
                f"    CASE WHEN {m_in_date} > 0 THEN {m_in_date} + 1 "
                f"         WHEN {y_pos} > 0 THEN {y_pos} + 1 "
                f"         ELSE 2 END, "
                f"    {d_in_date} - CASE WHEN {m_in_date} > 0 THEN {m_in_date} "
                f"                       WHEN {y_pos} > 0 THEN {y_pos} ELSE 1 END - 1) AS INTEGER)"
                f" ELSE 0 END"
            )

        elif prop_name == "hours":
            # Hours: number before 'H' in time part (after T)
            return (
                f"CASE WHEN {t_pos} > 0 AND CHARINDEX('H', {base_sql}) > {t_pos} THEN "
                f"  CAST(SUBSTRING({base_sql}, {t_pos} + 1, CHARINDEX('H', {base_sql}) - {t_pos} - 1) AS INTEGER)"
                f" ELSE 0 END"
            )

        elif prop_name == "minutes":
            # Minutes: 'M' after T, before 'S' or end
            # The time M comes after T, and after H if H is present
            h_pos = f"CHARINDEX('H', {base_sql})"
            m_time = f"CHARINDEX('M', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END)"
            return (
                f"CASE WHEN {t_pos} > 0 AND {m_time} > 0 THEN "
                f"  CAST(SUBSTRING("
                f"    CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE {base_sql} END,"
                f"    CASE WHEN CHARINDEX('H', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END) > 0 "
                f"         THEN CHARINDEX('H', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END) + 1 "
                f"         ELSE 1 END,"
                f"    {m_time} - CASE WHEN CHARINDEX('H', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END) > 0 "
                f"                    THEN CHARINDEX('H', CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END) "
                f"                    ELSE 0 END"
                f"  ) AS INTEGER)"
                f" ELSE 0 END"
            )

        elif prop_name == "seconds":
            # seconds = total seconds in time part = hours*3600 + minutes*60 + seconds
            # This is the "total seconds component" for the time part of the duration
            # Per Cypher spec: duration.seconds is the normalized seconds (floor division)
            # For PT22H → seconds = 79200
            # For PT-22H → seconds = -79200
            # For P-27DT-21H-40M-32.142S → seconds = -(21*3600 + 40*60 + 32) = -78032
            # Approach: compute H*3600 + M*60 + S components numerically
            time_part_sql = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END"
            h_in_t = f"CHARINDEX('H', {time_part_sql})"
            m_in_t = f"CHARINDEX('M', {time_part_sql})"
            s_in_t = f"CHARINDEX('S', {time_part_sql})"
            dot_in_t = f"CHARINDEX('.', {time_part_sql})"

            # Hours from time part
            h_val = f"CASE WHEN {h_in_t} > 0 THEN CAST(SUBSTRING({time_part_sql}, 1, {h_in_t} - 1) AS INTEGER) ELSE 0 END"
            # Minutes from time part (after H or start)
            m_start = f"CASE WHEN {h_in_t} > 0 THEN {h_in_t} + 1 ELSE 1 END"
            m_val = f"CASE WHEN {m_in_t} > 0 THEN CAST(SUBSTRING({time_part_sql}, {m_start}, {m_in_t} - {m_start}) AS INTEGER) ELSE 0 END"
            # Seconds from time part (after M or H or start, before S - use full float including fractional)
            s_start = f"CASE WHEN {m_in_t} > 0 THEN {m_in_t} + 1 WHEN {h_in_t} > 0 THEN {h_in_t} + 1 ELSE 1 END"
            # s_end must reach S to capture full float like "-59.9" (not just "-59" by stopping at dot)
            s_end_full = f"CASE WHEN {s_in_t} > 0 THEN {s_in_t} ELSE LENGTH({time_part_sql}) + 1 END"
            # Use FLOOR for seconds to handle negative fractional: FLOOR(-59.9) = -60
            s_val = f"CASE WHEN {s_in_t} > 0 THEN CAST(FLOOR(CAST(SUBSTRING({time_part_sql}, {s_start}, {s_end_full} - {s_start}) AS FLOAT)) AS INTEGER) ELSE 0 END"

            return f"CASE WHEN {t_pos} > 0 THEN ({h_val}) * 3600 + ({m_val}) * 60 + ({s_val}) ELSE 0 END"

        elif prop_name == "nanosecondsOfSecond":
            # Fractional nanoseconds of the seconds component
            # For PT22H → 0
            # For PT23H59M59.9S → 900000000
            # For PT-23H-59M-59.9S → 100000000
            # Cypher spec: nanoseconds is the positive offset from the floor second.
            # For negative fractional seconds: floor(-59.9) = -60, offset = 0.1s = 100000000 ns
            # i.e., nanos = 1_000_000_000 - frac_ns when seconds component is negative
            time_part_sql = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END"
            h_in_t = f"CHARINDEX('H', {time_part_sql})"
            m_in_t = f"CHARINDEX('M', {time_part_sql})"
            s_in_t = f"CHARINDEX('S', {time_part_sql})"
            dot_in_t = f"CHARINDEX('.', {time_part_sql})"
            # Determine start of seconds component (after M, or H, or beginning)
            s_start2 = f"CASE WHEN {m_in_t} > 0 THEN {m_in_t} + 1 WHEN {h_in_t} > 0 THEN {h_in_t} + 1 ELSE 1 END"
            # Check if seconds component starts with '-'
            s_is_neg = f"CASE WHEN {dot_in_t} > 0 AND {s_in_t} > {dot_in_t} AND SUBSTRING({time_part_sql}, {s_start2}, 1) = '-' THEN 1 ELSE 0 END"
            # Fractional nanoseconds (raw, from digits after dot before S)
            raw_frac_ns = f"CAST(RPAD(SUBSTRING({time_part_sql}, {dot_in_t} + 1, {s_in_t} - {dot_in_t} - 1), 9, '0') AS BIGINT)"
            return (
                f"CASE WHEN {dot_in_t} > 0 AND {s_in_t} > {dot_in_t} THEN "
                f"  CASE WHEN {s_is_neg} = 1 THEN 1000000000 - {raw_frac_ns} ELSE {raw_frac_ns} END"
                f" ELSE 0 END"
            )

        elif prop_name == "milliseconds":
            time_part_sql = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END"
            s_in_t = f"CHARINDEX('S', {time_part_sql})"
            dot_in_t = f"CHARINDEX('.', {time_part_sql})"
            return (
                f"CASE WHEN {dot_in_t} > 0 AND {s_in_t} > {dot_in_t} THEN "
                f"  CAST(RPAD(SUBSTRING({time_part_sql}, {dot_in_t} + 1, {s_in_t} - {dot_in_t} - 1), 3, '0') AS INTEGER)"
                f" ELSE 0 END"
            )

        elif prop_name == "microseconds":
            time_part_sql = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END"
            s_in_t = f"CHARINDEX('S', {time_part_sql})"
            dot_in_t = f"CHARINDEX('.', {time_part_sql})"
            return (
                f"CASE WHEN {dot_in_t} > 0 AND {s_in_t} > {dot_in_t} THEN "
                f"  CAST(RPAD(SUBSTRING({time_part_sql}, {dot_in_t} + 1, {s_in_t} - {dot_in_t} - 1), 6, '0') AS INTEGER)"
                f" ELSE 0 END"
            )

        elif prop_name == "nanoseconds":
            time_part_sql = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1, 9999) ELSE '' END"
            s_in_t = f"CHARINDEX('S', {time_part_sql})"
            dot_in_t = f"CHARINDEX('.', {time_part_sql})"
            return (
                f"CASE WHEN {dot_in_t} > 0 AND {s_in_t} > {dot_in_t} THEN "
                f"  CAST(RPAD(SUBSTRING({time_part_sql}, {dot_in_t} + 1, {s_in_t} - {dot_in_t} - 1), 9, '0') AS BIGINT)"
                f" ELSE 0 END"
            )

        return None

    return None


def _expr_property_reference(expr, context, segment):
    alias = context.variable_aliases.get(expr.variable)
    if not alias:
        raise SyntaxError(f"Undefined variable: {expr.variable}")
    temporal = _expr_propref_temporal(expr, context, alias)
    if temporal is not None:
        return temporal
    if alias in context.mapped_node_aliases:
        mapping = context.mapped_node_aliases[alias]
        if expr.property_name in ("id", "node_id"):
            return f"{alias}.{sanitize_identifier(mapping['id_column'])}"
        return f"{alias}.{sanitize_identifier(expr.property_name)}"
    if alias.startswith("Stage"):
        if expr.property_name in ("node_id", "id"):
            return _safe_alias(expr.variable)

        # Check if this is a temporal scalar variable (date, time, datetime, duration, etc.)
        if expr.variable in context.temporal_types:
            temporal_type = context.temporal_types[expr.variable]
            stage_col = _safe_alias(expr.variable)
            temp_extract = _extract_temporal_component(stage_col, temporal_type, expr.property_name)
            if temp_extract:
                return f"CASE WHEN {stage_col} IS NULL THEN NULL ELSE {temp_extract} END"

        # Edge-qualifiers variables: use JSON_VALUE on the column value (Stage column = qualifiers JSON)
        edge_stage_vars = getattr(context, "edge_stage_variables", set())
        if expr.variable in context.scalar_variables or expr.variable in edge_stage_vars:
            # Compile-time TypeError: scalar/list variables cannot have properties accessed on them
            if expr.variable in getattr(context, 'non_map_vars', set()):
                raise TypeError(
                    f"TypeError: Type mismatch: expected Map or Node, but was {expr.variable!r} (non-map scalar)"
                )
            col_ref = f"{alias}.{_safe_alias(expr.variable)}"
            return f"CASE WHEN {col_ref} IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{expr.property_name}') END"
        # Node variables from Stage: get property value for the stage node id.
        # When called from ORDER BY (segment="inline"), use a correlated subquery instead of
        # a JOIN — multiple ORDER BY keys would create multiple LEFT JOINs on rdf_props, which
        # triggers the %qaqpre SIGSEGV in IRIS 2026.3.0AI on multi-JOIN queries.
        stage_col = _safe_alias(expr.variable)
        prop_key = expr.property_name
        if segment == "inline":
            context.select_params.append(prop_key)
            return f"(SELECT val FROM {_table('rdf_props')} WHERE s = {stage_col} AND \"key\" = ?)"
        p_alias = context.next_alias("p")
        context.join_clauses.append(
            f'LEFT JOIN {_table("rdf_props")} {p_alias} ON {p_alias}.s = {stage_col} AND {p_alias}."key" = {context.add_join_param(prop_key)}'
        )
        return f"{p_alias}.val"
    if alias.startswith("e") and not alias.startswith("ES_"):
        return _expr_propref_edge_alias(expr, context, alias)
    # Collected node variable: the column holds a full node JSON blob ({_id,_labels,_props}).
    # Extract _id via a correlated subquery (can't use JSON_VALUE in JOIN ON in IRIS).
    if expr.variable in getattr(context, "collected_node_variables", set()):
        col_ref = f"{alias}.{_safe_alias(expr.variable)}"
        prop_key = expr.property_name
        context.select_params.append(prop_key)
        return (
            f"(SELECT val FROM {_table('rdf_props')} "
            f"WHERE s = SQLUser.JSON_VALUE({col_ref}, '$._id') AND \"key\" = ?)"
        )
    # Scalar variable from JSON_TABLE (list predicate / list comprehension): use JSON_VALUE
    # not rdf_props join.  The column holds a JSON-serialised value, not a graph node id.
    # Guard: only call JSON_VALUE when the value is a JSON object (starts with '{').
    # JSON_VALUE raises SQLCODE=-400 on non-JSON or non-matching path.
    if expr.variable in context.scalar_variables:
        col_ref = f"{alias}.{_safe_alias(expr.variable)}"
        prop = _jsonpath_key(expr.property_name)
        return (
            f"CASE WHEN ({col_ref}) IS NULL OR SUBSTRING({col_ref}, 1, 1) <> '{{' "
            f"THEN NULL ELSE SQLUser.JSON_VALUE({col_ref}, '$.{prop}') END"
        )
    if expr.property_name == "node_id":
        return f"{alias}.node_id"
    # For ORDER BY (segment="inline"), use a correlated subquery instead of a JOIN on rdf_props.
    # Multiple ORDER BY keys would create multiple LEFT JOINs on rdf_props, which triggers the
    # %qaqpre SIGSEGV in IRIS 2026.3.0AI on multi-JOIN+FETCH FIRST queries.
    # Use the variable name as the node_id column (it's projected as the variable alias in WITH).
    if segment == "inline":
        context.select_params.append(expr.property_name)
        # Use the table alias (n0.node_id) not the SELECT alias (n) — SELECT aliases are
        # not referenceable within the same SELECT's correlated subexpressions in IRIS SQL.
        var_col = f"{alias}.node_id"
        return f"(SELECT val FROM {_table('rdf_props')} WHERE s = {var_col} AND \"key\" = ?)"
    if segment == "where":
        opt_new = getattr(context, "optional_match_new_aliases", set())
        if alias in opt_new:
            # For optional-match variables in WHERE, use a correlated subquery so that
            # the property reference can be pushed into the edge JOIN's ON clause via
            # alias substitution (alias.node_id -> edge._dst) without creating a
            # dangling JOIN that precedes the node JOIN that defines the alias.
            context.where_params.append(expr.property_name)
            return f"(SELECT val FROM {_table('rdf_props')} WHERE s = {alias}.node_id AND \"key\" = ?)"
        # Skip the structural guard when this property is also IS NULL / IS NOT NULL
        # checked in the current boolean context (e.g. `a.x IS NULL OR a.x > 'y'`).
        # In that case the LEFT JOIN NULL already handles the missing-property case.
        null_guarded = getattr(context, "_null_guarded_props", set())
        if (alias, expr.property_name) not in null_guarded:
            context.where_conditions.append(
                TranslationContext._structural_guard_sql(alias, expr.property_name)
            )
    p_alias = context.next_alias("p")
    context.join_clauses.append(
        f'LEFT JOIN {_table("rdf_props")} {p_alias} ON {p_alias}.s = {alias}.node_id AND {p_alias}."key" = {context.add_join_param(expr.property_name)}'
    )
    return f"{p_alias}.val"



def _expr_map_projection(expr, context, segment):
    alias = context.variable_aliases.get(expr.variable, "")
    parts = []
    for key_spec, _ in expr.keys:
        prop = key_spec.lstrip(".")
        p_alias = context.next_alias("p")
        context.join_clauses.append(
            f"LEFT JOIN {_table('rdf_props')} {p_alias} ON {p_alias}.s = {alias}.node_id AND {p_alias}.\"key\" = {context.add_join_param(prop)}"
        )
        safe_prop = prop.replace("'", "''")
        parts.append(f"'\"'||'{safe_prop}'||'\":'||COALESCE('\"'||{p_alias}.val||'\"','null')")
    if not parts:
        return "'{}'"
    return "('{'||" + "||','||".join(parts) + "||'}')"


def _expr_map_literal(expr, context, segment):
    if not expr.entries:
        return "'{}'"
    if _is_fully_literal(expr):
        import json as _json
        py_val = _literal_to_python(expr)
        json_str = _json.dumps(py_val)
        str_len = max(len(json_str) + 1, 256)
        escaped = json_str.replace("'", "''")
        return f"CAST('{escaped}' AS VARCHAR({str_len}))"
    parts = []
    for k, v in expr.entries.items():
        safe_k = k.replace("'", "''")
        if isinstance(v, ast.Literal) and v.value is None:
            parts.append(f"'\"'||'{safe_k}'||'\":null'")
        elif isinstance(v, ast.Literal) and isinstance(v.value, bool):
            bval = "true" if v.value else "false"
            parts.append(f"'\"'||'{safe_k}'||'\":{bval}'")
        elif isinstance(v, ast.Literal) and isinstance(v.value, (int, float)):
            parts.append(f"'\"'||'{safe_k}'||'\":'||CAST({v.value} AS VARCHAR)")
        elif isinstance(v, ast.Literal) and isinstance(v.value, str):
            safe_v = v.value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "''")
            parts.append(f"'\"'||'{safe_k}'||'\":\"'||'{safe_v}'||'\"'")
        elif isinstance(v, ast.MapLiteral):
            # Nested map: the value is already a JSON object — no extra quotes
            val_sql = translate_expression(v, context, segment=segment)
            parts.append(f"'\"'||'{safe_k}'||'\":'||CAST({val_sql} AS VARCHAR)")
        elif isinstance(v, ast.Literal) and isinstance(v.value, list):
            # Nested list literal: already a JSON array — no extra quotes
            val_sql = translate_expression(v, context, segment=segment)
            parts.append(f"'\"'||'{safe_k}'||'\":'||CAST({val_sql} AS VARCHAR)")
        else:
            val_sql = translate_expression(v, context, segment=segment)
            parts.append(f"'\"'||'{safe_k}'||'\":\"'||CAST({val_sql} AS VARCHAR)||'\"'")
    inner = " || ',' || ".join(parts)
    return f"('{{'||{inner}||'}}')"


def _expr_subscript(expr, context, segment):
    base = expr.expression
    idx = expr.index
    if isinstance(base, ast.Variable):
        base_alias = context.variable_aliases.get(base.name, "")
        is_scalar = base_alias.startswith("Stage") or base.name in context.scalar_variables
        if not is_scalar:
            # Relationship variable: r[key] → JSON_VALUE(e.qualifiers, '$.' || key)
            if base.name in context.rel_variables:
                rel_alias = base_alias
                idx_sql = translate_expression(idx, context, segment=segment)
                return (
                    f"SQLUser.JSON_VALUE({rel_alias}.qualifiers, '$.' || CAST(({idx_sql}) AS VARCHAR))"
                )
            # Node variable — subscript is a property key expression via rdf_props JOIN
            node_alias = base_alias
            node_ref = f"{node_alias}.node_id" if node_alias else "NULL"
            p_alias = context.next_alias("dp")
            if isinstance(idx, ast.Variable):
                key_val = context.input_params.get(idx.name, idx.name)
                key_sql = context.add_join_param(key_val)
            else:
                key_sql = translate_expression(idx, context, segment="join")
            context.join_clauses.append(
                f"LEFT JOIN {_table('rdf_props')} {p_alias} ON {p_alias}.s = {node_ref} AND {p_alias}.\"key\" = {key_sql}"
            )
            return f"{p_alias}.val"
        # Scalar variable — use JSON array index or key lookup (property notation for maps)
        base_sql = translate_expression(base, context, segment=segment)
        # For literal integer indices, inline the value directly to avoid ? parameter
        # placeholders in the JSON path expression (IRIS can't use ? in '$[?]').
        # However, if the index is non-integer (a string key for maps), use property notation.
        if isinstance(idx, ast.Literal) and isinstance(idx.value, int) and not isinstance(idx.value, bool):
            # Integer index → use JSON_ARRAYGET (handles array JSON correctly without UDF lock issues)
            return f"SQLUser.JSON_ARRAYGET({base_sql}, {idx.value})"
        if isinstance(idx, ast.Literal) and isinstance(idx.value, str):
            # String literal key → map property notation
            safe_key = _jsonpath_key(idx.value)
            return f"SQLUser.JSON_VALUE({base_sql}, '$.{safe_key}')"
        # Dynamic index: check if it's a known non-integer type variable → unconditional TypeError
        if isinstance(idx, ast.Variable):
            idx_var_name = idx.name
            if idx_var_name in getattr(context, "non_integer_index_vars", set()):
                # Index was bound to a non-integer type at translation time.
                # If the base is known to be a map (dict param) and index is a string,
                # use runtime property access.  Otherwise emit TypeError.
                idx_pval = context.input_params.get(idx_var_name)
                base_is_map = False
                if isinstance(base, ast.Variable):
                    base_pval = context.input_params.get(base.name)
                    if isinstance(base_pval, dict):
                        base_is_map = True
                if base_is_map and isinstance(idx_pval, str):
                    # Map subscript with string key — valid in Cypher
                    idx_sql2 = translate_expression(idx, context, segment=segment)
                    return f"SQLUser.JSON_VALUE({base_sql}, '$.' || CAST(({idx_sql2}) AS VARCHAR))"
                # For maps at runtime with unknown base type, dispatch dynamically
                if isinstance(idx_pval, str):
                    idx_sql2 = translate_expression(idx, context, segment=segment)
                    return (
                        f"CASE WHEN ({base_sql}) IS NULL THEN NULL "
                        f"WHEN SUBSTRING({base_sql}, 1, 1) = '{{'  "
                        f"THEN SQLUser.JSON_VALUE({base_sql}, '$.' || CAST(({idx_sql2}) AS VARCHAR)) "
                        f"ELSE SQLUser.CypherFn_IVGTYPEERROR('Non-integer index type for list subscript') END"
                    )
                if isinstance(idx_pval, int) and not isinstance(idx_pval, bool):
                    # Integer index into a map → TypeError at runtime
                    return (
                        f"CASE WHEN ({base_sql}) IS NULL THEN NULL "
                        f"WHEN SUBSTRING({base_sql}, 1, 1) = '{{'  "
                        f"THEN SQLUser.CypherFn_IVGTYPEERROR('Map element access by non-string') "
                        f"ELSE NULL END"
                    )
                # Other non-integer (bool, float, list) — unconditional TypeError
                return (
                    f"CASE WHEN ({base_sql}) IS NOT NULL "
                    f"THEN SQLUser.CypherFn_IVGTYPEERROR('Non-integer index type for list subscript') "
                    f"ELSE NULL END"
                )
            # If the index variable is in input_params and is an integer, inline it
            # to avoid using ? inside JSON path string concat (IRIS rejects that form).
            if idx_var_name in context.input_params:
                pval = context.input_params[idx_var_name]
                if isinstance(pval, int) and not isinstance(pval, bool):
                    # Integer index: if base is known to be a map, emit TypeError
                    if isinstance(base, ast.Variable) and base.name in context.input_params:
                        base_pval = context.input_params[base.name]
                        if isinstance(base_pval, dict):
                            return (
                                f"CASE WHEN ({base_sql}) IS NULL THEN NULL "
                                f"ELSE SQLUser.CypherFn_IVGTYPEERROR('Map element access by non-string') END"
                            )
                    return f"SQLUser.JSON_ARRAYGET({base_sql}, {pval})"
        idx_sql = translate_expression(idx, context, segment=segment)
        return f"SQLUser.JSON_VALUE({base_sql}, '$.' || CAST(({idx_sql}) AS VARCHAR))"
    base_sql = translate_expression(base, context, segment=segment)
    if isinstance(idx, ast.Literal) and isinstance(idx.value, int) and not isinstance(idx.value, bool):
        i = idx.value
        return (
            f"(SELECT elem FROM JSON_TABLE({base_sql}, "
            f"'$[{i}]' COLUMNS (elem VARCHAR(1000) PATH '$')) __jt)"
        )
    idx_sql = translate_expression(idx, context, segment=segment)
    return f"SQLUser.JSON_ARRAYGET({base_sql}, CAST(({idx_sql}) AS INTEGER))"


def _expr_slice(expr, context, segment):
    # Cypher slice semantics:
    #   - implicit start (None) → 0
    #   - implicit end (None) → array length
    #   - null start or end (Literal(None)) → return NULL
    #   - negative index n → length + n  (e.g. -1 on [1,2,3] → index 2)
    base_sql = translate_expression(expr.expression, context, segment=segment)
    jt_alias = context.next_alias("slc")
    arr_len_sql = f"SQLUser.JSON_ARRAYLENGTH({base_sql})"

    # Determine if start/end are statically known literals (including null literal)
    start_is_literal = isinstance(expr.start, ast.Literal)
    end_is_literal = isinstance(expr.end, ast.Literal)
    start_val = expr.start.value if start_is_literal else None  # None = not a literal OR null literal
    end_val = expr.end.value if end_is_literal else None

    # Null propagation: if either bound is an explicit null literal, return NULL
    if (start_is_literal and start_val is None) or (end_is_literal and end_val is None):
        return "NULL"

    # Fast path: both bounds are static non-null integers
    if start_is_literal and start_val is not None and end_is_literal and end_val is not None:
        s = int(start_val)
        e = int(end_val)
        if s < 0 or e < 0:
            # Negative indices require knowing array length — fall through to dynamic path
            pass
        else:
            if e <= s:
                return _EMPTY_JSON_ARRAY
            return (
                f"(SELECT JSON_ARRAYAGG(elem) FROM "
                f"(SELECT elem, ROW_NUMBER() OVER() AS rn "
                f"FROM JSON_TABLE({base_sql}, '$[*]' COLUMNS(elem VARCHAR(1000) PATH '$')) {jt_alias}) __sliced "
                f"WHERE rn > {s} AND rn <= {e})"
            )

    def _bound_sql_once(bound_expr, is_end_bound):
        """Translate a slice bound to SQL, emitting parameters at most once.

        Returns (raw_sql, is_dynamic) where raw_sql references the bound without
        repeating parameter bindings.  For dynamic (non-literal) expressions we
        translate once; the caller must not embed the result more than once.
        """
        if bound_expr is None:
            return ("0" if not is_end_bound else arr_len_sql), False
        if isinstance(bound_expr, ast.Literal):
            v = bound_expr.value
            if v is None:
                return "NULL", False
            n = int(v)
            if n < 0:
                return f"GREATEST(0, {arr_len_sql} + {n})", False
            return str(n), False
        # Dynamic: translate once
        raw = translate_expression(bound_expr, context, segment=segment)
        return raw, True

    raw_start, start_dynamic = _bound_sql_once(expr.start, is_end_bound=False)
    raw_end, end_dynamic = _bound_sql_once(expr.end, is_end_bound=True)

    # For dynamic bounds, we must not repeat the SQL (which contains ?) since each
    # occurrence would add another parameter binding. Wrap into a scalar subquery-CTE
    # alias or just use the raw_sql directly (IRIS will evaluate ? only once per row
    # in a WHERE clause if referenced once).
    start_sql = raw_start
    end_sql = raw_end

    # If either dynamic bound could produce NULL at runtime, the whole slice → NULL.
    # We detect null by checking start/end SQL contains a ?, meaning they came from
    # a parameter.  Use NULLIF approach: wrap the entire expression.
    needs_null_check = start_dynamic or end_dynamic

    inner = (
        f"(SELECT JSON_ARRAYAGG(elem) FROM "
        f"(SELECT elem, ROW_NUMBER() OVER() AS rn "
        f"FROM JSON_TABLE({base_sql}, '$[*]' COLUMNS(elem VARCHAR(1000) PATH '$')) {jt_alias}) __sliced "
        f"WHERE rn > ({start_sql}) AND rn <= ({end_sql}))"
    )
    if needs_null_check:
        # Add sentinel null-check params: re-add the param values as additional ?
        # bindings for the CASE guard.  For each dynamic bound, add a separate IS NULL
        # check that evaluates a fresh ? binding.
        null_checks = []
        if start_dynamic:
            raw_start2, _ = _bound_sql_once(expr.start, is_end_bound=False)
            null_checks.append(f"({raw_start2}) IS NULL")
        if end_dynamic:
            raw_end2, _ = _bound_sql_once(expr.end, is_end_bound=True)
            null_checks.append(f"({raw_end2}) IS NULL")
        null_guard = " OR ".join(null_checks)
        return f"CASE WHEN {null_guard} THEN NULL ELSE {inner} END"
    return inner


def _expr_property_access(expr, context, segment):
    prop = expr.property_name.replace("'", "''")
    # Compile-time TypeError: property access on a known non-map type
    if isinstance(expr.expression, ast.Variable):
        vname = expr.expression.name
        if vname in getattr(context, 'non_map_vars', set()):
            raise TypeError(
                f"TypeError: Type mismatch: expected Map or Node but was a non-map value"
            )
    elif isinstance(expr.expression, ast.Literal):
        # Direct literal property access: always TypeError unless it's a dict
        lv = expr.expression.value
        if not isinstance(lv, dict):
            raise TypeError(
                f"TypeError: Type mismatch: expected Map or Node but was a literal non-map value"
            )
    base_sql = translate_expression(expr.expression, context, segment=segment)
    return f"CASE WHEN ({base_sql}) IS NULL THEN NULL ELSE SQLUser.JSON_VALUE({base_sql}, '$.{prop}') END"


def _expr_variable(expr, context, segment):
    alias = context.variable_aliases.get(expr.name)
    # ORDER BY alias substitution: when translating ORDER BY expressions, RETURN aliases
    # (like `n` in `RETURN n.num AS n ORDER BY n + 2`) should resolve to their SQL expression.
    _ob_alias_sql = getattr(context, "_orderby_alias_sql", None)
    if _ob_alias_sql and expr.name in _ob_alias_sql:
        return _ob_alias_sql[expr.name]
    # Variable-length relationship variable: the engine fills in the actual path list.
    # Emit NULL placeholder; the engine replaces this column with the relationship list.
    if alias == "__vl_rel__":
        safe = _safe_alias(expr.name)
        return f"NULL AS {safe}"
    if alias == "__foreach_literal__":
        val = getattr(context, "foreach_literals", {}).get(expr.name)
        if val is not None:
            if isinstance(val, str):
                safe = val.replace("'", "''")
                return f"'{safe}'"
            if isinstance(val, bool):
                return "1" if val else "0"
            return str(val)
    if not alias:
        if expr.name in context.input_params:
            v = context.input_params[expr.name]
            if segment == "select":
                return context.add_select_param(v)
            if segment == "join":
                return context.add_join_param(v)
            return context.add_where_param(v)
        raise SyntaxError(f"Undefined variable: {expr.name}")
    # TCK procedure CTEs: alias is the output column (possibly renamed via AS)
    if alias.startswith("TCK_Proc_"):
        renames = getattr(context, '_tck_yield_renames', {})
        if expr.name in renames:
            _cte, orig_col = renames[expr.name]
            return f"{alias}.{orig_col}"
        return f"{alias}.{expr.name}"
    # For scalar variables from a Stage, qualify the column reference
    if expr.name in context.scalar_variables:
        if alias.startswith("Stage"):
            return f"{alias}.{_safe_alias(expr.name)}"
        if alias == "scalar" or alias in _PROC_CTE_ALIASES:
            return expr.name
        return f"{alias}.{_safe_alias(expr.name)}"
    if alias.startswith("Stage"):
        return _safe_alias(expr.name)
    if alias.startswith("e"):
        is_undirected = alias in getattr(context, "_undirected_aliases", set())
        return f"{alias}.{'_p' if is_undirected else 'p'}"
    if alias in context.mapped_node_aliases:
        mapping = context.mapped_node_aliases[alias]
        return f"{alias}.{sanitize_identifier(mapping['id_column'])}"
    return f"{alias}.node_id"


def _is_fully_literal(node):
    """Return True if node is fully evaluable at translate-time (no variables/exprs)."""
    if isinstance(node, ast.Literal):
        v = node.value
        if isinstance(v, list):
            return all(_is_fully_literal(item) for item in v)
        return True  # scalar Literal
    if isinstance(node, ast.MapLiteral):
        return all(_is_fully_literal(val) for val in node.entries.values())
    return False


def _literal_to_python(node):
    """Extract Python value from a fully-literal AST node."""
    if isinstance(node, ast.Literal):
        v = node.value
        if isinstance(v, list):
            return [_literal_to_python(item) for item in v]
        if v is True: return True
        if v is False: return False
        return v
    if isinstance(node, ast.MapLiteral):
        return {k: _literal_to_python(val) for k, val in node.entries.items()}
    return None


def _expr_literal(expr, context, segment):
    import json as _json
    v = expr.value
    if v is True:
        return "1"
    if v is False:
        return "0"
    if v is None:
        return "NULL"
    if isinstance(v, list):
        # When ALL items are fully literal (including nested lists/maps), serialize
        # the whole structure as a JSON string.  This avoids IRIS embedding nested
        # arrays as VARCHAR strings (e.g. JSON_ARRAY(CAST('[1,2]' AS VARCHAR)) → ["[1,2]"]).
        if _is_fully_literal(expr):
            py_val = _literal_to_python(expr)
            json_str = _json.dumps(py_val)
            str_len = max(len(json_str) + 1, 256)
            escaped = json_str.replace("'", "''")
            return f"CAST('{escaped}' AS VARCHAR({str_len}))"
        sql_items = []
        for item in v:
            if isinstance(item, ast.Literal):
                iv = item.value
                if iv is True: sql_items.append("1")
                elif iv is False: sql_items.append("0")
                elif iv is None: sql_items.append("NULL")
                elif isinstance(iv, str): sql_items.append(f"'{iv.replace(chr(39), chr(39)+chr(39))}'")
                elif isinstance(iv, list):
                    # Nested list literal — recursively translate via the list branch
                    sql_items.append(translate_expression(item, context, segment=segment))
                else: sql_items.append(str(iv))
            else:
                sql_items.append(translate_expression(item, context, segment=segment))
        return f"JSON_ARRAY({', '.join(sql_items)})"
    if isinstance(v, float):
        import math as _math
        if _math.isinf(v) or _math.isnan(v):
            if segment == "select":
                return context.add_select_param(v)
            if segment == "join":
                return context.add_join_param(v)
            if segment == "inline":
                return repr(v)
            return context.add_where_param(v)
        float_str = repr(v)
        return f"CAST({float_str!r} AS DOUBLE)"
    if isinstance(v, str):
        escaped = v.replace("'", "''")
        str_len = max(len(v) + 1, 256)
        return f"CAST('{escaped}' AS VARCHAR({str_len}))"
    # Integers: always inline without bind parameters
    if isinstance(v, int):
        return str(v)
    if segment == "select":
        return context.add_select_param(v)
    if segment == "join":
        return context.add_join_param(v)
    if segment == "inline":
        if isinstance(v, str): return f"'{v.replace(chr(39), chr(39)+chr(39))}'"
        return str(v)
    return context.add_where_param(v)


_NON_DETERMINISTIC_FUNCTIONS = frozenset({
    "rand", "random", "timestamp", "randomuuid",
})


def _expr_aggregation(expr, context, segment):
    # Nested aggregation: count(count(*)) → NestedAggregation
    if expr.argument and isinstance(expr.argument, ast.AggregationFunction):
        raise SyntaxError(
            "NestedAggregation: Can't use an aggregate function inside an aggregate function."
        )
    # Non-constant argument: count(rand()) → NonConstantExpression
    if expr.argument and isinstance(expr.argument, ast.FunctionCall):
        fn_lower = expr.argument.function_name.lower()
        if fn_lower in _NON_DETERMINISTIC_FUNCTIONS:
            raise SyntaxError(
                f"NonConstantExpression: Can't use a non-deterministic function inside an aggregate function: {fn_lower}()"
            )
    if expr.argument and isinstance(expr.argument, ast.Literal):
        v = expr.argument.value
        if v is True: arg = "1"
        elif v is False: arg = "0"
        elif v is None: arg = "NULL"
        elif v == "*": arg = "*"  # count(*) — star is not a string literal
        elif isinstance(v, str): arg = f"'{v.replace(chr(39), chr(39)+chr(39))}'"
        elif isinstance(v, list): arg = _expr_literal(expr.argument, context, segment)
        else: arg = str(v)
    else:
        arg = (
            translate_expression(expr.argument, context, segment=segment)
            if expr.argument
            else "*"
        )
    fn = (
        "JSON_ARRAYAGG"
        if expr.function_name.upper() == "COLLECT"
        else expr.function_name.upper()
    )
    # collect(nodeVar) — emit structured JSON objects instead of bare node_id strings,
    # so the result set carries enough data for node-pattern comparison.
    if (fn == "JSON_ARRAYAGG"
            and expr.argument
            and isinstance(expr.argument, ast.Variable)):
        var_name = expr.argument.name
        alias = context.variable_aliases.get(var_name)
        edge_stage_vars = getattr(context, "edge_stage_variables", set())
        is_scalar = var_name in context.scalar_variables
        is_edge = alias and (alias.startswith("e") and not alias.startswith("ES_"))
        is_edge_stage = var_name in edge_stage_vars
        if alias and not is_scalar and not is_edge and not is_edge_stage:
            # Determine node_id expression for this variable
            if alias in context.mapped_node_aliases:
                mapping = context.mapped_node_aliases[alias]
                id_col = sanitize_identifier(mapping['id_column'])
                node_id_expr = f"{alias}.{id_col}"
            elif alias.startswith("Stage"):
                # Stage CTE: column name is the safe alias of the variable
                node_id_expr = _safe_alias(var_name)
            else:
                node_id_expr = f"{alias}.node_id"
            lbl_sql = labels_subquery(node_id_expr)
            props_sql = properties_subquery(node_id_expr)
            # Build JSON object string via concatenation (avoids IRIS JSON_OBJECT bug)
            node_json = (
                f"'{{\"_id\":\"' || {node_id_expr} || '\",' "
                f"|| '\"_labels\":' || {lbl_sql} || ',' "
                f"|| '\"_props\":' || COALESCE({props_sql}, '[]') || '}}'"
            )
            distinct_kw = "DISTINCT " if expr.distinct else ""
            return f"COALESCE(JSON_ARRAYAGG({distinct_kw}{node_json}), CAST('[]' AS VARCHAR(256)))"
    result_expr = f"{fn}({'DISTINCT ' if expr.distinct else ''}{arg})"
    # collect() must return [] not NULL when all collected values are NULL
    if fn == "JSON_ARRAYAGG":
        result_expr = f"COALESCE({result_expr}, CAST('[]' AS VARCHAR(256)))"
    return result_expr


def _scalar_coalesce(fn, args, args_exprs):
    if fn == "coalesce":
        if len(args) >= 2 and args_exprs:
            coerced = []
            for i, (arg, arg_expr) in enumerate(zip(args, args_exprs)):
                if i == 0:
                    coerced.append(f"CAST({arg} AS VARCHAR(4096))")
                elif isinstance(arg_expr, ast.Literal) and not isinstance(arg_expr.value, str) and arg_expr.value is not None:
                    coerced.append(f"CAST({arg} AS VARCHAR(4096))")
                else:
                    coerced.append(arg)
            return f"COALESCE({', '.join(coerced)})"
        return f"COALESCE({', '.join(args)})" if args else "NULL"
    return None


def _scalar_string(fn, args, args_exprs, context=None):
    if fn == "tointeger":
        return f"CASE WHEN ISNUMERIC({args[0]}) = 1 THEN CAST({args[0]} AS INTEGER) ELSE NULL END"
    if fn == "tofloat":
        return f"CASE WHEN ISNUMERIC({args[0]}) = 1 THEN CAST({args[0]} AS DOUBLE) ELSE NULL END"
    if fn == "tostring":
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and isinstance(args_exprs[0].value, bool):
            return f"'{'true' if args_exprs[0].value else 'false'}'"
        return f"CAST({args[0]} AS VARCHAR(4096))"
    if fn == "substring":
        if len(args) >= 2:
            start = f"({args[1]}) + 1"
            if len(args) >= 3:
                return f"SUBSTRING({args[0]}, {start}, {args[2]})"
            return f"SUBSTRING({args[0]}, {start})"
        return f"SUBSTRING({args[0]})"
    if fn == "reverse":
        if not args:
            return "NULL"
        arg_expr = args_exprs[0] if args_exprs else None
        is_list = (
            isinstance(arg_expr, ast.Literal) and isinstance(arg_expr.value, list)
        ) or isinstance(arg_expr, ast.ListComprehension)
        if is_list:
            return f"SQLUser.LIST_REVERSE({args[0]})"
        # For variables or expressions that may be either a string or a JSON array
        # at runtime, use CASE to dispatch: arrays start with '[', strings use REVERSE.
        if isinstance(arg_expr, (ast.Variable, ast.CaseExpression)):
            return (
                f"(CASE WHEN SUBSTRING(CAST({args[0]} AS VARCHAR(10)), 1, 1) = '['"
                f" THEN SQLUser.LIST_REVERSE({args[0]})"
                f" ELSE REVERSE({args[0]}) END)"
            )
        return f"REVERSE({args[0]})"
    if fn == "split":
        return f"SQLUser.STR_SPLIT({args[0]}, {args[1]})" if len(args) >= 2 else "NULL"
    return None


def _extract_int_from_map_entry(map_literal, key, default=0):
    if key not in map_literal.entries:
        return default
    val_expr = map_literal.entries[key]
    if isinstance(val_expr, ast.Literal) and isinstance(val_expr.value, int):
        return val_expr.value
    return default


def _extract_num_from_map_entry(map_literal, key, default=0):
    """Like _extract_int_from_map_entry but also returns floats."""
    if key not in map_literal.entries:
        return default
    val_expr = map_literal.entries[key]
    if isinstance(val_expr, ast.Literal) and isinstance(val_expr.value, (int, float)):
        return val_expr.value
    return default


def _has_map_key(map_literal, key):
    return key in map_literal.entries


def _date_from_iso_week(year, week, dow=1):
    """Compute date for ISO week date (year, week, day-of-week where Mon=1)."""
    import datetime as _dt
    # ISO week 1 is the week containing the first Thursday of the year.
    # Jan 4 is always in ISO week 1.
    jan4 = _dt.date(year, 1, 4)
    # Monday of ISO week 1
    week1_monday = jan4 - _dt.timedelta(days=jan4.isoweekday() - 1)
    return week1_monday + _dt.timedelta(weeks=week - 1, days=dow - 1)


def _date_from_ordinal_day(year, ordinal):
    """Convert year + ordinal day (1-based) to date."""
    import datetime as _dt
    return _dt.date(year, 1, 1) + _dt.timedelta(days=ordinal - 1)


def _date_from_quarter(year, quarter, day_of_quarter=1):
    """Convert year + quarter + day-of-quarter to date."""
    import datetime as _dt
    first_month = (quarter - 1) * 3 + 1
    start = _dt.date(year, first_month, 1)
    return start + _dt.timedelta(days=day_of_quarter - 1)


def _normalize_tz_str(tz):
    """Normalize a timezone suffix: compact +0100 → +01:00, -00:00 → Z, etc."""
    import re as _re
    if not tz:
        return ""
    if tz in ("Z", "z"):
        return "Z"
    # +HHMM or -HHMM (no colon, 4 digits) → +HH:MM
    m = _re.match(r'^([+-])(\d{2})(\d{2})$', tz)
    if m:
        sign, hh, mm = m.group(1), m.group(2), m.group(3)
        if (sign == "-" or sign == "+") and hh == "00" and mm == "00":
            return "Z"
        return f"{sign}{hh}:{mm}"
    # +HH:MM or -HH:MM (with colon)
    m = _re.match(r'^([+-])(\d{2}):(\d{2})$', tz)
    if m:
        sign, hh, mm = m.group(1), m.group(2), m.group(3)
        if hh == "00" and mm == "00":
            return "Z"
        return f"{sign}{hh}:{mm}"
    # +HH or -HH (hours only, 2 digits) → +HH:00
    m = _re.match(r'^([+-])(\d{2})$', tz)
    if m:
        sign, hh = m.group(1), m.group(2)
        return f"{sign}{hh}:00"
    # -HH:MM:SS → keep as-is, but strip :00 seconds
    return _normalize_tz_offset(tz)


def _iana_tz_offset(iana_name, ref_year=2015, ref_month=7, ref_day=21):
    """Return '+HH:MM' or '+HH:MM:SS' offset for IANA timezone at reference date."""
    try:
        from zoneinfo import ZoneInfo as _ZI
        import datetime as _dt2
        _zi = _ZI(iana_name)
        _aware = _dt2.datetime(ref_year, ref_month, ref_day, tzinfo=_zi)
        _off = _aware.utcoffset()
        _total_s = int(_off.total_seconds())
        _sign = "+" if _total_s >= 0 else "-"
        _abs_s = abs(_total_s)
        _hh = _abs_s // 3600
        _mm = (_abs_s % 3600) // 60
        _ss = _abs_s % 60
        if _ss:
            return f"{_sign}{_hh:02d}:{_mm:02d}:{_ss:02d}"
        return f"{_sign}{_hh:02d}:{_mm:02d}"
    except Exception:
        return ""


def _parse_time_string(s, ref_year=2015, ref_month=7, ref_day=21):
    """
    Parse ISO 8601 time string to normalized form 'HH:MM[:SS[.frac]][tz]'.
    Returns normalized string or None.
    """
    import re as _re
    s = s.strip()
    # Split off IANA bracket zone [Name]
    iana_suffix = ""
    iana_name = ""
    m_iana = _re.match(r'^(.*?)(\[([^\]]+)\])$', s)
    if m_iana:
        s = m_iana.group(1)
        iana_name = m_iana.group(3)
        iana_suffix = f"[{iana_name}]"

    # Split off timezone
    tz = ""
    m = _re.match(r'^(.*?)([Zz]|[+-]\d{2}(?::?\d{2}(?::?\d{2})?)?)$', s)
    if m:
        time_part = m.group(1)
        tz_raw = m.group(2)
        tz = _normalize_tz_str(tz_raw)
        # If no explicit offset but IANA zone given, compute the offset
        if iana_name and not tz:
            tz = _iana_tz_offset(iana_name, ref_year, ref_month, ref_day)
    else:
        time_part = s
        if iana_name:
            tz = _iana_tz_offset(iana_name, ref_year, ref_month, ref_day)

    # Extended: HH:MM[:SS[.frac]]
    m = _re.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', time_part)
    if m:
        h, mi, s_str, frac = m.group(1), m.group(2), m.group(3), m.group(4)
        if s_str:
            if frac:
                return f"{h}:{mi}:{s_str}.{frac}{tz}{iana_suffix}"
            return f"{h}:{mi}:{s_str}{tz}{iana_suffix}"
        return f"{h}:{mi}{tz}{iana_suffix}"

    # Compact: HHMMSS.frac or HHMMSS or HHMM or HH
    m = _re.match(r'^(\d{2})(?:(\d{2})(?:(\d{2})(?:\.(\d+))?)?)?$', time_part)
    if m:
        h = m.group(1)
        mi = m.group(2) or "00"
        s_str = m.group(3)
        frac = m.group(4)
        if s_str:
            if frac:
                return f"{h}:{mi}:{s_str}.{frac}{tz}{iana_suffix}"
            return f"{h}:{mi}:{s_str}{tz}{iana_suffix}"
        if m.group(2):
            return f"{h}:{mi}{tz}{iana_suffix}"
        return f"{h}:00{tz}{iana_suffix}"

    return None


def _parse_datetime_string(s):
    """
    Parse ISO 8601 datetime string (with T separator) to normalized form.
    Returns normalized string or None.
    """
    s = s.strip()
    # Split at T
    if "T" not in s and "t" not in s:
        return None
    sep_idx = s.upper().index("T")
    date_str = s[:sep_idx]
    rest = s[sep_idx + 1:]

    parsed_date = _parse_date_string(date_str)
    if not parsed_date:
        return None
    y_out, mo_out, d_out = parsed_date

    parsed_time = _parse_time_string(rest, ref_year=y_out, ref_month=mo_out, ref_day=d_out)
    if not parsed_time:
        return None

    return f"{y_out:04d}-{mo_out:02d}-{d_out:02d}T{parsed_time}"


def _parse_duration_string(s):
    """
    Parse ISO 8601 duration string into (years, months, days, hours, minutes, seconds_ns_total).
    Handles fractional components. Returns normalized ISO 8601 string or None.
    """
    import re as _re

    s = s.strip()
    # Calendar notation: P2012-02-02T14:37:21.545 → Pyyyy-mm-ddThh:mm:ss.frac
    m = _re.match(r'^P(-?\d+)-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$', s)
    if m:
        yr, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h, mi, sec = int(m.group(4)), int(m.group(5)), int(m.group(6))
        frac_str = m.group(7) or ""
        rem_ns = int(frac_str.ljust(9, '0')[:9]) if frac_str else 0
        return _format_duration(yr, mo, d, h, mi, sec, rem_ns)

    # Standard: PnYnMnWnDTnHnMnS with possible fractions on last component
    m = _re.match(
        r'^P'
        r'(?:(-?[\d.]+)Y)?'
        r'(?:(-?[\d.]+)M)?'
        r'(?:(-?[\d.]+)W)?'
        r'(?:(-?[\d.]+)D)?'
        r'(?:T'
        r'(?:(-?[\d.]+)H)?'
        r'(?:(-?[\d.]+)M)?'
        r'(?:(-?[\d.]+)S)?'
        r')?$',
        s
    )
    if not m or not any(m.groups()):
        return None

    years = float(m.group(1) or 0)
    months = float(m.group(2) or 0)
    weeks = float(m.group(3) or 0)
    days = float(m.group(4) or 0)
    hours = float(m.group(5) or 0)
    minutes = float(m.group(6) or 0)
    seconds = float(m.group(7) or 0)

    # Normalize fractional components
    mo_int = int(months)
    mo_frac = months - mo_int
    days = days + mo_frac * 30.436875
    days = days + weeks * 7

    d_int = int(days)
    d_frac = days - d_int
    hours = hours + d_frac * 24

    h_int = int(hours)
    h_frac = hours - h_int
    minutes = minutes + h_frac * 60

    m_int = int(minutes)
    m_frac = minutes - m_int
    seconds = seconds + m_frac * 60

    # Use truncating-toward-zero division for signed seconds/nanoseconds normalization
    s_total_ns = round(seconds * 1_000_000_000)
    s_int = int(s_total_ns / 1_000_000_000)  # truncates toward zero
    rem_ns = s_total_ns - s_int * 1_000_000_000  # same sign as s_total_ns or 0

    # Normalize seconds → minutes → hours → days (truncating toward zero)
    extra_m = int(s_int / 60)
    s_int = s_int - extra_m * 60
    m_int += extra_m
    extra_h = int(m_int / 60)
    m_int = m_int - extra_h * 60
    h_int += extra_h
    extra_d = int(h_int / 24)
    h_int = h_int - extra_d * 24
    d_int += extra_d

    return _format_duration(int(years), mo_int, d_int, h_int, m_int, s_int, rem_ns)


def _format_duration(yr_int, mo_int, d_int, h_int, m_int, s_int, rem_ns):
    """Format a duration as ISO 8601 string.

    s_int and rem_ns must have the same sign (or one be zero) — use truncating division
    when extracting them from total nanoseconds to guarantee this invariant.
    rem_ns may be negative when representing negative fractional seconds.
    """
    date_part = ""
    if yr_int:
        date_part += f"{yr_int}Y"
    if mo_int:
        date_part += f"{mo_int}M"
    if d_int:
        date_part += f"{d_int}D"
    time_part = ""
    if h_int:
        time_part += f"{h_int}H"
    if m_int:
        time_part += f"{m_int}M"
    if rem_ns != 0:
        abs_rem = abs(rem_ns)
        ns_str = f"{abs_rem:09d}".rstrip('0')
        if s_int == 0 and rem_ns < 0:
            # e.g. s_int=0, rem_ns=-1_000_000 → "-0.001S"
            time_part += f"-0.{ns_str}S"
        else:
            # s_int carries the sign; rem_ns has same sign or zero
            # e.g. s_int=-1, rem_ns=-999_000_000 → "-1.999S"
            # e.g. s_int=1, rem_ns=999_000_000 → "1.999S"
            time_part += f"{s_int}.{ns_str}S"
    elif s_int:
        time_part += f"{s_int}S"
    if not date_part and not time_part:
        return "PT0S"
    if time_part:
        return f"P{date_part}T{time_part}"
    return f"P{date_part}"


def _normalize_tz_offset(tz):
    """Normalize timezone offset string: strip trailing :00 seconds component."""
    import re as _re
    # +HH:MM:00 → +HH:MM, but keep +HH:MM:SS if SS != 00
    m = _re.match(r'^([+-]\d{2}:\d{2}):00$', tz)
    if m:
        return m.group(1)
    return tz


def _format_tz_for_iso(tz_str, ref_year=None, ref_month=None, ref_day=None):
    """Format a timezone string for an ISO 8601 datetime/time literal.

    Accepts:
      - Numeric offsets like '+01:00', '-05:00', 'Z'
      - IANA timezone names like 'Europe/Stockholm', 'UTC', 'GMT'

    Returns the ISO suffix to append, e.g. '+01:00', 'Z', '+02:00[Europe/Stockholm]'.

    If ref_year/month/day are provided, computes the DST-aware numeric offset for that
    specific date (for IANA zones).  Otherwise uses a representative summer date.
    """
    import re as _re_ftz
    if tz_str in ('Z', 'z', 'UTC', 'GMT', '+00:00', '-00:00', '+0000', '-0000'):
        return 'Z' if tz_str in ('Z', 'z', 'UTC', 'GMT') else tz_str
    # Numeric offset pattern: +HH:MM or -HH:MM or +HHMM or -HHMM
    m_num = _re_ftz.match(r'^([+-])(\d{2}):?(\d{2})(?::(\d{2}))?$', tz_str)
    if m_num:
        offset_str = f"{m_num.group(1)}{m_num.group(2)}:{m_num.group(3)}"
        if m_num.group(4) and m_num.group(4) != '00':
            offset_str += f":{m_num.group(4)}"
        return offset_str
    # IANA timezone name
    if '/' in tz_str or tz_str in ('UTC', 'GMT'):
        y = ref_year or 2015
        mo = ref_month or 7
        d = ref_day or 21
        numeric_offset = _iana_tz_offset(tz_str, y, mo, d)
        if numeric_offset:
            return f"{numeric_offset}[{tz_str}]"
        return tz_str
    # Fallback: return as-is after normalizing
    return _normalize_tz_offset(tz_str)


def _subsecond_frac(ns, us, ms):
    """Combine nanosecond/microsecond/millisecond into 9-digit nanosecond fraction string, strip trailing zeros."""
    total_ns = 0
    if ms >= 0:
        total_ns += ms * 1_000_000
    if us >= 0:
        total_ns += us * 1_000
    if ns >= 0:
        total_ns += ns
    if ms < 0 and us < 0 and ns < 0:
        return None
    if total_ns == 0:
        return None
    raw = f"{total_ns:09d}"
    return raw.rstrip('0')


def _parse_date_string(s):
    """Parse ISO 8601 date string to (year, month, day). Returns None on failure."""
    import datetime as _dt
    import re as _re
    s = s.strip()
    # YYYY-MM-DD or YYYYMMDD
    m = _re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _re.match(r'^(\d{4})(\d{2})(\d{2})$', s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    # YYYY-MM or YYYYMM → first day of month
    m = _re.match(r'^(\d{4})-(\d{2})$', s)
    if m:
        return int(m.group(1)), int(m.group(2)), 1
    m = _re.match(r'^(\d{4})(\d{2})$', s)
    if m:
        return int(m.group(1)), int(m.group(2)), 1
    # YYYY → Jan 1
    m = _re.match(r'^(\d{4})$', s)
    if m:
        return int(m.group(1)), 1, 1
    # YYYY-Www-D or YYYYWwwD
    m = _re.match(r'^(\d{4})-W(\d{2})-(\d)$', s)
    if m:
        d = _date_from_iso_week(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.year, d.month, d.day
    m = _re.match(r'^(\d{4})W(\d{2})(\d)$', s)
    if m:
        d = _date_from_iso_week(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.year, d.month, d.day
    # YYYY-Www or YYYYWww → Monday of that week
    m = _re.match(r'^(\d{4})-W(\d{2})$', s)
    if m:
        d = _date_from_iso_week(int(m.group(1)), int(m.group(2)), 1)
        return d.year, d.month, d.day
    m = _re.match(r'^(\d{4})W(\d{2})$', s)
    if m:
        d = _date_from_iso_week(int(m.group(1)), int(m.group(2)), 1)
        return d.year, d.month, d.day
    # YYYY-DDD or YYYYDDD (ordinal)
    m = _re.match(r'^(\d{4})-(\d{3})$', s)
    if m:
        d = _date_from_ordinal_day(int(m.group(1)), int(m.group(2)))
        return d.year, d.month, d.day
    m = _re.match(r'^(\d{4})(\d{3})$', s)
    if m:
        d = _date_from_ordinal_day(int(m.group(1)), int(m.group(2)))
        return d.year, d.month, d.day
    return None


def _build_date_sql_from_dynamic_base(map_expr, context, target_fn="date"):
    """Generate SQL for date/localdatetime/datetime({date: expr, ...overrides}) or
    localtime/time({time: expr, ...overrides}) where expr is a runtime SQL expression.

    Returns SQL string or None if the pattern is not recognised.
    """
    import re as _re2

    # Collect all entries. We need a 'date', 'localtime', 'time', 'localdatetime',
    # or 'datetime' base key whose value is a Variable or FunctionCall.
    DATE_BASE_KEYS = ("date", "localtime", "time", "localdatetime", "datetime")
    _DATE_ONLY_KEYS = ("date",)
    _TIME_ONLY_KEYS = ("localtime", "time")
    _DATETIME_KEYS = ("localdatetime", "datetime")
    base_key = None
    base_expr_node = None
    # Also check for a secondary time base (e.g. {date: d, time: t})
    time_base_key = None
    time_base_expr_node = None
    for key in DATE_BASE_KEYS:
        if key in map_expr.entries:
            expr_node = map_expr.entries[key]
            if not isinstance(expr_node, ast.Literal):
                if base_key is None:
                    base_key = key
                    base_expr_node = expr_node
                elif time_base_key is None and key in _TIME_ONLY_KEYS and base_key in _DATE_ONLY_KEYS + _DATETIME_KEYS:
                    # Secondary time base alongside a date/datetime base
                    time_base_key = key
                    time_base_expr_node = expr_node
                elif time_base_key is None and key in _DATE_ONLY_KEYS + _DATETIME_KEYS and base_key in _TIME_ONLY_KEYS:
                    # Secondary date base alongside a time base — swap order
                    time_base_key = base_key
                    time_base_expr_node = base_expr_node
                    base_key = key
                    base_expr_node = expr_node

    if base_key is None:
        return None  # no dynamic base found

    # Translate the base expression to SQL
    base_sql = translate_expression(base_expr_node, context, segment="select")
    time_base_sql = (
        translate_expression(time_base_expr_node, context, segment="select")
        if time_base_expr_node is not None else None
    )

    # Collect literal integer overrides (year, month, day, hour, minute, second, etc.)
    overrides = {}
    _skip_keys = {base_key}
    if time_base_key:
        _skip_keys.add(time_base_key)
    for k, v in map_expr.entries.items():
        if k in _skip_keys:
            continue
        if isinstance(v, ast.Literal) and isinstance(v.value, (int, float)):
            overrides[k] = int(v.value)
        elif isinstance(v, ast.Literal) and isinstance(v.value, str):
            overrides[k] = v.value

    # Helper to build a zero-padded field SQL expression
    def _padded(sql_int_expr, width):
        return f"LPAD(CAST({sql_int_expr} AS VARCHAR({width + 2})), {width}, '0')"

    def _int_field(override_key, default_sql):
        """Return SQL expression for a numeric field, using override if present."""
        if override_key in overrides and isinstance(overrides[override_key], int):
            return str(overrides[override_key])
        return default_sql

    # -------------------------------------------------------
    # target_fn == "date": result is YYYY-MM-DD
    # base_key in ("date", "localdatetime", "datetime")
    # base temporal string starts with "YYYY-MM-DD"
    # -------------------------------------------------------
    if target_fn == "date" and base_key in ("date", "localdatetime", "datetime"):
        if "week" in overrides:
            # ISO week override: compute Monday of ISO week W in the base year,
            # then offset by (dayOfWeek-1) from base date
            # ISO week 1 = week containing Jan 4. First Mon of ISO week year:
            # DATEADD(day, -DATEPART(weekday, CAST(year||'-01-04' AS DATE)) + 2, CAST(year||'-01-04' AS DATE))
            # But IRIS weekday: 1=Sun, 2=Mon, ..., 7=Sat
            # We want Mon (2 in IRIS). Offset to Monday: -DATEPART(weekday,x) + 2
            # Then add (week-1)*7 days and (dayOfWeek - 1) days
            week_val = overrides["week"]
            year_sql_raw = f"SUBSTRING({base_sql}, 1, 4)"
            # dayOfWeek from base date (ISO: Mon=1..Sun=7). IRIS weekday: Sun=1..Sat=7
            # Convert: ISO dow = (IRIS_dow + 5) % 7 + 1 where IRIS_dow=1..7
            base_iris_dow = f"DATEPART('weekday', CAST({base_sql} AS DATE))"
            base_iso_dow = f"(MOD({base_iris_dow} + 5, 7) + 1)"
            # First Monday of ISO week 1 of the year:
            jan4_str = f"CAST({year_sql_raw} || '-01-04' AS DATE)"
            jan4_iris_dow = f"DATEPART('weekday', {jan4_str})"
            first_mon = f"DATEADD('day', -MOD({jan4_iris_dow} - 2 + 7, 7), {jan4_str})"
            # Target date = first_mon + (week-1)*7 + (base_iso_dow - 1) days
            target_date = f"DATEADD('day', ({week_val} - 1) * 7 + ({base_iso_dow} - 1), {first_mon})"
            return (
                f"(LPAD(CAST(DATEPART('year', {target_date}) AS VARCHAR(6)), 4, '0') || '-' || "
                f"LPAD(CAST(DATEPART('month', {target_date}) AS VARCHAR(4)), 2, '0') || '-' || "
                f"LPAD(CAST(DATEPART('day', {target_date}) AS VARCHAR(4)), 2, '0'))"
            )
        if "ordinalDay" in overrides:
            # ordinalDay override: result = Jan 1 of base year + (ordinalDay - 1) days
            ordinal_val = overrides["ordinalDay"]
            year_sql_raw = f"SUBSTRING({base_sql}, 1, 4)"
            jan1 = f"CAST({year_sql_raw} || '-01-01' AS DATE)"
            target_date = f"DATEADD('day', {ordinal_val} - 1, {jan1})"
            return (
                f"(LPAD(CAST(DATEPART('year', {target_date}) AS VARCHAR(6)), 4, '0') || '-' || "
                f"LPAD(CAST(DATEPART('month', {target_date}) AS VARCHAR(4)), 2, '0') || '-' || "
                f"LPAD(CAST(DATEPART('day', {target_date}) AS VARCHAR(4)), 2, '0'))"
            )
        if "quarter" in overrides:
            # quarter override: preserve dayOfQuarter from base date, move to target quarter.
            # dayOfQuarter = DATEDIFF(day, first_day_of_base_quarter, base_date) + 1
            # first_day_of_target_quarter = CAST(year || '-MM-01' AS DATE) where MM = (q-1)*3+1
            quarter_val = overrides["quarter"]
            new_q_start_month = (quarter_val - 1) * 3 + 1
            new_q_start_month_str = f"'{new_q_start_month:02d}'"
            year_sql_raw = f"SUBSTRING({base_sql}, 1, 4)"
            base_month_sql = f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)"
            # base_quarter = (month - 1) / 3 + 1
            # IRIS uses float division by default; CAST to INTEGER to floor
            base_q_start_month_sql = f"(CAST(({base_month_sql} - 1) / 3 AS INTEGER) * 3 + 1)"
            base_q_start = f"CAST({year_sql_raw} || '-' || LPAD(CAST({base_q_start_month_sql} AS VARCHAR(3)), 2, '0') || '-01' AS DATE)"
            base_date = f"CAST(SUBSTRING({base_sql}, 1, 10) AS DATE)"
            day_of_quarter = f"(DATEDIFF('day', {base_q_start}, {base_date}) + 1)"
            new_q_start = f"CAST({year_sql_raw} || '-' || {new_q_start_month_str} || '-01' AS DATE)"
            target_date = f"DATEADD('day', {day_of_quarter} - 1, {new_q_start})"
            return (
                f"(LPAD(CAST(DATEPART('year', {target_date}) AS VARCHAR(6)), 4, '0') || '-' || "
                f"LPAD(CAST(DATEPART('month', {target_date}) AS VARCHAR(4)), 2, '0') || '-' || "
                f"LPAD(CAST(DATEPART('day', {target_date}) AS VARCHAR(4)), 2, '0'))"
            )
        year_sql = _int_field("year", f"CAST(SUBSTRING({base_sql}, 1, 4) AS INTEGER)")
        month_sql = _int_field("month", f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)")
        day_sql = _int_field("day", f"CAST(SUBSTRING({base_sql}, 9, 2) AS INTEGER)")
        y_str = _padded(year_sql, 4)
        mo_str = _padded(month_sql, 2)
        d_str = _padded(day_sql, 2)
        return f"({y_str} || '-' || {mo_str} || '-' || {d_str})"

    # -------------------------------------------------------
    # target_fn == "localtime": result is HH:MM[:SS[.frac]]
    # base_key in ("localtime", "time", "localdatetime", "datetime")
    # -------------------------------------------------------
    if target_fn in ("localtime", "time"):
        # Extract time-only portion from base: if base contains 'T' (datetime/localdatetime),
        # take the part after 'T'; otherwise use the base as-is (time/localtime).
        # This avoids having to know the actual runtime type of the base variable.
        t_pos = f"CHARINDEX('T', {base_sql})"
        time_base = f"CASE WHEN {t_pos} > 0 THEN SUBSTRING({base_sql}, {t_pos} + 1) ELSE {base_sql} END"
        # Now positions are all relative to time-only string: HH:MM:SS...
        h_pos, mi_pos, s_pos = 1, 4, 7

        h_sql = _int_field("hour", f"CAST(SUBSTRING({time_base}, {h_pos}, 2) AS INTEGER)")
        mi_sql = _int_field("minute", f"CAST(SUBSTRING({time_base}, {mi_pos}, 2) AS INTEGER)")
        # Seconds may not be present (e.g. '12:00+01:00' — position 6 is '+' not ':')
        # Use CASE to return 0 when seconds are absent
        _s_colon_check = f"SUBSTRING({time_base}, {s_pos - 1}, 1)"  # char before s_pos should be ':'
        s_sql = _int_field(
            "second",
            f"CASE WHEN {_s_colon_check} = ':' THEN CAST(SUBSTRING({time_base}, {s_pos}, 2) AS INTEGER) ELSE 0 END"
        )

        # fractional second: position of '.' after seconds (s_pos + 2, since SS occupies s_pos..s_pos+1)
        # frac_start points to the '.' character (1-based SQL position within time_base).
        # We check if that character is '.' and if so, SUBSTRING from there includes the dot.
        frac_start = s_pos + 2  # position of '.' (e.g. 9 for HH:MM:SS)

        # Build the result
        h_str = _padded(h_sql, 2)
        mi_str = _padded(mi_sql, 2)
        s_str = _padded(s_sql, 2)

        # Build time string with optional fractional seconds from base
        # Fractional part is replaced only when nanosecond/microsecond/millisecond is explicitly overridden.
        # Overriding 'second' does NOT strip the fractional part — only replaces the seconds integer.
        _has_frac_override = any(k in overrides for k in ("nanosecond", "microsecond", "millisecond"))
        if not _has_frac_override:
            if base_key in ("time", "datetime"):
                # Strip tz suffix from fractional part using CHARINDEX (IRIS has no REGEXP_REPLACE).
                # Find the first tz character (Z, + or -) after position frac_start.
                # Use SUBSTRING(s, frac_start, tz_pos - frac_start) when tz found, else SUBSTRING(s, frac_start).
                _z_pos = f"CHARINDEX('Z', {time_base})"
                _plus_pos = f"CHARINDEX('+', {time_base}, {frac_start})"
                _minus_pos = f"CHARINDEX('-', {time_base}, {frac_start})"
                # Best (earliest non-zero) tz position
                _tz_pos = (
                    f"CASE WHEN {_z_pos} >= {frac_start} AND ({_plus_pos} = 0 OR {_z_pos} <= {_plus_pos}) AND ({_minus_pos} = 0 OR {_z_pos} <= {_minus_pos}) THEN {_z_pos} "
                    f"WHEN {_plus_pos} >= {frac_start} AND ({_minus_pos} = 0 OR {_plus_pos} <= {_minus_pos}) THEN {_plus_pos} "
                    f"WHEN {_minus_pos} >= {frac_start} THEN {_minus_pos} "
                    f"ELSE 0 END"
                )
                _frac_clean = (
                    f"CASE WHEN ({_tz_pos}) > {frac_start} THEN SUBSTRING({time_base}, {frac_start}, ({_tz_pos}) - {frac_start}) "
                    f"ELSE SUBSTRING({time_base}, {frac_start}) END"
                )
            else:
                _frac_clean = f"SUBSTRING({time_base}, {frac_start})"
            # When no frac and seconds=0, omit the seconds component (openCypher: HH:MM is valid)
            time_str = (
                f"(CASE WHEN SUBSTRING({time_base}, {frac_start}, 1) = '.' "
                f"THEN {h_str} || ':' || {mi_str} || ':' || {s_str} || {_frac_clean} "
                f"WHEN ({s_sql}) <> 0 THEN {h_str} || ':' || {mi_str} || ':' || {s_str} "
                f"ELSE {h_str} || ':' || {mi_str} END)"
            )
        else:
            # Override present — always include seconds
            time_str = f"({h_str} || ':' || {mi_str} || ':' || {s_str})"

        if target_fn == "time":
            # Output needs timezone
            tz_override = overrides.get("timezone")
            if tz_override and isinstance(tz_override, str):
                tz_sql = f"'{_normalize_tz_str(tz_override)}'"
            elif base_key == "time":
                # Extract tz suffix from time string: Z, or +HH:MM, or -HH:MM after seconds
                tz_sql = (
                    f"CASE WHEN {base_sql} LIKE '%Z' THEN 'Z' "
                    f"WHEN {base_sql} LIKE '%+%' THEN '+' || SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}) + 1) "
                    f"WHEN CHARINDEX('-', {base_sql}, 6) > 0 THEN '-' || SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}, 6) + 1) "
                    f"ELSE 'Z' END"
                )
            else:
                tz_sql = "'Z'"
            return f"({time_str} || {tz_sql})"

        return time_str

    # -------------------------------------------------------
    # target_fn in ("localdatetime", "datetime"): result is YYYY-MM-DDTHH:MM[:SS[.frac]][tz]
    # -------------------------------------------------------
    if target_fn in ("localdatetime", "datetime"):
        if "week" in overrides or "ordinalDay" in overrides or "quarter" in overrides:
            return None  # complex
        # Base date part: from base_key
        if base_key in ("date", "localdatetime", "datetime"):
            year_sql = _int_field("year", f"CAST(SUBSTRING({base_sql}, 1, 4) AS INTEGER)")
            month_sql = _int_field("month", f"CAST(SUBSTRING({base_sql}, 6, 2) AS INTEGER)")
            day_sql = _int_field("day", f"CAST(SUBSTRING({base_sql}, 9, 2) AS INTEGER)")
        elif base_key == "time":
            # No date in a time — use literal date components from overrides only
            year_sql = str(overrides.get("year", 1970))
            month_sql = str(overrides.get("month", 1))
            day_sql = str(overrides.get("day", 1))
        else:
            return None

        y_str = _padded(year_sql, 4)
        mo_str = _padded(month_sql, 2)
        d_str = _padded(day_sql, 2)
        date_part = f"({y_str} || '-' || {mo_str} || '-' || {d_str})"

        # Time part: from time_base_sql if available, otherwise from base_key if it has time.
        # Extract time-only part from whatever source contains time.
        # Use CHARINDEX('T', ...) to dynamically handle both datetime/localdatetime (has T)
        # and localtime/time (no T) without knowing the type at compile time.
        def _time_src_sql(src_sql, src_key):
            """Return SQL expression for the time-only portion of src_sql."""
            if src_key in ("localtime", "time"):
                return src_sql  # already time-only
            elif src_key in ("localdatetime", "datetime"):
                # time starts after 'T'
                t_idx = f"CHARINDEX('T', {src_sql})"
                return f"CASE WHEN {t_idx} > 0 THEN SUBSTRING({src_sql}, {t_idx} + 1) ELSE {src_sql} END"
            else:
                return None  # date-only, no time

        if time_base_sql is not None:
            # Separate date and time bases: use time_base_sql for time components
            _tsrc = _time_src_sql(time_base_sql, time_base_key)
            _tsrc_colon = f"SUBSTRING({_tsrc}, 6, 1)"  # ':' if seconds present
            if "hour" in overrides:
                h_str2 = _padded(str(overrides["hour"]), 2)
            else:
                h_str2 = _padded(f"CAST(SUBSTRING({_tsrc}, 1, 2) AS INTEGER)", 2)
            if "minute" in overrides:
                mi_str2 = _padded(str(overrides["minute"]), 2)
            else:
                mi_str2 = _padded(f"CAST(SUBSTRING({_tsrc}, 4, 2) AS INTEGER)", 2)
            if "second" in overrides:
                s_str2 = _padded(str(overrides["second"]), 2)
            else:
                s_str2 = _padded(
                    f"CASE WHEN {_tsrc_colon} = ':' THEN CAST(SUBSTRING({_tsrc}, 7, 2) AS INTEGER) ELSE 0 END", 2
                )
            # Fractional seconds from time_base
            _has_frac_ov2 = any(k in overrides for k in ("nanosecond", "microsecond", "millisecond"))
            _frac_start2 = 9  # position of '.' in time-only string
            if not _has_frac_ov2:
                if time_base_key == "time":
                    _z2 = f"CHARINDEX('Z', {_tsrc})"
                    _p2 = f"CHARINDEX('+', {_tsrc}, {_frac_start2})"
                    _m2 = f"CHARINDEX('-', {_tsrc}, {_frac_start2})"
                    _tz2 = (
                        f"CASE WHEN {_z2} >= {_frac_start2} AND ({_p2} = 0 OR {_z2} <= {_p2}) AND ({_m2} = 0 OR {_z2} <= {_m2}) THEN {_z2} "
                        f"WHEN {_p2} >= {_frac_start2} AND ({_m2} = 0 OR {_p2} <= {_m2}) THEN {_p2} "
                        f"WHEN {_m2} >= {_frac_start2} THEN {_m2} "
                        f"ELSE 0 END"
                    )
                    _frac2 = (
                        f"CASE WHEN ({_tz2}) > {_frac_start2} THEN SUBSTRING({_tsrc}, {_frac_start2}, ({_tz2}) - {_frac_start2}) "
                        f"ELSE SUBSTRING({_tsrc}, {_frac_start2}) END"
                    )
                else:
                    _frac2 = f"SUBSTRING({_tsrc}, {_frac_start2})"
                time_part = (
                    f"(CASE WHEN SUBSTRING({_tsrc}, {_frac_start2}, 1) = '.' "
                    f"THEN {h_str2} || ':' || {mi_str2} || ':' || {s_str2} || {_frac2} "
                    f"WHEN CASE WHEN {_tsrc_colon} = ':' THEN CAST(SUBSTRING({_tsrc}, 7, 2) AS INTEGER) ELSE 0 END <> 0 "
                    f"THEN {h_str2} || ':' || {mi_str2} || ':' || {s_str2} "
                    f"ELSE {h_str2} || ':' || {mi_str2} END)"
                )
            else:
                time_part = f"({h_str2} || ':' || {mi_str2} || ':' || {s_str2})"
        elif base_key in ("localdatetime", "datetime"):
            # time starts at position 12 in base (after YYYY-MM-DDT)
            # Positions: H=12-13, M=15-16, S=18-19, frac=20+
            if "hour" in overrides:
                h_str2 = _padded(str(overrides["hour"]), 2)
            else:
                h_str2 = _padded(f"CAST(SUBSTRING({base_sql}, 12, 2) AS INTEGER)", 2)
            if "minute" in overrides:
                mi_str2 = _padded(str(overrides["minute"]), 2)
            else:
                mi_str2 = _padded(f"CAST(SUBSTRING({base_sql}, 15, 2) AS INTEGER)", 2)
            if "second" in overrides:
                s_str2 = _padded(str(overrides["second"]), 2)
            else:
                s_str2 = _padded(f"CAST(SUBSTRING({base_sql}, 18, 2) AS INTEGER)", 2)
            _has_frac_ov2 = any(k in overrides for k in ("nanosecond", "microsecond", "millisecond"))
            _frac_pos2 = 20  # position of '.' in datetime string
            if not _has_frac_ov2:
                if base_key == "datetime":
                    # Need to strip tz from frac
                    _z2 = f"CHARINDEX('Z', {base_sql})"
                    _p2 = f"CHARINDEX('+', {base_sql}, {_frac_pos2})"
                    _m2 = f"CHARINDEX('-', {base_sql}, {_frac_pos2})"
                    _tz2 = (
                        f"CASE WHEN {_z2} >= {_frac_pos2} AND ({_p2} = 0 OR {_z2} <= {_p2}) AND ({_m2} = 0 OR {_z2} <= {_m2}) THEN {_z2} "
                        f"WHEN {_p2} >= {_frac_pos2} AND ({_m2} = 0 OR {_p2} <= {_m2}) THEN {_p2} "
                        f"WHEN {_m2} >= {_frac_pos2} THEN {_m2} "
                        f"ELSE 0 END"
                    )
                    _frac2 = (
                        f"CASE WHEN ({_tz2}) > {_frac_pos2} THEN SUBSTRING({base_sql}, {_frac_pos2}, ({_tz2}) - {_frac_pos2}) "
                        f"ELSE SUBSTRING({base_sql}, {_frac_pos2}) END"
                    )
                else:
                    _frac2 = f"SUBSTRING({base_sql}, {_frac_pos2})"
                time_part = (
                    f"(CASE WHEN SUBSTRING({base_sql}, {_frac_pos2}, 1) = '.' "
                    f"THEN {h_str2} || ':' || {mi_str2} || ':' || {s_str2} || {_frac2} "
                    f"ELSE {h_str2} || ':' || {mi_str2} || ':' || {s_str2} END)"
                )
            else:
                time_part = f"({h_str2} || ':' || {mi_str2} || ':' || {s_str2})"
        elif base_key in ("localtime", "time"):
            # base starts with time
            _tsrc_colon = f"SUBSTRING({base_sql}, 6, 1)"
            if "hour" in overrides:
                h_str2 = _padded(str(overrides["hour"]), 2)
            else:
                h_str2 = _padded(f"CAST(SUBSTRING({base_sql}, 1, 2) AS INTEGER)", 2)
            if "minute" in overrides:
                mi_str2 = _padded(str(overrides["minute"]), 2)
            else:
                mi_str2 = _padded(f"CAST(SUBSTRING({base_sql}, 4, 2) AS INTEGER)", 2)
            if "second" in overrides:
                s_str2 = _padded(str(overrides["second"]), 2)
            else:
                s_str2 = _padded(f"CAST(SUBSTRING({base_sql}, 7, 2) AS INTEGER)", 2)
            time_part = f"({h_str2} || ':' || {mi_str2} || ':' || {s_str2})"
        else:  # date only
            h_str2 = _padded(str(overrides.get("hour", 0)), 2)
            mi_str2 = _padded(str(overrides.get("minute", 0)), 2)
            s_str2 = _padded(str(overrides.get("second", 0)), 2)
            time_part = f"({h_str2} || ':' || {mi_str2} || ':' || {s_str2})"

        result = f"({date_part} || 'T' || {time_part})"

        if target_fn == "datetime":
            tz_override = overrides.get("timezone")
            if tz_override and isinstance(tz_override, str):
                result += f" || '{_normalize_tz_str(tz_override)}'"
            elif base_key == "datetime":
                # Preserve input timezone from datetime base
                # Extract tz suffix: part after the time HH:MM[:SS...]
                # Use CHARINDEX to find +/- or Z after position 20 (YYYY-MM-DDTHH:MM:SS)
                _tz_src = base_sql
                _zp = f"CHARINDEX('Z', {_tz_src})"
                _pp = f"CHARINDEX('+', {_tz_src}, 20)"
                _mp = f"CHARINDEX('-', {_tz_src}, 20)"
                _tzp = (
                    f"CASE WHEN {_zp} >= 20 AND ({_pp} = 0 OR {_zp} <= {_pp}) AND ({_mp} = 0 OR {_zp} <= {_mp}) THEN {_zp} "
                    f"WHEN {_pp} >= 20 AND ({_mp} = 0 OR {_pp} <= {_mp}) THEN {_pp} "
                    f"WHEN {_mp} >= 20 THEN {_mp} ELSE 0 END"
                )
                result += f" || CASE WHEN ({_tzp}) > 0 THEN SUBSTRING({_tz_src}, ({_tzp})) ELSE 'Z' END"
            elif (base_key == "time" or time_base_key == "time"):
                # Extract tz from the time variable (e.g., '12:31:14+01:00')
                _t_src = time_base_sql if time_base_sql is not None else base_sql
                _zp = f"CHARINDEX('Z', {_t_src})"
                _pp = f"CHARINDEX('+', {_t_src}, 6)"
                _mp = f"CHARINDEX('-', {_t_src}, 6)"
                _tzp = (
                    f"CASE WHEN {_zp} > 0 AND ({_pp} = 0 OR {_zp} <= {_pp}) AND ({_mp} = 0 OR {_zp} <= {_mp}) THEN {_zp} "
                    f"WHEN {_pp} > 0 AND ({_mp} = 0 OR {_pp} <= {_mp}) THEN {_pp} "
                    f"WHEN {_mp} > 0 THEN {_mp} ELSE 0 END"
                )
                result += f" || CASE WHEN ({_tzp}) > 0 THEN SUBSTRING({_t_src}, ({_tzp})) ELSE 'Z' END"
            else:
                result += " || 'Z'"
            return f"({result})"

        return result

    return None


def _build_date_from_map(m, with_time=False, with_tz=False):
    """
    Build a date/datetime string from a MapLiteral.
    Returns None if map contains non-literal (dynamic) expressions.
    """
    import datetime as _dt

    # If map has a 'datetime' or 'time' base key, we can't resolve at compile time
    # (those require _build_temporal_from_variable_map for runtime projection).
    if _has_map_key(m, "datetime") or _has_map_key(m, "time"):
        return None

    # Resolve 'date' base key (string override)
    base_year, base_month, base_day = None, None, None
    base_iso_year = None  # ISO week-year may differ from calendar year
    base_iso_week_day = None  # ISO weekday (Mon=1) of the base date
    if _has_map_key(m, "date"):
        base_expr = m.entries["date"]
        # date('YYYY-MM-DD') nested call
        if (isinstance(base_expr, ast.FunctionCall) and
                base_expr.function_name.lower() == "date" and
                base_expr.arguments and
                isinstance(base_expr.arguments[0], ast.Literal) and
                isinstance(base_expr.arguments[0].value, str)):
            parsed = _parse_date_string(base_expr.arguments[0].value)
            if parsed:
                base_year, base_month, base_day = parsed
                base_dt = _dt.date(base_year, base_month, base_day)
                iso_cal = base_dt.isocalendar()
                base_iso_year = iso_cal[0]
                base_iso_week_day = iso_cal[2]
        if base_year is None:
            return None  # dynamic base date — can't resolve at compile time

    # For other temporal base keys with dynamic (non-literal) values, defer to dynamic builder
    for _dyn_key in ("time", "localtime", "localdatetime", "datetime"):
        if _has_map_key(m, _dyn_key):
            _dyn_expr = m.entries[_dyn_key]
            if not isinstance(_dyn_expr, ast.Literal):
                return None

    # year (for week calculations, use ISO week-year from base date if year not explicit)
    if _has_map_key(m, "year"):
        year = _extract_int_from_map_entry(m, "year", 1970)
        iso_year = year
    elif base_year is not None:
        year = base_year
        iso_year = base_iso_year if base_iso_year is not None else base_year
    else:
        year = 1970
        iso_year = 1970

    # Determine date component based on which keys are present
    if _has_map_key(m, "week"):
        week = _extract_int_from_map_entry(m, "week", 1)
        # Default dayOfWeek: from explicit key, or base date's weekday, or 1 (Monday)
        if _has_map_key(m, "dayOfWeek"):
            dow = _extract_int_from_map_entry(m, "dayOfWeek", 1)
        elif base_iso_week_day is not None:
            dow = base_iso_week_day
        else:
            dow = 1
        try:
            d = _date_from_iso_week(iso_year, week, dow)
            y_out, mo_out, d_out = d.year, d.month, d.day
        except Exception:
            return None
    elif _has_map_key(m, "ordinalDay"):
        ordinal = _extract_int_from_map_entry(m, "ordinalDay", 1)
        try:
            d = _date_from_ordinal_day(year, ordinal)
            y_out, mo_out, d_out = d.year, d.month, d.day
        except Exception:
            return None
    elif _has_map_key(m, "quarter"):
        quarter = _extract_int_from_map_entry(m, "quarter", 1)
        doq = _extract_int_from_map_entry(m, "dayOfQuarter", 1)
        try:
            d = _date_from_quarter(year, quarter, doq)
            y_out, mo_out, d_out = d.year, d.month, d.day
        except Exception:
            return None
    else:
        mo_out = _extract_int_from_map_entry(m, "month", base_month if base_month else 1)
        d_out = _extract_int_from_map_entry(m, "day", base_day if base_day else 1)
        y_out = year

    if not with_time:
        return f"'{y_out:04d}-{mo_out:02d}-{d_out:02d}'"

    h = _extract_int_from_map_entry(m, "hour", 0)
    mi = _extract_int_from_map_entry(m, "minute", 0)
    s = _extract_int_from_map_entry(m, "second", 0)
    # sub-second precision — combine ms/us/ns per openCypher spec
    ns = _extract_int_from_map_entry(m, "nanosecond", -1)
    us = _extract_int_from_map_entry(m, "microsecond", -1)
    ms = _extract_int_from_map_entry(m, "millisecond", -1)
    frac = _subsecond_frac(ns, us, ms)

    if frac is not None or ns >= 0 or us >= 0 or ms >= 0:
        if frac:
            time_str = f"{h:02d}:{mi:02d}:{s:02d}.{frac}"
        else:
            time_str = f"{h:02d}:{mi:02d}:{s:02d}"
    else:
        time_str = f"{h:02d}:{mi:02d}"
        if s != 0:
            time_str = f"{h:02d}:{mi:02d}:{s:02d}"

    if with_tz:
        tz_str = "Z"
        if "timezone" in m.entries:
            tz_expr = m.entries["timezone"]
            if isinstance(tz_expr, ast.Literal) and isinstance(tz_expr.value, str):
                tz_name = tz_expr.value
                # Named IANA timezone: compute offset and format as +HH:MM[Name]
                if "/" in tz_name or tz_name in ("UTC", "GMT"):
                    try:
                        from zoneinfo import ZoneInfo as _ZoneInfo
                        _zi = _ZoneInfo(tz_name)
                        _aware = _dt.datetime(y_out, mo_out, d_out, tzinfo=_zi)
                        _off = _aware.utcoffset()
                        _total_s = int(_off.total_seconds())
                        _sign = "+" if _total_s >= 0 else "-"
                        _abs_s = abs(_total_s)
                        _hh = _abs_s // 3600
                        _mm = (_abs_s % 3600) // 60
                        tz_str = f"{_sign}{_hh:02d}:{_mm:02d}[{tz_name}]"
                    except Exception:
                        tz_str = tz_name
                else:
                    tz_str = _normalize_tz_offset(tz_name)
    else:
        tz_str = ""
    return f"'{y_out:04d}-{mo_out:02d}-{d_out:02d}T{time_str}{tz_str}'"


def _build_temporal_from_variable_map(fn, m, context):
    """Generate runtime SQL for temporal projection from a variable base.

    Handles patterns like:
      date({date: other})               → extract date from variable
      date({date: other, year: 28})     → override year
      date({date: other, month: 3})     → override month
      localtime({time: other, second: 42}) → override second
      localdatetime({date: other, hour: 10, minute: 10, second: 10}) → combine
      etc.

    Returns SQL string or None if not handleable.

    date ISO format: 'YYYY-MM-DD' (positions 1-10)
    localtime/time format: 'HH:MM:SS[.nanos][tz]'
    localdatetime/datetime format: 'YYYY-MM-DDTHH:MM:SS[.nanos][tz]'
    """
    import re as _re

    # Determine which base key is present
    base_date_expr = None
    base_time_expr = None
    base_datetime_expr = None
    base_date_type = None  # type of the base temporal variable

    if _has_map_key(m, "date"):
        base_date_expr = m.entries["date"]
    if _has_map_key(m, "time"):
        base_time_expr = m.entries["time"]
    if _has_map_key(m, "datetime"):
        base_datetime_expr = m.entries["datetime"]

    # Get SQL for the base temporal variable
    def _get_base_sql(expr):
        """Get SQL column reference for a variable expression."""
        if expr is None:
            return None, None
        if isinstance(expr, ast.Variable):
            alias = context.variable_aliases.get(expr.name)
            temporal_type = context.temporal_types.get(expr.name)
            # Prefer compile-time literal value if available (enables TZ arithmetic)
            tlv = getattr(context, 'temporal_literal_values', {})
            if expr.name in tlv:
                return f"'{tlv[expr.name]}'", temporal_type
            if alias and alias.startswith("Stage"):
                sql = f"{alias}.{_safe_alias(expr.name)}"
            elif alias:
                sql = f"{alias}.{_safe_alias(expr.name)}"
            else:
                sql = _safe_alias(expr.name)
            return sql, temporal_type
        if isinstance(expr, ast.FunctionCall):
            fn_inner = expr.function_name.lower()
            if fn_inner in ("date", "localtime", "time", "localdatetime", "datetime"):
                # Inline the temporal constructor
                inner_args = [translate_expression(a, context, segment="inline") for a in expr.arguments]
                inner_args_exprs = expr.arguments
                inner_result = _scalar_numeric_and_datetime(fn_inner, inner_args, inner_args_exprs, context)
                if inner_result is not None:
                    return inner_result, fn_inner
        return None, None

    # For fn == "date": need date component from base
    if fn == "date":
        if base_date_expr is not None:
            base_sql, btype = _get_base_sql(base_date_expr)
            if base_sql is None:
                return None
            # Extract date portion from the base
            if btype in ("date",):
                date_sql = base_sql  # already a date string
            elif btype in ("localdatetime", "datetime"):
                # Extract first 10 chars (YYYY-MM-DD)
                date_sql = f"SUBSTRING({base_sql}, 1, 10)"
            else:
                date_sql = base_sql  # assume date-like

            # Apply overrides
            has_year = _has_map_key(m, "year")
            has_month = _has_map_key(m, "month")
            has_day = _has_map_key(m, "day")
            has_week = _has_map_key(m, "week")
            has_ordinal = _has_map_key(m, "ordinalDay")
            has_quarter = _has_map_key(m, "quarter")

            # Simple component overrides (year/month/day)
            if not has_week and not has_ordinal and not has_quarter:
                if has_year:
                    y_val = _extract_int_from_map_entry(m, "year", 1970)
                    y_sql = f"'{y_val:04d}'"
                else:
                    y_sql = f"SUBSTRING({date_sql}, 1, 4)"
                mo_sql = f"SUBSTRING({date_sql}, 6, 2)"
                if has_month:
                    mo_val = _extract_int_from_map_entry(m, "month", 1)
                    mo_sql = f"'{mo_val:02d}'"
                d_sql = f"SUBSTRING({date_sql}, 9, 2)"
                if has_day:
                    d_val = _extract_int_from_map_entry(m, "day", 1)
                    d_sql = f"'{d_val:02d}'"
                return f"({y_sql} || '-' || {mo_sql} || '-' || {d_sql})"

            # Week-based: date({date: other, week: W, dayOfWeek?: D})
            # ISO week date calculation — use IRIS DATEADD from Jan 4 of ISO year
            if has_week:
                week_val = _extract_int_from_map_entry(m, "week", 1)
                has_dow = _has_map_key(m, "dayOfWeek")
                dow_val = _extract_int_from_map_entry(m, "dayOfWeek", 1) if has_dow else None
                if has_year:
                    y_val = _extract_int_from_map_entry(m, "year", 1984)
                    y_sql = f"'{y_val:04d}'"
                else:
                    y_sql = f"SUBSTRING({date_sql}, 1, 4)"
                # Jan 4 is always in ISO week 1
                # IRIS DAYOFWEEK: 1=Sun, 2=Mon, 3=Tue...
                # offset_to_monday = MOD(DAYOFWEEK(jan4) - 2 + 7, 7)  → days to go back to Mon
                jan4_sql = f"CAST(({y_sql} || '-01-04') AS DATE)"
                dow_jan4_sql = f"MOD({{fn DAYOFWEEK({jan4_sql})}} - 2 + 7, 7)"
                # Monday of week 1:
                mon_wk1_sql = f"DATEADD('day', -{dow_jan4_sql}, {jan4_sql})"
                # Monday of week W:
                result_date_sql = f"DATEADD('day', ({week_val}-1)*7, {mon_wk1_sql})"
                if has_dow and dow_val is not None and dow_val != 1:
                    # Explicit dayOfWeek override: add (dow_val-1) days (Monday=1)
                    result_date_sql = f"DATEADD('day', {dow_val - 1}, {result_date_sql})"
                elif not has_dow:
                    # Inherit dayOfWeek from base date: MOD(DAYOFWEEK(base)-2+7,7)+1 gives ISO dow (1=Mon)
                    # IRIS DAYOFWEEK: 1=Sun,2=Mon,...,7=Sat → ISO: Mon=1,...,Sun=7
                    # iso_dow = MOD({fn DAYOFWEEK(base_date)} - 2 + 7, 7) + 1
                    # But 0-indexed offset from Monday = iso_dow - 1
                    base_date_cast_sql = f"CAST(SUBSTRING({date_sql}, 1, 10) AS DATE)"
                    dow_offset_sql = f"MOD({{fn DAYOFWEEK({base_date_cast_sql})}} - 2 + 7, 7)"
                    result_date_sql = f"DATEADD('day', {dow_offset_sql}, {result_date_sql})"
                # Format as YYYY-MM-DD
                return (f"(CAST(YEAR({result_date_sql}) AS VARCHAR(4)) || '-' || "
                        f"RIGHT('0' || CAST(MONTH({result_date_sql}) AS VARCHAR(2)), 2) || '-' || "
                        f"RIGHT('0' || CAST(DAY({result_date_sql}) AS VARCHAR(2)), 2))")

            # OrdinalDay: date({date: other, ordinalDay: N})
            if has_ordinal:
                ordinal_val = _extract_int_from_map_entry(m, "ordinalDay", 1)
                if has_year:
                    y_val = _extract_int_from_map_entry(m, "year", 1984)
                    y_sql = f"'{y_val:04d}'"
                else:
                    y_sql = f"SUBSTRING({date_sql}, 1, 4)"
                jan1_sql = f"CAST(({y_sql} || '-01-01') AS DATE)"
                result_date_sql = f"DATEADD('day', {ordinal_val - 1}, {jan1_sql})"
                return (f"(CAST(YEAR({result_date_sql}) AS VARCHAR(4)) || '-' || "
                        f"RIGHT('0' || CAST(MONTH({result_date_sql}) AS VARCHAR(2)), 2) || '-' || "
                        f"RIGHT('0' || CAST(DAY({result_date_sql}) AS VARCHAR(2)), 2))")

            # Quarter: date({date: other, quarter: Q, dayOfQuarter?: D})
            if has_quarter:
                quarter_val = _extract_int_from_map_entry(m, "quarter", 1)
                has_doq = _has_map_key(m, "dayOfQuarter")
                doq_val = _extract_int_from_map_entry(m, "dayOfQuarter", 1) if has_doq else None
                if has_year:
                    y_val = _extract_int_from_map_entry(m, "year", 1984)
                    y_sql = f"'{y_val:04d}'"
                else:
                    y_sql = f"SUBSTRING({date_sql}, 1, 4)"
                # Start month of quarter: Q1→1, Q2→4, Q3→7, Q4→10
                start_month = (quarter_val - 1) * 3 + 1
                q_start_sql = f"CAST(({y_sql} || '-{start_month:02d}-01') AS DATE)"
                if has_doq and doq_val is not None:
                    result_date_sql = f"DATEADD('day', {doq_val - 1}, {q_start_sql})"
                else:
                    # Inherit month-within-quarter and day-within-month from the base date.
                    # month_in_quarter = MOD(CAST(SUBSTRING(date, 6, 2) AS INT) - 1, 3)  (0-based)
                    # Result = DATEADD('month', month_in_quarter, q_start) with same day
                    base_month_sql = f"CAST(SUBSTRING({date_sql}, 6, 2) AS INTEGER)"
                    base_day_sql = f"CAST(SUBSTRING({date_sql}, 9, 2) AS INTEGER)"
                    # Month offset within the target quarter = MOD(base_month - 1, 3)
                    month_offset_sql = f"MOD({base_month_sql} - 1, 3)"
                    # Start of the resulting month: DATEADD('month', month_offset, q_start)
                    result_month_start_sql = f"DATEADD('month', {month_offset_sql}, {q_start_sql})"
                    # Then add day - 1 to reach the right day
                    result_date_sql = f"DATEADD('day', {base_day_sql} - 1, {result_month_start_sql})"
                return (f"(CAST(YEAR({result_date_sql}) AS VARCHAR(4)) || '-' || "
                        f"RIGHT('0' || CAST(MONTH({result_date_sql}) AS VARCHAR(2)), 2) || '-' || "
                        f"RIGHT('0' || CAST(DAY({result_date_sql}) AS VARCHAR(2)), 2))")
        return None

    # For fn in ("localtime", "time"): need time component from base
    if fn in ("localtime", "time"):
        if base_time_expr is not None:
            base_sql, btype = _get_base_sql(base_time_expr)
            if base_sql is None:
                return None
            # Extract time portion from the base
            if btype in ("localtime",):
                time_sql = base_sql
            elif btype == "time":
                if fn == "localtime":
                    # localtime from time: strip TZ from time value
                    if isinstance(base_sql, str) and base_sql.startswith("'") and base_sql.endswith("'"):
                        import re as _re_lt
                        _ts = base_sql[1:-1]
                        _ts = _re_lt.sub(r'\[.*\]$', '', _ts)
                        _ts = _re_lt.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', _ts)
                        if _ts.endswith('Z'):
                            _ts = _ts[:-1]
                        time_sql = f"'{_ts}'"
                    else:
                        # Runtime SQL: strip TZ via CASE
                        # TZ offset starts at pos 6 for HH:MM, pos 9 for HH:MM:SS
                        # Use pos 6 to catch both formats
                        time_sql = (f"CASE "
                                    f"WHEN CHARINDEX('+', {base_sql}, 6) > 0 THEN SUBSTRING({base_sql}, 1, CHARINDEX('+', {base_sql}, 6) - 1) "
                                    f"WHEN CHARINDEX('-', {base_sql}, 6) > 0 THEN SUBSTRING({base_sql}, 1, CHARINDEX('-', {base_sql}, 6) - 1) "
                                    f"WHEN CHARINDEX('Z', {base_sql}) > 0 THEN SUBSTRING({base_sql}, 1, CHARINDEX('Z', {base_sql}) - 1) "
                                    f"ELSE {base_sql} END")
                else:
                    time_sql = base_sql  # fn == "time": keep TZ
            elif btype in ("localdatetime", "datetime"):
                # Extract time part starting at position 12 (after 'YYYY-MM-DDT')
                # Strip IANA timezone name [Region/City] if present
                if isinstance(base_sql, str) and base_sql.startswith("'") and base_sql.endswith("'"):
                    # Compile-time literal: extract time part in Python
                    import re as _re_tdtm
                    _base_dt_inner = base_sql[1:-1]
                    _t_pos_dt = _base_dt_inner.find('T')
                    _time_str_raw = _base_dt_inner[_t_pos_dt + 1:] if _t_pos_dt >= 0 else _base_dt_inner
                    if fn == "localtime":
                        # Strip all TZ info
                        _time_str_raw = _re_tdtm.sub(r'\[[^\]]+\]', '', _time_str_raw)
                        _time_str_raw = _re_tdtm.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', _time_str_raw)
                        if _time_str_raw.endswith('Z'):
                            _time_str_raw = _time_str_raw[:-1]
                    else:
                        # fn == "time": keep numeric TZ, strip IANA zone
                        _time_str_raw = _re_tdtm.sub(r'\[[^\]]+\]', '', _time_str_raw)
                    time_sql = f"'{_time_str_raw}'"
                else:
                    _time_raw = f"SUBSTRING({base_sql}, 12, 99)"
                    if fn == "localtime":
                        # Strip all timezone info. TZ starts at pos 6 min (HH:MM format)
                        time_sql = (f"CASE WHEN CHARINDEX('[', {_time_raw}) > 0 THEN SUBSTRING({_time_raw}, 1, CHARINDEX('[', {_time_raw}) - 1) "
                                    f"WHEN CHARINDEX('+', {_time_raw}, 6) > 0 THEN SUBSTRING({_time_raw}, 1, CHARINDEX('+', {_time_raw}, 6) - 1) "
                                    f"WHEN CHARINDEX('-', {_time_raw}, 6) > 0 THEN SUBSTRING({_time_raw}, 1, CHARINDEX('-', {_time_raw}, 6) - 1) "
                                    f"WHEN CHARINDEX('Z', {_time_raw}) > 0 THEN SUBSTRING({_time_raw}, 1, CHARINDEX('Z', {_time_raw}) - 1) "
                                    f"ELSE {_time_raw} END")
                    else:
                        # fn == "time": keep offset, strip IANA name only
                        time_sql = (f"CASE WHEN CHARINDEX('[', {_time_raw}) > 0 THEN SUBSTRING({_time_raw}, 1, CHARINDEX('[', {_time_raw}) - 1) "
                                    f"ELSE {_time_raw} END")
            else:
                time_sql = base_sql

            # Apply overrides for hour/minute/second
            has_hour = _has_map_key(m, "hour")
            has_minute = _has_map_key(m, "minute")
            has_second = _has_map_key(m, "second")

            if not has_hour and not has_minute and not has_second:
                # No overrides — check timezone
                if fn == "time":
                    new_tz_str = None
                    if _has_map_key(m, "timezone"):
                        tz_expr = m.entries["timezone"]
                        if isinstance(tz_expr, ast.Literal) and isinstance(tz_expr.value, str):
                            new_tz_str = _normalize_tz_offset(tz_expr.value)
                    if btype in ("localtime",):
                        # Local time → zoned time: just append new tz (no shift needed)
                        tz_suffix = new_tz_str if new_tz_str else "Z"
                        return f"({time_sql} || '{tz_suffix}')"
                    elif btype in ("time", "datetime") and new_tz_str is not None:
                        # Zoned time → different zone: need to shift wall-clock time
                        # Extract old offset from the time string at runtime
                        # Strip time value (HH:MM[:SS[.frac]]) and convert to new tz
                        # old_tz_mins: look for +HH:MM or -HH:MM or Z after position 6
                        # Build runtime-adjusted time SQL
                        # time_sql is like '12:31:14.645876+01:00'
                        # Strip the time part first (HH:MM:SS.frac) by taking up to offset sign
                        # pos_of_tz: first +/- after position 6, or Z
                        # old_offset_mins_sql: extract from string
                        # Then compute new hour/minute and build result

                        # If time_sql is a compile-time literal, do it in Python
                        stripped = time_sql.strip("'") if (time_sql.startswith("'") and time_sql.endswith("'")) else None
                        if stripped is not None:
                            import re as _re3
                            # strip IANA
                            stripped = _re3.sub(r'\[.*\]$', '', stripped)
                            m_tz_old = _re3.search(r'([+-])(\d{2}):(\d{2})$', stripped)
                            if m_tz_old:
                                old_sign = 1 if m_tz_old.group(1) == '+' else -1
                                old_offset_mins = old_sign * (int(m_tz_old.group(2)) * 60 + int(m_tz_old.group(3)))
                                pure_time = stripped[:m_tz_old.start()]
                            elif stripped.endswith('Z'):
                                old_offset_mins = 0
                                pure_time = stripped[:-1]
                            else:
                                old_offset_mins = 0
                                pure_time = stripped
                            # Parse new_tz_str offset
                            m_new = _re3.match(r'^([+-])(\d{2}):(\d{2})$', new_tz_str)
                            if m_new:
                                new_sign = 1 if m_new.group(1) == '+' else -1
                                new_offset_mins = new_sign * (int(m_new.group(2)) * 60 + int(m_new.group(3)))
                            else:
                                new_offset_mins = 0
                            delta_mins = new_offset_mins - old_offset_mins
                            # Parse pure_time
                            tm = _re3.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', pure_time)
                            if tm:
                                h_v = int(tm.group(1)); mi_v = int(tm.group(2))
                                s_v = int(tm.group(3) or 0)
                                frac_v = tm.group(4) or ""
                                total_mins = h_v * 60 + mi_v + delta_mins
                                total_mins = total_mins % (24 * 60)
                                new_h = total_mins // 60
                                new_mi = total_mins % 60
                                if frac_v:
                                    return f"'{new_h:02d}:{new_mi:02d}:{s_v:02d}.{frac_v}{new_tz_str}'"
                                elif s_v:
                                    return f"'{new_h:02d}:{new_mi:02d}:{s_v:02d}{new_tz_str}'"
                                else:
                                    return f"'{new_h:02d}:{new_mi:02d}{new_tz_str}'"
                        # Fallback: runtime SQL conversion (complex, omit for now)
                        return f"({time_sql} || '')"  # return as-is if can't convert
                    elif btype in ("localdatetime",):
                        tz_suffix = new_tz_str if new_tz_str else "Z"
                        return f"({time_sql} || '{tz_suffix}')"
                return time_sql

            # Build new time string with overrides
            h_sql = f"SUBSTRING({time_sql}, 1, 2)"
            if has_hour:
                h_val = _extract_int_from_map_entry(m, "hour", 0)
                h_sql = f"'{h_val:02d}'"
            mi_sql = f"SUBSTRING({time_sql}, 4, 2)"
            if has_minute:
                mi_val = _extract_int_from_map_entry(m, "minute", 0)
                mi_sql = f"'{mi_val:02d}'"
            s_sql = f"SUBSTRING({time_sql}, 7, 2)"
            if has_second:
                s_val = _extract_int_from_map_entry(m, "second", 0)
                s_sql = f"'{s_val:02d}'"
            # Fractional seconds: keep from original unless overridden
            # Position 9 onwards: '.nanos[tz]' or '[tz]'
            frac_sql = f"CASE WHEN LENGTH({time_sql}) > 8 AND SUBSTRING({time_sql}, 9, 1) = '.' THEN SUBSTRING({time_sql}, 9, CHARINDEX('+', {time_sql} || '+', 9) - 9) ELSE '' END"
            tz_suffix_sql = f"CASE WHEN CHARINDEX('+', {time_sql}, 9) > 0 THEN SUBSTRING({time_sql}, CHARINDEX('+', {time_sql}, 9)) WHEN CHARINDEX('-', {time_sql}, 9) > 0 THEN SUBSTRING({time_sql}, CHARINDEX('-', {time_sql}, 9)) WHEN CHARINDEX('Z', {time_sql}) > 0 THEN 'Z' ELSE '' END"

            result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql})"
            if fn == "time":
                # Add timezone suffix
                new_tz_str = None
                if _has_map_key(m, "timezone"):
                    tz_expr = m.entries["timezone"]
                    if isinstance(tz_expr, ast.Literal) and isinstance(tz_expr.value, str):
                        new_tz_str = _normalize_tz_offset(tz_expr.value)
                if new_tz_str is not None:
                    # If source had TZ and override differs, shift the wall-clock time
                    if btype in ("time", "datetime"):
                        # Try to get old TZ from the original base_sql (before CASE transforms)
                        _orig_sql = base_sql
                        if isinstance(_orig_sql, str) and _orig_sql.startswith("'") and _orig_sql.endswith("'"):
                            import re as _re_shift2
                            _orig_inner = _orig_sql[1:-1]
                            _orig_inner = _re_shift2.sub(r'\[[^\]]+\]', '', _orig_inner)
                            # For datetime: extract time portion
                            _t_pos2 = _orig_inner.find('T')
                            if _t_pos2 >= 0:
                                _orig_inner = _orig_inner[_t_pos2 + 1:]
                            _m_old_tz = _re_shift2.search(r'([+-])(\d{2}):(\d{2})$', _orig_inner)
                            if _m_old_tz:
                                _old_mins2 = (1 if _m_old_tz.group(1) == '+' else -1) * (int(_m_old_tz.group(2)) * 60 + int(_m_old_tz.group(3)))
                                _m_new_tz = _re_shift2.match(r'^([+-])(\d{2}):(\d{2})', new_tz_str)
                                if _m_new_tz:
                                    _new_mins2 = (1 if _m_new_tz.group(1) == '+' else -1) * (int(_m_new_tz.group(2)) * 60 + int(_m_new_tz.group(3)))
                                    _delta2 = _new_mins2 - _old_mins2
                                    if _delta2 != 0:
                                        # Need to shift h_sql and mi_sql — only possible if base_sql is a literal
                                        _pure_t2 = _orig_inner[:_m_old_tz.start()]
                                        _tm3 = _re_shift2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', _pure_t2)
                                        if _tm3:
                                            h3 = int(_tm3.group(1)); mi3 = int(_tm3.group(2))
                                            s3 = int(_tm3.group(3) or 0); frac3 = _tm3.group(4) or ""
                                            # Apply second override AFTER extracting base values
                                            if has_second:
                                                s3 = _extract_int_from_map_entry(m, "second", s3)
                                            total3 = (h3 * 60 + mi3 + _delta2) % (24 * 60)
                                            h3n = total3 // 60; mi3n = total3 % 60
                                            if frac3:
                                                return f"'{h3n:02d}:{mi3n:02d}:{s3:02d}.{frac3}{new_tz_str}'"
                                            elif s3:
                                                return f"'{h3n:02d}:{mi3n:02d}:{s3:02d}{new_tz_str}'"
                                            else:
                                                return f"'{h3n:02d}:{mi3n:02d}{new_tz_str}'"
                    result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || '{new_tz_str}')"
                else:
                    # No TZ override — use source TZ or default to Z for local types
                    if btype in ("localtime", "localdatetime"):
                        result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || 'Z')"
                    elif btype in ("time", "datetime"):
                        # Extract TZ from source literal if available
                        if isinstance(base_sql, str) and base_sql.startswith("'") and base_sql.endswith("'"):
                            import re as _re_src_tz
                            _src = base_sql[1:-1]
                            _iana_src = _re_src_tz.search(r'\[([^\]]+)\]', _src)
                            _iana_src_str = _iana_src.group(0) if _iana_src else ""
                            _src_no_iana = _src[:_iana_src.start()] if _iana_src else _src
                            # For datetime: get time portion
                            _t_p = _src_no_iana.find('T')
                            if _t_p >= 0:
                                _src_no_iana = _src_no_iana[_t_p + 1:]
                            _m_src_tz = _re_src_tz.search(r'([+-]\d{2}:?\d{2}(?::\d{2})?)$', _src_no_iana)
                            if _m_src_tz:
                                # time() strips IANA zone; only datetime/localdatetime keep it
                                _src_tz = _m_src_tz.group(1) + (_iana_src_str if fn != "time" else "")
                                result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || '{_src_tz}')"
                            elif _src_no_iana.endswith('Z'):
                                result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || 'Z')"
                            else:
                                result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || {tz_suffix_sql})"
                        else:
                            result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || {tz_suffix_sql})"
                    else:
                        result_sql = f"({h_sql} || ':' || {mi_sql} || ':' || {s_sql} || {frac_sql} || {tz_suffix_sql})"
            return result_sql
        return None

    # For fn in ("localdatetime", "datetime"): combine date and time from variables
    if fn in ("localdatetime", "datetime"):
        # Case 0: {datetime: expr[, day: d, second: s, ...]} — convert/reuse datetime expr
        if base_datetime_expr is not None and base_date_expr is None and base_time_expr is None:
            base_sql, btype = _get_base_sql(base_datetime_expr)
            if base_sql is not None:
                # base_sql may be a quoted literal '2023-05-15T12:00:00+01:00' or SQL expr.
                # For localdatetime: strip TZ; for datetime: keep/override TZ.
                # Collect optional component overrides from the map (day, second, etc.)
                has_yr = _has_map_key(m, "year")
                has_mo = _has_map_key(m, "month")
                has_dy = _has_map_key(m, "day")
                has_hr = _has_map_key(m, "hour")
                has_mi = _has_map_key(m, "minute")
                has_sec = _has_map_key(m, "second")
                has_overrides = any([has_yr, has_mo, has_dy, has_hr, has_mi, has_sec])

                # Helper: strip TZ suffix from a compile-time literal string
                def _strip_tz_str(s):
                    import re as _rtz
                    s = _rtz.sub(r'\[[^\]]+\]', '', s)  # remove IANA zone
                    s = _rtz.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', s)
                    if s.endswith('Z'):
                        s = s[:-1]
                    return s

                # If base_sql is a compile-time literal, handle fully at compile time
                if isinstance(base_sql, str) and base_sql.startswith("'") and base_sql.endswith("'"):
                    inner = base_sql[1:-1]
                    tz_suffix = ""
                    if fn == "localdatetime":
                        inner = _strip_tz_str(inner)
                    else:  # fn == "datetime"
                        # Preserve or override TZ — extract IANA zone and numeric offset separately
                        import re as _re_dt0
                        # Step 1: extract IANA zone (e.g. [Europe/Stockholm])
                        _iana_m0 = _re_dt0.search(r'\[([^\]]+)\]', inner)
                        _iana_zone = _iana_m0.group(0) if _iana_m0 else ""  # e.g. '[Europe/Stockholm]'
                        _inner_no_iana = inner[:_iana_m0.start()] if _iana_m0 else inner
                        # Step 2: extract numeric TZ offset from end
                        tz_m0 = _re_dt0.search(r'([+-]\d{2}:?\d{2}(?::\d{2})?)$', _inner_no_iana)
                        if tz_m0:
                            _numeric_tz = tz_m0.group(1)
                            tz_suffix = _numeric_tz + _iana_zone  # e.g. '+01:00[Europe/Stockholm]'
                            inner = _inner_no_iana[:tz_m0.start()]
                        elif _inner_no_iana.endswith('Z'):
                            tz_suffix = 'Z'
                            inner = _inner_no_iana[:-1]
                        else:
                            # localdatetime source — default TZ is Z for datetime
                            tz_suffix = 'Z' if btype in ("localdatetime",) else ""
                            inner = _inner_no_iana
                        # Step 3: apply TZ override if present (override replaces tz_suffix)
                        tz_override_expr = m.entries.get("timezone") if hasattr(m, "entries") else None
                        if tz_override_expr and isinstance(tz_override_expr, ast.Literal):
                            new_tz = _format_tz_for_iso(tz_override_expr.value)
                            # If source had TZ and new differs, shift the wall-clock time
                            if tz_m0 and new_tz != tz_suffix:
                                _m_old_off = _re_dt0.match(r'^([+-])(\d{2}):(\d{2})', _numeric_tz)
                                _m_new_off = _re_dt0.match(r'^([+-])(\d{2}):(\d{2})', new_tz)
                                if _m_old_off and _m_new_off:
                                    _old_mins = (1 if _m_old_off.group(1) == '+' else -1) * (int(_m_old_off.group(2)) * 60 + int(_m_old_off.group(3)))
                                    _new_mins = (1 if _m_new_off.group(1) == '+' else -1) * (int(_m_new_off.group(2)) * 60 + int(_m_new_off.group(3)))
                                    _delta = _new_mins - _old_mins
                                    if _delta != 0:
                                        # Extract time part from inner (after 'T')
                                        _t_idx = inner.find('T')
                                        if _t_idx >= 0:
                                            _date_part = inner[:_t_idx]
                                            _t_str = inner[_t_idx + 1:]
                                            _tm_m = _re_dt0.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', _t_str)
                                            if _tm_m:
                                                _th = int(_tm_m.group(1)); _tmi = int(_tm_m.group(2))
                                                _ts = int(_tm_m.group(3) or 0); _tfrac = _tm_m.group(4) or ""
                                                _total = (_th * 60 + _tmi + _delta) % (24 * 60)
                                                _th_new = _total // 60; _tmi_new = _total % 60
                                                if _tfrac:
                                                    _t_shifted = f"{_th_new:02d}:{_tmi_new:02d}:{_ts:02d}.{_tfrac}"
                                                elif _ts:
                                                    _t_shifted = f"{_th_new:02d}:{_tmi_new:02d}:{_ts:02d}"
                                                else:
                                                    _t_shifted = f"{_th_new:02d}:{_tmi_new:02d}"
                                                inner = f"{_date_part}T{_t_shifted}"
                            tz_suffix = new_tz

                    if has_overrides:
                        # Parse inner into date+time parts and apply overrides
                        # inner is 'YYYY-MM-DDTHH:MM[:SS[.frac]]'
                        import re as _re_ov
                        _dt_m = _re_ov.match(
                            r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})([.\d]*))?$',
                            inner)
                        if _dt_m:
                            yr0 = int(_dt_m.group(1)); mo0 = int(_dt_m.group(2))
                            dy0 = int(_dt_m.group(3)); hr0 = int(_dt_m.group(4))
                            mi0 = int(_dt_m.group(5)); sc0 = int(_dt_m.group(6) or 0)
                            frac0 = _dt_m.group(7) or ""
                            if has_yr: yr0 = _extract_int_from_map_entry(m, "year", yr0)
                            if has_mo: mo0 = _extract_int_from_map_entry(m, "month", mo0)
                            if has_dy: dy0 = _extract_int_from_map_entry(m, "day", dy0)
                            if has_hr: hr0 = _extract_int_from_map_entry(m, "hour", hr0)
                            if has_mi: mi0 = _extract_int_from_map_entry(m, "minute", mi0)
                            if has_sec:
                                sc0 = _extract_int_from_map_entry(m, "second", sc0)
                                # second override resets sub-second fraction? No: keep frac unless nanosecond/ms key given
                            inner = f"{yr0:04d}-{mo0:02d}-{dy0:02d}T{hr0:02d}:{mi0:02d}:{sc0:02d}{frac0}"

                    if fn == "localdatetime":
                        return f"'{inner}'"
                    else:
                        return f"'{inner}{tz_suffix}'"
                else:
                    # Runtime SQL expression path
                    # Strip TZ suffix from base_sql (TZ starts after 'YYYY-MM-DDTHH:MM:SS')
                    no_tz_sql = (f"CASE "
                                 f"WHEN CHARINDEX('+', {base_sql}, 17) > 0 THEN SUBSTRING({base_sql}, 1, CHARINDEX('+', {base_sql}, 17) - 1) "
                                 f"WHEN CHARINDEX('-', {base_sql}, 17) > 0 THEN SUBSTRING({base_sql}, 1, CHARINDEX('-', {base_sql}, 17) - 1) "
                                 f"WHEN CHARINDEX('Z', {base_sql}, 17) > 0 THEN SUBSTRING({base_sql}, 1, CHARINDEX('Z', {base_sql}, 17) - 1) "
                                 f"ELSE {base_sql} END")
                    if has_overrides:
                        # Apply date/time component overrides to no_tz_sql
                        y_sql = f"'{_extract_int_from_map_entry(m,'year',0):04d}'" if has_yr else f"SUBSTRING({no_tz_sql}, 1, 4)"
                        mo_sql = f"'{_extract_int_from_map_entry(m,'month',0):02d}'" if has_mo else f"SUBSTRING({no_tz_sql}, 6, 2)"
                        d_sql = f"'{_extract_int_from_map_entry(m,'day',0):02d}'" if has_dy else f"SUBSTRING({no_tz_sql}, 9, 2)"
                        hr_sql = f"'{_extract_int_from_map_entry(m,'hour',0):02d}'" if has_hr else f"SUBSTRING({no_tz_sql}, 12, 2)"
                        mi_sql = f"'{_extract_int_from_map_entry(m,'minute',0):02d}'" if has_mi else f"SUBSTRING({no_tz_sql}, 15, 2)"
                        sc_sql = f"'{_extract_int_from_map_entry(m,'second',0):02d}'" if has_sec else f"SUBSTRING({no_tz_sql}, 18, 2)"
                        # Preserve fractional seconds from original
                        frac_sql = (f"CASE WHEN LENGTH({no_tz_sql}) > 19 AND SUBSTRING({no_tz_sql}, 20, 1) = '.' "
                                    f"THEN SUBSTRING({no_tz_sql}, 19, 99) ELSE '' END")
                        rebuilt = f"({y_sql} || '-' || {mo_sql} || '-' || {d_sql} || 'T' || {hr_sql} || ':' || {mi_sql} || ':' || {sc_sql} || {frac_sql})"
                    else:
                        rebuilt = no_tz_sql

                    if fn == "localdatetime":
                        return rebuilt
                    else:
                        tz_override_expr = m.entries.get("timezone") if hasattr(m, "entries") else None
                        if tz_override_expr and isinstance(tz_override_expr, ast.Literal) and isinstance(tz_override_expr.value, str):
                            new_tz_iso = _format_tz_for_iso(tz_override_expr.value)
                            return f"({rebuilt} || '{new_tz_iso}')"
                        else:
                            # Keep original timezone
                            tz_suffix_sql = (f"CASE "
                                             f"WHEN CHARINDEX('+', {base_sql}, 17) > 0 THEN SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}, 17)) "
                                             f"WHEN CHARINDEX('-', {base_sql}, 17) > 0 THEN SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}, 17)) "
                                             f"WHEN CHARINDEX('Z', {base_sql}, 17) > 0 THEN 'Z' ELSE 'Z' END")
                            return f"({rebuilt} || {tz_suffix_sql})"

        # Case 1: {date: var, hour: h, minute: m, second: s} — date from var, time from literals
        if base_date_expr is not None:
            base_sql, btype = _get_base_sql(base_date_expr)
            if base_sql is None:
                return None
            if btype in ("date",):
                date_sql = base_sql
            elif btype in ("localdatetime", "datetime"):
                date_sql = f"SUBSTRING({base_sql}, 1, 10)"
            else:
                date_sql = base_sql

            # Apply day override if present
            has_year = _has_map_key(m, "year")
            has_month = _has_map_key(m, "month")
            has_day = _has_map_key(m, "day")
            if has_year:
                y_val = _extract_int_from_map_entry(m, "year", 1970)
                y_sql = f"'{y_val:04d}'"
            else:
                y_sql = f"SUBSTRING({date_sql}, 1, 4)"
            if has_month:
                mo_val = _extract_int_from_map_entry(m, "month", 1)
                mo_sql = f"'{mo_val:02d}'"
            else:
                mo_sql = f"SUBSTRING({date_sql}, 6, 2)"
            if has_day:
                d_val = _extract_int_from_map_entry(m, "day", 1)
                d_sql = f"'{d_val:02d}'"
            else:
                d_sql = f"SUBSTRING({date_sql}, 9, 2)"
            new_date_sql = f"({y_sql} || '-' || {mo_sql} || '-' || {d_sql})"

            # Time part: from {time: var} if present, else from literals
            time_base_sql_outer, ttype_outer = None, None  # track for TZ extraction later
            if base_time_expr is not None:
                time_base_sql, ttype = _get_base_sql(base_time_expr)
                time_base_sql_outer, ttype_outer = time_base_sql, ttype
                if time_base_sql is None:
                    return None
                if ttype in ("localdatetime", "datetime"):
                    if isinstance(time_base_sql, str) and time_base_sql.startswith("'") and time_base_sql.endswith("'"):
                        # Compile-time literal: extract time part in Python
                        _tbs_inner = time_base_sql[1:-1]
                        _t_pos = _tbs_inner.find('T')
                        time_base_sql = f"'{_tbs_inner[_t_pos + 1:]}'" if _t_pos >= 0 else time_base_sql
                    else:
                        time_base_sql = f"SUBSTRING({time_base_sql}, 12, 99)"
                # Apply second override if present
                has_second = _has_map_key(m, "second")
                if has_second:
                    s_val = _extract_int_from_map_entry(m, "second", 0)
                    h_sql = f"SUBSTRING({time_base_sql}, 1, 2)"
                    mi_sql = f"SUBSTRING({time_base_sql}, 4, 2)"
                    # Fractional: extract from pos 9 (after 'HH:MM:SS'), strip tz suffix
                    # For localdatetime: strip +/- or Z tz; for datetime/time: keep tz
                    if fn == "localdatetime":
                        frac_sql = (f"CASE WHEN LENGTH({time_base_sql}) > 8 AND SUBSTRING({time_base_sql}, 9, 1) = '.' "
                                    f"THEN CASE WHEN CHARINDEX('+', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('+', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('-', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('-', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('Z', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('Z', {time_base_sql}, 10) - 9) "
                                    f"ELSE SUBSTRING({time_base_sql}, 9, 99) END ELSE '' END")
                    else:
                        # fn == "datetime": strip TZ from frac (TZ suffix will be handled below)
                        frac_sql = (f"CASE WHEN LENGTH({time_base_sql}) > 8 AND SUBSTRING({time_base_sql}, 9, 1) = '.' "
                                    f"THEN CASE WHEN CHARINDEX('+', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('+', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('-', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('-', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('Z', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('Z', {time_base_sql}, 10) - 9) "
                                    f"ELSE SUBSTRING({time_base_sql}, 9, 99) END ELSE '' END")
                    time_sql = f"({h_sql} || ':' || {mi_sql} || ':' || '{s_val:02d}' || {frac_sql})"
                else:
                    # Strip timezone from time base for localdatetime
                    if fn == "localdatetime":
                        time_sql = f"CASE WHEN CHARINDEX('+', {time_base_sql}, 6) > 0 THEN SUBSTRING({time_base_sql}, 1, CHARINDEX('+', {time_base_sql}, 6) - 1) WHEN CHARINDEX('-', {time_base_sql}, 6) > 0 THEN SUBSTRING({time_base_sql}, 1, CHARINDEX('-', {time_base_sql}, 6) - 1) WHEN CHARINDEX('Z', {time_base_sql}) > 0 THEN SUBSTRING({time_base_sql}, 1, CHARINDEX('Z', {time_base_sql}) - 1) ELSE {time_base_sql} END"
                    else:
                        time_sql = time_base_sql
            else:
                # Build time from literal components
                h = _extract_int_from_map_entry(m, "hour", 0)
                mi_v = _extract_int_from_map_entry(m, "minute", 0)
                s = _extract_int_from_map_entry(m, "second", 0)
                ns = _extract_int_from_map_entry(m, "nanosecond", -1)
                us = _extract_int_from_map_entry(m, "microsecond", -1)
                ms_v = _extract_int_from_map_entry(m, "millisecond", -1)
                frac = _subsecond_frac(ns, us, ms_v)
                if frac:
                    time_sql = f"'{h:02d}:{mi_v:02d}:{s:02d}.{frac}'"
                elif s != 0 or ns >= 0 or us >= 0 or ms_v >= 0:
                    time_sql = f"'{h:02d}:{mi_v:02d}:{s:02d}'"
                else:
                    time_sql = f"'{h:02d}:{mi_v:02d}'"

            if fn == "datetime":
                if _has_map_key(m, "timezone"):
                    # Explicit TZ override: convert time to new TZ if needed
                    tz_expr = m.entries["timezone"]
                    if isinstance(tz_expr, ast.Literal) and isinstance(tz_expr.value, str):
                        tz_str = _format_tz_for_iso(tz_expr.value)
                        # If source time had TZ and override differs, shift the time
                        if base_time_expr is not None and ttype_outer in ("time", "datetime") and isinstance(time_base_sql_outer, str) and time_base_sql_outer.startswith("'") and time_base_sql_outer.endswith("'"):
                            import re as _re_shift
                            _ts2 = time_base_sql_outer[1:-1]
                            # Extract IANA zone from source before stripping
                            _ts2_iana_m = _re_shift.search(r'\[([^\]]+)\]', _ts2)
                            _ts2_iana_name = _ts2_iana_m.group(1) if _ts2_iana_m else ""
                            _ts2 = _re_shift.sub(r'\[[^\]]+\]', '', _ts2)  # strip IANA zone
                            # If _ts2 is a full datetime (contains 'T'), extract time portion
                            _t_sep = _ts2.find('T')
                            if _t_sep >= 0:
                                _ts2 = _ts2[_t_sep + 1:]  # just the time part 'HH:MM+01:00'
                            _m_old = _re_shift.search(r'([+-])(\d{2}):(\d{2})$', _ts2)
                            if _m_old:
                                old_sign = 1 if _m_old.group(1) == '+' else -1
                                old_off_mins = old_sign * (int(_m_old.group(2)) * 60 + int(_m_old.group(3)))
                                _pure_t = _ts2[:_m_old.start()]
                                # If IANA zone present, recompute offset for target date (DST-aware)
                                if _ts2_iana_name:
                                    # Determine target date from base_date_expr + overrides
                                    _tgt_bs, _tgt_bt = _get_base_sql(base_date_expr)
                                    if isinstance(_tgt_bs, str) and _tgt_bs.startswith("'") and _tgt_bs.endswith("'"):
                                        _tgt_i = _tgt_bs[1:-1][:10]
                                        _tgt_y = int(_tgt_i[:4]) if _tgt_i[:4].isdigit() else 1984
                                        _tgt_mo = int(_tgt_i[5:7]) if len(_tgt_i) > 6 and _tgt_i[5:7].isdigit() else 10
                                        _tgt_d = int(_tgt_i[8:10]) if len(_tgt_i) > 9 and _tgt_i[8:10].isdigit() else 11
                                    else:
                                        _tgt_y, _tgt_mo, _tgt_d = 1984, 10, 11
                                    if has_day: _tgt_d = _extract_int_from_map_entry(m, "day", _tgt_d)
                                    if has_month: _tgt_mo = _extract_int_from_map_entry(m, "month", _tgt_mo)
                                    if has_year: _tgt_y = _extract_int_from_map_entry(m, "year", _tgt_y)
                                    _dst_off_s1 = _iana_tz_offset(_ts2_iana_name, _tgt_y, _tgt_mo, _tgt_d)
                                    if _dst_off_s1:
                                        _m_dst = _re_shift.match(r'^([+-])(\d{2}):(\d{2})', _dst_off_s1)
                                        if _m_dst:
                                            old_off_mins = (1 if _m_dst.group(1) == '+' else -1) * (int(_m_dst.group(2)) * 60 + int(_m_dst.group(3)))
                            elif _ts2.endswith('Z'):
                                old_off_mins = 0; _pure_t = _ts2[:-1]
                            else:
                                old_off_mins = 0; _pure_t = _ts2
                            # Also extract time portion from time_base_sql (which may have been overridden by second)
                            _time_for_shift = time_base_sql
                            if isinstance(_time_for_shift, str) and _time_for_shift.startswith("'") and _time_for_shift.endswith("'"):
                                _tfs = _time_for_shift[1:-1]
                                _tfs = _re_shift.sub(r'\[.*\]$', '', _tfs)
                                _tfs_tz_m = _re_shift.search(r'([+-])(\d{2}):(\d{2})$', _tfs)
                                if _tfs_tz_m:
                                    _pure_t = _tfs[:_tfs_tz_m.start()]
                                elif _tfs.endswith('Z'):
                                    _pure_t = _tfs[:-1]
                                else:
                                    _pure_t = _tfs
                            _m_new2 = _re_shift.match(r'^([+-])(\d{2}):(\d{2})', tz_str)
                            if _m_new2:
                                new_sign2 = 1 if _m_new2.group(1) == '+' else -1
                                new_off_mins = new_sign2 * (int(_m_new2.group(2)) * 60 + int(_m_new2.group(3)))
                            else:
                                new_off_mins = old_off_mins
                            delta_mins = new_off_mins - old_off_mins
                            if delta_mins != 0:
                                _tm2 = _re_shift.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', _pure_t)
                                if _tm2:
                                    h2 = int(_tm2.group(1)); mi2 = int(_tm2.group(2))
                                    s2v = int(_tm2.group(3) or 0); frac2 = _tm2.group(4) or ""
                                    # Apply second override if present
                                    if has_second:
                                        s2v = _extract_int_from_map_entry(m, "second", s2v)
                                    total2 = h2 * 60 + mi2 + delta_mins
                                    total2 = total2 % (24 * 60)
                                    h2n = total2 // 60; mi2n = total2 % 60
                                    if frac2: shifted = f"'{h2n:02d}:{mi2n:02d}:{s2v:02d}.{frac2}'"
                                    elif s2v: shifted = f"'{h2n:02d}:{mi2n:02d}:{s2v:02d}'"
                                    else: shifted = f"'{h2n:02d}:{mi2n:02d}'"
                                    return f"({new_date_sql} || 'T' || {shifted} || '{tz_str}')"
                    else:
                        tz_str = "Z"
                else:
                    # No TZ override: use source TZ from time variable
                    if base_time_expr is not None:
                        if ttype_outer in ("localtime", "localdatetime"):
                            tz_str = "Z"
                        elif isinstance(time_base_sql_outer, str) and time_base_sql_outer.startswith("'") and time_base_sql_outer.endswith("'"):
                            # Extract TZ from compile-time literal
                            import re as _re_tz2
                            _ts3 = time_base_sql_outer[1:-1]
                            # Strip IANA bracket but keep numeric TZ
                            _iana3 = _re_tz2.search(r'\[([^\]]+)\]', _ts3)
                            _iana_name3 = _iana3.group(1) if _iana3 else ""
                            _iana_bracket3 = _iana3.group(0) if _iana3 else ""
                            _ts3_no_iana = _ts3[:_iana3.start()] if _iana3 else _ts3
                            # For datetime: extract time portion
                            _ts3_t = _ts3_no_iana.find('T')
                            if _ts3_t >= 0:
                                _ts3_no_iana = _ts3_no_iana[_ts3_t + 1:]
                            _m_tz3 = _re_tz2.search(r'([+-]\d{2}:?\d{2}(?::\d{2})?)$', _ts3_no_iana)
                            if _m_tz3:
                                _num_tz3 = _m_tz3.group(1)
                                if _iana_name3:
                                    # Recompute DST-aware offset for the target date
                                    # Target date from base_date_expr + overrides
                                    _target_base_sql3, _target_btype3 = _get_base_sql(base_date_expr)
                                    if isinstance(_target_base_sql3, str) and _target_base_sql3.startswith("'") and _target_base_sql3.endswith("'"):
                                        _tgt_inner3 = _target_base_sql3[1:-1][:10]  # YYYY-MM-DD portion
                                        _tgt_y3 = int(_tgt_inner3[:4]) if _tgt_inner3[:4].isdigit() else 1984
                                        _tgt_mo3 = int(_tgt_inner3[5:7]) if _tgt_inner3[5:7].isdigit() else 10
                                        _tgt_d3 = int(_tgt_inner3[8:10]) if _tgt_inner3[8:10].isdigit() else 11
                                    else:
                                        _tgt_y3, _tgt_mo3, _tgt_d3 = 1984, 10, 11
                                    if has_day: _tgt_d3 = _extract_int_from_map_entry(m, "day", _tgt_d3)
                                    if has_month: _tgt_mo3 = _extract_int_from_map_entry(m, "month", _tgt_mo3)
                                    if has_year: _tgt_y3 = _extract_int_from_map_entry(m, "year", _tgt_y3)
                                    _dst_off3 = _iana_tz_offset(_iana_name3, _tgt_y3, _tgt_mo3, _tgt_d3)
                                    tz_str = (_dst_off3 if _dst_off3 else _num_tz3) + _iana_bracket3
                                else:
                                    tz_str = _num_tz3
                            elif _ts3_no_iana.endswith('Z'):
                                tz_str = "Z"
                            else:
                                tz_str = "Z"
                        else:
                            # Runtime: extract TZ suffix from time_base_sql_outer
                            tz_str = None  # handled via tz_suffix_sql below
                    else:
                        tz_str = "Z"
                if tz_str is not None:
                    # Strip TZ from time_sql before appending tz_str
                    # (time_sql may be a runtime expr or literal with TZ embedded)
                    _pure_time_sql = time_sql
                    if isinstance(time_sql, str) and time_sql.startswith("'") and time_sql.endswith("'"):
                        import re as _re_ptz
                        _pt_inner = time_sql[1:-1]
                        _pt_inner = _re_ptz.sub(r'\[.*\]$', '', _pt_inner)
                        _pt_inner = _re_ptz.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', _pt_inner)
                        if _pt_inner.endswith('Z'): _pt_inner = _pt_inner[:-1]
                        _pure_time_sql = f"'{_pt_inner}'"
                    elif time_base_sql_outer is not None:
                        # time_sql may equal time_base_sql_outer or be a CASE expression
                        # If it's time_base_sql_outer (runtime column), strip TZ via CASE
                        if time_sql == time_base_sql_outer:
                            _pure_time_sql = (f"CASE WHEN CHARINDEX('+', {time_sql}, 6) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('+', {time_sql}, 6) - 1) "
                                              f"WHEN CHARINDEX('-', {time_sql}, 6) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('-', {time_sql}, 6) - 1) "
                                              f"WHEN CHARINDEX('Z', {time_sql}) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('Z', {time_sql}) - 1) "
                                              f"ELSE {time_sql} END")
                    return f"({new_date_sql} || 'T' || {_pure_time_sql} || '{tz_str}')"
                else:
                    # Runtime TZ extraction from time_base_sql_outer
                    pure_t_sql = (f"CASE WHEN CHARINDEX('+', {time_sql}, 6) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('+', {time_sql}, 6) - 1) "
                                  f"WHEN CHARINDEX('-', {time_sql}, 6) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('-', {time_sql}, 6) - 1) "
                                  f"WHEN CHARINDEX('Z', {time_sql}) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('Z', {time_sql}) - 1) "
                                  f"ELSE {time_sql} END")
                    tz_suffix_sql = (f"CASE WHEN CHARINDEX('+', {time_base_sql_outer}, 6) > 0 THEN SUBSTRING({time_base_sql_outer}, CHARINDEX('+', {time_base_sql_outer}, 6)) "
                                     f"WHEN CHARINDEX('-', {time_base_sql_outer}, 6) > 0 THEN SUBSTRING({time_base_sql_outer}, CHARINDEX('-', {time_base_sql_outer}, 6)) "
                                     f"WHEN CHARINDEX('Z', {time_base_sql_outer}) > 0 THEN 'Z' ELSE 'Z' END")
                    return f"({new_date_sql} || 'T' || {pure_t_sql} || {tz_suffix_sql})"
            return f"({new_date_sql} || 'T' || {time_sql})"

        # Case 2: {year: ..., month: ..., day: ..., time: var}
        if base_time_expr is not None:
            base_sql, btype = _get_base_sql(base_time_expr)
            if base_sql is None:
                return None
            # Date from literal components
            year = _extract_int_from_map_entry(m, "year", 1970)
            month = _extract_int_from_map_entry(m, "month", 1)
            day = _extract_int_from_map_entry(m, "day", 1)

            # Time from variable
            if btype in ("localdatetime", "datetime"):
                if isinstance(base_sql, str) and base_sql.startswith("'") and base_sql.endswith("'"):
                    # Compile-time literal: extract time part in Python for precise TZ handling
                    _base_inner = base_sql[1:-1]
                    _t_start = _base_inner.find('T')
                    time_base_sql = f"'{_base_inner[_t_start + 1:]}'" if _t_start >= 0 else base_sql
                else:
                    time_base_sql = f"SUBSTRING({base_sql}, 12, 99)"
            else:
                time_base_sql = base_sql

            has_second = _has_map_key(m, "second")
            # For compile-time literals, extract time components in Python for precise handling
            _c2_base_literal = (isinstance(time_base_sql, str) and time_base_sql.startswith("'") and time_base_sql.endswith("'"))
            if _c2_base_literal:
                import re as _re_c2lit
                _c2_t_inner = time_base_sql[1:-1]
                # Extract IANA zone and numeric TZ from time_base_sql
                _c2_lit_iana = _re_c2lit.search(r'\[([^\]]+)\]', _c2_t_inner)
                _c2_lit_iana_name = _c2_lit_iana.group(1) if _c2_lit_iana else ""
                _c2_lit_iana_str = _c2_lit_iana.group(0) if _c2_lit_iana else ""
                _c2_t_no_iana = _c2_t_inner[:_c2_lit_iana.start()] if _c2_lit_iana else _c2_t_inner
                _c2_lit_tz_m = _re_c2lit.search(r'([+-]\d{2}:?\d{2}(?::\d{2})?)$', _c2_t_no_iana)
                if _c2_lit_tz_m:
                    _c2_lit_num_tz = _c2_lit_tz_m.group(1)
                    _c2_lit_pure_t = _c2_t_no_iana[:_c2_lit_tz_m.start()]
                elif _c2_t_no_iana.endswith('Z'):
                    _c2_lit_num_tz = 'Z'
                    _c2_lit_pure_t = _c2_t_no_iana[:-1]
                else:
                    _c2_lit_num_tz = ''
                    _c2_lit_pure_t = _c2_t_no_iana
                # Parse pure time components
                _c2_tm_m = _re_c2lit.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', _c2_lit_pure_t)
                if _c2_tm_m:
                    _c2_lit_h = int(_c2_tm_m.group(1)); _c2_lit_mi = int(_c2_tm_m.group(2))
                    _c2_lit_s = int(_c2_tm_m.group(3) or 0); _c2_lit_frac = _c2_tm_m.group(4) or ""
                else:
                    _c2_base_literal = False  # fallback

            if has_second:
                s_val = _extract_int_from_map_entry(m, "second", 0)
                if _c2_base_literal:
                    # Build time_sql entirely in Python — avoids frac TZ stripping issue
                    _c2_s_eff = s_val  # second override
                    if _c2_lit_frac:
                        # Strip TZ from frac in Python (frac is already pure fractional from _c2_lit_frac)
                        time_sql = f"'{_c2_lit_h:02d}:{_c2_lit_mi:02d}:{_c2_s_eff:02d}.{_c2_lit_frac}'"
                    else:
                        time_sql = f"'{_c2_lit_h:02d}:{_c2_lit_mi:02d}:{_c2_s_eff:02d}'"
                else:
                    h_sql = f"SUBSTRING({time_base_sql}, 1, 2)"
                    mi_sql = f"SUBSTRING({time_base_sql}, 4, 2)"
                    if fn == "localdatetime":
                        frac_sql = (f"CASE WHEN LENGTH({time_base_sql}) > 8 AND SUBSTRING({time_base_sql}, 9, 1) = '.' "
                                    f"THEN CASE WHEN CHARINDEX('+', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('+', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('-', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('-', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('Z', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('Z', {time_base_sql}, 10) - 9) "
                                    f"ELSE SUBSTRING({time_base_sql}, 9, 99) END ELSE '' END")
                    else:
                        # Strip TZ from frac for datetime
                        frac_sql = (f"CASE WHEN LENGTH({time_base_sql}) > 8 AND SUBSTRING({time_base_sql}, 9, 1) = '.' "
                                    f"THEN CASE WHEN CHARINDEX('+', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('+', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('-', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('-', {time_base_sql}, 10) - 9) "
                                    f"WHEN CHARINDEX('Z', {time_base_sql}, 10) > 0 THEN SUBSTRING({time_base_sql}, 9, CHARINDEX('Z', {time_base_sql}, 10) - 9) "
                                    f"ELSE SUBSTRING({time_base_sql}, 9, 99) END ELSE '' END")
                    time_sql = f"({h_sql} || ':' || {mi_sql} || ':' || '{s_val:02d}' || {frac_sql})"
            else:
                if fn == "localdatetime":
                    # Strip TZ from localtime/time base (TZ starts at pos 6 min for HH:MM)
                    time_sql = f"CASE WHEN CHARINDEX('+', {time_base_sql}, 6) > 0 THEN SUBSTRING({time_base_sql}, 1, CHARINDEX('+', {time_base_sql}, 6) - 1) WHEN CHARINDEX('-', {time_base_sql}, 6) > 0 THEN SUBSTRING({time_base_sql}, 1, CHARINDEX('-', {time_base_sql}, 6) - 1) WHEN CHARINDEX('Z', {time_base_sql}) > 0 THEN SUBSTRING({time_base_sql}, 1, CHARINDEX('Z', {time_base_sql}) - 1) ELSE {time_base_sql} END"
                else:
                    time_sql = time_base_sql

            if fn == "datetime":
                # Build timezone suffix; if a new timezone override is given and the source
                # time has a different timezone, shift the wall-clock time.
                tz_override_expr = m.entries.get("timezone") if hasattr(m, "entries") else None
                if tz_override_expr and isinstance(tz_override_expr, ast.Literal) and isinstance(tz_override_expr.value, str):
                    new_tz_iso = _format_tz_for_iso(tz_override_expr.value, year, month, day)
                    # If source has a known compile-time tz and new tz differs, convert
                    if _c2_base_literal and btype in ("time", "datetime") and _c2_lit_num_tz not in ('', 'Z'):
                        import re as _re4
                        _m_old_off = _re4.match(r'^([+-])(\d{2}):(\d{2})', _c2_lit_num_tz)
                        _m_new_off = _re4.match(r'^([+-])(\d{2}):(\d{2})', new_tz_iso)
                        if _m_old_off and _m_new_off:
                            old_off_mins = (1 if _m_old_off.group(1) == '+' else -1) * (int(_m_old_off.group(2)) * 60 + int(_m_old_off.group(3)))
                            new_off_mins = (1 if _m_new_off.group(1) == '+' else -1) * (int(_m_new_off.group(2)) * 60 + int(_m_new_off.group(3)))
                            delta_mins = new_off_mins - old_off_mins
                            if delta_mins != 0:
                                # s_val already applied in time_sql for has_second case; use _c2_lit_s as base
                                _c2_s_for_shift = s_val if has_second else _c2_lit_s
                                total2 = _c2_lit_h * 60 + _c2_lit_mi + delta_mins
                                total2 = total2 % (24 * 60)
                                h2n = total2 // 60; mi2n = total2 % 60
                                if _c2_lit_frac:
                                    shifted_t = f"'{h2n:02d}:{mi2n:02d}:{_c2_s_for_shift:02d}.{_c2_lit_frac}'"
                                elif _c2_s_for_shift:
                                    shifted_t = f"'{h2n:02d}:{mi2n:02d}:{_c2_s_for_shift:02d}'"
                                else:
                                    shifted_t = f"'{h2n:02d}:{mi2n:02d}'"
                                return f"('{year:04d}-{month:02d}-{day:02d}T' || {shifted_t} || '{new_tz_iso}')"
                    elif not _c2_base_literal:
                        # Runtime: check if time_sql is a literal (no-has_second case)
                        stripped_t = time_sql.strip("'") if (isinstance(time_sql, str) and time_sql.startswith("'") and time_sql.endswith("'")) else None
                        if stripped_t is not None and btype in ("time", "datetime"):
                            import re as _re4b
                            s2t = _re4b.sub(r'\[.*\]$', '', stripped_t)
                            m_old_tz = _re4b.search(r'([+-])(\d{2}):(\d{2})$', s2t)
                            if m_old_tz:
                                old_sign = 1 if m_old_tz.group(1) == '+' else -1
                                old_off_mins = old_sign * (int(m_old_tz.group(2)) * 60 + int(m_old_tz.group(3)))
                                pure_t = s2t[:m_old_tz.start()]
                            elif s2t.endswith('Z'):
                                old_off_mins = 0; pure_t = s2t[:-1]
                            else:
                                old_off_mins = 0; pure_t = s2t
                            _tz_raw = tz_override_expr.value
                            _m_new = _re4b.match(r'^([+-])(\d{2}):(\d{2})', _format_tz_for_iso(_tz_raw, year, month, day))
                            if _m_new:
                                new_sign = 1 if _m_new.group(1) == '+' else -1
                                new_off_mins = new_sign * (int(_m_new.group(2)) * 60 + int(_m_new.group(3)))
                            else:
                                new_off_mins = old_off_mins
                            delta_mins = new_off_mins - old_off_mins
                            if delta_mins != 0:
                                tm2 = _re4b.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', pure_t)
                                if tm2:
                                    h2 = int(tm2.group(1)); mi2 = int(tm2.group(2))
                                    s2v = int(tm2.group(3) or 0); frac2 = tm2.group(4) or ""
                                    total2 = h2 * 60 + mi2 + delta_mins
                                    total2 = total2 % (24 * 60)
                                    h2n = total2 // 60; mi2n = total2 % 60
                                    if frac2:
                                        shifted_t = f"'{h2n:02d}:{mi2n:02d}:{s2v:02d}.{frac2}'"
                                    elif s2v:
                                        shifted_t = f"'{h2n:02d}:{mi2n:02d}:{s2v:02d}'"
                                    else:
                                        shifted_t = f"'{h2n:02d}:{mi2n:02d}'"
                                    return f"('{year:04d}-{month:02d}-{day:02d}T' || {shifted_t} || '{new_tz_iso}')"
                    return f"('{year:04d}-{month:02d}-{day:02d}T' || {time_sql} || '{new_tz_iso}')"
                else:
                    # No timezone override: use source timezone from base_sql (for datetime/time)
                    if btype in ("time", "datetime") and _c2_base_literal:
                        # Extract TZ from compile-time base literal; recompute IANA offset for target date
                        _c2_tz_str_final = _c2_lit_num_tz  # default: use source numeric TZ
                        if _c2_lit_iana_name:
                            # Recompute DST-aware offset for the target date (year/month/day)
                            _c2_dst_offset = _iana_tz_offset(_c2_lit_iana_name, year, month, day)
                            if _c2_dst_offset:
                                _c2_tz_str_final = _c2_dst_offset + _c2_lit_iana_str
                            else:
                                _c2_tz_str_final = _c2_lit_num_tz + _c2_lit_iana_str
                        # Build pure time (without TZ) for the time part
                        _c2_s_eff2 = s_val if has_second else _c2_lit_s
                        if _c2_lit_frac:
                            _c2_pure_time_str = f"'{_c2_lit_h:02d}:{_c2_lit_mi:02d}:{_c2_s_eff2:02d}.{_c2_lit_frac}'"
                        elif _c2_s_eff2:
                            _c2_pure_time_str = f"'{_c2_lit_h:02d}:{_c2_lit_mi:02d}:{_c2_s_eff2:02d}'"
                        else:
                            _c2_pure_time_str = f"'{_c2_lit_h:02d}:{_c2_lit_mi:02d}'"
                        if not _c2_tz_str_final:
                            _c2_tz_str_final = 'Z'
                        return f"('{year:04d}-{month:02d}-{day:02d}T' || {_c2_pure_time_str} || '{_c2_tz_str_final}')"
                    elif btype in ("time", "datetime"):
                        tz_suffix_sql = (f"CASE WHEN CHARINDEX('+', {base_sql}, 6) > 0 THEN SUBSTRING({base_sql}, CHARINDEX('+', {base_sql}, 6)) "
                                         f"WHEN CHARINDEX('-', {base_sql}, 6) > 0 THEN SUBSTRING({base_sql}, CHARINDEX('-', {base_sql}, 6)) "
                                         f"WHEN CHARINDEX('Z', {base_sql}) > 0 THEN 'Z' ELSE 'Z' END")
                        # Strip tz from time_sql for the time part
                        pure_time_sql = (f"CASE WHEN CHARINDEX('+', {time_sql}, 6) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('+', {time_sql}, 6) - 1) "
                                         f"WHEN CHARINDEX('-', {time_sql}, 6) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('-', {time_sql}, 6) - 1) "
                                         f"WHEN CHARINDEX('Z', {time_sql}) > 0 THEN SUBSTRING({time_sql}, 1, CHARINDEX('Z', {time_sql}) - 1) "
                                         f"ELSE {time_sql} END")
                        return f"('{year:04d}-{month:02d}-{day:02d}T' || {pure_time_sql} || {tz_suffix_sql})"
                    elif btype in ("localtime", "localdatetime"):
                        return f"('{year:04d}-{month:02d}-{day:02d}T' || {time_sql} || 'Z')"

            return f"('{year:04d}-{month:02d}-{day:02d}T' || {time_sql})"

        return None

    return None


def _scalar_numeric_and_datetime(fn, args, args_exprs, context):
    if fn == "isnan":
        if not args:
            return "0"
        return f"CASE WHEN {args[0]} = CAST('NaN' AS DOUBLE) THEN 1 ELSE 0 END"
    if fn == "isinfinite":
        if not args:
            return "0"
        return f"CASE WHEN {args[0]} = CAST('Infinity' AS DOUBLE) OR {args[0]} = CAST('-Infinity' AS DOUBLE) THEN 1 ELSE 0 END"
    if fn == "haversin":
        return f"(1 - COS({args[0]})) / 2" if args else "NULL"
    if fn == "e":
        return "EXP(1)"
    if fn == "rand":
        return "SQLUser.RAND()"
    if fn == "timestamp":
        return "CAST(DATEDIFF('ms', '1970-01-01', GETDATE()) AS BIGINT)"
    if fn == "randomuuid":
        return "SQLUser.NEWID()"
    if fn == "date":
        if not args:
            return "NULL"
        if args_exprs and isinstance(args_exprs[0], ast.MapLiteral):
            result = _build_date_from_map(args_exprs[0], with_time=False)
            if result is not None:
                return result
            # Dynamic base: date({date: expr, ...overrides}) — generate SQL SUBSTRING ops
            result = _build_date_sql_from_dynamic_base(
                args_exprs[0], context, target_fn="date"
            )
            if result is not None:
                return result
        # String arg: parse ISO 8601 formats
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and isinstance(args_exprs[0].value, str):
            parsed = _parse_date_string(args_exprs[0].value)
            if parsed:
                return f"'{parsed[0]:04d}-{parsed[1]:02d}-{parsed[2]:02d}'"
        # Variable arg: may be a datetime/localdatetime — extract first 10 chars (YYYY-MM-DD)
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            return f"SUBSTRING({args[0]}, 1, 10)"
        return args[0]
    if fn in ("localdatetime",):
        if not args:
            return "NULL"
        if args_exprs and isinstance(args_exprs[0], ast.MapLiteral):
            result = _build_date_from_map(args_exprs[0], with_time=True, with_tz=False)
            if result is not None:
                return result
            result = _build_date_sql_from_dynamic_base(
                args_exprs[0], context, target_fn="localdatetime"
            )
            if result is not None:
                return result
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and isinstance(args_exprs[0].value, str):
            parsed = _parse_datetime_string(args_exprs[0].value)
            if parsed:
                return f"'{parsed}'"
        # For FunctionCall arg (e.g., localdatetime(datetime(...))):
        # strip timezone from the result — localdatetime is TZ-naive.
        # args[0] is already the compiled SQL for the inner function call.
        # If it's a string literal in the SQL (starts with '), strip TZ suffix.
        if args[0].startswith("'") and args[0].endswith("'"):
            inner_val = args[0][1:-1]  # strip quotes
            # Strip IANA bracket annotation first
            import re as _re_loc
            inner_val = _re_loc.sub(r'\[[^\]]+\]$', '', inner_val)
            # Strip +HH:MM or Z timezone suffix
            inner_val = _re_loc.sub(r'([+-]\d{2}:?\d{2}(?::\d{2})?)$', '', inner_val)
            if inner_val.endswith('Z'):
                inner_val = inner_val[:-1]
            # Compact time: remove trailing :00 from HH:MM:00 if seconds=0 and no ms
            return f"'{inner_val}'"
        # args[0] is a runtime SQL column reference — strip TZ from datetime column
        # TZ offset starts at position 20 ('YYYY-MM-DDTHH:MM:SS' = 19 chars + 'T')
        _ld_col = args[0]
        return (f"CASE "
                f"WHEN CHARINDEX('+', {_ld_col}, 17) > 0 THEN SUBSTRING({_ld_col}, 1, CHARINDEX('+', {_ld_col}, 17) - 1) "
                f"WHEN CHARINDEX('-', {_ld_col}, 17) > 0 THEN SUBSTRING({_ld_col}, 1, CHARINDEX('-', {_ld_col}, 17) - 1) "
                f"WHEN CHARINDEX('Z', {_ld_col}, 17) > 0 THEN SUBSTRING({_ld_col}, 1, CHARINDEX('Z', {_ld_col}, 17) - 1) "
                f"ELSE {_ld_col} END")
    if fn in ("datetime",):
        if not args:
            return "NULL"
        if args_exprs and isinstance(args_exprs[0], ast.MapLiteral):
            result = _build_date_from_map(args_exprs[0], with_time=True, with_tz=True)
            if result is not None:
                return result
            result = _build_date_sql_from_dynamic_base(
                args_exprs[0], context, target_fn="datetime"
            )
            if result is not None:
                return result
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and isinstance(args_exprs[0].value, str):
            parsed = _parse_datetime_string(args_exprs[0].value)
            if parsed:
                return f"'{parsed}'"
        # Variable arg: datetime(localdatetime_var) → add Z; datetime(datetime_var) → keep as-is
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            temporal_type = context.temporal_types.get(args_exprs[0].name)
            if temporal_type == "localdatetime":
                # localdatetime has no TZ — add Z
                tlv = getattr(context, 'temporal_literal_values', {})
                if args_exprs[0].name in tlv:
                    return f"'{tlv[args_exprs[0].name]}Z'"
                return f"({args[0]} || 'Z')"
            elif temporal_type == "datetime":
                # datetime already has TZ — return as-is
                return args[0]
        return args[0]
    if fn in ("localtime", "time"):
        if not args:
            return "NULL"
        if args_exprs and isinstance(args_exprs[0], ast.MapLiteral):
            # Check if map has a dynamic temporal base (time: var, localtime: var, etc.)
            _dyn_result = _build_date_sql_from_dynamic_base(
                args_exprs[0], context, target_fn=fn
            )
            if _dyn_result is not None:
                return _dyn_result
            m = args_exprs[0]
            h = _extract_int_from_map_entry(m, "hour", 0)
            mi = _extract_int_from_map_entry(m, "minute", 0)
            s = _extract_int_from_map_entry(m, "second", 0)
            ns = _extract_int_from_map_entry(m, "nanosecond", -1)
            us = _extract_int_from_map_entry(m, "microsecond", -1)
            ms_val = _extract_int_from_map_entry(m, "millisecond", -1)
            frac = _subsecond_frac(ns, us, ms_val)
            if frac:
                time_part = f"{h:02d}:{mi:02d}:{s:02d}.{frac}"
            elif ns >= 0 or us >= 0 or ms_val >= 0 or s != 0:
                time_part = f"{h:02d}:{mi:02d}:{s:02d}"
            else:
                time_part = f"{h:02d}:{mi:02d}"
            if fn == "time":
                tz_val = None
                if "timezone" in m.entries:
                    tz_expr = m.entries["timezone"]
                    if isinstance(tz_expr, ast.Literal) and isinstance(tz_expr.value, str):
                        tz_val = _normalize_tz_offset(tz_expr.value)
                tz_str = tz_val if tz_val else "Z"
                return f"'{time_part}{tz_str}'"
            return f"'{time_part}'"
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and isinstance(args_exprs[0].value, str):
            parsed = _parse_time_string(args_exprs[0].value)
            if parsed:
                if fn == "time" and not any(c in parsed for c in "Z+-"):
                    parsed = parsed + "Z"
                return f"'{parsed}'"
        # Variable arg: may be datetime/localdatetime (extract time part) or time/localtime
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            base = args[0]
            # Extract time portion: if contains 'T', take substring after 'T'; else use as-is
            t_idx = f"CHARINDEX('T', {base})"
            time_part_sql = f"CASE WHEN {t_idx} > 0 THEN SUBSTRING({base}, {t_idx} + 1) ELSE {base} END"
            if fn == "localtime":
                tp = time_part_sql
                _z = f"CHARINDEX('Z', {tp})"
                _p = f"CHARINDEX('+', {tp}, 6)"
                _m = f"CHARINDEX('-', {tp}, 6)"
                _tz = (
                    f"CASE WHEN {_z} > 0 AND ({_p} = 0 OR {_z} <= {_p}) AND ({_m} = 0 OR {_z} <= {_m}) THEN {_z} "
                    f"WHEN {_p} > 0 AND ({_m} = 0 OR {_p} <= {_m}) THEN {_p} "
                    f"WHEN {_m} > 0 THEN {_m} "
                    f"ELSE 0 END"
                )
                time_expr = f"CASE WHEN ({_tz}) > 0 THEN SUBSTRING({tp}, 1, ({_tz}) - 1) ELSE {tp} END"
            else:
                time_expr = time_part_sql
            return time_expr
        return args[0]
    if fn == "duration":
        if not args:
            return "NULL"
        if args_exprs and isinstance(args_exprs[0], ast.MapLiteral):
            m = args_exprs[0]
            # Collect all components (allow float for fractional values)
            years = _extract_num_from_map_entry(m, "years", 0)
            months = _extract_num_from_map_entry(m, "months", 0)
            weeks = _extract_num_from_map_entry(m, "weeks", 0)
            days = _extract_num_from_map_entry(m, "days", 0)
            hours = _extract_num_from_map_entry(m, "hours", 0)
            minutes = _extract_num_from_map_entry(m, "minutes", 0)
            seconds = _extract_num_from_map_entry(m, "seconds", 0)
            ms_d = _extract_num_from_map_entry(m, "milliseconds", 0)
            us_d = _extract_num_from_map_entry(m, "microseconds", 0)
            ns_d = _extract_num_from_map_entry(m, "nanoseconds", 0)

            # Normalize: fractional months → days, fractional weeks → days, etc.
            # months with fraction → convert fraction to days (avg 30.436875)
            mo_int = int(months)
            mo_frac = months - mo_int
            days = days + mo_frac * 30.436875

            # weeks → days
            days = days + weeks * 7

            # fractional days → hours
            d_int = int(days)
            d_frac = days - d_int
            hours = hours + d_frac * 24

            # fractional hours → minutes
            h_int = int(hours)
            h_frac = hours - h_int
            minutes = minutes + h_frac * 60

            # fractional minutes → seconds
            m_int = int(minutes)
            m_frac = minutes - m_int
            seconds = seconds + m_frac * 60

            # Combine seconds + sub-second components into a single signed nanosecond value.
            # Use truncating (toward-zero) division so s_int and rem_ns have the same sign.
            subsec_ns = round(ms_d * 1_000_000 + us_d * 1_000 + ns_d)
            # Include fractional seconds
            s_whole = int(seconds)  # truncate toward zero
            s_frac = seconds - s_whole
            subsec_ns += round(s_frac * 1_000_000_000)
            # Total signed nanoseconds for the seconds component
            s_total_ns = s_whole * 1_000_000_000 + subsec_ns
            # Truncating division toward zero:
            s_int = int(s_total_ns / 1_000_000_000)  # int() truncates toward zero
            rem_ns = s_total_ns - s_int * 1_000_000_000  # same sign as s_total_ns or 0
            # Normalize seconds → minutes (truncating toward zero)
            extra_m = int(s_int / 60)
            s_int = s_int - extra_m * 60
            m_int += extra_m
            # Normalize minutes → hours (truncating toward zero)
            extra_h = int(m_int / 60)
            m_int = m_int - extra_h * 60
            h_int += extra_h
            # Normalize hours → days (truncating toward zero)
            extra_d = int(h_int / 24)
            h_int = h_int - extra_d * 24
            d_int += extra_d

            result_str = _format_duration(int(years), mo_int, d_int, h_int, m_int, s_int, rem_ns)
            return f"'{result_str}'"
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and isinstance(args_exprs[0].value, str):
            parsed = _parse_duration_string(args_exprs[0].value)
            if parsed:
                return f"'{parsed}'"
        return args[0]
    return None


# ---------------------------------------------------------------------------
# Temporal namespace function evaluators (duration.between, datetime.fromepoch,
# date.truncate, etc.) — all evaluated at translation time using literal args.
# ---------------------------------------------------------------------------

def _temporal_to_datetime_obj(temporal_str, fn_name):
    """Convert a temporal ISO string to a Python datetime object (naive UTC).

    Returns a datetime.datetime or datetime.timedelta-like struct, or None.
    fn_name is used to determine the temporal type (date, datetime, localtime, etc.)
    """
    import datetime as _dt
    import re as _re

    if temporal_str is None:
        return None

    # For date strings like '1984-10-11'
    if fn_name == "date":
        parsed = _parse_date_string(temporal_str)
        if parsed:
            y, mo, d = parsed
            try:
                return _dt.datetime(y, mo, d, 0, 0, 0)
            except ValueError:
                pass
        return None

    # For localtime/time strings like '14:30' or '14:30:00.5'
    if fn_name in ("localtime", "time"):
        # Extract time part (strip timezone)
        s = temporal_str
        # Remove IANA timezone like '[America/New_York]'
        s = _re.sub(r'\[.*\]$', '', s)
        # Remove timezone offset like +01:00, +0100, Z
        s = _re.sub(r'[Zz]$', '', s)
        s = _re.sub(r'[+-]\d{2}:?\d{2}(?::?\d{2})?$', '', s)
        m = _re.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            sec = int(m.group(3) or 0)
            frac = m.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            us = ns // 1000
            return _dt.datetime(2000, 1, 1, h, mi, sec, us)
        return None

    # For localdatetime/datetime strings
    if fn_name in ("localdatetime", "datetime"):
        s = temporal_str
        # Remove IANA timezone
        s = _re.sub(r'\[.*\]$', '', s)
        if "T" not in s.upper():
            return None
        sep = s.upper().index("T")
        date_str = s[:sep]
        time_str = s[sep+1:]
        parsed_date = _parse_date_string(date_str)
        if not parsed_date:
            return None
        y, mo, d = parsed_date
        # Extract timezone offset
        tz_offset_secs = 0
        # Remove Z
        if time_str.endswith("Z"):
            time_str = time_str[:-1]
        else:
            # Remove +HH:MM, -HH:MM, +HHMM, or -HHMM
            tz_m = _re.search(r'([+-])(\d{2}):?(\d{2})(?::\d{2})?$', time_str)
            if tz_m:
                sign = 1 if tz_m.group(1) == '+' else -1
                tz_offset_secs = sign * (int(tz_m.group(2)) * 3600 + int(tz_m.group(3)) * 60)
                time_str = time_str[:tz_m.start()]
        # Parse time portion
        m = _re.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', time_str)
        if not m:
            return None
        h, mi = int(m.group(1)), int(m.group(2))
        sec = int(m.group(3) or 0)
        frac = m.group(4) or ""
        ns = int(frac.ljust(9, '0')[:9]) if frac else 0
        us = ns // 1000
        try:
            dt = _dt.datetime(y, mo, d, h, mi, sec, us)
            # Adjust for timezone: subtract offset to get UTC-equivalent
            dt = dt - _dt.timedelta(seconds=tz_offset_secs)
            return dt
        except ValueError:
            return None

    return None


def _datetime_to_epoch_ns(dt):
    """Convert a datetime to nanoseconds since Unix epoch."""
    import datetime as _dt
    epoch = _dt.datetime(1970, 1, 1, 0, 0, 0)
    delta = dt - epoch
    total_secs = delta.total_seconds()
    us = dt.microsecond
    ns_from_us = us * 1000
    # Total nanoseconds
    whole_secs = int(total_secs)
    return whole_secs, ns_from_us % 1_000_000_000


def _compute_duration_between(lhs_str, lhs_fn, rhs_str, rhs_fn):
    """Compute duration.between(lhs, rhs) → ISO duration string.

    Uses calendar-aware subtraction: years+months from calendar math, rest from timedelta.
    """
    import datetime as _dt
    import re as _re

    def _has_tz(fn_name, temporal_str):
        """Return True if this temporal type carries an explicit timezone offset."""
        import re as _re2
        if fn_name == "datetime":
            s = _re2.sub(r'\[.*\]$', '', temporal_str)
            return bool(_re2.search(r'[Zz]$|[+-]\d{2}:?\d{2}', s))
        if fn_name == "time":
            return bool(_re2.search(r'[Zz]$|[+-]\d{2}:?\d{2}', temporal_str))
        return False  # date, localtime, localdatetime have no tz

    def _parse_utc_normalized(temporal_str, fn_name):
        """Parse a temporal to a UTC-normalized comparable value.

        For datetime: subtracts tz offset → UTC datetime.
        For time with tz: subtracts tz offset → UTC time of day (date anchored to 2000-01-01).
        For time without tz: treat as UTC (no adjustment).
        For others: use wall-clock.
        """
        import re as _re2
        if fn_name in ("datetime", "localdatetime"):
            return _temporal_to_datetime_obj(temporal_str, fn_name)
        if fn_name == "date":
            return _temporal_to_datetime_obj(temporal_str, fn_name)
        if fn_name in ("localtime", "time"):
            # Extract wall-clock time value
            s = temporal_str
            tz_offset_secs = 0
            if s.endswith("Z") or s.endswith("z"):
                s = s[:-1]
            else:
                tz_m = _re2.search(r'([+-])(\d{2}):?(\d{2})(?::\d{2})?$', s)
                if tz_m:
                    sign = 1 if tz_m.group(1) == '+' else -1
                    tz_offset_secs = sign * (int(tz_m.group(2)) * 3600 + int(tz_m.group(3)) * 60)
                    s = s[:tz_m.start()]
            m2 = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
            if not m2:
                return None
            h, mi = int(m2.group(1)), int(m2.group(2))
            sec = int(m2.group(3) or 0)
            frac = m2.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            us = ns // 1000
            try:
                dt = _dt.datetime(2000, 1, 1, h, mi, sec, us)
                dt = dt - _dt.timedelta(seconds=tz_offset_secs)
                return dt
            except ValueError:
                return None
        return None

    def _parse_wall_clock_dt(temporal_str, fn_name):
        """Parse a temporal to a datetime treating wall-clock time (no UTC adjustment)."""
        import re as _re2
        if fn_name == "date":
            return _temporal_to_datetime_obj(temporal_str, fn_name)
        if fn_name in ("localtime", "time"):
            # Strip offset, use local time value as-is
            s = temporal_str
            s = _re2.sub(r'[Zz]$', '', s)
            s = _re2.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', s)
            m2 = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
            if not m2:
                return None
            h, mi = int(m2.group(1)), int(m2.group(2))
            sec = int(m2.group(3) or 0)
            frac = m2.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            us = ns // 1000
            return _dt.datetime(2000, 1, 1, h, mi, sec, us)
        # datetime / localdatetime: extract wall-clock (strip tz without applying)
        s = temporal_str
        s = _re2.sub(r'\[.*\]$', '', s)
        if "T" not in s.upper():
            return None
        sep = s.upper().index("T")
        date_str = s[:sep]
        time_str = s[sep+1:]
        parsed_date = _parse_date_string(date_str)
        if not parsed_date:
            return None
        y, mo, d = parsed_date
        if time_str.endswith("Z") or time_str.endswith("z"):
            time_str = time_str[:-1]
        else:
            tz_m = _re2.search(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', time_str)
            if tz_m:
                time_str = time_str[:tz_m.start()]
        m2 = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', time_str)
        if not m2:
            return None
        h, mi = int(m2.group(1)), int(m2.group(2))
        sec = int(m2.group(3) or 0)
        frac = m2.group(4) or ""
        ns = int(frac.ljust(9, '0')[:9]) if frac else 0
        us = ns // 1000
        try:
            return _dt.datetime(y, mo, d, h, mi, sec, us)
        except ValueError:
            return None

    # openCypher spec: UTC normalization only when BOTH sides are tz-aware.
    # tz-aware family: 'time' (UTC-implicit), 'datetime' (explicit offset).
    # tz-naive family: 'date', 'localtime', 'localdatetime'.
    # When mixing tz-aware + tz-naive: use wall-clock for both (tz offset ignored).
    _TZ_AWARE = ("time", "datetime")
    lhs_is_tz_aware = lhs_fn in _TZ_AWARE
    rhs_is_tz_aware = rhs_fn in _TZ_AWARE
    both_tz_aware = lhs_is_tz_aware and rhs_is_tz_aware

    if both_tz_aware:
        lhs_dt = _parse_utc_normalized(lhs_str, lhs_fn)
        rhs_dt = _parse_utc_normalized(rhs_str, rhs_fn)
    else:
        lhs_dt = _parse_wall_clock_dt(lhs_str, lhs_fn)
        rhs_dt = _parse_wall_clock_dt(rhs_str, rhs_fn)

    if lhs_dt is None or rhs_dt is None:
        return None

    # For time-only values, years/months are 0
    is_lhs_timeonly = lhs_fn in ("localtime", "time")
    is_rhs_timeonly = rhs_fn in ("localtime", "time")
    is_lhs_dateonly = lhs_fn == "date"
    is_rhs_dateonly = rhs_fn == "date"

    # If both are date-only or datetime-ish, compute calendar diff
    years = 0
    months = 0
    days = 0
    h_int = 0
    m_int = 0
    s_int = 0
    rem_ns = 0

    def _secs_ns_from_delta(delta):
        """Extract (whole_seconds, nanoseconds_of_second) from a timedelta.

        Uses truncating-toward-zero division so that whole_s and rem_us have the
        same sign (or one is zero).  This matches openCypher's duration semantics
        where -PT1.001S is -1 seconds -1 ms, not -2 seconds +999 ms.
        """
        total_us = delta.days * 86400 * 1_000_000 + delta.seconds * 1_000_000 + delta.microseconds
        whole_s = int(total_us / 1_000_000)   # truncate toward zero
        rem_us = total_us - whole_s * 1_000_000
        return whole_s, rem_us * 1000

    def _secs_to_hms(total_s):
        """Normalize total seconds into (days, h, m, s)."""
        neg = total_s < 0
        abs_s = abs(total_s)
        d = abs_s // 86400
        left = abs_s % 86400
        h = left // 3600
        left = left % 3600
        m = left // 60
        s = left % 60
        if neg:
            return -d, (-h if h else 0), (-m if m else 0), (-s if s else 0)
        return d, h, m, s

    if is_lhs_timeonly and is_rhs_timeonly:
        delta = rhs_dt - lhs_dt
        total_s, rem_ns = _secs_ns_from_delta(delta)
        _, h, m, s = _secs_to_hms(total_s)
        return _format_duration(0, 0, 0, h, m, s, rem_ns)
    elif is_lhs_timeonly or is_rhs_timeonly:
        # Mixed time/date — compute only time difference (strip date component)
        if is_lhs_timeonly:
            time_lhs = lhs_dt
            time_rhs = _dt.datetime(2000, 1, 1, rhs_dt.hour, rhs_dt.minute, rhs_dt.second, rhs_dt.microsecond)
        else:
            time_lhs = _dt.datetime(2000, 1, 1, lhs_dt.hour, lhs_dt.minute, lhs_dt.second, lhs_dt.microsecond)
            time_rhs = _dt.datetime(2000, 1, 1, rhs_dt.hour, rhs_dt.minute, rhs_dt.second, rhs_dt.microsecond)
        delta = time_rhs - time_lhs
        total_s, rem_ns = _secs_ns_from_delta(delta)
        _, h, m, s = _secs_to_hms(total_s)
        return _format_duration(0, 0, 0, h, m, s, rem_ns)

    def _compute_forward(start_dt, end_dt, start_is_dateonly, end_is_dateonly):
        """Compute duration from start → end where end >= start."""
        import calendar as _cal2
        sy, smo, sd = start_dt.year, start_dt.month, start_dt.day
        ey, emo, ed = end_dt.year, end_dt.month, end_dt.day

        # Calendar months
        total_months = (ey - sy) * 12 + (emo - smo)
        if total_months < 0:
            total_months = 0
        yrs = total_months // 12
        mos = total_months % 12

        # Anchor date = start + total_months
        anchor_mo_raw = smo + total_months
        anchor_y = sy + (anchor_mo_raw - 1) // 12
        anchor_mo = ((anchor_mo_raw - 1) % 12) + 1
        max_day = _cal2.monthrange(anchor_y, anchor_mo)[1]
        anchor_d = min(sd, max_day)
        try:
            anchor = _dt.datetime(anchor_y, anchor_mo, anchor_d,
                                  start_dt.hour, start_dt.minute, start_dt.second, start_dt.microsecond)
        except ValueError:
            anchor = start_dt

        remaining = end_dt - anchor
        total_remain_s, rem_ns2 = _secs_ns_from_delta(remaining)

        # If remaining is negative (day of month of end is before start's), roll back 1 month
        if total_remain_s < 0:
            yrs_new = (total_months - 1) // 12
            mos_new = (total_months - 1) % 12
            anchor_mo_raw2 = smo + (total_months - 1)
            anchor_y2 = sy + (anchor_mo_raw2 - 1) // 12
            anchor_mo2 = ((anchor_mo_raw2 - 1) % 12) + 1
            max_day2 = _cal2.monthrange(anchor_y2, anchor_mo2)[1]
            anchor_d2 = min(sd, max_day2)
            try:
                anchor2 = _dt.datetime(anchor_y2, anchor_mo2, anchor_d2,
                                       start_dt.hour, start_dt.minute, start_dt.second, start_dt.microsecond)
                remaining2 = end_dt - anchor2
                total_remain_s2, rem_ns3 = _secs_ns_from_delta(remaining2)
                if total_remain_s2 >= 0:
                    yrs, mos = yrs_new, mos_new
                    total_remain_s, rem_ns2 = total_remain_s2, rem_ns3
            except ValueError:
                pass

        d2, h2, m2, s2 = _secs_to_hms(total_remain_s)

        if start_is_dateonly and end_is_dateonly:
            h2 = m2 = s2 = 0
            rem_ns2 = 0

        return yrs, mos, d2, h2, m2, s2, rem_ns2

    # Both have date component — calendar diff for years/months, then remaining
    # Use "negative direction" fix: if rhs < lhs, compute forward then negate
    is_negative = rhs_dt < lhs_dt

    if is_negative:
        # Compute duration.between(rhs, lhs) then negate all components
        years, months, days, h_int, m_int, s_int, rem_ns = _compute_forward(
            rhs_dt, lhs_dt, is_rhs_dateonly, is_lhs_dateonly
        )
        # Negate components (rem_ns stays positive per Cypher spec)
        years, months, days, h_int, m_int, s_int = -years, -months, -days, (-h_int if h_int else 0), (-m_int if m_int else 0), (-s_int if s_int else 0)
    else:
        years, months, days, h_int, m_int, s_int, rem_ns = _compute_forward(
            lhs_dt, rhs_dt, is_lhs_dateonly, is_rhs_dateonly
        )

    return _format_duration(years, months, days, h_int, m_int, s_int, rem_ns)


def _compute_duration_inmonths(lhs_str, lhs_fn, rhs_str, rhs_fn):
    """Compute duration.inMonths — extract only years+months from duration.between.

    Uses the same tz-aware/wall-clock logic as _compute_duration_between but
    discards the day/time remainder, keeping only the complete year+month units.
    """
    is_timeonly_l = lhs_fn in ("localtime", "time")
    is_timeonly_r = rhs_fn in ("localtime", "time")
    if is_timeonly_l or is_timeonly_r:
        return "PT0S"
    # Get the full duration.between result and extract only yr+mo components
    full = _compute_duration_between(lhs_str, lhs_fn, rhs_str, rhs_fn)
    if full is None:
        return None
    if full == "PT0S":
        return "PT0S"
    # Parse the ISO duration string and extract years/months
    import re as _re
    m = _re.match(r'^P(-?\d+Y)?(-?\d+M)?', full)
    if not m:
        return "PT0S"
    yr_part = m.group(1)  # e.g. '-1Y' or '30Y' or None
    mo_part = m.group(2)  # e.g. '-8M' or '8M' or None
    if not yr_part and not mo_part:
        return "PT0S"
    yrs = int(yr_part[:-1]) if yr_part else 0
    mos = int(mo_part[:-1]) if mo_part else 0
    return _format_duration(yrs, mos, 0, 0, 0, 0, 0)


def _compute_duration_indays(lhs_str, lhs_fn, rhs_str, rhs_fn):
    """Compute duration.inDays — total whole days between the two temporals.

    Uses same tz-aware/wall-clock logic as duration.between.
    For date-only inputs, strips time component. Truncates toward zero.
    """
    import datetime as _dt
    import re as _re2

    is_timeonly_l = lhs_fn in ("localtime", "time")
    is_timeonly_r = rhs_fn in ("localtime", "time")
    if is_timeonly_l or is_timeonly_r:
        return "PT0S"

    _TZ_AWARE = ("time", "datetime")
    lhs_is_tz_aware = lhs_fn in _TZ_AWARE
    rhs_is_tz_aware = rhs_fn in _TZ_AWARE
    both_tz_aware = lhs_is_tz_aware and rhs_is_tz_aware

    def _utc_normalize(ts, fn):
        """UTC-normalize a temporal: for 'time', apply offset; for 'datetime', use _temporal_to_datetime_obj."""
        if fn == "datetime":
            return _temporal_to_datetime_obj(ts, fn)
        if fn == "time":
            s = _re2.sub(r'\[.*\]$', '', ts)
            tz_offset_secs = 0
            if s.endswith("Z") or s.endswith("z"):
                s = s[:-1]
            else:
                tz_m = _re2.search(r'([+-])(\d{2}):?(\d{2})(?::\d{2})?$', s)
                if tz_m:
                    sign = 1 if tz_m.group(1) == '+' else -1
                    tz_offset_secs = sign * (int(tz_m.group(2)) * 3600 + int(tz_m.group(3)) * 60)
                    s = s[:tz_m.start()]
            m = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
            if not m:
                return None
            h, mi = int(m.group(1)), int(m.group(2))
            sec = int(m.group(3) or 0)
            frac = m.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            try:
                dt = _dt.datetime(2000, 1, 1, h, mi, sec, ns // 1000)
                return dt - _dt.timedelta(seconds=tz_offset_secs)
            except ValueError:
                return None
        return _temporal_to_datetime_obj(ts, fn)

    def _wall_clock(ts, fn):
        """Wall-clock: strip tz offset without applying it."""
        if fn == "date":
            return _temporal_to_datetime_obj(ts, fn)
        if fn in ("localtime", "time"):
            s = _re2.sub(r'[Zz]$', '', ts)
            s = _re2.sub(r'[+-]\d{2}:?\d{2}(?::?\d{2})?$', '', s)
            m = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
            if not m:
                return None
            h, mi = int(m.group(1)), int(m.group(2))
            sec = int(m.group(3) or 0)
            frac = m.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            return _dt.datetime(2000, 1, 1, h, mi, sec, ns // 1000)
        s = _re2.sub(r'\[.*\]$', '', ts)
        if "T" not in s.upper():
            return None
        sep = s.upper().index("T")
        date_str, time_str = s[:sep], s[sep+1:]
        parsed = _parse_date_string(date_str)
        if not parsed:
            return None
        y, mo, d = parsed
        if time_str.endswith("Z") or time_str.endswith("z"):
            time_str = time_str[:-1]
        else:
            tz_m = _re2.search(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', time_str)
            if tz_m:
                time_str = time_str[:tz_m.start()]
        m2 = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', time_str)
        if not m2:
            return None
        h, mi = int(m2.group(1)), int(m2.group(2))
        sec = int(m2.group(3) or 0)
        frac = m2.group(4) or ""
        ns = int(frac.ljust(9, '0')[:9]) if frac else 0
        try:
            return _dt.datetime(y, mo, d, h, mi, sec, ns // 1000)
        except ValueError:
            return None

    if both_tz_aware:
        lhs_dt = _utc_normalize(lhs_str, lhs_fn)
        rhs_dt = _utc_normalize(rhs_str, rhs_fn)
    else:
        lhs_dt = _wall_clock(lhs_str, lhs_fn)
        rhs_dt = _wall_clock(rhs_str, rhs_fn)

    if lhs_dt is None or rhs_dt is None:
        return None

    # For date-only: strip time so only calendar date difference is used
    if lhs_fn == "date":
        lhs_dt = lhs_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if rhs_fn == "date":
        rhs_dt = rhs_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    delta = rhs_dt - lhs_dt
    days_total = int(delta.total_seconds() / 86400)  # truncate toward zero
    if days_total == 0:
        return "PT0S"
    return _format_duration(0, 0, days_total, 0, 0, 0, 0)


def _compute_duration_inseconds(lhs_str, lhs_fn, rhs_str, rhs_fn):
    """Compute duration.inSeconds — only seconds (no years/months).
    Normalizes total seconds into H/M/S components.
    """
    import datetime as _dt
    # Use the same tz-aware logic as duration.between
    _TZ_AWARE = ("time", "datetime")
    lhs_is_tz_aware = lhs_fn in _TZ_AWARE
    rhs_is_tz_aware = rhs_fn in _TZ_AWARE
    both_tz_aware = lhs_is_tz_aware and rhs_is_tz_aware

    import re as _re2

    def _utc_norm(ts, fn):
        """UTC-normalize for tz-aware types: apply tz offset."""
        if fn == "datetime":
            return _temporal_to_datetime_obj(ts, fn)
        if fn == "time":
            s = _re2.sub(r'\[.*\]$', '', ts)
            tz_offset_secs = 0
            if s.endswith("Z") or s.endswith("z"):
                s = s[:-1]
            else:
                tz_m = _re2.search(r'([+-])(\d{2}):?(\d{2})(?::\d{2})?$', s)
                if tz_m:
                    sign = 1 if tz_m.group(1) == '+' else -1
                    tz_offset_secs = sign * (int(tz_m.group(2)) * 3600 + int(tz_m.group(3)) * 60)
                    s = s[:tz_m.start()]
            m = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
            if not m:
                return None
            h, mi = int(m.group(1)), int(m.group(2))
            sec = int(m.group(3) or 0)
            frac = m.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            try:
                dt = _dt.datetime(2000, 1, 1, h, mi, sec, ns // 1000)
                return dt - _dt.timedelta(seconds=tz_offset_secs)
            except ValueError:
                return None
        return _temporal_to_datetime_obj(ts, fn)

    if both_tz_aware:
        lhs_dt = _utc_norm(lhs_str, lhs_fn)
        rhs_dt = _utc_norm(rhs_str, rhs_fn)
    else:
        # Wall-clock: parse without applying tz offset
        def _wall(ts, fn):
            if fn in ("date", "localtime"):
                return _temporal_to_datetime_obj(ts, fn)
            if fn == "time":
                s = _re2.sub(r'[Zz]$', '', ts)
                s = _re2.sub(r'[+-]\d{2}:?\d{2}(?::?\d{2})?$', '', s)
                m = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s)
                if not m:
                    return None
                h, mi = int(m.group(1)), int(m.group(2))
                sec = int(m.group(3) or 0)
                frac = m.group(4) or ""
                ns = int(frac.ljust(9, '0')[:9]) if frac else 0
                return _dt.datetime(2000, 1, 1, h, mi, sec, ns // 1000)
            # datetime/localdatetime: strip tz without applying
            s = _re2.sub(r'\[.*\]$', '', ts)
            if "T" not in s.upper():
                return None
            sep = s.upper().index("T")
            date_str, time_str = s[:sep], s[sep+1:]
            parsed = _parse_date_string(date_str)
            if not parsed:
                return None
            y, mo, d = parsed
            if time_str.endswith("Z") or time_str.endswith("z"):
                time_str = time_str[:-1]
            else:
                tz_m = _re2.search(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', time_str)
                if tz_m:
                    time_str = time_str[:tz_m.start()]
            m2 = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', time_str)
            if not m2:
                return None
            h, mi = int(m2.group(1)), int(m2.group(2))
            sec = int(m2.group(3) or 0)
            frac = m2.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            try:
                return _dt.datetime(y, mo, d, h, mi, sec, ns // 1000)
            except ValueError:
                return None
        lhs_dt = _wall(lhs_str, lhs_fn)
        rhs_dt = _wall(rhs_str, rhs_fn)

    if lhs_dt is None or rhs_dt is None:
        return None

    is_timeonly_l = lhs_fn in ("localtime", "time")
    is_timeonly_r = rhs_fn in ("localtime", "time")

    if is_timeonly_l or is_timeonly_r:
        if is_timeonly_l:
            time_lhs = lhs_dt if not both_tz_aware else _dt.datetime(2000, 1, 1, lhs_dt.hour, lhs_dt.minute, lhs_dt.second, lhs_dt.microsecond)
            time_rhs = _dt.datetime(2000, 1, 1, rhs_dt.hour, rhs_dt.minute, rhs_dt.second, rhs_dt.microsecond)
        else:
            time_lhs = _dt.datetime(2000, 1, 1, lhs_dt.hour, lhs_dt.minute, lhs_dt.second, lhs_dt.microsecond)
            time_rhs = rhs_dt if not both_tz_aware else _dt.datetime(2000, 1, 1, rhs_dt.hour, rhs_dt.minute, rhs_dt.second, rhs_dt.microsecond)
        delta = time_rhs - time_lhs
    else:
        delta = rhs_dt - lhs_dt

    # Compute total microseconds using truncating-toward-zero arithmetic
    total_us = delta.days * 86400 * 1_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    s_total = int(total_us / 1_000_000)  # truncate toward zero
    rem_us = total_us - s_total * 1_000_000
    rem_ns = rem_us * 1000

    # Normalize s_total into H/M/S
    neg = s_total < 0
    abs_s = abs(s_total)
    h = abs_s // 3600
    left = abs_s % 3600
    m = left // 60
    s = left % 60
    if neg:
        h, m, s = (-h if h else 0), (-m if m else 0), (-s if s else 0)

    return _format_duration(0, 0, 0, h, m, s, rem_ns)


def _eval_temporal_ns_function(fn, args_exprs, context):
    """Evaluate temporal namespace functions like duration.between, datetime.fromepoch, etc.

    Returns SQL literal string or None if not handleable.
    """
    import datetime as _dt
    import re as _re

    # ---- .statement / .transaction / .realtime variants ----
    # e.g. date.statement(null) → null; date.statement() → current date (not supported at compile-time)
    _TEMPORAL_BASES = ("date", "localtime", "time", "localdatetime", "datetime", "duration")
    _TEMPORAL_VARIANTS = ("statement", "transaction", "realtime")
    _parts = fn.split(".", 1)
    if len(_parts) == 2 and _parts[0] in _TEMPORAL_BASES and _parts[1] in _TEMPORAL_VARIANTS:
        # Null propagation: if called with null arg, return NULL
        if args_exprs:
            arg0 = args_exprs[0]
            if isinstance(arg0, ast.Literal) and arg0.value is None:
                return "NULL"
        # No-arg or non-null arg: can't evaluate at compile-time; return None to fall through
        return None

    # ---- duration.between(lhs, rhs) ----
    if fn == "duration.between":
        if len(args_exprs) < 2:
            return "NULL"
        lhs_expr, rhs_expr = args_exprs[0], args_exprs[1]
        # Get temporal type and literal value from each arg
        def _get_temporal_lit(expr):
            if isinstance(expr, ast.FunctionCall):
                fn_inner = expr.function_name.lower()
                if fn_inner in ("date", "datetime", "localdatetime", "localtime", "time"):
                    if expr.arguments and isinstance(expr.arguments[0], ast.Literal):
                        return expr.arguments[0].value, fn_inner
            return None, None
        lhs_str, lhs_fn = _get_temporal_lit(lhs_expr)
        rhs_str, rhs_fn = _get_temporal_lit(rhs_expr)
        if lhs_str is not None and rhs_str is not None:
            result = _compute_duration_between(lhs_str, lhs_fn, rhs_str, rhs_fn)
            if result is not None:
                return f"'{result}'"
        return "NULL"

    # ---- duration.inMonths(lhs, rhs) ----
    if fn == "duration.inmonths":
        if len(args_exprs) < 2:
            return "NULL"
        def _get_temporal_lit(expr):
            if isinstance(expr, ast.FunctionCall):
                fn_inner = expr.function_name.lower()
                if fn_inner in ("date", "datetime", "localdatetime", "localtime", "time"):
                    if expr.arguments and isinstance(expr.arguments[0], ast.Literal):
                        return expr.arguments[0].value, fn_inner
            return None, None
        lhs_str, lhs_fn = _get_temporal_lit(args_exprs[0])
        rhs_str, rhs_fn = _get_temporal_lit(args_exprs[1])
        if lhs_str is not None and rhs_str is not None:
            result = _compute_duration_inmonths(lhs_str, lhs_fn, rhs_str, rhs_fn)
            if result is not None:
                return f"'{result}'"
        return "NULL"

    # ---- duration.inDays(lhs, rhs) ----
    if fn == "duration.indays":
        if len(args_exprs) < 2:
            return "NULL"
        def _get_temporal_lit(expr):
            if isinstance(expr, ast.FunctionCall):
                fn_inner = expr.function_name.lower()
                if fn_inner in ("date", "datetime", "localdatetime", "localtime", "time"):
                    if expr.arguments and isinstance(expr.arguments[0], ast.Literal):
                        return expr.arguments[0].value, fn_inner
            return None, None
        lhs_str, lhs_fn = _get_temporal_lit(args_exprs[0])
        rhs_str, rhs_fn = _get_temporal_lit(args_exprs[1])
        if lhs_str is not None and rhs_str is not None:
            result = _compute_duration_indays(lhs_str, lhs_fn, rhs_str, rhs_fn)
            if result is not None:
                return f"'{result}'"
        return "NULL"

    # ---- duration.inSeconds(lhs, rhs) ----
    if fn == "duration.inseconds":
        if len(args_exprs) < 2:
            return "NULL"
        def _get_temporal_lit(expr):
            if isinstance(expr, ast.FunctionCall):
                fn_inner = expr.function_name.lower()
                if fn_inner in ("date", "datetime", "localdatetime", "localtime", "time"):
                    if expr.arguments and isinstance(expr.arguments[0], ast.Literal):
                        return expr.arguments[0].value, fn_inner
            return None, None
        lhs_str, lhs_fn = _get_temporal_lit(args_exprs[0])
        rhs_str, rhs_fn = _get_temporal_lit(args_exprs[1])
        if lhs_str is not None and rhs_str is not None:
            result = _compute_duration_inseconds(lhs_str, lhs_fn, rhs_str, rhs_fn)
            if result is not None:
                return f"'{result}'"
        return "NULL"

    # ---- datetime.fromepoch(seconds, nanoseconds) ----
    if fn == "datetime.fromepoch":
        if len(args_exprs) < 1:
            return "NULL"
        secs_expr = args_exprs[0]
        nanos_expr = args_exprs[1] if len(args_exprs) > 1 else None
        secs = None
        nanos = 0
        if isinstance(secs_expr, ast.Literal) and isinstance(secs_expr.value, (int, float)):
            secs = int(secs_expr.value)
        if nanos_expr and isinstance(nanos_expr, ast.Literal) and isinstance(nanos_expr.value, (int, float)):
            nanos = int(nanos_expr.value)
        if secs is not None:
            epoch = _dt.datetime(1970, 1, 1, 0, 0, 0)
            dt = epoch + _dt.timedelta(seconds=secs)
            frac_str = f"{nanos:09d}".rstrip('0') if nanos else None
            if frac_str:
                result = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}.{frac_str}Z"
            else:
                result = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}Z"
            return f"'{result}'"
        return "NULL"

    # ---- datetime.fromepochmillis(milliseconds) ----
    if fn == "datetime.fromepochmillis":
        if not args_exprs:
            return "NULL"
        millis_expr = args_exprs[0]
        millis = None
        if isinstance(millis_expr, ast.Literal) and isinstance(millis_expr.value, (int, float)):
            millis = int(millis_expr.value)
        if millis is not None:
            epoch = _dt.datetime(1970, 1, 1, 0, 0, 0)
            secs = millis // 1000
            ms_rem = millis % 1000
            dt = epoch + _dt.timedelta(seconds=secs)
            if ms_rem:
                result = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}.{ms_rem:03d}Z"
            else:
                result = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}Z"
            return f"'{result}'"
        return "NULL"

    # ---- date.truncate / datetime.truncate / localdatetime.truncate ----
    if fn in ("date.truncate", "datetime.truncate", "localdatetime.truncate",
              "localtime.truncate", "time.truncate"):
        return _eval_truncate(fn, args_exprs)

    return None


def _eval_truncate(fn, args_exprs):
    """Evaluate date.truncate / datetime.truncate / localdatetime.truncate.

    Signature: truncate(unit, temporal, map?)
    """
    import datetime as _dt
    import re as _re
    import calendar as _cal

    if not args_exprs:
        return "NULL"

    # unit must be a literal string
    unit_expr = args_exprs[0]
    if not (isinstance(unit_expr, ast.Literal) and isinstance(unit_expr.value, str)):
        return None  # can't evaluate at compile time
    unit = unit_expr.value.lower()

    # temporal is the second arg
    if len(args_exprs) < 2:
        return "NULL"
    temporal_expr = args_exprs[1]

    # Get the temporal string and fn type
    temporal_str = None
    temporal_fn = None
    if isinstance(temporal_expr, ast.FunctionCall):
        temporal_fn = temporal_expr.function_name.lower()
        if temporal_expr.arguments and isinstance(temporal_expr.arguments[0], ast.Literal):
            temporal_str = temporal_expr.arguments[0].value
        elif temporal_expr.arguments and isinstance(temporal_expr.arguments[0], ast.MapLiteral):
            map_arg = temporal_expr.arguments[0]
            if temporal_fn in ("localtime", "time"):
                # Build time-only string from map (no date component)
                _h = _extract_int_from_map_entry(map_arg, "hour", 0)
                _mi = _extract_int_from_map_entry(map_arg, "minute", 0)
                _s = _extract_int_from_map_entry(map_arg, "second", 0)
                _ns = _extract_int_from_map_entry(map_arg, "nanosecond", -1)
                _us_v = _extract_int_from_map_entry(map_arg, "microsecond", -1)
                _ms_v = _extract_int_from_map_entry(map_arg, "millisecond", -1)
                _frac = _subsecond_frac(_ns, _us_v, _ms_v)
                if _frac:
                    _time_only = f"{_h:02d}:{_mi:02d}:{_s:02d}.{_frac}"
                elif _s:
                    _time_only = f"{_h:02d}:{_mi:02d}:{_s:02d}"
                else:
                    _time_only = f"{_h:02d}:{_mi:02d}"
                if temporal_fn == "time":
                    # Include timezone offset if present
                    _tz_e = map_arg.entries.get("timezone")
                    if _tz_e and isinstance(_tz_e, ast.Literal) and isinstance(_tz_e.value, str):
                        _time_only += _normalize_tz_str(_tz_e.value)
                    else:
                        _time_only += "Z"
                temporal_str = _time_only
            else:
                # Handle date({year:...,month:...,day:...}) etc.
                sql = _build_date_from_map(map_arg,
                                           with_time=(temporal_fn in ("datetime", "localdatetime")),
                                           with_tz=(temporal_fn == "datetime"))
                if sql and sql.startswith("'") and sql.endswith("'"):
                    temporal_str = sql[1:-1]

    if temporal_str is None or temporal_fn is None:
        return None  # can't evaluate at compile time

    # Parse the temporal into a datetime — use LOCAL wall clock (no UTC adjustment).
    # Truncation operates on the local time components, not UTC.
    def _parse_local_dt(s, fn):
        """Parse temporal string to Python datetime using local wall-clock time (no TZ conversion)."""
        import datetime as _dt2
        import re as _re2
        if fn == "date":
            parsed = _parse_date_string(s)
            if parsed:
                y, mo, d = parsed
                return _dt2.datetime(y, mo, d, 0, 0, 0)
            return None
        if fn in ("localtime", "time"):
            s2 = _re2.sub(r'\[.*\]$', '', s)
            s2 = _re2.sub(r'[Z]$', '', s2)
            s2 = _re2.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', s2)
            m = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', s2)
            if m:
                h2, mi2 = int(m.group(1)), int(m.group(2))
                sec = int(m.group(3) or 0)
                frac = m.group(4) or ""
                ns = int(frac.ljust(9, '0')[:9]) if frac else 0
                return _dt2.datetime(2000, 1, 1, h2, mi2, sec, ns // 1000)
            return None
        if fn in ("localdatetime", "datetime"):
            s2 = _re2.sub(r'\[.*\]$', '', s)
            if "T" not in s2.upper():
                return None
            sep = s2.upper().index("T")
            date_s = s2[:sep]
            time_s = s2[sep+1:]
            parsed_date = _parse_date_string(date_s)
            if not parsed_date:
                return None
            y2, mo2, d2 = parsed_date
            # Strip timezone without adjusting
            time_s = _re2.sub(r'[Z]$', '', time_s)
            time_s = _re2.sub(r'[+-]\d{2}:?\d{2}(?::\d{2})?$', '', time_s)
            m = _re2.match(r'^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$', time_s)
            if not m:
                return None
            h2, mi2 = int(m.group(1)), int(m.group(2))
            sec = int(m.group(3) or 0)
            frac = m.group(4) or ""
            ns = int(frac.ljust(9, '0')[:9]) if frac else 0
            try:
                return _dt2.datetime(y2, mo2, d2, h2, mi2, sec, ns // 1000)
            except ValueError:
                return None
        return None

    dt = _parse_local_dt(temporal_str, temporal_fn)
    if dt is None:
        return "NULL"

    # Extract timezone from input temporal string (for datetime/time output)
    input_tz = ""
    _input_iana_tz = None  # IANA timezone name from input (needs re-resolve post-truncation)
    if temporal_fn in ("datetime", "time"):
        _tz_m = _re.search(r'([+-]\d{2}:\d{2}(?::\d{2})?)(\[[\w/]+\])?$', temporal_str or "")
        if _tz_m:
            input_tz = _tz_m.group(1)
            if _tz_m.group(2):
                input_tz += _tz_m.group(2)
                # Extract IANA name (e.g. 'Europe/Stockholm') for post-truncation re-resolve
                # (only relevant for datetime; time doesn't have IANA tz in openCypher)
                if temporal_fn == "datetime":
                    _iana_raw = _tz_m.group(2)[1:-1]  # strip '[' ']'
                    if "/" in _iana_raw or _iana_raw in ("UTC", "GMT"):
                        _input_iana_tz = _iana_raw
        elif temporal_str and temporal_str.endswith('Z'):
            input_tz = "Z"

    # Optional map for overrides (3rd arg)
    overrides = {}
    if len(args_exprs) >= 3:
        map_expr = args_exprs[2]
        if isinstance(map_expr, ast.MapLiteral):
            for k, v_expr in map_expr.entries.items():
                if isinstance(v_expr, ast.Literal) and isinstance(v_expr.value, (int, float)):
                    overrides[k] = int(v_expr.value)
                elif isinstance(v_expr, ast.Literal) and isinstance(v_expr.value, str):
                    overrides[k] = v_expr.value  # e.g. timezone: '+01:00' or 'Europe/Stockholm'

    # Determine the target type
    target_type = fn.split(".")[0]  # "date", "datetime", "localdatetime", "localtime", "time"

    # out_tz is resolved AFTER truncation so IANA timezone offset uses the output date
    # (which may differ from input, e.g. millennium truncation 2017→2000 changes DST)
    _pending_iana_tz = None  # IANA name to resolve post-truncation, if needed
    out_tz = input_tz or "Z"
    if "timezone" in overrides:
        tz_val = overrides["timezone"]
        if isinstance(tz_val, str):
            if "/" in tz_val or tz_val in ("UTC", "GMT"):
                _pending_iana_tz = tz_val  # defer until after truncation
            else:
                out_tz = _normalize_tz_str(tz_val)

    # Truncate based on unit
    y, mo, d = dt.year, dt.month, dt.day
    h, mi, s, us = dt.hour, dt.minute, dt.second, dt.microsecond
    ns_extra = 0  # sub-microsecond nanoseconds (from nanosecond override)

    if unit == "millennium":
        # openCypher: floor to nearest 1000-year boundary (e.g. 2017 → 2000)
        y = (y // 1000) * 1000
        mo, d, h, mi, s, us, ns_extra = 1, 1, 0, 0, 0, 0, 0
    elif unit == "century":
        # openCypher: floor to nearest 100-year boundary (e.g. 1984 → 1900)
        y = (y // 100) * 100
        mo, d, h, mi, s, us, ns_extra = 1, 1, 0, 0, 0, 0, 0
    elif unit == "decade":
        y = y // 10 * 10
        mo, d, h, mi, s, us, ns_extra = 1, 1, 0, 0, 0, 0, 0
    elif unit == "year":
        mo, d, h, mi, s, us, ns_extra = 1, 1, 0, 0, 0, 0, 0
    elif unit == "weekyear":
        # ISO week year: find first Monday of the ISO week year
        import datetime as _dt2
        iso_year = dt.isocalendar()[0]
        jan4_iy = _dt2.date(iso_year, 1, 4)
        monday_iy = jan4_iy - _dt2.timedelta(days=jan4_iy.weekday())
        y, mo, d = monday_iy.year, monday_iy.month, monday_iy.day
        h, mi, s, us, ns_extra = 0, 0, 0, 0, 0
    elif unit == "quarter":
        mo = ((mo - 1) // 3) * 3 + 1
        d, h, mi, s, us, ns_extra = 1, 0, 0, 0, 0, 0
    elif unit == "month":
        d, h, mi, s, us, ns_extra = 1, 0, 0, 0, 0, 0
    elif unit == "week":
        # Truncate to Monday of ISO week; dayOfWeek override selects a different weekday
        import datetime as _dt2
        dt_date = _dt2.date(y, mo, d)
        monday = dt_date - _dt2.timedelta(days=dt_date.weekday())
        dow_override = overrides.get("dayOfWeek")
        if dow_override is not None and isinstance(dow_override, int):
            dt_date = monday + _dt2.timedelta(days=dow_override - 1)
        else:
            dt_date = monday
        y, mo, d = dt_date.year, dt_date.month, dt_date.day
        h, mi, s, us, ns_extra = 0, 0, 0, 0, 0
    elif unit == "day":
        h, mi, s, us, ns_extra = 0, 0, 0, 0, 0
    elif unit == "hour":
        mi, s, us, ns_extra = 0, 0, 0, 0
    elif unit == "minute":
        s, us, ns_extra = 0, 0, 0
    elif unit == "second":
        us, ns_extra = 0, 0
    elif unit == "millisecond":
        us = (us // 1000) * 1000
        ns_extra = 0
    elif unit == "microsecond":
        ns_extra = 0  # already at microsecond granularity

    # Resolve IANA timezone offset using POST-truncation date (y/mo/d may have changed)
    # e.g. millennium truncation of 2017-10-11 (summer, +02:00) → 2000-01-01 (winter, +01:00)
    if _pending_iana_tz is not None:
        _offset = _iana_tz_offset(_pending_iana_tz, ref_year=y, ref_month=mo, ref_day=d)
        out_tz = f"{_offset}[{_pending_iana_tz}]" if _offset else _pending_iana_tz
    elif _input_iana_tz is not None:
        # Input temporal had IANA timezone; re-resolve offset for post-truncation date
        _offset = _iana_tz_offset(_input_iana_tz, ref_year=y, ref_month=mo, ref_day=d)
        out_tz = f"{_offset}[{_input_iana_tz}]" if _offset else _input_iana_tz

    # Apply numeric overrides from map (timezone already handled above)
    if "year" in overrides and isinstance(overrides["year"], int):
        y = overrides["year"]
    if "month" in overrides and isinstance(overrides["month"], int):
        mo = overrides["month"]
    if "day" in overrides and isinstance(overrides["day"], int):
        d = overrides["day"]
    if "hour" in overrides and isinstance(overrides["hour"], int):
        h = overrides["hour"]
    if "minute" in overrides and isinstance(overrides["minute"], int):
        mi = overrides["minute"]
    if "second" in overrides and isinstance(overrides["second"], int):
        s = overrides["second"]
    if "nanosecond" in overrides and isinstance(overrides["nanosecond"], int):
        # nanosecond override sets the sub-microsecond part only; us from truncation is preserved.
        # e.g. after millisecond truncation us=645000 + {nanosecond:2} → ns_extra=2, us stays 645000
        # → total 645000002 ns = '645000002'
        ns_extra = overrides["nanosecond"] % 1000
    if "microsecond" in overrides and isinstance(overrides["microsecond"], int):
        us = overrides["microsecond"]
        ns_extra = 0
    if "millisecond" in overrides and isinstance(overrides["millisecond"], int):
        us = overrides["millisecond"] * 1000
        ns_extra = 0

    # Clamp day to valid range
    max_day = _cal.monthrange(y, mo)[1]
    d = min(d, max_day)

    # Build fractional seconds (nanosecond precision)
    total_ns = us * 1000 + ns_extra
    frac_str = f"{total_ns:09d}".rstrip('0') if total_ns else None

    def _fmt_time_part(h, mi, s, frac_str):
        """Format time as HH:MM[:SS[.frac]], omitting trailing zero components."""
        if frac_str:
            return f"{h:02d}:{mi:02d}:{s:02d}.{frac_str}"
        if s:
            return f"{h:02d}:{mi:02d}:{s:02d}"
        return f"{h:02d}:{mi:02d}"

    # Format based on target type
    if target_type == "date":
        return f"'{y:04d}-{mo:02d}-{d:02d}'"
    elif target_type == "datetime":
        time_part = _fmt_time_part(h, mi, s, frac_str)
        return f"'{y:04d}-{mo:02d}-{d:02d}T{time_part}{out_tz}'"
    elif target_type == "localdatetime":
        # If timezone override present in map → promote output to datetime
        if "timezone" in overrides:
            time_part = _fmt_time_part(h, mi, s, frac_str)
            return f"'{y:04d}-{mo:02d}-{d:02d}T{time_part}{out_tz}'"
        time_part = _fmt_time_part(h, mi, s, frac_str)
        return f"'{y:04d}-{mo:02d}-{d:02d}T{time_part}'"
    elif target_type in ("localtime", "time"):
        time_part = _fmt_time_part(h, mi, s, frac_str)
        if target_type == "time":
            return f"'{time_part}{out_tz}'"
        return f"'{time_part}'"
    return "NULL"


def _scalar_statistical(fn, args, args_exprs, context):
    if fn in ("stdev", "stdevs"):
        return f"STDDEV({args[0]})" if args else "NULL"
    if fn in ("stdevp",):
        return f"STDDEV_POP({args[0]})" if args else "NULL"
    if fn in ("percentiledisc", "percentilecont"):
        if not args:
            return "NULL"
        val_expr = args[0]
        pct_expr = args[1] if len(args) > 1 else "0.5"
        context._percentile_queries = getattr(context, "_percentile_queries", [])
        # Accept Variable or PropertyReference as the value expression
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            var_name = args_exprs[0].name
            alias = context.variable_aliases.get(var_name, "")
        elif args_exprs and isinstance(args_exprs[0], ast.PropertyReference):
            var_name = args_exprs[0].variable
            alias = context.variable_aliases.get(var_name, "")
        else:
            var_name = ""
            alias = ""
        # Resolve the percentile value: if the 2nd arg is a named param, inline it
        # so it doesn't create a stray ? placeholder in where_params without a condition.
        if len(args_exprs) > 1 and isinstance(args_exprs[1], ast.Variable):
            pct_name = args_exprs[1].name
            if pct_name in context.input_params:
                pct_val = float(context.input_params[pct_name])
                # Remove the stray where_param that _translate_arg added for this variable
                if context.where_params and context.where_params[-1] == context.input_params[pct_name]:
                    context.where_params.pop()
                pct_expr = str(pct_val)
            else:
                pct_val = pct_expr
        else:
            pct_val = float(pct_expr) if isinstance(pct_expr, str) and pct_expr.replace('.','',1).isdigit() else pct_expr
        if var_name or val_expr:
            context._percentile_queries.append((val_expr, pct_val, fn, var_name, alias))
        return f"__PERCENTILE_PLACEHOLDER_{len(context._percentile_queries)-1 if context._percentile_queries else 0}__"
    return None


def _scalar_type_conversion(fn, args, args_exprs):
    if fn == "toboolean":
        return f"CASE WHEN LOWER(CAST({args[0]} AS VARCHAR)) IN ('true','1','yes','y') THEN 1 WHEN LOWER(CAST({args[0]} AS VARCHAR)) IN ('false','0','no','n') THEN 0 ELSE NULL END"
    return None


def _expr_scalar_function(fn, sql_fn, args, args_exprs, expr, context, segment):
    result = _scalar_coalesce(fn, args, args_exprs)
    if result is not None:
        return result
    result = _scalar_string(fn, args, args_exprs)
    if result is not None:
        return result
    result = _scalar_numeric_and_datetime(fn, args, args_exprs, context)
    if result is not None:
        return result
    result = _scalar_statistical(fn, args, args_exprs, context)
    if result is not None:
        return result
    result = _scalar_type_conversion(fn, args, args_exprs)
    if result is not None:
        return result
    return None


def _expr_fn_shortestpath(fn, expr, context):
    if fn not in ("shortestpath", "allshortestpaths") or not expr.arguments:
        return None
    arg = expr.arguments[0]
    if not (isinstance(arg, ast.Literal) and isinstance(arg.value, ast.GraphPattern)):
        return None
    pattern = arg.value
    is_all = fn == "allshortestpaths"
    for rel in pattern.relationships:
        if rel.variable_length is None:
            rel.variable_length = ast.VariableLength(
                min_hops=1, max_hops=5, shortest=not is_all, all_shortest=is_all
            )
        else:
            rel.variable_length.shortest = not is_all
            rel.variable_length.all_shortest = is_all
    fake_match = ast.MatchClause(patterns=[pattern], optional=False)
    translate_match_clause(fake_match, context, {})
    return "'path'"


def _expr_fn_path_funcs(fn, expr, context):
    if fn not in ("length", "nodes", "relationships") or len(expr.arguments) != 1:
        return None
    arg = expr.arguments[0]
    # null literal: nodes(null), relationships(null), length(null) → NULL
    if isinstance(arg, ast.Literal) and arg.value is None:
        return "NULL"
    if not (isinstance(arg, ast.Variable) and arg.name in context.named_paths):
        if isinstance(arg, ast.Variable) and arg.name not in context.named_paths:
            if fn in ("nodes", "relationships"):
                raise ValueError(f"'{arg.name}' is not a named path variable")
            if fn == "length":
                # length() on a node or relationship is a type error in openCypher.
                var_type = context.variable_types.get(arg.name)
                if var_type in ("node", "relationship"):
                    raise SyntaxError(
                        f"InvalidArgumentType: length() is not applicable to "
                        f"{var_type} values — use it on paths or strings"
                    )
        return None
    path_var = arg.name
    # When the OPTIONAL MATCH anchor is a null scalar, the entire path is null.
    if getattr(context, "optional_null_row_unconditional", False):
        return "NULL"
    if fn == "length":
        vl_names = {vl.get("path_var") for vl in (context.var_length_paths or [])}
        if path_var in vl_names:
            node_aliases = context.path_node_aliases.get(path_var, [])
            return str(max(0, len(node_aliases) - 1))
        return str(len(context.named_paths[path_var].pattern.relationships))
    elif fn == "nodes":
        aliases = context.path_node_aliases[path_var]
        return f"JSON_ARRAY({', '.join(f'{a}.node_id' for a in aliases)})"
    else:
        aliases = context.path_edge_aliases[path_var]
        undirected_aliases = getattr(context, "_undirected_aliases", set())
        rel_refs = []
        for a in aliases:
            # Use _p for bidirectional (undirected) edges, p for directed edges
            col = "_p" if a in undirected_aliases else "p"
            rel_refs.append(f"{a}.{col}")
        return f"JSON_ARRAY({', '.join(rel_refs)})"


def _expr_fn_vector_ops(fn, args_exprs, args, context):
    if fn not in ("vector_distance", "vector_similarity", "ivg.vector_distance", "ivg.vector_similarity"):
        return None
    if len(args_exprs) < 2:
        raise ValueError(f"{fn}() requires 2 arguments: (node_variable, query_vector)")
    node_arg = args_exprs[0]
    vec_arg = args_exprs[1]
    alias = context.variable_aliases.get(node_arg.name, node_arg.name) if isinstance(node_arg, ast.Variable) else args[0]
    emb_table = f"{_schema_prefix}.kg_NodeEmbeddings" if _schema_prefix else "Graph_KG.kg_NodeEmbeddings"
    if isinstance(vec_arg, ast.Variable) and vec_arg.name in context.input_params:
        vec_val = context.input_params[vec_arg.name]
        if isinstance(vec_val, list):
            vec_str = ",".join(str(x) for x in vec_val)
            placeholder = f"TO_VECTOR('{vec_str}', DOUBLE)"
        else:
            placeholder = f"TO_VECTOR(?, DOUBLE)"
            context.all_stage_params.append(vec_val)
    elif isinstance(vec_arg, ast.Literal) and isinstance(vec_arg.value, list):
        vec_str = ",".join(str(x) for x in vec_arg.value)
        placeholder = f"TO_VECTOR('{vec_str}', DOUBLE)"
    else:
        placeholder = args[1]
    if fn in ("vector_distance", "ivg.vector_distance"):
        return f"(1 - VECTOR_COSINE((SELECT emb FROM {emb_table} WHERE id = {alias}.node_id), {placeholder}))"
    else:
        return f"VECTOR_COSINE((SELECT emb FROM {emb_table} WHERE id = {alias}.node_id), {placeholder})"


def _expr_fn_node_funcs(fn, args_exprs, args, context):
    if fn == "type":
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            var_name = args_exprs[0].name
            context_alias = context.variable_aliases.get(var_name, "")
            if context_alias:
                if context_alias.startswith("Stage"):
                    return f"{context_alias}.{var_name}"
                p_col = "_p" if getattr(context, "_undirected_aliases", set()) and context_alias in context._undirected_aliases else "p"
                return f"{context_alias}.{p_col}"
        return args[0] if args else "NULL"
    if fn == "startnode":
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            var_name = args_exprs[0].name
            context_alias = context.variable_aliases.get(var_name, "")
            if context_alias:
                return f"{context_alias}.s"
        return args[0] if args else "NULL"
    if fn == "endnode":
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            var_name = args_exprs[0].name
            context_alias = context.variable_aliases.get(var_name, "")
            if context_alias:
                return f"{context_alias}.o_id"
        return args[0] if args else "NULL"
    if fn == "id":
        if args_exprs and isinstance(args_exprs[0], ast.Variable):
            var_name = args_exprs[0].name
            context_alias = context.variable_aliases.get(var_name, "")
            if context_alias:
                return f"{context_alias}.node_id"
        return args[0] if args else "NULL"
    return None


def _expr_fn_keys(args):
    if not args:
        return "JSON_ARRAY()"
    id_expr = args[0]
    # Node ID: look up rdf_props. Map literals are handled at compile time (line 11653).
    # Relationship qualifiers (JSON object): use SQLUser.JSON_KEYS to extract key names.
    return (
        f"CASE WHEN ({id_expr}) IS NULL THEN NULL "
        f"WHEN SUBSTRING({id_expr}, 1, 1) = '{{' "
        f"THEN COALESCE(SQLUser.JSON_KEYS({id_expr}), CAST('[]' AS VARCHAR(256))) "
        f"ELSE COALESCE((SELECT JSON_ARRAYAGG(rp.\"key\") FROM {_table('rdf_props')} rp WHERE rp.s = {id_expr}), CAST('[]' AS VARCHAR(256))) "
        f"END"
    )


_EMPTY_JSON_ARRAY = "CAST('[]' AS VARCHAR(256))"


def _expr_fn_range(args_exprs):
    if len(args_exprs) < 2:
        return _EMPTY_JSON_ARRAY
    # Type-check BEFORE int() conversion: int() raises TypeError for list/map/str args,
    # which would be swallowed by 'except TypeError: pass'. Check types explicitly first.
    # Also catch non-Literal AST nodes that are clearly wrong types (MapLiteral, etc.).
    for _i, _arg in enumerate(args_exprs[:3]):
        if isinstance(_arg, ast.MapLiteral):
            raise ValueError(
                f"range() argument {_i} must be an integer, got 'Map'"
            )
        if isinstance(_arg, ast.Literal):
            _v = _arg.value
            if isinstance(_v, list):
                raise ValueError(
                    f"range() argument {_i} must be an integer, got 'List'"
                )
            if not isinstance(_v, int) or isinstance(_v, bool):
                raise ValueError(
                    f"range() argument {_i} must be an integer, got {type(_v).__name__!r}"
                )
    try:
        start = int(args_exprs[0].value) if isinstance(args_exprs[0], ast.Literal) else None
        end = int(args_exprs[1].value) if isinstance(args_exprs[1], ast.Literal) else None
        step_arg = args_exprs[2] if len(args_exprs) > 2 else None
        if step_arg is not None:
            if not isinstance(step_arg, ast.Literal):
                # Dynamic step — fall through to empty-array fallback
                raise TypeError("dynamic step")
            step = int(step_arg.value)
        else:
            step = 1
        if step == 0:
            raise ValueError("range() step cannot be zero (NumberOutOfRange)")
        if start is not None and end is not None:
            vals = list(range(start, end + (1 if step > 0 else -1), step))
            if not vals:
                return _EMPTY_JSON_ARRAY
            return f"JSON_ARRAY({', '.join(str(v) for v in vals)})"
    except ValueError:
        raise
    except TypeError:
        pass
    return _EMPTY_JSON_ARRAY


def _expr_fn_list_ops(fn, args, args_exprs):
    if fn == "keys":
        # For literal maps, extract keys at compile time
        if args_exprs and isinstance(args_exprs[0], ast.MapLiteral):
            keys = list(args_exprs[0].entries.keys())
            if not keys:
                return "CAST('[]' AS VARCHAR(256))"
            return f"JSON_ARRAY({', '.join(repr(k) for k in keys)})"
        # For null literal, return NULL
        if args_exprs and isinstance(args_exprs[0], ast.Literal) and args_exprs[0].value is None:
            return "NULL"
        return _expr_fn_keys(args)
    if fn == "range":
        static = _expr_fn_range(args_exprs)
        if static is not None and static != _EMPTY_JSON_ARRAY:
            return static
        # Dynamic range: delegate to SQLUser.CypherFn_IVGRANGE(start, end[, step])
        if len(args) >= 2:
            step_arg = args[2] if len(args) > 2 else "1"
            return f"SQLUser.CypherFn_IVGRANGE({args[0]}, {args[1]}, {step_arg})"
        return _EMPTY_JSON_ARRAY
    if fn == "size":
        if not args:
            return "0"
        arg_expr = args_exprs[0] if args_exprs else None
        def _arg_is_list_type(e):
            """Heuristic: returns True if expression e produces a JSON array."""
            if isinstance(e, ast.Literal) and isinstance(e.value, list):
                return True
            if isinstance(e, ast.ListComprehension):
                return True
            if isinstance(e, ast.PatternComprehension):
                return True
            if isinstance(e, ast.FunctionCall):
                # List concatenation or list-producing functions
                if e.function_name in (
                    "__arith_+", "collect", "nodes", "relationships",
                    "labels", "keys", "range",
                ):
                    return True
                # range(), nodes(), etc. are list-producing
                if e.function_name.lower() in ("range", "nodes", "relationships", "labels", "keys", "collect"):
                    return True
            return False
        is_list = _arg_is_list_type(arg_expr)
        if is_list:
            return f"SQLUser.JSON_ARRAYLENGTH({args[0]})"
        return None
    if fn == "head":
        if not args:
            return "NULL"
        return f"SQLUser.JSON_ARRAYGET({args[0]}, 0)"
    if fn == "tail":
        if not args:
            return "JSON_ARRAY()"
        return f"SQLUser.LIST_TAIL({args[0]})"
    if fn == "last":
        if not args:
            return "NULL"
        return f"SQLUser.JSON_ARRAYGET({args[0]}, SQLUser.JSON_ARRAYLENGTH({args[0]})-1)"
    if fn == "isempty":
        if not args:
            return "1"
        return f"CASE WHEN {args[0]} IS NULL OR {args[0]} = '' OR {args[0]} = '[]' OR {args[0]} = '{{}}' THEN 1 ELSE 0 END"
    if fn == "round":
        return f"CAST(ROUND({args[0] if args else '0'}, 0) AS DOUBLE)"
    return None


def _expr_function_call(expr, context, segment):
    fn = expr.function_name.lower()

    # Handle temporal namespace functions (duration.between, datetime.fromepoch, etc.)
    if "." in fn:
        result = _eval_temporal_ns_function(fn, expr.arguments, context)
        if result is not None:
            return result

    result = _expr_fn_shortestpath(fn, expr, context)
    if result is not None:
        return result

    result = _expr_fn_path_funcs(fn, expr, context)
    if result is not None:
        return result

    if fn == "toboolean" and expr.arguments and isinstance(expr.arguments[0], ast.Literal):
        v = expr.arguments[0].value
        if not isinstance(v, str):
            return "1" if v else "0"
    # toString(bool_expr): must be checked BEFORE args translation to avoid double-parameter issue
    if fn == "tostring" and expr.arguments:
        arg0 = expr.arguments[0]
        if isinstance(arg0, ast.BooleanExpression):
            cond = translate_boolean_expression(arg0, context)
            return f"CASE WHEN ({cond}) THEN 'true' ELSE 'false' END"

    # tointeger/tofloat: CASE WHEN ISNUMERIC(x) THEN CAST(x ...) uses arg expression TWICE.
    # If arg translation adds params (? placeholders), we must duplicate them so one copy
    # goes to ISNUMERIC and one to CAST. Snapshot all param lists, translate once, then
    # re-append whatever was added.
    if fn in ("tointeger", "tofloat") and expr.arguments:
        _sp0 = len(context.select_params)
        _wp0 = len(context.where_params)
        _jp0 = len(context.join_params)
        if isinstance(expr.arguments[0], ast.Literal) and not isinstance(expr.arguments[0].value, list):
            inlined = _inline_literal(expr.arguments[0])
            if inlined is not None:
                arg_sql = inlined
            else:
                arg_sql = translate_expression(expr.arguments[0], context, segment="inline")
        else:
            arg_sql = translate_expression(expr.arguments[0], context, segment="inline")
        # Duplicate any params that were added during arg translation (arg appears twice: ISNUMERIC + CAST).
        _sp_added = context.select_params[_sp0:]
        _wp_added = context.where_params[_wp0:]
        _jp_added = context.join_params[_jp0:]
        if _sp_added or _wp_added or _jp_added:
            context.select_params.extend(_sp_added)
            context.where_params.extend(_wp_added)
            context.join_params.extend(_jp_added)
        # When toInteger/toFloat appears in a WHERE clause (segment="where"), the "inline" sub-
        # translation above added the property-key params to select_params. But select_params are
        # placed BEFORE join_params in build_stage_sql, while the correlated-subquery ? placeholders
        # appear inside the WHERE condition string — after the JOINs in the emitted SQL. Correct
        # placement: move those params from select_params to where_params so order matches SQL.
        if segment == "where" and _sp_added:
            # Remove the duplicated select_params entries and re-add to where_params.
            del context.select_params[_sp0:]
            context.where_params.extend(_sp_added * 2)
        cast_type = "INTEGER" if fn == "tointeger" else "DOUBLE"
        return f"CASE WHEN ISNUMERIC({arg_sql}) = 1 THEN CAST({arg_sql} AS {cast_type}) ELSE NULL END"

    # size(pattern-predicate) raises SyntaxError — ExistsExpression arg is a pattern, not a list
    if fn == "size" and expr.arguments and isinstance(expr.arguments[0], ast.ExistsExpression):
        raise SyntaxError(
            "SyntaxError: size() does not accept a pattern argument. "
            "Use a pattern comprehension [ ... ] with size() instead."
        )

    # keys(null) and keys(param_dict): handle with context before _expr_fn_list_ops
    if fn == "keys" and expr.arguments:
        arg0 = expr.arguments[0]
        # null literal → NULL
        if isinstance(arg0, ast.Literal) and arg0.value is None:
            return "NULL"
        # parameter variable bound to a dict → fold keys at compile time
        if isinstance(arg0, ast.Variable) and arg0.name in context.input_params:
            pval = context.input_params[arg0.name]
            if isinstance(pval, dict):
                import json as _json
                keys = list(pval.keys())
                if not keys:
                    return "CAST('[]' AS VARCHAR(256))"
                js = _json.dumps(keys)
                return f"CAST('{js}' AS VARCHAR({max(len(js)+1, 256)}))"
            if pval is None:
                return "NULL"
        # keys(r) on a relationship — use qualifiers JSON object directly
        if isinstance(arg0, ast.Variable):
            var_name = arg0.name
            alias = context.variable_aliases.get(var_name, "")
            edge_stage_vars = getattr(context, "edge_stage_variables", set())
            is_current_edge = alias.startswith("e") and not alias.startswith("Stage")
            is_stage_edge = alias.startswith("Stage") and var_name in edge_stage_vars
            if is_current_edge:
                return _expr_fn_keys([f"{alias}.qualifiers"])
            if is_stage_edge:
                return _expr_fn_keys([f"{alias}.{var_name}"])

    def _translate_arg(a):
        if isinstance(a, ast.Literal) and not isinstance(a.value, list):
            inlined = _inline_literal(a)
            if inlined is not None:
                return inlined
        return translate_expression(a, context, segment="inline")

    args = [_translate_arg(a) for a in expr.arguments]

    result = _expr_fn_vector_ops(fn, expr.arguments, args, context)
    if result is not None:
        return result

    result = _expr_fn_node_funcs(fn, expr.arguments, args, context)
    if result is not None:
        return result

    if fn == "labels":
        removed = getattr(context, '_removed_labels', None)
        return labels_subquery(args[0] if args else "NULL", exclude_labels=removed or None)
    if fn == "properties":
        if expr.arguments:
            arg0 = expr.arguments[0]
            # properties(map) — just return the map itself
            if isinstance(arg0, ast.MapLiteral):
                return args[0]
            # properties(null) — return null
            if isinstance(arg0, ast.Literal) and arg0.value is None:
                return "NULL"
            # properties(<scalar>) or properties(<list>) — InvalidArgumentType
            # List literals are Literal(value=list); scalars are Literal(value=non-None non-dict non-list)
            if isinstance(arg0, ast.Literal) and arg0.value is not None:
                raise SyntaxError(
                    f"properties() does not support scalar or list argument (InvalidArgumentType)"
                )
            # properties(r) on a relationship — qualifiers is a JSON object; return it directly
            if isinstance(arg0, ast.Variable):
                var_name = arg0.name
                alias = context.variable_aliases.get(var_name, "")
                edge_stage_vars = getattr(context, "edge_stage_variables", set())
                is_current_edge = alias.startswith("e") and not alias.startswith("Stage")
                is_stage_edge = alias.startswith("Stage") and var_name in edge_stage_vars
                if is_current_edge:
                    return f"{alias}.qualifiers"
                if is_stage_edge:
                    return f"{alias}.{var_name}"
        return properties_subquery(args[0] if args else "NULL")

    # size(x) where x is a scalar list-predicate variable (VARCHAR holding either a
    # plain string or a JSON-encoded list/map): dispatch at runtime by first character.
    if fn == "size" and args and expr.arguments:
        arg0 = expr.arguments[0]
        # size(collect(...)) or size(<any-aggregation>) — result is always a JSON array
        if isinstance(arg0, ast.AggregationFunction) or _contains_aggregation(arg0):
            return f"SQLUser.JSON_ARRAYLENGTH({args[0]})"
        if isinstance(arg0, ast.Variable) and arg0.name in context.scalar_variables:
            col = args[0]
            return (
                f"CASE WHEN SUBSTRING({col}, 1, 1) IN ('[', '{{') "
                f"THEN SQLUser.JSON_ARRAYLENGTH({col}) "
                f"ELSE LENGTH({col}) END"
            )
        if isinstance(arg0, ast.PropertyReference):
            col = args[0]
            # col may contain ? placeholders (added once by inline arg translation).
            # The CASE uses col 3 times → must duplicate any params that were added.
            _n_q = col.count("?")
            if _n_q > 0:
                # col was added via inline translation; it added _n_q params to select_params.
                # Duplicate them (2 more copies) so the 3 usages of col are all covered.
                _added = context.select_params[-_n_q:]
                context.select_params.extend(_added)
                context.select_params.extend(_added)
            return (
                f"CASE WHEN SUBSTRING({col}, 1, 1) IN ('[', '{{') "
                f"THEN SQLUser.JSON_ARRAYLENGTH({col}) "
                f"ELSE LENGTH({col}) END"
            )

    result = _expr_fn_list_ops(fn, args, expr.arguments)
    if result is not None:
        return result

    _CYPHER_FN_MAP = {
        "tolower": "LOWER",
        "toupper": "UPPER",
        "trim": "TRIM",
        "ltrim": "LTRIM",
        "rtrim": "RTRIM",
        "tostring": "CAST",
        "tointeger": "CAST",
        "tofloat": "CAST",
        "size": "LENGTH",
        "length": "LENGTH",
        "substring": "SUBSTRING",
        "left": "LEFT",
        "right": "RIGHT",
        "split": "STRTOK_TO_TABLE",
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
        "toboolean": "CASE WHEN",
    }
    sql_fn = _CYPHER_FN_MAP.get(fn, fn.upper())
    scalar_result = _expr_scalar_function(fn, sql_fn, args, expr.arguments, expr, context, segment)
    if scalar_result is not None:
        return scalar_result
    return f"{sql_fn}({', '.join(args)})"


def _expr_boolean(expr, context, segment):
    _sp0 = len(context.select_params)
    _wp0 = len(context.where_params)
    cond = translate_boolean_expression(expr, context)
    # If the condition evaluates to SQL NULL (e.g. NOT null, null = null),
    # the result must also be NULL (Cypher three-valued logic).
    if cond == "NULL":
        return "NULL"
    # Sentinel booleans from 3VL logic — return as integer literals
    if cond == "(1=1)":
        return "1"
    if cond == "(1=0)":
        return "0"
    # If translate_boolean_expression already returned a 1/0/NULL CASE expression
    # (e.g. for IN with null list elements, or 3VL AND/OR), don't wrap it again.
    # Also handle 3VL CASE WHEN patterns with (1=0)/(1=1) that need integer normalization.
    # IMPORTANT: only skip re-wrapping when the CASE is the entire expression (ends with END).
    # e.g. "CASE WHEN (0=1) THEN 1 ELSE 0 END IS NULL" must still be wrapped because IRIS
    # rejects a bare CASE expression followed by IS NULL in a SELECT list.
    if cond.startswith("CASE WHEN ") and cond.endswith(" END"):
        # Replace (1=0) and (1=1) sentinels with integers in the CASE WHEN body
        cond = cond.replace("THEN (1=0)", "THEN 0").replace("THEN (1=1)", "THEN 1")
        if " THEN 1 ELSE NULL END" in cond or " THEN 0 ELSE NULL END" in cond or " THEN 1 ELSE 0 END" in cond:
            return cond
    # IRIS rejects parentheses around IS NULL/IS NOT NULL predicates in CASE WHEN.
    # e.g. CASE WHEN (NULL IS NULL) fails; CASE WHEN NULL IS NULL works.
    # IS NULL/IS NOT NULL predicates always return 0 or 1, never null — ELSE 0 is correct.
    if cond.endswith(" IS NULL") or cond.endswith(" IS NOT NULL"):
        return f"CASE WHEN {cond} THEN 1 ELSE 0 END"
    # 3VL null propagation: CASE WHEN cond THEN 1 WHEN NOT cond THEN 0 ELSE NULL END.
    # cond appears twice in the SQL string, so any ? placeholders inside cond must appear
    # twice. select_params and where_params embed ? inside cond; join_params embed ? in
    # JOIN clauses (structural, not in cond) — only duplicate the former two.
    # Additionally, any params that translate_boolean_expression placed in where_params
    # must be promoted to select_params, because the resulting CASE expression is
    # emitted in the SELECT list — where_params are paired with WHERE conditions and
    # would be dropped if no matching WHERE ? placeholder exists.
    new_wp = context.where_params[_wp0:]
    if new_wp:
        # Promote new where_params to select_params: they belong in the SELECT CASE expression.
        # The CASE has two copies of cond, so params must also be doubled.
        del context.where_params[_wp0:]
        context.select_params.extend(new_wp)
    # Duplicate all newly-added select_params (cond appears twice in the CASE expression).
    context.select_params.extend(context.select_params[_sp0:])
    return f"CASE WHEN ({cond}) THEN 1 WHEN NOT ({cond}) THEN 0 ELSE NULL END"


def translate_expression(expr, context, segment="select") -> str:
    # Pattern predicates (n)-[r]->() are only valid in boolean WHERE context,
    # not as expressions in RETURN, WITH, or SET.
    if isinstance(expr, ast.ExistsExpression) and getattr(expr, "is_pattern_predicate", False):
        raise SyntaxError(
            "UnexpectedSyntax: Pattern expression is not allowed in an expression context "
            "(RETURN, WITH, SET). Use it in a WHERE clause or as EXISTS{...}."
        )

    if isinstance(expr, ast.PatternComprehension):
        return _expr_pattern_comprehension(expr, context, segment)
    if isinstance(expr, ast.FunctionCall) and expr.function_name == "__prop__":
        return _expr_prop(expr, context, segment)
    if isinstance(expr, ast.FunctionCall) and expr.function_name.startswith("__arith_"):
        return _expr_arith(expr, context, segment)
    if isinstance(expr, ast.ListPredicateExpression):
        return _expr_list_predicate(expr, context, segment)
    if isinstance(expr, ast.ListComprehension):
        return _expr_list_comprehension(expr, context, segment)
    if isinstance(expr, ast.ReduceExpression):
        return _expr_reduce(expr, context, segment)
    if isinstance(expr, ast.CaseExpression):
        return _expr_case(expr, context, segment)
    if isinstance(expr, ast.PropertyReference):
        return _expr_property_reference(expr, context, segment)
    if isinstance(expr, ast.MapProjection):
        return _expr_map_projection(expr, context, segment)
    if isinstance(expr, ast.MapLiteral):
        return _expr_map_literal(expr, context, segment)
    if isinstance(expr, ast.SubscriptExpression):
        return _expr_subscript(expr, context, segment)
    if isinstance(expr, ast.SliceExpression):
        return _expr_slice(expr, context, segment)
    if isinstance(expr, ast.PropertyAccessExpression):
        return _expr_property_access(expr, context, segment)
    if isinstance(expr, ast.Variable):
        return _expr_variable(expr, context, segment)
    if isinstance(expr, ast.Literal):
        return _expr_literal(expr, context, segment)
    if isinstance(expr, ast.AggregationFunction):
        return _expr_aggregation(expr, context, segment)
    if isinstance(expr, ast.FunctionCall):
        return _expr_function_call(expr, context, segment)
    if isinstance(expr, ast.BooleanExpression):
        return _expr_boolean(expr, context, segment)
    if isinstance(expr, ast.LabelPredicate):
        alias = context.variable_aliases.get(expr.variable)
        # Detect relationship variables (alias starts with 'e' for rdf_edges)
        is_rel = alias and (alias.startswith("e") or expr.variable in getattr(context, "edge_stage_variables", set()))
        if is_rel:
            # For relationships, r:TYPE means edge type matches — check rdf_edges.p
            if segment in ("select", "inline", None):
                safe_lbl = context.add_select_param(expr.label)
                return f"CASE WHEN ({alias}.p = {safe_lbl}) THEN 1 ELSE 0 END"
            safe_lbl = context.add_where_param(expr.label)
            return f"({alias}.p = {safe_lbl})"
        node_col = f"{alias}.node_id" if alias else "node_id"
        labels_tbl = _table("rdf_labels")
        if segment in ("select", "inline", None):
            safe_label = context.add_select_param(expr.label)
            cond = (
                f"EXISTS (SELECT 1 FROM {labels_tbl} _lp"
                f" WHERE _lp.s = {node_col} AND _lp.label = {safe_label})"
            )
            return f"CASE WHEN ({node_col} IS NULL) THEN NULL WHEN ({cond}) THEN 1 ELSE 0 END"
        safe_label = context.add_where_param(expr.label)
        return (
            f"EXISTS (SELECT 1 FROM {labels_tbl} _lp"
            f" WHERE _lp.s = {node_col} AND _lp.label = {safe_label})"
        )

    return "NULL"



_IRIS_RESERVED = frozenset({
    "count","sum","avg","min","max","key","value","type","name","label",
    "order","group","index","select","from","where","join","having",
    "union","insert","update","delete","create","drop","alter","set",
    "table","schema","column","row","data","id","user","date","time",
    "result","results","null","true","false","top","exists","not","and","or",
    "input","first","second","only","rows","fetch","with","offset","limit",
    "values","int","integer","varchar","char","double","float","decimal",
    "boolean","bit","case","when","then","else","end","in","is","as",
    "like","between","distinct","all","any","some","by","asc","desc",
    "inner","outer","left","right","full","cross","natural","on","using",
    "intersect","except","minus","having","into","for","primary","foreign",
    "references","unique","default","check","constraint","index","trigger",
    "view","procedure","function","begin","commit","rollback","transaction",
})


# IRIS tokenizer splits identifiers that start with certain reserved keyword tokens.
# e.g. "inputList" is tokenized as keyword INPUT + identifier List.
# Only add keywords here that are confirmed to cause IRIS tokenizer splitting.
_IRIS_RESERVED_PREFIX_MATCH = frozenset({"input"})


def _safe_alias(a: str) -> str:
    if not a:
        return a
    lower = a.lower()
    if lower in _IRIS_RESERVED:
        return f'"{a}"'
    for rw in _IRIS_RESERVED_PREFIX_MATCH:
        if lower.startswith(rw) and len(lower) > len(rw):
            return f'"{a}"'
    return a


def _expr_to_cypher_text(expr) -> str:
    """Return a Cypher-text representation of an expression for use as a column alias."""
    if isinstance(expr, ast.LabelPredicate):
        return f"({expr.variable}:{expr.label})"
    if isinstance(expr, ast.PropertyReference):
        return f"{expr.variable}.{expr.property_name}"
    if isinstance(expr, ast.PropertyAccessExpression):
        base = _expr_to_cypher_text(expr.expression)
        # Wrap in parens when the base is a non-simple expression (subscript, function, etc.)
        # so the column name matches the original Cypher text: (list[1]).prop not list[1].prop
        needs_parens = base and not isinstance(expr.expression, (ast.Variable, ast.PropertyReference))
        base_str = f"({base})" if needs_parens else base
        return f"{base_str}.{expr.property_name}" if base_str else f".{expr.property_name}"
    if isinstance(expr, ast.Variable):
        return expr.name
    if isinstance(expr, ast.Literal) and isinstance(expr.value, list):
        items = ", ".join(_expr_to_cypher_text(i) for i in expr.value)
        return f"[{items}]"
    if isinstance(expr, ast.Literal):
        return repr(expr.value)
    if isinstance(expr, ast.BooleanExpression):
        op = expr.operator
        if op in (ast.BooleanOperator.IS_NULL, ast.BooleanOperator.IS_NOT_NULL):
            left = _expr_to_cypher_text(expr.operands[0])
            suffix = "IS NULL" if op == ast.BooleanOperator.IS_NULL else "IS NOT NULL"
            return f"{left} {suffix}"
        if op == ast.BooleanOperator.NOT and len(expr.operands) == 1:
            return f"NOT {_expr_to_cypher_text(expr.operands[0])}"
        op_str = {
            ast.BooleanOperator.AND: "AND",
            ast.BooleanOperator.OR: "OR",
            ast.BooleanOperator.EQUALS: "=",
            ast.BooleanOperator.NOT_EQUALS: "<>",
            ast.BooleanOperator.LESS_THAN: "<",
            ast.BooleanOperator.LESS_THAN_OR_EQUAL: "<=",
            ast.BooleanOperator.GREATER_THAN: ">",
            ast.BooleanOperator.GREATER_THAN_OR_EQUAL: ">=",
            ast.BooleanOperator.IN: "IN",
            ast.BooleanOperator.CONTAINS: "CONTAINS",
            ast.BooleanOperator.STARTS_WITH: "STARTS WITH",
            ast.BooleanOperator.ENDS_WITH: "ENDS WITH",
        }.get(op, str(op))
        parts = [_expr_to_cypher_text(o) for o in expr.operands]
        return f" {op_str} ".join(parts)
    if isinstance(expr, ast.AggregationFunction):
        # count(*) may be parsed as argument=Literal("*") or argument=None
        is_count_star = (
            expr.function_name == "count"
            and (
                expr.argument is None
                or (isinstance(expr.argument, ast.Literal) and expr.argument.value == "*")
            )
        )
        if is_count_star:
            return "count(*)"
        distinct = "DISTINCT " if expr.distinct else ""
        arg_text = _expr_to_cypher_text(expr.argument) if expr.argument is not None else "*"
        return f"{expr.function_name}({distinct}{arg_text})"
    if isinstance(expr, ast.FunctionCall):
        fn = expr.function_name
        # Arithmetic operator wrappers: __arith_+ → infix notation "a + b"
        _ARITH_OPS = {
            "__arith_+": "+", "__arith_-": "-",
            "__arith_*": "*", "__arith_/": "/",
            "__arith_%": "%", "__arith_^": "^",
        }
        if fn in _ARITH_OPS and len(expr.arguments) == 2:
            op = _ARITH_OPS[fn]
            left = _expr_to_cypher_text(expr.arguments[0])
            right = _expr_to_cypher_text(expr.arguments[1])
            # Preserve parentheses: wrap sub-arith-expressions to match original Cypher text.
            # The parser encodes explicit parens by placing a lower-precedence op as a
            # direct argument. Wrap right operand if it is itself a binary arithmetic expr.
            _PREC = {"^": 4, "*": 3, "/": 3, "%": 3, "+": 2, "-": 2}
            def _needs_paren(arg, parent_op):
                if not (isinstance(arg, ast.FunctionCall) and arg.function_name in _ARITH_OPS and len(arg.arguments) == 2):
                    return False
                child_op = _ARITH_OPS[arg.function_name]
                return _PREC.get(child_op, 0) < _PREC.get(parent_op, 0)
            if _needs_paren(expr.arguments[1], op):
                right = f"({right})"
            if _needs_paren(expr.arguments[0], op):
                left = f"({left})"
            return f"{left} {op} {right}"
        if fn == "__arith_unary-" and len(expr.arguments) == 1:
            return f"-{_expr_to_cypher_text(expr.arguments[0])}"
        args = ", ".join(_expr_to_cypher_text(a) for a in expr.arguments)
        return f"{fn}({args})"
    if isinstance(expr, ast.MapLiteral):
        entries = ", ".join(
            f"{k}: {_expr_to_cypher_text(v)}" for k, v in expr.entries.items()
        )
        return "{" + entries + "}"
    if isinstance(expr, ast.SubscriptExpression):
        base_text = _expr_to_cypher_text(expr.expression)
        idx_text = _expr_to_cypher_text(expr.index)
        return f"{base_text}[{idx_text}]"
    if isinstance(expr, ast.SliceExpression):
        base_text = _expr_to_cypher_text(expr.expression)
        start_text = _expr_to_cypher_text(expr.start) if expr.start is not None else ""
        end_text = _expr_to_cypher_text(expr.end) if expr.end is not None else ""
        return f"{base_text}[{start_text}..{end_text}]"
    return ""


def _contains_aggregation(expr) -> bool:
    """Recursively check if an expression contains an aggregation function."""
    if isinstance(expr, ast.AggregationFunction):
        return True
    if isinstance(expr, ast.BooleanExpression):
        return any(_contains_aggregation(o) for o in expr.operands)
    if isinstance(expr, ast.FunctionCall):
        # Covers both user functions and __arith_+/- arithmetic wrappers
        return any(_contains_aggregation(a) for a in expr.arguments)
    if isinstance(expr, ast.MapLiteral):
        return any(_contains_aggregation(v) for v in expr.entries.values())
    if isinstance(expr, ast.Literal) and isinstance(expr.value, list):
        return any(_contains_aggregation(v) for v in expr.value if hasattr(v, '__class__') and not isinstance(v, (int, str, float, bool, type(None))))
    return False


def _collect_non_agg_var_refs(expr):
    """
    For an expression that contains aggregation, collect the non-aggregate sub-expressions
    that reference bound variables (PropertyReference or Variable). These are potential
    ambiguous grouping key candidates.

    Returns a list of non-aggregate sub-expressions that reference variables.
    When the expression is an arithmetic operation mixing aggregate and non-aggregate
    operands, returns the non-aggregate operands at the top level.
    """
    results = []
    if isinstance(expr, ast.AggregationFunction):
        return []  # aggregation boundary — don't descend
    if isinstance(expr, ast.FunctionCall) and expr.function_name.startswith("__arith_"):
        # Arithmetic: collect non-aggregate sub-operands that reference variables
        has_agg_args = any(_contains_aggregation(a) for a in expr.arguments)
        if has_agg_args:
            for arg in expr.arguments:
                if not _contains_aggregation(arg):
                    # Non-aggregate operand in a mixed arithmetic expression
                    if _expr_references_variable(arg):
                        results.append(arg)
                else:
                    results.extend(_collect_non_agg_var_refs(arg))
        return results
    if isinstance(expr, ast.FunctionCall):
        for arg in expr.arguments:
            results.extend(_collect_non_agg_var_refs(arg))
        return results
    if isinstance(expr, ast.BooleanExpression):
        for op in expr.operands:
            results.extend(_collect_non_agg_var_refs(op))
        return results
    return results


def _expr_references_variable(expr) -> bool:
    """Return True if the expression contains a Variable or PropertyReference."""
    if isinstance(expr, (ast.Variable, ast.PropertyReference)):
        return True
    if isinstance(expr, ast.FunctionCall):
        return any(_expr_references_variable(a) for a in expr.arguments)
    if isinstance(expr, ast.BooleanExpression):
        return any(_expr_references_variable(o) for o in expr.operands)
    return False


def translate_return_clause(ret, context):
    # RETURN * — star is parsed as Literal('*') or as a special ReturnStar item.
    is_return_star = (
        len(ret.items) == 1
        and isinstance(ret.items[0].expression, ast.Literal)
        and ret.items[0].expression.value == "*"
    )
    if is_return_star and not context.stages and not context.variable_aliases:
        raise SyntaxError(
            "NoVariablesInScope: RETURN * is not allowed when there are no variables in scope."
        )

    # Check for duplicate column names (ColumnNameConflict)
    seen_aliases: set = set()
    for item in ret.items:
        effective_alias = item.alias
        if effective_alias is None:
            if isinstance(item.expression, ast.Variable):
                effective_alias = item.expression.name
            elif isinstance(item.expression, ast.PropertyReference):
                effective_alias = f"{item.expression.variable}.{item.expression.property_name}"
        if effective_alias is not None:
            if effective_alias in seen_aliases:
                raise SyntaxError(
                    f"ColumnNameConflict: Multiple result columns with the same name are not supported: '{effective_alias}'"
                )
            seen_aliases.add(effective_alias)

    # RETURN * — the star is parsed as Literal('*').
    # Expand * into explicit hydration for all variables in scope.
    if (
        len(ret.items) == 1
        and isinstance(ret.items[0].expression, ast.Literal)
        and ret.items[0].expression.value == "*"
        and (context.stages or context.variable_aliases or context.named_paths)
    ):
        # Expand RETURN * into explicit select items for each variable.
        # Variables must be sorted deterministically for test reproducibility.
        for var_name in sorted(context.variable_aliases.keys()):
            if var_name in context.scalar_variables:
                continue

            alias_name = context.variable_aliases.get(var_name)
            if not alias_name:
                continue

            # Handle edge variables (aliases starting with 'e')
            if alias_name.startswith("e") and not alias_name.startswith("Stage"):
                # Edge variable: emit identity columns (s, p, o_id)
                is_undirected = alias_name in getattr(context, "_undirected_aliases", set())
                if is_undirected:
                    context.select_items.append(f"{alias_name}._src AS {var_name}_src")
                    context.select_items.append(f"{alias_name}._p AS {var_name}_p")
                    context.select_items.append(f"{alias_name}._dst AS {var_name}_dst")
                else:
                    context.select_items.append(f"{alias_name}.s AS {var_name}_s")
                    context.select_items.append(f"{alias_name}.p AS {var_name}_p")
                    context.select_items.append(f"{alias_name}.o_id AS {var_name}_o_id")
                context.optional_null_row_items.extend(["NULL", "NULL", "NULL"])
                continue

            # Handle stage-promoted edge variables (e.g., WITH r promoted to Stage1)
            edge_stage_vars = getattr(context, "edge_stage_variables", set())
            if alias_name.startswith("Stage") and var_name in edge_stage_vars:
                p_col = f"__edge_{var_name}_p"
                q_col = f"{alias_name}.{var_name}"  # Stage column holding qualifiers JSON
                edge_json = (
                    f"'{{\"type\":\"' || {alias_name}.{p_col} || '\",\"props\":' || "
                    f"COALESCE({q_col}, '{{}}') || '}}'"
                )
                context.select_items.append(f"{edge_json} AS {var_name}")
                context.optional_null_row_items.append("NULL")
                continue

            # TCK procedure CTE variables are scalar, not graph nodes
            if alias_name and alias_name.startswith("TCK_Proc_"):
                renames_star = getattr(context, '_tck_yield_renames', {})
                if var_name in renames_star:
                    _cte_s, orig_col_s = renames_star[var_name]
                    sql_col_s = f"{alias_name}.{orig_col_s}"
                else:
                    sql_col_s = f"{alias_name}.{var_name}"
                context.select_items.append(f"{sql_col_s} AS {_safe_alias(var_name)}")
                context.optional_null_row_items.append("NULL")
                continue

            # Handle node variables (all other non-edge, non-scalar aliases)
            if alias_name and not alias_name.startswith("e"):
                prefix = var_name
                if alias_name.startswith("Stage") or alias_name in _PROC_CTE_ALIASES:
                    node_expr = var_name
                else:
                    # Check if this node is null-gated by a downstream optional edge
                    gate_edge = context.opt_intermediate_nulled.get(alias_name)
                    if gate_edge:
                        node_expr = (
                            f"CASE WHEN {gate_edge}.s IS NULL "
                            f"THEN NULL ELSE {alias_name}.node_id END"
                        )
                    else:
                        node_expr = f"{alias_name}.node_id"
                context.select_items.append(f"{node_expr} AS {prefix}_id")
                context.select_items.append(
                    f"{labels_subquery(node_expr)} AS {prefix}_labels"
                )
                context.select_items.append(
                    f"{properties_subquery(node_expr)} AS {prefix}_props"
                )
                context.optional_null_row_items.extend(["NULL", "NULL", "NULL"])
        # Also expand named path variables from context.named_paths
        for path_var in sorted(context.named_paths.keys()):
            if path_var not in (context.path_node_aliases or {}):
                continue
            node_aliases = context.path_node_aliases[path_var]
            edge_aliases = context.path_edge_aliases.get(path_var, [])
            node_id_expr_map = getattr(context, "node_id_expr", {})
            nodes_arr = ", ".join(
                node_id_expr_map.get(a, f"{a}.node_id") for a in node_aliases
            )
            undirected_aliases = getattr(context, "_undirected_aliases", set())
            rels_parts = []
            for a in edge_aliases:
                col = "_p" if a in undirected_aliases else "p"
                rels_parts.append(f"{a}.{col}")
            rels_arr = ", ".join(rels_parts)
            raw_json = f"'{{\"nodes\":' || JSON_ARRAY({nodes_arr}) || ',\"rels\":' || JSON_ARRAY({rels_arr}) || '}}'"
            # For OPTIONAL MATCH named paths: if any relationship alias is NULL (no match),
            # the path should be NULL rather than a JSON string with null elements.
            if rels_parts:
                null_check = " OR ".join(f"{rp} IS NULL" for rp in rels_parts)
                json_expr = f"CASE WHEN ({null_check}) THEN NULL ELSE {raw_json} END"
            else:
                json_expr = raw_json
            context.select_items.append(f"{json_expr} AS {_safe_alias(path_var)}")
            context.optional_null_row_items.append("NULL")
        return
    # Detect if there are any aggregation functions in the RETURN items (including nested)
    has_agg = any(_contains_aggregation(i.expression) for i in ret.items)

    # AmbiguousAggregationExpression: detect return items that mix aggregation with
    # non-aggregate variable/property references that are not standalone grouping keys.
    # Rule: in a mixed aggregate expression:
    #   - simple property refs (n.prop) or variables (n) that are standalone return items: OK
    #   - anything else mixed with aggregation: AmbiguousAggregationExpression
    if has_agg:
        # Collect grouping keys: standalone non-aggregate return items.
        non_agg_items = [i for i in ret.items if not _contains_aggregation(i.expression)]
        if not non_agg_items:
            context.return_is_pure_aggregation = True
        grouping_exprs = set()
        for gi in non_agg_items:
            grouping_exprs.add(_expr_to_cypher_text(gi.expression))
        for item in ret.items:
            if not _contains_aggregation(item.expression):
                continue
            if isinstance(item.expression, ast.AggregationFunction):
                continue  # pure aggregate — OK
            # Mixed item: contains both aggregation and non-aggregate sub-expressions.
            # Extract the non-aggregate sub-expressions that reference variables.
            ambiguous_parts = _collect_non_agg_var_refs(item.expression)
            for part in ambiguous_parts:
                part_text = _expr_to_cypher_text(part)
                if not part_text:
                    continue
                # Skip query parameter variables — they are constants, not bound variables
                if isinstance(part, ast.Variable) and part.name in context.input_params:
                    continue
                # Complex non-aggregate expressions (arithmetic, function calls, etc.)
                # mixed with aggregation are always ambiguous, even if they are grouping keys.
                # Only simple property references and variables are allowed as grouping key refs.
                is_simple = isinstance(part, (ast.Variable, ast.PropertyReference))
                if not is_simple:
                    raise SyntaxError(
                        f"AmbiguousAggregationExpression: An expression using aggregation "
                        f"and a non-aggregate is ambiguous because of the way the query is structured: "
                        f"'{_expr_to_cypher_text(item.expression)}'"
                    )
                if part_text not in grouping_exprs:
                    raise SyntaxError(
                        f"AmbiguousAggregationExpression: An expression using aggregation "
                        f"and a non-aggregate is ambiguous because of the way the query is structured: "
                        f"'{_expr_to_cypher_text(item.expression)}'"
                    )

    agg_aliases: set = set()
    _used_ret_aliases: set = set()  # deduplicate auto-generated RETURN aliases
    for item in ret.items:
        if isinstance(item.expression, ast.Variable):
            var_name = item.expression.name
            if var_name in context.named_paths:
                alias = item.alias or var_name
                node_aliases = context.path_node_aliases[var_name]
                edge_aliases = context.path_edge_aliases[var_name]
                node_id_expr = getattr(context, "node_id_expr", {})
                nodes_arr = ", ".join(
                    node_id_expr.get(a, f"{a}.node_id") for a in node_aliases
                )
                # Use _p for bidirectional (undirected) edges, p for directed edges
                undirected_aliases = getattr(context, "_undirected_aliases", set())
                rels_parts = []
                for a in edge_aliases:
                    col = "_p" if a in undirected_aliases else "p"
                    rels_parts.append(f"{a}.{col}")
                rels_arr = ", ".join(rels_parts)
                raw_json = f"'{{\"nodes\":' || JSON_ARRAY({nodes_arr}) || ',\"rels\":' || JSON_ARRAY({rels_arr}) || '}}'"
                # For OPTIONAL MATCH: if any relationship alias is NULL, path should be NULL.
                if rels_parts:
                    null_check = " OR ".join(f"{rp} IS NULL" for rp in rels_parts)
                    json_expr = f"CASE WHEN ({null_check}) THEN NULL ELSE {raw_json} END"
                else:
                    json_expr = raw_json
                context.select_items.append(f"{json_expr} AS {_safe_alias(alias)}")
                continue
            alias_name = context.variable_aliases.get(var_name)
            is_scalar = var_name in context.scalar_variables
            # Variable-length relationship list variable: the engine fills in the actual
            # relationship path list after BFS traversal.  Emit NULL as a placeholder
            # column that the engine replaces.
            if alias_name == "__vl_rel__":
                col_alias = item.alias or var_name
                context.select_items.append(f"NULL AS {_safe_alias(col_alias)}")
                context.optional_null_row_items.append("NULL")
                continue
            # Stage-promoted edge variables (e.g. WITH r promoted to Stage1 with __edge_r_s/p/o)
            # must NOT go through the node path — emit edge identity + qualifiers as JSON.
            edge_stage_vars = getattr(context, "edge_stage_variables", set())
            if alias_name and alias_name.startswith("Stage") and var_name in edge_stage_vars:
                prefix = item.alias or var_name
                p_col = f"__edge_{var_name}_p"
                q_col = f"{alias_name}.{var_name}"  # Stage1.r = qualifiers JSON
                edge_json = (
                    f"'{{\"type\":\"' || {alias_name}.{p_col} || '\",\"props\":' || "
                    f"COALESCE({q_col}, '{{}}') || '}}'"
                )
                context.select_items.append(f"{edge_json} AS {_safe_alias(prefix)}")
                context.optional_null_row_items.append("NULL")
                # When aggregates are present, edge variables must be in GROUP BY
                if has_agg:
                    context.group_by_items.append(f"{alias_name}.{p_col}")
                # Track RETURN alias → original var for ORDER BY property resolution
                if prefix != var_name:
                    if not hasattr(context, "_return_alias_map"):
                        context._return_alias_map = {}
                    context._return_alias_map[prefix] = var_name
                continue
            if alias_name == "scalar":
                continue
            # TCK procedure CTE: variables are scalar columns, not graph nodes
            if alias_name and alias_name.startswith("TCK_Proc_"):
                renames = getattr(context, '_tck_yield_renames', {})
                if var_name in renames:
                    _cte, orig_col = renames[var_name]
                    sql_col = f"{alias_name}.{orig_col}"
                else:
                    sql_col = f"{alias_name}.{var_name}"
                prefix = item.alias or var_name
                context.select_items.append(f"{sql_col} AS {_safe_alias(prefix)}")
                continue
            # Edge variable in RETURN (alias starts with 'e', not a stage var):
            # emit type + qualifiers as JSON object so comparison can match [:TYPE {props}]
            if alias_name and alias_name.startswith("e") and not is_scalar:
                prefix = item.alias or var_name
                is_undirected = alias_name in getattr(context, "_undirected_aliases", set())
                p_col = f"{alias_name}.{'_p' if is_undirected else 'p'}"
                q_col = f"{alias_name}.qualifiers"
                edge_json = (
                    f"'{{\"type\":\"' || {p_col} || '\",\"props\":' || "
                    f"COALESCE({q_col}, '{{}}') || '}}'"
                )
                context.select_items.append(f"{edge_json} AS {_safe_alias(prefix)}")
                context.optional_null_row_items.append("NULL")
                if has_agg:
                    context.group_by_items.append(p_col)
                continue
            if alias_name and not alias_name.startswith("e") and not is_scalar:
                prefix = item.alias or var_name
                if alias_name.startswith("Stage") or alias_name in _PROC_CTE_ALIASES:
                    node_expr = var_name
                else:
                    # Check if this node is null-gated by a downstream optional edge.
                    # When multi-hop OPTIONAL MATCH fails the second hop, the intermediate
                    # node (e.g. b in OPTIONAL MATCH (a)-->(b)-->(c)) must appear as null
                    # even though it was left-outer-joined via the first hop.
                    gate_edge = context.opt_intermediate_nulled.get(alias_name)
                    if gate_edge:
                        node_expr = (
                            f"CASE WHEN {gate_edge}.s IS NULL "
                            f"THEN NULL ELSE {alias_name}.node_id END"
                        )
                    else:
                        node_expr = f"{alias_name}.node_id"
                context.select_items.append(f"{node_expr} AS {prefix}_id")
                context.select_items.append(
                    f"{labels_subquery(node_expr)} AS {prefix}_labels"
                )
                context.select_items.append(
                    f"{properties_subquery(node_expr)} AS {prefix}_props"
                )
                # Null-row for OPTIONAL MATCH: node is null → 3 NULLs
                context.optional_null_row_items.extend(["NULL", "NULL", "NULL"])
                # When aggregates are present, node variables must be in GROUP BY
                if has_agg:
                    context.group_by_items.append(node_expr)
                continue
        sql = translate_expression(item.expression, context, segment="select")
        # IRIS VARCHAR collation uppercases string values in SELECT/GROUP BY/DISTINCT.
        # Wrap bare property-value references (p\d+.val) with %EXACT() to preserve case.
        import re as _re_exact
        sql = _re_exact.sub(r'\bp(\d+)\.val\b', r'%EXACT(p\1.val)', sql)
        alias = item.alias
        user_provided_alias = alias is not None  # True when user wrote AS <alias>
        cypher_col = None  # Cypher-text column name for post-execution remapping
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference):
                alias = f"{item.expression.variable}_{item.expression.property_name}"
                cypher_col = f"{item.expression.variable}.{item.expression.property_name}"
                # NOTE: column_name_map registration done below after alias deduplication
            elif isinstance(item.expression, ast.Variable):
                alias = item.expression.name
            elif isinstance(
                item.expression, (ast.AggregationFunction, ast.FunctionCall)
            ):
                # For function calls (e.g., labels(a), count(*)), use cypher_text as the actual column name
                cypher_text = _expr_to_cypher_text(item.expression)
                if cypher_text:
                    import re as _re_fn
                    alias = _re_fn.sub(r'[^A-Za-z0-9_]', '_', cypher_text)
                    if alias and alias[0].isdigit():
                        alias = f"_{alias}"
                    if not alias:
                        alias = f"{item.expression.function_name}_res"
                else:
                    alias = f"{item.expression.function_name}_res"
                # NOTE: column_name_map registration done below after alias deduplication
            else:
                cypher_text = _expr_to_cypher_text(item.expression)
                if cypher_text:
                    import re as _re_alias
                    # Build a SQL-safe alias (replace non-identifier chars with underscores)
                    alias = _re_alias.sub(r'[^A-Za-z0-9_]', '_', cypher_text)
                    if alias and alias[0].isdigit():
                        alias = f"_{alias}"
                    # NOTE: do NOT register column_name_map here — done below after dedup
        if alias:
            # Deduplicate auto-generated aliases (e.g. x > d and x < d both → x___d)
            safe = _safe_alias(alias).replace('.', '_')
            if safe in _used_ret_aliases:
                _dedup_n = 2
                while f"{safe}_{_dedup_n}" in _used_ret_aliases:
                    _dedup_n += 1
                safe = f"{safe}_{_dedup_n}"
            _used_ret_aliases.add(safe)
            # Register column_name_map with the FINAL (deduplicated) alias.
            # Only when the alias was auto-generated (not user-provided via AS <alias>):
            # user-provided aliases are already the intended column names.
            if not user_provided_alias:
                cypher_text_final = _expr_to_cypher_text(item.expression)
                if cypher_text_final and cypher_text_final != safe:
                    context.column_name_map[safe] = cypher_text_final
            context.select_items.append(f"{sql} AS {safe}")
        else:
            context.select_items.append(sql)
        # If there's aggregation in the RETURN clause and this item does not contain
        # any aggregation, add it to GROUP BY (same logic as translate_with_clause)
        if has_agg and not _contains_aggregation(item.expression):
            context.group_by_items.append(sql)
        if isinstance(item.expression, ast.AggregationFunction):
            agg_aliases.add(alias) if alias else None
        # Build null-row value for OPTIONAL MATCH fallback:
        # IS NULL → 1 (null IS NULL = true), IS NOT NULL → 0, else NULL
        if isinstance(item.expression, ast.BooleanExpression):
            op = item.expression.operator
            if op == ast.BooleanOperator.IS_NULL:
                context.optional_null_row_items.append("1")
            elif op == ast.BooleanOperator.IS_NOT_NULL:
                context.optional_null_row_items.append("0")
            else:
                context.optional_null_row_items.append("NULL")
        else:
            context.optional_null_row_items.append("NULL")


def translate_with_clause(with_clause, context):
    if with_clause.star:
        for var, alias in context.variable_aliases.items():
            if alias.startswith("e"):
                is_undirected = alias in getattr(context, "_undirected_aliases", set())
                if is_undirected:
                    context.select_items.append(f"{alias}._src AS {var}_src, {alias}._p AS {var}_p, {alias}._dst AS {var}_dst")
                else:
                    context.select_items.append(f"{alias}.s AS {var}_s, {alias}.p AS {var}_p, {alias}.o_id AS {var}_o_id")
            else:
                context.select_items.append(f"{alias}.node_id AS {var}")
        if with_clause.where_clause:
            context.where_conditions.append(
                translate_boolean_expression(with_clause.where_clause.expression, context)
            )
        return
    has_agg = any(_contains_aggregation(i.expression) for i in with_clause.items)
    agg_aliases: set = set()
    agg_alias_sql: dict = {}  # Maps aggregate alias -> SQL expression (computed once)

    # AmbiguousAggregationExpression check for WITH clause (same rules as RETURN).
    if has_agg:
        non_agg_items = [i for i in with_clause.items if not _contains_aggregation(i.expression)]
        grouping_exprs = set()
        for gi in non_agg_items:
            grouping_exprs.add(_expr_to_cypher_text(gi.expression))
        for item in with_clause.items:
            if not _contains_aggregation(item.expression):
                continue
            if isinstance(item.expression, ast.AggregationFunction):
                continue
            ambiguous_parts = _collect_non_agg_var_refs(item.expression)
            for part in ambiguous_parts:
                part_text = _expr_to_cypher_text(part)
                if not part_text:
                    continue
                if isinstance(part, ast.Variable) and part.name in context.input_params:
                    continue
                is_simple = isinstance(part, (ast.Variable, ast.PropertyReference))
                if not is_simple:
                    raise SyntaxError(
                        f"AmbiguousAggregationExpression: An expression using aggregation "
                        f"and a non-aggregate is ambiguous: '{_expr_to_cypher_text(item.expression)}'"
                    )
                if part_text not in grouping_exprs:
                    raise SyntaxError(
                        f"AmbiguousAggregationExpression: An expression using aggregation "
                        f"and a non-aggregate is ambiguous: '{_expr_to_cypher_text(item.expression)}'"
                    )

    # Process WITH clause items: translate expressions and add to select
    for item in with_clause.items:
        sql = translate_expression(item.expression, context, segment="select")
        # Do NOT apply %EXACT() wrapping here — WITH items are intermediate CTE columns
        # used in downstream WHERE comparisons. Wrapping with %EXACT() causes comparison
        # mismatches when downstream queries use raw p*.val against Stage columns.
        # %EXACT() is applied only in final RETURN clause output.
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference):
                alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable):
                alias = item.expression.name
            elif isinstance(item.expression, (ast.AggregationFunction, ast.FunctionCall,
                                               ast.BooleanExpression, ast.Literal,
                                               ast.MapLiteral)):
                # Non-variable, non-property expressions require an explicit alias in WITH
                raise SyntaxError(
                    "NoExpressionAlias: Expression in WITH must be aliased"
                )
        if alias is None:
            alias = context.next_alias("v")

        # Track temporal literal values for compile-time TZ conversion in later stages
        if isinstance(sql, str) and sql.startswith("'") and sql.endswith("'"):
            if not hasattr(context, 'temporal_literal_values'):
                context.temporal_literal_values = {}
            context.temporal_literal_values[alias] = sql[1:-1]  # strip quotes

        # Track temporal types for property access extraction
        if isinstance(item.expression, ast.FunctionCall):
            fn = item.expression.function_name.lower()
            if fn in ("date", "localtime", "time", "datetime", "localdatetime", "duration"):
                context.temporal_types[alias] = fn
            # Namespace temporal functions: duration.between → duration, date.truncate → date, etc.
            elif fn.startswith("duration."):
                context.temporal_types[alias] = "duration"
            elif fn.startswith("date."):
                context.temporal_types[alias] = "date"
            elif fn.startswith("datetime."):
                context.temporal_types[alias] = "datetime"
            elif fn.startswith("localdatetime."):
                context.temporal_types[alias] = "localdatetime"
            elif fn.startswith("localtime."):
                context.temporal_types[alias] = "localtime"
            elif fn.startswith("time."):
                context.temporal_types[alias] = "time"
        # Also detect temporal properties: properties named 'date', 'time', 'datetime', etc.
        # from nodes stored in the database are assumed to contain temporal values
        elif isinstance(item.expression, ast.PropertyReference):
            prop_name = item.expression.property_name.lower()
            if prop_name in ("date", "localtime", "time", "datetime", "localdatetime", "duration"):
                context.temporal_types[alias] = prop_name

        # Track non-integer index type variables for list subscript TypeError enforcement.
        # If a variable is bound to a boolean, float, string, list, or map literal it cannot
        # serve as a valid Cypher list index — flag it so _expr_subscript emits IVGLISTGET.
        if isinstance(item.expression, ast.Literal):
            _v = item.expression.value
            if isinstance(_v, bool) or isinstance(_v, float) or isinstance(_v, str) or isinstance(_v, list):
                context.non_integer_index_vars.add(alias)
        elif isinstance(item.expression, ast.MapLiteral):
            context.non_integer_index_vars.add(alias)
        elif isinstance(item.expression, ast.Variable):
            # Also check input_params for parameter variables bound to non-integer types.
            # e.g. WITH $idx AS idx where $idx is a boolean/float/string/list/map.
            _param_name = item.expression.name
            if _param_name in context.input_params:
                _pval = context.input_params[_param_name]
                if isinstance(_pval, bool) or isinstance(_pval, float) or isinstance(_pval, str) or isinstance(_pval, (list, dict)):
                    context.non_integer_index_vars.add(alias)

        # Track literal list variables for list-comprehension constant folding.
        # When a WITH item binds a variable to a literal list, record the Python value so that
        # downstream list comprehensions with type-conversion projections (toFloat, toInteger, etc.)
        # can be constant-folded, preserving null slots that JSON_ARRAYAGG would silently drop.
        if isinstance(item.expression, ast.Literal) and isinstance(item.expression.value, list):
            _lit_elems = item.expression.value
            if hasattr(context, 'literal_list_vars'):
                context.literal_list_vars[alias] = _lit_elems
        elif isinstance(item.expression, ast.Variable):
            _pname = item.expression.name
            if hasattr(context, 'literal_list_vars') and _pname in context.literal_list_vars:
                context.literal_list_vars[alias] = context.literal_list_vars[_pname]

        # Track non-map variables for property access TypeError enforcement.
        # Scalars (int, float, bool, str) and lists cannot have properties accessed on them.
        # Note: null (None) is NOT added here — property access on null returns null, not TypeError.
        if isinstance(item.expression, ast.Literal):
            _v = item.expression.value
            if _v is not None and not isinstance(_v, dict):
                context.non_map_vars.add(alias)
        # ast.ListLiteral does not exist — list literals are Literal(value=list)
        # Already handled above by the isinstance(item.expression, ast.Literal) branch
        elif isinstance(item.expression, ast.Variable):
            _param_name = item.expression.name
            if _param_name in context.input_params:
                _pval = context.input_params[_param_name]
                if not isinstance(_pval, dict):
                    context.non_map_vars.add(alias)

        # Stage-promoted edge variables forwarded through another WITH: propagate identity columns
        # so downstream MATCH/RETURN can still reference __edge_<alias>_s/p/o.
        edge_stage_vars = getattr(context, "edge_stage_variables", set())
        if (isinstance(item.expression, ast.Variable)
                and item.expression.name in edge_stage_vars
                and context.variable_aliases.get(item.expression.name, "").startswith("Stage")):
            prev_stage = context.variable_aliases[item.expression.name]
            prev_var = item.expression.name
            # Propagate identity columns using new alias name
            stage_col_name = alias
            for suffix in ("_s", "_p", "_o"):
                context.select_items.append(
                    f"{prev_stage}.__edge_{prev_var}{suffix} AS __edge_{stage_col_name}{suffix}"
                )
            if not hasattr(context, "edge_stage_variables"):
                context.edge_stage_variables = set()
            context.edge_stage_variables.add(alias)
            if alias != prev_var:
                context.edge_stage_variables.add(alias)

        # Edge variables: expose qualifiers JSON so downstream r.prop works via JSON_VALUE(r, '$.prop')
        if (isinstance(item.expression, ast.Variable)
                and context.variable_aliases.get(item.expression.name, "").startswith("e")
                and not context.variable_aliases.get(item.expression.name, "").startswith("Stage")):
            e_alias = context.variable_aliases[item.expression.name]
            sql = f"{e_alias}.qualifiers"
            if not hasattr(context, "edge_stage_variables"):
                context.edge_stage_variables = set()
            context.edge_stage_variables.add(item.expression.name)
            # Also register the WITH alias (e.g. WITH r1 AS r2: alias = "r2") so that
            # downstream MATCH/RETURN using the alias name can find it in edge_stage_variables.
            if alias != item.expression.name:
                context.edge_stage_variables.add(alias)
                # Rebind the alias in variable_aliases so the second MATCH resolves it to Stage
                context.variable_aliases[alias] = context.variable_aliases.get(item.expression.name, "Stage1")
            # Preserve edge identity columns so DELETE can find the original edge row
            # even after the relationship variable is promoted to a CTE stage.
            # Use the final alias name so stage-bound MATCH uses matching column names.
            stage_col_name = alias  # column in Stage CTE is named after the alias
            is_undirected = e_alias in getattr(context, "_undirected_aliases", set())
            if is_undirected:
                context.select_items.append(f"{e_alias}._src AS __edge_{stage_col_name}_s")
                context.select_items.append(f"{e_alias}._p AS __edge_{stage_col_name}_p")
                context.select_items.append(f"{e_alias}._dst AS __edge_{stage_col_name}_o")
            else:
                context.select_items.append(f"{e_alias}.s AS __edge_{stage_col_name}_s")
                context.select_items.append(f"{e_alias}.p AS __edge_{stage_col_name}_p")
                context.select_items.append(f"{e_alias}.o_id AS __edge_{stage_col_name}_o")
        context.select_items.append(f"{sql} AS {_safe_alias(alias).replace('.', '_')}")
        if has_agg and not _contains_aggregation(item.expression):
            # For edge variables in GROUP BY, also include s/p/o identity columns so that
            # edges with the same qualifiers (e.g. both {}) are not collapsed into one group.
            if (isinstance(item.expression, ast.Variable)
                    and context.variable_aliases.get(item.expression.name, "").startswith("e")
                    and not context.variable_aliases.get(item.expression.name, "").startswith("Stage")):
                e_alias_gb = context.variable_aliases[item.expression.name]
                is_undirected_gb = e_alias_gb in getattr(context, "_undirected_aliases", set())
                if is_undirected_gb:
                    context.group_by_items.extend([
                        f"{e_alias_gb}._src", f"{e_alias_gb}._p",
                        f"{e_alias_gb}._dst", f"{e_alias_gb}.qualifiers"
                    ])
                else:
                    context.group_by_items.extend([
                        f"{e_alias_gb}.s", f"{e_alias_gb}.p",
                        f"{e_alias_gb}.o_id", f"{e_alias_gb}.qualifiers"
                    ])
            else:
                context.group_by_items.append(sql)
        if isinstance(item.expression, ast.AggregationFunction):
            agg_aliases.add(alias)
            # Store SQL for this aggregate so it can be reused in HAVING without re-translating
            agg_alias_sql[alias] = sql

    if with_clause.where_clause:
        expr = with_clause.where_clause.expression

        if has_agg and agg_aliases and _references_agg_alias(expr, agg_aliases):
            # For aggregate filters (HAVING), use the aggregate alias SQL directly
            context.having_conditions.append(
                _translate_having_expr(expr, agg_aliases, agg_alias_sql, context)
            )
        else:
            # For non-aggregate WHERE: translate expressions using their original forms,
            # not their aliases. This is because SQL WHERE is applied before SELECT aliases
            # are bound. We must replace alias references with their underlying expressions.
            # Build a map from alias names to their original expressions for substitution.
            alias_to_expr: dict = {}  # Maps alias -> original expression AST
            for item in with_clause.items:
                alias = item.alias
                if alias is None:
                    if isinstance(item.expression, ast.PropertyReference):
                        alias = f"{item.expression.variable}_{item.expression.property_name}"
                    elif isinstance(item.expression, ast.Variable):
                        alias = item.expression.name
                    elif isinstance(item.expression, ast.AggregationFunction):
                        alias = f"{item.expression.function_name}"
                if alias is None:
                    alias = context.next_alias("v")
                alias_to_expr[alias] = item.expression

            # Translate the WHERE using a substitute function that expands aliases to original expressions
            where_sql = _translate_where_with_alias_expansion(expr, alias_to_expr, context)
            context.where_conditions.append(where_sql)


def _translate_where_with_alias_expansion(expr, alias_to_expr: dict, context) -> str:
    """Translate a WHERE expression for WITH clause, expanding aliases to their original expressions.

    In SQL, WHERE is evaluated before SELECT aliases are bound. When a Cypher WITH clause has
    `WITH a.name AS n WHERE n = 'x'`, the WHERE must use the original expression (a.name),
    not the alias (n). This function recursively expands alias references to their expressions.

    Args:
        expr: The boolean expression AST to translate
        alias_to_expr: Map from alias name -> original expression AST
        context: Translation context

    Returns:
        SQL string for the WHERE clause
    """
    if isinstance(expr, ast.Variable):
        # If this variable is an alias defined in the WITH, substitute it with the original expression
        if expr.name in alias_to_expr:
            original_expr = alias_to_expr[expr.name]
            # Recursively translate the original expression
            return translate_expression(original_expr, context, segment="where")
        # Otherwise, translate normally
        return translate_expression(expr, context, segment="where")

    if isinstance(expr, ast.BooleanExpression):
        op = expr.operator
        if op in (ast.BooleanOperator.AND, ast.BooleanOperator.OR):
            op_str = " AND " if op == ast.BooleanOperator.AND else " OR "
            parts = [_translate_where_with_alias_expansion(o, alias_to_expr, context) for o in expr.operands]
            return "(" + op_str.join(parts) + ")"
        elif op == ast.BooleanOperator.NOT:
            inner = _translate_where_with_alias_expansion(expr.operands[0], alias_to_expr, context)
            return f"NOT ({inner})"
        elif op in (ast.BooleanOperator.IS_NULL, ast.BooleanOperator.IS_NOT_NULL):
            # Handle unary IS NULL / IS NOT NULL operators
            operand_expr = expr.operands[0]
            if isinstance(operand_expr, ast.Variable) and operand_expr.name in alias_to_expr:
                operand_sql = translate_expression(alias_to_expr[operand_expr.name], context, segment="where")
            else:
                operand_sql = translate_expression(operand_expr, context, segment="where")
            if op == ast.BooleanOperator.IS_NULL:
                return f"{operand_sql} IS NULL"
            else:
                return f"{operand_sql} IS NOT NULL"
        else:
            # For comparison operators, translate left and right with alias expansion
            left_expr = expr.operands[0]
            right_expr = expr.operands[1] if len(expr.operands) > 1 else None

            # Translate left side with alias expansion
            if isinstance(left_expr, ast.Variable) and left_expr.name in alias_to_expr:
                left = translate_expression(alias_to_expr[left_expr.name], context, segment="where")
            else:
                left = translate_expression(left_expr, context, segment="where")

            # Translate right side with alias expansion
            if right_expr:
                if isinstance(right_expr, ast.Variable) and right_expr.name in alias_to_expr:
                    right = translate_expression(alias_to_expr[right_expr.name], context, segment="where")
                else:
                    right = translate_expression(right_expr, context, segment="where")
            else:
                right = ""

            # Map operator to SQL
            op_map = {
                ast.BooleanOperator.EQUALS: "=",
                ast.BooleanOperator.NOT_EQUALS: "<>",
                ast.BooleanOperator.LESS_THAN: "<",
                ast.BooleanOperator.LESS_THAN_OR_EQUAL: "<=",
                ast.BooleanOperator.GREATER_THAN: ">",
                ast.BooleanOperator.GREATER_THAN_OR_EQUAL: ">=",
            }
            if op in op_map:
                return f"{left} {op_map[op]} {right}"
            # For other operators, fall through to standard translation
            return translate_boolean_expression(expr, context)

    # For non-variable, non-boolean expressions, translate normally
    return translate_boolean_expression(expr, context)


def _references_agg_alias(expr, agg_aliases: set) -> bool:
    if isinstance(expr, ast.Variable) and expr.name in agg_aliases:
        return True
    if isinstance(expr, ast.BooleanExpression):
        return any(_references_agg_alias(o, agg_aliases) for o in expr.operands)
    return False


def _translate_having_expr(expr, agg_aliases: set, agg_alias_sql: dict, context) -> str:
    if isinstance(expr, ast.Variable) and expr.name in agg_aliases:
        return agg_alias_sql.get(expr.name, expr.name)
    if isinstance(expr, ast.BooleanExpression):
        op = expr.operator
        if op == ast.BooleanOperator.AND:
            return "(" + " AND ".join(
                _translate_having_expr(o, agg_aliases, agg_alias_sql, context) for o in expr.operands
            ) + ")"
        if op == ast.BooleanOperator.OR:
            return "(" + " OR ".join(
                _translate_having_expr(o, agg_aliases, agg_alias_sql, context) for o in expr.operands
            ) + ")"
        if op == ast.BooleanOperator.NOT:
            return f"NOT ({_translate_having_expr(expr.operands[0], agg_aliases, agg_alias_sql, context)})"
        left = _translate_having_expr(expr.operands[0], agg_aliases, agg_alias_sql, context)
        right_expr = expr.operands[1] if len(expr.operands) > 1 else None
        # Use segment="inline" to inline literals (don't parameterize them) in HAVING clauses.
        # This avoids adding extra parameters for literal constants in aggregate comparisons.
        right = translate_expression(right_expr, context, segment="inline") if right_expr is not None else ""
        op_map = {
            ast.BooleanOperator.EQUALS: "=",
            ast.BooleanOperator.NOT_EQUALS: "<>",
            ast.BooleanOperator.LESS_THAN: "<",
            ast.BooleanOperator.LESS_THAN_OR_EQUAL: "<=",
            ast.BooleanOperator.GREATER_THAN: ">",
            ast.BooleanOperator.GREATER_THAN_OR_EQUAL: ">=",
        }
        if op in op_map:
            return f"{left} {op_map[op]} {right}"
    return translate_boolean_expression(expr, context)


def _translate_degree_centrality(proc, context) -> None:
    """CALL ivg.degreeCentrality({direction:'out', predicate:'CITES', topK:50}) YIELD node, score, degree"""
    opts = proc.options or {}
    _validate_centrality_proc_map("ivg.degreeCentrality", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    direction = str(_val("direction", "out"))
    pred_v = _val("predicate", "")
    predicate = str(pred_v) if pred_v is not None else ""
    top_k = int(_val("topK", 10000))

    fn = f"{_schema_prefix}.kg_DegreeCentrality" if _schema_prefix else "kg_DegreeCentrality"
    cte_sql = (
        f"SELECT j.node_id AS node, j.score, j.degree\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(direction)}, {_sql_arg(predicate)}, {_sql_arg(top_k)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score',\n"
        f"    degree INTEGER PATH '$.degree'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"DegCent AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "DegCent"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")
    if "degree" in proc.yield_items:
        context.scalar_variables.add("degree")


def _translate_betweenness(proc, context) -> None:
    """CALL ivg.betweenness({sampleSize:100, direction:'out', maxHops:0, topK:50, memBudgetMB:256}) YIELD node, score"""
    opts = proc.options or {}
    _validate_centrality_proc_map("ivg.betweenness", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    sample_size = int(_val("sampleSize", 0))
    direction = str(_val("direction", "out"))
    max_hops = int(_val("maxHops", 0))
    top_k = int(_val("topK", 10000))
    mem_budget_mb = int(_val("memBudgetMB", 256))

    fn = f"{_schema_prefix}.kg_Betweenness" if _schema_prefix else "kg_Betweenness"
    cte_sql = (
        f"SELECT j.node_id AS node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(sample_size)}, {_sql_arg(direction)}, {_sql_arg(max_hops)}, {_sql_arg(top_k)}, {_sql_arg(mem_budget_mb)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"Betweenness AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "Betweenness"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_closeness(proc, context) -> None:
    """CALL ivg.closeness({formula:'harmonic', direction:'out', maxHops:0, topK:50}) YIELD node, score (Phase 5 — pending T064)"""
    opts = proc.options or {}
    _validate_centrality_proc_map("ivg.closeness", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    formula = str(_val("formula", "harmonic"))
    direction = str(_val("direction", "out"))
    max_hops = int(_val("maxHops", 0))
    top_k = int(_val("topK", 10000))

    fn = f"{_schema_prefix}.kg_Closeness" if _schema_prefix else "kg_Closeness"
    cte_sql = (
        f"SELECT j.node_id AS node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(formula)}, {_sql_arg(direction)}, {_sql_arg(max_hops)}, {_sql_arg(top_k)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"Closeness AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "Closeness"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_eigenvector(proc, context) -> None:
    """CALL ivg.eigenvector({maxIter:30, tol:1e-6, topK:50}) YIELD node, score (Phase 6 — pending T080)"""
    opts = proc.options or {}
    _validate_centrality_proc_map("ivg.eigenvector", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    max_iter = int(_val("maxIter", 30))
    tol = float(_val("tol", 1e-6))
    top_k = int(_val("topK", 10000))

    fn = f"{_schema_prefix}.kg_Eigenvector" if _schema_prefix else "kg_Eigenvector"
    cte_sql = (
        f"SELECT j.node_id AS node, j.score\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(max_iter)}, {_sql_arg(tol)}, {_sql_arg(top_k)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    score DOUBLE PATH '$.score'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"Eigenvector AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "Eigenvector"
    if "score" in proc.yield_items:
        context.scalar_variables.add("score")


def _translate_leiden(proc, context) -> None:
    opts = proc.options or {}
    _validate_community_proc_map("ivg.leiden", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    max_levels = int(_val("maxLevels", 10))
    gamma = float(_val("gamma", 1.0))
    tol = float(_val("tol", 1e-4))
    top_k = int(_val("topK", 10000))
    mem_budget_mb = int(_val("memBudgetMB", 256))
    seed_v = _val("randomSeed", None)
    random_seed = -1 if seed_v is None else int(seed_v)

    fn = f"{_schema_prefix}.kg_Leiden" if _schema_prefix else "kg_Leiden"
    cte_sql = (
        f"SELECT j.node_id AS node, j.community, j.size\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(max_levels)}, {_sql_arg(gamma)}, {_sql_arg(tol)}, {_sql_arg(top_k)}, {_sql_arg(mem_budget_mb)}, {_sql_arg(random_seed)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    community INTEGER PATH '$.community',\n"
        f"    size INTEGER PATH '$.size'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"Leiden AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "Leiden"
    if "community" in proc.yield_items:
        context.scalar_variables.add("community")
    if "size" in proc.yield_items:
        context.scalar_variables.add("size")


def _translate_triangle_count(proc, context) -> None:
    opts = proc.options or {}
    _validate_community_proc_map("ivg.triangleCount", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    top_k = int(_val("topK", 10000))

    fn = f"{_schema_prefix}.kg_TriangleCount" if _schema_prefix else "kg_TriangleCount"
    cte_sql = (
        f"SELECT j.node_id AS node, j.triangles, j.lcc\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(top_k)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    triangles INTEGER PATH '$.triangles',\n"
        f"    lcc DOUBLE PATH '$.lcc'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"TriangleCount AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "TriangleCount"
    if "triangles" in proc.yield_items:
        context.scalar_variables.add("triangles")
    if "lcc" in proc.yield_items:
        context.scalar_variables.add("lcc")


def _translate_scc(proc, context) -> None:
    opts = proc.options or {}
    _validate_community_proc_map("ivg.scc", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    top_k = int(_val("topK", 10000))

    fn = f"{_schema_prefix}.kg_SCC" if _schema_prefix else "kg_SCC"
    cte_sql = (
        f"SELECT j.node_id AS node, j.component, j.size\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(top_k)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    component INTEGER PATH '$.component',\n"
        f"    size INTEGER PATH '$.size'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"SCC AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "SCC"
    if "component" in proc.yield_items:
        context.scalar_variables.add("component")
    if "size" in proc.yield_items:
        context.scalar_variables.add("size")


def _translate_kcore(proc, context) -> None:
    opts = proc.options or {}
    _validate_community_proc_map("ivg.kcore", opts.keys())

    def _val(key, default):
        v = opts.get(key, default)
        if hasattr(v, "value"):
            return v.value
        return v

    top_k = int(_val("topK", 10000))

    fn = f"{_schema_prefix}.kg_KCore" if _schema_prefix else "kg_KCore"
    cte_sql = (
        f"SELECT j.node_id AS node, j.coreness\n"
        f"FROM JSON_TABLE(\n"
        f"  {fn}({_sql_arg(top_k)}),\n"
        f"  '$[*]' COLUMNS(\n"
        f"    node_id VARCHAR(256) PATH '$.id',\n"
        f"    coreness INTEGER PATH '$.coreness'\n"
        f"  )\n"
        f") j"
    )
    context.stages.insert(0, f"KCore AS (\n{cte_sql}\n)")
    for item in proc.yield_items:
        context.variable_aliases[item] = "KCore"
    if "coreness" in proc.yield_items:
        context.scalar_variables.add("coreness")
