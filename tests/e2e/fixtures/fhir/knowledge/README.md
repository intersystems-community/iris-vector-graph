# Spec 232 knowledge fixture

Loaded in stages through `IVGTest.FHIRLoad.Dispatch`; the graph syncs once after each
stage. See `specs/232-fhir-canonical-links/quickstart.md` for the expected outcomes.

`@P@` is replaced by the loader's per-run prefix in every id and url, so reruns
against the same repository never share a canonical url with an earlier run.

- `stage1/`: Library `l1` (L 1.0.0), `l2` (L 2.0.0), `b` (B), `m` (U1); PlanDefinition
  `pd1`, `pd2`, `pd3`; ActivityDefinition `ad1`; Patient `p1`; CarePlan `cp1`.
- `stage2/m2.json`: a second Library carrying U1.
- `stage3/m.json`: `m` edited from U1 to U2.
- `stage4.json`: the keys to delete (`Library/l2`).
