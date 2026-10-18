#!/bin/sh
# Build the static console and deploy it to Cloudflare Pages (needs `wrangler login`).
# functions/ proxies /api/* and /health to the Pulse API so the session cookie stays same-origin.
set -eu
cd "$(dirname "$0")"
npm ci
STATIC_EXPORT=1 npm run build
wrangler pages deploy out --project-name pulse-dev-ui --branch main --commit-dirty=true
