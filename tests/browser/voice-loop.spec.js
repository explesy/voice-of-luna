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

test("selecting uninstalled model triggers download banner and polling UI", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.route("**/api/voice", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        active_voice: "Denis (Piper Neural · Offline)",
        auto_downloading: true,
        model_id: "piper_ru_denis",
      }),
    });
  });

  await page.route("**/api/tts/models/piper_ru_denis/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "piper_ru_denis",
        name: "Piper Denis (Medium)",
        status: "downloading",
        progress_percent: 42,
        downloaded_mb: 25.2,
        total_mb: 60.0,
        speed_kbps: 1024,
        eta_seconds: 35,
      }),
    });
  });

  await page.goto("/");
  await page.locator("#voice-select").selectOption("Denis (Piper Neural · Offline)");

  const banner = page.locator("#model-download-banner");
  await expect(banner).toBeVisible();
  await expect(page.locator("#download-pct")).toHaveText(/42%/);
  await expect(page.locator("#voice-download-badge")).toBeVisible();
  await expect(page.locator("#voice-download-badge")).toContainText("42%");

  expect(pageErrors).toEqual([]);
});
