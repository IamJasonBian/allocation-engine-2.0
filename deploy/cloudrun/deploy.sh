#!/usr/bin/env bash
# Build and deploy the risk service to Cloud Run (project route-manager-prod).
#
#   deploy/cloudrun/deploy.sh                 # APP_MODE=dev: serves the baked-in data/local/trading.db
#   APP_MODE=prod deploy/cloudrun/deploy.sh   # live Robinhood via the auth-service box
#
# prod needs AUTH_SERVICE_URL + RH_AUTH_SERVICE_REQUEST_TOKEN as Secret Manager
# secrets (auth-service-url, rh-auth-service-request-token) and an egress path
# the box's firewall accepts — see deploy/cloudrun/README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."

PROJECT=route-manager-prod
REGION=us-central1
SERVICE=risk-service
IMAGE=us-central1-docker.pkg.dev/$PROJECT/allocation-engine/risk-service
TAG=$(git rev-parse --short HEAD)
APP_MODE=${APP_MODE:-dev}

gcloud builds submit --project "$PROJECT" \
  --config deploy/cloudrun/cloudbuild.yaml \
  --substitutions _IMAGE="$IMAGE",_TAG="$TAG" .

args=(
  --project "$PROJECT" --region "$REGION"
  --image "$IMAGE:$TAG"
  --platform managed --allow-unauthenticated
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 2
  --set-env-vars "APP_MODE=$APP_MODE,ENGINE_ENABLED=false,DRY_RUN=true,DEFAULT_BROKER=robinhood"
)
if [[ "$APP_MODE" == "prod" ]]; then
  args+=(--set-secrets "AUTH_SERVICE_URL=auth-service-url:latest,RH_AUTH_SERVICE_REQUEST_TOKEN=rh-auth-service-request-token:latest")
fi

gcloud run deploy "$SERVICE" "${args[@]}"
URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')
echo "deployed $SERVICE ($APP_MODE) at $URL"
curl -sf "$URL/api/health" && echo
