"""TCK result comparison: value parsing and table diff."""
from __future__ import annotations

import contextlib
import json
import math
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




# ---------------------------------------------------------------------------
# Expected values: a TCK result cell parsed into typed values, with graph
# values (nodes, relationships, paths) as their own types rather than strings.
# ---------------------------------------------------------------------------


@dataclass
class ExpNode:
    labels: frozenset
    props: dict

    def __repr__(self) -> str:
        return f"({''.join(':' + lb for lb in sorted(self.labels))} {self.props})"


@dataclass
class ExpRel:
    type: str
    props: dict

    def __repr__(self) -> str:
        return f"[:{self.type} {self.props}]"


@dataclass
class ExpPath:
    nodes: list
    rels: list  # (ExpRel, forward: bool), one per hop

    def __repr__(self) -> str:
        out = [repr(self.nodes[0])] if self.nodes else []
        for (rel, fwd), node in zip(self.rels, self.nodes[1:]):
            out.append(f"-{rel!r}->" if fwd else f"<-{rel!r}-")
            out.append(repr(node))
        return "<" + "".join(out) + ">"


_INT_TEXT = re.compile(r"-?\d+")
_FLOAT_TEXT = re.compile(
    r"-?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|-?\d+[eE][-+]?\d+|-?(?:NaN|Infinity|inf|nan)"
)


class _ExpectedParser:
    """Recursive-descent reader for a TCK result cell."""

    _TOKEN_END = set(" \t\n,]})>")

    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def fail(self, msg: str):
        raise ValueError(f"TCK cell {self.s!r}: {msg} at offset {self.i}")

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\n\r":
            self.i += 1

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def expect(self, ch: str):
        self.ws()
        if self.peek() != ch:
            self.fail(f"expected {ch!r}")
        self.i += 1

    def ident(self) -> str:
        self.ws()
        if self.peek() == "`":
            end = self.s.index("`", self.i + 1)
            name = self.s[self.i + 1:end]
            self.i = end + 1
            return name
        j = self.i
        while j < len(self.s) and (self.s[j].isalnum() or self.s[j] == "_"):
            j += 1
        if j == self.i:
            self.fail("expected a name")
        name, self.i = self.s[self.i:j], j
        return name

    def value(self) -> Any:
        self.ws()
        c = self.peek()
        if c == "'":
            return self.string()
        if c == "[":
            j = self.i + 1
            while j < len(self.s) and self.s[j] in " \t":
                j += 1
            if j < len(self.s) and self.s[j] == ":":
                return self.rel()
            return self.list_()
        if c == "{":
            return self.map_()
        if c == "(":
            return self.node()
        if c == "<":
            return self.path()
        return self.token()

    def string(self) -> str:
        j = self.i + 1
        while j < len(self.s):
            if self.s[j] == "\\":
                j += 2
                continue
            if self.s[j] == "'":
                break
            j += 1
        if j >= len(self.s):
            self.fail("unterminated string")
        raw, self.i = self.s[self.i:j + 1], j + 1
        return _parse_tck_value(raw)

    def token(self) -> Any:
        j = self.i
        while j < len(self.s) and self.s[j] not in self._TOKEN_END:
            j += 1
        tok, self.i = self.s[self.i:j], j
        if tok == "null":
            return None
        if tok in ("true", "false"):
            return tok == "true"
        if tok == "NaN":
            return float("nan")
        if tok in ("Infinity", "-Infinity"):
            return float(tok.replace("Infinity", "inf"))
        if _INT_TEXT.fullmatch(tok):
            return int(tok)
        try:
            return float(tok)
        except ValueError:
            self.fail(f"unreadable value {tok!r}")

    def list_(self) -> list:
        self.expect("[")
        out: list = []
        self.ws()
        if self.peek() == "]":
            self.i += 1
            return out
        while True:
            out.append(self.value())
            self.ws()
            if self.peek() == ",":
                self.i += 1
                continue
            self.expect("]")
            return out

    def map_(self) -> dict:
        self.expect("{")
        out: dict = {}
        self.ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            key = self.ident()
            self.expect(":")
            out[key] = self.value()
            self.ws()
            if self.peek() == ",":
                self.i += 1
                continue
            self.expect("}")
            return out

    def _labels_and_props(self, close: str):
        labels: list[str] = []
        props: dict = {}
        self.ws()
        if self.peek() not in (":", "{", close):
            self.ident()  # a variable name carries no value
        self.ws()
        while self.peek() == ":":
            self.i += 1
            labels.append(self.ident())
            self.ws()
        if self.peek() == "{":
            props = self.map_()
        self.expect(close)
        return labels, props

    def node(self) -> ExpNode:
        self.expect("(")
        labels, props = self._labels_and_props(")")
        return ExpNode(frozenset(labels), props)

    def rel(self) -> ExpRel:
        self.expect("[")
        labels, props = self._labels_and_props("]")
        if len(labels) != 1:
            self.fail("a relationship has exactly one type")
        return ExpRel(labels[0], props)

    def path(self) -> ExpPath:
        self.expect("<")
        nodes = [self.node()]
        rels: list = []
        while True:
            self.ws()
            if self.peek() == ">":
                self.i += 1
                return ExpPath(nodes, rels)
            if self.peek() == "<":
                self.i += 1
                self.expect("-")
                rel = self.rel()
                self.expect("-")
                rels.append((rel, False))
            else:
                self.expect("-")
                rel = self.rel()
                self.expect("-")
                self.expect(">")
                rels.append((rel, True))
            nodes.append(self.node())


