"""4.6.0 R307: every writer x every reader x both executors over an adversarial value set, under the driver this
interpreter has.

Dozens of 4.6.0 defects were one class: a value written by one path read back with another type or order by another
(numbers as text, a $DOUBLE that driver 5.4.0 and 5.4.1 decode differently, strings starting "P" read as durations,
lists stored as the parser's text, `any(...)` answering '1'). Each was fixed after a walkthrough found it. This file
crosses them all.

- Values: tests/oracle/values.py (integers to 2**62, floats with NaN and infinity, strings that spell numbers, P-strings,
  durations, dates, booleans, '' and $Char(0), unicode, quotes, lists, maps).
- Writers: nodes by create_node, bulk_create_nodes, an NDJSON node line, Cypher CREATE (literal and $v), SET (literal and
  $v) and MERGE; relationships by create_edge, an NDJSON edge line, Cypher CREATE (literal and $v), MERGE, and SET
  (literal and $v). bulk_create_edges writes no properties (API_REFERENCE), so it is not a writer here.
- Readers: get_node, RETURN x.p, WITH, collect, DISTINCT, min/max, a map projection, a whole x, a *1..1 read,
  properties(x), toString, x.p + 1, any(...) beside another column, list functions, size of a string, WHERE = $v,
  WHERE = literal, >= and >, and ORDER BY per group; each on the native executor and the SQL path.
- The documented answer of every cell: tests/oracle/spec.py, from the docs (or openCypher where they say nothing),
  never from IVG's output. A cell asserts the value and its Python type; a failure lists writer, reader, path, driver,
  value, what came back and what is documented, with the source of the documented answer.

Namespace: `IVG_NS_IVG460FXOV`, an IVG460FX* scratch namespace with this checkout installed
(scripts/dev/make_scratch_namespace.py). Run it under both drivers (scripts/dev/driver_matrix.sh's interpreters):

    IVG_NS_IVG460FXOV=IVG460FXOV .venv/bin/python -m pytest tests/e2e/test_460_r307_value_matrix_e2e.py
    IVG_NS_IVG460FXOV=IVG460FXOV ~/.cache/ivg-bench/fixes26/venv541/bin/python -m pytest tests/e2e/test_460_r307_...

`IVG_R307_REPORT=<dir>` writes every cell (got, documented, executor) to `<dir>/cells-<driver>.jsonl`.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from tests.e2e import ns460alt as alt
from tests.oracle import spec
from tests.oracle.values import BY_KEY, VALUES

NS = os.environ.get("IVG_NS_IVG460FXOV", "")
DRIVER = importlib.metadata.version("intersystems-irispython")
REPORT = os.environ.get("IVG_R307_REPORT", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NS_IVG460FXOV must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

HUB, RHUB = "ov_hub", "ov_rhub"
MISSING = "<no row>"


def node_id(writer, key):
    return f"ov_n_{writer}_{key}"


def target_id(writer, key):
    return f"ov_t_{writer}_{key}"


def _cell_of(store, ident):
    """(writer, key) from a node id or a relationship's target id."""
    prefix = "ov_n_" if store == "node" else "ov_t_"
    if not isinstance(ident, str) or not ident.startswith(prefix):
        return None
    rest = ident[len(prefix):]
    for writer in sorted(spec.WRITERS[store], key=len, reverse=True):
        if rest.startswith(writer + "_") and rest[len(writer) + 1:] in BY_KEY:
            return writer, rest[len(writer) + 1:]
    return None


# ── writing ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _tags(writer, v):
    return {"k": v.key, "w": writer, "g": v.group}


def _tag_literal(writer, v):
    return ", ".join(f"{k}: '{t}'" for k, t in _tags(writer, v).items())


