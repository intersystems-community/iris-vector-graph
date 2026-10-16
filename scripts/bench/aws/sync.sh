#!/usr/bin/env bash
# Push the local alt-parity branch to the run instance (git bundle; no remote involved) and fast-forward its checkout.
set -euo pipefail
source "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}"
cd "$(git rev-parse --show-toplevel)"
mkdir -p "$HOME/.cache/ivg-bench"
git bundle create "$HOME/.cache/ivg-bench/ivg-sync.bundle" alt-parity >/dev/null 2>&1
scp -q -i "$PEM" -o StrictHostKeyChecking=accept-new "$HOME/.cache/ivg-bench/ivg-sync.bundle" "ec2-user@$run_IP:/tmp/ivg-sync.bundle"
ssh -i "$PEM" "ec2-user@$run_IP" 'cd /Users/tdyar/ws/iris-vector-graph && git fetch -q /tmp/ivg-sync.bundle alt-parity:refs/remotes/sync/alt-parity && git reset -q --hard sync/alt-parity && git log --oneline -1'
