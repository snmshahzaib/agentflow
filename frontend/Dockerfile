# syntax=docker/dockerfile:1.7
#
# Builds the static bundle and serves it from nginx. The API is a separate
# origin in compose, reached through the nginx proxy below so the browser sees
# one origin and the session cookie behaves as it does in production.

# ---------- builder ----------------------------------------------------------
FROM node:24-alpine AS builder

RUN corepack enable && corepack prepare pnpm@9.1.0 --activate

WORKDIR /app

# Lockfile-only layer, so a source change does not reinstall dependencies.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
# Empty base URL: the bundle calls /api on its own origin, which nginx proxies.
ENV VITE_API_BASE_URL=""
RUN pnpm build

# ---------- runtime ----------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=4 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
