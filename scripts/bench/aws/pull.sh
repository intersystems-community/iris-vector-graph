#!/usr/bin/env bash
# Copy the run instance's results to this machine (the second copy next to run_snb.sh's S3 pushes, and the one that does not
# depend on the instance's expiring credentials). Safe to repeat; `pull.sh loop` repeats it every 20 minutes until DONE.
# Usage: scripts/bench/aws/pull.sh [loop]
set -uo pipefail
source "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}"
REPO="$(git rev-parse --show-toplevel)"; RES="docs/performance/alt-bench/results/"
pull_once() {
  rsync -az -e "ssh -i $PEM -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new" \
    --include='aws-*/***' --exclude='*' "ec2-user@$run_IP:/Users/tdyar/ws/iris-vector-graph/$RES" "$REPO/$RES" \
    && echo "[$(date +%H:%M)] pulled" || echo "[$(date +%H:%M)] pull failed rc=$?"
}
pull_once
if [[ "${1:-}" == loop ]]; then
  until ssh -i "$PEM" -o ConnectTimeout=15 "ec2-user@$run_IP" 'grep -q CHAIN-DONE ~/logs/chain.log' </dev/null 2>/dev/null; do sleep 1200; pull_once; done
  pull_once
fi
