import type { NextConfig } from "next";

// The console is an authenticated, client-driven app: every request goes to the
// Pulse API with the session cookie. Proxy /api to FastAPI so the browser stays
// same-origin (cookies, SSE) in dev and in production.
const API_ORIGIN = process.env.PULSE_API_ORIGIN ?? "http://localhost:9000";

const nextConfig: NextConfig = {
  // FastAPI routes like /health/ carry a trailing slash; do not redirect it away.
  skipTrailingSlashRedirect: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
      { source: "/health", destination: `${API_ORIGIN}/health/` },
    ];
  },
};

export default nextConfig;
