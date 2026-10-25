import { expect, test } from "@playwright/test";
import { signIn, watch } from "./helpers";

const PAGES: [string, string][] = [
  ["/overview", "Overview"], ["/live", "Live Stream"], ["/inbox", "Inbox"], ["/notifications", "Notifications"],
  ["/events", "Events"], ["/dead-letters", "Dead Letters"], ["/send", "Send Event"], ["/workflows", "Workflows"],
  ["/templates", "Templates"], ["/providers", "Providers"], ["/preferences", "Preferences"], ["/suppressions", "Suppressions"],
  ["/devices", "Devices"], ["/team", "Team"], ["/audit-log", "Audit Log"], ["/api-keys", "API Keys"],
  ["/tenants", "Tenants"], ["/users", "Users"],
];

test("signing out returns to the login page, and bad credentials are refused", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();

  await signIn(page);
  await page.getByRole("button", { name: /Platform Admin|@/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/workflows");
  await expect(page).toHaveURL(/\/login/);   // the session is really gone
});

test("every console page loads without errors", async ({ page }) => {
  const problems = watch(page);
  await signIn(page);
  for (const [path, title] of PAGES) {
    await page.goto(path);
    await expect(page.locator(".page-title")).toHaveText(title);
    await page.waitForLoadState("networkidle");
  }
  expect(problems).toEqual([]);
});
