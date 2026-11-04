<!-- markdownlint-disable MD013 -->

# Driver drift: intersystems-irispython 5.4.0 and 5.4.1

The repo's `.venv` has intersystems-irispython 5.4.0. A fresh `pip install iris-vector-graph` gets 5.4.1. The two
versions decode a value stored in a VARCHAR column differently, so a green `.venv` run says nothing about what a user on
a fresh install sees. This page records what was measured, and how to run the gate that checks both drivers (4.6.0
R230).

## What differs

`rdf_props.val` is VARCHAR. A Cypher write stores a number there as an IRIS number (a `$DOUBLE`, an integer or a
decimal), and the API stores the number's text. Reading `val` straight through the driver gives:

| stored by                     | 5.4.0                     | 5.4.1                     |
| ----------------------------- | ------------------------- | ------------------------- |
| `CREATE (... {score: 0.95})`  | `0.95` (float)            | `'.94999999999999995559'` |
| `CREATE (... {count: 29})`    | `29` (int)                | `'29'`                    |
| `SET n.k = 2.5 * 2` (decimal) | `Decimal('5')`            | `'5'`                     |
| `create_node(..., {f: 0.95})` | `'0.95'` (the API's text) | `'0.95'`                  |

5.4.0 decodes the list item's own type; 5.4.1 converts it to the column's type. The figures above are from 2026-11-04,
on `irishealth:2026.3.0AI.113.0` (`ivg-iris-enterprise`): the red runs of
`tests/e2e/test_460_r264_embed_text_typed_e2e.py`, under each driver, before R264. fixes26 measured the same difference
on Community 2026.1 (`0.95`, `0.5` and `Decimal('5.0')` against `'.94999999999999995559'`, `'.5'` and `'5'`).

IVG reads these values typed in 4.6.0. The server marks a number's type (`CY_TYPED`, `CY_JSON_NUM`) and the engine
decodes the mark (`prop_values.decode_typed_text`), so the answer does not depend on the driver:

- R218: a node property in a `RETURN` column, `get_node` and `get_property_values`;
- R248: whole-node values;
- R264: the properties `embed_nodes` gives `text_fn`.

A re-embed under 5.4.1 of a graph that 4.5.x embedded can build a different text, and so a different vector, for a
Cypher-written number.

## The gate

```bash
IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 IVG_ARNO_PORT=31972 IVG_NAMESPACE=<your scratch namespace> \
  scripts/dev/driver_matrix.sh ~/.cache/ivg-bench/driver-matrix/run [--tck <TCK namespace>]
```

It runs each E2E file in `scripts/dev/driver_matrix_e2e.txt` (the files that read node and relationship values back)
under two interpreters, one file at a time. The first is the repo's `.venv` (`IVG_PY_REPO`). The second is a venv built
from `pyproject.toml` as a fresh install resolves it (`IVG_PY_FRESH`, by default
`~/.cache/ivg-bench/driver-matrix/venv-fresh`, made with uv when absent). With `--tck`, it also runs the whole TCK under
each interpreter and lists the scenarios that pass under one only. `summary.txt` has a line per file and interpreter,
and each interpreter's driver version. `tests/unit/test_460_r230_driver_matrix.py` checks the list.

## Measured

E2E, 2026-11-04, `IVG460FXZ` on `ivg-iris-enterprise`, branch 460-fixes36 (R264 and R265 in), one file at a time:

| file                                           | 5.4.0              | 5.4.1              |
| ---------------------------------------------- | ------------------ | ------------------ |
| `test_460_r218_typed_node_reads_e2e.py`        | 13 passed          | 13 passed          |
| `test_460_r248_typed_node_values_e2e.py`       | 24 passed          | 24 passed          |
| `test_460_r264_embed_text_typed_e2e.py`        | 1 passed           | 1 passed           |
| `test_460_r250_value_spellings_e2e.py`         | 16 passed          | 16 passed          |
| `test_460_r253_list_value_types_e2e.py`        | 22 passed          | 22 passed          |
| `test_460_r228_path_list_elements_e2e.py`      | 24 passed          | 24 passed          |
| `test_460_r209_tostring_float_values_e2e.py`   | 9 passed           | 9 passed           |
| `test_460_tostring_stored_number_e2e.py`       | 7 passed           | 7 passed           |
| `test_460_node_number_props_e2e.py`            | 7 passed           | 7 passed           |
| `test_460_r240_arith_keeps_number_type_e2e.py` | 29 passed          | 29 passed          |
| `test_460_rel_prop_fraction_e2e.py`            | 15 passed          | 15 passed          |
| `test_460_relationship_property_read_e2e.py`   | 6 passed           | 6 passed           |
| `test_460_r210_set_reads_relationship_e2e.py`  | 5 passed           | 5 passed           |
| `test_460_r242_set_reads_with_value_e2e.py`    | 1 failed, 9 passed | 8 failed, 2 passed |
| `test_460_properties_map_graph_e2e.py`         | 3 passed           | 3 passed           |

One file differs, and it fails the same way on the release tip (1d218b0a), before this branch:

- Under 5.4.1, a `SET` that takes a value from the `WITH` before it reads back as text. `WITH n.k AS k, n SET n.r = k`
  answers `'3'`, `'5'` for 3, 5; arithmetic and float columns do the same (`'6'`, `'4'`, `'2.5'`, `'2'`).
- Under both drivers, `... SET n.r = k RETURN n.name, n.r, k` answers no rows.

The gate exists to catch this kind of drift; it is not fixed here.

TCK, under each driver, by earlier agents (not re-run here):

- Before R248 (fixes26): 5.4.1 failed 138 scenarios that 5.4.0 passed. Whole-node values carried Cypher-written numbers
  as IRIS text.
- After it (fixes35, 2026-11-04 20:05 UTC, a Community container): 3896 of 3896 eligible scenarios under each driver.
  Logs: `~/.cache/ivg-bench/fixes35/tck_before540.log` and `tck_before541.log`.

## CI

`.github/workflows/ci.yml` installs with `pip install -e ".[dev,full]"`, which uses no lock file and pins no driver. On
2026-11-04 it resolved to 5.4.1, the same as a bare install (`uv pip compile pyproject.toml`, with and without
`--extra dev --extra full`). A step after the install now compares the installed driver with the one a dry run of the
bare package's install picks (`pip install --dry-run --report`), and fails if an extra pulls in a different one.

The unit job runs no E2E, so CI cannot see a decode difference. Only the gate above can, against a live container.
