import { expect, test } from "@playwright/test";

async function preparePage(page) {
  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
    HTMLMediaElement.prototype.pause = function () {};
  });
  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({ type: "ready", locale: "ru-RU", live_transcript_available: false, live_transcript: false }));
  });
  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
}

test("stop during streaming keeps the assistant text", async ({ page }) => {
  await preparePage(page);

  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "delta", delta: "Частичный ответ" }) });
    stopSpeaking();
  });

  const entry = page.locator(".log-entry.assistant");
  await expect(entry).toHaveCount(1);
  await expect(entry.locator(".log-text")).toHaveText("Частичный ответ");
});

test("stop removes a still-empty assistant bubble", async ({ page }) => {
  await preparePage(page);

  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "delta", delta: "" }) });
    stopSpeaking();
  });

  await expect(page.locator(".log-entry.assistant")).toHaveCount(0);
});
