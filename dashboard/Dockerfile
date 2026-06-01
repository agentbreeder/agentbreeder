# Build stage uses node:22-slim (Debian glibc) — node:22-alpine (musl) was
# causing esbuild's Go daemon to die mid-transformation with EPIPE during
# `vite build` inside Docker (#411). glibc avoids the musl/seccomp interaction
# and keeps vite + esbuild stable across BuildKit + Docker Desktop on macOS.
FROM --platform=$BUILDPLATFORM node:22-slim AS build

WORKDIR /app

# Bound esbuild's Node-side memory so a runaway worker can't be OOM-killed by
# the BuildKit container before it can report a real error.
ENV NODE_OPTIONS="--max-old-space-size=4096"

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# Production stage — nginx serves static files + proxies /api to backend
FROM nginx:alpine AS production

LABEL org.opencontainers.image.source="https://github.com/agentbreeder/agentbreeder"
LABEL org.opencontainers.image.description="AgentBreeder Dashboard"

COPY --from=build /app/dist /usr/share/nginx/html
# nginx config is rendered from env at startup (API_UPSTREAM / LISTEN_PORT) so
# the same image self-hosts anywhere — see docker-entrypoint.sh + self-hosting docs.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.sh /docker-entrypoint.sh

# Drop root: nginx:alpine already has a `nginx` user (UID 101). The pid file
# lives at /var/run/nginx.pid by default — make it writable by that user so
# the master process can start. Cache + log dirs are nginx-only writes too, and
# the entrypoint writes the rendered config into /etc/nginx/conf.d.
# Listening on 3001 (≥1024) means CAP_NET_BIND_SERVICE is not required.
RUN chmod +x /docker-entrypoint.sh \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx /etc/nginx/conf.d \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 3001

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
