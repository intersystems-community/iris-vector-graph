# ADR-0005 — Vector width is never declared inside `TO_VECTOR`

**Status**: Accepted
**Date**: 2026-09-19
**Specs**: 226 (embedding identity and width contract)

## Decision

The generated vector-search procedures convert the query vector with the **unlengthed**
form:

```sql
TO_VECTOR(:queryInput, DOUBLE)
```

The three-argument form `TO_VECTOR(:queryInput, DOUBLE, <n>)` MUST NOT be used for a
query vector, and the `embedding_dimension` parameter of
`GraphSchema.get_procedures_sql_list` — which exists, is accepted, and is read nowhere —
MUST NOT be wired into it. Spec 226 deprecates that parameter rather than completing it.

The rule is narrow on purpose: it is about the **query** vector at comparison time. A
stored vector **column** should still be declared with a width; that is where
enforcement legitimately lives.

## Context

The parameter looks unfinished. A maintainer seeing a width available at generation time
and a `TO_VECTOR` call without one will reasonably assume the wiring was forgotten, and
"fixing" it is a one-line change. It is not a fix.

Measured 2026-09-19 against `ivg-iris-enterprise` (port 31972), querying a
`VECTOR(DOUBLE, 4)` column with `VECTOR_COSINE`:

| Query form                 | Query vector width | Result                                       |
| -------------------------- | ------------------ | -------------------------------------------- |
| `TO_VECTOR(:q, DOUBLE)`    | 4                  | correct score                                |
| `TO_VECTOR(:q, DOUBLE)`    | 3 or 6             | `SQLCODE -257`, vectors of different lengths |
| `TO_VECTOR(:q, DOUBLE, 4)` | 3                  | `0.6831300510639733`                         |
| `TO_VECTOR(:q, DOUBLE, 4)` | 6                  | `1.0`                                        |

The lengthed form pads or truncates the query vector to the declared length and then
computes a distance against the padded value. No error, no warning. A 6-element query
truncated to 4 returned a perfect-match score of `1.0`, which is the worst possible
failure mode for a nearest-neighbour search: it does not merely rank badly, it ranks a
wrong row first with maximum confidence.

Declaring the width converts a loud, correct `-257` into a silently wrong answer. The
absence of a length is what makes IRIS compare the two widths at all.

Two adjacent facts, same measurement session, that bear on why this is easy to get
wrong:

- A declared **column** width is enforced at INSERT with `SQLCODE -104`, for vectors
  that are too short as well as too long. Enforcement at the column is real and worth
  keeping.
- `SQLCODE -260` ("Cannot perform vector operation on vectors with unspecified length")
  is raised off the column declaration, not the data — a table holding two _uniform_
  4-element vectors in a `VECTOR(DOUBLE)` column still fails. It is tempting to read
  `-260` as evidence that widths need pinning everywhere, including in `TO_VECTOR`. It
  is not; it means one column lacks a length.

## Consequences

- A caller who passes a wrong-width query vector gets an error instead of a plausible
  number. Spec 226 pins this with a regression test asserting `-257`, so a future change
  that declares the width fails the suite rather than shipping quietly.
- `GraphSchema.get_procedures_sql_list(embedding_dimension=...)` emits a
  `DeprecationWarning` in 3.2.0 and is removed in 4.0.0. It is not completed.
- Width agreement between writers is enforced where it can be enforced usefully — at the
  column declaration, and against the recorded expectation in
  `Graph_KG.embedding_registry` (spec 226) — not by silently reshaping query vectors.
- This ADR does not discourage declaring lengths on vector **columns**. Undeclared
  columns are the cause of `-260` and should be redeclared, not worked around.

## Alternatives considered

**Wire the parameter through, as it appears to intend.** Rejected on the measurement
above: it would trade a correct error for a wrong result. The parameter's inertness is
the safe state.

**Validate the query-vector width in Python before calling the procedure.** Useful, and
spec 226 does check offered widths at the write seams — but it cannot cover a caller who
invokes the stored procedure directly from SQL or ObjectScript. The unlengthed
`TO_VECTOR` protects that path too, and it protects it inside the database where the
comparison actually happens.

**Delete the parameter outright in 3.2.0.** Rejected as gratuitously breaking for a
parameter that has never had an effect; a caller passing it today gets exactly the
behaviour they already get. Deprecate in 3.2.0, remove in 4.0.0.
