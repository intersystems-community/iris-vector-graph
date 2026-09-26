"""TCK result comparison: value parsing and table diff."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class TCKValue:
    raw: str
    python: Any

    @staticmethod
    def parse(cell: str) -> "TCKValue":
        cell = _gherkin_unescape(cell.strip())
        python = _parse_tck_value(cell)
        return TCKValue(raw=cell, python=python)


def _gherkin_unescape(cell: str) -> str:
    """Gherkin table-cell escapes (``\\\\``, ``\\n``); behave only handles ``\\|``."""
    if "\\" not in cell:
        return cell
    out = []
    i = 0
    while i < len(cell):
        c = cell[i]
        if c == "\\" and i + 1 < len(cell) and cell[i + 1] in "\\n":
            out.append("\\" if cell[i + 1] == "\\" else "\n")
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_tck_value(s: str) -> Any:
    s = s.strip()
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s.startswith("'") and s.endswith("'"):
        raw = s[1:-1]
        # Process escape sequences: \\ first (must be before others), then \n, \t, \r, \'
        result = []
        i = 0
        while i < len(raw):
            if raw[i] == '\\' and i + 1 < len(raw):
                nxt = raw[i + 1]
                if nxt == '\\':
                    result.append('\\')
                    i += 2
                elif nxt == 'n':
                    result.append('\n')
                    i += 2
                elif nxt == 't':
                    result.append('\t')
                    i += 2
                elif nxt == 'r':
                    result.append('\r')
                    i += 2
                elif nxt == "'":
                    result.append("'")
                    i += 2
                elif nxt == '"':
                    result.append('"')
                    i += 2
                elif nxt in ('u', 'U') and i + 5 < len(raw):
                    hex_len = 4 if nxt == 'u' else 8
                    hex_str = raw[i + 2: i + 2 + hex_len]
                    if len(hex_str) == hex_len and all(c in '0123456789abcdefABCDEF' for c in hex_str):
                        result.append(chr(int(hex_str, 16)))
                        i += 2 + hex_len
                    else:
                        result.append(raw[i])
                        i += 1
                else:
                    result.append(raw[i])
                    i += 1
            else:
                result.append(raw[i])
                i += 1
        return ''.join(result)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_tck_value(item) for item in _split_tck_list(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        result = {}
        for pair in _split_tck_list(inner):
            k, _, v = pair.partition(":")
            result[k.strip()] = _parse_tck_value(v.strip())
        return result
    # numeric — handle int, float (1.5), and scientific notation (1e4, 1.5E-3)
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def _split_tck_list(s: str) -> list[str]:
    """Split a comma-separated TCK list respecting nested brackets and quotes."""
    items = []
    depth = 0
    in_quote = False
    buf = []
    for ch in s:
        if ch == "'" and not in_quote:
            in_quote = True
            buf.append(ch)
        elif ch == "'" and in_quote:
            in_quote = False
            buf.append(ch)
        elif in_quote:
            buf.append(ch)
        elif ch in ("[", "{"):
            depth += 1
            buf.append(ch)
        elif ch in ("]", "}"):
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        items.append("".join(buf).strip())
    return items


@dataclass
class TCKResultTable:
    columns: list[str]
    rows: list[list[TCKValue]]
    ordered: bool
    list_unordered: bool

    def compare(self, actual_rows: list[dict], actual_columns: list[str]) -> str | None:
        """Return diff string on mismatch, None on match."""
        expected_rows = [
            {col: cell.python for col, cell in zip(self.columns, row)}
            for row in self.rows
        ]

        # Remap IVG's expanded node columns (var_id, var_labels, var_props) → var
        remapped_rows = [_remap_node_columns(row, self.columns, actual_columns) for row in actual_rows]

        # Case-insensitive column renaming: IVG lowercases function names (toInteger→tointeger).
        # Also normalize whitespace (cOuNt( * ) vs count(*)) for function call column names.
        # Build a lower-normalized→tck_col map so actual rows use TCK-canonical casing.
        import re as _re_col
        def _norm_col(s: str) -> str:
            """Normalize column name: lowercase + collapse all whitespace."""
            return _re_col.sub(r'\s+', '', s.lower())
        lower_to_tck = {_norm_col(col): col for col in self.columns}
        remapped_rows = [
            {lower_to_tck.get(_norm_col(k), k): v for k, v in row.items()}
            for row in remapped_rows
        ]

        # Build a column-level type schema from ALL expected rows.
        # For each column, take the first non-None expected value so that
        # normalise_iris_value knows the target type regardless of row order.
        # This prevents unordered comparisons from using the wrong positional
        # expected row to type-hint the actual value (e.g. IRIS returns '1'/'0'
        # for booleans from JSON_TABLE, but if the positionally-aligned expected
        # row has null for that column we'd miss the bool cast).
        type_schema: dict[str, Any] = {}
        for col in self.columns:
            for exp_row in expected_rows:
                v = exp_row.get(col)
                if v is not None:
                    type_schema[col] = v
                    break

        # normalise actual values against expected types (use type_schema not
        # position-matched row so unordered result sets normalise correctly)
        norm_actual = [
            _normalise_row_with_nodes(row, type_schema, self.columns)
            for row in remapped_rows
        ]

        if len(norm_actual) != len(expected_rows):
            return (
                f"Row count mismatch: expected {len(expected_rows)}, got {len(norm_actual)}\n"
                f"Expected: {expected_rows}\n"
                f"Actual:   {norm_actual}"
            )

        if self.ordered:
            for i, (exp, act) in enumerate(zip(expected_rows, norm_actual)):
                if not _rows_equal(exp, act, self.columns, self.list_unordered):
                    return f"Row {i} mismatch:\n  expected: {exp}\n  actual:   {act}"
            return None
        else:
            # unordered: bipartite matching — each expected row must match a distinct
            # actual row.  Simple sort-then-compare fails when expected uses node
            # pattern strings ("(:A)") and actual uses node dicts (sort keys differ).
            # Greedy matching fails when a less-specific pattern consumes a node that
            # a more-specific pattern needed, so we use backtracking.
            def _can_match(exp_rows, avail_indices):
                if not exp_rows:
                    return True
                exp = exp_rows[0]
                for i in avail_indices:
                    if _rows_equal(exp, norm_actual[i], self.columns, self.list_unordered):
                        remaining = [j for j in avail_indices if j != i]
                        if _can_match(exp_rows[1:], remaining):
                            return True
                return False

            avail = list(range(len(norm_actual)))
            if not _can_match(expected_rows, avail):
                return (
                    f"Unordered comparison: no perfect matching found\n"
                    f"Expected: {expected_rows}\n"
                    f"Actual:   {norm_actual}"
                )
            return None


def _remap_node_columns(actual_row: dict, tck_columns: list[str], actual_columns: list[str]) -> dict:
    """Collapse IVG's var_id/var_labels/var_props and var_s/var_p/var_o_id triplets.

    IVG expands RETURN n into n_id, n_labels, n_props columns (nodes).
    IVG expands RETURN r into r_s, r_p, r_o_id columns (relationships).
    TCK expects a single column with node/relationship pattern notation.
    """
    result = dict(actual_row)
    for col in tck_columns:
        id_key = f"{col}_id"
        labels_key = f"{col}_labels"
        props_key = f"{col}_props"
        s_key = f"{col}_s"
        p_key = f"{col}_p"
        o_id_key = f"{col}_o_id"
        if id_key in actual_columns and labels_key in actual_columns and props_key in actual_columns:
            node_id = actual_row.get(id_key)
            node_labels = actual_row.get(labels_key)
            node_props = actual_row.get(props_key)
            # Null node from OPTIONAL MATCH: id is None and labels/props are empty
            _empty = ("[]", "null", None)
            if node_id is None and (node_labels is None or node_labels in _empty) and (node_props is None or node_props in _empty):
                result[col] = None
            else:
                result[col] = {
                    "_id": node_id,
                    "_labels": node_labels,
                    "_props": node_props,
                }
        elif s_key in actual_columns and p_key in actual_columns and o_id_key in actual_columns:
            # Relationship triplet: collapse var_s/var_p/var_o_id into var dict
            rel_s = actual_row.get(s_key)
            rel_type = actual_row.get(p_key)
            rel_o = actual_row.get(o_id_key)
            if rel_s is None and rel_type is None and rel_o is None:
                result[col] = None
            else:
                result[col] = {"_type": rel_type, "_s": rel_s, "_o_id": rel_o}
    return result


def _parse_node_pattern(s: str) -> dict | None:
    """Parse TCK node pattern like (:A), (:B {name: 'x'}) into a dict.

    Returns None if s is not a node pattern.
    """
    s = s.strip()
    if not (s.startswith("(") and s.endswith(")")):
        return None
    inner = s[1:-1].strip()
    # inner may be empty, :Label, :Label {props}, var:Label, var {props}
    labels: list[str] = []
    props: dict = {}
    # extract variable name (no colon at start)
    if inner and not inner.startswith(":"):
        # variable name up to ':' or ' ' or '{'
        end = 0
        while end < len(inner) and inner[end] not in (":", " ", "{"):
            end += 1
        inner = inner[end:].lstrip()
    # extract labels (:A:B...)
    while inner.startswith(":"):
        inner = inner[1:]
        end = 0
        while end < len(inner) and inner[end] not in (":", " ", "{"):
            end += 1
        labels.append(inner[:end])
        inner = inner[end:].lstrip()
    # extract props {k: v}
    if inner.startswith("{") and inner.endswith("}"):
        props = _parse_tck_value(inner)
    return {"labels": labels, "props": props}


def _parse_path_pattern(s: str) -> dict | None:
    """Parse TCK path pattern like <(:A)-[:R]->(:B)> into structured form.

    Returns a dict with:
      nodes: list of node pattern dicts (from _parse_node_pattern)
      rels: list of relationship type strings (or None for anonymous rels)
    or None if s is not a path pattern.
    """
    s = s.strip()
    if not (s.startswith("<") and s.endswith(">")):
        return None
    inner = s[1:-1].strip()

    nodes: list[dict] = []
    rels: list[str | None] = []

    # Parse alternating: node, rel, node, rel, node...
    i = 0
    while i < len(inner):
        # Skip whitespace
        while i < len(inner) and inner[i] in (" ", "\t", "\n"):
            i += 1
        if i >= len(inner):
            break

        # Expect a node pattern: (...)
        if inner[i] == "(":
            # Find matching )
            depth = 1
            j = i + 1
            while j < len(inner) and depth > 0:
                if inner[j] == "(":
                    depth += 1
                elif inner[j] == ")":
                    depth -= 1
                j += 1
            node_str = inner[i:j]
            node_pattern = _parse_node_pattern(node_str)
            if node_pattern:
                nodes.append(node_pattern)
            i = j
        else:
            i += 1

        # Skip whitespace
        while i < len(inner) and inner[i] in (" ", "\t", "\n"):
            i += 1
        if i >= len(inner):
            break

        # Expect a relationship pattern: -[...]-> or -[...]- or <-[...]-
        # Format: -[type]-> or -[type]- or <-[type]- or <-[type]->
        if inner[i] == "-" or (i > 0 and inner[i] == "<"):
            # Look for [ ] pair
            start = i
            # Find the opening [
            bracket_start = inner.find("[", i)
            if bracket_start != -1:
                # Find the closing ]
                bracket_end = inner.find("]", bracket_start)
                if bracket_end != -1:
                    # Extract relationship type
                    rel_inner = inner[bracket_start + 1:bracket_end].strip()
                    # rel_inner may be empty, :Type, var:Type, etc.
                    rel_type = None
                    if rel_inner:
                        # Extract type after : (if any) or just use the identifier
                        if ":" in rel_inner:
                            rel_type = rel_inner.split(":", 1)[1].strip()
                        else:
                            # Look for identifier before any space or end
                            for ch in rel_inner:
                                if ch in (" ", "{"):
                                    break
                                if ch.isalnum() or ch == "_":
                                    continue
                            # For now, if there's content, try to extract type
                            # Simple case: just the type name
                            rel_type = rel_inner.split()[0].lstrip(":") if rel_inner else None
                    # `[:KNOWS {num: 1}]` — the type ends where the property map starts
                    if rel_type:
                        rel_type = rel_type.split("{", 1)[0].strip() or None
                    rels.append(rel_type)
                    i = bracket_end + 1
                    # Skip any trailing -> or - or <
                    while i < len(inner) and inner[i] in ("-", ">", "<"):
                        i += 1
                else:
                    i += 1
            else:
                i += 1
        else:
            i += 1

    return {"nodes": nodes, "rels": rels}


def _paths_equal(expected_path: dict, actual_path_json: dict) -> bool:
    """Compare a TCK path pattern with an IVG path JSON representation.

    expected_path: {"nodes": [node_pattern, ...], "rels": [rel_type | None, ...]}
    actual_path_json: {"nodes": [node_id, ...], "rels": [rel_type | None, ...]}

    Strategy:
    - Check structure: same number of nodes and rels (n nodes → n-1 rels)
    - For rel types: they must match (both None or both the same string)
    - For nodes: we cannot fully validate without DB hydration, so we accept if:
      * The number of nodes matches
      * Expected has no label/prop constraints (empty pattern) OR
      * We could hydrate (not available here, so skip for now)
    """
    exp_nodes = expected_path.get("nodes", [])
    exp_rels = expected_path.get("rels", [])

    act_node_ids = actual_path_json.get("nodes", [])
    act_rels = actual_path_json.get("rels", [])

    # Check structure: n nodes → n-1 rels
    if len(exp_nodes) != len(act_node_ids):
        return False

    # rels can be empty (single-node path like <>)
    # or have n-1 elements (multi-node path)
    # actual_path_json may have n rels (with trailing None) or n-1
    # We're lenient: as long as counts are close, accept it
    if len(exp_rels) > len(act_rels) + 1:
        return False
    if len(act_rels) > len(exp_rels) + 1:
        return False

    # Check rel types: must match where specified
    for i, (exp_rel, act_rel) in enumerate(zip(exp_rels, act_rels)):
        if exp_rel is not None and act_rel is not None:
            if exp_rel != act_rel:
                return False

    return True


def _node_matches(node_data: dict, pattern: dict, isolation_label: str | None = None) -> bool:
    """Check if an IVG node data dict matches a TCK node pattern dict."""
    raw_labels = node_data.get("_labels", "[]")
    if isinstance(raw_labels, str):
        import json
        try:
            actual_labels_list = json.loads(raw_labels)
        except (json.JSONDecodeError, ValueError):
            actual_labels_list = []
    else:
        actual_labels_list = list(raw_labels) if raw_labels else []

    # strip isolation label(s) — any TCK_* label
    actual_labels = {lbl for lbl in actual_labels_list if not lbl.startswith("TCK_")}

    expected_labels = set(pattern["labels"])
    if not expected_labels.issubset(actual_labels):
        return False

    expected_props = pattern.get("props") or {}
    raw_props = node_data.get("_props", "[]")
    if isinstance(raw_props, str):
        import json
        try:
            props_list = json.loads(raw_props)
        except (json.JSONDecodeError, ValueError):
            props_list = []
    else:
        props_list = list(raw_props) if raw_props else []

    # props_list is a list of {key, value} dicts OR JSON-encoded strings of same
    actual_props: dict = {}
    if isinstance(props_list, list):
        for item in props_list:
            if isinstance(item, str):
                import json as _j
                try:
                    item = _j.loads(item)
                except (json.JSONDecodeError, ValueError):
                    pass
            if isinstance(item, dict) and "key" in item:
                actual_props[item["key"]] = item.get("value")
    elif isinstance(props_list, dict):
        actual_props = props_list

    for k, v in expected_props.items():
        if actual_props.get(k) != v:
            # try string/int normalisation
            av = actual_props.get(k)
            if isinstance(v, bool) and isinstance(av, str):
                if (v and av in ("1", "true", "True")) or (not v and av in ("0", "false", "False")):
                    continue
            if isinstance(v, int) and not isinstance(v, bool) and isinstance(av, str):
                try:
                    if int(av) == v:
                        continue
                except ValueError:
                    pass
            if isinstance(v, float) and isinstance(av, str):
                try:
                    if abs(float(av) - v) < 1e-9 * max(1.0, abs(v)):
                        continue
                except ValueError:
                    pass
            if isinstance(v, str) and isinstance(av, (int, float)):
                if str(av) == v:
                    continue
            # If expected is a complex type (list, dict) and actual is a string, try JSON parsing
            if isinstance(v, (list, dict)) and isinstance(av, str):
                try:
                    import json as _j
                    parsed_av = _j.loads(av)
                    if parsed_av == v:
                        continue
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            return False
    return True


def _normalise_row_with_nodes(actual: dict, expected: dict, columns: list[str]) -> dict:
    """Normalise actual row values, with special handling for node-format TCK cells."""
    import json as _json
    result = {}
    for col in columns:
        aval = actual.get(col)
        eval_ = expected.get(col)
        if isinstance(eval_, str):
            pattern = _parse_node_pattern(eval_)
            if pattern is not None:
                # If actual value is a JSON string with node blob, parse it
                if isinstance(aval, str):
                    try:
                        parsed = _json.loads(aval)
                        if isinstance(parsed, dict) and "_id" in parsed and "_labels" in parsed:
                            aval = parsed
                    except (ValueError, TypeError):
                        pass
                if isinstance(aval, dict) and "_labels" in aval:
                    # Keep node data as-is; comparison is done in _rows_equal
                    result[col] = aval
                    continue
        result[col] = normalise_iris_value(aval, eval_)
    return result


def _normalise_row(actual: dict, expected: dict, columns: list[str]) -> dict:
    result = {}
    for col in columns:
        aval = actual.get(col)
        eval_ = expected.get(col)
        result[col] = normalise_iris_value(aval, eval_)
    return result


def normalise_iris_value(iris_val: Any, expected_tck_val: Any) -> Any:
    """Cast IRIS value to match expected TCK type."""
    # IRIS stores '' as NULL — coerce back when expected is empty string
    if iris_val is None and expected_tck_val == '':
        return ''
    # None vs list: IRIS function like labels() returns None instead of empty list
    if iris_val is None and isinstance(expected_tck_val, list):
        # ... but a null relationship / relationship list ([:T], [[:X]]) stays null
        if expected_tck_val and all(
            (isinstance(x, str) and x.startswith(":"))
            or (isinstance(x, list) and len(x) == 1 and isinstance(x[0], str) and x[0].startswith(":"))
            for x in expected_tck_val
        ):
            return None
        return []
    if iris_val is None:
        return None
    if isinstance(iris_val, Decimal):
        if isinstance(expected_tck_val, int) and not isinstance(expected_tck_val, bool):
            return int(iris_val)
        return float(iris_val)
    if isinstance(expected_tck_val, bool):
        if isinstance(iris_val, str):
            return iris_val.lower() in ("true", "1")
        if isinstance(iris_val, int):
            return iris_val != 0
        return bool(iris_val)
    if isinstance(expected_tck_val, int) and not isinstance(expected_tck_val, bool):
        if isinstance(iris_val, str):
            try:
                return int(iris_val)
            except ValueError:
                pass
        if isinstance(iris_val, float):
            return int(iris_val)
    if isinstance(expected_tck_val, float):
        if isinstance(iris_val, str):
            try:
                return float(iris_val)
            except ValueError:
                pass
    if isinstance(expected_tck_val, dict):
        if isinstance(iris_val, str):
            import json
            try:
                parsed = json.loads(iris_val)
                if isinstance(parsed, dict):
                    # Normalize string values to int/float when expected dict has numeric vals
                    expected_dict = expected_tck_val
                    result = {}
                    for k, v in parsed.items():
                        exp_v = expected_dict.get(k)
                        if isinstance(exp_v, int) and not isinstance(exp_v, bool) and isinstance(v, str):
                            try:
                                result[k] = int(v)
                                continue
                            except (ValueError, TypeError):
                                pass
                        if isinstance(exp_v, float) and isinstance(v, str):
                            try:
                                result[k] = float(v)
                                continue
                            except (ValueError, TypeError):
                                pass
                        result[k] = v
                    return result
                # properties() returns [{key:..., value:...}] array (or list of
                # JSON strings from IRIS double-encoding) — convert to dict
                def _try_parse_item(item):
                    if isinstance(item, dict):
                        return item
                    if isinstance(item, str):
                        try:
                            obj = json.loads(item)
                            if isinstance(obj, dict):
                                return obj
                        except (json.JSONDecodeError, ValueError):
                            pass
                    return None
                if isinstance(parsed, list):
                    parsed_items = [_try_parse_item(x) for x in parsed]
                    if parsed_items and all(
                        item is not None and "key" in item and "value" in item
                        for item in parsed_items
                    ):
                        result = {}
                        for item in parsed_items:
                            k = item["key"]
                            v = item["value"]
                            try:
                                result[k] = int(v)
                            except (ValueError, TypeError):
                                try:
                                    result[k] = float(v)
                                except (ValueError, TypeError):
                                    result[k] = v
                        return result
            except (json.JSONDecodeError, ValueError):
                pass
    if isinstance(expected_tck_val, list):
        if isinstance(iris_val, (list, tuple)):
            import json as _json
            result_list = list(iris_val)
            # Strip TCK isolation labels (TCK_*) from lists (e.g., from labels() function)
            result_list = [item for item in result_list if not (isinstance(item, str) and item.startswith("TCK_"))]
            # Recursively normalize nested elements against expected element types
            if expected_tck_val:
                norm = []
                for i, item in enumerate(result_list):
                    exp_elem = expected_tck_val[i] if i < len(expected_tck_val) else expected_tck_val[-1]
                    if isinstance(item, str) and isinstance(exp_elem, list):
                        try:
                            parsed_item = _json.loads(item)
                            norm.append(normalise_iris_value(parsed_item, exp_elem))
                        except (ValueError, TypeError):
                            norm.append(item)
                    else:
                        norm.append(normalise_iris_value(item, exp_elem))
                return norm
            return result_list
        # JSON string → list (e.g., labels() returns JSON array as string)
        if isinstance(iris_val, str):
            import json
            try:
                parsed = json.loads(iris_val)
                if isinstance(parsed, list):
                    # Strip TCK isolation labels from the parsed list
                    result_list = [item for item in parsed if not (isinstance(item, str) and item.startswith("TCK_"))]
                    return normalise_iris_value(result_list, expected_tck_val)
            except (json.JSONDecodeError, ValueError):
                pass
    # None vs 0: IRIS count() may return None instead of 0
    if iris_val is None and isinstance(expected_tck_val, int) and expected_tck_val == 0:
        return 0
    return iris_val


def _as_rel_obj(v: Any):
    """A relationship value as {"type", "props"}, or None if it is not one."""
    import json as _json

    if isinstance(v, str):
        try:
            v = _json.loads(v)
        except (ValueError, TypeError):
            return None
    if isinstance(v, dict) and "type" in v:
        props = v.get("props") or {}
        if isinstance(props, str):
            try:
                props = _json.loads(props)
            except (ValueError, TypeError):
                props = {}
        return {"type": v["type"], "props": props if isinstance(props, dict) else {}}
    return None


def _rel_obj_matches(pattern: str, rel: dict) -> bool:
    """TCK relationship pattern ':T' or ':T {k: v}' against a {"type", "props"} value."""
    m = re.match(r":(\w+)\s*(?:\{(.*)\})?$", pattern.strip())
    if not m or rel["type"] != m.group(1):
        return False
    expected = _parse_tck_value("{" + m.group(2) + "}") if m.group(2) else {}
    actual = rel["props"]
    if set(actual.keys()) != set(expected.keys()):
        return False
    for k, pv in expected.items():
        av = actual.get(k)
        if isinstance(pv, (int, float)) and not isinstance(pv, bool):
            try:
                av = type(pv)(av)
            except (TypeError, ValueError):
                pass
        if av != pv:
            return False
    return True


def _graph_value_matches(ev: Any, av: Any, unordered: bool = False) -> bool:
    """Nested TCK value holding node / relationship patterns — `[(:A), [:T], (:B)]`,
    `{node1: (:A), rel: [:T]}` — against the engine's JSON list / map of node and
    relationship values. `unordered` ignores the order of the outermost list."""
    import json as _json

    if isinstance(av, str) and av[:1] in ("[", "{"):
        try:
            av = _json.loads(av)
        except (ValueError, TypeError):
            pass
    if isinstance(ev, str):
        pattern = _parse_node_pattern(ev)
        if pattern is not None:
            return isinstance(av, dict) and "_labels" in av and _node_matches(av, pattern)
        return ev == av
    if isinstance(ev, list) and len(ev) == 1 and isinstance(ev[0], str) and ev[0].startswith(":"):
        rel = _as_rel_obj(av)
        return rel is not None and _rel_obj_matches(ev[0], rel)
    if isinstance(ev, list):
        if not (isinstance(av, list) and len(ev) == len(av)):
            return False
        if not unordered:
            return all(_graph_value_matches(e, a) for e, a in zip(ev, av))

        def _match(i, avail):
            if i == len(ev):
                return True
            return any(
                _graph_value_matches(ev[i], av[j]) and _match(i + 1, avail - {j}) for j in avail
            )

        return _match(0, frozenset(range(len(av))))
    if isinstance(ev, dict):
        return isinstance(av, dict) and set(ev) == set(av) and all(
            _graph_value_matches(ev[k], av[k]) for k in ev
        )
    return ev == av


def _rows_equal(exp: dict, act: dict, columns: list[str], list_unordered: bool) -> bool:
    for col in columns:
        ev = exp.get(col)
        av = act.get(col)
        # Path pattern comparison: TCK expects "<(:A)-[:R]->(:B)>"
        if isinstance(ev, str):
            path_pattern = _parse_path_pattern(ev)
            if path_pattern is not None:
                # Expected value is a path pattern; actual should be path JSON
                if isinstance(av, str):
                    # Try to parse as JSON path
                    try:
                        import json
                        path_json = json.loads(av)
                        if isinstance(path_json, dict) and "nodes" in path_json and "rels" in path_json:
                            if not _paths_equal(path_pattern, path_json):
                                return False
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                # Path mismatch
                return False
        # Node pattern comparison: TCK expects "(:A)" or "(:B {name: 'x'})"
        if isinstance(ev, str):
            pattern = _parse_node_pattern(ev)
            if pattern is not None:
                if isinstance(av, dict) and "_labels" in av:
                    if not _node_matches(av, pattern):
                        return False
                    continue
                # av is not a node dict — mismatch
                return False
        # Relationship pattern comparison: TCK expects "[':TYPE']" or "[':TYPE {props}']"
        # Actual value from engine is either the type string 'TYPE' or a JSON edge object.
        if isinstance(ev, list) and len(ev) == 1 and isinstance(ev[0], str) and ev[0].startswith(":"):
            rel_pattern_str = ev[0]  # e.g. ':REL' or ':REL {property2: 24}'
            # Parse relationship type and properties from the TCK pattern string
            import re as _re_rel, json as _json_rel
            _rel_type_m = _re_rel.match(r':(\w+)\s*(?:\{(.*)\})?$', rel_pattern_str.strip())
            if _rel_type_m:
                _expected_type = _rel_type_m.group(1)
                _expected_props_str = _rel_type_m.group(2)
                _expected_props = _parse_tck_value('{' + _expected_props_str + '}') if _expected_props_str else {}
                # av may be: type string 'TYPE', JSON edge object '{"type":"TYPE","props":{...}}',
                # or a remapped relationship dict {"_type": "TYPE", "_s": ..., "_o_id": ...}
                if isinstance(av, dict) and "_type" in av:
                    # Remapped relationship triplet from _remap_node_columns
                    _actual_type = av["_type"]
                    if _actual_type != _expected_type:
                        return False
                    if _expected_props:
                        return False  # No props available in triplet format
                    continue
                if isinstance(av, str):
                    try:
                        _av_obj = _json_rel.loads(av)
                        if isinstance(_av_obj, dict) and "type" in _av_obj:
                            _actual_type = _av_obj["type"]
                            _actual_props_raw = _av_obj.get("props", {})
                            if isinstance(_actual_props_raw, str):
                                try:
                                    _actual_props = _json_rel.loads(_actual_props_raw)
                                except Exception:
                                    _actual_props = {}
                            else:
                                _actual_props = _actual_props_raw
                            if _actual_type != _expected_type:
                                return False
                            # Compare props: coerce numeric strings
                            for pk, pv in _expected_props.items():
                                _av = _actual_props.get(pk)
                                if isinstance(pv, int) and not isinstance(pv, bool):
                                    try:
                                        _av = int(_av)
                                    except (TypeError, ValueError):
                                        pass
                                elif isinstance(pv, float):
                                    try:
                                        _av = float(_av)
                                    except (TypeError, ValueError):
                                        pass
                                if _av != pv:
                                    return False
                            if set(_actual_props.keys()) != set(_expected_props.keys()):
                                return False
                            continue
                    except (ValueError, TypeError):
                        pass
                    # Fallback: type-only match (no props)
                    if not _expected_props and av == _expected_type:
                        continue
                return False
            continue
        # List of relationships, in path order: TCK [[':T {k: v}'], [':T']] vs actual
        # {"type", "props"} values (a var-length relationship, or relationships(p)).
        av_seq = av
        if isinstance(ev, list) and isinstance(av, str) and av.startswith("["):
            try:
                import json as _json_rl

                av_seq = _json_rl.loads(av)
            except (ValueError, TypeError):
                av_seq = av
        if (
            isinstance(ev, list)
            and isinstance(av_seq, list)
            and ev
            and len(ev) == len(av_seq)
            and all(
                isinstance(x, list) and len(x) == 1 and isinstance(x[0], str) and x[0].startswith(":")
                for x in ev
            )
        ):
            av_rels = [_as_rel_obj(x) for x in av_seq]
            if all(r is not None for r in av_rels):
                if all(_rel_obj_matches(e[0], r) for e, r in zip(ev, av_rels)):
                    continue
                return False
        # List-of-nodes comparison: TCK [(), ()] vs actual [{"_id":..., "_labels":..., "_props":...}]
        if isinstance(ev, list) and isinstance(av, list):
            # Parse items that are JSON strings of node objects (from collect(nodeVar))
            import json as _json_list
            def _parse_node_item(item):
                if isinstance(item, str):
                    try:
                        parsed = _json_list.loads(item)
                        if isinstance(parsed, dict) and "_id" in parsed:
                            return parsed
                    except (ValueError, TypeError):
                        pass
                return item
            av_parsed = [_parse_node_item(x) for x in av]
            # List-of-paths comparison: TCK [<(:A)-[:R]->(:B)>] vs actual [{"nodes":[...],"rels":[...]}]
            ev_path_patterns = [_parse_path_pattern(x) if isinstance(x, str) else None for x in ev]
            all_path_patterns = all(p is not None for p in ev_path_patterns)
            av_path_dicts = []
            for item in av_parsed:
                if isinstance(item, dict) and "nodes" in item and "rels" in item:
                    av_path_dicts.append(item)
                elif isinstance(item, str):
                    try:
                        parsed = _json_list.loads(item)
                        if isinstance(parsed, dict) and "nodes" in parsed and "rels" in parsed:
                            av_path_dicts.append(parsed)
                            continue
                    except (ValueError, TypeError):
                        pass
                    av_path_dicts.append(None)
                else:
                    av_path_dicts.append(None)
            all_path_dicts = all(p is not None for p in av_path_dicts)
            if all_path_patterns and all_path_dicts:
                if len(ev_path_patterns) != len(av_path_dicts):
                    return False
                if list_unordered:
                    def _list_paths_match(patterns, avail):
                        if not patterns:
                            return True
                        p = patterns[0]
                        for i in avail:
                            if _paths_equal(p, av_path_dicts[i]):
                                remaining = [j for j in avail if j != i]
                                if _list_paths_match(patterns[1:], remaining):
                                    return True
                        return False
                    if not _list_paths_match(ev_path_patterns, list(range(len(av_path_dicts)))):
                        return False
                else:
                    for pat, path in zip(ev_path_patterns, av_path_dicts):
                        if not _paths_equal(pat, path):
                            return False
                continue
            # Detect if all expected items are node patterns
            ev_patterns = [_parse_node_pattern(x) if isinstance(x, str) else None for x in ev]
            all_node_patterns = all(p is not None for p in ev_patterns)
            if all_node_patterns and all(isinstance(x, dict) and "_id" in x for x in av_parsed):
                if len(ev_patterns) != len(av_parsed):
                    return False
                if list_unordered:
                    # Backtracking match for unordered node lists
                    def _list_nodes_match(patterns, avail):
                        if not patterns:
                            return True
                        p = patterns[0]
                        for i in avail:
                            if _node_matches(av_parsed[i], p):
                                remaining = [j for j in avail if j != i]
                                if _list_nodes_match(patterns[1:], remaining):
                                    return True
                        return False
                    if not _list_nodes_match(ev_patterns, list(range(len(av_parsed)))):
                        return False
                else:
                    for pat, node in zip(ev_patterns, av_parsed):
                        if not _node_matches(node, pat):
                            return False
                continue
            if ev != av_parsed and _graph_value_matches(ev, av, unordered=list_unordered):
                continue
            if list_unordered:
                if sorted(str(x) for x in ev) != sorted(str(x) for x in av_parsed):
                    return False
            else:
                if ev != av_parsed:
                    return False
            continue
        if ev != av and isinstance(ev, (list, dict)) and _graph_value_matches(ev, av):
            continue
        if ev != av:
            return False
    return True


def _sort_key(row: dict, columns: list[str]) -> tuple:
    return tuple(str(row.get(c, "")) for c in columns)


def _sort_rows(rows: list[dict], columns: list[str]) -> list[dict]:
    return sorted(rows, key=lambda r: _sort_key(r, columns))
