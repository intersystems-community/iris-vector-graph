"""Spec 262 T049: the schema record and the package-clash refusal against a live namespace, with direct SQL and the
Native API only (no engine): what `^IVG.Deploy("schema")` holds after `write_record`, that names compare without
case, and that a schema whose class package holds someone else's class is refused before any DDL.

Runs in `IVG_NS_IVG262REC` (default IVG262REC; created by `scripts/dev/make_scratch_namespace.py` when absent). The
record the namespace held before is put back afterwards. The never-set-up case needs a namespace setup never touched,
which that one is not once test_function_placement (which shares the variable) has installed into it: it makes an
empty namespace of its own, named per run (`IVG470NS` and a random suffix), and drops it after.
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.integration, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG262REC", "IVG262REC")


@pytest.fixture(scope="module")
def live():
    import make_scratch_namespace

    make_scratch_namespace.make(NS)  # no-op when it exists
    conn = ns262.connect(NS)
    # A connection of its own for the Native API: createIRIS on one that runs DDL corrupts its binds (engine._iris_obj)
    nconn = ns262.connect(NS)
    native = ns262.native(nconn)
    before = (native.get("^IVG.Deploy", "schema"), native.get("^IVG.Deploy", "schema", "recorded"))
    yield conn, native
    native.kill("^IVG.Deploy", "schema")
    if before[0] is not None:
        native.set(before[0], "^IVG.Deploy", "schema")
    if before[1] is not None:
        native.set(before[1], "^IVG.Deploy", "schema", "recorded")
    nconn.close()
    conn.close()


def test_a_written_record_reads_back_and_carries_when_and_which_version(live):
    from iris_vector_graph.schema_name import read_record, write_record

    conn, native = live
    write_record(native, "ivgalt", "4.6.0-test")
    assert native.get("^IVG.Deploy", "schema") == "ivgalt"
    assert read_record(native) == "ivgalt"
    stamp = native.get("^IVG.Deploy", "schema", "recorded")
    assert stamp.endswith("UTC 4.6.0-test") and stamp[:4].isdigit()


def test_the_recorded_name_compares_without_case(live):
    from iris_vector_graph.schema_name import IVGSchemaError, check_first_access, write_record

    conn, native = live
    write_record(native, "ivgalt", "4.6.0-test")
    check_first_access(native, "IVGALT")  # one schema: no raise
    with pytest.raises(IVGSchemaError) as raised:
        check_first_access(native, "IVGALT2")
    assert raised.value.recorded == "ivgalt" and NS in str(raised.value)


@pytest.fixture
def untouched():
    """An empty namespace of this run's own (make_scratch_namespace's make, no IVG install), dropped after."""
    import make_scratch_namespace

    ns = "IVG470NS" + uuid.uuid4().hex[:6].upper()
    assert make_scratch_namespace.make(ns), f"{ns} existed already"
    conn = ns262.connect(ns)
    nconn = ns262.connect(ns)
    try:
        yield ns, conn, ns262.native(nconn)
    finally:
        nconn.close()
        conn.close()
        make_scratch_namespace.drop(ns)


def test_no_record_and_no_46_objects_is_refused_with_the_never_set_up_error(untouched):
    """4.6.0 R201: a namespace holding none of IVG's objects says it was never set up; the not-upgraded message is
    only for one where an earlier release's objects are (this test asserted that one before R201)."""
    from iris_vector_graph.schema_name import IVGSchemaError, check_first_access

    ns, conn, native = untouched
    assert native.get("^IVG.Deploy", "schema") is None
    held = ns262.rows(conn, "SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name %STARTSWITH 'Graph.KG.'")
    assert held[0][0] == 0, f"{ns} holds IVG's classes: this case needs a namespace setup never touched"
    assert ns262.rows(conn, "SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = 'IVGNONE.funcCYCMP'"
                      )[0][0] == 0
    with pytest.raises(IVGSchemaError) as raised:
        check_first_access(native, "IVGNONE")
    assert f"Namespace {ns} has never been set up for IVG" in str(raised.value)


def test_no_record_with_the_46_objects_is_accepted(live):
    """The probe is the class `CREATE FUNCTION <schema>.CY_CMP` generates."""
    from iris_vector_graph.schema_name import check_first_access

    conn, native = live
    native.kill("^IVG.Deploy", "schema")
    cur = conn.cursor()
    cur.execute("CREATE FUNCTION IVGMARK.CY_CMP(a VARCHAR(10)) RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit 1 }")
    try:
        assert ns262.rows(conn, "SELECT COUNT(*) FROM %Dictionary.ClassDefinition WHERE Name = 'IVGMARK.funcCYCMP'"
                          )[0][0] == 1
        check_first_access(native, "IVGMARK")
        check_first_access(native, "ivgmark")
    finally:
        cur.execute("DROP FUNCTION IVGMARK.CY_CMP")
        cur.close()


def test_a_package_holding_someone_elses_class_is_refused_and_ivgs_own_are_not(live):
    from iris_vector_graph.schema_name import IVGSchemaError, foreign_classes, refuse_package_clash

    conn, _ = live
    cur = conn.cursor()
    cur.execute("CREATE TABLE IVGCLASH.foreign_t (x INTEGER)")
    cur.execute("CREATE TABLE IVGOWN.nodes (node_id VARCHAR(10))")
    try:
        assert foreign_classes(conn, "IVGCLASH") == ["IVGCLASH.foreignt"]
        with pytest.raises(IVGSchemaError) as raised:
            refuse_package_clash(conn, "ivgclash")
        assert "IVGCLASH.foreignt" in str(raised.value) and "Nothing was created" in str(raised.value)
        refuse_package_clash(conn, "IVGOWN")  # only an IVG table class: IVG's own
        refuse_package_clash(conn, "IVGEMPTY")  # nothing there
    finally:
        cur.execute("DROP TABLE IVGCLASH.foreign_t")
        cur.execute("DROP TABLE IVGOWN.nodes")
        cur.close()
