# Doc-test baseline, 4.6.0 (R042, R043)

Recorded 2026-11-03 on `460-doctest` (branched from `release-4.6.0` at `53e4f3c7`), before workstream C rewrites
the docs. No doc carries a doc-test marker yet, so every block was run or classified as written. Nothing in the
docs was changed to make this run. Reproduce:

```bash
IVG_DOCTEST_JSON=~/.cache/ivg-bench/doctest/baseline.json pytest tests/docs/test_docs_run.py -s
python -m tests.docs.harness render ~/.cache/ivg-bench/doctest/baseline.json
pytest tests/docs/test_docs_consistent.py -rx
```

## Layer 1: the docs' code runs (R042)

13 documents from `tests/docs/docs.toml`, run against `ivg-iris-enterprise` namespace `IVG460DOC`,
emptied before each document. Blocks found 207; ran OK 59; bash syntax-checked only 36; failed 73; unclassifiable 16; data or output, not run 23; skipped 0.
Documents passing: 1 of 13 (docs/demos/BIOMEDICAL_DEMO.md). Answers the docs state in
a form the harness compares (`# →` lines and output blocks): 3, so "ran OK" today means only
that the code raised nothing, not that it gave the right answer.

The 89 failing blocks (73 failed, 16 unclassifiable) are the lines of `tests/docs/run_known_failures.txt`
(`doc | block index | first error line`, normalized). The R042 test ratchets on it as R043 does on its list: all
listed is XFAIL, a new failure fails, a listed block that stops failing that way fails until its line is deleted.
`RELEASE_GATE=1 pytest tests/unit/test_ci_parity_gates.py` requires both lists empty.

| Doc | Blocks | Ran OK | Bash checked | Failed | Unclassifiable | Skipped | Data/output | First error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `README.md` | 11 | 2 | 4 | 4 | 0 | 0 | 1 | `line 185: BadSyntax: at line 1 of <>:` |
| `docs/setup/QUICKSTART.md` | 12 | 5 | 4 | 0 | 3 | 0 | 0 | `line 104: a fenced block with no language and no terminal prompt: give it a language (python, sql, objectscript, bash, t` |
| `docs/setup/DATA_FORMATS.md` | 9 | 0 | 0 | 8 | 0 | 0 | 1 | `line 17: NameError: name 'engine' is not defined` |
| `docs/USER_GUIDE.md` | 62 | 35 | 1 | 21 | 5 | 0 | 0 | `line 86: AttributeError: 'ellipsis' object has no attribute 'get'` |
| `docs/ADMIN_GUIDE.md` | 18 | 11 | 5 | 1 | 0 | 0 | 1 | `line 351: ValueError: predicate(s) ['EMITTED_AT', 'METRIC_AT'] are still structural: register them temporal-only first (` |
| `docs/ADMIN_API.md` | 29 | 0 | 16 | 1 | 1 | 0 | 11 | `line 79: a fenced block with no language and no terminal prompt: give it a language (python, sql, objectscript, bash, te` |
| `docs/FHIR_GRAPH.md` | 5 | 0 | 1 | 2 | 0 | 0 | 2 | `line 17: NameError: name 'IRISGraphEngine' is not defined` |
| `docs/SEMANTIC_LAYER.md` | 26 | 1 | 1 | 23 | 0 | 0 | 1 | `line 33: NameError: name 'conn' is not defined` |
| `docs/COMMUNITIES.md` | 5 | 0 | 0 | 4 | 1 | 0 | 0 | `line 33: NameError: name 'engine' is not defined` |
| `docs/demos/BIOMEDICAL_DEMO.md` | 4 | 0 | 2 | 0 | 0 | 0 | 2 |  |
| `docs/demos/FRAUD_DEMO.md` | 8 | 0 | 1 | 0 | 5 | 0 | 2 | `line 52: the harness does not run 'cypher' blocks` |
| `docs/demos/PER_GRAPH_EMBEDDINGS_DEMO.md` | 10 | 0 | 1 | 7 | 0 | 0 | 2 | `line 35: NameError: name 'conn' is not defined` |
| `docs/demos/VERSIONED_GRAPH_DEMO.md` | 8 | 5 | 0 | 2 | 1 | 0 | 0 | `line 50: FileNotFoundError: [Errno 2] No such file or directory: 'staging.ndjson'` |

### Every failure, by document

`README.md`

