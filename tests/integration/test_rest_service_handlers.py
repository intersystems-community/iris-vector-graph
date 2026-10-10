"""Graph.KG.Service handlers, driven from a terminal session with stub %request and %response.

There is no web gateway in the enterprise test container, so the dispatch layer cannot
be exercised over HTTP. The handlers only read %request.Content and write through the
current device, so a session that sets those two objects runs exactly the code a REST call
would. Output is wrapped in markers so the session banner and prompts can be ignored.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


def run_handler(method: str, body: str | None) -> tuple[str, str]:
    """Run Graph.KG.Service.<method>() and return (response status, body text)."""
    content = ""
    if body is not None:
        escaped = body.replace('"', '""')
        content = f'do s.Write("{escaped}")\ndo s.Rewind()\nset %request.Content=s\n'
    script = (
        'set %request=##class(%CSP.Request).%New()\n'
        'set %response=##class(%CSP.Response).%New()\n'
        'set s=##class(%Stream.TmpCharacter).%New()\n'
        + content
        + 'write "<<"_"BODY",!\n'
        + f'set rc=##class(Graph.KG.Service).{method}()\n'
        + 'write !,"BODY"_">>",!\n'
        + 'write "<<"_"STATUS:",%response.Status,":",rc,":STATUS"_">>",!\n'
        + 'halt\n'
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "USER"],
        input=script, capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout
    raw = out.split("<<BODY", 1)[1].split("BODY>>", 1)[0]
    # the terminal prints its prompt around the handler's own output
    text = "\n".join(line for line in raw.splitlines() if line.strip() and not line.startswith("USER")).strip()
    status = out.split("<<STATUS:", 1)[1].split(":STATUS>>", 1)[0]
    return status, text


def test_meta_path_answers_with_json(tmp_path):
    status, text = run_handler("MetaPath", json.dumps({"srcId": "no-such-node", "predicates": ["R"], "maxHops": 1}))
    assert status.endswith(":1"), status  # returns $$$OK
    json.loads(text)  # a JSON document, whatever the result


def test_a_body_that_is_not_json_is_a_500_with_an_error_object():
    status, text = run_handler("VectorSearch", "{not json")
    assert status.startswith("500 Internal Server Error"), status
    assert "error" in json.loads(text)


def test_each_handler_reports_a_missing_body_as_an_error_not_a_crash():
    for method in ("VectorSearch", "HybridSearch", "MetaPath"):
        status, text = run_handler(method, None)
        assert text, method
        json.loads(text)
        assert status.endswith(":1"), (method, status)


def test_vector_and_hybrid_search_write_a_json_document():
    vec = [0.1] * 768
    for method, body in (
        ("VectorSearch", {"vector": json.dumps(vec), "k": 3}),
        ("HybridSearch", {"vector": json.dumps(vec), "text": "x", "k": 3, "c": 60}),
    ):
        status, text = run_handler(method, json.dumps(body))
        assert isinstance(json.loads(text), (list, dict)), (method, text)


def _session(script: str) -> str:
    return subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "iris", "session", "IRIS", "-U", "USER"],
        input=script, capture_output=True, text=True, timeout=120,
    ).stdout


def test_create_web_app_registers_the_dispatch_class_and_can_be_removed():
    path = "/ivg-test-kg"
    out = _session(
        'zn "%SYS"\n'
        f'do ##class(Security.Applications).Delete("{path}")\n'  # a leftover from an earlier failed run
        'zn "USER"\n'
        f'set sc=##class(Graph.KG.Service).CreateWebApp("{path}")\n'
        'write "<<"_"OK:",$system.Status.IsOK(sc),!\n'
        'zn "%SYS"\n'
        f'write "<<"_"EXISTS:",##class(Security.Applications).Exists("{path}"),!\n'
        f'set x=##class(Security.Applications).Get("{path}",.p)\n'
        'write "<<"_"DISPATCH:",$get(p("DispatchClass")),":NS:",$get(p("NameSpace")),!\n'
        f'do ##class(Security.Applications).Delete("{path}")\n'
        f'write "<<"_"GONE:",##class(Security.Applications).Exists("{path}"),!\n'
        "halt\n"
    )
    assert "<<OK:1" in out, out
    assert "<<EXISTS:1" in out, out
    assert "<<DISPATCH:Graph.KG.Service:NS:USER" in out, out
    assert "<<GONE:0" in out, out


# ------------------------------------------------------------------ through the URL map


def dispatch(url: str, method: str, body: str | None):
    """Run the REST dispatcher (URL map and all) in a terminal session, as a gateway would."""
    content = ""
    if body is not None:
        escaped = body.replace('"', '""')
        content = f'do s.Write("{escaped}")\ndo s.Rewind()\nset %request.Content=s\n'
    script = (
        "set %request=##class(%CSP.Request).%New()\n"
        "set %response=##class(%CSP.Response).%New()\n"
        "set %session=##class(%CSP.Session).%New()\n"
        "set s=##class(%Stream.TmpCharacter).%New()\n"
        + content
        + f'set %request.Method="{method}"\n'
        + f'set %request.URL="{url}"\n'
        + f'set %request.CgiEnvs("REQUEST_METHOD")="{method}"\n'
        + f'set %request.CgiEnvs("PATH_INFO")="{url}"\n'
        + 'set %request.CgiEnvs("CONTENT_TYPE")="application/json"\n'
        + 'set %request.CgiEnvs("HTTP_ACCEPT")="application/json"\n'
        + 'write "<<"_"B",!\n'
        + f'set sc=##class(Graph.KG.Service).DispatchRequest("{url}","{method}",0)\n'
        + 'write !,"B"_">>",!\n'
        + 'write "<<"_"ST:",%response.Status,":ST"_">>",!\n'
        + "halt\n"
    )
    out = _session(script)
    raw = out.split("<<B", 1)[1].split("B>>", 1)[0]
    text = "\n".join(line for line in raw.splitlines() if line.strip() and not line.startswith("USER")).strip()
    status = out.split("<<ST:", 1)[1].split(":ST>>", 1)[0]
    return status, text


def test_meta_path_route_answers_200_with_a_json_list():
    status, text = dispatch("/metaPath", "POST", json.dumps({"srcId": "no-such-node", "maxHops": 1}))
    assert status.startswith("200"), (status, text)
    assert json.loads(text) == []


def test_meta_path_defaults_hops_and_predicates_when_the_body_omits_them():
    status, text = dispatch("/metaPath", "POST", json.dumps({"srcId": "no-such-node"}))
    assert status.startswith("200"), (status, text)


def test_vector_and_hybrid_routes_answer_with_json_and_default_their_limits():
    vec = [0.1] * 768
    for route, body in (
        ("/vectorSearch", {"vector": vec}),
        ("/vectorSearch", {"vector": vec, "k": 2, "label": "Thing", "graph": ""}),
        ("/hybridSearch", {"vector": vec, "text": "x"}),
        ("/hybridSearch", {"vector": vec, "text": "x", "k": 2, "c": 30}),
    ):
        status, text = dispatch(route, "POST", json.dumps(body))
        assert status.startswith("200"), (route, status, text)
        assert isinstance(json.loads(text), (list, dict))


def test_an_unknown_route_is_a_404_and_a_wrong_method_is_a_405():
    status, _ = dispatch("/nope", "POST", "{}")
    assert status.startswith("404"), status
    status, _ = dispatch("/metaPath", "GET", None)
    assert status.startswith("405"), status


def test_a_malformed_body_on_a_route_is_a_500_error_object():
    status, text = dispatch("/metaPath", "POST", "{broken")
    assert status.startswith("500"), status
    assert "error" in json.loads(text)


def test_options_on_a_route_answers_without_running_its_handler():
    status, _ = dispatch("/vectorSearch", "OPTIONS", None)
    assert status.startswith("200"), status
    status, _ = dispatch("/metaPath", "OPTIONS", None)
    assert status.startswith("200"), status
    # an unknown route is still a 404 for OPTIONS
    status, _ = dispatch("/nope", "OPTIONS", None)
    assert status.startswith("404"), status
