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
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3001

CMD ["nginx", "-g", "daemon off;"]
