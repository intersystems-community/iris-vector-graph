#!/usr/bin/env bash
# Create (optionally) and deploy one IRIS namespace for TCK runs.
#
# usage: scripts/tck/setup_namespace.sh <NS> [--create]
#   --create  make the database /usr/irissys/mgr/<ns>/ and namespace <NS>
#             (globals and routines both in that database) if it does not
#             exist, via `docker exec ... iris session IRIS -U %SYS`
#   then      deploy this repo's Graph.KG.* classes, IVG schema and CY_* UDFs
#             (scripts/tck/deploy_namespace.py)
#
# environment: IVG_REPO, IVG_TEST_CONTAINER (default ivg-iris-enterprise),
# IVG_PORT (default 31972), IRIS_INSTANCE (default IRIS), PYTHON.
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
container=${IVG_TEST_CONTAINER:-ivg-iris-enterprise}
py=${PYTHON:-$repo/.venv/bin/python}
if [ ! -x "$py" ] && [ -z "${PYTHON:-}" ]; then
  py=$(cd "$(git -C "$repo" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
fi

if [ -z "${_TCK_LOCKED:-}" ]; then
  exec /usr/bin/env python3 -c 'import fcntl,os,sys;l=open(sys.argv[1],"w");fcntl.flock(l,fcntl.LOCK_EX);os.environ["_TCK_LOCKED"]="1";os.execv(sys.argv[2],sys.argv[2:])' \
    "/tmp/ivg_iris_$ns.lock" "$0" "$ns" "$@"
fi

if [ $create = 1 ]; then
  lower=$(printf %s "$ns" | tr '[:upper:]' '[:lower:]')
  docker exec -i "$container" iris session "${IRIS_INSTANCE:-IRIS}" -U %SYS <<EOF
set ns="$ns",dir="/usr/irissys/mgr/$lower/" if ##class(Config.Namespaces).Exists(ns) { write ns," exists",! } else { do ##class(%File).CreateDirectoryChain(dir) set sc=##class(SYS.Database).CreateDatabase(dir) write ns," createdb ",sc,! kill p set p("Directory")=dir set sc=##class(Config.Databases).Create(ns,.p) write ns," cfgdb ",sc,! kill q set q("Globals")=ns,q("Routines")=ns set sc=##class(Config.Namespaces).Create(ns,.q) write ns," cfgns ",sc,! }
halt
EOF
fi

PYTHONPATH=$repo IVG_IGNORE_NAMESPACE_CHECK=1 "$py" "$here/deploy_namespace.py" "$ns" --repo "$repo"
