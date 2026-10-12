# Alternative vs baseline: launch-3

- side A: ref `6f17be0f` commit `6f17be0f6eab175038585494c31ae39c82e5c9e1`

Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; faster: B/A < 0.90 outside the spread; otherwise slower.

## drkg-load-500k

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| load_edges | 58600 | 58600 | n/a | n/a | n/a | n/a |
| load_nodes | 242500 | 242500 | n/a | n/a | n/a | n/a |
| total | 301100 | 301100 | n/a | n/a | n/a | n/a |
| wall_total | 303631 | 303631 | n/a | n/a | n/a | n/a |

## Totals

parity: 0, faster: 0, slower: 0, n/a: 4
