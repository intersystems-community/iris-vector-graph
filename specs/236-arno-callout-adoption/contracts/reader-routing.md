# Contract: reader routing

## ObjectScript

`Graph.KG.ArnoAccel.ReaderRevisions() As %String` returns a JSON object
`{"<name>": <int>, ...}` of reader revisions, or `"{}"` when no library is
loaded, the `capabilities` entry is missing, or the call throws. It never
throws. Call form inside: `$ZF(-4,3,dllid,"capabilities")` then
`$ZF(-5,dllid,funcid)`, inside `Try/Catch`.

## Python

`iris_vector_graph.arno_readers`:

- `read_reader_report(conn) -> dict[str, int]`: calls `ReaderRevisions`; any
  exception or non-JSON result returns `{}` and logs a diagnostic.
- `load_proof(path=None) -> Proof`: missing or invalid returns an empty proof.
- `route(algorithm, readers, proof, graph="") -> str` (`readers` is a
  name-to-revision mapping):
  returns `"native"` or `"owner"`. Pure function; no I/O.

Rules: betweenness and unknown names return `"owner"`; a non-empty `graph`
returns `"owner"` (FR-014a); native requires
`readers.get(algorithm, 0) >= proof.min_revision(algorithm)` with the proof
value present; the library version string is never consulted.

Dispatch sites keep their existing Owner fallback: a Native Reader exception
logs and falls back to the Owner.
