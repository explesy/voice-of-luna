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

test("live transcript chip offers the streaming model download and enables when ready", async ({ page }) => {
  const downloadRequests = [];
  let modelReady = false;
  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({
      type: "ready",
      locale: "ru-RU",
      live_transcript_available: false,
      live_transcript: false,
      live_transcript_model: {
        id: "tone_ru",
        name: "T-One Russian (streaming)",
        status: "not_installed",
        installed: false,
        progress_percent: 0,
        size_mb: 122.5,
      },
    }));
  });
  await page.route("**/api/stt/models/tone_ru/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "tone_ru",
        name: "T-One Russian (streaming)",
        status: modelReady ? "ready" : "downloading",
        installed: modelReady,
        progress_percent: modelReady ? 100 : 42,
        size_mb: 122.5,
      }),
    });
  });
  await page.route("**/api/stt/models/tone_ru/download", async (route) => {
    downloadRequests.push(route.request().url());
    modelReady = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, model_id: "tone_ru", status: "downloading" }) });
  });

  await page.goto("/");
  const chip = page.locator("#live-transcript-toggle");
  await expect(chip).toBeVisible();
  await expect(chip.locator(".live-transcript-state-text")).toContainText("↓");
  await chip.click();
  await expect.poll(() => downloadRequests.length).toBe(1);
  await expect.poll(() => chip.getAttribute("aria-pressed"), { timeout: 8000 }).toBe("true");
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
