import { expect, test } from "@playwright/test";

function fakeResynthesis(requests) {
  return async (route) => {
    const body = route.request().postDataJSON();
    requests.push({ url: route.request().url(), body });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        clip_id: "clip-1",
        turn_id: "turn-1",
        audio_base64: "UklGRg==",
        mime_type: "audio/wav",
        text: "Ответ",
        voice: body.voice || "Milena",
        requested_tts_engine: "MACOS_SAY",
        tts_engine: "MACOS_SAY",
        set_default: Boolean(body.set_default),
        replay: true,
      }),
    });
  };
}

async function seedAssistantTurn(page) {
  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "transcript", role: "user", text: "Вопрос" }) });
    handleSocketMessage({
      data: JSON.stringify({ type: "turn_completed", turn_id: "turn-1", turn: { role: "assistant", text: "Ответ" } }),
    });
  });
}

test("pause button toggles the paused playback state", async ({ page }) => {
  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({ type: "ready", locale: "ru-RU", live_transcript_available: false }));
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  await seedAssistantTurn(page);

  await page.evaluate(() => {
    window.pauseAudioPlayback = () => {
      window.isAudioPaused = true;
    };
    window.resumeAudioPlayback = () => {
      window.isAudioPaused = false;
    };
    isAudioQueuePlaying = true;
    setVoiceState("speaking", "Luna responding", "SPEAKING // STREAM");
  });

  const pauseBtn = page.locator(".log-entry.assistant .bubble-transport [data-bubble-pause]");
  await expect(pauseBtn).toBeVisible();
  await pauseBtn.click();
  await expect(page.locator(".radar-stage")).toHaveClass(/state-paused/);
  await expect(pauseBtn.locator(".pause-text")).toHaveText("RESUME");

  await pauseBtn.click();
  await expect(page.locator(".radar-stage")).toHaveClass(/state-speaking/);
  await expect(pauseBtn.locator(".pause-text")).toHaveText("PAUSE");

  // Barge-in while paused stops everything immediately.
  await pauseBtn.click();
  await expect(page.locator(".radar-stage")).toHaveClass(/state-paused/);
  await page.locator(".log-entry.assistant .bubble-transport [data-bubble-stop]").click();
  await expect(page.locator(".radar-stage")).toHaveClass(/state-idle/);
  expect(await page.evaluate(() => window.isAudioPaused)).toBe(false);
});

test("replay re-synthesizes through the server and marks the output event as replay", async ({ page }) => {
  const sent = [];
  const requests = [];

  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
    HTMLMediaElement.prototype.play = function () {
      return Promise.resolve();
    };
    HTMLMediaElement.prototype.pause = function () {};
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.onMessage((raw) => {
      try {
        sent.push(JSON.parse(raw));
      } catch (_) {
        // ignore binary frames
      }
    });
    socket.send(JSON.stringify({ type: "ready", locale: "ru-RU", voice: "Milena", live_transcript_available: false }));
  });
  await page.route("**/api/conversations/*/turns/*/resynthesize", fakeResynthesis(requests));

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  const chosenVoice = await page.evaluate(() => {
    const select = document.querySelector("#voice-select");
    const option = Array.from(select.options).find((candidate) => candidate.value && !candidate.value.startsWith("__"));
    if (option) select.value = option.value;
    return select.value || null;
  });
  await seedAssistantTurn(page);

  await page.locator(".log-entry.assistant").last().locator("[data-replay]").click();

  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].url).toContain("/turns/1/resynthesize");
  expect(requests[0].body.set_default).toBe(false);
  expect(requests[0].body.voice).toBe(chosenVoice);
  await expect
    .poll(() => sent.some((message) => message.type === "output_event" && message.replay === true))
    .toBe(true);
});

test("revoice controls keep working after an htmx conversation swap", async ({ page }) => {
  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
  });
  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({ type: "ready", locale: "ru-RU", voice: "Milena", live_transcript_available: true, live_transcript: true }));
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  await expect(page.locator("#live-transcript-toggle")).toBeVisible();

  // Emulate the command form's innerHTML swap that replaces the dialog node.
  await page.evaluate(() => {
    const conversation = document.querySelector("#conversation");
    conversation.innerHTML = conversation.innerHTML;
    document.body.dispatchEvent(new CustomEvent("htmx:afterSwap", { detail: { target: conversation } }));
  });

  await seedAssistantTurn(page);
  await page.locator(".log-entry.assistant").last().locator("[data-revoice]").click();
  await expect(page.locator("#revoice-dialog")).toBeVisible();
  await expect(page.locator("#revoice-voice-select option")).not.toHaveCount(0);
  // The LIVE toggle also survives the swap.
  await expect(page.locator("#live-transcript-toggle")).toBeVisible();
});

test("revoice opens a voice picker and can set the session default", async ({ page }) => {
  const requests = [];

  await page.addInitScript(() => {
    window.speechSynthesis = { cancel() {}, speak() {} };
    HTMLMediaElement.prototype.play = function () {
      return Promise.resolve();
    };
    HTMLMediaElement.prototype.pause = function () {};
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({ type: "ready", locale: "ru-RU", voice: "Milena", live_transcript_available: false }));
  });
  await page.route("**/api/conversations/*/turns/*/resynthesize", fakeResynthesis(requests));

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  await seedAssistantTurn(page);

  await page.locator(".log-entry.assistant").last().locator("[data-revoice]").click();
  const dialog = page.locator("#revoice-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.locator("#revoice-voice-select option")).not.toHaveCount(0);

  await page.locator("#revoice-voice-select").selectOption({ index: 1 });
  const chosen = await page.locator("#revoice-voice-select").inputValue();
  await page.locator("#revoice-set-default").check();
  await page.locator("#revoice-speak").click();

  await expect(dialog).toBeHidden();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].url).toContain("/turns/1/resynthesize");
  expect(requests[0].body.voice).toBe(chosen);
  expect(requests[0].body.set_default).toBe(true);
  await expect(page.locator("#voice-select")).toHaveValue(chosen);
});