- line 175 (python): `line 185: BadSyntax: at line 1 of <>:`
- line 208 (python): `line 219: FileNotFoundError: [Errno 2] No such file or directory: 'export.ndjson'`
- line 250 (python): `line 251: FHIRGraphError: Register: ERROR #5001: namespace IVG460DOC has no FHIR repository (HS_FHIRServer.Repo not found)`
- line 323 (python): `python syntax error at line 327: positional argument follows keyword argument`

`docs/setup/QUICKSTART.md`

- line 104 (unclassifiable): `unclassifiable: a fenced block with no language and no terminal prompt`
- line 120 (unclassifiable): `unclassifiable: a fenced block with no language and no terminal prompt`
- line 136 (unclassifiable): `unclassifiable: a fenced block with no language and no terminal prompt`

`docs/setup/DATA_FORMATS.md`

- line 15 (python): `line 17: NameError: name 'engine' is not defined`
- line 31 (python): `line 33: NameError: name 'engine' is not defined`
- line 62 (python): `line 64: NameError: name 'engine' is not defined`
- line 75 (python): `line 76: NameError: name 'engine' is not defined`
- line 84 (python): `line 85: NameError: name 'engine' is not defined`
- line 92 (python): `line 93: NameError: name 'engine' is not defined`
- line 114 (python): `line 116: NameError: name 'engine' is not defined`
- line 135 (python): `line 138: NameError: name 'engine' is not defined`

`docs/USER_GUIDE.md`

- line 84 (python): `line 86: AttributeError: 'ellipsis' object has no attribute 'get'`
- line 182 (python): `line 197: ValueError: invalid literal for int() with base 10: "<MagicMock name='mock.PurgeRawBefore()' id='4515639504'>"`
- line 215 (python): `line 226: FileNotFoundError: [Errno 2] No such file or directory: 'export.ndjson'`
- line 278 (python): `line 279: RuntimeError: GraphMode.Apply('') failed: <MagicMock name='mock.Apply()' id='4513569680'>`
- line 323 (python): `line 325: TemporalWindowError: Graph.KG.TemporalWindow.Open failed: Expecting value: line 1 column 1 (char 0)`
- line 348 (python): `line 349: ValueError: invalid literal for int() with base 10: "<MagicMock name='mock.PurgeRawBefore()' id='4514415440'>"`
- line 378 (python): `line 380: CypherParseError: Cypher error at line 1, col 40: Expected IDENTIFIER, got *`
- line 387 (python): `line 389: TemporalWindowError: temporal window read failed: Expecting value: line 1 column 1 (char 0). QueryWindow answers in one string of at most 3,641,144 characters; iter_edges_in_window reads any`
- line 401 (python): `line 413: JSONDecodeError: Expecting value: line 1 column 1 (char 0)`
- line 427 (python): `line 428: NameError: name 'ts_start' is not defined`
- line 522 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 559 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 601 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 623 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 672 (python): `line 673: AttributeError: 'IRISGraphEngine' object has no attribute 'k_core_decomposition'`
- line 731 (python): `line 738: NameError: name 'my_vector' is not defined`
- line 771 (python): `line 776: NameError: name 'my_vector' is not defined`
- line 786 (python): `line 788: NameError: name 'my_vector' is not defined`
- line 801 (python): `line 802: NameError: name 'vec_768' is not defined`
- line 830 (python): `line 832: TypeError: VectorMixin.bm25_build() got an unexpected keyword argument 'props'`
- line 854 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 876 (python): `line 886: BadSyntax: at line 1 of <>:`
- line 974 (python): `line 988: NameError: name 'cs_without_stub' is not defined`
- line 1020 (python): `line 1049: TypeError: 'int' object is not subscriptable`
- line 1173 (python): `line 1177: LedgerStrictModeError: ledger strict mode: structural write 'erase_graph' rejected; commit a changeset via engine.ledger`
- line 1207 (python): `line 1208: FHIRGraphError: Register: ERROR #5001: namespace IVG460DOC has no FHIR repository (HS_FHIRServer.Repo not found)`

`docs/ADMIN_GUIDE.md`

- line 349 (python): `line 351: ValueError: predicate(s) ['EMITTED_AT', 'METRIC_AT'] are still structural: register them temporal-only first (set_temporal_structural(predicate, False))`

`docs/ADMIN_API.md`