def _write_nodes(eng, scratch: Path, errors):
    written = []
    for writer in spec.NODE_WRITERS:
        todo = [v for v in VALUES if spec.writes("node", writer, v)]
        if writer == "ndjson":
            path = scratch / f"nodes-{DRIVER}.ndjson"
            with path.open("w") as f:
                for v in todo:
                    f.write(json.dumps({"kind": "node", "id": node_id(writer, v.key), "labels": ["L307"],
                                        "properties": {"p": v.py, **_tags(writer, v)}}) + "\n")
            try:
                got = eng.import_graph_ndjson(str(path))
                assert got.get("skipped") == 0, got
                written += [(writer, v) for v in todo]
            except Exception as ex:  # noqa: BLE001 - every write failure is reported as a cell
                errors.update({("node", writer, v.key): repr(ex) for v in todo})
            continue
        for v in todo:
            nid = node_id(writer, v.key)
            try:
                if writer == "api_create":
                    assert eng.create_node(nid, labels=["L307"], properties={"p": v.py, **_tags(writer, v)})
                elif writer == "api_bulk":
                    assert eng.bulk_create_nodes([{"id": nid, "labels": ["L307"],
                                                   "properties": {"p": v.py, **_tags(writer, v)}}]) == [nid]
                elif writer == "cy_create":
                    eng.execute_cypher(f"CREATE (:L307 {{id: '{nid}', {_tag_literal(writer, v)}, p: {v.cypher}}})",
                                       raise_on_error=True)
                elif writer == "cy_create_p":
                    eng.execute_cypher(f"CREATE (:L307 {{id: '{nid}', {_tag_literal(writer, v)}, p: $v}})",
                                       {"v": v.py}, raise_on_error=True)
                elif writer in ("cy_set", "cy_set_p"):
                    eng.execute_cypher(f"CREATE (:L307 {{id: '{nid}', {_tag_literal(writer, v)}}})", raise_on_error=True)
                    rhs = v.cypher if writer == "cy_set" else "$v"
                    eng.execute_cypher(f"MATCH (n:L307 {{id: '{nid}'}}) SET n.p = {rhs}", {"v": v.py},
                                       raise_on_error=True)
                elif writer == "cy_merge":
                    eng.execute_cypher(f"MERGE (:L307 {{id: '{nid}', {_tag_literal(writer, v)}, p: {v.cypher}}})",
                                       raise_on_error=True)
                written.append((writer, v))
            except Exception as ex:  # noqa: BLE001
                errors[("node", writer, v.key)] = repr(ex)
    for writer, v in written:
        eng.create_edge(HUB, "HAS", node_id(writer, v.key))
    return written


def _write_rels(eng, scratch: Path, errors):
    written = []
    for writer in spec.REL_WRITERS:
        todo = [v for v in VALUES if spec.writes("rel", writer, v)]
        for v in todo:
            eng.create_node(target_id(writer, v.key), labels=["L307T"])
        if writer == "ndjson_edge":
            path = scratch / f"edges-{DRIVER}.ndjson"
            with path.open("w") as f:
                for v in todo:
                    f.write(json.dumps({"kind": "edge", "source": RHUB, "predicate": "R307",
                                        "target": target_id(writer, v.key),
                                        "qualifiers": {"p": v.py, **_tags(writer, v)}}) + "\n")
            try:
                got = eng.import_graph_ndjson(str(path))
                assert got.get("skipped") == 0, got
                written += [(writer, v) for v in todo]
            except Exception as ex:  # noqa: BLE001
                errors.update({("rel", writer, v.key): repr(ex) for v in todo})
            continue
        for v in todo:
            tid = target_id(writer, v.key)
            ends = f"MATCH (a {{node_id: '{RHUB}'}}), (b {{node_id: '{tid}'}}) "
            try:
                if writer == "api_edge":
                    assert eng.create_edge(RHUB, "R307", tid, qualifiers={"p": v.py, **_tags(writer, v)})
                elif writer in ("cy_create", "cy_create_p", "cy_merge"):
                    verb = "MERGE" if writer == "cy_merge" else "CREATE"
                    rhs = "$v" if writer == "cy_create_p" else v.cypher
                    eng.execute_cypher(ends + f"{verb} (a)-[:R307 {{{_tag_literal(writer, v)}, p: {rhs}}}]->(b)",
                                       {"v": v.py} if writer == "cy_create_p" else {}, raise_on_error=True)
                elif writer in ("cy_set", "cy_set_p"):
                    eng.execute_cypher(ends + f"CREATE (a)-[:R307 {{{_tag_literal(writer, v)}}}]->(b)",
                                       raise_on_error=True)
                    rhs = v.cypher if writer == "cy_set" else "$v"
                    eng.execute_cypher(
                        f"MATCH (a {{node_id: '{RHUB}'}})-[r:R307]->(b {{node_id: '{tid}'}}) SET r.p = {rhs}",
                        {"v": v.py} if writer == "cy_set_p" else {}, raise_on_error=True)
                written.append((writer, v))
            except Exception as ex:  # noqa: BLE001
                errors[("rel", writer, v.key)] = repr(ex)
    return written


