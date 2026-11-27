"""The shared embedding table's width gets restored between tests, not at session end.

`Graph_KG.kg_NodeEmbeddings` has one `emb VECTOR(DOUBLE, n)` declaration for the
whole namespace, and `initialize_schema()` re-declares it whenever the table is
empty. Plenty of live tests legitimately build an engine at 4, 8, 32 or 128 —
several exercise the width migration itself — so the drift is not a defect in the
test that causes it. Leaving it behind for the next test is.

Through 3.2.0 `tests/conftest.py` restored the width only at *session* teardown.
That kept the namespace clean between runs and did nothing for the run itself. The
T074 gate showed the in-run cost: four separate failures, all of them victims of
some earlier test's width —

    tests/e2e/test_subgraph_e2e.py:310  SQLCODE -104 … Field
        'Graph_KG.kg_NodeEmbeddings.emb' (value '3AA971…@$vector') failed validation
    tests/unit/test_vector_search_routing.py::test_native_tier_faster_than_ivf_on_live_db
        ValueError: query vector has 768 dimensions, but graph '' is routed to
        kg_NodeEmbeddings, which stores 128-dimensional vectors
    tests/e2e/test_230_callers_and_reports.py:335
        this test needs the default table declared at a width neither route uses;
        it is 8
    tests/e2e/test_230_embedding_width_isolation.py  (the guard, firing correctly)

— and none of those messages names the test that caused it, which is the second
half of the problem. The restore now runs per test and logs the polluter's nodeid.

The probe costs one `%Dictionary` read, measured at 6.6 ms p50 on
`ivg-iris-enterprise`, so it is only worth paying for a test wired to the live
session connection. That decision is what `touches_shared_namespace` makes.
"""

from tests.conftest import touches_shared_namespace


def test_a_test_on_the_live_session_connection_is_checked():
    assert touches_shared_namespace(["request", "iris_connection"])


def test_a_test_that_reaches_the_connection_through_another_fixture_is_checked():
    # pytest resolves the whole fixture closure into `request.fixturenames`, so a
    # test asking only for `iris_master_cleanup` still lists `iris_connection`.
    assert touches_shared_namespace(
        ["request", "iris_master_cleanup", "iris_connection", "iris_cursor"]
    )


def test_a_pure_unit_test_is_not_checked():
    # 13,000-odd tests never open a connection. Paying 6.6 ms each to prove they
    # did not narrow a table they never touched is 90 seconds of nothing.
    assert not touches_shared_namespace(["request", "tmp_path", "monkeypatch"])


def test_the_arno_connection_is_its_own_namespace_when_it_is_another_container():
    # `arno_iris_connection` on a container other than the session's has no
    # `Graph_KG.kg_NodeEmbeddings` in common with the session connection.
    assert not touches_shared_namespace(["request", "arno_iris_connection"], arno_shared=False)


def test_the_arno_connection_is_the_shared_namespace_when_it_is_the_session_container():
    # The usual setup (`IVG_TEST_CONTAINER` is `ivg-iris-enterprise`, and so is the Arno container) opens
    # `arno_iris_connection` in the run's namespace: a test that re-declares the table's width through it
    # (`initialize_schema(rewidth=True)`) leaves the next test, on `iris_connection`, at that width.
    assert touches_shared_namespace(["request", "arno_iris_connection"], arno_shared=True)


def test_arno_shares_the_session_namespace_only_on_the_same_container():
    from tests.conftest import arno_shares_session_namespace

    assert arno_shares_session_namespace({"IVG_TEST_CONTAINER": "ivg-iris-enterprise"}, "ivg-iris-enterprise")
    assert arno_shares_session_namespace({}, "ivg-iris-enterprise")  # the default session container
    assert not arno_shares_session_namespace({"IVG_TEST_CONTAINER": "ivg-iris"}, "ivg-iris-enterprise")
