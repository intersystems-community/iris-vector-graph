#!/usr/bin/env bash
# Make a freshly launched run instance ready for run_snb.sh: repo, IRIS container, venvs, side trees, datasets from S3.
# Usage: AWS_PROFILE=... [DATASETS="sf1"] [IVG_REF=branch] scripts/bench/aws/bootstrap.sh   (reads ~/.cache/ivg-bench/aws.env, needs run_IP and
#   BUCKET; DATASETS names the scales to fetch from S3, default all three: "sf0.1 sf1 sf10")
set -euo pipefail
source "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}"
export AWS_REGION
REPO="$(git rev-parse --show-toplevel)"; cd "$REPO"
CACHE="$HOME/.cache/ivg-bench"; mkdir -p "$CACHE"
IMAGE="${IVG_AWS_IRIS_IMAGE:-122293094970.dkr.ecr.us-east-1.amazonaws.com/isc-obs/irishealth:2026.3.0AI.128.0-arm64}"
ssh_() { ssh -i "$PEM" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "ec2-user@$run_IP" "$@"; }

for _ in $(seq 1 40); do ssh_ 'test -f /var/tmp/ivg-ready' 2>/dev/null && break; sleep 10; done
# the local branch the instance checks out (IVG_REF, default alt-parity), shipped as a bundle
REF="${IVG_REF:-alt-parity}"
git bundle create "$CACHE/ivg.bundle" "$REF" >/dev/null 2>&1
scp -q -i "$PEM" "$CACHE/ivg.bundle" docker/enterprise/iris.key docker/enterprise/libarno_callout.so "ec2-user@$run_IP:~/"
CRED="$(aws configure export-credentials --format env-no-export | tr '\n' ' ')"

ssh_ "export $CRED AWS_REGION=$AWS_REGION BUCKET=$BUCKET IMAGE=$IMAGE REF=$REF DATASETS='${DATASETS:-sf0.1 sf1 sf10}'; bash -s" <<'REMOTE'
set -euo pipefail
sudo dnf install -y patch >/dev/null 2>&1
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64 -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
printf '#!/bin/bash\nif [ "$1" = "-a" ]; then shift 2; fi\nexec sha256sum "$@"\n' | sudo tee /usr/local/bin/shasum >/dev/null; sudo chmod +x /usr/local/bin/shasum
R=/Users/tdyar/ws/iris-vector-graph; C=$HOME/.cache/ivg-bench; mkdir -p "$C"
[ -d "$R/.git" ] || git clone -q ~/ivg.bundle "$R" -b "$REF"
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
.venv/bin/python scripts/bench/alt/setup_sides.py export --alt-ref "$REF" | tail -2
for d in $DATASETS; do aws s3 sync "s3://$BUCKET/data/$d" "/snb/$d" --only-show-errors; done
for pair in snb01:sf0.1 snb1:sf1 snb10:sf10; do
  [ -d "/snb/${pair##*:}" ] || continue
  mkdir -p "$C/${pair%%:*}"; ln -sfn "/snb/${pair##*:}" "$C/${pair%%:*}/out"
done
du -sh /snb/*; df -h / | tail -1; docker ps --format '{{.Names}} {{.Status}}'
echo BOOTSTRAP-OK
REMOTE
