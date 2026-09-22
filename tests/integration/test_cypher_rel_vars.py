import json

import pytest


def test_relationship_variable_return(execute_cypher):
    """Returning a relationship variable answers the whole relationship, not just its type.

    The assertion used to read `== "FROM_ACCOUNT"` under a comment saying "the predicate
    name for now". `for now` has passed: the column is a relationship value carrying its
    type and its properties, which is what the Bolt encoder needs to tag the column as a
    relationship (`TranslationContext.rel_variables`). These two tests never noticed,
    because `FETCH FIRST` over their five-table JOIN crashed the process before the
    assertion ran.
    """
    query = "MATCH (t:Transaction)-[r:FROM_ACCOUNT]->(a:Account) RETURN r LIMIT 1"
    result = execute_cypher(query)

    assert len(result["rows"]) > 0
    assert result["columns"] == ["r"]
    rel = json.loads(result["rows"][0][0])
    assert rel["type"] == "FROM_ACCOUNT"
    assert isinstance(rel["props"], dict)


def test_multiple_nodes_and_rel_vars(execute_cypher):
    """A relationship variable keeps its shape alongside scalar projections."""
    query = (
        "MATCH (t:Transaction)-[r:TO_ACCOUNT]->(a:Account) "
        "RETURN t.amount, r, a.node_id LIMIT 1"
    )
    result = execute_cypher(query)

    assert len(result["rows"]) > 0
    amount, rel, node_id = result["rows"][0]
    assert json.loads(rel)["type"] == "TO_ACCOUNT"
    assert node_id
