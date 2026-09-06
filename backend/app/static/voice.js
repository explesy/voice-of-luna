/**
 * Voice of Luna — Personal Voice Interface
 * Reactive audio processing, state machine, and UX controllers
 */

let recorder = null;
let audioChunks = [];
let activePlayer = null;
let audioContext = null;
let analyserNode = null;
let micSourceNode = null;
let animationFrameId = null;
let isMuted = false;

// Audio level visualization
function initAudioAnalyser(stream) {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    audioContext = new AudioCtx();
    micSourceNode = audioContext.createMediaStreamSource(stream);
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 256;
    analyserNode.smoothingTimeConstant = 0.5;
    micSourceNode.connect(analyserNode);

    const dataArray = new Uint8Array(analyserNode.frequencyBinCount);

    function updateVolume() {
      if (!analyserNode) return;
      analyserNode.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) {
        sum += dataArray[i];
      }
      const average = sum / dataArray.length;
      const normalizedVolume = Math.min(1, Math.max(0, average / 128));

      // Update CSS custom property on orb container and document
      const orbContainer = document.querySelector(".orb-container");
      if (orbContainer) {
        orbContainer.style.setProperty("--volume", normalizedVolume.toFixed(3));
      }
      animationFrameId = requestAnimationFrame(updateVolume);
    }

    updateVolume();
  } catch (error) {
    console.warn("Web Audio API analyser could not start:", error);
  }
}

function stopAudioAnalyser() {
  if (animationFrameId) {
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }
  if (micSourceNode) {
    try { micSourceNode.disconnect(); } catch (_) {}
    micSourceNode = null;
  }
  if (analyserNode) {
    try { analyserNode.disconnect(); } catch (_) {}
    analyserNode = null;
  }
  if (audioContext && audioContext.state !== "closed") {
    try { audioContext.close(); } catch (_) {}
    audioContext = null;
  }
  const orbContainer = document.querySelector(".orb-container");
  if (orbContainer) {
    orbContainer.style.setProperty("--volume", "0");
  }
}

// UI State Management
function setVoiceState(state, statusMessage, modeLabel) {
  const stage = document.querySelector(".voice-stage");
  const modePill = document.querySelector("[data-mode-pill]");
  const statusEl = document.querySelector("[data-voice-status]");
  const recordBtns = document.querySelectorAll("[data-record]");
  const stopBtns = document.querySelectorAll("[data-stop-speaking]");

  if (stage) {
    stage.classList.remove("state-idle", "state-listening", "state-thinking", "state-speaking", "state-muted");
    stage.classList.add(`state-${state}`);
  }

  if (modePill && modeLabel) {
    modePill.textContent = modeLabel;
  }

  if (statusEl && statusMessage) {
    statusEl.textContent = statusMessage;
  }

  // Update Record Button UI
  recordBtns.forEach((btn) => {
    const isPrimary = btn.classList.contains("deck-btn-primary");
    const micIcon = btn.querySelector(".icon-mic");
    const stopIcon = btn.querySelector(".icon-stop");

    if (state === "listening") {
      btn.dataset.recording = "true";
      if (isPrimary) {
        btn.classList.add("recording");
        if (micIcon) micIcon.style.display = "none";
        if (stopIcon) stopIcon.style.display = "block";
      }
      btn.setAttribute("title", "Остановить запись (Пробел)");
    } else {
      btn.dataset.recording = "false";
      if (isPrimary) {
        btn.classList.remove("recording");
        if (micIcon) micIcon.style.display = "block";
        if (stopIcon) stopIcon.style.display = "none";
      }
      btn.setAttribute("title", "Запись (Пробел)");
    }
  });

  // Update Stop Speaking Button highlight
  stopBtns.forEach((btn) => {
    if (state === "speaking") {
      btn.classList.add("is-active");
    } else {
      btn.classList.remove("is-active");
    }
  });
}

function showToast(message) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 2800);
}

function languageFor(text) {
  return /\p{Script=Cyrillic}/u.test(text) ? "ru-RU" : "en-US";
}

