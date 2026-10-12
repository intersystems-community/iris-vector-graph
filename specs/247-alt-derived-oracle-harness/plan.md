<!-- markdownlint-disable MD013 -->

# Implementation Plan: Derived-store oracle

**Branch**: `alt/derived-oracle-harness` | **Spec**: [spec.md](spec.md)

## Summary

A pure-Python oracle, `tests/support/derived_oracle.py`, that walks `^KG` through an injected host and reads
`Graph_KG.rdf_edges` through an injected connection, and reports typed violations in both directions. Tests only; no
change under `iris_vector_graph/` or `iris_src/`.

## Technical Context

- **Language**: Python 3.11 (project `.venv`), pytest.
- **Host interface**: `get(name, *subs)`, `next_subscript(name, *subs, reverse=False)` (`iris_vector_graph/host.py`).
- **Graph key**: `Graph.KG.GraphKey.ForIndex` maps the default graph to `0`; the oracle mirrors that in Python.
- **Random sequence**: hypothesis is NOT in the dev extra, so the e2e property test uses a seeded `random.Random`.
- **Testing**: unit tests run with the `.venv` (see BRIEF). The e2e test is `@pytest.mark.e2e` and needs IRIS; the lead runs it.

## Constitution Check

- Test-first: unit tests are the gate for the oracle. See the deviation note in tasks.md (the module was drafted before its tests).
- No IRIS container used by this package. No push. No attribution.

## Project Structure

```text
tests/support/__init__.py          package marker
tests/support/derived_oracle.py    DerivedOracle, Violation, Walk, graph_key, KINDS
tests/unit/test_derived_oracle.py  fake host and fake connection; one test per kind
tests/e2e/test_derived_oracle_property.py  seeded random ops through the engine (IRIS)
```
