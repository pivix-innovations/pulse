import { expect, test } from "@playwright/test";
import { apiSend, signIn } from "./helpers";

// Needs the API only (fan-out creates the attempts and the scheduled steps); no workers.
test("send an event, find it by search, then cancel the sequence steps still waiting", async ({ page }) => {
  const stamp = Date.now();
  const eventType = `e2e.send.${stamp}`;
  await signIn(page);
  page.on("dialog", d => d.accept());

  const created = await apiSend(page, "POST", "/workflows", {
    name: `e2e-send-${stamp}`, event_type: eventType, is_active: true,
    definition: { steps: [
      { id: "nudge", channel: "in_app", subject: "Hi", body: "Open the app", format: "text" },
      { id: "mail", channel: "email", subject: "Missed", body: "Still here", format: "text", delay: "1d", when: { status: "unread" } },
    ] },
  });
  expect(created.status()).toBe(201);
  const workflowId = (await created.json()).id;

  try {
    await page.goto("/send");
    await page.getByPlaceholder("user.signed_up").fill(eventType);
    await page.getByPlaceholder("quotient").fill("e2e");
    await page.getByRole("button", { name: "Send event" }).click();
    await expect(page.getByText("Notification created")).toBeVisible();
    await page.getByRole("link", { name: /Inspect delivery/ }).click();

    await expect(page.getByText("Delivery attempts (1)")).toBeVisible();
    await expect(page.getByText("Sequence steps (2)")).toBeVisible();

    // Search finds it by event type, and not under an event type it does not have.
    await page.goto("/notifications");
    await page.getByLabel("Event type").fill(eventType);
    await expect(page.getByText("Matching notifications")).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await page.getByLabel("Event type").fill(`${eventType}.nope`);
    await expect(page.getByText("Nothing matches these filters.")).toBeVisible();
    await page.getByLabel("Event type").fill(eventType);
    await page.locator("tbody tr").first().click();

    await page.getByRole("button", { name: "Cancel remaining steps" }).click();
    await expect(page.getByText("Cancelled 1 waiting step")).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel remaining steps" })).toHaveCount(0);
    await expect(page.getByText("cancelled by an operator")).toBeVisible();
  } finally {
    await apiSend(page, "DELETE", `/workflows/${workflowId}`);
  }
});
