#!/usr/bin/env bash
# First-time data setup on AWS, as a one-off ECS task with the Sentinel image: Airflow's metadata database,
# Kafka topics, the dataset and sanctions lists in Aurora, policy embeddings (Bedrock) and the AuraDB graph.
#   infra/aws/bootstrap.sh                    everything
#   infra/aws/bootstrap.sh "sentinel data kb"  any other command(s)
set -euo pipefail
cd "$(dirname "$0")/../terraform/platform"

TF="${TERRAFORM:-terraform}"
REGION="${AWS_REGION:-eu-west-2}"
out() { "$TF" output -json run_task | python -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
CLUSTER="$(out cluster)"; TASKDEF="$(out task_definition)"; SUBNETS="$(out subnets)"; SG="$(out security_group)"
LOGS="$(out log_group)"

CREATE_AIRFLOW_DB="python -c \"import os, psycopg
c = psycopg.connect(os.environ['SENTINEL_PG_DSN'], autocommit=True)
if not c.execute(\\\"SELECT 1 FROM pg_database WHERE datname = 'airflow'\\\").fetchone(): c.execute('CREATE DATABASE airflow')\""
COMMAND="${1:-$CREATE_AIRFLOW_DB && sentinel kafka init && sentinel data load && sentinel data kb && sentinel data graph}"

overrides="$(python -c "import json,sys; print(json.dumps({'containerOverrides': [{'name': 'task', 'command': [sys.argv[1]]}]}))" "$COMMAND")"
arn="$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASKDEF" --launch-type FARGATE --region "$REGION" \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides "$overrides" --query 'tasks[0].taskArn' --output text)"
echo "task ${arn##*/}: $COMMAND"
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$arn" --region "$REGION" || \
  aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$arn" --region "$REGION"
code="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$arn" --region "$REGION" \
  --query 'tasks[0].containers[0].exitCode' --output text)"
aws logs get-log-events --log-group-name "$LOGS" --log-stream-name "task/task/${arn##*/}" --region "$REGION" \
  --query 'events[].message' --output text | tr '\t' '\n' | tail -40
echo "exit code: $code"
[[ "$code" == 0 ]]
