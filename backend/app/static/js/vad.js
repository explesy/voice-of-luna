/**
 * Voice Activity Detection (VAD) state and control handlers.
 */

window.vadEnabled = localStorage.getItem("voice_of_luna_vad") !== "false";
window.vadSpeechDetected = false;
window.vadSilenceStartTime = null;
window.speechStartTime = null;
window.VAD_VOLUME_THRESHOLD = 0.055;
window.VAD_MIN_START_THRESHOLD = 0.035;
window.VAD_START_MARGIN = 0.018;
window.VAD_STOP_MARGIN = 0.008;
window.VAD_SILENCE_TIMEOUT_MS = 450;
window.VAD_MIN_SPEECH_DURATION_MS = 350;
window.vadNoiseFloor = null;
window.vadTraceEnabled = localStorage.getItem("voice_of_luna_vad_trace") === "true";
window.vadTrace = [];

function recordVadTraceSample(normalizedVolume, vadThreshold, speechDetected, silenceStart, event = null, stopThreshold = null, noiseFloor = null) {
  if (!window.vadTraceEnabled) return;
  if (window.vadTrace.length >= 20000) window.vadTrace.shift();
  window.vadTrace.push({
    timestamp_ms: Math.round(performance.now()),
    normalizedVolume: Number(normalizedVolume.toFixed(4)),
    vadThreshold: Number(vadThreshold.toFixed(4)),
    stopThreshold: stopThreshold == null ? null : Number(stopThreshold.toFixed(4)),
    noiseFloor: noiseFloor == null ? null : Number(noiseFloor.toFixed(4)),
    speechDetected: Boolean(speechDetected),
    silenceStart: silenceStart == null ? null : Math.round(silenceStart),
    ...(event ? { event } : {}),
  });
}

function setVadTraceCapture(enabled) {
  window.vadTraceEnabled = Boolean(enabled);
  localStorage.setItem("voice_of_luna_vad_trace", window.vadTraceEnabled ? "true" : "false");
  if (window.vadTraceEnabled) window.vadTrace = [];
}

function getVadTrace() {
  return window.vadTrace.map((entry) => ({ ...entry }));
}

function downloadVadTrace() {
  const samples = getVadTrace();
  const blob = new Blob([JSON.stringify({ version: "vad-trace-v2", session_id: crypto.randomUUID(), parameters: { threshold: window.VAD_VOLUME_THRESHOLD, min_start_threshold: window.VAD_MIN_START_THRESHOLD, start_margin: window.VAD_START_MARGIN, stop_margin: window.VAD_STOP_MARGIN, silence_timeout_ms: window.VAD_SILENCE_TIMEOUT_MS, min_speech_ms: window.VAD_MIN_SPEECH_DURATION_MS }, traces: [{ id: "session", samples }] }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `voice-of-luna-vad-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

window.recordVadTraceSample = recordVadTraceSample;
window.setVadTraceCapture = setVadTraceCapture;
window.getVadTrace = getVadTrace;
window.downloadVadTrace = downloadVadTrace;

function toggleVad() {
  window.vadEnabled = !window.vadEnabled;
  localStorage.setItem("voice_of_luna_vad", window.vadEnabled ? "true" : "false");
  const btn = document.querySelector("#vad-toggle");
  if (btn) {
    const textEl = btn.querySelector(".vad-state-text");
    if (window.vadEnabled) {
      btn.classList.add("is-auto");
      btn.classList.remove("is-manual");
      if (textEl) textEl.textContent = "AUTO";
      btn.setAttribute("title", "Voice Activity Detection: AUTO (Click or press [V] to toggle) [V]");
      if (typeof showToast === "function") showToast("// VAD: AUTO (auto-stop on silence)");
    } else {
      btn.classList.remove("is-auto");
      btn.classList.add("is-manual");
      if (textEl) textEl.textContent = "MANUAL";
      btn.setAttribute("title", "Voice Activity Detection: MANUAL (Click or press [V] to toggle) [V]");
      if (typeof showToast === "function") showToast("// VAD: MANUAL (push-to-talk)");
    }
  }
}

function initVadToggle() {
  const btn = document.querySelector("#vad-toggle");
  if (!btn) return;

  function updateVadUi() {
    const textEl = btn.querySelector(".vad-state-text");
    if (window.vadEnabled) {
      btn.classList.add("is-auto");
      btn.classList.remove("is-manual");
      if (textEl) textEl.textContent = "AUTO";
      btn.setAttribute("title", "Voice Activity Detection: AUTO (Click or press [V] to toggle) [V]");
    } else {
      btn.classList.remove("is-auto");
      btn.classList.add("is-manual");
      if (textEl) textEl.textContent = "MANUAL";
      btn.setAttribute("title", "Voice Activity Detection: MANUAL (Click or press [V] to toggle) [V]");
    }
  }

  updateVadUi();

  if (btn.dataset.initialized) return;
  btn.dataset.initialized = "true";

  btn.addEventListener("click", () => {
    toggleVad();
  });
}

window.toggleVad = toggleVad;
window.initVadToggle = initVadToggle;
