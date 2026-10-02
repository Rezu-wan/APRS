# --- Build stage -----------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# API base URL is a PUBLIC value (inlined into the JS bundle by Vite).
# It is supplied at build time; no secrets are ever baked into this image.
ARG VITE_API_BASE_URL=http://localhost:8000/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# --- Serve stage -----------------------------------------------------------
# Unprivileged nginx variant: the server runs as uid 101 (non-root) and
# listens on 8080, so the running container never needs root privileges.
FROM nginxinc/nginx-unprivileged:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1

EXPOSE 8080
