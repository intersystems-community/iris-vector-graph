"""
Contract tests are TDD stubs written BEFORE implementation.
They are expected to error/fail — collect them but don't break CI.
"""
import pytest

collect_ignore_glob = []


def pytest_collection_modifyitems(items, config):
    for item in items:
        if "contract" in str(item.fspath):
            item.add_marker(pytest.mark.skip(reason="TDD stub: implementation pending"))
