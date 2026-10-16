#!/usr/bin/env bash
# Network, key pair and bucket for the quiet-machine benchmark run. Everything is tagged Project=ivg-bench; teardown.sh removes it.
# Usage: AWS_PROFILE=... scripts/bench/aws/provision.sh   (writes ids to ${IVG_AWS_ENV:-/tmp/ivg-aws.env})
set -euo pipefail
export AWS_REGION="${AWS_REGION:-us-east-1}"
ENVF="${IVG_AWS_ENV:-/tmp/ivg-aws.env}"
STAMP="$(date +%Y%m%d)"
MYIP="$(curl -s https://checkip.amazonaws.com)"
KEY="ivg-bench-$STAMP"
PEM="$HOME/.ssh/$KEY.pem"
VPC="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
TAGS='ResourceType=%s,Tags=[{Key=Project,Value=ivg-bench},{Key=Owner,Value=tdyar}]'

if [[ ! -f "$PEM" ]]; then
  aws ec2 create-key-pair --key-name "$KEY" --query KeyMaterial --output text >"$PEM"
  chmod 600 "$PEM"
fi
SG="$(aws ec2 create-security-group --group-name "ivg-bench-$STAMP" --description "ivg-bench ssh from one address" --vpc-id "$VPC" \
      --tag-specifications "$(printf "$TAGS" security-group)" --query GroupId --output text)"
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" >/dev/null
BUCKET="ivg-bench-$(aws sts get-caller-identity --query Account --output text)-$STAMP"
aws s3api create-bucket --bucket "$BUCKET" >/dev/null
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=Project,Value=ivg-bench},{Key=Owner,Value=tdyar}]'
cat >"$ENVF" <<ENV
AWS_REGION=$AWS_REGION
KEY=$KEY
PEM=$PEM
VPC=$VPC
SG=$SG
BUCKET=$BUCKET
MYIP=$MYIP
ENV
echo "wrote $ENVF"; cat "$ENVF"
