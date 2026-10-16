import { expect, type Page } from "@playwright/test";

// The seed script's development account (`make seed-admin`); override for any other environment.
export const EMAIL = process.env.PULSE_E2E_EMAIL ?? "admin@pulse.local";
export const PASSWORD = process.env.PULSE_E2E_PASSWORD ?? "admin12345";

export async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/overview/);
}

/** Collects page errors and failed API calls so a test can assert the page was healthy. */
export function watch(page: Page) {
  const problems: string[] = [];
  page.on("pageerror", e => problems.push(`pageerror: ${e.message}`));
  page.on("response", r => {
    const url = new URL(r.url());
    const signedOutProbe = url.pathname === "/api/v1/auth/me" && r.status() === 401;   // the login page asks "who am I?" first
    if (url.pathname.startsWith("/api/") && r.status() >= 400 && !signedOutProbe) problems.push(`${r.status()} ${r.request().method()} ${url.pathname}`);
  });
  return problems;
}

/** GET through the signed-in session, acting as the first tenant (what the console's switcher defaults to). */
export async function apiGet<T = unknown>(page: Page, path: string): Promise<T> {
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const res = await page.request.get(`/api/v1${path}`, { headers: { "X-Tenant-Id": me.memberships[0].tenant_id } });
  expect(res.ok()).toBeTruthy();
  return res.json();
}

/** Run a request as the signed-in user, acting as the first tenant. */
export async function apiSend(page: Page, method: "POST" | "DELETE", path: string, data?: unknown) {
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  return page.request.fetch(`/api/v1${path}`, { method, data, headers: { "X-Tenant-Id": me.memberships[0].tenant_id } });
}
