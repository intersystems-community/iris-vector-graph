import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, _table
from iris_vector_graph.result import IVGResult
from iris_vector_graph._validate import CypherInput, KHop2Input

logger = logging.getLogger(__name__)


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
        read_only: bool = False,
    ) -> "IVGResult":
        """
        Execute a Cypher query by translating it to IRIS SQL.

        Args:
            cypher_query: Cypher query string
            parameters: Optional query parameters
            read_only: If True, rejects any mutation (CREATE/DELETE/SET/MERGE/REMOVE/FOREACH)

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
            ).get("rows", [])
            rels = self._try_system_procedure(
                type("P", (), {"procedure_name": "db.relationshipTypes"})()
            ).get("rows", [])
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
                            all_cols = sub.get("columns", [])
                        all_rows.extend(sub.get("rows", []))
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
                result = self._execute_parsed(part_query, current_params)
                if result and result.get("rows") and result.get("columns"):
                    first_row = result["rows"][0] if result["rows"] else []
                    for col, val in zip(result["columns"], first_row):
                        if isinstance(val, (str, int, float, bool, type(None))):
                            current_params[col] = val
            return result

        return self._execute_parsed(parsed, parameters)
    def _execute_parsed(self, parsed, parameters):
        if parsed.procedure_call is not None:
            result = self._try_system_procedure(parsed.procedure_call)
            if result is not None:
                return result
        sql_query = translate_to_sql(parsed, parameters, engine=self)
        if sql_query.var_length_paths:
            return self._route_var_length(sql_query, parameters)
        metadata = sql_query.query_metadata
        if sql_query.is_transactional:
            result = self._store.execute_transaction(sql_query.sql, sql_query.parameters)
            result.metadata = metadata
            return result
        if self._store_capabilities.get("native_sql", True):
            sql_str = sql_query.sql
            p = sql_query.parameters[0] if sql_query.parameters else []
            result = self._store.execute_sql(sql_str, p)
            result.metadata = metadata
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

        import re as _re
        sql_str = sql_query.sql if isinstance(sql_query.sql, str) else (sql_query.sql[0] if sql_query.sql else "")
        count_match = _re.search(r'SELECT\s+COUNT\s*\(\s*DISTINCT\s+.*?\)\s+AS\s+(\w+)', sql_str, _re.IGNORECASE)

        params = sql_query.parameters[0] if sql_query.parameters else []
        source_id = None
        for item in params:
            if isinstance(item, str) and not item.startswith("Graph_KG"):
                source_id = item
                break
        if source_id is None and parameters:
            src_var = vl0.get("source_var")
            if src_var and src_var in parameters:
                source_id = str(parameters[src_var])
            else:
                source_id = next(iter(parameters.values()), None)

        if source_id is None:
            return IVGResult(columns=[], rows=[], sql="", params=[], metadata=sql_query.query_metadata)

        predicates = vl0.get("types", [])
        max_hops = vl0.get("max_hops", 5)
        direction = vl0.get("direction", "out")
        max_results = 0
        if sql_str:
            m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

        if count_match:
            col_name = count_match.group(1)
            bfs_result = self._store.execute_bfs(source_id, predicates, max_hops, direction, 0)
            cnt = len(bfs_result.rows) if not bfs_result.error else 0
            return IVGResult(columns=[col_name], rows=[[cnt]], metadata=sql_query.query_metadata)
        max_results = 0
        if sql_str:
            m = _re.search(r"\bLIMIT\s+(\d+)", sql_str, _re.IGNORECASE)
            if m:
                max_results = int(m.group(1))

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
        return self._store.execute_shortest_path(
            source_id, target_id, predicates, max_hops, direction, bool(find_all)
        )

        if not paths:
            return IVGResult(                columns= ["p"],
                rows= [],
                sql= "",
                params= [],
                metadata= sql_query.query_metadata
            )

        return_funcs = vl.get("return_path_funcs", [])
        rows = []
        for path in paths:
            row = []
            if not return_funcs or "path" in return_funcs:
                row.append(
                    _json.dumps(
                        {
                            "nodes": path.get("nodes", []),
                            "rels": path.get("rels", []),
                            "length": path.get("length", 0),
                        }
                    )
                )
            if "length" in return_funcs:
                row.append(path.get("length", 0))
            if "nodes" in return_funcs:
                row.append(path.get("nodes", []))
            if "relationships" in return_funcs:
                row.append(path.get("rels", []))
            if not row:
                row.append(
                    _json.dumps(
                        {
                            "nodes": path.get("nodes", []),
                            "rels": path.get("rels", []),
                            "length": path.get("length", 0),
                        }
                    )
                )
            rows.append(row)

        columns = []
        if not return_funcs or "path" in return_funcs:
            columns.append("p")
        if "length" in return_funcs:
            columns.append("length")
        if "nodes" in return_funcs:
            columns.append("nodes")
        if "relationships" in return_funcs:
            columns.append("relationships")
        if not columns:
            columns = ["p"]

        return IVGResult(            columns= columns,
            rows= rows,
            sql= f"ShortestPathJson({source_id}, {target_id}, {max_hops})",
            params= [],
            metadata= sql_query.query_metadata
        )
    def _execute_var_length_cypher(self, sql_query, parameters=None) -> Dict[str, Any]:
        import json as _json
        import warnings as _warnings

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
                ids = [x for x in raw.split("\n") if x]
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
                ids = [x for x in raw.split("\n") if x]
                col = alias or "node_id"
                return IVGResult(columns=[col], rows=[(nid,) for nid in ids])
            except Exception:
                return None

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