function voiceFor(language) {
  if (!window.speechSynthesis) return null;
  const voices = window.speechSynthesis.getVoices();
  const normalizedLanguage = language.toLowerCase();
  const languageFamily = normalizedLanguage.split("-")[0];
  return (
    voices.find((voice) => voice.lang.toLowerCase() === normalizedLanguage) ||
    voices.find((voice) => voice.lang.toLowerCase().startsWith(`${languageFamily}-`))
  );
}

function stopSpeaking() {
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  if (activePlayer) {
    try {
      activePlayer.pause();
      activePlayer.currentTime = 0;
    } catch (_) {}
    activePlayer = null;
  }
  setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
}

function speakLatestResponse() {
  const responses = document.querySelectorAll("[data-spoken-response]");
  const latest = responses[responses.length - 1];
  if (!latest || latest.dataset.spoken) return;
  latest.dataset.spoken = "true";

  const localAudio = latest.querySelector("[data-server-audio]");
  if (localAudio) {
    activePlayer = localAudio;
    setVoiceState("speaking", "Luna отвечает… Нажмите Esc для прерывания", "Говорю…");

    localAudio.addEventListener("play", () => {
      setVoiceState("speaking", "Luna отвечает… Нажмите Esc для прерывания", "Говорю…");
    }, { once: true });

    localAudio.addEventListener("ended", () => {
      activePlayer = null;
      setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
    }, { once: true });

    localAudio.play().catch(() => {
      setVoiceState("idle", "Нажмите Play в транскрипте, чтобы услышать ответ", "Готово к разговору");
    });
    return;
  }

  if (!window.speechSynthesis) {
    setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
    return;
  }

  window.speechSynthesis.cancel();
  const text = latest.querySelector(".message-text")?.textContent || latest.textContent;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = languageFor(text);
  utterance.voice = voiceFor(utterance.lang) || null;

  utterance.addEventListener("start", () => {
    setVoiceState("speaking", "Luna отвечает… Нажмите Esc для прерывания", "Говорю…");
  });

  utterance.addEventListener("end", () => {
    setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
  });

  utterance.addEventListener("error", (event) => {
    if (event.error !== "canceled" && event.error !== "interrupted") {
      setVoiceState("idle", `Ошибка воспроизведения: ${event.error}`, "Ошибка звука");
    } else {
      setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
    }
  });

  window.speechSynthesis.speak(utterance);
}

function replayLatestResponse() {
  // Stop ongoing audio
  stopSpeaking();

  const responses = document.querySelectorAll("[data-spoken-response]");
  const latest = responses[responses.length - 1];
  if (!latest) {
    showToast("Нет реплик для повтора");
    return;
  }

  showToast("Повтор ответа");
  const localAudio = latest.querySelector("[data-server-audio]");
  if (localAudio) {
    activePlayer = localAudio;
    localAudio.currentTime = 0;
    setVoiceState("speaking", "Повтор ответа Luna…", "Говорю…");
    localAudio.play().catch(() => {
      setVoiceState("idle", "Не удалось воспроизвести аудио", "Ошибка");
    });
    return;
  }

  if (!window.speechSynthesis) return;
  const text = latest.querySelector(".message-text")?.textContent || latest.textContent;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = languageFor(text);
  utterance.voice = voiceFor(utterance.lang) || null;

  utterance.addEventListener("start", () => {
    setVoiceState("speaking", "Повтор ответа Luna…", "Говорю…");
  });
  utterance.addEventListener("end", () => {
    setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
  });
  window.speechSynthesis.speak(utterance);
}

