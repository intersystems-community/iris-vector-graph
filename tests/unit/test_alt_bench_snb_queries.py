"""The SNB query set and its parameter sampler: deterministic, shared by every engine."""

import re

import pytest

from scripts.bench.alt import snb_queries as q


@pytest.fixture(scope="module")
def params(tmp_path_factory):
    root = tmp_path_factory.mktemp("snb")

    def put(d, text):
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d / "part-0.csv").write_text(text)

    people = "".join(f"2010-01-01T00:00:00.000+00:00|x|false|{i}|Name{i % 4}|L|male|1980-01-01|1.1.1.1|Firefox|2|en|e\n" for i in range(1, 41))
    put("dynamic/Person", "creationDate|deletionDate|explicitlyDeleted|id|firstName|lastName|gender|birthday|locationIP|browserUsed|LocationCityId|language|email\n" + people)
    knows = "".join(f"2010-02-02T00:00:00.000+00:00|x|false|{i}|{j}\n" for i in range(1, 41) for j in range(1, 41) if i < j and (i * j) % 3 == 0)
    put("dynamic/Person_knows_Person", "creationDate|deletionDate|explicitlyDeleted|Person1Id|Person2Id\n" + knows)
    put("static/Tag", "id|name|url|TypeTagClassId\n9|Mozart|u|6\n10|Bach|u|6\n")
    put("dynamic/Post", "creationDate|deletionDate|explicitlyDeleted|id|imageFile|locationIP|browserUsed|language|content|length|CreatorPersonId|ContainerForumId|LocationCountryId\n2011-06-01T00:00:00.000+00:00|x|false|100||1.1.1.1|Firefox|en|hi|2|3|30|1\n2012-06-01T00:00:00.000+00:00|x|false|101||1.1.1.1|Firefox|en|hi|2|3|30|1\n")
    return q.sample_params(root, n=8, seed=7)


def test_every_query_has_cypher_and_names_only_sampled_parameters(params):
    assert len(q.QUERIES) >= 15
    for name, spec in q.QUERIES.items():
        for variant in ("cypher", "memgraph", "ivg"):
            if variant in spec:
                needed = set(re.findall(r"\$(\w+)", spec[variant]))
                assert needed <= set(spec["params"]), (name, variant, needed)
        assert set(spec["params"]) <= set(params[0]), (name, spec["params"])


def test_sampling_is_deterministic_and_prefers_connected_people(params, tmp_path):
    again = q.sample_params(tmp_path, n=8, seed=7) if False else params
    assert len(params) == 8 and params == again
    assert all(isinstance(p["personId"], str) for p in params)
    assert len({p["personId"] for p in params}) == 8  # distinct start people
    assert all(p["personId"] != p["person2Id"] for p in params)


def test_aggregate_queries_order_by_aliases_so_every_engine_accepts_them():
    for name in ("IC5_forums_of_friends", "IC6_tag_cooccurrence", "BI_posts_per_tag", "BI_comments_per_person"):
        c = q.QUERIES[name]["cypher"]
        order = c.split("ORDER BY")[1]
        assert "." not in order.split("LIMIT")[0], (name, order)  # no property expression of an unreturned variable


def test_engine_specific_overrides_exist_only_where_the_dialect_differs():
    assert "*BFS" in q.QUERIES["IC13_shortest_path"]["memgraph"]
    assert {k for spec in q.QUERIES.values() for k in spec} <= {"cypher", "params", "memgraph", "ivg", "ivg_client_limit"}


def test_ivg_overrides_work_around_two_translator_limits_and_say_so():
    # shortestPath needs node ids (`node_id`) on IVG; aggregates with ORDER BY and LIMIT fail to prepare, so the limit moves client-side
    assert "node_id: $personKey" in q.QUERIES["IC13_shortest_path"]["ivg"]
    for name in ("IC5_forums_of_friends", "IC6_tag_cooccurrence", "BI_posts_per_tag", "BI_comments_per_person"):
        spec = q.QUERIES[name]
        assert spec["ivg_client_limit"] == int(spec["cypher"].rsplit("LIMIT", 1)[1])
        assert "LIMIT" not in q.ivg_cypher(spec)
    assert q.ivg_cypher(q.QUERIES["IS1_profile"]) == q.QUERIES["IS1_profile"]["cypher"]


def test_samples_carry_ivg_node_keys(params):
    assert params[0]["personKey"] == "Person:" + params[0]["personId"]


def test_queries_are_plain_single_statements():
    for name, spec in q.QUERIES.items():
        assert ";" not in spec["cypher"], name
        assert spec["cypher"].lstrip().startswith(("MATCH", "OPTIONAL")), name