def parse_expected(cell: str) -> Any:
    """A TCK result cell as a typed value: None, bool, int, float, str, list, dict,
    ExpNode, ExpRel or ExpPath. `'(:A)'` is a string, `(:A)` is a node."""
    p = _ExpectedParser(cell.strip())
    v = p.value()
    p.ws()
    if p.i != len(p.s):
        p.fail("trailing text")
    return v


def _parse_node_pattern(s: str) -> dict | None:
    """A node cell as {"labels": [...], "props": {...}}; None if it is not a node."""
    try:
        v = parse_expected(s)
    except ValueError:
        return None
    if not isinstance(v, ExpNode):
        return None
    return {"labels": sorted(v.labels), "props": v.props}


# ---------------------------------------------------------------------------
# Actual values. IVG returns graph values as JSON text and many scalars as
# VARCHAR text, so the actual side is decoded against the expected value's
# shape, but never loosened: text becomes a number only when it is the
# number's own spelling (`1` is an integer, `1.0` a float), JSON keeps its
# types, and nothing but null equals null.
# ---------------------------------------------------------------------------


class SqlHydrator:
    """Reads nodes and relationships back from the tables by id, so a path (which the
    engine returns as ids and types only) and a node's typed properties can be checked."""

    def __init__(self, conn, schema: str = "Graph_KG"):
        self.conn = conn
        self.schema = schema
        self._nodes: dict = {}
        self._edges: dict = {}

    def _rows(self, sql: str, params: list):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            with contextlib.suppress(Exception):
                cur.close()

    @staticmethod
    def _default_graph(g) -> bool:
        return g in (None, "")

    def node(self, node_id: str):
        if node_id in self._nodes:
            return self._nodes[node_id]
        s = self.schema
        exists = [r for r in self._rows(f"SELECT graph_id FROM {s}.nodes WHERE node_id = ?", [node_id])
                  if self._default_graph(r[0])]
        if not exists:
            self._nodes[node_id] = None
            return None
        labels = [r[0] for r in self._rows(f"SELECT label, graph_id FROM {s}.rdf_labels WHERE s = ?", [node_id])
                  if self._default_graph(r[1])]
        # A NULL-valued row (left by SET n.p = null) is an absent property.
        props = {r[0]: r[1] for r in self._rows(f"SELECT key, val, graph_id FROM {s}.rdf_props WHERE s = ?", [node_id])
                 if self._default_graph(r[2]) and r[1] is not None}
        self._nodes[node_id] = {"labels": labels, "props": props}
        return self._nodes[node_id]

    def edges(self, s_id: str, o_id: str, rel_type: str) -> list[dict]:
        key = (s_id, o_id, rel_type)
        if key not in self._edges:
            s = self.schema
            rows = self._rows(
                f"SELECT qualifiers, graph_id FROM {s}.rdf_edges WHERE s = ? AND p = ? AND o_id = ?",
                [s_id, rel_type, o_id],
            )
            self._edges[key] = [_as_props(r[0]) for r in rows if self._default_graph(r[1])]
        return self._edges[key]


