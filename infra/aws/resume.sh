#!/usr/bin/env bash
# Resume the AWS demo stack after pause.sh: start the database, re-apply the stack (services back to one
# task each; the IP allow-list is refreshed with this machine's current address), wait until the
# services are stable and print the URLs. Takes about 5-10 minutes.
# AuraDB Free pauses itself after a few days without use: resume it in console.neo4j.io first.
set -euo pipefail
cd "$(dirname "$0")/../.."
REGION="${AWS_REGION:-eu-west-2}"
CLUSTER="${CLUSTER:-sentinel}"
DB="${DB:-sentinel}"
TF="${TERRAFORM:-terraform}"

state="$(aws rds describe-db-instances --db-instance-identifier "$DB" --region "$REGION" \
  --query 'DBInstances[0].DBInstanceStatus' --output text)"
echo "database: $state"
if [[ "$state" == stopped ]]; then
  aws rds start-db-instance --db-instance-identifier "$DB" --region "$REGION" >/dev/null
fi
[[ "$state" == available ]] || { echo "waiting for the database..."; aws rds wait db-instance-available --db-instance-identifier "$DB" --region "$REGION"; }

bash infra/aws/up.sh >/dev/null
echo "waiting for the services..."
services="$(aws ecs list-services --cluster "$CLUSTER" --region "$REGION" --query 'serviceArns[]' --output text | tr '\t' '\n' | sed 's#.*/##' | tr '\n' ' ')"
# describe/wait take at most 10 services per call
set -- $services
while (( $# )); do
  aws ecs wait services-stable --cluster "$CLUSTER" --region "$REGION" --services "${@:1:10}"
  shift $(( $# < 10 ? $# : 10 ))
done
echo "Resumed."
"$TF" -chdir=infra/terraform/platform output urls
