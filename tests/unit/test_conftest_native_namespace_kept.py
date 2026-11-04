"""R234: a test that leaves the session's Native API process in another namespace is named, and the process moved back.

The session shares one Native API connection (tests/conftest.py, ReconnectingNative). One Arno call that left it in
USER (BetweennessNeighborhood's fast path) made every later native read in an IVGFIX run answer from USER: hundreds
of empty traversals, subgraphs and centralities, none of them in the test that caused it. After each test the
process's namespace is checked; a moved one is put back and that test errors at teardown.
"""

from tests.conftest import put_native_namespace_back


class FakeNative:
    def __init__(self, namespace):
        self.namespace = namespace
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, *args))
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return self.namespace
        if (cls, method) == ("%SYSTEM.Process", "SetNamespace"):
            self.namespace = args[0]
            return self.namespace
        raise AssertionError(f"unexpected call {cls}.{method}")


def test_a_process_in_the_expected_namespace_is_left_alone():
    native = FakeNative("IVGFIX")
    assert put_native_namespace_back(native, "IVGFIX") is None
    assert native.calls == [("%SYSTEM.SYS", "NameSpace")]


def test_the_namespace_is_compared_without_case():
    assert put_native_namespace_back(FakeNative("IVGFIX"), "ivgfix") is None


def test_a_moved_process_is_put_back_and_the_namespace_it_was_in_is_returned():
    native = FakeNative("USER")
    assert put_native_namespace_back(native, "IVGFIX") == "USER"
    assert native.namespace == "IVGFIX"
    assert ("%SYSTEM.Process", "SetNamespace", "IVGFIX") in native.calls


def test_a_process_that_cannot_be_put_back_says_so():
    class Stuck(FakeNative):
        def classMethodValue(self, cls, method, *args):
            if (cls, method) == ("%SYSTEM.Process", "SetNamespace"):
                return "USER"
            return super().classMethodValue(cls, method, *args)

    import pytest

    with pytest.raises(RuntimeError, match="still in USER"):
        put_native_namespace_back(Stuck("USER"), "IVGFIX")