def _clean(eng):
    r = eng.execute_cypher("MATCH (n) WHERE n.node_id STARTS WITH 'ov_' RETURN n.node_id")
    assert r.error is None, r.error
    ids = sorted({row[0] for row in r.rows})
    if ids:
        eng.bulk_delete_nodes(ids)


@pytest.fixture(scope="module")
def matrix():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    scratch = Path(REPORT or Path.home() / ".cache" / "ivg-bench" / "oracle-values")
    scratch.mkdir(parents=True, exist_ok=True)
    _clean(nat)
    nat.create_node(HUB, labels=["L307H"])
    nat.create_node(RHUB, labels=["L307H"])
    errors: dict = {}
    written = {"node": _write_nodes(nat, scratch, errors), "rel": _write_rels(nat, scratch, errors)}
    with contextlib.suppress(Exception):
        conn.commit()
    nat.sync()
    report = (scratch / f"cells-{DRIVER}.jsonl").open("w") if REPORT else None
    try:
        yield {"engines": {"native": nat, "sql": sql}, "written": written, "errors": errors, "report": report,
               "conn": conn}
    finally:
        if report:
            report.close()
        if not os.environ.get("IVG_R307_KEEP"):
            _clean(nat)
        conn.close()


# ── comparing ──────────────────────────────────────────────────────────────────────────────────────────────────────
def show(x) -> str:
    return MISSING if x is MISSING else f"{type(x).__name__} {x!r}"


def _json_or(x):
    if isinstance(x, str):
        with contextlib.suppress(ValueError):
            return json.loads(x)
    return x


def _props_of(cell):
    """A whole node's or relationship's properties, from a dict or its JSON text ({"type", "props"} for a
    relationship, `n_props` for a node)."""
    cell = _json_or(cell)
    if isinstance(cell, dict) and isinstance(cell.get("props"), (dict, str)):
        return _json_or(cell["props"])
    if isinstance(cell, dict) and isinstance(cell.get("properties"), (dict, str)):
        return _json_or(cell["properties"])
    return cell


def _run(m, path, query, params=None):
    """Rows of one query on one path, and which executor answered."""
    eng = m["engines"][path]
    before = getattr(eng, "_native_answers", 0)
    r = eng.execute_cypher(query, dict(params or {}))
    by = "native" if getattr(eng, "_native_answers", 0) > before else "sql"
    return r, by


def _base(store):
    return (f"MATCH (h {{node_id: '{HUB}'}})-[:HAS]->(n) ", "n", "n.node_id") if store == "node" else (
        f"MATCH (h {{node_id: '{RHUB}'}})-[r:R307]->(t) ", "r", "t.node_id")


def _groups_filter(x, reader):
    groups = spec.READERS[reader][1]
    if groups is None:
        return ""
    return f"WHERE {x}.g IN [{', '.join(repr(g) for g in sorted(groups))}] "


