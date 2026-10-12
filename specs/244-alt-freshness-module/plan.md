<!-- markdownlint-disable MD013 -->

# Implementation Plan: Freshness module for Rebuilt stores

**Branch**: `alt/freshness-module` | **Spec**: [spec.md](spec.md)

## Summary

One new module, `iris_vector_graph/freshness.py`, with a `Freshness(host)` class. It stores stamps at
`^IVG.BuiltFrom(store)` and reads the token at `^KG("__version")`, through the five host operations. Tests first: an
in-memory fake host in `tests/unit/test_freshness.py`, and a live test in `tests/e2e/test_freshness_live.py`.

## Technical Context

- **Language**: Python 3.10+ (repo `.venv` 3.11).
- **Dependencies**: none new. The module imports `Host` only under `TYPE_CHECKING`.
- **Storage**: `^KG("__version")` (read, written by existing write paths); `^IVG.BuiltFrom` (new, owned by this module).
- **Testing**: pytest unit (no IRIS); e2e against `host_for(conn)` run by the lead.
- **Constraint**: the branch base (main HEAD 6f17be0f) has no `iris_vector_graph/host.py`. It is on `alt/host-sites-1`
  (commit 88650355). The unit tests do not need it. The e2e test imports `host_for` and fails at collection until the
  host package is on this branch.

## Design

- `token()`: `is_defined` bit 1 check, then `get`; unset or empty reads as 0.
- `stamp(store)`: writes `self.token()` at call time. Callers stamp BEFORE the rebuild reads its source (see module
  docstring). A stamp taken after the build can claim writes the build did not include.
- `built_from(store)`: `is_defined` bit 1 check, then `get`; None when unstamped.
- `is_current(store)`: stamped and equal to the token.
- `stale_stores(stores)`: reads the token once, returns the non-current names in order.

## Project Structure

```text
iris_vector_graph/freshness.py          new
tests/unit/test_freshness.py            new (16 tests)
tests/e2e/test_freshness_live.py        new (4 tests, needs IRIS)
specs/244-alt-freshness-module/         spec, plan, tasks (gitignored; committed with git add -f)
```

## Gates

- Phase 1 gate: unit tests red before `freshness.py` exists (confirmed: collection error), green after.
- Phase 2 gate: e2e test passes on the enterprise container. Run by the lead.
