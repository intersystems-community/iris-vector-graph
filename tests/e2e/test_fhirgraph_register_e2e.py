"""FHIRGraph.Register: input validation and idempotent registration against the live repository.

Every refusal is an error object naming the cause; a valid call registers (or re-registers) the
repository's graph and answers the same description each time. Runs in IVGFHIR.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.fhir_conftest import ENDPOINT, GRAPH

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def register(fhir_conn):
    import iris

    obj = iris.createIRIS(fhir_conn)

    def call(*args):
        return json.loads(str(obj.classMethodValue("Graph.KG.FHIRGraph", "Register", *args)))

    return call


def test_an_interval_below_the_task_manager_granularity_is_refused(register):
    out = register(ENDPOINT, "[]", 30, "")
    assert out["status"] == "error" and "at least 60 seconds" in out["error"]


def test_an_endpoint_the_namespace_does_not_serve_is_refused(register):
    out = register("/csp/healthshare/nope/fhir/r4", "[]", 60, "")
    assert out["status"] == "error" and "no FHIR endpoint" in out["error"]


def test_json_links_must_be_an_array_of_strings(register):
    out = register(ENDPOINT, "[]", 60, "{not json")
    assert out == {"status": "error", "error": "json links must be a JSON array of strings"}


def test_an_unparseable_denylist_is_an_error_not_a_crash(register):
    assert register(ENDPOINT, "{bad", 60, "")["status"] == "error"


@pytest.mark.parametrize("endpoint", [ENDPOINT, ENDPOINT + "/", ""])
def test_registration_is_idempotent_and_tolerates_a_trailing_slash_or_no_endpoint(register, endpoint):
    out = register(endpoint, "[]", 60, "")
    assert out["status"] == "registered"
    assert out["graph_id"] == GRAPH and out["endpoints"] == [ENDPOINT]
    assert out["interval_s"] == 60 and out["denylist"] == [] and out["created"] is False
    assert out["json_links"], "the default json links are reported"


@pytest.mark.parametrize(
    "denylist,fragment",
    [
        ('{"a": 1}', "must be a JSON array"),
        ('["NoDot"]', "is not \"Type.param\""),
        ('["A.B.C"]', "is not \"Type.param\""),
        ('[".param"]', "is not \"Type.param\""),
        ('["Type."]', "is not \"Type.param\""),
        ("[1]", "is not \"Type.param\""),
    ],
)
def test_a_malformed_denylist_is_refused_with_the_entry_named(register, denylist, fragment):
    out = register(ENDPOINT, denylist, 60, "")
    assert out["status"] == "error" and fragment in out["error"], out


def test_indexed_token_params_refuses_something_that_is_not_an_array(fhir_conn):
    import iris

    raw = str(iris.createIRIS(fhir_conn).classMethodValue("Graph.KG.FHIRGraph", "IndexedTokenParams", GRAPH, '{"a": 1}'))
    assert json.loads(raw) == {"status": "error", "error": "params must be a JSON array"}


def test_schedule_refuses_an_interval_below_the_task_manager_granularity(fhir_conn):
    import iris

    raw = str(iris.createIRIS(fhir_conn).classMethodValue("Graph.KG.FHIRGraph", "Schedule", GRAPH, 30))
    out = json.loads(raw)
    assert out["status"] == "error" and "at least 60 seconds" in out["error"], out
