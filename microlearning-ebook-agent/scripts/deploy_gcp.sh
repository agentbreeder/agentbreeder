#!/usr/bin/env bash
# End-to-end Cloud Run deploy for the microlearning-ebook-agent.
#
# What it does:
#   1. Enables required GCP APIs (idempotent)
#   2. Creates an Artifact Registry repo if missing
#   3. Pushes secrets (GOOGLE_API_KEY, TAVILY_API_KEY, AGENT_AUTH_TOKEN) to Secret Manager
#   4. Submits a Cloud Build job to build + push the image
#   5. Deploys to Cloud Run with min=0, max=5, memory=2Gi
#   6. Prints the public service URL + a sample curl command
#
# Prereqs:
#   - gcloud CLI authenticated (gcloud auth login + application-default login)
#   - Project set: gcloud config set project <PROJECT_ID>
#   - .env file in this directory with GOOGLE_API_KEY, TAVILY_API_KEY, AGENT_AUTH_TOKEN
set -euo pipefail

cd "$(dirname "$0")/.."
AGENT_DIR="$(pwd)"
REPO_ROOT="$(cd .. && pwd)"

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-microlearning-ebook-agent}"
REPO_NAME="${REPO_NAME:-hippokit}"
IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d-%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:${IMAGE_TAG}"

if [ -z "$PROJECT_ID" ]; then
  echo "ERROR: no GCP project set. Run: gcloud config set project <PROJECT_ID>" >&2
  exit 1
fi

# Source .env so we can push secrets
if [ -f "$AGENT_DIR/.env" ]; then
  set -a; source "$AGENT_DIR/.env"; set +a
fi

for v in GOOGLE_API_KEY TAVILY_API_KEY AGENT_AUTH_TOKEN; do
  if [ -z "${!v:-}" ]; then
    echo "WARN: $v is not set in .env — secret will be skipped." >&2
  fi
done

echo "════════════════════════════════════════════════════════════════"
echo "  Project:  $PROJECT_ID"
echo "  Region:   $REGION"
echo "  Service:  $SERVICE_NAME"
echo "  Image:    $IMAGE"
echo "════════════════════════════════════════════════════════════════"

echo
echo "── 1. Enable required APIs (idempotent) ──"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT_ID"

echo
echo "── 2. Ensure Artifact Registry repo '${REPO_NAME}' exists ──"
if ! gcloud artifacts repositories describe "$REPO_NAME" \
       --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="AgentBreeder agent images" \
    --project="$PROJECT_ID"
fi

echo
echo "── 3. Push secrets to Secret Manager ──"
push_secret() {
  local name="$1" value="$2"
  if [ -z "$value" ]; then
    echo "  (skipped $name — empty)"
    return
  fi
  if gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project="$PROJECT_ID" >/dev/null
    echo "  + new version of $name"
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- \
      --replication-policy=automatic --project="$PROJECT_ID" >/dev/null
    echo "  + created $name"
  fi
}
push_secret "GOOGLE_API_KEY" "${GOOGLE_API_KEY:-}"
push_secret "TAVILY_API_KEY" "${TAVILY_API_KEY:-}"
push_secret "AGENT_AUTH_TOKEN" "${AGENT_AUTH_TOKEN:-}"

echo
echo "── 4. Build + push image via Cloud Build (context = repo root) ──"
cd "$REPO_ROOT"
CLOUDBUILD_TMP="$(mktemp -t agentbreeder-cloudbuild-XXXX.yaml)"
trap "rm -f $CLOUDBUILD_TMP" EXIT
cat > "$CLOUDBUILD_TMP" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args:
      - build
      - -f
      - microlearning-ebook-agent/Dockerfile.cloudrun
      - -t
      - $IMAGE
      - .
images:
  - $IMAGE
options:
  logging: CLOUD_LOGGING_ONLY
EOF
gcloud builds submit . \
  --config "$CLOUDBUILD_TMP" \
  --project "$PROJECT_ID" \
  || { echo "Build failed"; exit 1; }
cd "$AGENT_DIR"

echo
echo "── 5. Deploy to Cloud Run ──"
SECRET_FLAGS=()
for v in GOOGLE_API_KEY TAVILY_API_KEY AGENT_AUTH_TOKEN; do
  if [ -n "${!v:-}" ]; then
    SECRET_FLAGS+=(--update-secrets "${v}=${v}:latest")
  fi
done

gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 0 \
  --max-instances 5 \
  --concurrency 10 \
  --timeout 300 \
  --set-env-vars "AGENT_MODEL=gemini-2.5-flash,AGENT_NAME=${SERVICE_NAME},LOG_LEVEL=info" \
  "${SECRET_FLAGS[@]}"

echo
URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')"
echo "════════════════════════════════════════════════════════════════"
echo "  Deployed: $URL"
echo "════════════════════════════════════════════════════════════════"
echo
echo "Test:"
echo "  curl $URL/health"
echo
echo "  curl -X POST $URL/invoke \\"
echo "    -H \"Authorization: Bearer \$AGENT_AUTH_TOKEN\" \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"input\":\"What tools do you have?\"}'"
