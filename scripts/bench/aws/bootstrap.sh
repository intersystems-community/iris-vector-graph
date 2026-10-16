#!/usr/bin/env bash
# Make a freshly launched run instance ready for run_snb.sh: repo, IRIS container, venvs, side trees, datasets from S3.
# Usage: AWS_PROFILE=... scripts/bench/aws/bootstrap.sh            (reads ~/.cache/ivg-bench/aws.env, needs run_IP and BUCKET)
set -euo pipefail
source "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}"
export AWS_REGION
REPO="$(git rev-parse --show-toplevel)"; cd "$REPO"
CACHE="$HOME/.cache/ivg-bench"; mkdir -p "$CACHE"
IMAGE="${IVG_AWS_IRIS_IMAGE:-122293094970.dkr.ecr.us-east-1.amazonaws.com/isc-obs/irishealth:2026.3.0AI.128.0-arm64}"
ssh_() { ssh -i "$PEM" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "ec2-user@$run_IP" "$@"; }

for _ in $(seq 1 40); do ssh_ 'test -f /var/tmp/ivg-ready' 2>/dev/null && break; sleep 10; done
git bundle create "$CACHE/ivg.bundle" alt-parity >/dev/null 2>&1
scp -q -i "$PEM" "$CACHE/ivg.bundle" docker/enterprise/iris.key docker/enterprise/libarno_callout.so "ec2-user@$run_IP:~/"
CRED="$(aws configure export-credentials --format env-no-export | tr '\n' ' ')"

ssh_ "export $CRED AWS_REGION=$AWS_REGION BUCKET=$BUCKET IMAGE=$IMAGE" bash -s <<'REMOTE'
set -euo pipefail
sudo dnf install -y patch >/dev/null 2>&1
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64 -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
printf '#!/bin/bash\nif [ "$1" = "-a" ]; then shift 2; fi\nexec sha256sum "$@"\n' | sudo tee /usr/local/bin/shasum >/dev/null; sudo chmod +x /usr/local/bin/shasum
R=/Users/tdyar/ws/iris-vector-graph; C=$HOME/.cache/ivg-bench; mkdir -p "$C"
[ -d "$R/.git" ] || git clone -q ~/ivg.bundle "$R" -b alt-parity
cp ~/iris.key ~/libarno_callout.so "$R/docker/enterprise/"
aws ecr get-login-password | docker login --username AWS --password-stdin "${IMAGE%%/*}" >/dev/null
docker pull -q "$IMAGE" >/dev/null
docker rm -f ivg-iris-enterprise >/dev/null 2>&1 || true
cd "$R/docker/enterprise"
docker run -d --name ivg-iris-enterprise -p 31972:1972 -p 62773:52773 -e ISC_CPF_MERGE_FILE=/home/irisowner/merge.cpf \
  -v "$PWD/iris.key:/usr/irissys/mgr/iris.key:ro" -v "$PWD/merge.cpf:/home/irisowner/merge.cpf:ro" \
  -v "$PWD/libarno_callout.so:/tmp/libarno_callout.so:ro" -v "$R/iris_src/src:/usr/irissys/mgr/user/ivg_src:ro" \
  --restart unless-stopped "$IMAGE" >/dev/null
cd "$R"
python3.11 -m venv .venv && .venv/bin/pip -q install -U pip && .venv/bin/pip -q install -e ".[dev]" networkx igraph requests
python3.11 -m venv "$C/comp-venv" && "$C/comp-venv/bin/pip" -q install -U pip && "$C/comp-venv/bin/pip" -q install -r scripts/bench/competitors/requirements.txt
bash scripts/bench/competitors/competitors.sh gds >/dev/null
for _ in $(seq 1 40); do docker exec ivg-iris-enterprise /usr/irissys/bin/iris session iris -U%SYS "Write 1" 2>/dev/null | grep -q 1 && break; sleep 5; done
.venv/bin/python scripts/bench/alt/setup_sides.py export --alt-ref alt-parity | tail -2
aws s3 sync "s3://$BUCKET/data" /snb --only-show-errors
for pair in snb01:sf0.1 snb1:sf1 snb10:sf10; do mkdir -p "$C/${pair%%:*}"; ln -sfn "/snb/${pair##*:}" "$C/${pair%%:*}/out"; done
du -sh /snb/*; df -h / | tail -1; docker ps --format '{{.Names}} {{.Status}}'
echo BOOTSTRAP-OK
REMOTE
