#!/usr/bin/env bash
# Launch one benchmark instance. Usage: launch.sh <name> <instance-type> <ami-ssm-arch: arm64|x86_64> <disk-GB>
# Dead-man switch: the instance terminates itself after 24 hours whatever happens.
set -euo pipefail
source "${IVG_AWS_ENV:-/tmp/ivg-aws.env}"
export AWS_REGION
NAME="$1"; TYPE="$2"; ARCH="$3"; DISK="$4"
AMI="$(aws ssm get-parameter --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-$ARCH" --query Parameter.Value --output text)"
SUBNET="$(aws ec2 describe-subnets --filters Name=default-for-az,Values=true Name=availability-zone,Values=${AWS_REGION}a --query 'Subnets[0].SubnetId' --output text)"
UD="$(mktemp)"
cat >"$UD" <<'UD'
#!/bin/bash
dnf install -y docker git tar gzip python3.11 python3.11-pip rsync >/var/log/ivg-userdata.log 2>&1
systemctl enable --now docker
usermod -aG docker ec2-user
mkdir -p /Users/tdyar/ws && chown -R ec2-user /Users
( sleep 86400; shutdown -h now ) >/dev/null 2>&1 &
touch /var/tmp/ivg-ready
UD
IID="$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --key-name "$KEY" --security-group-ids "$SG" --subnet-id "$SUBNET" \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=$DISK,VolumeType=gp3,Iops=6000,Throughput=500,DeleteOnTermination=true}" \
  --instance-initiated-shutdown-behavior terminate --user-data "file://$UD" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=ivg-bench},{Key=Owner,Value=tdyar}]" "ResourceType=volume,Tags=[{Key=Project,Value=ivg-bench},{Key=Owner,Value=tdyar}]" \
  --query 'Instances[0].InstanceId' --output text)"
echo "$NAME=$IID" >>"${IVG_AWS_ENV:-/tmp/ivg-aws.env}"
aws ec2 wait instance-running --instance-ids "$IID"
IP="$(aws ec2 describe-instances --instance-ids "$IID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
echo "$NAME ip=$IP id=$IID type=$TYPE"
echo "${NAME}_IP=$IP" >>"${IVG_AWS_ENV:-/tmp/ivg-aws.env}"
