"""4.6.0 R197: `scripts/test-container.sh` `compile` and `compile-all` refuse a namespace recorded under another IVG
Schema before they load anything, as `scripts/enterprise-container.sh` does (R185).

Both loaded the raw `iris_src/src` (which names `Graph_KG`) into USER through `iris session`, without reading
`^IVG.Deploy("schema")`. They now take the namespace from IVG_NAMESPACE (USER by default) and stage in
tests/conftest.py's directory (`/tmp/src`, `/tmp/src_<NS>`), as R185 made the enterprise script do, and run the same
check first: switch to the namespace, read the record, exit 3 naming it unless it is `Graph_KG`; the command then
exits 1 having loaded nothing. `up` works in USER. The live run is tests/e2e/test_460_r197_test_container_refusal_e2e.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "test-container.sh"
ENTERPRISE = ROOT / "scripts" / "enterprise-container.sh"


def _branch(name: str) -> str:
    text = SCRIPT.read_text()
    return text.split(f"\n  {name})", 1)[1].split("\n    ;;", 1)[0]


def _refusal(path: Path) -> str:
    return path.read_text().split('REFUSE_OTHER_SCHEMA_PY="', 1)[1].split('\n"\n', 1)[0]


def test_the_refusal_is_the_enterprise_script_s():
    assert _refusal(SCRIPT) == _refusal(ENTERPRISE)


@pytest.mark.parametrize("name, load", [("compile-all", "LoadDir("), ("compile", "OBJ.Load(")])
def test_a_loading_branch_refuses_before_it_loads(name, load):
    body = _branch(name)
    assert body.find("refuse_other_schema") != -1 and body.find("refuse_other_schema") < body.find(load), body
    assert 'iris session iris -U \\"$NS' in body, body


def test_the_check_runs_in_the_namespace_and_fails_the_command():
    text = SCRIPT.read_text()
    fn = text.split("refuse_other_schema() {", 1)[1].split("\n}\n", 1)[0]
    assert 'IVG_NS="$NS"' in fn and '"$REFUSE_OTHER_SCHEMA_PY"' in fn and "exit 1" in fn and "_rc=$?" in fn, fn


def test_up_works_in_user():
    body = _branch("up")
    assert 'IVG_NAMESPACE=USER "$0" deploy' in body and 'IVG_NAMESPACE=USER "$0" compile-all' in body, body
    assert 'IVG_NAMESPACE=USER "$0" compile "$_cls"' in body, body


@pytest.mark.parametrize("name", ["deploy", "compile-all", "compile"])
def test_the_staging_directory_is_conftests(name):
    assert "$STAGE" in _branch(name), _branch(name)
    text = SCRIPT.read_text()
    assert 'STAGE="/tmp/src"' in text and 'STAGE="/tmp/src_${NS}"' in text


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
