#!/usr/bin/env bash
# Create (optionally) and deploy one IRIS namespace for TCK runs.
#
# usage: scripts/tck/setup_namespace.sh <NS> [--create]
#   --create  make the database /usr/irissys/mgr/<ns>/ and namespace <NS>
#             (globals and routines both in that database) if it does not
#             exist, over TCP (scripts/dev/tcp_setup.py make; no iris session)
#   then      deploy this repo's Graph.KG.* classes, IVG schema and CY_* UDFs
#             (scripts/tck/deploy_namespace.py)
#
# environment: IVG_REPO, IVG_TEST_CONTAINER (default ivg-iris-enterprise),
# IVG_PORT (default 31972, on 127.0.0.1), PYTHON.
# Holds the namespace's run lock (/tmp/ivg_iris_<NS>.lock) so it never
# redeploys under a running suite. Refuses USER. Rerun after any .cls change or
# any UDF change in iris_vector_graph/schema.py.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "${IVG_REPO:-$here/../..}" && pwd)
[ $# -ge 1 ] || { sed -n '2,16p' "$0" >&2; exit 2; }
ns=$(printf %s "$1" | tr '[:lower:]' '[:upper:]'); shift
create=0; [ "${1:-}" = --create ] && create=1
case "$ns" in USER|%SYS|"") echo "setup_namespace.sh: refusing $ns" >&2; exit 2;; esac
[[ "$ns" =~ ^[A-Z][A-Z0-9]*$ ]] || { echo "setup_namespace.sh: namespace must be alphanumeric" >&2; exit 2; }
py=${PYTHON:-$repo/.venv/bin/python}
if [ ! -x "$py" ] && [ -z "${PYTHON:-}" ]; then
  py=$(cd "$(git -C "$repo" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
fi

if [ -z "${_TCK_LOCKED:-}" ]; then
  exec /usr/bin/env python3 -c 'import fcntl,os,sys;l=open(sys.argv[1],"w");fcntl.flock(l,fcntl.LOCK_EX);os.environ["_TCK_LOCKED"]="1";os.execv(sys.argv[2],sys.argv[2:])' \
    "/tmp/ivg_iris_$ns.lock" "$0" "$ns" "$@"
fi

if [ $create = 1 ]; then
  "$py" "$repo/scripts/dev/tcp_setup.py" make "$ns"
fi

PYTHONPATH=$repo IVG_IGNORE_NAMESPACE_CHECK=1 "$py" "$here/deploy_namespace.py" "$ns" --repo "$repo"
