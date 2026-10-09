import { expect, test } from "@playwright/test";

test("a server-delivered answer is not read again by the browser fallback", async ({ page }) => {
  await page.addInitScript(() => {
    window.__spoken = [];
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel = () => {};
      window.speechSynthesis.speak = (utterance) => {
        window.__spoken.push(utterance.text);
      };
    }
  });

  await page.routeWebSocket(/\/ws\/conversations\//, (socket) => {
    socket.send(JSON.stringify({ type: "ready", locale: "ru-RU", live_transcript_available: false, live_transcript: false }));
  });

  await page.goto("/");
  await expect(page.locator("[data-conversation-id]")).toBeVisible();
  // Headless Chromium has no Russian voice; steer the fallback to English so it
  // actually reaches speechSynthesis.speak.
  await page.evaluate(() => {
    window.languageFor = () => "en-US";
    window.voiceFor = () => null;
  });

  // First answer arrives and is delivered by the server audio.
  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "delta", delta: "Первый ответ" }) });
    handleSocketMessage({
      data: JSON.stringify({
        type: "audio_chunk",
        clip_id: "clip-1",
        turn_id: "turn-1",
        audio_url: "/speech/clip-1",
        audio_base64: "UklGRg==",
        mime_type: "audio/wav",
        text: "Первый ответ",
      }),
    });
    handleSocketMessage({
      data: JSON.stringify({ type: "turn_completed", turn_id: "turn-1", turn: { role: "assistant", text: "Первый ответ" } }),
    });
  });

  // A later browser-TTS fallback must not re-read the delivered answer.
  await page.evaluate(() => speakLatestResponse());
  expect(await page.evaluate(() => window.__spoken)).toEqual([]);

  // A fresh answer is still eligible for the fallback path.
  await page.evaluate(() => {
    handleSocketMessage({ data: JSON.stringify({ type: "delta", delta: "Второй ответ" }) });
  });
  await page.evaluate(() => speakLatestResponse());

  const spoken = await page.evaluate(() => window.__spoken);
  expect(spoken).toContain("Второй ответ");
  expect(spoken).not.toContain("Первый ответ");
});
