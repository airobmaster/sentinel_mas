#!/usr/bin/env bash
# Create or update the AWS demo stack (infra/terraform/platform), then register the workbench's HTTPS
# address with Cognito (identity stack). Neo4j AuraDB settings come from .env:
#   SENTINEL_NEO4J_AURA_URI, SENTINEL_NEO4J_AURA_USER, SENTINEL_NEO4J_AURA_PASSWORD
# The allow-list is this machine's public IPv4 address, plus any in EXTRA_IPS (comma-separated).
#   infra/aws/release.sh   first (images), then  infra/aws/up.sh,  then once  infra/aws/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

TF="${TERRAFORM:-terraform}"
env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'; }
IPS="$(curl -s https://checkip.amazonaws.com)${EXTRA_IPS:+,$EXTRA_IPS}"
ALLOWED="$(python -c "import json,sys; print(json.dumps(sys.argv[1].split(',')))" "$IPS")"

export TF_VAR_allowed_ips="$ALLOWED"
export TF_VAR_neo4j_uri="$(env_value SENTINEL_NEO4J_AURA_URI)"
export TF_VAR_neo4j_user="$(env_value SENTINEL_NEO4J_AURA_USER)"
export TF_VAR_neo4j_password="$(env_value SENTINEL_NEO4J_AURA_PASSWORD)"
[[ -n "$TF_VAR_neo4j_uri" && -n "$TF_VAR_neo4j_password" ]] || { echo "Set the SENTINEL_NEO4J_AURA_* values in .env"; exit 1; }

"$TF" -chdir=infra/terraform/platform init -input=false >/dev/null
"$TF" -chdir=infra/terraform/platform apply -input=false -auto-approve
WORKBENCH="$("$TF" -chdir=infra/terraform/platform output -json urls | python -c "import json,sys; print(json.load(sys.stdin)['workbench'])")"

# Cognito: allow sign-in to return to the AWS workbench as well as the local one
"$TF" -chdir=infra/terraform/identity apply -input=false -auto-approve \
  -var "workbench_callback_urls=[\"http://localhost:5173/callback\",\"$WORKBENCH/callback\"]" \
  -var "workbench_logout_urls=[\"http://localhost:5173/\",\"$WORKBENCH/\"]"

"$TF" -chdir=infra/terraform/platform output urls
