#!/usr/bin/env bash
# Pause the AWS demo stack between demos: every ECS service to 0 tasks and the RDS database stopped.
# Nothing is deleted (same URLs and data on resume). Still billed while paused: NAT gateway, ALB and
# public IP (about $2/day). AWS restarts a stopped RDS database after 7 days; resume.sh handles either state.
#   infra/aws/pause.sh      then, before the next demo:   infra/aws/resume.sh
set -euo pipefail
REGION="${AWS_REGION:-eu-west-2}"
CLUSTER="${CLUSTER:-sentinel}"
DB="${DB:-sentinel}"

for arn in $(aws ecs list-services --cluster "$CLUSTER" --region "$REGION" --query 'serviceArns[]' --output text); do
  aws ecs update-service --cluster "$CLUSTER" --service "${arn##*/}" --desired-count 0 --region "$REGION" \
    --query 'service.serviceName' --output text
done

state="$(aws rds describe-db-instances --db-instance-identifier "$DB" --region "$REGION" \
  --query 'DBInstances[0].DBInstanceStatus' --output text)"
if [[ "$state" == available ]]; then
  aws rds stop-db-instance --db-instance-identifier "$DB" --region "$REGION" --query 'DBInstance.DBInstanceStatus' --output text
else
  echo "database: $state (left as it is)"
fi
echo "Paused. Run infra/aws/resume.sh before the next demo."
