#!/usr/bin/env bash
# Northwind example stack: Postgres+pgvector (commerce seed data), LiteLLM gateway (Gemini, OpenRouter
# fallback), Phoenix tracing, the Tavily/trace key proxy and the ops dashboard (staff approvals,
# online evals). This is example infrastructure, not AgentBreeder product code.
#
# It also writes what AgentBreeder needs to build and deploy the agent in ../.agentbreeder/:
#   operator.yaml   environment `dev`: gateway grant, model allow-list, ceilings, service URLs,
#                   secret bindings and the agent's identities (static demo provider)
#   principals.yaml demo identities support-agent and support-agent-candidate
#   state/secrets/  local secret files: northwind-db (DATABASE_URL), northwind-gateway-key
# Keys come from the environment or ~/.env: GEMINI_API_KEY, TAVILY_API_KEY, OPENROUTER_API_KEY (optional).
set -euo pipefail
cd "$(dirname "$0")"
umask 077
STACK=$PWD
EXAMPLE=$(cd .. && pwd)
AB_DIR=$EXAMPLE/.agentbreeder
STATE=$AB_DIR/state
mkdir -p .secrets/secrets "$AB_DIR" "$STATE"
chmod 700 .secrets .secrets/secrets "$AB_DIR" "$STATE"
AGENTBREEDER=${AGENTBREEDER:-agentbreeder}
command -v "$AGENTBREEDER" >/dev/null || { echo "agentbreeder not on PATH (pip install -e the repo)" >&2; exit 2; }

read_env() {  # read_env NAME FILE
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1=//p" "$2" | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"
}
key() {  # key NAME [optional] -> writes .secrets/secrets/<name>
  local value=${!1:-}
  if [ -z "$value" ] && [ -f "$HOME/.env" ]; then value=$(read_env "$1" "$HOME/.env"); fi
  if [ -z "$value" ] && [ "${2:-}" != optional ]; then echo "Missing $1 (environment or ~/.env)" >&2; exit 2; fi
  printf '%s' "$value" > ".secrets/secrets/$(echo "$1" | tr 'A-Z' 'a-z')"
}
key GEMINI_API_KEY
key TAVILY_API_KEY
key OPENROUTER_API_KEY optional
[ -s .secrets/secrets/litellm_master_key ] || printf 'sk-%s' "$(openssl rand -hex 24)" > .secrets/secrets/litellm_master_key
[ -s .secrets/db-password ] || openssl rand -hex 24 > .secrets/db-password
if [ ! -s .secrets/phoenix.env ]; then
  {
    echo "PHOENIX_SECRET=s1$(openssl rand -hex 24)"
    echo "PHOENIX_ADMIN_SECRET=a1$(openssl rand -hex 24)"
    echo "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD=P1$(openssl rand -hex 12)"
  } > .secrets/phoenix.env
fi
echo "PHOENIX_POSTGRES_PASSWORD=$(cat .secrets/db-password)" > .secrets/phoenix-db.env
[ -f .secrets/phoenix-api-key ] || : > .secrets/phoenix-api-key
docker volume create agentbreeder-northwind-pg >/dev/null

export HOST_UID HOST_GID
HOST_UID=$(id -u)
HOST_GID=$(id -g)
docker compose up -d --build --wait postgres phoenix
if [ ! -s .secrets/phoenix-api-key ]; then
  ADMIN=$(read_env PHOENIX_ADMIN_SECRET .secrets/phoenix.env)
  curl -sf http://127.0.0.1:6006/graphql -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
    -d '{"query":"mutation { createSystemApiKey(input:{name:\"northwind-ingest\"}) { jwt } }"}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["createSystemApiKey"]["jwt"])' \
    > .secrets/phoenix-api-key
fi
docker compose up -d --build --wait

# --- AgentBreeder operator config for environment `dev` ---------------------------------------
cat > "$AB_DIR/principals.yaml" <<'YAML'
principals:
  support-agent:
    kind: agent
    enabled: true
    environment: dev
    review_by: "2027-06-30"
    sponsors: [{id: support-lead, name: support-lead@example.com, active: true}]
  support-agent-candidate:
    kind: agent
    enabled: true
    environment: dev
    review_by: "2027-06-30"
    sponsors: [{id: support-lead, name: support-lead@example.com, active: true}]
YAML
cat > "$AB_DIR/operator.yaml" <<YAML
enforcement: enforce
default_environment: dev
providers:
  demo: {type: static, authority: demo, file: principals.yaml}
gateways:
  litellm-local:
    type: litellm
    url: http://127.0.0.1:4000
    agent_url: http://host.docker.internal:4000
    master_key: file:$STACK/.secrets/secrets/litellm_master_key
environments:
  dev:
    targets: [local]
    gateway: litellm-local
    models: [support-model, support-embedding, support-judge]
    ceilings: {budget_usd: 50, rpm: 300}
    services:
      traces_url: http://host.docker.internal:8787/v1/traces
      tools_proxy_url: http://host.docker.internal:8787
    bindings:
      DATABASE_URL: secret://northwind-db
      LLM_GATEWAY_KEY: secret://northwind-gateway-key
    agents:
      northwind-support:
        identity: {provider: demo, ref: support-agent, environment: dev}
        candidate_identity: {provider: demo, ref: support-agent-candidate, environment: dev}
YAML
chmod 600 "$AB_DIR/operator.yaml" "$AB_DIR/principals.yaml"

# --- local secret files (values never printed) --------------------------------------------------
printf 'postgresql://support:%s@host.docker.internal:5433/support' "$(cat .secrets/db-password)" \
  | "$AGENTBREEDER" secret set northwind-db --state-dir "$STATE" >/dev/null
# Until per-deployment key minting lands (#725 slice 6), the stack mints one scoped key.
MASTER="Authorization: Bearer $(cat .secrets/secrets/litellm_master_key)"
if [ ! -s "$STATE/secrets/northwind-gateway-key" ] || ! curl -sf http://127.0.0.1:4000/key/info \
     -H "Authorization: Bearer $(cat "$STATE/secrets/northwind-gateway-key")" >/dev/null; then
  curl -sf http://127.0.0.1:4000/key/delete -H "$MASTER" -H 'content-type: application/json' \
    -d '{"key_aliases":["northwind-support-agent"]}' >/dev/null 2>&1 || true
  python3 -c 'import json; print(json.dumps({"key_alias": "northwind-support-agent",
    "models": ["support-model", "support-embedding"], "max_budget": 25.0,
    "budget_duration": "30d", "rpm_limit": 120}))' \
    | curl -sf http://127.0.0.1:4000/key/generate -H "$MASTER" -H 'content-type: application/json' -d @- \
    | python3 -c 'import json,sys; sys.stdout.write(json.load(sys.stdin)["key"])' \
    | "$AGENTBREEDER" secret set northwind-gateway-key --state-dir "$STATE" >/dev/null
fi

cat <<EOF

Northwind stack is up. Now:
  export AGENTBREEDER_OPERATOR_CONFIG=$AB_DIR/operator.yaml AGENTBREEDER_STATE_DIR=$STATE
  cd $EXAMPLE
  agentbreeder build agent.yaml --environment dev
  agentbreeder deploy deploy/northwind-support.deploy.yaml --local --environment dev
  Ops dashboard  http://127.0.0.1:8790   Phoenix  http://127.0.0.1:6006   Gateway  http://127.0.0.1:4000
EOF