def _query(store, reader):
    """The query of a reader that answers every cell in one statement: (id, value) per row."""
    match, x, ident = _base(store)
    where = _groups_filter(x, reader)
    if reader == "varlen":
        if store == "node":
            return f"MATCH (h {{node_id: '{HUB}'}})-[:HAS*1..1]->(n) RETURN n.node_id, n.p", None
        return f"MATCH (h {{node_id: '{RHUB}'}})-[rs:R307*1..1]->(t) RETURN t.node_id, rs", "rels"
    if reader == "varlen_comp":
        return f"MATCH (h {{node_id: '{RHUB}'}})-[rs:R307*1..1]->(t) RETURN t.node_id, [y IN rs | y.p] AS ps", "first"
    body = {
        "ret": (f"RETURN {ident}, {x}.p", None),
        "with": (f"WITH {x}, {ident} AS i, {x}.p AS v RETURN i, v", None),
        "collect": (f"RETURN {ident}, collect({x}.p) AS c", "first"),
        "distinct": (f"RETURN DISTINCT {ident}, {x}.p", None),
        "minmax": (f"RETURN {ident}, min({x}.p), max({x}.p)", "pair"),
        "mapproj": (f"RETURN {ident}, {x} {{.p}} AS m", "p"),
        "whole": (f"RETURN {ident}, {x}", "whole"),
        "props": (f"RETURN {ident}, properties({x}) AS m", "p"),
        "tostring": (f"RETURN {ident}, toString({x}.p)", None),
        "arith": (f"RETURN {ident}, {x}.p + 1", None),
        "any_beside": (f"RETURN {ident}, any(e IN [{x}.p] WHERE e IS NOT NULL)", None),
        "list_fns": (f"RETURN {ident}, size({x}.p), head({x}.p), last({x}.p), {x}.p[0], "
                     f"any(e IN {x}.p WHERE e IS NOT NULL), all(e IN {x}.p WHERE e IS NOT NULL)", "list_fns"),
        "size_str": (f"RETURN {ident}, size({x}.p)", None),
    }[reader]
    return match + where + body[0], body[1]


def _value(row, shape, columns):
    """The cell value a reader's row carries."""
    if shape is None:
        return row[1]
    if shape == "first":
        c = _json_or(row[1])
        return c[0] if isinstance(c, list) and len(c) == 1 else ("<not a one-element list>", c)
    if shape == "pair":
        return row[1] if spec.same(row[1], row[2]) else ("<min and max differ>", row[1], row[2])
    if shape == "p":
        m = _json_or(row[1])
        return m.get("p") if isinstance(m, dict) else ("<not a map>", m)
    if shape == "rels":
        rels = _json_or(row[1])
        if not (isinstance(rels, list) and len(rels) == 1):
            return ("<not one relationship>", rels)
        props = _props_of(rels[0])
        return props.get("p") if isinstance(props, dict) else ("<no props>", rels[0])
    if shape == "whole":
        # a node answers n_id, n_labels, n_props; a relationship one value
        cols = list(columns)
        cell = row[cols.index("n_props")] if "n_props" in cols else row[1]
        props = _props_of(cell)
        return props.get("p") if isinstance(props, dict) else ("<no props>", cell)
    if shape == "list_fns":
        return dict(zip(("size", "head", "last", "first", "any", "all"), row[1:7]))
    raise KeyError(shape)


def _fail_table(rows):
    lines = [f"{len(rows)} cell(s) disagree with the documented answer (driver {DRIVER}):",
             "store | writer | reader | path(executor) | value | got | documented [source]"]
    lines += [" | ".join(map(str, r)) for r in rows]
    return "\n".join(lines)


def _record(m, **cell):
    if m["report"]:
        m["report"].write(json.dumps(cell, default=repr) + "\n")


def _check(m, store, reader, path, got_by_cell, by):
    bad = []
    for writer, v in m["written"][store]:
        if not spec.applies(store, reader, v):
            continue
        want = spec.expect(store, writer, v, reader)
        got = got_by_cell.get((writer, v.key), MISSING)
        ok = got is not MISSING and spec.same(got, want.value)
        _record(m, store=store, writer=writer, reader=reader, path=path, executor=by, driver=DRIVER, value=v.key,
                got=show(got), want=show(want.value), source=want.source, ok=ok)
        if not ok:
            bad.append((store, writer, reader, f"{path}({by})", v.key, show(got), f"{show(want.value)} [{want.source}]"))
    return bad


PATHS = ("native", "sql")
ROW_READERS = ("ret", "with", "collect", "distinct", "minmax", "mapproj", "whole", "varlen", "varlen_comp", "props",
               "tostring", "arith", "any_beside", "list_fns", "size_str")


