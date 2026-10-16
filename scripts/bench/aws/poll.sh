#!/usr/bin/env bash
# Status of the run on the instance. Adds this machine's current public IP to the security group first (the laptop's address
# changes as it moves between networks, and SSH then times out).
set -uo pipefail
source "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}"
export AWS_REGION
IP="$(curl -s --max-time 5 https://checkip.amazonaws.com)"
if [[ -n "$IP" ]] && ! aws ec2 describe-security-groups --group-ids "$SG" --query "SecurityGroups[0].IpPermissions[].IpRanges[].CidrIp" --output text | tr '\t' '\n' | grep -qx "$IP/32"; then
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$IP/32" >/dev/null && echo "allowed $IP"
fi
timeout 40 ssh -i "$PEM" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ec2-user@$run_IP" \
  'date -u +%H:%M; tail -${1:-1} ~/logs/chain.log; f=$(ls -t /Users/tdyar/ws/iris-vector-graph/docs/performance/alt-bench/results/aws-*/query.log 2>/dev/null | head -1); [ -n "$f" ] && tail -1 "$f" | cut -c1-100; df -h / | tail -1 | awk "{print \"disk\", \$3, \$5}"' < /dev/null
