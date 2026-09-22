import json

import pytest


def test_multi_relationship_types(execute_cypher):
    """A `|` alternation matches either predicate, and `r` answers a relationship value.

    This is the query whose `FETCH FIRST 5 ROWS ONLY` over five joined VARCHAR-keyed
    tables SIGSEGVs in `%qaqpre` on the IRIS AI builds. It only stopped crashing once
    `execute_cypher` started handing the engine to the translator, which is what selects
    `TOP 5` instead.
    """
    query = (
        "MATCH (t:Transaction)-[r:FROM_ACCOUNT|TO_ACCOUNT]->(a:Account) "
        "RETURN t.node_id, r LIMIT 5"
    )
    result = execute_cypher(query)

    assert len(result["rows"]) > 0
    rel_types = {json.loads(row[1])["type"] for row in result["rows"]}
    assert rel_types <= {"FROM_ACCOUNT", "TO_ACCOUNT"}
    assert rel_types
