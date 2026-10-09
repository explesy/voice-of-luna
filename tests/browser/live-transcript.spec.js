import { expect, test } from "@playwright/test";

test("interim transcript is replaced by the final authoritative transcript", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({
      type: "ready",
      locale: "ru-RU",
      live_transcript_available: true,
      live_transcript: true,
    }));
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();

  await page.evaluate(() => {
    handleSocketMessage({
      data: JSON.stringify({ type: "stt_partial", provider: "t-one", text: "привет как", interim: true, final: false }),
    });
  });

  const interim = page.locator(".log-entry.user.interim");
  await expect(interim).toHaveCount(1);
  await expect(interim.locator(".log-text")).toHaveText("привет как");

  await page.evaluate(() => {
    handleSocketMessage({
      data: JSON.stringify({ type: "transcript", role: "user", text: "Привет, как дела?" }),
    });
  });

  // The interim entry is reused and finalized: exactly one user entry remains.
  await expect(page.locator(".log-entry.user")).toHaveCount(1);
  await expect(page.locator(".log-entry.user.interim")).toHaveCount(0);
  await expect(page.locator(".log-entry.user .log-text")).toHaveText("Привет, как дела?");
  expect(pageErrors).toEqual([]);
});

test("interim transcript is cleared on error and on empty final transcript", async ({ page }) => {
  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({
      type: "ready",
      locale: "ru-RU",
      live_transcript_available: true,
      live_transcript: true,
    }));
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();

  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "stt_partial", text: "частично" }) });
  });
  await expect(page.locator(".log-entry.user.interim")).toHaveCount(1);

  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "error", message: "boom" }) });
  });
  await expect(page.locator(".log-entry.user.interim")).toHaveCount(0);
  await expect(page.locator(".log-entry.user")).toHaveCount(0);

  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "stt_partial", text: "снова" }) });
  });
  await expect(page.locator(".log-entry.user.interim")).toHaveCount(1);

  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "transcript", role: "user", text: "" }) });
  });
  await expect(page.locator(".log-entry.user")).toHaveCount(0);
});

test("live transcript toggle follows streaming availability and syncs preference", async ({ page }) => {
  const sent = [];
  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.onMessage((raw) => {
      try {
        sent.push(JSON.parse(raw));
      } catch (_) {
        // ignore binary frames
      }
    });
    socket.send(JSON.stringify({
      type: "ready",
      locale: "ru-RU",
      live_transcript_available: true,
      live_transcript: true,
    }));
  });

  await page.goto("/");
  const toggle = page.locator("#live-transcript-toggle");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect
    .poll(() => sent.some((message) => message.type === "set_settings" && message.live_transcript === false))
    .toBe(true);
});

test("live transcript toggle is hidden when no streaming model is available", async ({ page }) => {
  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({
      type: "ready",
      locale: "ru-RU",
      live_transcript_available: false,
      live_transcript: false,
    }));
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  await expect(page.locator("#live-transcript-toggle")).toBeHidden();
});
