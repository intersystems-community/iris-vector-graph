#!/usr/bin/env bash
# Run some TCK areas and compare them with a baseline TSV.
#
# usage: TCK_BASE=<baseline.tsv> scripts/tck/compare.sh <area> [area ...]
#
# Runs the areas with run_all.sh into a temp dir (same env variables, same
# lock), then prints per-area pass counts against the baseline and every
# scenario that passes in the baseline and not now (REG) or the reverse (GAIN).
# Scenarios tagged @ignore upstream are left out of both. Do not wrap this in
# flock: run_all.sh takes the lock itself.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "${IVG_REPO:-$here/../..}" && pwd)
[ $# -ge 1 ] || { sed -n '2,10p' "$0" >&2; exit 2; }
: "${TCK_BASE:?set TCK_BASE to a baseline results TSV}"
py=${PYTHON:-$repo/.venv/bin/python}
if [ ! -x "$py" ] && [ -z "${PYTHON:-}" ]; then  # a git worktree: use the main checkout's venv
  py=$(cd "$(git -C "$repo" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
fi

out=$(mktemp -d "${TMPDIR:-/tmp}/tck_cmp.XXXXXX")
IVG_REPO=$repo "$here/run_all.sh" "$out" "$@" >/dev/null

"$py" - "$here" "$TCK_BASE" "$out" "$repo/tests/tck/features" "$@" <<'PY'
import os, sys
here, base_tsv, out, features, areas = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
sys.path.insert(0, here)
import summarize as S

base = S.read_tsv(base_tsv)
ignored = S.upstream_ignored(features)
for area in areas:
    now = S.collect(os.path.join(out, area), None)
    now = {k: (S.IGNORED if k in ignored else v) for k, v in now.items()}
    b = {k: (S.IGNORED if k in ignored else base.get(k, S.FAIL)) for k in now}
    d = S.diff(b, now)
    c, cb = S.counts(now), S.counts(b)
    print(f"{area}: {c.passed}/{c.eligible} (base {cb.passed}) +{len(d.only_b)} -{len(d.only_a)}"
          + (f" [{c.ignored} ignored]" if c.ignored else ""))
    for k in d.only_a:
        print("  REG ", k[:160])
    for k in d.only_b:
        print("  GAIN", k[:160])
print(f"junit: {out}")
PY