async function startRecording(recordBtn) {
  if (isMuted) {
    showToast("Микрофон отключен. Включите микрофон кнопкой Mute.");
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setVoiceState("idle", "Запись не поддерживается данным браузером", "Недоступно");
    showToast("Запись звука не поддерживается в этом браузере");
    return;
  }

  // BARGE-IN: If Luna is currently speaking, immediately cut off speech
  stopSpeaking();

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    initAudioAnalyser(stream);

    audioChunks = [];
    recorder = new MediaRecorder(stream);

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) audioChunks.push(event.data);
    });

    recorder.addEventListener("stop", async () => {
      stopAudioAnalyser();
      stream.getTracks().forEach((track) => track.stop());

      setVoiceState("transcribing", "Распознавание речи локальным Whisper…", "Распознаю…");
      const audioUrl = recordBtn.dataset.audioUrl || document.querySelector("[data-record]")?.dataset.audioUrl;
      await sendRecording(audioUrl, recorder.mimeType || "audio/webm");
    });

    recorder.start();
    setVoiceState("listening", "Слушаю… Нажмите снова или пробел для завершения", "Слушаю…");
  } catch (error) {
    stopAudioAnalyser();
    setVoiceState("idle", `Микрофон недоступен: ${error.message}`, "Ошибка микрофона");
    showToast(`Ошибка доступа к микрофону: ${error.message}`);
  }
}

async function stopRecording() {
  if (recorder && recorder.state === "recording") {
    recorder.stop();
  }
}

async function sendRecording(url, type) {
  if (!url) return;
  setVoiceState("thinking", "Модель обрабатывает ваш запрос…", "Думаю…");

  const body = new FormData();
  body.append("audio", new Blob(audioChunks, { type }), "recording.webm");

  try {
    const response = await fetch(url, { method: "POST", body });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const html = await response.text();
    const conversationEl = document.querySelector("#conversation");
    if (conversationEl) {
      conversationEl.innerHTML = html;
    }
    setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
    speakLatestResponse();
    scrollTranscriptToBottom();
  } catch (error) {
    setVoiceState("idle", `Ошибка голосового сообщения: ${error.message}`, "Ошибка");
    showToast(`Ошибка отправки: ${error.message}`);
  }
}

function toggleTranscriptDrawer() {
  const drawer = document.getElementById("transcript-drawer");
  if (!drawer) return;
  const isHidden = drawer.hasAttribute("hidden");
  if (isHidden) {
    drawer.removeAttribute("hidden");
    scrollTranscriptToBottom();
  } else {
    drawer.setAttribute("hidden", "");
  }
}

function scrollTranscriptToBottom() {
  const body = document.getElementById("transcript-body");
  if (body) {
    body.scrollTop = body.scrollHeight;
  }
}

function copyTranscript() {
  const messages = document.querySelectorAll("#transcript-body .message");
  if (!messages.length) {
    showToast("Транскрипт пуст");
    return;
  }

  let textLines = [];
  messages.forEach((msg) => {
    const role = msg.classList.contains("user") ? "Вы" : "Luna";
    const content = msg.querySelector(".message-text")?.textContent?.trim() || msg.textContent.trim();
    textLines.push(`${role}:\n${content}\n`);
  });

  const fullText = textLines.join("\n");
  navigator.clipboard.writeText(fullText).then(
    () => showToast("Транскрипт скопирован в буфер обмена"),
    () => showToast("Не удалось скопировать транскрипт")
  );
}

function toggleTextInput() {
  const panel = document.getElementById("text-panel");
  if (!panel) return;
  const isHidden = panel.hasAttribute("hidden");
  if (isHidden) {
    panel.removeAttribute("hidden");
    const textarea = panel.querySelector("textarea");
    if (textarea) textarea.focus();
  } else {
    panel.setAttribute("hidden", "");
  }
}

function toggleMute() {
  isMuted = !isMuted;
  const muteBtns = document.querySelectorAll("[data-toggle-mute]");

  if (isMuted && recorder && recorder.state === "recording") {
    stopRecording();
  }

  muteBtns.forEach((btn) => {
    const unmutedIcon = btn.querySelector(".icon-unmuted");
    const mutedIcon = btn.querySelector(".icon-muted");
    if (unmutedIcon && mutedIcon) {
      unmutedIcon.style.display = isMuted ? "none" : "block";
      mutedIcon.style.display = isMuted ? "block" : "none";
    }
  });

  if (isMuted) {
    showToast("Микрофон отключен");
    setVoiceState("muted", "Микрофон отключен (Muted)", "Muted");
  } else {
    showToast("Микрофон включен");
    setVoiceState("idle", "Нажмите микрофон или пробел, чтобы говорить", "Готово к разговору");
  }
}

