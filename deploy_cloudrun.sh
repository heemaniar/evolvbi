#!/usr/bin/env bash
# EvolvBI — Cloud Run deploy
# Usage: bash deploy_cloudrun.sh
set -euo pipefail

PROJECT="mallpulse-hackathon"
REGION="us-central1"
SERVICE="evolvbi"
REPO="mallpulse-repo"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}"

if [[ -f .env ]]; then
  export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

echo "▶ Project : ${PROJECT}"
echo "▶ Service : ${SERVICE}"
echo "▶ Image   : ${IMAGE}"
echo ""

echo "1/4  Ensuring Artifact Registry repo..."
gcloud artifacts repositories describe "${REPO}" \
  --project="${PROJECT}" --location="${REGION}" --format="value(name)" 2>/dev/null \
  || gcloud artifacts repositories create "${REPO}" \
       --project="${PROJECT}" --location="${REGION}" \
       --repository-format=docker \
       --description="MallPulse/EvolvBI container images"

echo "2/4  Building image with Cloud Build..."
gcloud builds submit . \
  --project="${PROJECT}" \
  --tag="${IMAGE}" \
  --machine-type=E2_HIGHCPU_8

echo "3/4  Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --memory=2Gi \
  --cpu=2 \
  --timeout=300 \
  --concurrency=10 \
  --min-instances=0 \
  --max-instances=3 \
  --set-env-vars="\
GOOGLE_GENAI_USE_VERTEXAI=${GOOGLE_GENAI_USE_VERTEXAI:-1},\
GOOGLE_CLOUD_PROJECT=${PROJECT},\
PHOENIX_API_KEY=${PHOENIX_API_KEY:-},\
PHOENIX_COLLECTOR_ENDPOINT=${PHOENIX_COLLECTOR_ENDPOINT:-},\
PHOENIX_CLIENT_HEADERS=${PHOENIX_CLIENT_HEADERS:-}"

echo "4/4  Done! Service URL:"
gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format="value(status.url)"
