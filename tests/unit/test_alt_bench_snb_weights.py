"""Interaction weights for the weighted-path BI queries: derived from the reply structure, the same for every engine."""

import pytest

from scripts.bench.alt import snb_weights as w


@pytest.fixture()
def raw(tmp_path):
    def put(d, text):
        (tmp_path / "graphs/csv/raw/composite-merged-fk" / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / "graphs/csv/raw/composite-merged-fk" / d / "part-0.csv").write_text(text)

    put("dynamic/Post", "creationDate|deletionDate|explicitlyDeleted|id|imageFile|locationIP|browserUsed|language|content|length|CreatorPersonId|ContainerForumId|LocationCountryId\n"
        "d|x|false|100||i|b|en|c|1|1|30|1\nd|x|false|101||i|b|en|c|1|2|30|1\n")
    put("dynamic/Comment", "creationDate|deletionDate|explicitlyDeleted|id|locationIP|browserUsed|content|length|CreatorPersonId|LocationCountryId|ParentPostId|ParentCommentId\n"
        "d|x|false|200|i|b|c|1|2|1|100|\nd|x|false|201|i|b|c|1|2|1|100|\nd|x|false|202|i|b|c|1|1|1||200\nd|x|false|203|i|b|c|1|3|1|101|\n")
    put("dynamic/Person_knows_Person", "creationDate|deletionDate|explicitlyDeleted|Person1Id|Person2Id\nd|x|false|1|2\nd|x|false|2|3\nd|x|false|1|3\n")
    return tmp_path


def test_interactions_count_replies_in_both_directions(raw):
    inter = w.interaction_counts(raw)
    assert inter[frozenset(("1", "2"))] == 3  # 2 replied to 1's post twice, 1 replied to 2's comment once
    assert inter[frozenset(("2", "3"))] == 1 and frozenset(("1", "3")) not in inter  # 3 replied to 2's post 101? no: post 101 is 2's


def test_weight_falls_with_interactions_and_stays_positive(raw):
    weights = w.knows_weights(raw)
    assert set(weights) == {("1", "2"), ("2", "3"), ("1", "3")}
    assert weights[("1", "3")] == 1.0
    assert weights[("1", "2")] < weights[("2", "3")] < weights[("1", "3")]
    assert all(v > 0 for v in weights.values())


def test_weights_are_deterministic_and_rounded(raw):
    assert w.knows_weights(raw) == w.knows_weights(raw)
    assert all(v == round(v, 6) for v in w.knows_weights(raw).values())


def test_inline_replaces_parameters_with_escaped_literals():
    assert w.inline("CALL x($a, $b)", {"a": "Person:1", "b": "O'Brien"}) == "CALL x('Person:1', 'O\\'Brien')"
    assert w.inline("RETURN 1", {}) == "RETURN 1"
