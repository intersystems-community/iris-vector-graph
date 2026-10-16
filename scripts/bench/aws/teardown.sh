#!/usr/bin/env bash
# Remove everything provision.sh and launch.sh created (instances, security group, key pair, bucket). Idempotent.
set -uo pipefail
source "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}"
export AWS_REGION
for id in $(grep -E '^(datagen|run)=i-' "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}" | cut -d= -f2); do
  aws ec2 terminate-instances --instance-ids "$id" --query 'TerminatingInstances[0].CurrentState.Name' --output text
done
for id in $(grep -E '^(datagen|run)=i-' "${IVG_AWS_ENV:-$HOME/.cache/ivg-bench/aws.env}" | cut -d= -f2); do aws ec2 wait instance-terminated --instance-ids "$id"; done
aws ec2 delete-security-group --group-id "$SG" && echo "deleted $SG"
aws ec2 delete-key-pair --key-name "$KEY" && echo "deleted key $KEY" && rm -f "$PEM"
aws s3 rm "s3://$BUCKET" --recursive --only-show-errors && aws s3api delete-bucket --bucket "$BUCKET" && echo "deleted s3://$BUCKET"
echo "check: aws ec2 describe-instances --filters Name=tag:Project,Values=ivg-bench Name=instance-state-name,Values=running,pending"
