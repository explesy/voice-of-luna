import { expect, test } from "@playwright/test";

test("text turn, settings, and fake microphone stay on one browser session", async ({ page }) => {
  const sent = [];
  const pageErrors = [];
  const consoleErrors = [];

  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(raw);
      sent.push(message);

      if (message.type === "text") {
        socket.send(JSON.stringify({ type: "status", state: "thinking", message: "Processing" }));
        socket.send(JSON.stringify({ type: "transcript", text: message.text }));
        socket.send(JSON.stringify({ type: "delta", delta: "Mock browser response." }));
        socket.send(JSON.stringify({ type: "turn_completed", timing: { llm_first_delta_ms: 1 } }));
      }
    });
    socket.send(JSON.stringify({
      type: "ready",
      locale: "ru-RU",
      model: "gpt-5.6-luna",
      effort: "low",
      voice: "Svetlana (Neural · Edge)",
      tts_engine: "edge",
    }));
  });

  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
    window.MediaRecorder = class {
      constructor() { this.state = "inactive"; }
      start() { this.state = "recording"; }
      stop() { this.state = "inactive"; this.onstop?.(); }
      addEventListener() {}
    };
    navigator.mediaDevices.getUserMedia = async () => ({
      getTracks: () => [{ stop() {} }],
    });
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  await expect(page.locator("#voice-select")).toHaveValue("Svetlana (Neural · Edge)");

  await page.locator("#locale-select").selectOption("en-US");
  await page.locator("#session-plugin-select").selectOption("project_room");
  await page.locator("#message").fill("hello from browser smoke");
  await page.locator("#message").press("Enter");

  await expect.poll(() => sent.filter((message) => message.type === "text").length).toBe(1);
  await expect(page.locator(".log-entry.assistant .log-text")).toContainText("Mock browser response.");

  const recordButton = page.locator(".radar-trigger").first();
  await recordButton.click();
  await expect(page.locator(".radar-stage")).toHaveClass(/state-listening/);
  await recordButton.click();

  await expect(page.locator(".btn-record")).toHaveCount(0);
  await expect(page.locator("[data-replay]")).toHaveCount(0);

  expect(sent.filter((message) => message.type === "set_settings").length).toBeGreaterThan(0);
  expect(sent.filter((message) => message.type === "set_plugin").length).toBeGreaterThan(0);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