_ISOLATION_LABEL = re.compile(r"TCK_[0-9a-f]{8}")


def _without_isolation_labels(actual: list, expected: list) -> list:
    """The harness tags every scenario node with a TCK_xxxxxxxx label, which labels()
    then returns. Such a string is dropped unless the expected list holds it."""
    keep = {e for e in expected if isinstance(e, str)}
    return [a for a in actual
            if not (isinstance(a, str) and _ISOLATION_LABEL.fullmatch(a) and a not in keep)]


def _json_or(v: Any, typ: type) -> Any:
    """`v` if it already is `typ`, the JSON it spells if that is `typ`, else None."""
    if isinstance(v, typ):
        return v
    if isinstance(v, tuple) and typ is list:
        return list(v)
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, typ) else None
    return None


def _as_props(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    d = _json_or(raw, dict)
    return d if d is not None else {}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _float_eq(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


def _decimal(av: Decimal, ev: Any) -> Any:
    """IRIS NUMERIC carries no Cypher type: an integral value reads as an integer
    unless a float is expected; a fractional one is always a float."""
    if isinstance(ev, float) or av != av.to_integral_value():
        return float(av)
    return int(av)


def normalise_iris_value(iris_val: Any, expected_tck_val: Any) -> Any:
    """An IRIS scalar decoded against the expected value's type, without loosening it:
    text becomes a bool only as `true`/`false`, an integer only as integer digits, a
    float only in float syntax; a float never becomes an integer."""
    if isinstance(iris_val, Decimal):
        return _decimal(iris_val, expected_tck_val)
    if not isinstance(iris_val, str):
        return list(iris_val) if isinstance(iris_val, tuple) else iris_val
    t = iris_val.strip()
    if isinstance(expected_tck_val, bool):
        return {"true": True, "false": False}.get(t, iris_val)
    if _is_int(expected_tck_val) and _INT_TEXT.fullmatch(t):
        return int(t)
    if isinstance(expected_tck_val, float) and _FLOAT_TEXT.fullmatch(t):
        return float(t.replace("Infinity", "inf"))
    if isinstance(expected_tck_val, (list, dict)):
        parsed = _json_or(iris_val, type(expected_tck_val))
        if parsed is not None:
            return parsed
    return iris_val


class _Matcher:
    def __init__(self, hydrator=None):
        self.hydrator = hydrator

    # -- scalars / collections ------------------------------------------------

    def match(self, ev: Any, av: Any, text_ok: bool = True, unordered: bool = False) -> bool:
        """`text_ok`: `av` came through a VARCHAR, so `'1'` may spell the integer 1.
        Inside decoded JSON and hydrated properties a string is only ever a string."""
        if isinstance(av, Decimal):
            av = _decimal(av, ev)
        if isinstance(av, tuple):
            av = list(av)
        if ev is None:
            return av is None
        if av is None:
            return False
        if isinstance(ev, ExpNode):
            return self.node(ev, av)
        if isinstance(ev, ExpRel):
            return self.rel(ev, av)
        if isinstance(ev, ExpPath):
            return self.path(ev, av)
        if isinstance(ev, bool):
            if isinstance(av, bool):
                return av == ev
            return text_ok and isinstance(av, str) and av.strip() == ("true" if ev else "false")
        if _is_int(ev):
            if _is_int(av):
                return av == ev
            return text_ok and isinstance(av, str) and bool(_INT_TEXT.fullmatch(av.strip())) and int(av) == ev
        if isinstance(ev, float):
            if isinstance(av, float):
                return _float_eq(av, ev)
            if text_ok and isinstance(av, str) and _FLOAT_TEXT.fullmatch(av.strip()):
                return _float_eq(float(av.strip().replace("Infinity", "inf")), ev)
            return False
        if isinstance(ev, str):
            return isinstance(av, str) and av == ev
        if isinstance(ev, list):
            al = _json_or(av, list)
            if al is None:
                return False
            al = _without_isolation_labels(al, ev)
            if len(al) != len(ev):
                return False
            if unordered:
                return _perfect_matching(len(ev), len(al), lambda i, j: self.match(ev[i], al[j], text_ok=False))
            return all(self.match(e, a, text_ok=False) for e, a in zip(ev, al))
        if isinstance(ev, dict):
            ad = _json_or(av, dict)
            if ad is None or set(ad) != set(ev):
                return False
            return all(self.match(ev[k], ad[k], text_ok=False) for k in ev)
        return False

    def stored(self, ev: Any, text: Any, float_from_int_text: bool = False) -> bool:
        """A property value as the engine renders it inside a node / relationship value:
        text, with lists and maps as JSON. `float_from_int_text`: node renderings drop a
        float's `.0` (`1.0` -> `"1"`); the hydrated check then carries the type."""
        if not isinstance(text, str):
            return self.match(ev, text, text_ok=False)
        if isinstance(ev, float) and float_from_int_text and _INT_TEXT.fullmatch(text.strip()):
            return float(text) == ev
        if isinstance(ev, (list, dict)):
            return self.match(ev, text, text_ok=False)
        return self.match(ev, text, text_ok=True)

    # -- nodes ---------------------------------------------------------------

    @staticmethod
    def _node_obj(av: Any):
        d = _json_or(av, dict)
        if d is None or "_labels" not in d:
            return None
        return d

    @staticmethod
    def _labels(raw: Any) -> set:
        lst = _json_or(raw, list) if raw is not None else []
        return {lb for lb in (lst or []) if not (isinstance(lb, str) and lb.startswith("TCK_"))}

    @staticmethod
    def _blob_props(raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        items = _json_or(raw, list) if raw is not None else []
        out: dict = {}
        for item in items or []:
            if isinstance(item, str):
                item = _json_or(item, dict)
            if isinstance(item, dict) and "key" in item:
                out[item["key"]] = item.get("value")
        return out

    def node(self, ev: ExpNode, av: Any) -> bool:
        d = self._node_obj(av)
        if d is None:
            return False
        if self._labels(d.get("_labels")) != set(ev.labels):
            return False
        props = self._blob_props(d.get("_props"))
        if set(props) != set(ev.props):
            return False
        if not all(self.stored(ev.props[k], props[k], float_from_int_text=True) for k in ev.props):
            return False
        return self.hydrated_node(ev, d.get("_id"), required=False)

    def hydrated_node(self, ev: ExpNode, node_id: Any, required: bool) -> bool:
        """The node as stored, with typed property values. `required`: the value carried
        only an id (a path), so a node that cannot be read back is a mismatch."""
        if self.hydrator is None or node_id is None:
            return not required
        h = self.hydrator.node(node_id)
        if h is None:
            return not required
        if self._labels(h["labels"]) != set(ev.labels):
            return False
        if set(h["props"]) != set(ev.props):
            return False
        return all(self._hydrated_value(ev.props[k], h["props"][k]) for k in ev.props)

    def _hydrated_value(self, ev: Any, hv: Any) -> bool:
        # rdf_props.val carries no type tag: a boolean is stored as the text 'true' /
        # 'false' (never '1' / '0' on new writes), and nothing else distinguishes it.
        if isinstance(ev, bool) and isinstance(hv, str):
            return hv == ("true" if ev else "false")
        return self.match(ev, hv, text_ok=False)

    # -- relationships ---------------------------------------------------------

    def _rel_props_match(self, ev: ExpRel, props: dict) -> bool:
        return set(props) == set(ev.props) and all(self.stored(ev.props[k], props[k]) for k in ev.props)

    def rel(self, ev: ExpRel, av: Any) -> bool:
        d = _json_or(av, dict)
        if d is None:
            return False
        if "type" in d and "_labels" not in d:
            return d["type"] == ev.type and self._rel_props_match(ev, _as_props(d.get("props")))
        if "_type" in d:  # RETURN r expanded to r_s / r_p / r_o_id
            if d["_type"] != ev.type:
                return False
            if self.hydrator is None:
                return not ev.props
            cands = self.hydrator.edges(d.get("_s"), d.get("_o_id"), ev.type)
            return any(self._rel_props_match(ev, c) for c in cands)
        return False

    # -- paths -----------------------------------------------------------------

    def path(self, ev: ExpPath, av: Any) -> bool:
        d = _json_or(av, dict)
        if d is None or "nodes" not in d or "rels" not in d:
            return False
        nodes, rels = d["nodes"] or [], d["rels"] or []
        if len(nodes) != len(ev.nodes) or len(rels) != len(ev.rels):
            return False
        if self.hydrator is None:
            return False  # a path is ids and types; without the store it cannot be checked
        ids = []
        for exp_node, n in zip(ev.nodes, nodes):
            if isinstance(n, dict):
                if "_labels" in n and not self.node(exp_node, n):
                    return False
                n = n.get("_id", n.get("id"))
            ids.append(n)
            if not self.hydrated_node(exp_node, n, required=True):
                return False
        for i, ((exp_rel, forward), r) in enumerate(zip(ev.rels, rels)):
            if isinstance(r, dict):
                if not self.rel(exp_rel, r):
                    return False
                r = r.get("type")
            if r != exp_rel.type:
                return False
            s, o = (ids[i], ids[i + 1]) if forward else (ids[i + 1], ids[i])
            if not any(self._rel_props_match(exp_rel, c) for c in self.hydrator.edges(s, o, exp_rel.type)):
                return False
        return True


def _why(m: _Matcher, ev: Any, av: Any, depth: int = 0) -> str:
    """A short tag naming why `av` is not `ev`, for the failure message (diagnostic only;
    it never decides a match)."""
    if isinstance(av, Decimal):
        av = _decimal(av, ev)
    if ev is None:
        if av in ([], {}, "", "[]", "{}", 0):
            return "null-got-empty"
        return "null-got-value"
    if av is None:
        return "value-got-null" if ev not in ([], {}, "") else "empty-got-null"
    if isinstance(ev, bool):
        if _is_int(av) or (isinstance(av, str) and av.strip() in ("0", "1")):
            return "bool-got-int"
        return "bool"
    if _is_int(ev):
        if isinstance(av, float) or (isinstance(av, str) and _FLOAT_TEXT.fullmatch(av.strip())):
            return "int-got-float" if float(av) == ev else "int-value"
        if isinstance(av, str) and not _INT_TEXT.fullmatch(av.strip()):
            return "int-got-text"
        return "int-value"
    if isinstance(ev, float):
        if _is_int(av) or (isinstance(av, str) and _INT_TEXT.fullmatch(av.strip())):
            return "float-got-int" if float(av) == ev else "float-value"
        return "float-value"
    if isinstance(ev, str):
        return "string-got-" + type(av).__name__ if not isinstance(av, str) else "string-value"
    if isinstance(ev, ExpNode):
        d = m._node_obj(av)
        if d is None:
            return "node-got-nonnode"
        labels = m._labels(d.get("_labels"))
        if labels != set(ev.labels):
            return "node-extra-labels" if labels > set(ev.labels) else "node-labels"
        props = m._blob_props(d.get("_props"))
        if set(props) != set(ev.props):
            return "node-extra-props" if set(props) > set(ev.props) else "node-prop-keys"
        if not all(m.stored(ev.props[k], props[k], float_from_int_text=True) for k in ev.props):
            return "node-prop-value"
        return "node-prop-type(hydrated)"
    if isinstance(ev, ExpRel):
        d = _json_or(av, dict)
        if d is None:
            return "rel-got-" + type(av).__name__
        t = d.get("type", d.get("_type"))
        if t != ev.type:
            return "rel-type"
        props = _as_props(d.get("props")) if "type" in d else None
        if props is not None and set(props) > set(ev.props):
            return "rel-extra-props"
        return "rel-props"
    if isinstance(ev, ExpPath):
        d = _json_or(av, dict)
        if d is None or "nodes" not in d:
            return "path-got-nonpath"
        if len(d.get("nodes") or []) != len(ev.nodes) or len(d.get("rels") or []) != len(ev.rels):
            return "path-length"
        if m.hydrator is None:
            return "path-unhydrated"
        flipped = ExpPath(ev.nodes, [(r, not f) for r, f in ev.rels])
        if m.path(flipped, av):
            return "path-direction"
        return "path-node-or-rel"
    if isinstance(ev, (list, dict)):
        typ = type(ev)
        a = _json_or(av, typ)
        if a is None:
            return f"{typ.__name__}-got-" + type(av).__name__
        if len(a) != len(ev):
            return f"{typ.__name__}-length"
        if typ is dict:
            if set(a) != set(ev):
                return "map-keys"
            pairs = [(ev[k], a[k]) for k in ev]
        else:
            pairs = list(zip(ev, a))
        for e, x in pairs:
            if not m.match(e, x, text_ok=False):
                inner = _why(m, e, x, depth + 1)
                if isinstance(x, str) and not isinstance(e, (str, ExpNode, ExpRel, ExpPath, list, dict)):
                    inner = "text-in-json(" + inner + ")"
                return f"in-{typ.__name__}:{inner}"
        return f"{typ.__name__}-order"
    return "value"


def _row_diff(m: _Matcher, exp_row: list, act_row: list, columns: list[str], list_unordered: bool) -> str:
    out = []
    for col, e, a in zip(columns, exp_row, act_row):
        if not m.match(e, a, text_ok=True, unordered=list_unordered):
            out.append(f"{col}: {_why(m, e, a)} (expected {e!r}, got {a!r})")
    return "; ".join(out)


def _node_matches(node_data: dict, pattern: dict, isolation_label: str | None = None) -> bool:
    """An IVG node value against a {"labels", "props"} pattern: labels and properties exact."""
    exp = ExpNode(frozenset(pattern.get("labels") or []), dict(pattern.get("props") or {}))
    return _Matcher().node(exp, node_data)


def _perfect_matching(n_exp: int, n_act: int, ok) -> bool:
    """Whether every expected item pairs with a distinct actual item (Kuhn's algorithm)."""
    if n_exp != n_act:
        return False
    cache: dict = {}

    def edge(i, j):
        if (i, j) not in cache:
            cache[(i, j)] = ok(i, j)
        return cache[(i, j)]

    owner = [-1] * n_act
    for i in range(n_exp):  # greedy first: exact values almost always pair directly
        for j in range(n_act):
            if owner[j] == -1 and edge(i, j):
                owner[j] = i
                break
    placed = set(owner) - {-1}

    def augment(i, seen):
        for j in range(n_act):
            if j in seen or not edge(i, j):
                continue
            seen.add(j)
            if owner[j] == -1 or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    return all(i in placed or augment(i, set()) for i in range(n_exp))


# ---------------------------------------------------------------------------
# Columns and tables
# ---------------------------------------------------------------------------


def _collapse_columns(actual_columns: list[str]) -> list[tuple[str, tuple[str, ...], str]]:
    """IVG returns a node column `n` as n_id / n_labels / n_props and a relationship
    column `r` as r_s / r_p / r_o_id. Each entry: (name, source columns, kind)."""
    present = set(actual_columns)
    out = []
    used: set = set()
    for c in actual_columns:
        if c in used:
            continue
        for suffixes, kind in ((("_id", "_labels", "_props"), "node"), (("_s", "_p", "_o_id"), "rel")):
            if c.endswith(suffixes[0]):
                base = c[: -len(suffixes[0])]
                group = tuple(base + s for s in suffixes)
                if base and all(g in present for g in group):
                    out.append((base, group, kind))
                    used.update(group)
                    break
        else:
            out.append((c, (c,), "plain"))
            used.add(c)
    return out


def _collapsed_value(row: dict, group: tuple[str, ...], kind: str) -> Any:
    vals = [row.get(g) for g in group]
    if kind == "plain":
        return vals[0]
    if kind == "node":
        nid, labels, props = vals
        if nid is None:
            return None  # OPTIONAL MATCH miss
        return {"_id": nid, "_labels": labels, "_props": props}
    s, p, o = vals
    if s is None and p is None and o is None:
        return None
    return {"_type": p, "_s": s, "_o_id": o}


def _remap_node_columns(actual_row: dict, tck_columns: list[str], actual_columns: list[str]) -> dict:
    """The row with node / relationship column groups collapsed to one value each."""
    out = dict(actual_row)
    for name, group, kind in _collapse_columns(actual_columns):
        if kind != "plain":
            out[name] = _collapsed_value(actual_row, group, kind)
    return out


@dataclass
class TCKResultTable:
    columns: list[str]
    rows: list[list[TCKValue]]
    ordered: bool
    list_unordered: bool

    def compare(self, actual_rows: list[dict], actual_columns: list[str], hydrator=None) -> str | None:
        """None when the result equals the table exactly, else a description of the first
        difference. Columns must be the expected header, by name and in order."""
        groups = _collapse_columns(list(actual_columns))
        got_cols = [g[0] for g in groups]
        if got_cols != list(self.columns):
            return f"Column mismatch: expected {list(self.columns)}, got {got_cols} (raw {list(actual_columns)})"

        expected = [[parse_expected(cell.raw) for cell in row] for row in self.rows]
        actual = [[_collapsed_value(r, g, k) for _, g, k in groups] for r in actual_rows]
        if len(actual) != len(expected):
            return (
                f"Row count mismatch: expected {len(expected)}, got {len(actual)}\n"
                f"Expected: {expected}\nActual:   {actual}"
            )
        m = _Matcher(hydrator)

        def row_eq(i: int, j: int) -> bool:
            return all(
                m.match(e, a, text_ok=True, unordered=self.list_unordered)
                for e, a in zip(expected[i], actual[j])
            )

        cols = list(self.columns)
        if self.ordered:
            for i in range(len(expected)):
                if not row_eq(i, i):
                    return (
                        f"Row {i} mismatch: {_row_diff(m, expected[i], actual[i], cols, self.list_unordered)}\n"
                        f"  expected: {expected[i]}\n  actual:   {actual[i]}"
                    )
            return None
        if not _perfect_matching(len(expected), len(actual), row_eq):
            return (
                "Unordered comparison: no perfect matching found: "
                f"{self._unmatched(m, expected, actual, row_eq)}\n"
                f"Expected: {expected}\nActual:   {actual}"
            )
        return None

    def _unmatched(self, m, expected, actual, row_eq) -> str:
        """The first expected row no actual row equals, against its closest actual row."""
        cols = list(self.columns)
        for i, exp in enumerate(expected):
            if any(row_eq(i, j) for j in range(len(actual))):
                continue
            best = max(
                range(len(actual)),
                key=lambda j: sum(
                    m.match(e, a, text_ok=True, unordered=self.list_unordered)
                    for e, a in zip(exp, actual[j])
                ),
            )
            return f"row {i}: {_row_diff(m, exp, actual[best], cols, self.list_unordered)}"
        return "multiplicity (every expected row has an equal actual row, but not one each)"
