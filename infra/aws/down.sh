#!/usr/bin/env bash
# Destroy the AWS demo stack (everything hourly-billed) and reset Cognito to the local workbench only.
# The identity stack (Cognito, free) and the ci stack (GitHub OIDC role) are kept.
set -euo pipefail
cd "$(dirname "$0")/../.."
TF="${TERRAFORM:-terraform}"
"$TF" -chdir=infra/terraform/platform destroy -input=false -auto-approve \
  -var 'allowed_ips=["127.0.0.1"]' -var 'neo4j_uri=destroyed'
"$TF" -chdir=infra/terraform/identity apply -input=false -auto-approve
