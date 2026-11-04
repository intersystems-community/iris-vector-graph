"""4.6.0 R185: `scripts/enterprise-container.sh` `compile-all` and `compile` refuse a namespace recorded under another
IVG Schema before they load anything, as `tcp-deploy` does (R173).

Both ran `%SYSTEM.OBJ.LoadDir` / `Load` on the raw `iris_src/src` (which names `Graph_KG`) in USER through
irispython, without reading `^IVG.Deploy("schema")`, so a USER recorded as `RAG` would have got `Graph_KG`'s schema
classes beside its own. `tcp-deploy` does not replace them: `up` compiles through irispython on purpose (its comment:
the embedded process writes the store TCP callers see), so they stay, and refuse.

They now take the namespace from IVG_NAMESPACE (USER by default), as `tcp-deploy` does, so the refusal can be run
against a scratch namespace; the staging directory follows tests/conftest.py's (`/tmp/src` for USER,
`/tmp/src_<NS>` otherwise), and `deploy` copies into the same one. `deploy` loads nothing, so it has nothing to refuse.
The live run is tests/e2e/test_460_r185_container_script_refusal_e2e.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "enterprise-container.sh"


def _branch(name: str) -> str:
    text = SCRIPT.read_text()
    return text.split(f"\n  {name})", 1)[1].split("\n    ;;", 1)[0]


def _refusal() -> str:
    text = SCRIPT.read_text()
    return text.split('REFUSE_OTHER_SCHEMA_PY="', 1)[1].split('\n"\n', 1)[0]


def test_the_refusal_switches_namespace_then_reads_the_record_and_stops():
    code = _refusal()
    assert code.find("SetNamespace(ns)") < code.find("'^IVG.Deploy'") < code.find("sys.exit(3)"), code
    assert "'GRAPH_KG'" in code and "os.environ['IVG_NS']" in code, code


@pytest.mark.parametrize("name, load", [("compile-all", "LoadDir("), ("compile", ".Load(")])
def test_a_loading_branch_runs_the_refusal_first_in_the_loading_process(name, load):
    body = _branch(name)
    # one irispython -c: the refusal's code, then the load
    assert body.find('-c "$REFUSE_OTHER_SCHEMA_PY') != -1, body
    assert body.find('-c "$REFUSE_OTHER_SCHEMA_PY') < body.find(load), body
    assert 'IVG_NS="$NS"' in body, body


@pytest.mark.parametrize("name", ["compile-all", "compile"])
def test_a_refused_load_fails_the_command(name):
    assert "_rc=$?" in _branch(name) and "exit 1" in _branch(name)


def test_up_works_in_user():
    body = _branch("up")
    assert body.count('IVG_NAMESPACE=USER "$0" compile-all') == 2 and body.count('IVG_NAMESPACE=USER "$0" deploy') == 2


def test_deploy_loads_nothing():
    body = _branch("deploy")
    assert "Load" not in body and "irispython" not in body and "iris session" not in body, body


@pytest.mark.parametrize("name", ["deploy", "compile-all", "compile"])
def test_the_staging_directory_is_conftests(name):
    assert "$STAGE" in _branch(name), _branch(name)
    text = SCRIPT.read_text()
    assert 'STAGE="/tmp/src"' in text and 'STAGE="/tmp/src_${NS}"' in text


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
