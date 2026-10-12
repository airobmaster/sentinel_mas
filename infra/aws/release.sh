#!/usr/bin/env bash
# Build the images, push them to ECR and roll the ECS services (also used by .github/workflows/deploy.yml).
#   infra/aws/release.sh            all images, then redeploy every service
#   infra/aws/release.sh sentinel   one image (sentinel | mcp | workbench | airflow | ops)
# Needs Docker and AWS credentials; reads the repository URLs from the platform stack's region/account.
set -euo pipefail
cd "$(dirname "$0")/../.."

REGION="${AWS_REGION:-eu-west-2}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
TAG="${IMAGE_TAG:-latest}"
CLUSTER="${CLUSTER:-sentinel}"
ONLY="${1:-all}"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

push() {  # repo tag build-args...
  local repo="$1" tag="$2"; shift 2
  echo "== $repo:$tag"
  docker build --platform linux/amd64 -t "$REGISTRY/sentinel/$repo:$tag" "$@"
  docker push -q "$REGISTRY/sentinel/$repo:$tag"
}

want() { [[ "$ONLY" == all || "$ONLY" == "$1" ]]; }
want sentinel && push sentinel "$TAG" -f docker/sentinel.Dockerfile .
want mcp && push mcp "$TAG" -f mcp_servers/Dockerfile .
want workbench && push workbench "$TAG" -f docker/workbench.Dockerfile ui/workbench
want airflow && push airflow "$TAG" -f docker/airflow.Dockerfile .
if want ops; then
  for target in opa otel tempo prometheus grafana; do
    push ops "$target" -f docker/ops.Dockerfile --target "$target" .
  done
fi

# Roll the services that use the rebuilt images (tags are mutable: force a new deployment). Kafka and
# Presidio use public images and are left alone.
uses() {  # service -> image it runs
  case "$1" in
    api) echo sentinel ;;
    worker) echo "sentinel ops" ;;
    mcp-*) echo mcp ;;
    workbench) echo workbench ;;
    airflow) echo airflow ;;
    observability) echo ops ;;
  esac
}
services="$(aws ecs list-services --cluster "$CLUSTER" --region "$REGION" --query 'serviceArns[]' --output text 2>/dev/null || true)"
for arn in $services; do
  service="${arn##*/}"
  if [[ "$ONLY" == all && -n "$(uses "$service")" ]] || [[ " $(uses "$service") " == *" $ONLY "* ]]; then
    aws ecs update-service --cluster "$CLUSTER" --service "$service" --force-new-deployment \
      --region "$REGION" --query 'service.serviceName' --output text
  fi
done
