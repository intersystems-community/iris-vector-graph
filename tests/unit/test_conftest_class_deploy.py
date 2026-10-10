"""tests/conftest.py recompiles only the ObjectScript classes whose source changed since the last deploy."""

from tests.conftest import classes_to_deploy


def test_everything_is_deployed_when_nothing_is_recorded():
    assert classes_to_deploy({"A.cls": "h1", "B/C.cls": "h2"}, {}) == ["A.cls", "B/C.cls"]


def test_an_unchanged_class_is_skipped():
    sources = {"A.cls": "h1", "B.cls": "h2"}
    assert classes_to_deploy(sources, {"A.cls": "h1", "B.cls": "h2"}) == []


def test_a_changed_or_new_class_is_deployed():
    sources = {"A.cls": "h1", "B.cls": "h2new", "C.cls": "h3"}
    assert classes_to_deploy(sources, {"A.cls": "h1", "B.cls": "h2old"}) == ["B.cls", "C.cls"]


def test_a_class_removed_from_the_source_is_not_deployed():
    assert classes_to_deploy({"A.cls": "h1"}, {"A.cls": "h1", "Gone.cls": "x"}) == []