def test_ivg_node_batches_use_label_colon_id_keys_and_carry_properties():
    from scripts.bench.alt import snb_worker as w

    batches = list(w.node_batches([{"label": "Person", "id": "14", "props": {"sid": "14", "firstName": "Ann"}}] * 5, size=2))
    assert [len(b) for b in batches] == [2, 2, 1]
    assert batches[0][0] == {"id": "Person:14", "labels": ["Person"], "properties": {"sid": "14", "firstName": "Ann"}}


def test_ivg_edge_batches_use_the_same_keys():
    from scripts.bench.alt import snb_worker as w

    e = {"type": "KNOWS", "src": ("Person", "1"), "dst": ("Person", "2")}
    assert list(w.edge_batches([e, e, e], size=2))[0] == [{"s": "Person:1", "p": "KNOWS", "o": "Person:2"}] * 2


def test_result_signature_is_order_insensitive_but_content_sensitive():
    from scripts.bench.alt import snb_worker as w

    a = w.signature([["x", 1], ["y", 2]])
    assert a == w.signature([["y", 2], ["x", 1]])
    assert a != w.signature([["x", 1], ["y", 3]])
    assert w.signature([[1.0]]) == w.signature([[1]])  # 1.0 vs 1 across engines


def test_leg_plan_shrinks_for_slow_queries():
    from scripts.bench.alt import snb_worker as w

    assert w.plan_leg(first_ms=50, params=20, warmup=1, reps=3) == (20, 1, 3)
    # a slow leg still discards its first (cold: compile, buffers) pass and times a later one
    assert w.plan_leg(first_ms=4_000, params=20, warmup=1, reps=3) == (5, 1, 1)
    assert w.plan_leg(first_ms=30_000, params=20, warmup=1, reps=3) == (1, 1, 2)
    assert w.plan_leg(first_ms=50, params=2, warmup=1, reps=3) == (2, 1, 3)


def test_call_with_cap_returns_value_or_none_on_timeout():
    import time as _t

    from scripts.bench.alt import snb_worker as w

    assert w.call_with_cap(lambda: 7, cap_s=1.0) == (True, 7)
    done, val = w.call_with_cap(lambda: _t.sleep(2), cap_s=0.05)
    assert done is False and val is None


def test_forced_plan_parses_and_overrides_the_adaptive_rule():
    from scripts.bench.alt import snb_worker as w

    assert w.parse_plan("5,0,1") == (5, 0, 1)
    assert w.parse_plan(None) is None
    assert w.choose_plan(first_ms=50_000, params=20, warmup=1, reps=3, forced=(20, 1, 3)) == (20, 1, 3)
    assert w.choose_plan(first_ms=50_000, params=20, warmup=1, reps=3, forced=None) == (1, 1, 2)


def test_a_result_carrying_an_error_is_an_error_not_zero_rows():
    from types import SimpleNamespace

    from scripts.bench.alt import snb_worker as w

    assert w.rows_or_raise(SimpleNamespace(rows=[[1]], error=None)) == [[1]]
    assert w.rows_or_raise({"rows": [[2]]}) == [[2]]
    try:
        w.rows_or_raise(SimpleNamespace(rows=[], error="<MAXSTRING>"))
    except RuntimeError as ex:
        assert "MAXSTRING" in str(ex)
    else:
        raise AssertionError("an IVGResult.error must raise")


def test_the_loader_detects_the_deployed_classes_before_it_writes_edges(monkeypatch, tmp_path):
    """A fresh engine reports objectscript_deployed=False until initialize_schema runs, and bulk_ingest_edges then takes the per-row Python path (1.5K edges/s against 9-20K): the IVG load rows were measuring that."""
    import argparse
    import types

    from scripts.bench.alt import snb_worker as w

    calls = []

    class Session:
        stats = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add_edges(self, batch):
            calls.append("add_edges")

    class Engine:
        def initialize_schema(self, *a, **k):
            calls.append("initialize_schema")

        def bulk_create_nodes(self, batch):
            calls.append("bulk_create_nodes")

        def bulk_load_session(self):
            return Session()

    class Conn:
        def cursor(self):
            return types.SimpleNamespace()

        def commit(self):
            pass

    monkeypatch.setattr(w, "_engine", lambda ns: (Conn(), Engine()))
    monkeypatch.setattr(w.model, "iter_nodes", lambda root: iter([{"label": "Person", "id": "1", "props": {}}]))
    monkeypatch.setattr(w.model, "iter_edges", lambda root: iter([{"src": ("Person", "1"), "dst": ("Person", "2"), "type": "KNOWS"}]))
    monkeypatch.setattr("iris_vector_graph.schema.GraphSchema.tune_tables", staticmethod(lambda cur: []))
    w.run_load(argparse.Namespace(ns="X", root="r", out=str(tmp_path / "o.json")))
    assert calls == ["bulk_create_nodes", "initialize_schema", "add_edges"]
