/**
 * Voice Activity Detection (VAD) state and control handlers.
 */

window.vadEnabled = localStorage.getItem("voice_of_luna_vad") !== "false";
window.vadSpeechDetected = false;
window.vadSilenceStartTime = null;
window.speechStartTime = null;
window.VAD_VOLUME_THRESHOLD = 0.055;
window.VAD_SILENCE_TIMEOUT_MS = 450;
window.VAD_MIN_SPEECH_DURATION_MS = 350;

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
