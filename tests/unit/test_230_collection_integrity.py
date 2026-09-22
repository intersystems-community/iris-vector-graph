"""The command the project documents has to collect the tests it claims to run.

`CLAUDE.md` publishes `pytest` ("All tests") and `pytest tests/`, and
`docs/TESTING_POLICY.md` calls `pytest` "the project's standard test command."
Through 3.2.0 all three aborted before running anything:

    ModuleNotFoundError: No module named 'tck.test_steps_query'
    !!!!!!!!!!! Interrupted: 10 errors during collection !!!!!!!!!!!
    29 deselected, 5 warnings, 10 errors in 3.96s

`tests/tck/` and `tests/unit/tck/` each hold an `__init__.py`, while `tests/` and
`tests/unit/` did not. In prepend import mode both directories therefore claimed
the same top-level package name, `tck`; whichever imported first bound the name
and the other's submodules became unreachable. A working invocation existed
(`pytest tests/unit tests/e2e` collects one of the two and never the pair), which
is exactly why this went unnoticed — the gate ran, the documented command did not.

These tests are structural, not historical: any future package added under
`tests/` without `__init__.py` files above it reintroduces the same abort.
"""

import subprocess
import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TESTS_ROOT.parent


def test_every_test_package_has_an_unbroken_init_chain():
    # A package whose ancestors are not packages claims a top-level name. Two of
    # them claiming the same name is the abort above.
    broken = []
    for init in sorted(TESTS_ROOT.rglob("__init__.py")):
        ancestor = init.parent.parent
        while ancestor != REPO_ROOT:
            if not (ancestor / "__init__.py").exists():
                broken.append((init.parent.relative_to(REPO_ROOT), ancestor.relative_to(REPO_ROOT)))
                break
            ancestor = ancestor.parent

    assert not broken, (
        "these test packages sit under a directory that is not a package, so each "
        "claims its own basename as a top-level module name:\n"
        + "\n".join(f"  {pkg}  (not a package: {gap})" for pkg, gap in broken)
    )


def test_no_two_test_packages_claim_the_same_top_level_name():
    seen: dict = {}
    collisions = []
    for init in sorted(TESTS_ROOT.rglob("__init__.py")):
        name = init.parent.name
        if name in seen:
            collisions.append((name, seen[name], init.parent.relative_to(REPO_ROOT)))
        else:
            seen[name] = init.parent.relative_to(REPO_ROOT)

    # Duplicate basenames are fine once the chain above is unbroken — they become
    # tests.tck and tests.unit.tck. This test names them so a regression in the
    # chain has an obvious culprit rather than ten import errors.
    if collisions:
        for name, first, second in collisions:
            assert (first.parent / "__init__.py").exists() and (
                second.parent / "__init__.py"
            ).exists(), (
                f"'{name}' exists at both {first} and {second}, and at least one of "
                f"their parents is not a package — they will collide on import"
            )


def test_the_documented_bare_pytest_command_collects():
    # The documented command, run for real. Collection is the step that aborted.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        "`pytest` — the command CLAUDE.md and docs/TESTING_POLICY.md publish — does "
        f"not collect.\nexit={result.returncode}\n"
        + "\n".join(result.stdout.splitlines()[-25:])
        + "\n"
        + result.stderr[-2000:]
    )
