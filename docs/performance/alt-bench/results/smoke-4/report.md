# Alternative vs baseline: smoke-4

- side A: ref `6f17be0f` commit `6f17be0f6eab175038585494c31ae39c82e5c9e1`
- side B: ref `alt-integration` commit `f754f13551fe84a15de4584225978096eb4aec50`

Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; faster: B/A < 0.90 outside the spread; otherwise slower.

## e2e-227-scale

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| create_routes | 22100 | 22100 | 19400 | 19400 | 0.878 | faster |
| erase | 3900 | 3900 | 3100 | 3100 | 0.795 | faster |
| inventory | 3600 | 3600 | 2310 | 2310 | 0.642 | faster |
| wall_total | 49245 | 49245 | 42750 | 42750 | 0.868 | faster |

## nkg-enterprise-khop

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| q1.p50 | 0.283 | 0.283 | 0.265 | 0.265 | 0.936 | parity |
| q1.p95 | 0.341 | 0.341 | 0.33 | 0.33 | 0.968 | parity |
| q2a.p50 | 0.34 | 0.34 | 0.404 | 0.404 | 1.19 | slower |
| q2a.p95 | 0.386 | 0.386 | 0.527 | 0.527 | 1.37 | slower |
| q3.p50 | 0.788 | 0.788 | 1.08 | 1.08 | 1.37 | slower |
| q3.p95 | 0.9 | 0.9 | 1.19 | 1.19 | 1.32 | slower |
| wall_total | 9231 | 9231 | 7590 | 7590 | 0.822 | faster |

## perf-scale-benchmark

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| bulk_load | 14870 | 14870 | 4760 | 4760 | 0.32 | faster |
| create_node | 27.9 | 27.9 | 30.5 | 30.5 | 1.09 | parity |
| get_nodes.batch1 | 36.1 | 36.1 | 54.1 | 54.1 | 1.5 | slower |
| get_nodes.batch10 | 39.6 | 39.6 | 48.8 | 48.8 | 1.23 | slower |
| get_nodes.batch100 | 83.5 | 83.5 | 79.2 | 79.2 | 0.949 | parity |
| get_nodes.batch1000 | 482 | 482 | 458 | 458 | 0.951 | parity |
| search.1 | 42.2 | 42.2 | 45.3 | 45.3 | 1.07 | parity |
| search.2 | 26.1 | 26.1 | 31.7 | 31.7 | 1.21 | slower |
| search.3 | 24.1 | 24.1 | 27.6 | 27.6 | 1.15 | slower |
| wall_total | 16280 | 16280 | 6378 | 6378 | 0.392 | faster |

## three-engine

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| count friends | 4.03 | 4.03 | 3.21 | 3.21 | 0.797 | faster |
| ic13 shortestpath | 0.467 | 0.467 | 0.398 | 0.398 | 0.852 | faster |
| ic2 1-hop knows | 4.01 | 4.01 | 3.28 | 3.28 | 0.818 | faster |
| ic3 2-hop knows | 1.38 | 1.38 | 1.16 | 1.16 | 0.841 | faster |
| wall_total | 69184 | 69184 | 26678 | 26678 | 0.386 | faster |

## Totals

parity: 6, faster: 12, slower: 8, n/a: 0
