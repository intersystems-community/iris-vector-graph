"""The SNB interactive-update workload: deterministic parameters over ids that exist, new ids that do not."""

import pytest

from scripts.bench.alt import snb_updates as u


@pytest.fixture()
def nodes():
    return {
        "Person": [str(i) for i in range(1, 30)],
        "Post": [str(100 + i) for i in range(30)],
        "Forum": [str(500 + i) for i in range(10)],
    }


def test_every_update_names_only_parameters_it_is_given(nodes):
    params = u.update_params(nodes, n=5, seed=1)
    assert set(params) == set(u.UPDATES)
    for name, spec in u.UPDATES.items():
        assert len(params[name]) == 5
        for ps in params[name]:
            assert set(spec["params"]) == set(ps), name


def test_parameters_are_deterministic_and_existing_ids_come_from_the_graph(nodes):
    a, b = u.update_params(nodes, n=8, seed=3), u.update_params(nodes, n=8, seed=3)
    assert a == b
    assert u.update_params(nodes, n=8, seed=4) != a
    for ps in a["IU8_add_friendship"]:
        assert ps["friendA"] in nodes["Person"] and ps["friendB"] in nodes["Person"] and ps["friendA"] != ps["friendB"]
    for ps in a["IU2_add_like"]:
        assert ps["person"] in nodes["Person"] and ps["post"] in nodes["Post"]


def test_new_ids_never_collide_with_each_other_or_the_graph(nodes):
    params = u.update_params(nodes, n=20, seed=1)
    new = [ps["sid"] for name in ("IU1_add_person", "IU4_add_forum", "IU6_add_post", "IU7_add_comment") for ps in params[name]]
    assert len(new) == len(set(new)) == 80
    assert all(int(x) >= u.NEW_ID_BASE for x in new)


def test_likes_memberships_and_friendships_avoid_pairs_the_graph_already_has(nodes):
    persons, posts, forums = nodes["Person"], nodes["Post"], nodes["Forum"]
    taken = {
        "LIKES": {(p, m) for p in persons for m in posts[:-1]},  # one free post left
        "HAS_MEMBER": {(f, p) for f in forums for p in persons[:-2]},
        "KNOWS": {frozenset((a, b)) for a in persons for b in persons if a < b and (a, b) != ("1", "2")},
    }
    params = u.update_params(nodes, n=1, seed=5, taken=taken)
    assert (params["IU2_add_like"][0]["person"], params["IU2_add_like"][0]["post"]) not in taken["LIKES"]
    assert (params["IU5_add_membership"][0]["forum"], params["IU5_add_membership"][0]["person"]) not in taken["HAS_MEMBER"]
    assert frozenset((params["IU8_add_friendship"][0]["friendA"], params["IU8_add_friendship"][0]["friendB"])) not in taken["KNOWS"]


def test_friendship_parameters_do_not_share_names_with_the_pattern_variables():
    # the baseline translator reads $a / $b as the node ids themselves when the pattern variables are a and b
    assert not {"a", "b"} & set(u.UPDATES["IU8_add_friendship"]["params"])


def test_friendships_are_new_pairs(nodes):
    params = u.update_params(nodes, n=25, seed=2)
    pairs = [frozenset((ps["friendA"], ps["friendB"])) for ps in params["IU8_add_friendship"]]
    assert len(pairs) == len(set(pairs))


def test_statements_are_single_cypher_writes_and_ivg_keys_nodes():
    for name, spec in u.UPDATES.items():
        text = u.statement(name, "neo4j")
        assert "CREATE" in text and ";" not in text, name
        assert "id: $key" not in text
        if "(" in text and name in ("IU1_add_person", "IU4_add_forum", "IU6_add_post", "IU7_add_comment"):
            assert "id: $key" in u.statement(name, "ivg"), name


def test_ivg_parameters_carry_the_node_key():
    ps = {"sid": "9000000001", "firstName": "x"}
    assert u.with_key(ps, "Person")["key"] == "Person:9000000001"


def test_expected_counts_follow_the_parameters(nodes):
    params = u.update_params(nodes, n=7, seed=1)
    exp = u.expected_delta(params)
    assert exp["nodes"] == {"Person": 7, "Forum": 7, "Post": 7, "Comment": 7}
    assert exp["edges"]["KNOWS"] == 7 and exp["edges"]["LIKES"] == 7 and exp["edges"]["HAS_MEMBER"] == 7


def test_statements_keep_their_braces_for_every_engine():
    for engine in ("ivg", "neo4j", "memgraph"):
        for name in u.UPDATES:
            text = u.statement(name, engine)
            assert text.count("{") == text.count("}") and "<<" not in text
    assert "(p:Person {sid: $sid" in u.statement("IU1_add_person", "neo4j")
    assert "(p:Person {id: $key, sid: $sid" in u.statement("IU1_add_person", "ivg")
