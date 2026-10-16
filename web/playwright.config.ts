import { defineConfig } from "@playwright/test";

// Smoke tests run against a live console (and the API behind it):
//   make start-api && make web   then   npm run test:e2e
// They use the Chrome you already have installed, so there is no browser download.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  use: { baseURL: process.env.PULSE_WEB_URL ?? "http://localhost:3100", channel: "chrome", trace: "retain-on-failure" },
});
