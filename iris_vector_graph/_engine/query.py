import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.result import IVGResult
from iris_vector_graph._validate import CypherInput, KHop2Input

logger = logging.getLogger(__name__)


def _split_top_level_and(where_clause: str) -> list:
    """Split a SQL WHERE clause on top-level AND conjuncts only.

    Respects nesting depth: AND tokens inside parentheses (subqueries, EXISTS)
    are not treated as separators.  Returns a list of condition strings.
    """
    parts = []
    depth = 0
    buf = []
    i = 0
    text = where_clause
    while i < len(text):
        ch = text[i]
        if ch == '(':
            depth += 1
            buf.append(ch)
            i += 1
        elif ch == ')':
            depth -= 1
            buf.append(ch)
            i += 1
        elif depth == 0 and text[i:i+4].upper() == ' AND' and (i + 4 >= len(text) or text[i+4] == ' '):
            # Top-level AND separator
            part = ''.join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            i += 4  # skip ' AND'
        elif depth == 0 and text[i:i+3].upper() == 'AND' and i == 0:
            i += 3
        else:
            buf.append(ch)
            i += 1
    part = ''.join(buf).strip()
    if part:
        parts.append(part)
    return parts if parts else [where_clause]


# ---------------------------------------------------------------------------
# GDS → ivg procedure shim map
# Keys are lowercase gds.* procedure names; values are ivg.* equivalents.
# ---------------------------------------------------------------------------

GDS_SHIM_MAP: Dict[str, str] = {
    "gds.pagerank.stream":              "ivg.ppr",
    "gds.shortestpath.dijkstra.stream": "ivg.shortestPath.weighted",
    "gds.shortestpath.dijkstra":        "ivg.shortestPath.weighted",
    "gds.betweenness.stream":           "ivg.betweenness",
    "gds.louvain.stream":               "ivg.leiden",
    "gds.nodesimilarity.stream":        "ivg.vector.search",
}


def _handle_gds_shim(proc) -> Optional["IVGResult"]:
    """Handle a gds.* procedure call: shim to ivg.* or return an error result.

    Returns None for non-gds procedures (caller should continue normal dispatch).
    Returns an IVGResult for gds.* procedures (either shimmed result or error).
    """
    name = proc.procedure_name.lower()
    if not name.startswith("gds."):
        return None

    ivg_equiv = GDS_SHIM_MAP.get(name)
    if ivg_equiv is None:
        suggestions = ", ".join(sorted(set(GDS_SHIM_MAP.values())))
        return IVGResult(
            columns=["error"],
            rows=[],
            error=(
                f"gds procedure '{proc.procedure_name}' not shimmed; "
                f"use ivg equivalent. Available: {suggestions}"
            ),
        )

    # Build a shimmed proc object with the ivg equivalent name.
    # Return it as a tuple sentinel so the caller can re-dispatch.
    shimmed = type("ShimmedProc", (), {
        "procedure_name": ivg_equiv,
        "arguments": proc.arguments,
        "yield_items": getattr(proc, "yield_items", []),
    })()
    # (shimmed_proc, None) sentinel tuple — caller checks isinstance(result, tuple)
    return (shimmed, None)  # type: ignore[return-value]


def _build_path_func_columns(return_path_funcs: list, source_var: str, target_var: str,
                              col_map: dict, path_named_var: str = None) -> list:
    """Build ordered output column names for a labeled path function result.

    Uses col_map to find the RETURN aliases for path functions and node variables.
    Falls back to standard names if col_map is empty.
    """
    columns = []
    # col_map: {sql_alias -> cypher_expression}
    # e.g. {"l": "length(p)", "a": "a", "b": "b"}
    # Reverse map: cypher_expression -> sql_alias
    cypher_to_alias = {v: k for k, v in col_map.items()} if col_map else {}

    # Determine columns from col_map first (preserves RETURN order)
    if col_map:
        # We can't guarantee order from dict, so use return_path_funcs and var names
        # as guidance. But col_map is unordered — we need to infer from what funcs we have.
        added = set()
        # Check for source_var in col_map
        src_cypher = source_var
        if src_cypher in cypher_to_alias:
            alias = cypher_to_alias[src_cypher]
            columns.append(alias)
            added.add(alias)
        # Check for target_var in col_map
        tgt_cypher = target_var
        if tgt_cypher in cypher_to_alias:
            alias = cypher_to_alias[tgt_cypher]
            if alias not in added:
                columns.append(alias)
                added.add(alias)
        # Check for path functions
        for func in return_path_funcs:
            # Find alias for "length(p)", "relationships(p)", "nodes(p)"
            for cypher_expr, alias in col_map.items():
                if cypher_expr.lower().startswith(func + "(") and alias not in added:
                    columns.append(alias)
                    added.add(alias)
            # Check for col_map entries where alias == cypher_expr (no rename)
            func_expr = f"{func}(p)"
            if func_expr in col_map and col_map[func_expr] not in added:
                columns.append(col_map[func_expr])
                added.add(col_map[func_expr])
        # Add any remaining col_map entries
        for sql_alias, cypher_expr in col_map.items():
            if sql_alias not in added:
                columns.append(sql_alias)
                added.add(sql_alias)
    else:
        # No col_map: use standard names
        if "path" in return_path_funcs:
            columns.append(path_named_var or "p")
        if "nodes" in return_path_funcs or "length" in return_path_funcs:
            columns.append(source_var)
        if "nodes" in return_path_funcs or "length" in return_path_funcs:
            columns.append(target_var)
        if "length" in return_path_funcs:
            columns.append("l")
        if "relationships" in return_path_funcs:
            columns.append("relationships(p)")
        if "nodes" in return_path_funcs and "nodes(p)" not in columns:
            columns.append("nodes(p)")

    return columns if columns else [source_var, target_var]


