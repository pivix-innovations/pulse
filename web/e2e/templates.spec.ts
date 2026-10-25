import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("create a template, preview it with sample data, publish it, then delete it", async ({ page }) => {
  const name = `e2e-tpl-${Date.now()}`;
  await signIn(page);
  await page.goto("/templates");
  page.on("dialog", d => d.accept());

  await page.getByRole("button", { name: "+ New template" }).click();
  const modal = page.getByRole("dialog");
  await modal.getByPlaceholder("welcome-email").fill(name);
  await modal.getByPlaceholder("Welcome, {{ name }}!").fill("Hi {{ name }}");
  await modal.getByPlaceholder("Hi {{ name }}, …").fill("Welcome aboard, {{ name }}.");
  await modal.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Template created")).toBeVisible();

  const row = page.getByRole("row", { name: new RegExp(name) });
  try {
    await row.getByRole("button", { name: "Preview" }).click();
    const preview = page.getByRole("dialog");
    await preview.locator("textarea").fill('{ "name": "Ada" }');
    await preview.getByRole("button", { name: "Render" }).click();
    await expect(preview.getByText("Hi Ada")).toBeVisible();
    await expect(preview.getByText("Welcome aboard, Ada.")).toBeVisible();
    await preview.getByRole("button", { name: "Close" }).click();

    await row.getByRole("button", { name: "Publish" }).click();
    await expect(page.getByText("Published v1")).toBeVisible();
    await expect(row.getByText("v1")).toBeVisible();
  } finally {
    await row.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Template deleted")).toBeVisible();
  }
});