def test_every_write_succeeds(matrix):
    assert not matrix["errors"], "\n".join(f"{k}: {e}" for k, e in sorted(matrix["errors"].items()))


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("store,reader", [(s, r) for r in ROW_READERS for s in ("node", "rel")
                                          if s in spec.READERS[r][0]])
def test_reader(matrix, store, reader, path):
    query, shape = _query(store, reader)
    r, by = _run(matrix, path, query)
    assert r.error is None, (query, r.error)
    got, dupes = {}, []
    for row in r.rows:
        cell = _cell_of(store, row[0])
        if cell is None:
            continue
        if cell in got:
            dupes.append(cell)
        got[cell] = _value(row, shape, r.columns)
    bad = _check(matrix, store, reader, path, got, by)
    assert not dupes, f"{reader} {path}: more than one row for {dupes}"
    assert not bad, _fail_table(bad)


def test_get_node(matrix):
    eng = matrix["engines"]["native"]
    got = {}
    for writer, v in matrix["written"]["node"]:
        node = eng.get_node(node_id(writer, v.key))
        got[(writer, v.key)] = MISSING if node is None else node.get("p")
    bad = _check(matrix, "node", "get_node", "api", got, "api")
    assert not bad, _fail_table(bad)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("store", ("node", "rel"))
@pytest.mark.parametrize("reader", ("eq_param", "eq_lit", "ge_param", "gt_param"))
def test_filter(matrix, store, reader, path):
    match, x, ident = _base(store)
    op = {"eq_param": "=", "eq_lit": "=", "ge_param": ">=", "gt_param": ">"}[reader]
    bad, executors = [], set()
    by_value = defaultdict(list)
    for writer, v in matrix["written"][store]:
        if spec.applies(store, reader, v):
            by_value[v.key].append(writer)
    for key, writers in sorted(by_value.items()):
        v = BY_KEY[key]
        rhs = v.cypher if reader == "eq_lit" else "$v"
        query = match + f"WHERE {x}.k = $k AND {x}.p {op} {rhs} RETURN {ident}"
        r, by = _run(matrix, path, query, {"k": key, "v": v.py})
        executors.add(by)
        if r.error is not None:
            bad += [(store, w, reader, f"{path}({by})", key, f"error {r.error[:120]}", "a row or none") for w in writers]
            continue
        hits = {cell[0] for cell in (_cell_of(store, row[0]) for row in r.rows) if cell}
        got = {(w, key): (w in hits) for w in writers}
        bad += _check(matrix, store, reader, path, got, by)
    assert not bad, _fail_table(bad)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("store", ("node", "rel"))
@pytest.mark.parametrize("group", sorted(spec.ORDER_GROUPS))
def test_order_by(matrix, store, group, path):
    match, x, ident = _base(store)
    groups = ", ".join(repr(g) for g in sorted(spec.ORDER_GROUPS[group]))
    query = match + f"WHERE {x}.g IN [{groups}] RETURN {ident} ORDER BY {x}.w, {x}.p, {x}.k"
    r, by = _run(matrix, path, query)
    assert r.error is None, (query, r.error)
    got = defaultdict(list)
    for row in r.rows:
        cell = _cell_of(store, row[0])
        if cell:
            got[cell[0]].append(cell[1])
    bad = []
    written = {(w, v.key) for w, v in matrix["written"][store]}
    for writer in spec.WRITERS[store]:
        want = spec.order(store, writer, group)
        want_keys = [k for k in want.value if (writer, k) in written]
        if not want_keys:
            continue
        ok = got[writer] == want_keys
        _record(matrix, store=store, writer=writer, reader=f"order:{group}", path=path, executor=by, driver=DRIVER,
                value=group, got=got[writer], want=want_keys, source=want.source, ok=ok)
        if not ok:
            bad.append((store, writer, f"order:{group}", f"{path}({by})", group, got[writer],
                        f"{want_keys} [{want.source}]"))
    assert not bad, _fail_table(bad)