// Global Click Dispatcher
document.addEventListener("click", (event) => {
  // Record Trigger (Main deck button or Luna Orb)
  const recordTrigger = event.target.closest("[data-record]");
  if (recordTrigger) {
    if (recorder?.state === "recording") {
      stopRecording();
    } else {
      startRecording(recordTrigger);
    }
    return;
  }

  // Stop Speaking Button (Barge-in manually)
  if (event.target.closest("[data-stop-speaking]")) {
    stopSpeaking();
    return;
  }

  // Replay Last Response
  if (event.target.closest("[data-replay]")) {
    replayLatestResponse();
    return;
  }

  // Toggle Mute
  if (event.target.closest("[data-toggle-mute]")) {
    toggleMute();
    return;
  }

  // Toggle Transcript
  if (event.target.closest("[data-toggle-transcript]")) {
    toggleTranscriptDrawer();
    return;
  }

  // Close Transcript
  if (event.target.closest("[data-close-transcript]")) {
    const drawer = document.getElementById("transcript-drawer");
    if (drawer) drawer.setAttribute("hidden", "");
    return;
  }

  // Copy Transcript
  if (event.target.closest("[data-copy-transcript]")) {
    copyTranscript();
    return;
  }

  // Toggle Text Input
  if (event.target.closest("[data-toggle-text-btn]")) {
    toggleTextInput();
    return;
  }

  // Help Dialog Open
  if (event.target.closest("#help-btn")) {
    const dialog = document.getElementById("help-dialog");
    if (dialog) dialog.showModal();
    return;
  }

  // Help Dialog Close
  if (event.target.closest("#close-help-btn")) {
    const dialog = document.getElementById("help-dialog");
    if (dialog) dialog.close();
    return;
  }
});

// Keyboard Navigation
document.addEventListener("keydown", (event) => {
  const activeTag = document.activeElement?.tagName?.toLowerCase();
  const isInputFocused = activeTag === "textarea" || activeTag === "input";

  // Escape: Close modals, close transcript, or stop speaking
  if (event.key === "Escape") {
    const helpDialog = document.getElementById("help-dialog");
    if (helpDialog?.open) {
      helpDialog.close();
      return;
    }
    const drawer = document.getElementById("transcript-drawer");
    if (drawer && !drawer.hasAttribute("hidden")) {
      drawer.setAttribute("hidden", "");
      return;
    }
    stopSpeaking();
    return;
  }

  // Spacebar: Toggle recording when not in input
  if (event.code === "Space" && !isInputFocused) {
    event.preventDefault();
    const recordBtn = document.querySelector("[data-record]");
    if (recordBtn) {
      if (recorder?.state === "recording") {
        stopRecording();
      } else {
        startRecording(recordBtn);
      }
    }
    return;
  }

  // 'T' / 't': Toggle transcript when not in input
  if ((event.key === "t" || event.key === "T" || event.key === "е" || event.key === "Е") && !isInputFocused) {
    event.preventDefault();
    toggleTranscriptDrawer();
    return;
  }

  // Enter inside textarea: submit form directly (Shift+Enter for newline)
  if (event.key === "Enter" && !event.shiftKey && activeTag === "textarea") {
    const form = document.activeElement.closest("form");
    if (form) {
      event.preventDefault();
      form.requestSubmit();
    }
  }
});

// HTMX Lifecycle Hooks
document.body.addEventListener("htmx:beforeRequest", (event) => {
  stopSpeaking();
  if (recorder && recorder.state === "recording") {
    stopRecording();
  }
  if (event.detail.elt.tagName === "FORM") {
    setVoiceState("thinking", "Модель обрабатывает ваш запрос…", "Думаю…");
  }
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "conversation") {
    speakLatestResponse();
    scrollTranscriptToBottom();
  }
});

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
  // Pre-load voices if speech synthesis is available
  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    window.speechSynthesis.addEventListener("voiceschanged", () => {
      window.speechSynthesis.getVoices();
    });
  }
});
