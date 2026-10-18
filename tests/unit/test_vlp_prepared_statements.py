"""CY_VLP_PATHS / CY_VLP_ENDS prepare their two adjacency statements once per call.

The trail walk asked for each new node's edges with `%SQL.Statement.%ExecDirect`, which
prepares the statement every time: 1,430 prepares for one 1..3-hop KNOWS walk at SNB SF0.1.
Same ends for 240 (source, range, direction) cases; 20% less time.
"""

import pytest

from iris_vector_graph.schema import GraphSchema


def _ddl(name):
    ddl = [s for s in GraphSchema.get_procedures_sql_list() if f"FUNCTION SQLUser.{name}(" in s]
    assert len(ddl) == 1
    return ddl[0]


@pytest.mark.parametrize("name", ["CY_VLP_PATHS", "CY_VLP_ENDS"])
def test_no_prepare_per_node(name):
    ddl = _ddl(name)
    assert "%ExecDirect" not in ddl
    assert ddl.count("%Prepare(sql(dd))") == 1
    assert "stmt(dd).%Execute(args...)" in ddl


@pytest.mark.parametrize("name", ["CY_VLP_PATHS", "CY_VLP_ENDS"])
def test_the_body_survives_the_dbapi_ddl_tokenizer(name):
    body = _ddl(name).split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert body.count("'") % 2 == 0
    assert "?" not in body
