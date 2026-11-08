"""4.6.3: `remove_legacy_objects` keeps a same-named SQLUser function whose body IVG never shipped.

IVG 4.5.x and earlier made their SQLUser functions with ``CREATE OR REPLACE FUNCTION SQLUser.<name>``, so a
namespace's own ``JSON_VALUE`` or ``RAND`` was overwritten, and the migration guide tells an operator to install theirs
again after upgrading. Before 4.6.3 ``remove_legacy_objects(dry_run=False)`` then dropped it, by its name alone: the
operator lost their function a second time. Three namespaces, each checked by INFORMATION_SCHEMA and by calling the
function after the call:

1. v4.5.1's own setup, 4.6's, then the operator's JSON_VALUE (one that reads the nested path ``$.a.b``, which 4.5.1's
   cannot): the removal keeps it, and it still answers the nested path; IVG's other leftovers go.
2. No IVG 4.5.x at all: the operator's own JSON_VALUE and RAND, then 4.6's setup. The dry run lists both as not IVG's
   and the removal keeps both.
3. v4.5.1 and 4.6 alone: every leftover is IVG's and goes, as before 4.6.3.

The namespace is `IVG_NS_IVG463L` (the gate names one per run; unset, the tests skip), dropped and built anew for each
case (`scripts/fixtures/build_release_namespace.py` for v4.5.1's own code; `make_scratch_namespace.recreate` for the
empty one).

    export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 IVG_NS_IVG463L=IVG463L
    .venv/bin/pytest tests/e2e/test_463_legacy_same_named_functions_e2e.py -p no:cacheprovider
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262, scratch_vars

pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG_NS_IVG463L")]

NS = os.environ.get("IVG_NS_IVG463L", "")

#: The operator's JSON_VALUE: walks each key of the path (no `'`, `?` or `:` in the body: the DB-API DDL tokenizer
#: takes `:1` for a parameter).
USER_JSON_VALUE = (
    "CREATE OR REPLACE FUNCTION SQLUser.JSON_VALUE(j VARCHAR(32000), p VARCHAR(1000)) RETURNS VARCHAR(4000) "
    "LANGUAGE OBJECTSCRIPT { Set obj = ##class(%Library.DynamicObject).%FromJSON(j), path = $Extract(p, 3, *) "
    'Set n = $Length(path, "."), i = 0 While i < n { Set i = i + 1, obj = obj.%Get($Piece(path, ".", i)) '
    'If $IsObject(obj) = 0 { Quit } } Quit obj _ "" }'
)
USER_RAND = "CREATE OR REPLACE FUNCTION SQLUser.RAND() RETURNS DOUBLE LANGUAGE OBJECTSCRIPT { Quit 0.25 }"
NESTED = ('{"a": {"b": "deep"}}', "$.a.b")
#: The manifest classes a v4.5.1 namespace holds that removal deletes as classes, and a function's generated class.
SHIPPED_CLASSES = {"User.CypherFn", "User.Exec", "User.SQL", "User.PageRankEmbedded", "Graph.KG.Benchmark",
                   "Graph.KG.BenchFormat", "iris.vector.graph.GraphOperators"}


def _engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, native_cypher=False)


def _execute(conn, sql: str) -> None:
    cur = conn.cursor()
    try:
        cur.execute(sql)
    finally:
        cur.close()
    conn.commit()


def _json_value(conn, doc: str, path: str):
    return ns262.rows(conn, "SELECT SQLUser.JSON_VALUE(?, ?)", [doc, path])[0][0]


def _rand(conn):
    return float(ns262.rows(conn, "SELECT SQLUser.RAND()")[0][0])


def _defined(conn, names) -> set:
    names = sorted(names)
    marks = ", ".join("?" for _ in names)
    return {str(r[0]) for r in ns262.rows(conn, f"SELECT Name FROM %Dictionary.ClassDefinition WHERE Name IN ({marks})",
                                          names)}


def _by_name(items) -> dict:
    return {i["sql_name"] or i["class_name"]: i for i in items}


@pytest.fixture
def at_451_with_own_json_value():
    from build_release_namespace import build

    build("v4.5.1", NS, seed=False)
    conn = ns262.connect(NS)
    engine = _engine(conn)
    engine.initialize_schema()
    _execute(conn, USER_JSON_VALUE)
    yield conn, engine
    conn.close()


def test_the_operators_json_value_is_kept_and_ivgs_leftovers_go(at_451_with_own_json_value):
    conn, engine = at_451_with_own_json_value
    before = ns262.routines(conn, "SQLUser")
    assert len(before) == 46 and "JSON_VALUE" in before  # v4.5.1's 46 functions, one of them now the operator's
    assert len(ns262.views(conn, "SQLUser")) == 4
    assert _json_value(conn, *NESTED) == "deep"

    engine.remove_legacy_objects(dry_run=False)

    assert ns262.routines(conn, "SQLUser") == {"JSON_VALUE"}
    assert _json_value(conn, *NESTED) == "deep"
    assert ns262.views(conn, "SQLUser") == set()
    assert _defined(conn, SHIPPED_CLASSES | {"User.funcJSONVALUE", "User.funcRAND"}) == {"User.funcJSONVALUE"}
    left = _by_name(engine.remove_legacy_objects(dry_run=True))
    assert list(left) == ["SQLUser.JSON_VALUE"]
    item = left["SQLUser.JSON_VALUE"]
    assert item["ivg_owned"] is False and item["removed"] is False
    assert "not IVG's" in item["note"] and "kept" in item["note"], item["note"]
    assert engine.status().legacy_objects == 0  # the operator's function is not a leftover to report


@pytest.fixture
def own_functions_only():
    ns262.recreate(NS)
    conn = ns262.connect(NS)
    _execute(conn, USER_JSON_VALUE)
    _execute(conn, USER_RAND)
    engine = _engine(conn)
    engine.initialize_schema()
    yield conn, engine
    conn.close()


def test_a_namespace_that_never_had_45x_keeps_its_own_functions(own_functions_only):
    conn, engine = own_functions_only
    assert ns262.routines(conn, "SQLUser") == {"JSON_VALUE", "RAND"}
    assert _json_value(conn, *NESTED) == "deep" and _rand(conn) == 0.25

    listed = _by_name(engine.remove_legacy_objects(dry_run=True))
    engine.remove_legacy_objects(dry_run=False)

    assert ns262.routines(conn, "SQLUser") == {"JSON_VALUE", "RAND"}
    assert _json_value(conn, *NESTED) == "deep" and _rand(conn) == 0.25
    assert _defined(conn, {"User.funcJSONVALUE", "User.funcRAND"}) == {"User.funcJSONVALUE", "User.funcRAND"}
    assert set(listed) == {"SQLUser.JSON_VALUE", "SQLUser.RAND"}
    for name, item in listed.items():
        assert item["ivg_owned"] is False and item["removed"] is False, item
        assert name in item["note"] and "not IVG's" in item["note"] and "kept" in item["note"], item["note"]


@pytest.fixture
def at_451():
    from build_release_namespace import build

    build("v4.5.1", NS, seed=False)
    conn = ns262.connect(NS)
    engine = _engine(conn)
    engine.initialize_schema()
    yield conn, engine
    conn.close()


def test_a_plain_451_namespace_loses_every_leftover(at_451):
    conn, engine = at_451
    routines, views = ns262.routines(conn, "SQLUser"), ns262.views(conn, "SQLUser")
    assert len(routines) == 46 and len(views) == 4
    assert _defined(conn, SHIPPED_CLASSES) == SHIPPED_CLASSES

    listed = engine.remove_legacy_objects(dry_run=True)
    removed = engine.remove_legacy_objects(dry_run=False)

    assert ns262.routines(conn, "SQLUser") == set()
    assert ns262.views(conn, "SQLUser") == set()
    assert _defined(conn, SHIPPED_CLASSES) == set()
    assert {i["sql_name"] for i in listed if i["sql_name"]} == {f"SQLUser.{n}" for n in routines | views}
    assert all(i["ivg_owned"] and i["note"] == "" for i in listed), [i for i in listed if not i["ivg_owned"]]
    assert all(i["removed"] for i in removed)
    assert engine.remove_legacy_objects(dry_run=True) == []
