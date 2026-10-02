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
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
