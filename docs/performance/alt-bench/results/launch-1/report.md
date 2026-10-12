# Alternative vs baseline: launch-1

- side A: ref `6f17be0f` commit `6f17be0f6eab175038585494c31ae39c82e5c9e1`

Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; faster: B/A < 0.90 outside the spread; otherwise slower.

## bench-q1q6-m

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| M.Q1.ivg-os.hot_p50_ms | 1.37 | 1.37 | n/a | n/a | n/a | n/a |
| M.Q1.ivg-os.hot_p90_ms | 1.44 | 1.44 | n/a | n/a | n/a | n/a |
| M.Q2.ivg-arno.hot_p50_ms | 76.5 | 76.5 | n/a | n/a | n/a | n/a |
| M.Q2.ivg-arno.hot_p90_ms | 79.5 | 79.5 | n/a | n/a | n/a | n/a |
| M.Q2.ivg-os.hot_p50_ms | 65.7 | 65.7 | n/a | n/a | n/a | n/a |
| M.Q2.ivg-os.hot_p90_ms | 67.0 | 67.0 | n/a | n/a | n/a | n/a |
| M.Q3.ivg-arno.hot_p50_ms | 165 | 165 | n/a | n/a | n/a | n/a |
| M.Q3.ivg-arno.hot_p90_ms | 168 | 168 | n/a | n/a | n/a | n/a |
| M.Q3.ivg-os.hot_p50_ms | 141 | 141 | n/a | n/a | n/a | n/a |
| M.Q3.ivg-os.hot_p90_ms | 158 | 158 | n/a | n/a | n/a | n/a |
| M.Q4.ivg-arno.hot_p50_ms | 188 | 188 | n/a | n/a | n/a | n/a |
| M.Q4.ivg-arno.hot_p90_ms | 203 | 203 | n/a | n/a | n/a | n/a |
| M.Q4.ivg-os.hot_p50_ms | 130 | 130 | n/a | n/a | n/a | n/a |
| M.Q4.ivg-os.hot_p90_ms | 133 | 133 | n/a | n/a | n/a | n/a |
| M.Q5.ivg-os.hot_p50_ms | 0.29 | 0.29 | n/a | n/a | n/a | n/a |
| M.Q5.ivg-os.hot_p90_ms | 0.47 | 0.47 | n/a | n/a | n/a | n/a |
| M.Q6.ivg-os.hot_p50_ms | 0.22 | 0.22 | n/a | n/a | n/a | n/a |
| M.Q6.ivg-os.hot_p90_ms | 0.32 | 0.32 | n/a | n/a | n/a | n/a |
| wall_total | 21908 | 21908 | n/a | n/a | n/a | n/a |

## Failed or incomplete reps

- bench-193-full-s side A rep 1: failed (no metrics)

## Totals

parity: 0, faster: 0, slower: 0, n/a: 19
