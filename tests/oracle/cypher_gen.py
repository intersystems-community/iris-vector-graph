"""Seeded read-only Cypher queries over a seeded graph, each with the answer a small reference interpreter works out.

4.6.0 R308. IVG answers a read through the native executor over ^KG, the SQL translation, and the SQL path's own
routes (the variable-length BFS routes, the edge scan). tests/e2e/test_460_r308_cypher_differential_e2e.py runs every
query here through each of them and compares the answers with each other and with `Query.evaluate`.

What is here:

- `make_graph(seed)`: a graph of ~15 nodes in the default graph and a named one, labels A and B (prefixed), relationship
  types R and S, node properties of every type (string, integer, float, boolean, list), relationship properties of
  every type (and lists of floats, maps), some missing.
- A query builder (`Q`, `match`, `pat`, `N`, `R`, `ret`, `with_`, `unwind`, expressions such as `prop`, `cmp`, `agg`)
  that renders Cypher text and evaluates the same structure in Python by openCypher's rules: patterns match trails (no
  relationship twice in one MATCH), null is three-valued, OPTIONAL MATCH binds nulls, aggregates group by the other
  columns, and a query without `USE GRAPH` reads every graph (each pattern inside one graph).
- `generate(graph, seed, count)`: queries of the families in FAMILIES, each small. `forms` holds texts that must give
  the same answer (a property map written as a WHERE, say), which reach other routes.
- `hand_graph()` and `hand_cases()`: a fixed small graph and queries whose answers are worked by hand (in the source,
  next to each query), never taken from IVG. tests/unit/test_460_r308_cypher_gen.py checks the interpreter against
  them.
- `same_rows`: the comparison. Values compare by type (the string '1', the integer 1 and the float 1.0 are three
  values), floats to 9 decimals; rows as a multiset unless the query orders them; a list whose order Cypher leaves open
  (`collect`, `labels`) as a multiset.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

FAMILIES = (
    "fixed", "where", "varlen", "varlen_props", "pathfn", "optional", "with", "agg", "list", "case", "unwind",
    "funcs", "graph", "pattern_comp", "optional_varlen",
)


class NoReference(Exception):
    """The interpreter has no answer for this expression (id(), elementId(): IVG's own representation)."""


# ---------------------------------------------------------------- the graph


@dataclass(eq=False)
class GNode:
    id: str
    labels: Tuple[str, ...]
    props: Dict[str, Any]
    graph: str


@dataclass(eq=False)
class GEdge:
    src: str
    type: str
    dst: str
    props: Dict[str, Any]
    graph: str


@dataclass(eq=False)
class PathV:
    nodes: List[GNode]
    edges: List[GEdge]


@dataclass
class Graph:
    nodes: List[GNode]
    edges: List[GEdge]
    labels: Tuple[str, str]
    types: Tuple[str, str]
    named: str
    id_prefix: str

    def __post_init__(self):
        self.by_key = {(n.graph, n.id): n for n in self.nodes}
        self.out: Dict[Tuple[str, str], List[GEdge]] = {}
        self.inn: Dict[Tuple[str, str], List[GEdge]] = {}
        for e in self.edges:
            self.out.setdefault((e.graph, e.src), []).append(e)
            self.inn.setdefault((e.graph, e.dst), []).append(e)

    def graphs(self) -> List[str]:
        return ["", self.named]

    def node(self, graph: str, nid: str) -> GNode:
        return self.by_key[(graph, nid)]


_NAMES = ["ada", "bo", "cy", "di", "ed", "fay", "gus", "hal", "ivy", "jo", "kit", "lu", "mo", "ned", "oz", "pam",
          "quin", "rex"]
_EDGE_LISTS = ([1], [1, 2], [1.5], ["a"], [2.0], [1, "a"])
_EDGE_MAPS = ({"k": 1}, {"k": "a"}, {"k": 1.5}, {"k": [1]})


def make_graph(seed: int, prefix: str = "O308", named_graph: bool = True) -> Graph:
    """The generated graph: 11 nodes in the default graph and 5 in the named graph `<prefix>g` (lower case), no self
    loops, no two edges with the same ends and type. `named_graph=False`: the default graph's 11 alone (one graph in
    the namespace, where all-graphs reads correlate no graph_id)."""
    rng = random.Random(seed)
    low = prefix.lower()
    la, lb = f"{prefix}A", f"{prefix}B"
    tr, ts = f"{prefix}R", f"{prefix}S"
    named = f"{low}g"
    names = list(_NAMES)
    rng.shuffle(names)
    nodes: List[GNode] = []
    for i, name in enumerate(names[:16] if named_graph else names[:11]):
        graph = "" if i < 11 else named
        labels = rng.choices([(la,), (lb,), (la, lb), ()], weights=[4, 3, 2, 1])[0]
        if i in (0, 11):
            labels = (la,)  # each graph has an A to start from
        props: Dict[str, Any] = {"id": f"{low}:{name}", "name": name, "kind": rng.choice("xyz")}
        if rng.random() < 0.75:
            props["age"] = rng.randint(1, 9)
        if rng.random() < 0.6:
            props["score"] = rng.choice([0.5, 1.5, 2.0, 2.25, 3.75])
        if rng.random() < 0.6:
            props["tags"] = rng.sample(["p", "q", "r"], rng.randint(0, 3))
        if rng.random() < 0.6:
            props["flag"] = rng.random() < 0.5
        nodes.append(GNode(props["id"], labels, props, graph))
    edges: List[GEdge] = []
    seen = set()
    for graph in ("", named) if named_graph else ("",):
        ids = [n.id for n in nodes if n.graph == graph]
        want = len(ids) * 2
        while len([e for e in edges if e.graph == graph]) < want:
            s, d = rng.sample(ids, 2)
            t = tr if rng.random() < 0.7 else ts
            if (graph, s, t, d) in seen:
                continue
            seen.add((graph, s, t, d))
            props = {}
            if rng.random() < 0.6:
                props["w"] = rng.randint(1, 5)
            if rng.random() < 0.4:
                props["f"] = rng.choice([0.5, 1.5, 2.0])
            if rng.random() < 0.4:
                props["s"] = rng.choice("uv")
            if rng.random() < 0.3:
                props["b"] = rng.random() < 0.5
            if rng.random() < 0.5:
                props["l"] = list(rng.choice(_EDGE_LISTS))
            if rng.random() < 0.3:
                props["m"] = dict(rng.choice(_EDGE_MAPS))
            edges.append(GEdge(s, t, d, props, graph))
    return Graph(nodes, edges, (la, lb), (tr, ts), named, f"{low}:")


def hand_graph(prefix: str = "H308") -> Graph:
    """The graph the hand cases are worked over. Default graph:

        ann :A  {kind 'x', age 30, score 1.5, tags ['p','q'], flag true}
        bob :A  {kind 'y', age 25, tags ['q'], flag false}
        cat :A:B {kind 'x', age 35, score 2.0, tags []}
        dan :B  {kind 'y', score 0.5}
        eve (no label) {kind 'z', age 40}

        ann -R-> bob {w: 1, l: [1], s: 'u'}       bob -R-> cat {w: 2, l: [1], f: 1.5}
        cat -R-> dan {w: 3, l: [1.5], m: {k: 1}}  ann -S-> cat {w: 4, b: true}
        dan -R-> eve {w: 5, l: [1], m: {k: 1}}    eve -S-> ann {f: 2.0}

    Named graph `<prefix>g` (lower case): fay :A {kind 'x', age 50}, gus :B {kind 'y', age 20}, fay -R-> gus
    {w: 7, l: [1]}. Every node also has `name` and `id` (the node id, `<prefix>:<name>` in lower case)."""
    low = prefix.lower()
    a, b = f"{prefix}A", f"{prefix}B"
    r, s = f"{prefix}R", f"{prefix}S"
    named = f"{low}g"

    def n(name, labels, graph="", **props):
        return GNode(f"{low}:{name}", labels, {"id": f"{low}:{name}", "name": name, **props}, graph)

    nodes = [
        n("ann", (a,), kind="x", age=30, score=1.5, tags=["p", "q"], flag=True),
        n("bob", (a,), kind="y", age=25, tags=["q"], flag=False),
        n("cat", (a, b), kind="x", age=35, score=2.0, tags=[]),
        n("dan", (b,), kind="y", score=0.5),
        n("eve", (), kind="z", age=40),
        n("fay", (a,), named, kind="x", age=50),
        n("gus", (b,), named, kind="y", age=20),
    ]

    def e(x, t, y, graph="", **props):
        return GEdge(f"{low}:{x}", t, f"{low}:{y}", props, graph)

    edges = [
        e("ann", r, "bob", w=1, l=[1], s="u"),
        e("bob", r, "cat", w=2, l=[1], f=1.5),
        e("cat", r, "dan", w=3, l=[1.5], m={"k": 1}),
        e("ann", s, "cat", w=4, b=True),
        e("dan", r, "eve", w=5, l=[1], m={"k": 1}),
        e("eve", s, "ann", f=2.0),
        e("fay", r, "gus", named, w=7, l=[1]),
    ]
    return Graph(nodes, edges, (a, b), (r, s), named, f"{low}:")


# ---------------------------------------------------------------- values


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def cy_eq(a, b):
    """openCypher `=`: True, False, or None (null)."""
    if a is None or b is None:
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _num(a) and _num(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        out = True
        for x, y in zip(a, b):
            e = cy_eq(x, y)
            if e is False:
                return False
            if e is None:
                out = None
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return cy_eq([a[k] for k in sorted(a)], [b[k] for k in sorted(b)])
    if isinstance(a, (GNode, GEdge)) and isinstance(b, (GNode, GEdge)):
        return a is b
    return False


def cy_lt(a, b):
    """openCypher `<` for the values generated here: numbers with numbers, strings with strings, else null."""
    if _num(a) and _num(b):
        return a < b
    if isinstance(a, str) and isinstance(b, str):
        return a < b
    return None


def _not(v):
    return None if v is None else not v


def _and(a, b):
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def _or(a, b):
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def _sort_key(v):
    """Ascending openCypher order (map, list, string, boolean, number, null last; a list element by element)."""
    if v is None:
        return (9,)
    if isinstance(v, bool):
        return (6, v)
    if _num(v):
        return (7, v)
    if isinstance(v, str):
        return (5, v)
    if isinstance(v, list):
        return (3, tuple(_sort_key(x) for x in v))
    if isinstance(v, dict):
        return (0, tuple(sorted((k, _sort_key(x)) for k, x in v.items())))
    return (8, repr(v))


def canon(v, bag: bool = False):
    """A comparable, hashable form of an answer value that keeps the types apart (see the module docstring)."""
    if v is None:
        return ("null",)
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, int):
        return ("int", v)
    if isinstance(v, float):
        if math.isnan(v):
            return ("float", "nan")
        return ("float", round(v, 9))
    if isinstance(v, str):
        return ("str", v)
    if isinstance(v, (list, tuple)):
        items = [canon(x) for x in v]
        return ("list", tuple(sorted(items, key=repr) if bag else items))
    if isinstance(v, dict):
        return ("map", tuple(sorted((str(k), canon(x)) for k, x in v.items())))
    return ("other", type(v).__name__, repr(v))


def canon_rows(rows: Iterable[Sequence[Any]], ordered: bool, bag_cols: FrozenSet[int] = frozenset()) -> list:
    out = [tuple(canon(v, i in bag_cols) for i, v in enumerate(row)) for row in rows]
    return out if ordered else sorted(out, key=repr)


def same_rows(a, b, query: "Query") -> bool:
    return canon_rows(a, query.ordered, query.bag_cols) == canon_rows(b, query.ordered, query.bag_cols)


def to_plain(v):
    """An interpreter value as an answer value (nodes and relationships do not appear in the generated answers)."""
    if isinstance(v, GNode):
        return {"__node__": v.id, "labels": sorted(v.labels), "props": dict(v.props)}
    if isinstance(v, GEdge):
        return {"__rel__": v.type, "props": dict(v.props)}
    if isinstance(v, list):
        return [to_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: to_plain(x) for k, x in v.items()}
    return v


def cy_text(v) -> str:
    """A Cypher literal."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(v, list):
        return "[" + ", ".join(cy_text(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {cy_text(x)}" for k, x in v.items()) + "}"
    raise TypeError(v)


# ---------------------------------------------------------------- expressions


@dataclass(frozen=True)
class Expr:
    text: str
    fn: Callable[[dict], Any] = field(compare=False)
    params: Tuple[Tuple[str, Any], ...] = ()
    bag: bool = False  # a list whose order Cypher leaves open


def _ps(*exprs) -> Tuple[Tuple[str, Any], ...]:
    out: List[Tuple[str, Any]] = []
    for e in exprs:
        if isinstance(e, Expr):
            out.extend(p for p in e.params if p not in out)
    return tuple(out)


def var(name: str) -> Expr:
    return Expr(name, lambda b: b.get(name))


def _get(x, key):
    if x is None:
        return None
    if isinstance(x, GNode) and key == "node_id":
        return x.id  # IVG's name for the node's identity
    if isinstance(x, (GNode, GEdge)):
        return x.props.get(key)
    if isinstance(x, dict):
        return x.get(key)
    raise TypeError(f"property {key} of {x!r}")


def prop(v: str, key: str) -> Expr:
    return Expr(f"{v}.{key}", lambda b: _get(b.get(v), key))


def lit(value) -> Expr:
    return Expr(cy_text(value), lambda b: value)


def param(name: str, value) -> Expr:
    return Expr(f"${name}", lambda b: value, ((name, value),))


def cmp(left: Expr, op: str, right: Expr) -> Expr:
    ops = {
        "=": lambda x, y: cy_eq(x, y),
        "<>": lambda x, y: _not(cy_eq(x, y)),
        "<": lambda x, y: cy_lt(x, y),
        ">": lambda x, y: cy_lt(y, x),
        "<=": lambda x, y: _or(cy_lt(x, y), cy_eq(x, y)) if cy_lt(x, y) is not None else None,
        ">=": lambda x, y: _or(cy_lt(y, x), cy_eq(x, y)) if cy_lt(y, x) is not None else None,
        "STARTS WITH": lambda x, y: x.startswith(y) if isinstance(x, str) and isinstance(y, str) else None,
    }
    f = ops[op]
    return Expr(f"{left.text} {op} {right.text}", lambda b: f(left.fn(b), right.fn(b)), _ps(left, right))


def is_null(e: Expr, negate: bool = False) -> Expr:
    return Expr(f"{e.text} IS {'NOT ' if negate else ''}NULL", lambda b: (e.fn(b) is None) != negate, e.params)


def and_(a: Expr, b_: Expr) -> Expr:
    return Expr(f"{a.text} AND {b_.text}", lambda b: _and(a.fn(b), b_.fn(b)), _ps(a, b_))


def or_(a: Expr, b_: Expr) -> Expr:
    return Expr(f"({a.text} OR {b_.text})", lambda b: _or(a.fn(b), b_.fn(b)), _ps(a, b_))


def not_(e: Expr) -> Expr:
    return Expr(f"NOT ({e.text})", lambda b: _not(e.fn(b)), e.params)


def plus(a: Expr, b_: Expr) -> Expr:
    def f(b):
        x, y = a.fn(b), b_.fn(b)
        if x is None or y is None:
            return None
        return x + y

    return Expr(f"{a.text} + {b_.text}", f, _ps(a, b_))


def _size(v):
    return None if v is None else len(v)


def _labels(v):
    return None if v is None else list(v.labels)


def _properties(v):
    return None if v is None else dict(v.props)


def _no_reference(v):
    raise NoReference("id")


_FUNCS = {
    "size": (_size, False),
    "length": (lambda p: None if p is None else len(p.edges), False),
    "labels": (_labels, True),
    "properties": (_properties, False),
    "type": (lambda r: None if r is None else r.type, False),
    "nodes": (lambda p: None if p is None else list(p.nodes), False),
    "relationships": (lambda p: None if p is None else list(p.edges), False),
    "id": (_no_reference, False),
    "elementId": (_no_reference, False),
    "toUpper": (lambda s: None if s is None else s.upper(), False),
}


def fn(name: str, arg: Expr) -> Expr:
    f, bag = _FUNCS[name]
    return Expr(f"{name}({arg.text})", lambda b: f(arg.fn(b)), arg.params, bag)


def comp(x: str, source: Expr, where: Optional[Expr] = None, proj: Optional[Expr] = None) -> Expr:
    """[x IN source WHERE where | proj]"""
    text = f"[{x} IN {source.text}" + (f" WHERE {where.text}" if where else "") + (f" | {proj.text}" if proj else "") + "]"

    def f(b):
        items = source.fn(b)
        if items is None:
            return None
        out = []
        for it in items:
            inner = {**b, x: it}
            if where is not None and where.fn(inner) is not True:
                continue
            out.append(proj.fn(inner) if proj else it)
        return out

    return Expr(text, f, _ps(source, where, proj))


def quant(kind: str, x: str, source: Expr, where: Expr) -> Expr:
    """any/all/none/single(x IN source WHERE where)"""

    def f(b):
        items = source.fn(b)
        if items is None:
            return None
        vals = [where.fn({**b, x: it}) for it in items]
        t, n = vals.count(True), sum(1 for v in vals if v is None)
        if kind == "any":
            return True if t else (None if n else False)
        if kind == "all":
            return False if any(v is False for v in vals) else (None if n else True)
        if kind == "none":
            return False if t else (None if n else True)
        if t > 1:
            return False
        return None if n else t == 1

    return Expr(f"{kind}({x} IN {source.text} WHERE {where.text})", f, _ps(source, where))


def case_when(whens: Sequence[Tuple[Expr, Expr]], else_: Optional[Expr] = None) -> Expr:
    text = "CASE " + " ".join(f"WHEN {c.text} THEN {v.text}" for c, v in whens)
    text += (f" ELSE {else_.text}" if else_ else "") + " END"

    def f(b):
        for c, v in whens:
            if c.fn(b) is True:
                return v.fn(b)
        return else_.fn(b) if else_ else None

    return Expr(text, f, _ps(*[e for w in whens for e in w], else_))


def case_simple(subject: Expr, whens: Sequence[Tuple[Expr, Expr]], else_: Optional[Expr] = None) -> Expr:
    text = f"CASE {subject.text} " + " ".join(f"WHEN {c.text} THEN {v.text}" for c, v in whens)
    text += (f" ELSE {else_.text}" if else_ else "") + " END"

    def f(b):
        s = subject.fn(b)
        for c, v in whens:
            if cy_eq(s, c.fn(b)) is True:
                return v.fn(b)
        return else_.fn(b) if else_ else None

    return Expr(text, f, _ps(subject, *[e for w in whens for e in w], else_))


#: The graph and graphs `run` is evaluating, for pattern comprehensions (an expression has only its row).
_RUNNING: Dict[str, Any] = {"graph": None, "scope": []}


def pattern_comp(p: "Pattern", where: Optional[Expr] = None, proj: Optional[Expr] = None) -> Expr:
    """[(a)-[..]->(b) WHERE where | proj]: one element per match of `p` from the row's bound nodes (order open)."""
    text = "[" + p.text() + (f" WHERE {where.text}" if where else "") + f" | {proj.text}]"

    def f(b):
        bound = [b[n.var] for n in p.nodes if n.var and n.var in b]
        if any(x is None for x in bound):
            return None
        g, scope = _RUNNING["graph"], _RUNNING["scope"]
        graphs = {x.graph for x in bound} or set(scope)
        found = [r for gname in sorted(graphs) for r in _match_in(g, gname, b, p)]
        if where is not None:
            found = [r for r in found if where.fn(r) is True]
        return [proj.fn(r) for r in found]

    return Expr(text, f, _ps(*[Expr("", lambda b: None, p.params())], where, proj), bag=True)


@dataclass(frozen=True)
class Agg:
    kind: str  # count collect sum avg min max
    arg: Optional[Expr] = None  # None: count(*)
    distinct: bool = False

    @property
    def text(self) -> str:
        inner = "*" if self.arg is None else ("DISTINCT " if self.distinct else "") + self.arg.text
        return f"{self.kind}({inner})"

    @property
    def params(self):
        return self.arg.params if self.arg else ()

    @property
    def bag(self) -> bool:
        return self.kind == "collect"

    def compute(self, rows: List[dict]):
        if self.arg is None:
            return len(rows)
        vals = [self.arg.fn(r) for r in rows]
        vals = [v for v in vals if v is not None]
        if self.distinct:
            seen, uniq = set(), []
            for v in vals:
                c = canon(v)
                if c not in seen:
                    seen.add(c)
                    uniq.append(v)
            vals = uniq
        if self.kind == "count":
            return len(vals)
        if self.kind == "collect":
            return vals
        if self.kind == "sum":
            if any(isinstance(v, float) for v in vals):
                return float(sum(vals))
            return sum(vals)
        if self.kind == "avg":
            return sum(vals) / len(vals) if vals else None
        if self.kind in ("min", "max"):
            if not vals:
                return None
            pick = min if self.kind == "min" else max
            return pick(vals, key=_sort_key)
        raise ValueError(self.kind)


def agg(kind: str, arg: Optional[Expr] = None, distinct: bool = False) -> Agg:
    return Agg(kind, arg, distinct)


# ---------------------------------------------------------------- patterns and clauses


@dataclass
class NodeP:
    var: Optional[str]
    labels: Tuple[str, ...]
    props: Dict[str, Any]  # a value or an Expr (a parameter)

    def text(self) -> str:
        t = (self.var or "") + "".join(f":{lbl}" for lbl in self.labels)
        if self.props:
            t += (" " if t else "") + "{" + ", ".join(f"{k}: {_vtext(v)}" for k, v in self.props.items()) + "}"
        return f"({t})"


@dataclass
class RelP:
    var: Optional[str]
    types: Tuple[str, ...]
    dir: str  # out in both
    hops: Optional[Tuple[int, Optional[int]]]
    props: Dict[str, Any]

    def text(self) -> str:
        t = (self.var or "") + (":" + "|".join(self.types) if self.types else "")
        if self.hops is not None:
            lo, hi = self.hops
            t += f"*{lo}.." + ("" if hi is None else str(hi))
        if self.props:
            t += " {" + ", ".join(f"{k}: {_vtext(v)}" for k, v in self.props.items()) + "}"
        body = f"[{t}]" if t else ""
        return {"out": f"-{body}->", "in": f"<-{body}-", "both": f"-{body}-"}[self.dir]


def _vtext(v) -> str:
    return v.text if isinstance(v, Expr) else cy_text(v)


def _vval(v):
    return v.fn({}) if isinstance(v, Expr) else v


def N(var_: Optional[str] = None, *labels: str, **props) -> NodeP:
    return NodeP(var_, tuple(labels), dict(props))


def R(var_: Optional[str] = None, *types: str, dir: str = "out", hops=None, props: Optional[dict] = None) -> RelP:
    return RelP(var_, tuple(types), dir, hops, dict(props or {}))


@dataclass
class Pattern:
    elems: List[Any]
    path: Optional[str] = None

    @property
    def nodes(self) -> List[NodeP]:
        return self.elems[0::2]

    @property
    def rels(self) -> List[RelP]:
        return self.elems[1::2]

    def text(self) -> str:
        return (f"{self.path} = " if self.path else "") + "".join(e.text() for e in self.elems)

    def params(self):
        return _ps(*[v for e in self.elems for v in e.props.values()])


def pat(*elems, path: Optional[str] = None) -> Pattern:
    assert len(elems) % 2 == 1
    return Pattern(list(elems), path)


@dataclass
class Match:
    pattern: Pattern
    where: Optional[Expr] = None
    optional: bool = False

    def text(self) -> str:
        t = ("OPTIONAL MATCH " if self.optional else "MATCH ") + self.pattern.text()
        return t + (f" WHERE {self.where.text}" if self.where else "")

    def params(self):
        return _ps(*[Expr("", lambda b: None, self.pattern.params())], self.where)


@dataclass
class Unwind:
    expr: Expr
    var: str

    def text(self) -> str:
        return f"UNWIND {self.expr.text} AS {self.var}"

    def params(self):
        return self.expr.params


@dataclass
class Project:
    """WITH (keyword 'WITH', may have a WHERE) or RETURN."""

    keyword: str
    items: List[Tuple[Any, Optional[str]]]  # (Expr or Agg, alias)
    distinct: bool = False
    order: List[Tuple[Expr, bool]] = field(default_factory=list)  # (expr, descending)
    skip: Optional[int] = None
    limit: Optional[int] = None
    where: Optional[Expr] = None

    def text(self) -> str:
        t = self.keyword + (" DISTINCT" if self.distinct else "") + " "
        t += ", ".join(e.text + (f" AS {a}" if a else "") for e, a in self.items)
        if self.order:
            t += " ORDER BY " + ", ".join(e.text + (" DESC" if d else "") for e, d in self.order)
        if self.skip is not None:
            t += f" SKIP {self.skip}"
        if self.limit is not None:
            t += f" LIMIT {self.limit}"
        if self.where is not None:
            t += f" WHERE {self.where.text}"
        return t

    def params(self):
        exprs = [e for e, _ in self.items] + [e for e, _ in self.order] + [self.where]
        out: List[Tuple[str, Any]] = []
        for e in exprs:
            if e is not None:
                out.extend(p for p in e.params if p not in out)
        return tuple(out)

    def keys(self) -> List[str]:
        return [a or e.text for e, a in self.items]


def match(pattern: Pattern, where: Optional[Expr] = None, optional: bool = False) -> Match:
    return Match(pattern, where, optional)


def unwind(expr: Expr, var_: str) -> Unwind:
    return Unwind(expr, var_)


def with_(items, distinct=False, order=(), skip=None, limit=None, where=None) -> Project:
    return Project("WITH", list(items), distinct, list(order), skip, limit, where)


def ret(items, distinct=False, order=(), skip=None, limit=None) -> Project:
    return Project("RETURN", list(items), distinct, list(order), skip, limit)


# ---------------------------------------------------------------- the interpreter


def _node_ok(np: NodeP, n: GNode, row: dict, local: dict) -> bool:
    if np.var:
        bound = local.get(np.var, row.get(np.var) if np.var in row else None)
        if np.var in local or np.var in row:
            if bound is not n:
                return False
    if not all(lbl in n.labels for lbl in np.labels):
        return False
    return all(cy_eq(_get(n, k), _vval(v)) is True for k, v in np.props.items())


def _edge_ok(rp: RelP, e: GEdge) -> bool:
    if rp.types and e.type not in rp.types:
        return False
    return all(cy_eq(e.props.get(k), _vval(v)) is True for k, v in rp.props.items())


def _steps(g: Graph, graph: str, cur: GNode, rp: RelP):
    if rp.dir in ("out", "both"):
        for e in g.out.get((graph, cur.id), ()):
            yield e, g.node(graph, e.dst)
    if rp.dir in ("in", "both"):
        for e in g.inn.get((graph, cur.id), ()):
            yield e, g.node(graph, e.src)


def _match_in(g: Graph, graph: str, row: dict, p: Pattern) -> List[dict]:
    nodes, rels = p.nodes, p.rels
    first = nodes[0]
    if first.var and first.var in row:
        cands = [row[first.var]] if isinstance(row[first.var], GNode) and row[first.var].graph == graph else []
    else:
        cands = [n for n in g.nodes if n.graph == graph]
    out: List[dict] = []

    def bind_node(np, n, local):
        if np.var and np.var not in row:
            local = {**local, np.var: n}
        return local

    def walk(i, cur, used, local, pn, pe):
        if i == len(rels):
            res = {**row, **local}
            if p.path:
                res[p.path] = PathV(list(pn), list(pe))
            out.append(res)
            return
        rp, nxt = rels[i], nodes[i + 1]
        if rp.hops is None:
            for e, other in _steps(g, graph, cur, rp):
                if id(e) in used or not _edge_ok(rp, e) or not _node_ok(nxt, other, row, local):
                    continue
                loc = dict(local)
                if rp.var:
                    loc[rp.var] = e
                walk(i + 1, other, used | {id(e)}, bind_node(nxt, other, loc), pn + [other], pe + [e])
            return
        lo, hi = rp.hops
        hi = len(g.edges) if hi is None else hi

        def trail(end, edges, nodes_on):
            if len(edges) >= lo and _node_ok(nxt, end, row, local):
                loc = dict(local)
                if rp.var:
                    loc[rp.var] = list(edges)
                walk(i + 1, end, used | {id(e) for e in edges}, bind_node(nxt, end, loc), pn + nodes_on, pe + edges)
            if len(edges) == hi:
                return
            taken = {id(e) for e in edges}
            for e, other in _steps(g, graph, end, rp):
                if id(e) in used or id(e) in taken or not _edge_ok(rp, e):
                    continue
                trail(other, edges + [e], nodes_on + [other])

        trail(cur, [], [])

    for n in cands:
        if _node_ok(first, n, row, {}):
            walk(0, n, frozenset(), bind_node(first, n, {}), [n], [])
    return out


def _new_vars(p: Pattern, row: dict) -> List[str]:
    names = [e.var for e in p.elems if e.var] + ([p.path] if p.path else [])
    return [n for n in dict.fromkeys(names) if n not in row]


def _run_match(g: Graph, rows: List[dict], m: Match, scope: List[str]) -> List[dict]:
    out = []
    for row in rows:
        bound = [row[v] for v in (n.var for n in m.pattern.nodes) if v and v in row]
        graphs = {b.graph for b in bound if b is not None}
        if any(b is None for b in bound) or len(graphs) > 1:
            found = []  # a null, or nodes of two graphs, match nothing
        else:
            found = [r for gname in (sorted(graphs) or scope) for r in _match_in(g, gname, row, m.pattern)]
        if m.where is not None:
            found = [r for r in found if m.where.fn(r) is True]
        if not found and m.optional:
            found = [{**row, **{v: None for v in _new_vars(m.pattern, row)}}]
        out.extend(found)
    return out


def _project(rows: List[dict], pj: Project) -> Tuple[List[dict], List[dict]]:
    """(projected rows, the rows ORDER BY reads): keyed by alias, or by the item's text."""
    keys = pj.keys()
    aggs = [isinstance(e, Agg) for e, _ in pj.items]
    if any(aggs):
        groups: Dict[tuple, Tuple[dict, List[dict]]] = {}
        for r in rows:
            gk = tuple(canon(e.fn(r)) for (e, _), a in zip(pj.items, aggs) if not a)
            groups.setdefault(gk, (r, []))[1].append(r)
        if not groups and not any(not a for a in aggs):
            groups[()] = ({}, [])
        proj = []
        for first, members in groups.values():
            proj.append({k: (e.compute(members) if a else e.fn(first)) for (e, _), a, k in zip(pj.items, aggs, keys)})
        merged = proj
    else:
        proj = [{k: e.fn(r) for (e, _), k in zip(pj.items, keys)} for r in rows]
        merged = [{**r, **p} for r, p in zip(rows, proj)]
    if pj.distinct:
        seen, keep_p, keep_m = set(), [], []
        for p, m in zip(proj, merged):
            c = tuple(canon(p[k]) for k in keys)
            if c not in seen:
                seen.add(c)
                keep_p.append(p)
                keep_m.append(m if not any(aggs) else p)
        proj, merged = keep_p, keep_m
    return proj, merged


def _finish(proj: List[dict], merged: List[dict], pj: Project) -> List[dict]:
    pairs = list(zip(proj, merged))
    keys = pj.keys()
    for e, desc in reversed(pj.order):
        # an ORDER BY naming a returned item (`RETURN b.kind, count(*) ORDER BY b.kind`) reads that column
        read = (lambda pm, k=e.text: pm[0][k]) if e.text in keys else (lambda pm, e=e: e.fn(pm[1]))
        pairs.sort(key=lambda pm: _sort_key(read(pm)), reverse=desc)
    if pj.skip:
        pairs = pairs[pj.skip:]
    if pj.limit is not None:
        pairs = pairs[: pj.limit]
    out = [p for p, _ in pairs]
    if pj.where is not None:
        out = [p for p in out if pj.where.fn(p) is True]
    return out


def run(g: Graph, clauses: Sequence[Any], final: Project, graph: Optional[str] = None) -> List[tuple]:
    scope = [graph] if graph is not None else g.graphs()
    _RUNNING.update(graph=g, scope=scope)  # what a pattern comprehension matches against
    rows: List[dict] = [{}]
    for c in clauses:
        if isinstance(c, Match):
            rows = _run_match(g, rows, c, scope)
        elif isinstance(c, Unwind):
            nxt = []
            for r in rows:
                items = c.expr.fn(r)
                if items is None:
                    continue
                for it in items if isinstance(items, list) else [items]:
                    nxt.append({**r, c.var: it})
            rows = nxt
        elif isinstance(c, Project):
            rows = _finish(*_project(rows, c), c)
        else:
            raise TypeError(c)
    proj = _finish(*_project(rows, final), final)
    keys = final.keys()
    return [tuple(to_plain(p[k]) for k in keys) for p in proj]


# ---------------------------------------------------------------- queries


@dataclass
class Query:
    text: str
    params: Dict[str, Any]
    ordered: bool
    bag_cols: FrozenSet[int]
    family: str
    forms: List[str]
    _eval: Callable[[Graph], Optional[List[tuple]]] = field(repr=False)
    seed: Optional[int] = None
    index: Optional[int] = None

    def evaluate(self, g: Graph) -> Optional[List[tuple]]:
        """The reference answer, or None when the interpreter has none (id(), elementId())."""
        try:
            return self._eval(g)
        except NoReference:
            return None


def _render(clauses, final: Project, graph: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    parts = ([f"USE GRAPH {graph}"] if graph is not None else []) + [c.text() for c in clauses] + [final.text()]
    params: Dict[str, Any] = {}
    for c in list(clauses) + [final]:
        for k, v in c.params():
            params[k] = v
    return " ".join(parts), params


def Q(clauses, final: Project, graph: Optional[str] = None, family: str = "hand", forms=(), ordered=None) -> Query:
    """A query from clauses and its RETURN; `forms` are other (clauses, RETURN) giving the same answer."""
    text, params = _render(clauses, final, graph)
    alt = []
    for f_clauses, f_final in forms:
        t, p = _render(f_clauses, f_final, graph)
        assert p == params, (t, p, params)
        alt.append(t)
    bag = frozenset(i for i, (e, _) in enumerate(final.items) if getattr(e, "bag", False))
    if ordered is None:
        # ordered when the RETURN orders every row: rows tied on the ORDER BY keys keep no defined order, so the
        # generator orders by every returned column
        ordered = bool(final.order)
    return Query(text, params, ordered, bag, family, alt, lambda g: run(g, clauses, final, graph))


# ---------------------------------------------------------------- the generator


class _Gen:
    def __init__(self, g: Graph, rng: random.Random):
        self.g, self.rng = g, rng
        self.la, self.lb = g.labels
        self.tr, self.ts = g.types
        self.np = 0

    def pick(self, seq):
        return self.rng.choice(list(seq))

    def label(self):
        return self.pick([self.la, self.la, self.lb])

    def name_of(self, label=None, graph=""):
        cands = [n for n in self.g.nodes if n.graph == graph and (label is None or label in n.labels)]
        return self.pick(cands).props["name"] if cands else "nobody"

    def name_value(self, label, graph=""):
        """A name: as a parameter or a literal, sometimes one no node has."""
        v = self.name_of(label, graph) if self.rng.random() < 0.9 else "nobody"
        if self.rng.random() < 0.5:
            self.np += 1
            return param(f"p{self.np}", v)
        return v

    def start(self, var_="a", graph="", by_id=True):
        """A labelled start, named by its name (mostly), or a node named by its id (`{node_id: $p}`)."""
        lbl = self.label()
        if by_id and self.rng.random() < 0.15:
            cands = [n for n in self.g.nodes if n.graph == graph]
            self.np += 1
            return N(var_, node_id=param(f"p{self.np}", self.pick(cands).id))
        props = {"name": self.name_value(lbl, graph)} if self.rng.random() < 0.7 else {}
        return N(var_, lbl, **props)

    def types(self):
        return self.pick([(self.tr,), (self.tr,), (self.ts,), (self.tr, self.ts), ()])

    def dir(self):
        return self.pick(["out", "out", "in", "both"])

    def node_col(self, v):
        return prop(v, self.pick(["name", "name", "kind", "age", "score", "flag"]))

    def ret_cols(self, vars_, k=None):
        k = k or self.rng.randint(1, min(2, len(vars_)))
        cols = []
        for v in self.rng.sample(vars_, k):
            cols.append((self.node_col(v), None))
        return cols

    def finish(self, cols, distinct=None, limit_ok=True):
        """RETURN cols, sometimes DISTINCT, sometimes ordered by every column (and then maybe cut)."""
        distinct = self.rng.random() < 0.25 if distinct is None else distinct
        order, skip, limit = [], None, None
        if self.rng.random() < 0.4:
            order = [(e, self.rng.random() < 0.3) for e, _ in cols]
            if limit_ok and self.rng.random() < 0.6:
                limit = self.rng.randint(1, 4)
                if self.rng.random() < 0.3:
                    skip = 1
        return ret(cols, distinct, order, skip, limit)

    # --- families

    def fixed(self):
        hops = self.rng.randint(1, 3)
        elems = [self.start()]
        vars_ = ["a"]
        for i in range(hops):
            v = "bcd"[i]
            lbl = (self.label(),) if self.rng.random() < 0.3 else ()
            elems += [R(None, *self.types(), dir=self.dir()), N(v, *lbl)]
            vars_.append(v)
        m = match(pat(*elems))
        return Q([m], self.finish(self.ret_cols(vars_)), family="fixed")

    def where(self):
        a = self.start(by_id=False)
        lbl = a.labels[0]
        conds = [
            lambda: cmp(prop("a", "age"), self.pick([">", "<", ">=", "<=", "=", "<>"]), lit(self.rng.randint(1, 9))),
            lambda: cmp(prop("a", "kind"), self.pick(["=", "<>"]), lit(self.pick("xyz"))),
            lambda: cmp(prop("a", "score"), self.pick([">=", "<"]), lit(self.pick([1.0, 2.0, 2.25]))),
            lambda: cmp(prop("a", "flag"), "=", lit(self.rng.random() < 0.5)),
            lambda: is_null(prop("a", self.pick(["age", "score", "tags"])), self.rng.random() < 0.5),
            lambda: cmp(prop("a", "name"), self.pick(["<", ">="]), lit(self.pick(["c", "h", "m"]))),
            lambda: cmp(prop("a", "name"), "STARTS WITH", lit(self.pick(["a", "b", "g", "j", "m"]))),
        ]
        cond = self.pick(conds)()
        if self.rng.random() < 0.3:
            cond = and_(cond, self.pick(conds)())
        elif self.rng.random() < 0.3:
            cond = or_(cond, self.pick(conds)())
        if self.rng.random() < 0.15:
            cond = not_(cond)
        elems = [N("a", lbl)]
        vars_ = ["a"]
        if self.rng.random() < 0.5:
            elems += [R(None, *self.types(), dir=self.dir()), N("b")]
            vars_.append("b")
        forms = ()
        if self.rng.random() < 0.4 and "name" in a.props:
            # the name map as a pattern and as a WHERE: two shapes, one answer
            nm = a.props["name"]
            nv = nm if isinstance(nm, Expr) else lit(nm)
            final = self.finish(self.ret_cols(vars_))
            in_map = [match(pat(N("a", lbl, name=nm), *elems[1:]), cond)]
            in_where = [match(pat(*elems), and_(cmp(prop("a", "name"), "=", nv), cond))]
            return Q(in_map, final, family="where", forms=[(in_where, final)])
        return Q([match(pat(*elems), cond)], self.finish(self.ret_cols(vars_)), family="where", forms=forms)

    def varlen(self):
        lo = self.pick([0, 1, 1, 1, 2])
        hi = self.pick([lo + 1, lo + 2, 3]) if lo < 3 else 3
        hi = max(hi, max(lo, 1))
        a = self.start()
        lbl = (self.label(),) if self.rng.random() < 0.3 else ()
        m = match(pat(a, R(None, *self.types(), dir=self.dir(), hops=(lo, hi)), N("b", *lbl)))
        cols = [(prop("b", "name"), None)] + ([(prop("a", "name"), None)] if self.rng.random() < 0.4 else [])
        return Q([m], self.finish(cols, distinct=self.rng.random() < 0.5), family="varlen")

    def varlen_props(self):
        key = self.pick(["w", "f", "s", "b", "l", "l", "m", "m"])
        held = [e.props[key] for e in self.g.edges if key in e.props]
        value = self.pick(held) if held and self.rng.random() < 0.85 else {"w": 9, "f": 9.5, "s": "zz", "b": False,
                                                                         "l": [9], "m": {"k": 9}}[key]
        if key == "w" and self.rng.random() < 0.3:
            value = float(value)  # 2.0 matches a stored 2
        hi = self.pick([1, 2, 2, 3])
        a = self.start()
        types = self.pick([(self.tr,), (self.tr, self.ts), ()])
        d = self.dir()
        b = N("b")
        cols = [(prop("a", "name"), None), (prop("b", "name"), None)]
        final = self.finish(cols, distinct=self.rng.random() < 0.3)
        in_map = [match(pat(a, R("r", *types, dir=d, hops=(1, hi), props={key: value}), b))]
        vv = lit(value)
        in_where = [match(pat(a, R("r", *types, dir=d, hops=(1, hi)), b),
                          quant("all", "x", var("r"), cmp(prop("x", key), "=", vv)))]
        return Q(in_map, final, family="varlen_props", forms=[(in_where, final)])

    def pathfn(self):
        hi = self.pick([1, 2, 2, 3])
        a = self.start()
        m = match(pat(a, R(None, *self.pick([(self.tr,), (self.tr, self.ts)]), dir=self.dir(), hops=(1, hi)), N("b"),
                      path="p"))
        extra = self.pick([
            fn("length", var("p")),
            fn("size", fn("relationships", var("p"))),
            comp("n", fn("nodes", var("p")), proj=prop("n", "name")),
            comp("r", fn("relationships", var("p")), proj=prop("r", "w")),
        ])
        return Q([m], self.finish([(prop("b", "name"), None), (extra, None)], distinct=False), family="pathfn")

    def optional(self):
        kind = self.pick(["trailing", "leading", "anon", "varlen", "where"])
        lbl = self.label()
        if kind == "leading":
            name = self.pick([self.name_of(lbl), "nobody"])
            m = [match(pat(N("a", lbl, name=name)), optional=True)]
            return Q(m, ret([(prop("a", "name"), None), (prop("a", "kind"), None)]), family="optional")
        if kind == "anon":
            name = self.pick([self.name_of(lbl), "nobody"])
            m = [match(pat(N(None, lbl, name=name), R(None, *self.types(), dir=self.dir()), N("b")), optional=True)]
            return Q(m, self.finish([(prop("b", "name"), None)]), family="optional")
        first = match(pat(N("a", lbl)))
        if kind == "varlen":
            first = match(pat(N("a", lbl, name=self.name_of(lbl))))
            rel = R(None, *self.types(), dir=self.dir(), hops=(1, self.pick([1, 2])))
        else:
            rel = R(None, *self.types(), dir=self.dir())
        tl = (self.label(),) if self.rng.random() < 0.5 else ()
        where = cmp(prop("b", "kind"), "=", lit(self.pick("xyz"))) if kind == "where" else None
        second = match(pat(N("a"), rel, N("b", *tl)), where, optional=True)
        cols = [(prop("a", "name"), None), (prop("b", "name"), None)]
        return Q([first, second], self.finish(cols), family="optional")

    def with_stage(self):
        lbl = self.label()
        kind = self.pick(["cut", "agg", "agg2", "value"])
        if kind == "cut":
            desc = self.rng.random() < 0.3
            w = with_([(var("a"), None)], order=[(prop("a", "name"), desc)], skip=self.pick([None, 1]),
                      limit=self.rng.randint(1, 3))
            second = match(pat(N("a"), R(None, *self.types(), dir=self.dir()), N("b")))
            cols = [(prop("a", "name"), None), (prop("b", "name"), None)]
            return Q([match(pat(N("a", lbl))), w, second], self.finish(cols), family="with")
        if kind == "agg":
            w = with_([(var("a"), None), (agg("count", var("b")), "c")], where=cmp(var("c"), ">", lit(1)))
            m = match(pat(N("a", lbl), R(None, *self.types(), dir=self.dir()), N("b")))
            return Q([m, w], self.finish([(prop("a", "name"), None), (var("c"), None)]), family="with")
        if kind == "agg2":
            # an aggregate over an aggregate: how many starts have each number of neighbours
            w = with_([(var("a"), None), (agg("count", var("b")), "c")])
            m = match(pat(N("a", lbl), R(None, *self.types(), dir=self.dir()), N("b")))
            return Q([m, w], ret([(var("c"), None), (agg("count"), "n")]), family="with")
        key = self.pick(["age", "score", "name"])
        w = with_([(prop("a", key), "x")], order=[(var("x"), self.rng.random() < 0.5)], limit=self.rng.randint(1, 3))
        m = match(pat(N("a", lbl)), is_null(prop("a", key), negate=True))
        return Q([m, w], ret([(var("x"), None)]), family="with")

    def aggregate(self):
        lbl = self.label()
        hop = self.rng.random() < 0.6
        m = match(pat(N("a", lbl), R(None, *self.types(), dir=self.dir()), N("b"))) if hop else match(pat(N("b", lbl)))
        a = self.pick([
            agg("count"), agg("count", prop("b", "age")), agg("count", var("b")),
            agg("count", var("b"), distinct=True), agg("collect", prop("b", "name")),
            agg("sum", prop("b", "age")), agg("avg", prop("b", self.pick(["age", "score"]))),
            agg("min", prop("b", self.pick(["age", "name", "score"]))),
            agg("max", prop("b", self.pick(["age", "name", "score"]))),
            agg("count", prop("b", "kind"), distinct=True), agg("sum", prop("b", "score")),
        ])
        key = self.pick([None, prop("a", "name") if hop else prop("b", "kind"), prop("b", "kind")])
        cols = ([(key, None)] if key is not None else []) + [(a, "v")]
        order = [(key, False)] if key is not None and self.rng.random() < 0.4 else []
        return Q([m], ret(cols, order=order), family="agg")

    def lists(self):
        lbl = self.label()
        tags = prop("a", "tags")
        e = self.pick([
            fn("size", tags),
            comp("x", tags, where=cmp(var("x"), "<>", lit("p"))),
            comp("x", tags, proj=plus(var("x"), lit("!"))),
            quant(self.pick(["any", "all", "none", "single"]), "x", tags, cmp(var("x"), "=", lit(self.pick("pqr")))),
            comp("x", lit([1, 2, 3]), where=cmp(var("x"), ">", lit(1)), proj=plus(var("x"), prop("a", "age"))),
        ])
        if self.rng.random() < 0.3:
            m = match(pat(N("a", lbl)), quant("any", "x", tags, cmp(var("x"), "=", lit(self.pick("pqr")))))
            return Q([m], ret([(prop("a", "name"), None)]), family="list")
        return Q([match(pat(N("a", lbl)))], ret([(prop("a", "name"), None), (e, None)]), family="list")

    def case(self):
        lbl = self.label()
        k = self.rng.randint(1, 9)
        e = self.pick([
            case_when([(cmp(prop("a", "age"), ">", lit(k)), lit("hi")), (is_null(prop("a", "age")), lit("none"))],
                      lit("lo")),
            case_simple(prop("a", "kind"), [(lit("x"), lit(1)), (lit("y"), lit(2))], lit(0)),
            case_when([(cmp(prop("a", "flag"), "=", lit(True)), prop("a", "name"))]),
        ])
        return Q([match(pat(N("a", lbl)))], ret([(prop("a", "name"), None), (e, "c")]), family="case")

    def unwind_(self):
        lbl = self.label()
        if self.rng.random() < 0.5:
            xs = self.rng.sample(range(1, 10), 3)
            cl = [unwind(lit(xs), "x"), match(pat(N("a", lbl)), cmp(prop("a", "age"), "=", var("x")))]
            return Q(cl, self.finish([(var("x"), None), (prop("a", "name"), None)]), family="unwind")
        cl = [match(pat(N("a", lbl))), unwind(prop("a", "tags"), "t")]
        return Q(cl, self.finish([(prop("a", "name"), None), (var("t"), None)]), family="unwind")

    def funcs(self):
        lbl = self.label()
        name = self.name_of(lbl)
        kind = self.pick(["labels", "properties", "id", "rel"])
        if kind == "rel":
            m = match(pat(N("a", lbl, name=name), R("r", *self.types(), dir=self.dir()), N("b")))
            e = self.pick([fn("type", var("r")), fn("properties", var("r")), prop("r", self.pick("wfslbm"))])
            return Q([m], ret([(prop("b", "name"), None), (e, None)]), family="funcs")
        e = {"labels": fn("labels", var("a")), "properties": fn("properties", var("a")),
             "id": fn(self.pick(["id", "elementId"]), var("a"))}[kind]
        return Q([match(pat(N("a", lbl, name=name)))], ret([(prop("a", "name"), None), (e, None)]), family="funcs")

    def pattern_comp_(self):
        lbl = self.label()
        kind = self.pick(["plain", "where", "labels", "varlen"])
        tl = (self.label(),) if kind == "labels" else ()
        hops = (1, self.pick([1, 2])) if kind == "varlen" else None
        p = pat(N("a"), R(None, *self.types(), dir=self.dir(), hops=hops), N("b", *tl))
        where = cmp(prop("b", "kind"), "=", lit(self.pick("xyz"))) if kind == "where" else None
        e = pattern_comp(p, where, prop("b", "name"))
        return Q([match(pat(N("a", lbl, name=self.name_of(lbl))))], ret([(prop("a", "name"), None), (e, "bs")]),
                 family="pattern_comp")

    def optional_varlen(self):
        """The OPTIONAL MATCH variable-length shapes the statement reads: a lone hop from a bound start to a plain or
        labelled new end (the others raise, docs/KNOWN_ISSUES.md)."""
        lbl = self.label()
        tl = (self.label(),) if self.rng.random() < 0.4 else ()
        first = match(pat(N("a", lbl, name=self.name_of(lbl)))) if self.rng.random() < 0.6 else match(pat(N("a", lbl)))
        rel = R(None, *self.types(), dir=self.dir(), hops=(1, self.pick([1, 2, 3])))
        second = match(pat(N("a"), rel, N("b", *tl)), optional=True)
        return Q([first, second], self.finish([(prop("a", "name"), None), (prop("b", "name"), None)]),
                 family="optional_varlen")

    def graph(self):
        graph = self.pick([self.g.named, None])
        lbl = self.label()
        kind = self.pick(["hop", "count", "node", "varlen"])
        if kind == "varlen":
            m = match(pat(N("a", lbl), R(None, *self.types(), dir=self.dir(), hops=(1, self.pick([2, 3]))), N("b")))
            return Q([m], ret([(prop("b", "name"), None)], distinct=True), graph=graph, family="graph")
        if kind == "node":
            return Q([match(pat(N("a", lbl)))], self.finish([(prop("a", "name"), None)]), graph=graph, family="graph")
        m = match(pat(N("a", lbl), R(None, *self.types(), dir=self.dir()), N("b")))
        if kind == "count":
            return Q([m], ret([(agg("count"), "c")]), graph=graph, family="graph")
        cols = [(prop("a", "name"), None), (prop("b", "name"), None)]
        return Q([m], self.finish(cols), graph=graph, family="graph")


_MAKERS = {
    "fixed": _Gen.fixed, "where": _Gen.where, "varlen": _Gen.varlen, "varlen_props": _Gen.varlen_props,
    "pathfn": _Gen.pathfn, "optional": _Gen.optional, "with": _Gen.with_stage, "agg": _Gen.aggregate,
    "list": _Gen.lists, "case": _Gen.case, "unwind": _Gen.unwind_, "funcs": _Gen.funcs, "graph": _Gen.graph,
    "pattern_comp": _Gen.pattern_comp_, "optional_varlen": _Gen.optional_varlen,
}


def generate(g: Graph, seed: int, count: int) -> List[Query]:
    """`count` queries, the families taken in turn so each gets its share."""
    rng = random.Random(seed * 7919 + 1)
    gen = _Gen(g, rng)
    out = []
    for i in range(count):
        family = FAMILIES[i % len(FAMILIES)]
        q = _MAKERS[family](gen)
        q.seed, q.index = seed, i
        out.append(q)
    return out


# ---------------------------------------------------------------- hand cases


@dataclass
class HandCase:
    name: str
    query: Query
    expected: List[tuple]


def hand_cases(prefix: str = "H308") -> List[HandCase]:
    """Queries over `hand_graph(prefix)` with answers worked by hand from its docstring (each comment says how)."""
    A, B = f"{prefix}A", f"{prefix}B"
    Rt, St = f"{prefix}R", f"{prefix}S"
    G = f"{prefix.lower()}g"
    nm = lambda v: prop(v, "name")  # noqa: E731
    c: List[HandCase] = []

    def case(name, query, expected):
        c.append(HandCase(name, query, expected))

    def one(*elems, where=None, cols=(), **kw):
        return Q([match(pat(*elems), where)], ret([(e, None) for e in cols], **kw))

    # every :A in both graphs
    case("all_graphs_A", one(N("a", A), cols=[nm("a")]), [("ann",), ("bob",), ("cat",), ("fay",)])
    case("use_graph_A", Q([match(pat(N("a", A)))], ret([(nm("a"), None)]), graph=G), [("fay",)])
    case("hop_out", one(N("a", A, name="ann"), R(None, Rt), N("b"), cols=[nm("b")]), [("bob",)])
    # R edges from an :A to a :B: bob->cat (cat is B), cat->dan, fay->gus
    case("hop_labels", one(N("a", A), R(None, Rt), N("b", B), cols=[nm("a"), nm("b")]),
         [("bob", "cat"), ("cat", "dan"), ("fay", "gus")])
    case("hop_in", one(N("a", A, name="cat"), R(None, Rt, dir="in"), N("b"), cols=[nm("b")]), [("bob",)])
    # dan's R edges: cat->dan, dan->eve
    case("hop_both", one(N("a", B, name="dan"), R(None, Rt, dir="both"), N("b"), cols=[nm("b")]),
         [("cat",), ("eve",)])
    case("hop_param", one(N("a", A, name=param("who", "ann")), R(None, Rt), N("b"), cols=[nm("b")]), [("bob",)])
    case("two_hops", one(N("a", A, name="ann"), R(None, Rt), N("b"), R(None, Rt), N("c"), cols=[nm("c")]),
         [("cat",)])
    case("three_hops", one(N("a", A, name="ann"), R(None, Rt), N("b"), R(None, Rt), N("c"), R(None, Rt), N("d"),
                           cols=[nm("d")]), [("dan",)])
    # bob -R- ann (then ann has no other R edge), bob -R- cat -R- dan
    case("both_trail", one(N("a", A, name="bob"), R(None, Rt, dir="both"), N("b"), R(None, Rt, dir="both"), N("c"),
                           cols=[nm("c")]), [("dan",)])
    case("types_or", one(N("a", A, name="ann"), R(None, Rt, St), N("b"), cols=[nm("b")]), [("bob",), ("cat",)])
    # ages of :A: ann 30, bob 25, cat 35, fay 50
    case("where_int", one(N("a", A), where=cmp(prop("a", "age"), ">", lit(28)), cols=[nm("a")]),
         [("ann",), ("cat",), ("fay",)])
    case("where_str", one(N("a", A), where=cmp(prop("a", "kind"), "=", lit("x")), cols=[nm("a")]),
         [("ann",), ("cat",), ("fay",)])
    # :B scores: cat 2.0, dan 0.5, gus none
    case("where_float", one(N("a", B), where=cmp(prop("a", "score"), ">=", lit(1.0)), cols=[nm("a")]), [("cat",)])
    case("where_bool", one(N("a", A), where=cmp(prop("a", "flag"), "=", lit(True)), cols=[nm("a")]), [("ann",)])
    case("where_null", one(N("a", A), where=is_null(prop("a", "score")), cols=[nm("a")]), [("bob",), ("fay",)])
    # flag: ann true, bob false, cat and fay none (null <> false is null)
    case("where_ne_null", one(N("a", A), where=cmp(prop("a", "flag"), "<>", lit(False)), cols=[nm("a")]),
         [("ann",)])
    case("where_starts", one(N("a", A), where=cmp(prop("a", "name"), "STARTS WITH", lit("b")), cols=[nm("a")]),
         [("bob",)])
    case("order_desc_limit", Q([match(pat(N("a", A)))], ret([(nm("a"), "n")], order=[(var("n"), True)], limit=2)),
         [("fay",), ("cat",)])
    case("order_skip", Q([match(pat(N("a", A)))], ret([(nm("a"), "n")], order=[(var("n"), False)], skip=1, limit=2)),
         [("bob",), ("cat",)])
    case("distinct", Q([match(pat(N("a", A)))], ret([(prop("a", "kind"), None)], distinct=True)), [("x",), ("y",)])
    # ann -R-> bob -R-> cat -R-> dan
    case("vl_1_3", one(N("a", A, name="ann"), R(None, Rt, hops=(1, 3)), N("b"), cols=[nm("b")]),
         [("bob",), ("cat",), ("dan",)])
    case("vl_2_3", one(N("a", A, name="ann"), R(None, Rt, hops=(2, 3)), N("b"), cols=[nm("b")]),
         [("cat",), ("dan",)])
    # dan -R- cat, dan -R- eve; cat -R- bob; eve has no other R edge
    case("vl_both_distinct", Q([match(pat(N("a", B, name="dan"), R(None, Rt, dir="both", hops=(1, 2)), N("b")))],
                               ret([(nm("b"), None)], distinct=True)), [("bob",), ("cat",), ("eve",)])
    case("vl_zero", one(N("a", A, name="cat"), R(None, Rt, hops=(0, 1)), N("b"), cols=[nm("b")]),
         [("cat",), ("dan",)])
    # l: ann->bob [1], bob->cat [1], cat->dan [1.5]
    case("vl_map_int_list", one(N("a", A, name="ann"), R("r", Rt, hops=(1, 3), props={"l": [1]}), N("b"),
                                cols=[nm("b")]), [("bob",), ("cat",)])
    # from bob the first edge (bob->cat) holds l [1], not [1.5]: nothing. Every :A start would add (cat, dan)
    case("vl_map_float_list_start", one(N("a", A, name="bob"), R("r", Rt, hops=(1, 2), props={"l": [1.5]}), N("b"),
                                        cols=[nm("a"), nm("b")]), [])
    case("vl_map_float_list", one(N("a", A, name="cat"), R("r", Rt, hops=(1, 2), props={"l": [1.5]}), N("b"),
                                  cols=[nm("b")]), [("dan",)])
    # m {k: 1}: cat->dan, dan->eve
    case("vl_map_map", one(N("a", A, name="cat"), R("r", Rt, hops=(1, 2), props={"m": {"k": 1}}), N("b"),
                           cols=[nm("b")]), [("dan",), ("eve",)])
    case("vl_map_map_start", one(N("a", A, name="ann"), R("r", Rt, hops=(1, 2), props={"m": {"k": 1}}), N("b"),
                                 cols=[nm("a"), nm("b")]), [])
    case("vl_map_int", one(N("a", A, name="ann"), R("r", Rt, hops=(1, 2), props={"w": 1}), N("b"), cols=[nm("b")]),
         [("bob",)])
    case("vl_map_float", one(N("a", A, name="bob"), R("r", Rt, hops=(1, 1), props={"f": 1.5}), N("b"),
                             cols=[nm("b")]), [("cat",)])
    case("vl_map_str", one(N("a", A, name="ann"), R("r", Rt, hops=(1, 2), props={"s": "u"}), N("b"), cols=[nm("b")]),
         [("bob",)])
    case("vl_map_bool", one(N("a", A, name="ann"), R("r", St, hops=(1, 1), props={"b": True}), N("b"),
                            cols=[nm("b")]), [("cat",)])
    # eve -S-> ann holds f 2.0, which equals 2
    case("vl_map_int_matches_float", one(N("a", A, name="ann"), R("r", St, dir="in", hops=(1, 1), props={"f": 2}),
                                         N("b"), cols=[nm("b")]), [("eve",)])
    # an OPTIONAL hop with a map: cat -> dan -> eve hold m {k: 1}; ann's first relationship has no m
    case("opt_vl_map", Q([match(pat(N("a", A, name="cat"))),
                          match(pat(N("a"), R("r", Rt, hops=(1, 2), props={"m": {"k": 1}}), N("b")), optional=True)],
                         ret([(nm("a"), None), (nm("b"), None)])), [("cat", "dan"), ("cat", "eve")])
    case("opt_vl_map_none", Q([match(pat(N("a", A, name="ann"))),
                               match(pat(N("a"), R("r", Rt, hops=(1, 2), props={"m": {"k": 1}}), N("b")),
                                     optional=True)],
                              ret([(nm("a"), None), (nm("b"), None)])), [("ann", None)])
    # of cat's ends dan holds B, eve no label
    case("opt_vl_map_labelled_end", Q([match(pat(N("a", A, name="cat"))),
                                       match(pat(N("a"), R("r", Rt, hops=(1, 2), props={"m": {"k": 1}}), N("b", B)),
                                             optional=True)],
                                      ret([(nm("a"), None), (nm("b"), None)])), [("cat", "dan")])
    # ann -[l [1]]-> bob, -> cat; then one relationship holding [1.5]: only cat -> dan
    case("two_vl_maps", one(N("a", A, name="ann"), R("r", Rt, hops=(1, 2), props={"l": [1]}), N("b"),
                            R("q", Rt, hops=(1, 1), props={"l": [1.5]}), N("c"), cols=[nm("b"), nm("c")]),
         [("cat", "dan")])
    # a missing property is null where an expression reads it, and the row stays (scores: ann 1.5, cat 2.0)
    case("case_missing_prop", Q([match(pat(N("a", A)))],
                                ret([(nm("a"), None), (case_when([(cmp(prop("a", "score"), ">", lit(1.6)), lit("hi"))],
                                                                 lit("lo")), "c")])),
         [("ann", "lo"), ("bob", "lo"), ("cat", "hi"), ("fay", "lo")])
    case("with_case_missing_prop", Q([match(pat(N("a", A))),
                                      with_([(var("a"), None),
                                             (case_when([(cmp(prop("a", "flag"), "=", lit(True)), lit(1))], lit(0)), "c")])],
                                     ret([(nm("a"), None), (var("c"), None)])),
         [("ann", 1), ("bob", 0), ("cat", 0), ("fay", 0)])
    case("where_case_missing_prop", one(N("a", A), where=cmp(case_when([(cmp(prop("a", "score"), ">", lit(1.6)), lit(1))],
                                                                    lit(2)), "=", lit(2)), cols=[nm("a")]),
         [("ann",), ("bob",), ("fay",)])
    case("order_case_missing_prop", Q([match(pat(N("a", A)))],
                                      ret([(nm("a"), None)], order=[(case_when([(cmp(prop("a", "score"), ">", lit(1.6)),
                                                                                 lit(0))], lit(1)), False),
                                                                    (nm("a"), False)])),
         [("cat",), ("ann",), ("bob",), ("fay",)])
    case("comp_missing_prop", Q([match(pat(N("a", A)))],
                                ret([(nm("a"), None), (comp("y", lit([1, 2]), where=cmp(prop("a", "score"), ">", lit(1.6))),
                                                       None)])),
         [("ann", []), ("bob", []), ("cat", [1, 2]), ("fay", [])])
    # an OR keeps a node missing one side's property (flag: ann true, bob false; kind x: ann, cat, fay)
    case("where_or_missing_prop", one(N("a", A), where=or_(cmp(prop("a", "flag"), "=", lit(False)),
                                                           cmp(prop("a", "kind"), "=", lit("x"))), cols=[nm("a")]),
         [("ann",), ("bob",), ("cat",), ("fay",)])
    # arithmetic over a missing property is a null element (ages: cat 35, gus 20; dan has none)
    case("comp_arith_null", Q([match(pat(N("a", B)))],
                              ret([(nm("a"), None), (comp("x", lit([1, 2]), proj=plus(var("x"), prop("a", "age"))), None)])),
         [("cat", [36, 37]), ("dan", [None, None]), ("gus", [21, 22])])
    # an anonymous start with a map over an undirected hop: dan -R- cat, dan -R- eve
    case("anon_start_undirected", one(N(None, B, name="dan"), R(None, Rt, dir="both"), N("b"), cols=[nm("b")]),
         [("cat",), ("eve",)])
    case("opt_anon_start_undirected_none", Q([match(pat(N(None, B, name="zed"), R(None, Rt, dir="both"), N("b")),
                                                    optional=True)], ret([(nm("b"), None)])), [(None,)])
    # from bob over R either way: ann (1), cat (1); ann has no other R edge; cat -R- dan (2)
    case("path_length_id_undirected", Q([match(pat(N("a", node_id=f"{prefix.lower()}:bob"),
                                                   R(None, Rt, dir="both", hops=(1, 2)), N("b"), path="p"))],
                                        ret([(nm("b"), None), (fn("length", var("p")), None)])),
         [("ann", 1), ("cat", 1), ("dan", 2)])
    # all(x IN r WHERE x.f = 2.0): eve -S-> ann holds f 2.0
    case("vl_all_where_float", Q([match(pat(N("a", A, name="ann"), R("r", St, dir="in", hops=(1, 1)), N("b")),
                                        quant("all", "x", var("r"), cmp(prop("x", "f"), "=", lit(2.0))))],
                                 ret([(nm("b"), None)])), [("eve",)])
    # pattern comprehensions from one start: ann -R-> bob (kind y), ann -S-> cat (kind x, w 4); cat -R-> dan -R-> eve
    def pc(start, p, where=None):
        return Q([match(pat(N("a", A, name=start)))], ret([(pattern_comp(p, where, nm("b")), None)]))

    case("pc_plain", pc("ann", pat(N("a"), R(None, Rt), N("b"))), [(["bob"],)])
    case("pc_where", pc("ann", pat(N("a"), R(None, Rt, St), N("b")), cmp(prop("b", "kind"), "=", lit("x"))),
         [(["cat"],)])
    case("pc_rel_map", pc("ann", pat(N("a"), R(None, Rt, St, props={"w": 4}), N("b"))), [(["cat"],)])
    case("pc_node_map", pc("ann", pat(N("a"), R(None, Rt, St), N("b", kind="x"))), [(["cat"],)])
    case("pc_varlen", pc("cat", pat(N("a"), R(None, Rt, hops=(1, 2)), N("b"))), [(["dan", "eve"],)])
    case("pc_varlen_where", pc("cat", pat(N("a"), R(None, Rt, hops=(1, 2)), N("b")), cmp(nm("b"), "=", lit("eve"))),
         [(["eve"],)])
    case("vl_all_where", Q([match(pat(N("a", A, name="ann"), R("r", Rt, hops=(1, 3)), N("b")),
                                  quant("all", "x", var("r"), cmp(prop("x", "l"), "=", lit([1]))))],
                           ret([(nm("b"), None)])), [("bob",), ("cat",)])
    case("path_length", Q([match(pat(N("a", A, name="ann"), R(None, Rt, hops=(1, 2)), N("b"), path="p"))],
                          ret([(nm("b"), None), (fn("length", var("p")), None)])), [("bob", 1), ("cat", 2)])
    case("path_nodes", Q([match(pat(N("a", A, name="ann"), R(None, Rt, hops=(1, 2)), N("b"), path="p"))],
                         ret([(comp("n", fn("nodes", var("p")), proj=nm("n")), None)])),
         [(["ann", "bob"],), (["ann", "bob", "cat"],)])
    case("path_rels", Q([match(pat(N("a", A, name="ann"), R(None, Rt, hops=(1, 2)), N("b"), path="p"))],
                        ret([(nm("b"), None), (comp("r", fn("relationships", var("p")), proj=prop("r", "w")), None)])),
         [("bob", [1]), ("cat", [1, 2])])
    # S out of each :A: ann -S-> cat only
    case("opt_trailing", Q([match(pat(N("a", A))), match(pat(N("a"), R(None, St), N("b")), optional=True)],
                           ret([(nm("a"), None), (nm("b"), None)])),
         [("ann", "cat"), ("bob", None), ("cat", None), ("fay", None)])
    case("opt_leading_none", Q([match(pat(N("a", A, name="zed")), optional=True)], ret([(nm("a"), None)])),
         [(None,)])
    case("opt_anon", Q([match(pat(N(None, A, name="ann"), R(None, Rt), N("b")), optional=True)],
                       ret([(nm("b"), None)])), [("bob",)])
    case("opt_anon_none", Q([match(pat(N(None, A, name="bob"), R(None, St), N("b")), optional=True)],
                            ret([(nm("b"), None)])), [(None,)])
    case("opt_varlen_none", Q([match(pat(N("a", A, name="cat"))),
                               match(pat(N("a"), R(None, St, hops=(1, 2)), N("b")), optional=True)],
                              ret([(nm("a"), None), (nm("b"), None)])), [("cat", None)])
    # bob's R neighbours: ann (in), cat (out); keep the :A ones (ann, cat), else null
    case("opt_where", Q([match(pat(N("a", A, name="bob"))),
                         match(pat(N("a"), R(None, Rt, dir="both"), N("b")), cmp(prop("b", "kind"), "=", lit("x")),
                               optional=True)],
                        ret([(nm("a"), None), (nm("b"), None)])), [("bob", "ann"), ("bob", "cat")])
    # the first two :A by name are ann and bob
    case("with_cut", Q([match(pat(N("a", A))), with_([(var("a"), None)], order=[(nm("a"), False)], limit=2),
                        match(pat(N("a"), R(None, Rt), N("b")))],
                       ret([(nm("a"), None), (nm("b"), None)])), [("ann", "bob"), ("bob", "cat")])
    # out-edges of either type: ann 2 (bob, cat), bob 1, cat 1, fay 1
    case("with_agg_where", Q([match(pat(N("a", A), R(None, Rt, St), N("b"))),
                              with_([(var("a"), None), (agg("count", var("b")), "c")], where=cmp(var("c"), ">", lit(1)))],
                             ret([(nm("a"), None), (var("c"), None)])), [("ann", 2)])
    case("count_star", Q([match(pat(N("a", A), R(None, Rt), N("b")))], ret([(agg("count"), None)])), [(4,)])
    # targets bob, cat (2.0), dan (0.5), gus: two scores
    case("count_prop", Q([match(pat(N("a", A), R(None, Rt), N("b")))], ret([(agg("count", prop("b", "score")), None)])),
         [(2,)])
    case("collect", Q([match(pat(N("a", A, name="ann"), R(None, Rt, St), N("b")))],
                      ret([(agg("collect", nm("b")), None)])), [(["bob", "cat"],)])
    case("sum_avg", Q([match(pat(N("a", A)))], ret([(agg("sum", prop("a", "age")), None),
                                                    (agg("avg", prop("a", "age")), None)])), [(140, 35.0)])
    case("min_max", Q([match(pat(N("a", A)))], ret([(agg("min", nm("a")), None), (agg("max", prop("a", "age")), None)])),
         [("ann", 50)])
    case("count_distinct", Q([match(pat(N("a", A)))], ret([(agg("count", prop("a", "kind"), distinct=True), None)])),
         [(2,)])
    case("group_count", Q([match(pat(N("a", A)))], ret([(prop("a", "kind"), None), (agg("count"), None)])),
         [("x", 3), ("y", 1)])
    case("sum_float", Q([match(pat(N("a", B)))], ret([(agg("sum", prop("a", "score")), None)])), [(2.5,)])
    # scores of x: ann 1.5, cat 2.0, fay none; y: bob none, so its sum is 0
    case("sum_only_nulls", Q([match(pat(N("a", A)))], ret([(prop("a", "kind"), None), (agg("sum", prop("a", "score")), None)])),
         [("x", 3.5), ("y", 0)])
    # the same over an undirected hop: ann's R neighbour bob has no score, cat's are bob and dan (0.5)
    case("sum_only_nulls_undirected", Q([match(pat(N("a", A), R(None, Rt, dir="both"), N("b")))],
                                        ret([(nm("a"), None), (agg("sum", prop("b", "score")), None)])),
         [("ann", 0), ("bob", 3.5), ("cat", 0.5), ("fay", 0)])
    case("count_empty", Q([match(pat(N("a", A, name="zed")))], ret([(agg("count"), None)])), [(0,)])
    case("list_comp", Q([match(pat(N("a", A, name="ann")))],
                        ret([(comp("x", prop("a", "tags"), where=cmp(var("x"), "<>", lit("p")),
                                   proj=plus(var("x"), lit("!"))), None)])), [(["q!"],)])
    case("list_size", Q([match(pat(N("a", A)))], ret([(nm("a"), None), (fn("size", prop("a", "tags")), None)])),
         [("ann", 2), ("bob", 1), ("cat", 0), ("fay", None)])
    case("list_any", one(N("a", A), where=quant("any", "x", prop("a", "tags"), cmp(var("x"), "=", lit("q"))),
                         cols=[nm("a")]), [("ann",), ("bob",)])
    case("case_when", Q([match(pat(N("a", A)))],
                        ret([(nm("a"), None), (case_when([(cmp(prop("a", "age"), ">", lit(30)), lit("hi"))], lit("lo")),
                                               "c")])),
         [("ann", "lo"), ("bob", "lo"), ("cat", "hi"), ("fay", "hi")])
    case("case_simple", Q([match(pat(N("a", B)))],
                          ret([(nm("a"), None), (case_simple(prop("a", "kind"), [(lit("x"), lit(1))], lit(0)), "c")])),
         [("cat", 1), ("dan", 0), ("gus", 0)])
    case("unwind_match", Q([unwind(lit([25, 35, 99]), "x"), match(pat(N("a", A)), cmp(prop("a", "age"), "=", var("x")))],
                           ret([(var("x"), None), (nm("a"), None)])), [(25, "bob"), (35, "cat")])
    case("unwind_prop", Q([match(pat(N("a", A))), unwind(prop("a", "tags"), "t")], ret([(nm("a"), None), (var("t"), None)])),
         [("ann", "p"), ("ann", "q"), ("bob", "q")])
    case("labels", Q([match(pat(N("a", A, name="cat")))], ret([(fn("labels", var("a")), None)])), [([A, B],)])
    case("properties", Q([match(pat(N("a", B, name="dan")))], ret([(fn("properties", var("a")), None)])),
         [({"id": f"{prefix.lower()}:dan", "name": "dan", "kind": "y", "score": 0.5},)])
    case("rel_type", one(N("a", A, name="ann"), R("r"), N("b"), cols=[fn("type", var("r")), nm("b")]),
         [(Rt, "bob"), (St, "cat")])
    case("rel_props", one(N("a", A, name="bob"), R("r", Rt), N("b"), cols=[prop("r", "w"), prop("r", "f")]),
         [(2, 1.5)])
    case("rel_properties", one(N(None, A, name="cat"), R("r", Rt), N("b"), cols=[fn("properties", var("r"))]),
         [({"w": 3, "l": [1.5], "m": {"k": 1}},)])
    case("use_graph_hop", Q([match(pat(N("a"), R(None, Rt), N("b")))], ret([(nm("a"), None), (nm("b"), None)]),
                            graph=G), [("fay", "gus")])
    case("all_graphs_count", Q([match(pat(N("a", A), R(None, Rt), N("b", B)))], ret([(agg("count"), None)])), [(3,)])
    return c


def describe(q: Query) -> str:
    """The query, its parameters and where it came from, for a failure message."""
    where = f"seed {q.seed} #{q.index} " if q.seed is not None else ""
    return f"{where}[{q.family}] {q.text}" + (f"  params={json.dumps(q.params)}" if q.params else "")
