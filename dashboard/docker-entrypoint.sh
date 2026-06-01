#!/bin/sh
# Render the nginx config from env at container start, then run the CMD.
#
# Runs as the non-root `nginx` user (see Dockerfile), so the built-in
# nginx:alpine /docker-entrypoint.d mechanism (root-only) is bypassed in favour
# of this explicit entrypoint. Only ${API_UPSTREAM} and ${LISTEN_PORT} are
# substituted so nginx's own runtime vars ($host, $uri, ...) survive untouched.
set -e

: "${API_UPSTREAM:=http://api:8000}"
: "${LISTEN_PORT:=3001}"
export API_UPSTREAM LISTEN_PORT

envsubst '${API_UPSTREAM} ${LISTEN_PORT}' \
    < /etc/nginx/templates/default.conf.template \
    > /etc/nginx/conf.d/default.conf

exec "$@"
