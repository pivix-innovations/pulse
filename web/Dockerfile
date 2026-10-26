# Pulse console: static files served by nginx, which proxies /api and /health to the Pulse API.
# Multi-stage: Node only builds the site; the final image is just nginx and the exported files.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# STATIC_EXPORT=1 writes plain files to out/ (see next.config.ts); the API proxy is nginx's job at runtime.
ENV STATIC_EXPORT=1
RUN npm run build

FROM nginxinc/nginx-unprivileged:alpine
# The API the console talks to, resolved when the container starts (see nginx.conf.template).
ENV PULSE_API_ORIGIN=http://host.docker.internal:9000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 3100
