<!-- markdownlint-disable MD013 -->

# Implementation Plan: parity-py

**Branch**: `alt/parity-py` | **Spec**: [spec.md](spec.md)

## Summary

Find every Python site that reads or writes `^KG("deg")`/`^KG("degp")` and route it to `^KG("out")` or to
`Graph.KG.Adj.Degree`. The interface of the other package is fixed (BRIEF-PARITY.md) and is only called here.

## Sites (from `grep -rn "deg" iris_vector_graph`, tests, docs)

| Site                                                 | Change                                                                                   |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `stores/lazy_kg.py` `iter_nodes`                     | iterate `out`                                                                            |
| `stores/lazy_kg.py` `degree`, `degree_for_predicate` | `host.call("Graph.KG.Adj", "Degree", ...)`                                               |
| `stores/iris_sql_store.py` `_check_namespace`        | `$Data(^KG("out"))`                                                                      |
| `_engine/snapshot.py` `STORE_PLAN`, restore          | drop `deg`/`degp`; `gen`/`degc` derived; skip on import; clear for restored graphs       |
| `_engine/schema.py` `backfill_degp`                  | deprecated no-op                                                                         |
| `tests/support/derived_oracle.py`                    | stop verifying `deg`/`degp`; verify `degc`                                               |
| docs                                                 | USER_GUIDE, ADMIN_GUIDE, README                                                          |

No other Python writes `out`/`in`, so no other Python path owes a generation bump.

## Technical Context

Python 3.11 (project `.venv`); fakes in the style of `tests/unit/test_host.py` and `tests/unit/test_lazykg_graph_scope.py`.
No IRIS container is used.

## Constitution Check

Test first (tasks.md orders failing tests before code). No push. No attribution lines.
