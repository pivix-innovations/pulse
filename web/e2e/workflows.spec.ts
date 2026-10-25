import { expect, test } from "@playwright/test";
import { apiGet, signIn } from "./helpers";

test("a sequence workflow built visually is saved with its delay and condition", async ({ page }) => {
  const stamp = Date.now();
  const name = `e2e-seq-${stamp}`;
  await signIn(page);
  await page.goto("/workflows");
  page.on("dialog", d => d.accept());

  await page.getByRole("button", { name: "+ New workflow" }).click();
  const modal = page.getByRole("dialog");
  await modal.getByPlaceholder("welcome").fill(name);
  await modal.getByPlaceholder("user.signed_up").fill(`e2e.seq.${stamp}`);
  await modal.getByRole("button", { name: "Make it a sequence…" }).click();

  await expect(modal.getByLabel("Delay before step 2")).toHaveValue("10m");
  await modal.getByLabel("Delay before step 2").fill("2h");
  await modal.getByRole("button", { name: "+ Add a check" }).click();
  await modal.getByLabel("Field").fill("plan");
  await modal.getByLabel("Value").fill("pro");
  await modal.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Workflow created")).toBeVisible();

  try {
    const wf = (await apiGet<{ name: string; definition: { steps: { channel: string; delay?: string; when?: unknown }[] } }[]>(page, "/workflows")).find(w => w.name === name)!;
    const mail = wf.definition.steps[1];
    expect(wf.definition.steps.map(s => s.channel)).toEqual(["in_app", "email"]);
    expect(mail.delay).toBe("2h");
    expect(mail.when).toEqual({ status: "not_delivered", payload: [{ field: "plan", op: "eq", value: "pro" }] });
  } finally {
    const row = page.getByRole("row", { name: new RegExp(name) });
    await row.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Workflow deleted")).toBeVisible();
  }
});
