import { test } from "node:test";
import assert from "node:assert/strict";
import { fromSteps, toSteps } from "./sequence.ts";

const STEPS = [
  { id: "nudge", channel: "in_app", subject: "Hi", body: "Open the app", format: "text" },
  { id: "mail", channel: "email", subject: "Missed", body: "Still here", format: "markdown", delay: "10m", when: { status: "unread" } },
];

test("a definition survives a round trip through the builder (aliases are normalised)", () => {
  const out = toSteps(fromSteps(STEPS));
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], { id: "nudge", channel: "in_app", subject: "Hi", body: "Open the app", format: "text" });
  assert.deepEqual(out[1], { id: "mail", channel: "email", subject: "Missed", body: "Still here", format: "markdown", delay: "10m", when: { status: "not_delivered" } });
});

test("keys the builder does not edit are preserved", () => {
  const [s] = toSteps(fromSteps([{ id: "a", channel: "sms", body: "x", format: "text", future_option: { keep: true } }]));
  assert.deepEqual(s.future_option, { keep: true });
});

test("a saved template step carries no inline message", () => {
  const [s] = toSteps(fromSteps([{ id: "a", channel: "email", template: "welcome" }]));
  assert.deepEqual(s, { id: "a", channel: "email", template: "welcome" });
});

test("payload checks parse their values by operator", () => {
  const steps = fromSteps([{ id: "a", channel: "in_app", body: "x" }, {
    id: "b", channel: "email", body: "y", when: { step: "a", status: "failed", payload: [
      { field: "plan", op: "eq", value: "pro" }, { field: "seats", op: "gte", value: 5 },
      { field: "tier", op: "in", value: ["gold", "silver"] }, { field: "vip", op: "exists" }, { field: "on", op: "eq", value: true },
    ] },
  }]);
  assert.deepEqual(steps[1].checks.map(c => c.value), ["pro", "5", "gold, silver", "", "true"]);
  const when = toSteps(steps)[1].when as Record<string, unknown>;
  assert.equal(when.step, "a");
  assert.deepEqual(when.payload, [
    { field: "plan", op: "eq", value: "pro" }, { field: "seats", op: "gte", value: 5 },
    { field: "tier", op: "in", value: ["gold", "silver"] }, { field: "vip", op: "exists" }, { field: "on", op: "eq", value: true },
  ]);
});

test("delays: plain numbers stay numbers, durations stay text, blanks are dropped", () => {
  const out = toSteps(fromSteps([{ id: "a", channel: "in_app", body: "x" }, { id: "b", channel: "email", body: "y", delay: 90 }, { id: "c", channel: "sms", body: "z", delay: "2h" }]));
  assert.equal("delay" in out[0], false);
  assert.equal(out[1].delay, 90);
  assert.equal(out[2].delay, "2h");
});

test("a condition on a step with no status is not emitted", () => {
  const steps = fromSteps([{ id: "a", channel: "in_app", body: "x" }, { id: "b", channel: "email", body: "y" }]);
  steps[1].condStep = "a";   // a step id alone means nothing without a status to test
  assert.equal("when" in toSteps(steps)[1], false);
});