- line 79 (unclassifiable): `unclassifiable: a fenced block with no language and no terminal prompt`
- line 286 (python): `line 290: ConnectError: [Errno 61] Connection refused`

`docs/FHIR_GRAPH.md`

- line 16 (python): `line 17: NameError: name 'IRISGraphEngine' is not defined`
- line 34 (python): `line 37: NameError: name 'engine' is not defined`

`docs/SEMANTIC_LAYER.md`

- line 30 (python): `line 33: NameError: name 'conn' is not defined`
- line 61 (python): `line 63: NameError: name 'engine' is not defined`
- line 76 (python): `line 78: NameError: name 'engine' is not defined`
- line 99 (python): `line 100: NameError: name 'engine' is not defined`
- line 111 (python): `line 113: NameError: name 'engine' is not defined`
- line 135 (python): `line 136: NameError: name 'engine' is not defined`
- line 147 (python): `line 148: NameError: name 'engine' is not defined`
- line 163 (python): `line 164: NameError: name 'engine' is not defined`
- line 184 (python): `line 186: NameError: name 'engine' is not defined`
- line 213 (python): `line 215: NameError: name 'engine' is not defined`
- line 224 (python): `line 228: NameError: name 'report' is not defined`
- line 333 (python): `line 335: NameError: name 'engine' is not defined`
- line 345 (python): `line 350: NameError: name 'engine' is not defined`
- line 364 (python): `line 365: NameError: name 'engine' is not defined`
- line 373 (python): `line 375: NameError: name 'engine' is not defined`
- line 391 (python): `line 397: FileNotFoundError: [Errno 2] No such file or directory: '/Users/tdyar/.cache/ivg-bench/doctest/20261103-142123/provenance.ttl'`
- line 414 (python): `line 416: NameError: name 'engine' is not defined`
- line 432 (python): `line 434: NameError: name 'engine' is not defined`
- line 443 (python): `line 445: NameError: name 'engine' is not defined`
- line 462 (python): `line 465: NameError: name 'engine' is not defined`
- line 478 (python): `line 479: NameError: name 'engine' is not defined`
- line 485 (python): `line 487: NameError: name 'engine' is not defined`
- line 496 (python): `line 500: NameError: name 'engine' is not defined`

`docs/COMMUNITIES.md`

- line 32 (python): `line 33: NameError: name 'engine' is not defined`
- line 71 (python): `line 72: NameError: name 'engine' is not defined`
- line 85 (python): `line 86: NameError: name 'engine' is not defined`
- line 104 (python): `line 105: NameError: name 'engine' is not defined`
- line 118 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`

`docs/demos/FRAUD_DEMO.md`

- line 52 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 60 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 69 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 77 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`
- line 88 (unclassifiable): `unclassifiable: the harness does not run 'cypher' blocks`

`docs/demos/PER_GRAPH_EMBEDDINGS_DEMO.md`

- line 28 (python): `line 35: NameError: name 'conn' is not defined`
- line 44 (python): `line 46: NameError: name 'engine' is not defined`
- line 55 (python): `line 56: NameError: name 'engine' is not defined`
- line 76 (python): `line 77: NameError: name 'engine' is not defined`
- line 90 (python): `line 91: NameError: name 'engine' is not defined`
- line 100 (python): `line 101: NameError: name 'engine' is not defined`
- line 108 (python): `line 109: NameError: name 'engine' is not defined`

`docs/demos/VERSIONED_GRAPH_DEMO.md`

- line 28 (python): `line 50: FileNotFoundError: [Errno 2] No such file or directory: 'staging.ndjson'`
- line 132 (unclassifiable): `unclassifiable: a fenced block with no language and no terminal prompt`
- line 142 (python): `line 145: TypeError: 'HistoryPage' object is not iterable`

### What the failures are

- **No setup in the document** (`NameError: engine`/`conn`): DATA_FORMATS, SEMANTIC_LAYER, COMMUNITIES,
  FHIR_GRAPH, PER_GRAPH_EMBEDDINGS_DEMO assume an `engine` the reader made elsewhere. One failing setup block
  fails every block after it.
- **Placeholders that are not code**: `...`, `my_vector`, `vec_768`, `ts_start`, files the doc never writes
  (`export.ndjson`, `staging.ndjson`).
- **Wrong API**: `engine.k_core_decomposition`, `bm25_build(props=…)`, a positional argument after a keyword
  (README line 327), a Cypher `*` the parser rejects (USER_GUIDE line 380), `HistoryPage` iterated.
