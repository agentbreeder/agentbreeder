#!/bin/sh
# Load provider keys and the master key from Compose secret files into the environment that
# litellm's config reads (os.environ/...), then start the proxy. Missing optional keys
# (OpenRouter) leave that route configured but unusable; the Gemini route still works.
set -eu
read_secret() { [ -s "/run/secrets/$1" ] && cat "/run/secrets/$1" || printf ''; }
export GEMINI_API_KEY="$(read_secret gemini_api_key)"
export OPENROUTER_API_KEY="$(read_secret openrouter_api_key)"
export LITELLM_MASTER_KEY="$(read_secret litellm_master_key)"
export DATABASE_URL="postgresql://support:$(read_secret db_password)@postgres:5432/litellm"
[ -n "$OPENROUTER_API_KEY" ] || echo "litellm: no OpenRouter key; fallback route disabled" >&2
exec litellm --config /app/config/config.yaml --port 4000 "$@"
