# Alternative vs baseline: launch-2

- side A: ref `6f17be0f` commit `6f17be0f6eab175038585494c31ae39c82e5c9e1`

Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; faster: B/A < 0.90 outside the spread; otherwise slower.

## bench-193-full-s

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| s1.S.hops.1.direct.p50 | 1.09 | 1.09 | n/a | n/a | n/a | n/a |
| s1.S.hops.1.direct.p90 | 1.1 | 1.1 | n/a | n/a | n/a | n/a |
| s1.S.hops.1.orig.p50 | 0.809 | 0.809 | n/a | n/a | n/a | n/a |
| s1.S.hops.1.orig.p90 | 0.869 | 0.869 | n/a | n/a | n/a | n/a |
| s1.S.hops.2.direct.p50 | 20.4 | 20.4 | n/a | n/a | n/a | n/a |
| s1.S.hops.2.direct.p90 | 20.6 | 20.6 | n/a | n/a | n/a | n/a |
| s1.S.hops.2.orig.p50 | 7.37 | 7.37 | n/a | n/a | n/a | n/a |
| s1.S.hops.2.orig.p90 | 7.57 | 7.57 | n/a | n/a | n/a | n/a |
| s1.S.hops.3.direct.p50 | 33.2 | 33.2 | n/a | n/a | n/a | n/a |
| s1.S.hops.3.direct.p90 | 33.5 | 33.5 | n/a | n/a | n/a | n/a |
| s1.S.hops.3.orig.p50 | 10.3 | 10.3 | n/a | n/a | n/a | n/a |
| s1.S.hops.3.orig.p90 | 10.5 | 10.5 | n/a | n/a | n/a | n/a |
| s1.S.hops.4.direct.p50 | 34.6 | 34.6 | n/a | n/a | n/a | n/a |
| s1.S.hops.4.direct.p90 | 34.7 | 34.7 | n/a | n/a | n/a | n/a |
| s1.S.hops.4.orig.p50 | 10.9 | 10.9 | n/a | n/a | n/a | n/a |
| s1.S.hops.4.orig.p90 | 11.0 | 11.0 | n/a | n/a | n/a | n/a |
| s2.S.hops.2.arno.p50 | 9.15 | 9.15 | n/a | n/a | n/a | n/a |
| s2.S.hops.2.arno.p90 | 9.19 | 9.19 | n/a | n/a | n/a | n/a |
| s2.S.hops.2.nkg.p50 | 1.55 | 1.55 | n/a | n/a | n/a | n/a |
| s2.S.hops.2.nkg.p90 | 1.66 | 1.66 | n/a | n/a | n/a | n/a |
| s2.S.hops.3.arno.p50 | 12.4 | 12.4 | n/a | n/a | n/a | n/a |
| s2.S.hops.3.arno.p90 | 12.5 | 12.5 | n/a | n/a | n/a | n/a |
| s2.S.hops.3.nkg.p50 | 2.12 | 2.12 | n/a | n/a | n/a | n/a |
| s2.S.hops.3.nkg.p90 | 2.17 | 2.17 | n/a | n/a | n/a | n/a |
| s2.S.hops.4.arno.p50 | 13.0 | 13.0 | n/a | n/a | n/a | n/a |
| s2.S.hops.4.arno.p90 | 13.2 | 13.2 | n/a | n/a | n/a | n/a |
| s2.S.hops.4.nkg.p50 | 2.2 | 2.2 | n/a | n/a | n/a | n/a |
| s2.S.hops.4.nkg.p90 | 2.23 | 2.23 | n/a | n/a | n/a | n/a |
| s2.S.hops.5.arno.p50 | 13.2 | 13.2 | n/a | n/a | n/a | n/a |
| s2.S.hops.5.arno.p90 | 13.2 | 13.2 | n/a | n/a | n/a | n/a |
| s2.S.hops.5.nkg.p50 | 2.12 | 2.12 | n/a | n/a | n/a | n/a |
| s2.S.hops.5.nkg.p90 | 2.12 | 2.12 | n/a | n/a | n/a | n/a |
| s3.S.Q1.ivg-os.p50 | 0.496 | 0.496 | n/a | n/a | n/a | n/a |
| s3.S.Q1.ivg-os.p90 | 0.555 | 0.555 | n/a | n/a | n/a | n/a |
| s3.S.Q2.ivg-arno.p50 | 9.06 | 9.06 | n/a | n/a | n/a | n/a |
| s3.S.Q2.ivg-arno.p90 | 9.12 | 9.12 | n/a | n/a | n/a | n/a |
| s3.S.Q2.ivg-os.p50 | 7.31 | 7.31 | n/a | n/a | n/a | n/a |
| s3.S.Q2.ivg-os.p90 | 7.59 | 7.59 | n/a | n/a | n/a | n/a |
| s3.S.Q3.ivg-arno.p50 | 12.6 | 12.6 | n/a | n/a | n/a | n/a |
| s3.S.Q3.ivg-arno.p90 | 12.9 | 12.9 | n/a | n/a | n/a | n/a |
| s3.S.Q3.ivg-os.p50 | 10.5 | 10.5 | n/a | n/a | n/a | n/a |
| s3.S.Q3.ivg-os.p90 | 10.7 | 10.7 | n/a | n/a | n/a | n/a |
| s3.S.Q4.ivg-arno.p50 | 16.4 | 16.4 | n/a | n/a | n/a | n/a |
| s3.S.Q4.ivg-arno.p90 | 27.5 | 27.5 | n/a | n/a | n/a | n/a |
| s3.S.Q4.ivg-os.p50 | 11.0 | 11.0 | n/a | n/a | n/a | n/a |
| s3.S.Q4.ivg-os.p90 | 11.2 | 11.2 | n/a | n/a | n/a | n/a |
| s3.S.Q5.ivg-os.p50 | 0.331 | 0.331 | n/a | n/a | n/a | n/a |
| s3.S.Q5.ivg-os.p90 | 0.35 | 0.35 | n/a | n/a | n/a | n/a |
| s3.S.Q6.ivg-os.p50 | 0.334 | 0.334 | n/a | n/a | n/a | n/a |
| s3.S.Q6.ivg-os.p90 | 0.351 | 0.351 | n/a | n/a | n/a | n/a |
| wall_total | 3342 | 3342 | n/a | n/a | n/a | n/a |

## Failed or incomplete reps

- bench-193-full-s side A rep 1: tests-failed (metrics kept)

## Totals

parity: 0, faster: 0, slower: 0, n/a: 51