- **Needs something the namespace does not have**: a FHIR repository (README 250, USER_GUIDE 1207), the IVG
  API server on 8200 (ADMIN_API 286), ledger strict mode left on by an earlier block (USER_GUIDE 1173).
- **Cascades from USER_GUIDE line 22** ("Inside IRIS (Embedded Python)"): outside IRIS,
  `IRISGraphEngine(EmbeddedConnection())` builds an engine whose native calls return `MagicMock`s and
  `initialize_schema()` raises nothing. It replaces the real `engine` for the rest of the document, so the
  later `PurgeRawBefore`, `GraphMode.Apply` and `TemporalWindow` failures are that mock. The block needs a
  skip marker; the silent mock is an IVG defect reported separately.
- **Unclassifiable**: `cypher` blocks (FRAUD_DEMO, USER_GUIDE, COMMUNITIES) and unlabelled fences (QUICKSTART,
  ADMIN_API, VERSIONED_GRAPH_DEMO). Give them a language, put the query in a python block, or skip with a reason.

## Layer 2: the docs agree with the code (R043)

88 documents checked (README.md and `docs/**/*.md` that git ships; there is no CONTRIBUTING.md yet);
42 have findings, 95 findings in all. These are the lines of
`tests/docs/consistency_known_failures.txt`: the test XFAILs a document whose findings are all listed, fails on
any finding not listed, and fails on a listed one that is fixed until its line is deleted.

| Kind | Findings | Meaning |
| --- | ---: | --- |
| path | 26 | a repo path in inline code or a code block that does not exist |
| class | 19 | a `Graph.KG.*`/`IVG.*` class not in `iris_src/src` nor made by IVG's DDL |
| method | 13 | `engine.<name>` that `IRISGraphEngine` does not have |
| global | 11 | a `^Global` root no code names |
| link | 7 | a relative link whose target is missing |
| env | 7 | an `IVG_*` variable no code reads |
| member | 4 | `Class.Member` not defined in the class |
| port | 4 | a reader-doc port not in `docker-compose.yml` (nor `other_ports`) |
| dev-script | 3 | a reader doc tells the user to run a maintainer script |
| container | 1 | a reader-doc container name not in `docker-compose.yml` |

### Findings in the reader documents

- `docs/ADMIN_GUIDE.md` line 24: dev-script `scripts/enterprise-container.sh`
- `docs/ADMIN_GUIDE.md` line 65: dev-script `scripts/test-container.sh`
- `docs/ADMIN_GUIDE.md` line 206: class `Graph.KG.Edge`
- `docs/ADMIN_GUIDE.md` line 396: container `ivg-iris`
- `docs/COMMUNITIES.md` line 142: link `../ENGINEERING_DEBT.md`
- `docs/COMMUNITIES.md` line 241: class `Graph.KG.ArnoAccelNKG.ExportAdjacencyKG`
- `docs/USER_GUIDE.md` line 451: method `engine.cypher_shadow`
- `docs/USER_GUIDE.md` line 673: method `engine.k_core_decomposition`
- `docs/demos/BIOMEDICAL_DEMO.md` line 11: port `31972`
- `docs/demos/BIOMEDICAL_DEMO.md` line 11: dev-script `scripts/enterprise-container.sh`
- `docs/demos/BIOMEDICAL_DEMO.md` line 22: env `IVG_BIO_HOST`
- `docs/demos/BIOMEDICAL_DEMO.md` line 22: env `IVG_BIO_PORT`
- `docs/demos/BIOMEDICAL_DEMO.md` line 22: env `IVG_BIO_NAMESPACE`
- `docs/demos/BIOMEDICAL_DEMO.md` line 23: env `IVG_BIO_USER`
- `docs/demos/BIOMEDICAL_DEMO.md` line 23: env `IVG_BIO_PASSWORD`
- `docs/demos/FRAUD_DEMO.md` line 18: port `8100`
- `docs/demos/PER_GRAPH_EMBEDDINGS_DEMO.md` line 7: port `31972`
- `docs/demos/VERSIONED_GRAPH_DEMO.md` line 6: port `31972`
- `docs/setup/QUICKSTART.md` line 151: method `engine.run_pagerank`
- `docs/setup/QUICKSTART.md` line 151: method `engine.run_khop`

The other findings (internal, research and release docs) are in `tests/docs/consistency_known_failures.txt`.