@pytest.mark.parametrize("path", PATHS)
def test_any_answers_a_boolean_alone_and_beside_a_column(matrix, path):
    """R307 (a), found by fixes50: `RETURN n.id, any(...)` answered '1' as text, and alone 1. openCypher: true."""
    for query in (f"MATCH (n {{node_id: '{HUB}'}}) RETURN any(x IN [1, 2] WHERE x = 1)",
                  f"MATCH (n {{node_id: '{HUB}'}}) RETURN n.node_id, any(x IN [1, 2] WHERE x = 1)",
                  f"MATCH (n {{node_id: '{HUB}'}}) RETURN n.node_id, all(x IN [1, 2] WHERE x > 0), "
                  "none(x IN [1, 2] WHERE x > 5), single(x IN [1, 2] WHERE x = 2)"):
        r, by = _run(matrix, path, query)
        assert r.error is None, (query, r.error)
        flags = r.rows[0][-1:] if "single" not in query else r.rows[0][1:]
        assert [show(f) for f in flags] == [show(True)] * len(flags), (query, path, by, DRIVER, r.rows)


#: R307 (b): by length, as openCypher's CIP2015-08-06-date-time ("Ordering", vendor/opencypher/cip/2.testable) defines
#: ORDER BY for durations: "normalising all components as if all years were 365.2425 days long, all months were
#: 30.436875 (1/12 year) days long, and all days were 24 hours long" (a month 2629746 s). The TCK has no duration
#: ORDER BY scenario; Temporal7 [6] holds equality per component, which ORDER BY does not decide. Equal lengths sort
#: by text (P1D before PT24H), so the order is total.
DURATIONS_BY_LENGTH = ["PT-1S", "PT0S", "PT1S", "PT1H", "PT1H30M", "P1D", "PT24H", "PT36H", "P1W", "P14D", "P1M",
                       "P1Y2M", "P2Y"]


@pytest.mark.parametrize("where", ["udf", "class"])
def test_duration_keys_sort_by_length(matrix, where):
    """R307 (b): ORDER BY over durations sorted by their text, so P1D came before PT1H. The SQL path's CY_SORT_KEY and
    the native executor's Graph.KG.Cypher.SortKey.Key are the same code (tests/unit/test_257_sort_key_class.py)."""
    import iris

    eng = matrix["engines"]["sql"]
    if where == "udf":
        cur = matrix["conn"].cursor()

        def key(d):
            cur.execute(f"SELECT {eng._ivg_schema}.CY_SORT_KEY(?, 'x')", [d])
            return cur.fetchone()[0]
    else:
        nat = iris.createIRIS(matrix["conn"])

        def key(d):
            return nat.classMethodValue("Graph.KG.Cypher.SortKey", "Key", d, "x")
    keys = {d: key(d) for d in DURATIONS_BY_LENGTH}
    assert all(k.startswith("k") for k in keys.values()), keys
    assert sorted(DURATIONS_BY_LENGTH, key=keys.get) == DURATIONS_BY_LENGTH, keys


@pytest.mark.parametrize("path", PATHS)
def test_durations_do_not_compare(matrix, path):
    """openCypher CIP2015-08-06-date-time: "Since Duration values are not comparable, the result of applying a
    comparison operator between two Duration values is null." Between two stored properties the range compares sort
    keys (CY_CMP, Exec.CmpKeys), which answered by text and, with the length key, would answer by length. Numbers in the
    same shape still compare (the control)."""
    pair = (f"MATCH (h {{node_id: '{HUB}'}})-[:HAS]->(a), (h)-[:HAS]->(b) WHERE a.w = 'cy_create' "
            "AND b.w = 'cy_create' AND a.k = $a AND b.k = $b ")
    for a, b, want in (("dP1D", "dPT36H", [None, None]), ("i0", "i7", [True, False])):
        r, by = _run(matrix, path, pair + "RETURN a.p < b.p, a.p > b.p", {"a": a, "b": b})
        assert r.error is None, r.error
        assert [[show(x) for x in row] for row in r.rows] == [[show(x) for x in want]], (a, b, path, by, DRIVER)
        r, by = _run(matrix, path, pair + "AND a.p < b.p RETURN count(*)", {"a": a, "b": b})
        assert r.error is None, r.error
        assert r.rows == [[0 if want[0] is None else 1]], (a, b, path, by, DRIVER, r.rows)