class QueryMixin:
    def execute_aql(
        self,
        aql: str,
        bind_vars: Optional[Dict[str, Any]] = None,
    ) -> "IVGResult":
        from iris_vector_graph.cypher.aql import translate_aql
        cypher_query, params = translate_aql(aql, bind_vars or {})
        return self.execute_cypher(cypher_query, parameters=params)
    def execute_cypher(
        self, cypher_query: str, parameters: Dict[str, Any] = None,
        read_only: bool = False, procedures: Dict[str, Any] = None,
    ) -> "IVGResult":
        """
        Execute a Cypher query by translating it to IRIS SQL.

        Args:
            cypher_query: Cypher query string
            parameters: Optional query parameters
            read_only: If True, rejects any mutation (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH)
            procedures: Optional dict of test procedures (for TCK harness): {name: {args, outputs, rows}}

        Returns:
            Dict containing 'columns', 'rows', and 'metadata'
        """
        CypherInput(cypher_query=cypher_query)
        import re as _re_ec
        _APPROX_RE = _re_ec.compile(
            r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
            _re_ec.IGNORECASE,
        )
        _approx_m = _APPROX_RE.search(cypher_query)
        if _approx_m:
            return self._execute_approx_count_distinct(cypher_query, parameters, _approx_m)

        _fast = self._try_khop_fast_path(cypher_query, parameters)
        if _fast is not None:
            return _fast

        stripped = cypher_query.strip().upper()

        if "CALL DB.LABELS() YIELD" in stripped and "UNION" in stripped:
            labels = self._try_system_procedure(
                type("P", (), {"procedure_name": "db.labels"})()
            ).rows
            rels = self._try_system_procedure(
                type("P", (), {"procedure_name": "db.relationshipTypes"})()
            ).rows
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT DISTINCT TOP 1000 "key" FROM Graph_KG.rdf_props ORDER BY "key"'
            )
            prop_keys = [r[0] for r in cursor.fetchall()]
            return IVGResult(                columns= ["result"],
                rows= [
                    [{"name": "labels", "data": [r[0] for r in labels]}],
                    [{"name": "relationshipTypes", "data": [r[0] for r in rels]}],
                    [{"name": "propertyKeys", "data": prop_keys}],
                ]
            )

        if (
            "RETURN DISTINCT" in stripped
            and "UNION ALL" in stripped
            and "ENTITY" in stripped
        ):
            cursor = self.conn.cursor()
            cursor.execute("SELECT TOP 25 node_id FROM Graph_KG.nodes")
            node_rows = [["node", r[0]] for r in cursor.fetchall()]
            cursor.execute("SELECT DISTINCT TOP 25 p FROM Graph_KG.rdf_edges")
            rel_rows = [["relationship", r[0]] for r in cursor.fetchall()]
            return IVGResult(columns=["entity", "id"], rows=node_rows + rel_rows)

        if (
            "MATCH ()" in stripped
            and "COUNT(*)" in stripped
            and "UNION ALL" in stripped
        ):
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
            node_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
            edge_count = cursor.fetchone()[0]
            return IVGResult(                columns= ["result"],
                rows= [
                    [{"name": "nodes", "data": node_count}],
                    [{"name": "relationships", "data": edge_count}],
                ]
            )

        if ";" in cypher_query and "CALL " in cypher_query.upper():
            parts = [p.strip() for p in cypher_query.split(";") if p.strip()]
            if len(parts) > 1:
                all_rows = []
                all_cols = None
                for part in parts:
                    try:
                        sub = self.execute_cypher(part, parameters=parameters)
                        if all_cols is None:
                            all_cols = sub.columns
                        all_rows.extend(sub.rows)
                    except Exception:
                        pass
                return IVGResult(columns=all_cols or ["result"], rows=all_rows)

        if stripped.startswith("EXPLAIN "):
            return IVGResult(                columns= ["Plan"],
                rows= [["No execution plan available (IRIS backend)"]]
            )

        if stripped.startswith("SHOW "):
            return self._handle_show_command(stripped)

        if (stripped.startswith("CREATE CONSTRAINT")
                or stripped.startswith("DROP CONSTRAINT")
                or stripped.startswith("CREATE INDEX")
                or stripped.startswith("CREATE TEXT INDEX")
                or stripped.startswith("CREATE RANGE INDEX")
                or stripped.startswith("CREATE POINT INDEX")
                or stripped.startswith("DROP INDEX")
                or stripped.startswith("CREATE FULLTEXT")
                or stripped.startswith("CREATE LOOKUP")):
            return IVGResult(columns=[], rows=[], sql=cypher_query, params=[])

        parsed = parse_query(cypher_query)

        self._reconnect_if_stale()

        if read_only and parsed.is_mutation:
            raise PermissionError(
                f"Read-only mode: mutation queries (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH) "
                f"are not allowed. Query: {cypher_query[:100]}"
            )

        if parsed.subsequent_queries:
            result = None
            current_params = dict(parameters) if parameters else {}
            for part_query in [parsed] + parsed.subsequent_queries:
                part_query.subsequent_queries = []
                result = self._execute_parsed(part_query, current_params, procedures)
                if result and result.rows and result.columns:
                    first_row = result["rows"][0] if result["rows"] else []
                    for col, val in zip(result["columns"], first_row):
                        if isinstance(val, (str, int, float, bool, type(None))):
                            current_params[col] = val
            return result

        return self._execute_parsed(parsed, parameters, procedures)
    def _execute_parsed(self, parsed, parameters, procedures=None):
        if parsed.procedure_call is not None:
            result = self._try_system_procedure(parsed.procedure_call)
            if result is not None:
                return result
        sql_query = translate_to_sql(parsed, parameters, engine=self, procedures=procedures)
        if sql_query.var_length_paths:
            return self._route_var_length(sql_query, parameters)
        metadata = sql_query.query_metadata
        if sql_query.is_transactional:
            result = self._store.execute_transaction(sql_query.sql, sql_query.parameters)
            result.metadata = metadata
            if sql_query.column_name_map and result.columns:
                result.columns = [
                    sql_query.column_name_map.get(col, col) for col in result.columns
                ]
            return result
        if self._store_capabilities.get("native_sql", True):
            sql_str = sql_query.sql
            p = sql_query.parameters[0] if sql_query.parameters else []
            result = self._store.execute_sql(sql_str, p)
            result.metadata = metadata
            if sql_query.bolt_column_types:
                result.bolt_column_types = sql_query.bolt_column_types
            if sql_query.column_name_map and result.columns:
                result.columns = [
                    sql_query.column_name_map.get(col, col) for col in result.columns
                ]
            return result
        traversal = self._extract_traversal(parsed, parameters)
        if traversal is not None:
            return self._execute_traversal(traversal, sql_query, parsed, parameters)
        label_filter = None
        return_props = None
        limit = 0
        try:
            if parsed.query_parts:
                clause = parsed.query_parts[0].clauses[0]
                if hasattr(clause, "patterns") and clause.patterns:
                    node = clause.patterns[0].nodes[0] if clause.patterns[0].nodes else None
                    if node and node.labels:
                        label_filter = node.labels[0]
            if parsed.return_clause:
                return_props = [
                    item.expression.property_name
                    for item in parsed.return_clause.items
                    if hasattr(item.expression, "property_name")
                ]
            if parsed.limit:
                limit = int(parsed.limit)
        except Exception:
            pass
        return self._store.query_nodes(
            label_filter=label_filter,
            property_filters=None,
            return_properties=return_props,
            limit=limit,
        )
    def _extract_traversal(self, parsed, parameters):
        from iris_vector_graph.cypher.ast import Direction
        try:
            clause = parsed.query_parts[0].clauses[0]
            if not (hasattr(clause, "patterns") and clause.patterns):
                return None
            pat = clause.patterns[0]
            if len(pat.nodes) < 2 or len(pat.relationships) < 1:
                return None
            rel = pat.relationships[0]
            if rel.variable_length is not None:
                return None
            src_node = pat.nodes[0]
            src_id = None
            if src_node.properties:
                for k, v in src_node.properties.items():
                    if k == "id":
                        if isinstance(v, str) and v.startswith("$"):
                            src_id = parameters.get(v[1:])
                        elif hasattr(v, 'name'):
                            src_id = parameters.get(v.name)
                        elif isinstance(v, str):
                            src_id = v
                        else:
                            src_id = str(v)
                        break
            if src_id is None:
                return None
            direction_map = {Direction.OUTGOING: "out", Direction.INCOMING: "in", Direction.BOTH: "both"}
            is_count = bool(
                parsed.return_clause and
                any(hasattr(item.expression, "function_name") and
                    item.expression.function_name.upper() == "COUNT"
                    for item in parsed.return_clause.items)
            )
            return {
                "source_id": str(src_id),
                "predicates": rel.types or [],
                "direction": direction_map.get(rel.direction, "out"),
                "is_count": is_count,
                "return_col": (
                    (parsed.return_clause.items[0].alias or "count") if is_count
                    else (parsed.return_clause.items[0].alias or "id") if (parsed.return_clause and parsed.return_clause.items)
                    else "id"
                ),
            }
        except Exception:
            return None
    def _execute_traversal(self, traversal, sql_query, parsed, parameters):
        raw = self._store.execute_bfs(
            traversal["source_id"],
            traversal["predicates"],
            1,
            traversal["direction"],
            0,
        )
        if isinstance(raw, list):
            rows = [[r.get("node_id", r.get("id", "")), r.get("hops", 1)] for r in raw]
        else:
            rows = raw.rows if not raw.error else []
        if traversal["is_count"]:
            return IVGResult(columns=[traversal["return_col"]], rows=[[len(rows)]], metadata=sql_query.query_metadata)
        return IVGResult(columns=[traversal["return_col"]], rows=[[r[0]] for r in rows], metadata=sql_query.query_metadata)
    def _route_var_length(self, sql_query, parameters):
        if self._nkg_dirty:
            from iris_vector_graph.errors import IndexNotSyncedError
            raise IndexNotSyncedError()
        vl0 = sql_query.var_length_paths[0]
        if vl0.get("weighted"):
            return self._execute_weighted_shortest_path(sql_query, parameters)
        if vl0.get("shortest") or vl0.get("all_shortest"):
            return self._execute_shortest_path_cypher(sql_query, parameters)

        # Resolve source_id from explicit id parameter or query parameters
        source_id = None
        src_id_param = vl0.get("src_id_param")
        if src_id_param:
            if isinstance(src_id_param, str) and src_id_param.startswith("$"):
                pname = src_id_param[1:]
                if parameters and pname in parameters:
                    source_id = str(parameters[pname])
            elif src_id_param:
                source_id = str(src_id_param)
        if source_id is None and parameters:
            src_var = vl0.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])

        # When source is not ID-bound, try to resolve from SQL parameters before
        # falling back to labeled multi-source traversal.
        source_labels = vl0.get("source_labels") or []
        if source_id is None and src_id_param is None:
            # Fallback: extract source_id from the first SQL param that looks like
            # a node ID (non-schema-prefix string). Covers WHERE a.node_id = $src
            # patterns where the translator doesn't set src_id_param.
            params_list = sql_query.parameters[0] if sql_query.parameters else []
            for item in params_list:
                if isinstance(item, str) and not item.startswith("Graph_KG"):
                    source_id = item
                    break
            if source_id is None and parameters:
                source_id = next(iter(parameters.values()), None)
                if source_id is not None:
                    source_id = str(source_id)

        # When source is not ID-bound, use labeled multi-source traversal.
        # This covers two cases:
        #   (a) source labeled in this pattern: source_labels is populated
        #   (b) source bound in a prior MATCH: source_labels=[] but source_alias
        #       is present in the SQL with label joins we can query.
        if source_id is None and src_id_param is None:
            # When path functions (length/nodes/relationships) are in RETURN,
            # use dedicated method that tracks (source, target, hop) triples.
            if vl0.get("return_path_funcs"):
                return self._execute_var_length_labeled_path_funcs(sql_query, parameters, vl0)
            return self._execute_var_length_labeled(sql_query, parameters, vl0)

        if vl0.get("min_hops", 1) > 1 or vl0.get("properties") or vl0.get("return_path_funcs"):
            return self._execute_var_length_cypher(sql_query, parameters)

        import re as _re
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else (sql_query.sql[0] if sql_query.sql else "")
        count_match = _re.search(r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)

        # Fallback source_id resolution from SQL parameters
        if source_id is None:
            params = sql_query.parameters[0] if sql_query.parameters else []
            for item in params:
                if isinstance(item, str) and not item.startswith("Graph_KG"):
                    source_id = item
                    break
        if source_id is None and parameters:
            source_id = next(iter(parameters.values()), None)

        if source_id is None:
            return IVGResult(columns=[], rows=[], sql="", params=[], metadata=sql_query.query_metadata)

        predicates = vl0.get("types", [])
        max_hops = vl0.get("max_hops", 5)
        direction = vl0.get("direction", "out")
        def _extract_limit(s: str) -> int:
            # IRIS SQL uses FETCH FIRST N ROWS ONLY; the build-106 %qaqpre workaround
            # emits SELECT TOP N instead; fall back to LIMIT N.
            m = _re.search(r"FETCH\s+FIRST\s+(\d+)\s+ROWS\s+ONLY", s, _re.IGNORECASE)
            if not m:
                m = _re.search(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s+(\d+)\b", s, _re.IGNORECASE)
            if not m:
                m = _re.search(r"\bLIMIT\s+(\d+)", s, _re.IGNORECASE)
            return int(m.group(1)) if m else 0

        max_results = _extract_limit(sql_str) if sql_str else 0

        if count_match:
            col_name = count_match.group(1)
            bfs_result = self._store.execute_bfs(source_id, predicates, max_hops, direction, 0)
            cnt = len(bfs_result.rows) if not bfs_result.error else 0
            return IVGResult(columns=[col_name], rows=[[cnt]], metadata=sql_query.query_metadata)
        max_results = _extract_limit(sql_str) if sql_str else 0

        direction = vl0.get("direction", "out")
        predicates = vl0.get("types", [])
        max_hops = vl0.get("max_hops", 5)

        if vl0.get("temporal_window"):
            ts_start = vl0.get("ts_start", 0)
            ts_end = vl0.get("ts_end", 9999999999)
            result = self._store.execute_temporal_cypher(
                source_id, predicates, ts_start, ts_end, direction, max_hops
            )
        else:
            result = self._store.execute_bfs(source_id, predicates, max_hops, direction, max_results)

        return_properties = getattr(sql_query.query_metadata, "return_properties", None)
        if return_properties and result.rows:
            node_ids = [row[0] for row in result.rows if row]
            if node_ids:
                props_result = self._store.get_nodes(node_ids, return_properties)
                props_by_id = {r[0]: r[2:] for r in props_result.rows}
                enriched = [[r[0], r[1]] + list(props_by_id.get(r[0], [None] * len(return_properties))) for r in result.rows]
                result = IVGResult(
                    columns=result.columns + return_properties,
                    rows=enriched,
                    metadata=result.metadata,
                )
        return result

    def _execute_var_length_labeled_path_funcs(self, sql_query, parameters, vl0) -> "IVGResult":
        """Execute a var-length path query with path functions (length/nodes/relationships)
        where the source is identified by label(s) rather than a bound node ID.

        Unlike _execute_var_length_labeled (which only returns target node data), this
        method tracks full (source, target, hop) triples and assembles results for
        path function RETURN columns.
        """
        import re as _re
        import json as _json

        source_labels = vl0.get("source_labels") or []
        target_labels = vl0.get("target_labels") or []
        predicates = vl0.get("types") or []
        min_hops = vl0.get("min_hops", 1)
        max_hops = vl0.get("max_hops", 10)
        direction = vl0.get("direction", "out")
        source_var = vl0.get("source_var") or "a"
        target_var = vl0.get("target_var") or "b"
        source_alias = vl0.get("source_alias") or ""
        target_alias = vl0.get("target_alias") or ""
        return_path_funcs = vl0.get("return_path_funcs") or []
        path_named_var = vl0.get("path_named_var")
        col_map = sql_query.column_name_map or {}
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else ""

        # Step 1: Collect source node IDs (same logic as _execute_var_length_labeled)
        source_ids: list = []
        if source_labels:
            label_sets = []
            for lbl in source_labels:
                try:
                    lbl_result = self._store.query_nodes(label_filter=lbl)
                    label_sets.append({row[0] for row in lbl_result.rows if row and row[0]})
                except Exception as exc:
                    logger.debug("label lookup failed for %s: %s", lbl, exc)
            if label_sets:
                common = label_sets[0]
                for s in label_sets[1:]:
                    common = common & s
                source_ids = list(common)
        elif source_alias and target_alias:
            # Source bound in prior MATCH: extract IDs from SQL
            cartesian_pat = _re.compile(
                r'\bJOIN\s+\S+\s+' + _re.escape(target_alias) + r'\s+ON\s+1\s*=\s*1\b',
                _re.IGNORECASE,
            )
            m = cartesian_pat.search(sql_str)
            if m:
                from_start = sql_str.find('\nFROM ')
                if from_start == -1:
                    from_start = sql_str.lower().find('\nfrom ')
                src_portion = sql_str[from_start:m.start()].strip()
                src_query = f"SELECT DISTINCT {source_alias}.node_id {src_portion}"
                # Collect all SQL aliases defined AFTER the Cartesian JOIN boundary
                post_boundary_sql = sql_str[m.start():]
                post_aliases = set(_re.findall(
                    r'\bJOIN\s+\S+\s+(\w+)\s+ON\b',
                    post_boundary_sql,
                    _re.IGNORECASE,
                ))
                post_aliases.add(target_alias)
                # Fixed WHERE regex: |$ outside the \n group so end-of-string matches
                where_m = _re.search(r'\nWHERE\s+(.*?)(?:\n(?:ORDER|HAVING|GROUP)|$)', sql_str, _re.DOTALL)
                if where_m:
                    where_raw = where_m.group(1).strip()
                    # Split on top-level AND (not inside EXISTS or other subqueries)
                    all_conds = _split_top_level_and(where_raw)
                    src_conds = []
                    for c in all_conds:
                        c = c.strip()
                        if not c:
                            continue
                        if any(_re.search(r'\b' + _re.escape(pa) + r'\b', c) for pa in post_aliases):
                            post_where_conds.append(c)
                        else:
                            src_conds.append(c)
                    if src_conds:
                        src_query += "\nWHERE " + " AND ".join(src_conds)
                params_list = sql_query.parameters[0] if sql_query.parameters else []
                src_param_count = src_query.count("?")
                src_params = list(params_list[:src_param_count])
                post_params = list(params_list[src_param_count:])
                try:
                    cursor = self._store.conn.cursor()
                    cursor.execute(src_query, src_params)
                    for row in cursor.fetchall():
                        if row and row[0]:
                            source_ids.append(row[0])
                except Exception as exc:
                    logger.debug("Source ID extraction query failed: %s", exc)

        if not source_ids:
            columns = _build_path_func_columns(return_path_funcs, source_var, target_var, col_map, path_named_var)
            return IVGResult(columns=columns, rows=[], metadata=sql_query.query_metadata)

        # Step 2: BFS from each source — collect ALL (src, tgt, hop) triples
        # For min_hops=0, include 0-hop self-pairs
        path_triples: list = []  # [(src_id, tgt_id, hop)]
        # full_paths: list of (node_list, edge_type_list) — only populated when "path" is returned
        full_paths: list = []
        need_full_paths = "path" in return_path_funcs

        for src_id in source_ids:
            if min_hops == 0:
                path_triples.append((src_id, src_id, 0))
                if need_full_paths:
                    full_paths.append(([src_id], []))
            if need_full_paths:
                # Use _bfs_with_paths to get full node/edge sequence per path
                try:
                    for node_list, edge_list in self._bfs_with_paths(
                        src_id, predicates, max_hops, direction
                    ):
                        if not node_list:
                            continue
                        hop = len(edge_list)
                        tgt_id = node_list[-1]
                        if min_hops <= hop <= max_hops:
                            path_triples.append((src_id, tgt_id, hop))
                            full_paths.append((node_list, edge_list))
                except Exception as exc:
                    logger.debug("BFS with paths failed for %s: %s", src_id, exc)
            else:
                try:
                    bfs_result = self._store.execute_bfs(src_id, predicates, max_hops, direction, 0)
                    if bfs_result and not getattr(bfs_result, "error", False):
                        for row in bfs_result.rows:
                            tgt_id = row[0] if row else None
                            hop = row[1] if len(row) > 1 else 1
                            if tgt_id and min_hops <= hop <= max_hops:
                                path_triples.append((src_id, tgt_id, hop))
                except Exception as exc:
                    logger.debug("BFS failed for %s: %s", src_id, exc)

        # Step 3: Filter by target_labels if specified
        if target_labels and path_triples:
            all_labeled = set()
            for lbl in target_labels:
                try:
                    lbl_result = self._store.query_nodes(label_filter=lbl)
                    labeled_ids = {row[0] for row in lbl_result.rows if row}
                    all_labeled |= labeled_ids
                except Exception as exc:
                    logger.debug("target label lookup failed for %s: %s", lbl, exc)
            if all_labeled:
                if need_full_paths and full_paths:
                    filtered = [(t, fp) for (s, t, h), fp in zip(path_triples, full_paths)
                                if t in all_labeled]
                    path_triples = [(s, t, h) for (s, t, h) in path_triples
                                    if t in all_labeled]
                    full_paths = [fp for (_, fp) in filtered]
                else:
                    path_triples = [(s, t, h) for s, t, h in path_triples if t in all_labeled]

        if not path_triples:
            columns = _build_path_func_columns(return_path_funcs, source_var, target_var, col_map, path_named_var)
            return IVGResult(columns=columns, rows=[], metadata=sql_query.query_metadata)

        # Step 4: Fetch node data for all source and target IDs (+ intermediates for full paths)
        if need_full_paths and full_paths:
            all_node_ids = list({nid for nl, _ in full_paths for nid in nl})
        else:
            all_node_ids = list({nid for s, t, h in path_triples for nid in (s, t)})
        node_data_map: dict = {}  # node_id -> {"_id": ..., "_labels": ..., "_props": ...}
        try:
            nodes_result = self._store.query_nodes()
            # query_nodes() may be expensive; use targeted fetch
            from iris_vector_graph.schema import _call_classmethod as _ccm
            schema = getattr(self._store, '_schema_prefix', 'Graph_KG')
            placeholders = ", ".join("?" * len(all_node_ids))
            cursor = self._store.conn.cursor()
            cursor.execute(
                f"SELECT n.node_id, "
                f"COALESCE((SELECT JSON_ARRAYAGG(label) FROM {schema}.rdf_labels WHERE s = n.node_id), '[]'), "
                f"(SELECT JSON_ARRAYAGG('{{\"key\":\"' || REPLACE(REPLACE(\"key\", '\\\\', '\\\\\\\\'), '\"', '\\\\\"') || '\",\"value\":\"' || REPLACE(REPLACE(val, '\\\\', '\\\\\\\\'), '\"', '\\\\\"') || '\"}}') FROM {schema}.rdf_props WHERE s = n.node_id) "
                f"FROM {schema}.nodes n WHERE n.node_id IN ({placeholders})",
                all_node_ids,
            )
            for row in cursor.fetchall():
                nid, labels_json, props_json = row
                node_data_map[nid] = {
                    "_id": nid,
                    "_labels": labels_json or "[]",
                    "_props": props_json,
                }
            cursor.close()
        except Exception as exc:
            logger.debug("Node data fetch failed: %s", exc)
            for nid in all_node_ids:
                if nid not in node_data_map:
                    node_data_map[nid] = {"_id": nid, "_labels": "[]", "_props": None}

        # Step 5: Build result rows
        def _get_node(nid):
            return node_data_map.get(nid, {"_id": nid, "_labels": "[]", "_props": None})

        def _get_edge_rels(src_id, tgt_id, hop, predicates):
            """Get ordered relationship list for a path from src to tgt in `hop` steps.
            Returns list of formatted relationship strings like ':TYPE {key: val}'.
            """
            if hop == 0:
                return []
            schema = getattr(self._store, '_schema_prefix', 'Graph_KG')
            preds_clause = ""
            if predicates:
                preds_ph = ", ".join("?" * len(predicates))
                preds_clause = f" AND p IN ({preds_ph})"

            # For 1-hop: direct edge
            if hop == 1:
                try:
                    cursor = self._store.conn.cursor()
                    cursor.execute(
                        f"SELECT p, qualifiers FROM {schema}.rdf_edges "
                        f"WHERE s = ? AND o_id = ?{preds_clause}",
                        [src_id, tgt_id] + list(predicates),
                    )
                    row = cursor.fetchone()
                    cursor.close()
                    if row:
                        rel_type, qualifiers = row
                        return [_format_rel(rel_type, qualifiers)]
                except Exception as exc:
                    logger.debug("Edge fetch 1-hop failed: %s", exc)
                return []

            # For multi-hop: walk path hop by hop using ShortestPathJson
            # ShortestPathJson returns nodes + rel type list (no props)
            # Then fetch qualifiers per hop
            try:
                import json as _j
                pred_json = _j.dumps(predicates) if predicates else ""
                from iris_vector_graph.schema import _call_classmethod as _ccm2
                path_json_str = str(_ccm2(
                    self._store.conn, "Graph.KG.Traversal", "ShortestPathJson",
                    src_id, tgt_id, str(hop), pred_json, direction, "0",
                ))
                paths = _j.loads(path_json_str) if path_json_str else []
                if isinstance(paths, dict):
                    paths = [paths]
                if not paths:
                    return []
                path = paths[0]
                nodes_in_path = path.get("nodes", [])
                rels_in_path = path.get("rels", [])  # list of type strings

                result_rels = []
                for i, rel_type in enumerate(rels_in_path):
                    s_node = nodes_in_path[i] if i < len(nodes_in_path) else None
                    t_node = nodes_in_path[i + 1] if i + 1 < len(nodes_in_path) else None
                    qualifiers = None
                    if s_node and t_node:
                        try:
                            cursor = self._store.conn.cursor()
                            cursor.execute(
                                f"SELECT qualifiers FROM {schema}.rdf_edges "
                                f"WHERE s = ? AND p = ? AND o_id = ?",
                                [s_node, rel_type, t_node],
                            )
                            qrow = cursor.fetchone()
                            cursor.close()
                            if qrow:
                                qualifiers = qrow[0]
                        except Exception:
                            pass
                    result_rels.append(_format_rel(rel_type, qualifiers))
                return result_rels
            except Exception as exc:
                logger.debug("Edge fetch multi-hop failed: %s", exc)
                return []

        def _format_rel(rel_type, qualifiers_json):
            """Format a relationship as ':TYPE {key: val, ...}' string."""
            if not qualifiers_json:
                return f":{rel_type}"
            try:
                import json as _j
                props = _j.loads(qualifiers_json) if isinstance(qualifiers_json, str) else qualifiers_json
                if isinstance(props, dict) and props:
                    parts = []
                    for k, v in props.items():
                        try:
                            parts.append(f"{k}: {int(v)}")
                        except (ValueError, TypeError):
                            parts.append(f"{k}: {v}")
                    return f":{rel_type} {{{', '.join(parts)}}}"
            except Exception:
                pass
            return f":{rel_type}"

        # Determine output columns from RETURN clause and col_map
        columns = _build_path_func_columns(return_path_funcs, source_var, target_var, col_map, path_named_var)

        path_var_col = path_named_var or "p"
        rows_out = []
        path_iter = iter(full_paths) if (need_full_paths and full_paths) else None

        for src_id, tgt_id, hop in path_triples:
            fp_nodes, fp_edges = (next(path_iter) if path_iter else (None, None))
            row = []
            for col in columns:
                cypher_col = col_map.get(col, col) if col_map else col
                if "path" in return_path_funcs and (col == path_var_col or cypher_col == path_var_col):
                    # Build path JSON object: {"nodes": [...node_data...], "rels": [...edge_types...]}
                    if fp_nodes:
                        import json as _pj
                        path_json = _pj.dumps({
                            "nodes": [_get_node(n) for n in fp_nodes],
                            "rels": list(fp_edges),
                        })
                    else:
                        import json as _pj
                        path_json = _pj.dumps({
                            "nodes": [_get_node(src_id), _get_node(tgt_id)],
                            "rels": [],
                        })
                    row.append(path_json)
                elif cypher_col == source_var or col == source_var:
                    row.append(_get_node(src_id))
                elif cypher_col == target_var or col == target_var:
                    row.append(_get_node(tgt_id))
                elif cypher_col in ("length(p)", f"length({cypher_col})") or col == "l" and "length" in return_path_funcs:
                    row.append(hop)
                elif "length" in return_path_funcs and col in ("l", "length", "length(p)"):
                    row.append(hop)
                elif "relationships" in return_path_funcs and "relationship" in col.lower():
                    rels = _get_edge_rels(src_id, tgt_id, hop, predicates)
                    row.append(rels)
                elif "nodes" in return_path_funcs and "node" in col.lower():
                    row.append([_get_node(n) for n in (fp_nodes if fp_nodes else [src_id, tgt_id])])
                else:
                    row.append(None)
            rows_out.append(row)

        return IVGResult(
            columns=columns,
            rows=rows_out,
            metadata=sql_query.query_metadata,
        )

    def _execute_var_length_labeled(self, sql_query, parameters, vl0) -> "IVGResult":
        """Execute a variable-length path where source is identified by label(s).

        Handles two cases:
        1. Source labeled in same MATCH: source_labels is populated → use query_nodes
        2. Source bound in prior MATCH clause: source_labels is empty but the SQL
           FROM/JOIN clauses correctly filter source nodes → extract source IDs by
           running a trimmed version of the SQL up to the Cartesian JOIN boundary.

        Steps:
        1. Collect source node IDs (via label lookup OR SQL-based extraction).
        2. Run BFS from each source node.
        3. Deduplicate target node IDs, applying min_hops filter.
        4. Filter target nodes by target_labels if specified.
        5. Fetch requested properties and return result shaped to the SQL SELECT.
        """
        import re as _re

        source_labels = vl0.get("source_labels") or []
        target_labels = vl0.get("target_labels") or []
        predicates = vl0.get("types") or []
        min_hops = vl0.get("min_hops", 1)
        max_hops = vl0.get("max_hops", 10)
        direction = vl0.get("direction", "out")
        target_var = vl0.get("target_var") or "c"
        source_alias = vl0.get("source_alias") or ""
        target_alias = vl0.get("target_alias") or ""
        rel_var = vl0.get("rel_var")
        is_optional = vl0.get("optional", False)

        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else ""

        # Determine result shape from column_name_map
        col_map = sql_query.column_name_map or {}
        is_count = any(
            v.lower().startswith("count(") for v in col_map.values()
        ) or bool(_re.search(r'SELECT\s+COUNT\s*\(', sql_str, _re.IGNORECASE))

        # Build return_props list: [(cypher_expr, prop_key)] for the target variable
        # Use cypher_expr (e.g. "c.name") as the output column name, not the SQL alias
        # (e.g. "c_name"), so that TCK column comparison works correctly.
        return_props = []
        for sql_alias, cypher_expr in col_map.items():
            if "." in cypher_expr:
                var_name, prop_key = cypher_expr.split(".", 1)
                if var_name == target_var:
                    return_props.append((cypher_expr, prop_key))

        out_cols = [col for col, _ in return_props] if return_props else [target_var]

        # Detect whether the SQL SELECT uses the node-triple pattern for target_var:
        # "x_id, x_labels, x_props" — used by _remap_node_columns for node comparison.
        # If the SQL has this pattern, we must return those three columns with proper data.
        _id_col = f"{target_var}_id"
        _labels_col = f"{target_var}_labels"
        _props_col = f"{target_var}_props"
        _node_triple_in_sql = (
            _id_col in sql_str
            and _labels_col in sql_str
            and _props_col in sql_str
        )
        # Also detect source variable triple columns in the SQL for source var returns
        source_var = vl0.get("source_var")
        _src_id_col = f"{source_var}_id" if source_var else None
        _src_labels_col = f"{source_var}_labels" if source_var else None
        _src_props_col = f"{source_var}_props" if source_var else None
        _src_triple_in_sql = (
            source_var
            and _src_id_col in sql_str
            and _src_labels_col in sql_str
            and _src_props_col in sql_str
        )

        # Detect rel_var column in SQL (NULL AS r sentinel)
        _rel_col = rel_var
        _rel_in_sql = bool(
            _rel_col and _re.search(
                r'\bNULL\s+AS\s+' + _re.escape(_rel_col) + r'\b',
                sql_str,
                _re.IGNORECASE,
            )
        )

        # Step 1: Collect source node IDs
        # Also track post-boundary conditions for target property filtering.
        source_ids: list = []
        post_where_conds: list = []
        post_params: list = []
        if source_labels:
            # Case 1: Source labeled in this MATCH pattern → use query_nodes per label
            # Multiple labels use AND semantics: intersect the sets
            label_sets = []
            for lbl in source_labels:
                try:
                    lbl_result = self._store.query_nodes(label_filter=lbl)
                    label_sets.append({row[0] for row in lbl_result.rows if row and row[0]})
                except Exception as exc:
                    logger.debug("label lookup failed for %s: %s", lbl, exc)
            if label_sets:
                common = label_sets[0]
                for s in label_sets[1:]:
                    common = common & s
                source_ids = list(common)
        elif source_alias and target_alias:
            # Case 2: Source bound in prior MATCH — extract IDs by running a trimmed
            # version of the SQL that stops before the Cartesian JOIN on target_alias.
            # Pattern: "JOIN nodes {target_alias} ON 1=1" marks the boundary.
            cartesian_pat = _re.compile(
                r'\bJOIN\s+\S+\s+' + _re.escape(target_alias) + r'\s+ON\s+1\s*=\s*1\b',
                _re.IGNORECASE,
            )
            m = cartesian_pat.search(sql_str)
            if m:
                # Build: SELECT DISTINCT {source_alias}.node_id FROM ... (source joins only)
                # Use the FROM clause up to but not including the Cartesian JOIN
                from_start = sql_str.find('\nFROM ')
                if from_start == -1:
                    from_start = sql_str.lower().find('\nfrom ')
                src_portion = sql_str[from_start:m.start()].strip()
                src_query = f"SELECT DISTINCT {source_alias}.node_id {src_portion}"
                # Collect all SQL aliases defined AFTER the Cartesian JOIN boundary
                # (including target_alias itself and any dependent joins like l4, p5).
                # These must be excluded from the WHERE clause for the source query.
                post_boundary_sql = sql_str[m.start():]
                # Extract aliases: patterns like "JOIN ... alias ON" or "FROM ... alias"
                post_aliases = set(_re.findall(
                    r'\bJOIN\s+\S+\s+(\w+)\s+ON\b',
                    post_boundary_sql,
                    _re.IGNORECASE,
                ))
                post_aliases.add(target_alias)
                # WHERE clause for source: keep only conditions that reference no
                # post-boundary aliases.  This fixes the case where the isolation
                # label JOIN for the target (e.g. l4 which JOINs on n3.node_id) leaks
                # its IS NOT NULL condition into the source query.
                where_m = _re.search(r'\nWHERE\s+(.*?)(?:\n(?:ORDER|HAVING|GROUP)|$)', sql_str, _re.DOTALL)
                if where_m:
                    where_raw = where_m.group(1).strip()
                    # Split on top-level AND only (not AND inside subqueries/parens)
                    src_conds = []
                    for c in _split_top_level_and(where_raw):
                        c = c.strip()
                        if not c:
                            continue
                        if any(_re.search(r'\b' + _re.escape(pa) + r'\b', c) for pa in post_aliases):
                            post_where_conds.append(c)
                        else:
                            src_conds.append(c)
                    if src_conds:
                        src_query += "\nWHERE " + " AND ".join(src_conds)
                # Use just the source-related params (those before target_alias params)
                params_list = sql_query.parameters[0] if sql_query.parameters else []
                # Count '?' in src_query to determine how many params to use
                src_param_count = src_query.count("?")
                src_params = list(params_list[:src_param_count])
                post_params = list(params_list[src_param_count:])
                try:
                    cursor = self._store.conn.cursor()
                    cursor.execute(src_query, src_params)
                    for row in cursor.fetchall():
                        if row and row[0]:
                            source_ids.append(row[0])
                except Exception as exc:
                    logger.debug("Source ID extraction query failed: %s", exc)

        if not source_ids:
            if is_count:
                col_name = next(iter(col_map.keys()), "count")
                return IVGResult(columns=[col_name], rows=[[0]], metadata=sql_query.query_metadata)
            return IVGResult(columns=out_cols, rows=[], metadata=sql_query.query_metadata)

        # Step 2: BFS from each source node.
        # When rel_var is requested, use path-tracking BFS to collect edge sequences.
        # Otherwise, collect unique (node_id, min_hop) pairs.
        min_hop_per_node: dict = {}
        path_edges_by_target: dict = {}  # target_id → list of edge-type lists per path

        if min_hops == 0:
            for src_id in source_ids:
                min_hop_per_node[src_id] = 0
                if _rel_in_sql:
                    path_edges_by_target.setdefault(src_id, []).append([])

        for src_id in source_ids:
            try:
                if _rel_in_sql:
                    # Path-tracking BFS to reconstruct edge sequences for RETURN r
                    paths = self._bfs_with_paths(src_id, predicates, max_hops, direction)
                    for path_nodes, path_edges in paths:
                        if not path_nodes:
                            continue
                        target_id = path_nodes[-1]
                        hop = len(path_nodes) - 1
                        if hop < min_hops or hop > max_hops:
                            continue
                        existing = min_hop_per_node.get(target_id)
                        if existing is None or hop < existing:
                            min_hop_per_node[target_id] = hop
                        path_edges_by_target.setdefault(target_id, []).append(path_edges)
                else:
                    bfs_result = self._store.execute_bfs(src_id, predicates, max_hops, direction, 0)
                    if bfs_result and not getattr(bfs_result, "error", False):
                        for row in bfs_result.rows:
                            nid = row[0] if row else None
                            hop = row[1] if len(row) > 1 else 1
                            if nid:
                                existing = min_hop_per_node.get(nid)
                                if existing is None or hop < existing:
                                    min_hop_per_node[nid] = hop
            except Exception as exc:
                logger.debug("BFS failed from %s: %s", src_id, exc)

        # Filter by min_hops and collect in-order unique target IDs
        target_ids = [
            nid for nid, hop in min_hop_per_node.items()
            if hop >= min_hops
        ]

        # Step 3: Filter target nodes by target_labels (AND semantics per label)
        if target_labels and target_ids:
            for lbl in target_labels:
                try:
                    lbl_result = self._store.query_nodes(label_filter=lbl)
                    labeled_ids = {row[0] for row in lbl_result.rows if row}
                    target_ids = [nid for nid in target_ids if nid in labeled_ids]
                except Exception as exc:
                    logger.debug("target label lookup failed for %s: %s", lbl, exc)

        # Step 4: Filter target nodes by post-boundary WHERE conditions (target props)
        if post_where_conds and target_ids and target_alias:
            target_ids = self._filter_nodes_by_post_where(
                target_ids, target_alias, post_where_conds, post_params, sql_str
            )

        if is_count:
            col_name = next(iter(col_map.values()), "count")
            return IVGResult(columns=[col_name], rows=[[len(target_ids)]], metadata=sql_query.query_metadata)

        if not target_ids:
            if is_optional:
                null_cols = [_id_col, _labels_col, _props_col] if _node_triple_in_sql else out_cols
                return IVGResult(columns=null_cols, rows=[[None] * len(null_cols)], metadata=sql_query.query_metadata)
            return IVGResult(columns=out_cols, rows=[], metadata=sql_query.query_metadata)

        # Step 5: Handle RETURN r — return list of per-path edge-type lists
        if _rel_in_sql and _rel_col:
            rows_out = []
            for nid in target_ids:
                for path_edges in path_edges_by_target.get(nid, []):
                    # Each path_edges is a list of edge types; each becomes [':TYPE']
                    rows_out.append([[f":{et}"] for et in path_edges])
            return IVGResult(
                columns=[_rel_col],
                rows=[[row] for row in rows_out],
                metadata=sql_query.query_metadata,
            )

        if not return_props:
            # RETURN x (whole node) — return node triple if SQL expects it
            if _node_triple_in_sql:
                # Fetch labels+props so _remap_node_columns can reconstruct the node
                nodes_result = self._store.get_nodes(target_ids, [])
                # get_nodes returns rows: [node_id, labels_json]
                node_data = {row[0]: row[1] for row in (nodes_result.rows if nodes_result else [])}
                # Fetch props as JSON for x_props column
                props_json_by_id = self._fetch_props_json(target_ids)
                rows_out = []
                for nid in target_ids:
                    rows_out.append([
                        nid,
                        node_data.get(nid, "[]"),
                        props_json_by_id.get(nid),
                    ])
                return IVGResult(
                    columns=[_id_col, _labels_col, _props_col],
                    rows=rows_out,
                    metadata=sql_query.query_metadata,
                )
            # No triple columns — return IDs only (legacy path)
            return IVGResult(
                columns=out_cols,
                rows=[[nid] for nid in target_ids],
                metadata=sql_query.query_metadata,
            )

        # Step 4: Fetch requested properties for each target node
        prop_keys = [pk for _, pk in return_props]
        props_result = self._store.get_nodes(target_ids, prop_keys)
        # get_nodes returns rows: [node_id, labels_json, prop1, prop2, ...]
        props_by_id: dict = {}
        for row in (props_result.rows if props_result else []):
            if row:
                nid = row[0]
                props_by_id[nid] = list(row[2:])  # skip node_id and labels

        rows_out = []
        for nid in target_ids:
            prop_vals = list(props_by_id.get(nid, []))
            # Pad to expected column count
            while len(prop_vals) < len(prop_keys):
                prop_vals.append(None)
            rows_out.append(prop_vals[:len(prop_keys)])

        return IVGResult(
            columns=out_cols,
            rows=rows_out,
            metadata=sql_query.query_metadata,
        )

    def _fetch_props_json(self, node_ids: list) -> dict:
        """Fetch props as JSON strings (matching SQL x_props format) keyed by node_id.

        Returns a dict mapping node_id → JSON string like
        '[{"key":"name","value":"A"},{"key":"age","value":"30"}]'
        or None if the node has no properties.
        """
        if not node_ids:
            return {}
        _CHUNK = 499
        result: dict = {nid: None for nid in node_ids}
        try:
            cursor = self._store.conn.cursor()
            for i in range(0, len(node_ids), _CHUNK):
                chunk = node_ids[i:i + _CHUNK]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f'SELECT s, "key", val FROM {self._t("rdf_props")} WHERE s IN ({placeholders})',
                    chunk,
                )
                props_by_nid: dict = {}
                for row in cursor.fetchall():
                    nid, key, val = row[0], row[1], row[2]
                    props_by_nid.setdefault(nid, []).append({"key": key, "value": val})
                for nid, props in props_by_nid.items():
                    result[nid] = json.dumps(props)
        except Exception as exc:
            logger.debug("_fetch_props_json failed: %s", exc)
        return result

    def _bfs_with_paths(
        self, source_id: str, predicates: list, max_hops: int, direction: str
    ) -> list:
        """BFS that tracks the full path (node + edge-type sequence).

        Returns a list of (node_list, edge_type_list) tuples.
        node_list[0] == source_id; node_list[-1] == target node.
        edge_type_list has len(node_list) - 1 entries.
        """
        import re as _re
        try:
            cursor = self._store.conn.cursor()
        except Exception:
            return []

        schema = getattr(self._store, "_schema_prefix", "Graph_KG")
        edges_table = f"{schema}.rdf_edges"

        results: list = []
        # frontier: list of (nodes_on_path, edges_on_path)
        frontier: list = [([source_id], [])]

        for _hop in range(1, max_hops + 1):
            if not frontier:
                break
            all_src_ids = list({path[0][-1] for path in frontier})
            if not all_src_ids:
                break
            preds_clause = ""
            if predicates:
                placeholders_p = ",".join("?" * len(predicates))
                preds_clause = f" AND p IN ({placeholders_p})"
            placeholders_f = ",".join("?" * len(all_src_ids))
            try:
                params = all_src_ids + (predicates if predicates else [])
                if direction in ("out", "outbound"):
                    sql = f"SELECT s, o_id, p FROM {edges_table} WHERE s IN ({placeholders_f}){preds_clause}"
                    cursor.execute(sql, params)
                    edges = list(cursor.fetchall())
                elif direction in ("in", "inbound"):
                    sql = f"SELECT o_id, s, p FROM {edges_table} WHERE o_id IN ({placeholders_f}){preds_clause}"
                    cursor.execute(sql, params)
                    edges = list(cursor.fetchall())
                else:
                    sql_o = f"SELECT s, o_id, p FROM {edges_table} WHERE s IN ({placeholders_f}){preds_clause}"
                    cursor.execute(sql_o, params)
                    edges = list(cursor.fetchall())
                    sql_i = f"SELECT o_id, s, p FROM {edges_table} WHERE o_id IN ({placeholders_f}){preds_clause}"
                    cursor.execute(sql_i, params)
                    edges += list(cursor.fetchall())
            except Exception as exc:
                logger.debug("_bfs_with_paths edge query failed: %s", exc)
                break

            # adj: src_id → [(nbr_id, pred)]
            adj: dict = {}
            for src, nbr, pred in edges:
                adj.setdefault(src, []).append((nbr, pred))

            next_frontier: list = []
            for nodes, path_edges in frontier:
                current = nodes[-1]
                for nbr, pred in adj.get(current, []):
                    if nbr not in nodes:  # no cycles on a single path
                        new_nodes = nodes + [nbr]
                        new_edges = path_edges + [pred]
                        results.append((new_nodes, new_edges))
                        next_frontier.append((new_nodes, new_edges))
            frontier = next_frontier

        return results

    def _filter_nodes_by_post_where(
        self,
        target_ids: list,
        target_alias: str,
        post_where_conds: list,
        post_params: list,
        original_sql: str,
    ) -> list:
        """Filter target_ids using the post-boundary WHERE conditions (target props).

        Extracts the post-boundary FROM/JOIN clauses and reruns them as a SQL query
        restricted to candidate target_ids.
        """
        import re as _re
        if not target_ids or not post_where_conds:
            return target_ids
        try:
            cursor = self._store.conn.cursor()
            cartesian_pat = _re.compile(
                r'\bJOIN\s+\S+\s+' + _re.escape(target_alias) + r'\s+ON\s+1\s*=\s*1\b',
                _re.IGNORECASE,
            )
            m = cartesian_pat.search(original_sql)
            if not m:
                return target_ids
            where_start = original_sql.find('\nWHERE ')
            if where_start == -1:
                where_start = len(original_sql)
            post_joins = original_sql[m.start():where_start].strip()
            # Replace "JOIN nodes target_alias ON 1=1" with "FROM nodes target_alias"
            post_joins = _re.sub(
                r'\bJOIN\s+(\S+)\s+' + _re.escape(target_alias) + r'\s+ON\s+1\s*=\s*1\b',
                r'FROM \1 ' + target_alias,
                post_joins,
                count=1,
                flags=_re.IGNORECASE,
            )
            _CHUNK = 499
            filtered: set = set()
            for i in range(0, len(target_ids), _CHUNK):
                chunk = target_ids[i:i + _CHUNK]
                placeholders = ",".join("?" * len(chunk))
                post_filter = f"{target_alias}.node_id IN ({placeholders})"
                all_conds = [post_filter] + post_where_conds
                post_query = (
                    f"SELECT DISTINCT {target_alias}.node_id\n"
                    f"{post_joins}\n"
                    f"WHERE {' AND '.join(all_conds)}"
                )
                params = list(post_params) + list(chunk)
                try:
                    cursor.execute(post_query, params)
                    for row in cursor.fetchall():
                        if row and row[0]:
                            filtered.add(row[0])
                except Exception as exc:
                    logger.debug("_filter_nodes_by_post_where query failed: %s", exc)
                    filtered.update(chunk)
            return [nid for nid in target_ids if nid in filtered]
        except Exception as exc:
            logger.debug("_filter_nodes_by_post_where failed: %s", exc)
            return target_ids

    def _execute_weighted_shortest_path(
        self, sql_query, parameters=None
    ) -> Dict[str, Any]:
        import json as _json

        vl = sql_query.var_length_paths[0]

        def _resolve(param_ref):
            if param_ref is None:
                return None
            s = str(param_ref)
            if s.startswith("'") and s.endswith("'"):
                return s[1:-1]
            if s.startswith("$"):
                name = s[1:]
                if parameters and name in parameters:
                    return str(parameters[name])
                return None
            return s

        source_id = _resolve(vl.get("src_id_param"))
        target_id = _resolve(vl.get("dst_id_param"))

        if source_id is None or target_id is None:
            raise ValueError(
                "ivg.shortestPath.weighted requires both from and to to be bound IDs"
            )

        weight_prop = vl.get("weight_property", "weight")
        max_hops = int(vl.get("max_hops", 10))
        return self._store.execute_weighted_shortest_path(source_id, target_id, weight_prop, max_hops)
    def _execute_shortest_path_cypher(
        self, sql_query, parameters=None
    ) -> Dict[str, Any]:
        import json as _json

        vl = sql_query.var_length_paths[0]
        preds_json = _json.dumps(vl["types"]) if vl.get("types") else "[]"
        max_hops = vl.get("max_hops", 5)
        direction = vl.get("direction", "both")
        find_all = 1 if vl.get("all_shortest") else 0

        def _resolve(param_ref):
            if param_ref is None:
                return None
            if isinstance(param_ref, str) and param_ref.startswith("$"):
                name = param_ref[1:]
                if parameters and name in parameters:
                    return str(parameters[name])
                return None
            return str(param_ref)

        source_id = _resolve(vl.get("src_id_param"))
        target_id = _resolve(vl.get("dst_id_param"))

        if source_id is None and parameters:
            src_var = vl.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(
                    (str(v) for v in parameters.values() if isinstance(v, str)), None
                )

        if target_id is None and parameters:
            dst_var = vl.get("target_var")
            if dst_var and dst_var in parameters:
                target_id = str(parameters[dst_var])
            else:
                vals = [str(v) for v in parameters.values() if isinstance(v, str)]
                target_id = vals[1] if len(vals) > 1 else None

        if source_id is None or target_id is None:
            sql_params = sql_query.parameters[0] if sql_query.parameters else []
            str_params = [p for p in sql_params if isinstance(p, str) and not p.startswith("Graph_KG")]
            if source_id is None and len(str_params) >= 1:
                source_id = str_params[0]
            if target_id is None and len(str_params) >= 2:
                target_id = str_params[1]

        if source_id is None or target_id is None:
            raise ValueError(
                "shortestPath requires both source and target node IDs to be bound. "
                "Use {id: $from} / {id: $to} or {id: 'literal'} on both endpoints."
            )

        predicates = vl.get("types", [])
        result = self._store.execute_shortest_path(
            source_id, target_id, predicates, max_hops, direction, bool(find_all)
        )

        return_funcs = vl.get("return_path_funcs", [])
        if not return_funcs or "path" in return_funcs:
            return result

        cols_out = []
        rows_out = []
        for row in result.rows:
            path_json, length = row[0], row[1]
            r = []
            if "length" in return_funcs:
                r.append(length)
                cols_out = ["length"] if not cols_out else cols_out
            if "nodes" in return_funcs:
                import json as _j
                nodes = _j.loads(path_json).get("nodes", []) if path_json else []
                r.append(nodes)
                if "nodes" not in cols_out:
                    cols_out.append("nodes")
            if "relationships" in return_funcs:
                import json as _j
                rels = _j.loads(path_json).get("rels", []) if path_json else []
                r.append(rels)
                if "relationships" not in cols_out:
                    cols_out.append("relationships")
            if r:
                rows_out.append(r)

        return IVGResult(
            columns=cols_out or ["p"],
            rows=rows_out,
            sql=result.sql,
            params=result.params,
            metadata=sql_query.query_metadata,
        )

    def _execute_var_length_cypher(self, sql_query, parameters=None) -> Dict[str, Any]:
        import json as _json
        import warnings as _warnings
        from iris_vector_graph.engine import _bfs_stream_pages
        from iris_vector_graph.schema import _call_classmethod

        if self._nkg_dirty:
            from iris_vector_graph.errors import IndexNotSyncedError
            raise IndexNotSyncedError()

        vl = sql_query.var_length_paths[0]
        predicates_json = _json.dumps(vl["types"]) if vl["types"] else ""
        max_hops = vl["max_hops"]
        min_hops = vl["min_hops"]
        rel_props_filter = vl.get("properties", {})

        params = sql_query.parameters[0] if sql_query.parameters else []
        source_id = None
        for item in params:
            if isinstance(item, str) and not item.startswith("Graph_KG"):
                source_id = item
                break
        if source_id is None and parameters:
            src_var = vl.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(iter(parameters.values()), None)

        if source_id is None:
            return IVGResult(                columns= [],
                rows= [],
                sql= "",
                params= [],
                metadata= sql_query.query_metadata
            )

        max_results = 0
        import re as _re
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else (sql_query.sql[0] if sql_query.sql else "")
        if sql_query.sql:
            # IRIS SQL uses "FETCH FIRST N ROWS ONLY"; the build-106 %qaqpre workaround
            # emits SELECT TOP N instead; fall back to LIMIT N.
            m = _re.search(r"FETCH\s+FIRST\s+(\d+)\s+ROWS\s+ONLY", sql_str, _re.IGNORECASE)
            if not m:
                m = _re.search(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s+(\d+)\b", sql_str, _re.IGNORECASE)
            if not m:
                m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

        count_match = _re.search(r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)
        if count_match:
            col_name = count_match.group(1)
            try:
                cnt = int(str(_call_classmethod(
                    self.conn, "Graph.KG.Traversal", "BFSFastCountDistinct",
                    source_id, predicates_json, max_hops, "", vl.get("direction", "out"),
                )))
            except Exception:
                cnt = 0
            return IVGResult(                columns= [col_name],
                rows= [[cnt]],
                sql= f"BFSFastCountDistinct({source_id}, {predicates_json}, {max_hops})",
                params= [],
                metadata= sql_query.query_metadata
            )

        bfs_results = None
        direction = vl.get("direction", "out")
        arno_usable = (
            self._detect_arno()
            and self._arno_capabilities.get("bfs")
            and self._arno_capabilities.get("rust_callout")
            and direction == "out"
        )
        if arno_usable:
            try:
                bfs_json = self._arno_call(
                    "Graph.KG.NKGAccel",
                    "BFSJson",
                    source_id,
                    predicates_json,
                    max_hops,
                    max_results,
                )
                bfs_str = str(bfs_json) if bfs_json else ""
                if bfs_str.startswith("SORTED:") and bfs_str != "SORTED:0":
                    tag = bfs_str.split(":")[1]
                    if max_results == 0:
                        bfs_results = list(_bfs_stream_pages(self.conn, tag))
                    else:
                        try:
                            results_str = str(_call_classmethod(
                                self.conn, "Graph.KG.Traversal", "ReadBFSResults", tag
                            ))
                            bfs_results = _json.loads(results_str)
                        except Exception:
                            bfs_results = list(_bfs_stream_pages(self.conn, tag))
                elif bfs_str:
                    bfs_results = _json.loads(bfs_str)
                else:
                    bfs_results = []
                logger.debug("Arno BFSJson: %d results for %s", len(bfs_results), source_id)
            except Exception as e:
                logger.warning(f"Arno BFSJson failed, falling back to BFSFastJsonSorted: {e}")
                bfs_results = None

        if bfs_results is None:
            direction = vl.get("direction", "out")
            try:
                resp = str(_call_classmethod(
                    self.conn, "Graph.KG.Traversal", "BFSFastJsonSorted",
                    source_id, predicates_json, max_hops, "", direction, max_results,
                ))
                if resp.startswith("SORTED:") and resp != "SORTED:0":
                    tag = resp.split(":", 2)[1]
                    if max_results == 0:
                        bfs_results = list(_bfs_stream_pages(self.conn, tag))
                    else:
                        try:
                            results_str = str(_call_classmethod(
                                self.conn, "Graph.KG.Traversal", "ReadBFSResults", tag
                            ))
                            bfs_results = _json.loads(results_str)
                        except Exception:
                            bfs_results = list(_bfs_stream_pages(self.conn, tag))
                else:
                    bfs_results = []
            except Exception as e:
                logger.warning(f"BFSFastJsonSorted failed: {e}")
                return IVGResult(columns=[], rows=[], sql="", params=[], metadata=sql_query.query_metadata)

        if min_hops > 1:
            min_step_per_node: dict = {}
            for r in bfs_results:
                oid = r.get("o")
                if oid:
                    s = r.get("step", 1)
                    if oid not in min_step_per_node or s < min_step_per_node[oid]:
                        min_step_per_node[oid] = s
            bfs_results = [
                r
                for r in bfs_results
                if min_step_per_node.get(r.get("o"), 0) >= min_hops
            ]

        if rel_props_filter and bfs_results:
            bfs_results = self._filter_edges_by_properties(bfs_results, rel_props_filter)

        seen = set()
        target_ids = []
        for r in bfs_results:
            oid = r.get("o")
            if oid and oid not in seen:
                seen.add(oid)
                target_ids.append(oid)

        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else ""

        # Fast path: if query only needs node IDs (RETURN DISTINCT b.node_id or RETURN b.node_id),
        # skip get_nodes() entirely — BFS already has the IDs.
        id_only_match = _re.search(
            r'SELECT\s+(?:DISTINCT\s+)?(?:\S+\.node_id|\S+\.id)\s+AS\s+(\w+)',
            sql_str, _re.IGNORECASE
        )
        # Count path: COUNT(DISTINCT ...) — just return the count
        count_match = _re.search(
            r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)',
            sql_str, _re.IGNORECASE
        )

        if count_match:
            col_name = count_match.group(1)
            return IVGResult(                columns= [col_name],
                rows= [[len(target_ids)]],
                sql= f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
                params= [],
                metadata= sql_query.query_metadata
            )

        if id_only_match:
            col_name = id_only_match.group(1)
            # Apply LIMIT from SQL if present
            limit_match = _re.search(r'\bLIMIT\s+(\d+)', sql_str, _re.IGNORECASE)
            limit = int(limit_match.group(1)) if limit_match else None
            result_ids = target_ids[:limit] if limit else target_ids
            return IVGResult(                columns= [col_name],
                rows= [[nid] for nid in result_ids],
                sql= f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
                params= [],
                metadata= sql_query.query_metadata
            )

        # Full path: caller wants labels/props — fall through to get_nodes()
        alias_match = _re.search(r'SELECT\s+DISTINCT\s+\S+\s+AS\s+(\w+)|SELECT\s+\S+\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)
        col_name = (alias_match.group(1) or alias_match.group(2)) if alias_match else "b_id"

        if not target_ids:
            return IVGResult(                columns= [col_name, "b_labels", "b_props"],
                rows= [],
                sql= "",
                params= [],
                metadata= sql_query.query_metadata
            )

        nodes = self.get_nodes(target_ids)
        rows = []
        for data in nodes:
            node_id = data.get("id", "")
            rows.append(
                (
                    node_id,
                    data.get("labels", []),
                    {k: v for k, v in data.items() if k not in ("labels", "id")},
                )
            )

        return IVGResult(            columns= [col_name, "b_labels", "b_props"],
            rows= [list(r) for r in rows],
            sql= f"BFSFastJson({source_id}, {predicates_json}, {max_hops})",
            params= [],
            metadata= sql_query.query_metadata
        )
    def _try_khop_fast_path(self, cypher_query: str, parameters) -> Optional[Dict[str, Any]]:
        import re as _re

        _1HOP_COUNT_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+count\s*\(\s*\3\s*\)\s+AS\s+(\w+)\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        _1HOP_IDS_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+\3\.node_id(?:\s+AS\s+(\w+))?\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        _2HOP_COUNT_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\*2\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+count\s*\(\s*\3\s*\)\s+AS\s+(\w+)\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        _2HOP_IDS_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*:\s*(\w+)\s*\*2\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+\3\.node_id(?:\s+AS\s+(\w+))?(?:\s+LIMIT\s+(\d+))?\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )

        params = parameters or {}

        m = _1HOP_COUNT_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, col = m.group(1), m.group(2), m.group(3), m.group(4)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            try:
                cnt = int(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHopCount", str(src_id), pred
                ))
                return IVGResult(columns=[col], rows=[(cnt,)])
            except Exception:
                return None

        m = _1HOP_IDS_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, alias = m.group(1), m.group(2), m.group(3), m.group(4)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            try:
                raw = str(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHopNeighborIds", str(src_id), pred
                ))
                # KHopNeighborIds returns "" for no results; ObjectScript None → "None"
                ids = [x for x in raw.split("\n") if x and x != "None"]
                col = alias or "node_id"
                return IVGResult(columns=[col], rows=[(nid,) for nid in ids])
            except Exception:
                return None

        m = _2HOP_COUNT_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, col = m.group(1), m.group(2), m.group(3), m.group(4)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            try:
                cnt = int(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHop2CountExact", str(src_id), pred
                ))
                return IVGResult(columns=[col], rows=[(cnt,)])
            except Exception:
                return None

        m = _2HOP_IDS_RE.match(cypher_query)
        if m:
            src_param, pred, _nvar, alias, limit_str = (
                m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            )
            src_id = params.get(src_param)
            if src_id is None:
                return None
            limit = int(limit_str) if limit_str else 0
            try:
                raw = str(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "KHop2NeighborIds", str(src_id), pred, limit
                ))
                # KHopNeighborIds returns "" for no results; ObjectScript None → "None"
                ids = [x for x in raw.split("\n") if x and x != "None"]
                col = alias or "node_id"
                return IVGResult(columns=[col], rows=[(nid,) for nid in ids])
            except Exception:
                return None

        # NKG fast-path: variable-length patterns [*1..K] or [:PRED*1..K] for K up to 5.
        # Routes to NKGAccelTraversal.KHopNeighbors when ^NKG is populated.
        # Handles: MATCH (n {node_id: $x})-[*1..K]->(m) RETURN m.node_id [AS alias] [LIMIT N]
        #          MATCH (n {node_id: $x})-[:PRED*1..K]->(m) RETURN m.node_id [AS alias] [LIMIT N]
        _KHOP_VAR_RE = _re.compile(
            r'''^\s*MATCH\s*\(\s*\w+\s*\{\s*node_id\s*:\s*\$(\w+)\s*\}\s*\)
                \s*-\s*\[\s*(?::\s*(\w+)\s*)?\*\s*1\s*\.\.\s*([2-5])\s*\]\s*->\s*\(\s*(\w+)\s*\)
                \s*RETURN\s+\4\.node_id(?:\s+AS\s+(\w+))?(?:\s+LIMIT\s+(\d+))?\s*$''',
            _re.IGNORECASE | _re.VERBOSE,
        )
        m = _KHOP_VAR_RE.match(cypher_query)
        if m:
            src_param = m.group(1)
            _pred_type = m.group(2)  # may be None (untyped)
            max_hops = int(m.group(3))
            _nvar = m.group(4)
            alias = m.group(5)
            limit_str = m.group(6)
            src_id = params.get(src_param)
            if src_id is None:
                return None
            # Only route through NKG if ^NKG is populated
            try:
                nkg_ok = bool(int(str(self._iris_obj().classMethodValue(
                    "Graph.KG.Traversal", "NKGPopulated"
                ))))
            except Exception:
                nkg_ok = False
            if not nkg_ok:
                return None
            limit = int(limit_str) if limit_str else 0
            max_nodes = max(limit, 100_000) if limit else 100_000
            try:
                import json as _json
                raw = str(self._iris_obj().classMethodValue(
                    "Graph.KG.NKGAccelTraversal", "KHopNeighbors",
                    str(src_id), max_hops, max_nodes,
                ))
                r = _json.loads(raw)
                col = alias or "node_id"
                # Exclude seed (dist == 0); apply limit if specified
                ids = [n["id"] for n in r.get("nodes", []) if n.get("dist", 0) > 0]
                if limit:
                    ids = ids[:limit]
                return IVGResult(columns=[col], rows=[(nid,) for nid in ids])
            except Exception:
                return None

        return None
    def _execute_approx_count_distinct(self, cypher_query: str, parameters, match) -> Dict[str, Any]:
        import json as _json
        import re as _re
        from iris_vector_graph.schema import _call_classmethod

        col_name = match.group(2)

        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql
        try:
            q = parse_query(cypher_query)
            sql_query = translate_to_sql(q, params=parameters or {})
        except Exception:
            return IVGResult(columns=[col_name], rows=[[0]], sql="", params=[])

        if not sql_query.var_length_paths:
            return IVGResult(columns=[col_name], rows=[[0]], sql="", params=[])

        vl = sql_query.var_length_paths[0]
        predicates_json = _json.dumps(vl["types"]) if vl["types"] else ""
        max_hops = vl["max_hops"]
        direction = vl.get("direction", "both")

        params = sql_query.parameters[0] if sql_query.parameters else []
        source_id = None
        for item in params:
            if isinstance(item, str) and not item.startswith("Graph_KG"):
                source_id = item
                break
        if source_id is None and parameters:
            src_var = vl.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(iter(parameters.values()), None) if parameters else None

        if not source_id:
            return IVGResult(columns=[col_name], rows=[[0]], sql="", params=[])

        try:
            raw = str(_call_classmethod(
                self.conn, "Graph.KG.NKGAccel", "CountDistinctKHop",
                source_id, predicates_json, max_hops, direction,
            ))
            result = _json.loads(raw)
            estimate = result.get("estimate", 0)
            registers = result.get("registers", 256)
            std_error = result.get("std_error", 0.065)
        except Exception as e:
            logger.warning(f"CountDistinctKHop failed: {e}")
            estimate = 0
            registers = 256
            std_error = 0.065

        from iris_vector_graph.cypher.translator import QueryMetadata
        meta = QueryMetadata(
            warnings=[
                f"approx_count_distinct: HLL-{registers}, "
                f"std_error={std_error*100:.1f}%, registers={registers}"
            ]
        )
        return IVGResult(            columns= [col_name],
            rows= [[estimate]],
            sql= f"CountDistinctKHop({source_id}, {predicates_json}, {max_hops})",
            params= [],
            metadata= meta
        )
    def khop2_count_fast(self, node_id: str, predicate: str = "") -> int:
        KHop2Input(node_id=node_id)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.Traversal", "KHop2CountFast", node_id, predicate
        )
        return int(result)
    def khop2_count_exact(self, node_id: str, predicate: str = "") -> int:
        KHop2Input(node_id=node_id)
        result = self._iris_obj().classMethodValue(
            "Graph.KG.Traversal", "KHop2CountExact", node_id, predicate
        )
        return int(result)
