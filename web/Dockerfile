# Pulse console: a standalone Node service that proxies /api to the Pulse API.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# The rewrite target is baked in at build time (next.config.ts), so pass it as a build arg.
ARG PULSE_API_ORIGIN=http://localhost:9000
ENV PULSE_API_ORIGIN=$PULSE_API_ORIGIN
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3100 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3100
CMD ["node", "server.js"]
