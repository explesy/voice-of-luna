/**
 * Voice of Luna — Terminal Audio & Voice Controller
 */

let recorder = null;
let isRecordingActive = false;
let activeRecordingStream = null;
let audioChunks = [];
let pcmProcessorNode = null;
let pcmSamples = [];
let audioWorkletNode = null;
let audioWorkletModuleLoaded = false;
let audioContext = null;
let analyserNode = null;
let micSourceNode = null;
let animationFrameId = null;
let speechEndDetectedAt = null;
let streamingPcmUpload = false;
let pendingStreamingText = "";
let streamingTextFrame = null;
let interimTranscriptEntry = null;
let liveTranscriptAvailable = false;
let liveTranscriptEnabled = true;
let liveTranscriptModel = null;
let liveTranscriptPollTimer = null;
let liveTranscriptPollingId = null;
let lastVoiceUiState = "idle";

// Audio processing and VAD state are provided by audio-player.js and vad.js

// Audio level visualization & 16kHz PCM capture via Web Audio API
async function initAudioAnalyser(stream) {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    audioContext = new AudioCtx();
    micSourceNode = audioContext.createMediaStreamSource(stream);
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 256;
    analyserNode.smoothingTimeConstant = 0.5;
    micSourceNode.connect(analyserNode);

    // Setup direct PCM capture (prefer AudioWorklet, fallback to ScriptProcessor)
    pcmSamples = [];
    let workletInitialized = false;

    if (audioContext.audioWorklet && window.AudioWorkletNode) {
      try {
        if (!audioContext._workletLoaded) {
          await audioContext.audioWorklet.addModule("/static/pcm-recorder-processor.js");
          audioContext._workletLoaded = true;
        }
        audioWorkletNode = new AudioWorkletNode(audioContext, "pcm-recorder-processor");
        audioWorkletNode.port.onmessage = (event) => {
          if (event.data && event.data.type === "pcm_data" && event.data.buffer) {
            const samples = new Float32Array(event.data.buffer);
            pcmSamples.push(samples);
            sendStreamingPcmFrame(samples);
          }
        };
        micSourceNode.connect(audioWorkletNode);
        audioWorkletNode.connect(audioContext.destination);
        workletInitialized = true;
      } catch (err) {
        console.warn("// AudioWorklet registration failed, falling back to ScriptProcessor:", err);
      }
    }

    if (!workletInitialized && audioContext.createScriptProcessor) {
      try {
        pcmProcessorNode = audioContext.createScriptProcessor(4096, 1, 1);
        pcmProcessorNode.onaudioprocess = (event) => {
          const input = event.inputBuffer.getChannelData(0);
          const samples = new Float32Array(input);
          pcmSamples.push(samples);
          sendStreamingPcmFrame(samples);
        };
        micSourceNode.connect(pcmProcessorNode);
        pcmProcessorNode.connect(audioContext.destination);
        workletInitialized = true;
      } catch (err) {
        console.warn("// ScriptProcessor failed, falling back to MediaRecorder:", err);
      }
    }

    window.isDirectPcmActive = workletInitialized;

    const dataArray = new Float32Array(analyserNode.fftSize);

    function updateVolume() {
      if (!analyserNode) return;
      analyserNode.getFloatTimeDomainData(dataArray);
      let sumSquares = 0;
      for (let i = 0; i < dataArray.length; i++) {
        sumSquares += dataArray[i] * dataArray[i];
      }
      const rms = Math.sqrt(sumSquares / dataArray.length);
      // Scale RMS to retain a familiar 0..1 visual range while using the raw
      // time-domain signal for a microphone-independent VAD noise estimate.
      const normalizedVolume = Math.min(1, Math.max(0, rms * 2.5));

      const stage = document.querySelector(".radar-stage");
      if (stage) {
        stage.style.setProperty("--volume", normalizedVolume.toFixed(3));
      }

      // VAD (Voice Activity Detection) during active recording
      if (window.vadEnabled && isRecordingActive) {
        const now = performance.now();
        if (window.vadNoiseFloor == null) window.vadNoiseFloor = normalizedVolume;
        const wasSpeaking = Boolean(window.vadSpeechDetected);
        if (!wasSpeaking || normalizedVolume < window.vadNoiseFloor + (window.VAD_START_MARGIN || 0.018)) {
          window.vadNoiseFloor = (window.vadNoiseFloor * 0.96) + (normalizedVolume * 0.04);
        }
        const noiseFloor = window.vadNoiseFloor;
        const threshold = Math.max(window.VAD_MIN_START_THRESHOLD || 0.035, noiseFloor + (window.VAD_START_MARGIN || 0.018));
        const stopThreshold = Math.max(0.02, noiseFloor + (window.VAD_STOP_MARGIN || 0.008));
        const minDuration = window.VAD_MIN_SPEECH_DURATION_MS || 350;
        const silenceTimeout = window.VAD_SILENCE_TIMEOUT_MS || 450;

        if (normalizedVolume >= threshold) {
          if (!window.vadSpeechDetected) {
            window.vadSpeechDetected = true;
            window.speechStartTime = now;
          }
          window.vadSilenceStartTime = null;
          speechEndDetectedAt = null;
        } else if (window.vadSpeechDetected && normalizedVolume < stopThreshold && window.speechStartTime && (now - window.speechStartTime) >= minDuration) {
          if (!window.vadSilenceStartTime) {
            window.vadSilenceStartTime = now;
            speechEndDetectedAt = now;
          } else if (now - window.vadSilenceStartTime >= silenceTimeout) {
            console.log("// VAD auto-stop: silence detected for", Math.round(now - window.vadSilenceStartTime), "ms");
            if (typeof window.recordVadTraceSample === "function") {
              window.recordVadTraceSample(normalizedVolume, threshold, window.vadSpeechDetected, window.vadSilenceStartTime, "automaticStop", stopThreshold, noiseFloor);
            }
            window.vadSpeechDetected = false;
            window.vadSilenceStartTime = null;
            window.speechStartTime = null;
            stopRecording();
            return;
          }
        }
        if (typeof window.recordVadTraceSample === "function") {
          window.recordVadTraceSample(normalizedVolume, threshold, window.vadSpeechDetected, window.vadSilenceStartTime, null, stopThreshold, noiseFloor);
        }
      }

      animationFrameId = requestAnimationFrame(updateVolume);
    }

    updateVolume();
  } catch (error) {
    console.warn("Web Audio API analyser error:", error);
  }
}

function sendStreamingPcmFrame(samples) {
  if (!streamingPcmUpload || !socket || socket.readyState !== WebSocket.OPEN || !samples?.length) return;
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  socket.send(pcm.buffer);
}

function stopAudioAnalyser() {
  window.vadSpeechDetected = false;
  window.vadSilenceStartTime = null;
  window.speechStartTime = null;
  window.vadNoiseFloor = null;
  if (animationFrameId) {
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }
  if (audioWorkletNode) {
    try {
      audioWorkletNode.port.postMessage({ command: "stop" });
      audioWorkletNode.disconnect();
    } catch (_) {}
    audioWorkletNode = null;
  }
  if (pcmProcessorNode) {
    try { pcmProcessorNode.disconnect(); } catch (_) {}
    pcmProcessorNode = null;
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
  const stage = document.querySelector(".radar-stage");
  if (stage) {
    stage.style.setProperty("--volume", "0");
  }
}

// UI State Management
function setVoiceState(state, statusMessage, modeLabel) {
  const stateMap = { ready: "idle", idle: "idle", listening: "listening", transcribing: "transcribing", thinking: "thinking", speaking: "speaking", paused: "paused", error: "error" };
  const uiState = stateMap[state] || "error";
  const stage = document.querySelector(".radar-stage");
  const modePill = document.querySelector("[data-mode-pill]");
  const statusEl = document.querySelector("[data-voice-status]");
  const recordBtns = document.querySelectorAll("[data-record]");
  const stopBtns = document.querySelectorAll("[data-stop-speaking]");

  if (stage) {
    stage.classList.remove("state-idle", "state-listening", "state-transcribing", "state-thinking", "state-speaking", "state-paused", "state-error");
    stage.classList.add(`state-${uiState}`);
  }

  if (modePill && modeLabel) {
    modePill.textContent = modeLabel;
  }

  if (statusEl && statusMessage) {
    statusEl.textContent = statusMessage;
  }

  // Update Record Buttons
  recordBtns.forEach((btn) => {
    const isCmd = btn.classList.contains("btn-record");
    const recText = btn.querySelector(".rec-text");

    if (state === "listening") {
      btn.dataset.recording = "true";
      if (isCmd) {
        btn.classList.add("recording");
        if (recText) recText.textContent = "STOP";
      }
      btn.setAttribute("title", "Stop Recording [Space]");
    } else {
      btn.dataset.recording = "false";
      if (isCmd) {
        btn.classList.remove("recording");
        if (recText) recText.textContent = "REC";
      }
      btn.setAttribute("title", "Start Recording [Space]");
    }
  });

  // Highlight Stop Speaking Button
  stopBtns.forEach((btn) => {
    if (uiState === "speaking" || uiState === "paused") {
      btn.classList.add("is-active");
      btn.hidden = false;
    } else {
      btn.classList.remove("is-active");
      btn.hidden = true;
    }
  });

  // Playback transport lives under the latest assistant bubble.
  lastVoiceUiState = uiState;
  updateBubbleTransportVisibility(uiState);
}

function togglePauseSpeaking() {
  if (window.isAudioPaused) {
    if (typeof resumeAudioPlayback === "function") resumeAudioPlayback();
    if (!window.isAudioPaused) {
      setVoiceState("speaking", "Luna responding... Press [Esc] to stop", "SPEAKING // STREAM");
    }
    return;
  }
  const isPlaying = typeof isAudioQueuePlaying !== "undefined" && isAudioQueuePlaying;
  if (!isPlaying && !(typeof activeScheduledSources !== "undefined" && activeScheduledSources.length > 0)) return;
  if (typeof pauseAudioPlayback === "function") pauseAudioPlayback();
  if (window.isAudioPaused) {
    setVoiceState("paused", "Paused. Press [P] to resume", "PAUSED // AUDIO");
  }
}

function updateFooterStatus(ttsEngine) {
  if (!ttsEngine) return;
  const footerEl = document.querySelector("[data-footer-meta]");
  if (footerEl) {
    let ttsLabel = ttsEngine;
    if (ttsEngine === "EDGE_TTS" || ttsEngine.includes("EDGE")) {
      ttsLabel = "EDGE_CLOUD";
    } else if (ttsEngine === "PIPER_OFFLINE" || ttsEngine.includes("PIPER")) {
      ttsLabel = "PIPER_LOCAL";
    } else if (ttsEngine === "SILERO_OFFLINE" || ttsEngine.includes("SILERO")) {
      ttsLabel = "SILERO_LOCAL";
    } else if (ttsEngine === "MACOS_SAY" || ttsEngine.includes("MACOS")) {
      ttsLabel = "MACOS_LOCAL";
    }
    footerEl.textContent = `STT:LOCAL // TTS:${ttsLabel} // LLM:CODEX`;
  }
}

function showToast(message) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 2200);
}

let cachedVoices = [];

function populateVoices() {
  if (typeof window === "undefined" || !window.speechSynthesis) return [];
  const voices = window.speechSynthesis.getVoices();
  if (voices && voices.length > 0) {
    cachedVoices = voices;
  }
  return cachedVoices.length > 0 ? cachedVoices : (voices || []);
}

if (typeof window !== "undefined" && window.speechSynthesis) {
  populateVoices();
  window.speechSynthesis.onvoiceschanged = populateVoices;
  window.speechSynthesis.addEventListener("voiceschanged", populateVoices);
}

function normalizeLocale(tag) {
  return (tag || "").toLowerCase().replace(/_/g, "-");
}

function languageFor(text) {
  if (/\p{Script=Cyrillic}/u.test(text)) return "ru-RU";
  if (/[áéíóúüñ¿¡]/i.test(text)) return "es-ES";
  return "en-US";
}

function voiceFor(language) {
  if (!window.speechSynthesis) return null;
  const voices = populateVoices();
  if (!voices || voices.length === 0) return null;

  const targetLang = normalizeLocale(language);
  const targetFamily = targetLang.split("-")[0];

  const preferredVoiceName = (
    document.querySelector("#voice-select")?.value ||
    document.querySelector("[data-russian-voice]")?.dataset.russianVoice ||
    document.body.dataset.russianVoice ||
    localStorage.getItem("voice_of_luna_voice") ||
    "Milena"
  ).toLowerCase();

  // 1. If searching for Russian, prioritize configured voice name (e.g. Milena)
  if (targetFamily === "ru" && preferredVoiceName) {
    const preferred = voices.find((v) => {
      const vName = (v.name || "").toLowerCase();
      const vLang = normalizeLocale(v.lang);
      return vName.includes(preferredVoiceName) && (vLang.startsWith("ru") || !vLang || vLang.includes("ru"));
    });
    if (preferred) return preferred;

    const preferredByName = voices.find((v) => (v.name || "").toLowerCase().includes(preferredVoiceName));
    if (preferredByName) return preferredByName;
  }

  // 1b. If searching for Spanish, prioritize Spanish voices
  if (targetFamily === "es") {
    const preferred = voices.find((v) => {
      const vName = (v.name || "").toLowerCase();
      const vLang = normalizeLocale(v.lang);
      return (vName.includes(preferredVoiceName) || vName.includes("mónica") || vName.includes("monica") || vName.includes("paulina")) && vLang.startsWith("es");
    });
    if (preferred) return preferred;
  }

  // 2. Exact match (e.g. "ru-ru" === "ru-ru" or normalized "ru_RU")
  const exactMatch = voices.find((v) => normalizeLocale(v.lang) === targetLang);
  if (exactMatch) return exactMatch;

  // 3. Family match (e.g. "ru" or "es" or "es-mx")
  const familyMatch = voices.find((v) => {
    const vLang = normalizeLocale(v.lang);
    return vLang === targetFamily || vLang.startsWith(`${targetFamily}-`);
  });
  if (familyMatch) return familyMatch;

  // 4. Keyword match in name for Russian
  if (targetFamily === "ru") {
    const nameMatch = voices.find((v) =>
      /(milena|yuri|katya|tatyana|russian|русский)/i.test(v.name || "")
    );
    if (nameMatch) return nameMatch;
  }

  // 5. Fallback matching family substring
  const prefixMatch = voices.find((v) => normalizeLocale(v.lang).includes(targetFamily));
  if (prefixMatch) return prefixMatch;

  return null;
}

let socket = null;

function stopSpeaking() {
  if (currentStreamingEntry) {
    // Flush whatever already arrived, then keep the bubble if it has text.
    // STOP must only silence audio; it must not erase an answer the user
    // already saw. A still-empty bubble is dropped so no ghost remains.
    flushStreamingText();
    const textEl = currentStreamingEntry.querySelector(".log-text");
    const hasText = Boolean((textEl?.dataset?.rawText || textEl?.textContent || "").trim());
    if (!hasText) {
      currentStreamingEntry.remove();
    } else if (textEl) {
      textEl.dataset.rawText = textEl.textContent;
      formatTerminalText(textEl);
    }
    currentStreamingEntry = null;
    pendingStreamingText = "";
  }
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  if (typeof stopAudioPlayback === "function") {
    stopAudioPlayback();
  } else if (typeof window.stopAudioPlayback === "function") {
    window.stopAudioPlayback();
  }
  if (activePlayer) {
    try {
      activePlayer.pause();
      activePlayer.currentTime = 0;
    } catch (_) {}
    activePlayer = null;
  }
  firstAudioPlayTime = null;
  firstAudioSoundOffsetMs = null;
  if (socket && socket.readyState === WebSocket.OPEN) {
    try {
      socket.send(JSON.stringify({ type: "stop_speaking" }));
    } catch (_) {}
  }
  setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
}

function updateLatencyHud(timing, clientE2eMs) {
  const hud = document.querySelector("#latency-metrics");
  const val = document.querySelector("[data-latency-val]");
  if (!hud || !val) return;

  const parts = [];
  if (clientE2eMs) {
    parts.push(`⚡ ${(clientE2eMs / 1000).toFixed(2)}s e2e`);
  } else if (timing?.backend_first_audio_ms) {
    parts.push(`⚡ ${(timing.backend_first_audio_ms / 1000).toFixed(2)}s`);
  }
  if (timing?.stt_ms != null) parts.push(`STT: ${timing.stt_ms}ms`);
  if (timing?.client_endpoint_delay_ms != null) parts.push(`VAD: ${timing.client_endpoint_delay_ms}ms`);
  if (timing?.client_audio_encode_ms != null) parts.push(`ENC: ${timing.client_audio_encode_ms}ms`);
  if (timing?.server_audio_prep_ms != null) parts.push(`PREP: ${timing.server_audio_prep_ms}ms`);
  if (timing?.llm_first_delta_ms != null) parts.push(`LLM: ${timing.llm_first_delta_ms}ms`);
  if (timing?.tts_synthesis_first_chunk_ms != null) parts.push(`TTS: ${timing.tts_synthesis_first_chunk_ms}ms`);
  if (firstAudioSoundOffsetMs != null) parts.push(`ONSET: ${firstAudioSoundOffsetMs}ms`);

  if (parts.length > 0) {
    val.textContent = parts.join(" | ");
    hud.style.display = "inline-flex";
  }
}

function showInterimTranscript(text) {
  if (!text) return;
  const feed = document.getElementById("messages-feed");
  if (!feed) return;
  if (interimTranscriptEntry && !interimTranscriptEntry.isConnected) {
    interimTranscriptEntry = null;
  }

  const emptyConsole = feed.querySelector(".empty-console");
  if (emptyConsole) emptyConsole.remove();

  if (!interimTranscriptEntry) {
    interimTranscriptEntry = document.createElement("div");
    interimTranscriptEntry.className = "log-entry user interim";
    interimTranscriptEntry.dataset.interim = "true";
    interimTranscriptEntry.dataset.turnIndex = String(feed.querySelectorAll(".log-entry").length);

    const header = document.createElement("div");
    header.className = "log-header";
    const roleSpan = document.createElement("span");
    roleSpan.className = "log-role";
    roleSpan.textContent = "USER >";
    header.appendChild(roleSpan);

    const body = document.createElement("div");
    body.className = "log-body";
    const textDiv = document.createElement("div");
    textDiv.className = "log-text";
    body.appendChild(textDiv);

    interimTranscriptEntry.appendChild(header);
    interimTranscriptEntry.appendChild(body);
    feed.appendChild(interimTranscriptEntry);
  }

  const textEl = interimTranscriptEntry.querySelector(".log-text");
  if (textEl) textEl.textContent = text;
  scrollFeedToBottom();
}

function finalizeInterimTranscript(text) {
  if (interimTranscriptEntry && !interimTranscriptEntry.isConnected) {
    interimTranscriptEntry = null;
  }
  if (interimTranscriptEntry) {
    const entry = interimTranscriptEntry;
    interimTranscriptEntry = null;
    if (!text) {
      entry.remove();
      return null;
    }
    entry.classList.remove("interim");
    delete entry.dataset.interim;
    const textEl = entry.querySelector(".log-text");
    if (textEl) {
      textEl.dataset.rawText = text;
      textEl.textContent = text;
      formatTerminalText(textEl);
    }
    scrollFeedToBottom();
    return entry;
  }
  if (!text) return null;
  return appendMessageToFeed("user", text);
}

function clearInterimTranscript() {
  if (interimTranscriptEntry) {
    interimTranscriptEntry.remove();
    interimTranscriptEntry = null;
  }
}

function appendMessageToFeed(role, text) {
  const feed = document.getElementById("messages-feed");
  if (!feed) return null;

  const emptyConsole = feed.querySelector(".empty-console");
  if (emptyConsole) {
    emptyConsole.remove();
  }

  const entry = document.createElement("div");
  entry.className = `log-entry ${role}`;
  entry.dataset.turnIndex = String(feed.querySelectorAll(".log-entry").length);
  if (role === "assistant") {
    entry.dataset.spokenResponse = "true";
  }

  const header = document.createElement("div");
  header.className = "log-header";
  const roleSpan = document.createElement("span");
  roleSpan.className = "log-role";
  roleSpan.textContent = role === "user" ? "USER >" : "LUNA >";
  header.appendChild(roleSpan);

  const body = document.createElement("div");
  body.className = "log-body";
  const textDiv = document.createElement("div");
  textDiv.className = "log-text";
  if (text) {
    textDiv.dataset.rawText = text;
    formatTerminalText(textDiv);
  }
  body.appendChild(textDiv);

  entry.appendChild(header);
  entry.appendChild(body);

  if (role === "assistant") {
    body.appendChild(buildMessageControls());
  }

  feed.appendChild(entry);
  if (role === "assistant") updateBubbleTransportVisibility(lastVoiceUiState);

  scrollFeedToBottom();
  return entry;
}

function buildMessageControls() {
  const controls = document.createElement("div");
  controls.className = "log-audio";

  const replayBtn = document.createElement("button");
  replayBtn.type = "button";
  replayBtn.className = "btn-inline-replay";
  replayBtn.dataset.replay = "";
  replayBtn.title = "Replay this response";
  replayBtn.textContent = "↻ REPLAY";

  const revoiceBtn = document.createElement("button");
  revoiceBtn.type = "button";
  revoiceBtn.className = "btn-inline-replay";
  revoiceBtn.dataset.revoice = "";
  revoiceBtn.title = "Speak again with another voice";
  revoiceBtn.textContent = "🎙 REVOICE";

  controls.appendChild(replayBtn);
  controls.appendChild(revoiceBtn);
  controls.appendChild(buildBubbleTransport());
  return controls;
}

function buildBubbleTransport() {
  const transport = document.createElement("span");
  transport.className = "bubble-transport";
  transport.hidden = true;

  const pauseBtn = document.createElement("button");
  pauseBtn.type = "button";
  pauseBtn.className = "btn-inline-replay";
  pauseBtn.dataset.bubblePause = "";
  pauseBtn.title = "Pause/resume this playback (P)";
  const pauseText = document.createElement("span");
  pauseText.className = "pause-text";
  pauseText.textContent = "PAUSE";
  pauseBtn.appendChild(pauseText);

  const stopBtn = document.createElement("button");
  stopBtn.type = "button";
  stopBtn.className = "btn-inline-replay";
  stopBtn.dataset.bubbleStop = "";
  stopBtn.title = "Stop playback (Esc)";
  stopBtn.textContent = "■ STOP";

  transport.appendChild(pauseBtn);
  transport.appendChild(stopBtn);
  return transport;
}

function updateBubbleTransportVisibility(uiState) {
  document.querySelectorAll(".bubble-transport").forEach((el) => {
    el.hidden = true;
  });
  if (uiState !== "speaking" && uiState !== "paused") return;
  const entries = document.querySelectorAll(".log-entry.assistant");
  const last = entries[entries.length - 1];
  const transport = last?.querySelector(".bubble-transport");
  if (!transport) return;
  transport.hidden = false;
  const label = transport.querySelector(".pause-text");
  if (label) label.textContent = uiState === "paused" ? "RESUME" : "PAUSE";
}

function updateTurnsCount() {
  const countEl = document.querySelector("[data-turns-count]");
  const entries = document.querySelectorAll(".log-entry");
  if (countEl) {
    countEl.textContent = entries.length;
  }
}

function connectWebSocket() {
  const container = document.querySelector("[data-conversation-id]");
  const conversationId = container?.dataset.conversationId;
  if (!conversationId) return;

  if (socket) {
    if (
      socket._conversationId === conversationId &&
      (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    try {
      socket.close();
    } catch (_) {}
    socket = null;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/conversations/${conversationId}`;

  try {
    socket = new WebSocket(wsUrl);
    socket._conversationId = conversationId;
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      console.log("// websocket connected:", wsUrl);
      const currentVoice = document.querySelector("#voice-select")?.value;
      const currentModel = document.querySelector("#model-select")?.value;
      const currentEffort = document.querySelector("#effort-select")?.value;
      const currentPlugin = document.querySelector("#session-plugin-select, #plugin-select, .plugin-select")?.value;
      const currentMode = document.querySelector("#mode-select")?.value || "default";
      const savedLiveTranscript = localStorage.getItem("voice_of_luna_live_transcript");
      const settings = {
        voice: currentVoice,
        model: currentModel,
        effort: currentEffort,
        binary_audio: true,
      };
      if (savedLiveTranscript !== null) {
        settings.live_transcript = savedLiveTranscript !== "false";
      }
      socket.send(JSON.stringify({ type: "set_settings", ...settings }));
      if (currentPlugin) {
        socket.send(JSON.stringify({
          type: "set_plugin",
          plugin_id: currentPlugin,
          mode: currentMode,
        }));
      }
    };

    socket.onmessage = (event) => {
      handleSocketMessage(event);
    };

    socket.onclose = () => {
      socket = null;
      clearInterimTranscript();
      const currentContainer = document.querySelector("[data-conversation-id]");
      if (currentContainer?.dataset?.conversationId === conversationId) {
        setTimeout(connectWebSocket, 2500);
      }
    };

    socket.onerror = (err) => {
      console.warn("// websocket error:", err);
    };
  } catch (e) {
    console.warn("// websocket init failed:", e);
  }
}

function handleSocketMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    try {
      const view = new DataView(event.data);
      const magic = view.getUint8(0);
      if (magic === 0x01) {
        // Audio chunk frame: [0x01][2-byte header len L][JSON header bytes][Audio raw bytes]
        const headerLen = view.getUint16(1, false);
        const headerBytes = new Uint8Array(event.data, 3, headerLen);
        const decoder = new TextDecoder("utf-8");
        const meta = JSON.parse(decoder.decode(headerBytes));
        const audioBuffer = event.data.slice(3 + headerLen);
        if (!currentStreamingEntry && meta.text) {
          currentStreamingEntry = appendMessageToFeed("assistant", meta.text);
        }
        enqueueAudioChunk(
          meta.audio_url || (meta.clip_id ? `/speech/${meta.clip_id}` : null),
          currentStreamingEntry,
          null,
          meta.mime_type || "audio/wav",
          audioBuffer,
          meta.turn_id || null,
          meta.clip_id || null,
          meta.text || null
        );
      }
    } catch (e) {
      console.warn("// binary websocket frame parse error:", e);
    }
    return;
  }

  let data;
  try {
    data = JSON.parse(event.data);
  } catch (_) {
    return;
  }

  if (data.type === "ready") {
    if (data.locale) {
      const localeSelect = document.querySelector("#locale-select");
      if (localeSelect) localeSelect.value = data.locale;
      document.documentElement.lang = data.locale.startsWith("en") ? "en" : "ru";
    }
    if (data.model) {
      const modelSelect = document.querySelector("#model-select");
      if (modelSelect) modelSelect.value = data.model;
    }
    if (data.effort) {
      const effortSelect = document.querySelector("#effort-select");
      if (effortSelect) effortSelect.value = data.effort;
    }
    if (data.voice) {
      const voiceSelect = document.querySelector("#voice-select");
      if (voiceSelect) {
        voiceSelect.value = data.voice;
        updateVoiceAttributes(data.voice);
      }
    }
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
    applyLiveTranscriptState(data);
  } else if (data.type === "voice_updated") {
    if (data.voice) {
      const voiceSelect = document.querySelector("#voice-select");
      if (voiceSelect) {
        voiceSelect.value = data.voice;
        updateVoiceAttributes(data.voice);
      }
    }
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
  } else if (data.type === "locale_updated") {
    if (data.locale) {
      const localeSelect = document.querySelector("#locale-select");
      if (localeSelect) localeSelect.value = data.locale;
      document.documentElement.lang = data.locale.startsWith("en") ? "en" : "ru";
      localStorage.setItem("voice_of_luna_locale", data.locale);
    }
    if (data.voice) {
      const voiceSelect = document.querySelector("#voice-select");
      if (voiceSelect) {
        voiceSelect.value = data.voice;
        updateVoiceAttributes(data.voice);
      }
    }
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
    if (Object.prototype.hasOwnProperty.call(data, "live_transcript_model")) {
      applyLiveTranscriptState(data);
    }
  } else if (data.type === "settings_updated") {
    if (data.locale) {
      const localeSelect = document.querySelector("#locale-select");
      if (localeSelect) localeSelect.value = data.locale;
      document.documentElement.lang = data.locale.startsWith("en") ? "en" : "ru";
    }
    if (data.model) {
      const modelSelect = document.querySelector("#model-select");
      if (modelSelect) modelSelect.value = data.model;
    }
    if (data.effort) {
      const effortSelect = document.querySelector("#effort-select");
      if (effortSelect) effortSelect.value = data.effort;
    }
    if (data.voice) {
      const voiceSelect = document.querySelector("#voice-select");
      if (voiceSelect) {
        voiceSelect.value = data.voice;
        updateVoiceAttributes(data.voice);
      }
    }
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
    if (Object.prototype.hasOwnProperty.call(data, "live_transcript_model")) {
      applyLiveTranscriptState(data);
    }
  } else if (data.type === "plugin_updated") {
    if (Object.prototype.hasOwnProperty.call(data, "panel")) renderPluginPanel(data.panel, data.plugin_id, data.settings || {});
    if (data.plugin_id) {
      document.querySelectorAll(".plugin-select, #plugin-select, #session-plugin-select").forEach((el) => {
        el.value = data.plugin_id;
      });
      localStorage.setItem("voice_of_luna_plugin", data.plugin_id);
      if (typeof window._updatePluginModeOptions === "function") {
        window._updatePluginModeOptions(data.plugin_id, data.mode || "default");
      }
    }
    if (data.voice) {
      const voiceSelect = document.querySelector("#voice-select");
      if (voiceSelect) {
        voiceSelect.value = data.voice;
        updateVoiceAttributes(data.voice);
      }
    }
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
  } else if (data.type === "status") {
    if (data.turn_taking_profile === "patient") window.VAD_SILENCE_TIMEOUT_MS = 1800;
    else if (data.turn_taking_profile === "normal") window.VAD_SILENCE_TIMEOUT_MS = 450;
    setVoiceState(data.state, data.message, data.mode_label);
  } else if (data.type === "transcript") {
    flushStreamingText();
    finalizeInterimTranscript(data.text);
    currentStreamingEntry = null;
  } else if (data.type === "stt_partial") {
    if (data.text) {
      showInterimTranscript(data.text);
      const providerLabel = `STREAM // ${(data.provider || "stt").toUpperCase()}`;
      // The interim words live in the feed only; the sidebar status stays neutral.
      setVoiceState(
        "transcribing",
        "Listening...",
        data.final ? `${providerLabel} FINAL` : providerLabel,
      );
    }
  } else if (data.type === "delta") {
    if (!currentStreamingEntry) {
      currentStreamingEntry = appendMessageToFeed("assistant", "");
    }
    pendingStreamingText += data.delta || "";
    scheduleStreamingTextFlush();
  } else if (data.type === "audio_chunk") {
    if (!currentStreamingEntry && data.text) {
      currentStreamingEntry = appendMessageToFeed("assistant", data.text);
    }
    enqueueAudioChunk(data.audio_url, currentStreamingEntry, data.audio_base64, data.mime_type, null, data.turn_id || null, data.clip_id || null, data.text || null);
  } else if (data.type === "turn_completed") {
    clearInterimTranscript();
    flushStreamingText();
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
    if (data.timing) {
      latestTiming = data.timing;
      const clientE2e = firstAudioPlayTime && lastSpeechEndTime ? Math.round(firstAudioPlayTime - lastSpeechEndTime) : null;
      updateLatencyHud(data.timing, clientE2e);
    }
    if (currentStreamingEntry) {
      if (data.turn_id) currentStreamingEntry.dataset.turnId = data.turn_id;
      const textEl = currentStreamingEntry.querySelector(".log-text");
      if (textEl) {
        if (!textEl.textContent && data.turn?.text) {
          textEl.textContent = data.turn.text;
        }
        formatTerminalText(textEl);
      }
    } else if (data.turn && data.turn.text) {
      const appended = appendMessageToFeed(data.turn.role || "assistant", data.turn.text);
      if (appended && data.turn_id) appended.dataset.turnId = data.turn_id;
    }
    currentStreamingEntry = null;
    updateTurnsCount();
    updateBubbleTransportVisibility(lastVoiceUiState);
  } else if (data.type === "error") {
    clearInterimTranscript();
    if (currentStreamingEntry) {
      currentStreamingEntry.remove();
      currentStreamingEntry = null;
      pendingStreamingText = "";
    }
    showToast(`// error: ${data.message}`);
    setVoiceState("error", data.message, "ERR // SERVER");
  }
}

function scheduleStreamingTextFlush() {
  if (streamingTextFrame != null) return;
  streamingTextFrame = requestAnimationFrame(flushStreamingText);
}

function flushStreamingText() {
  if (streamingTextFrame != null) {
    cancelAnimationFrame(streamingTextFrame);
    streamingTextFrame = null;
  }
  if (!pendingStreamingText || !currentStreamingEntry) return;
  const textEl = currentStreamingEntry.querySelector(".log-text");
  if (textEl) {
    textEl.textContent += pendingStreamingText;
    pendingStreamingText = "";
    scrollFeedToBottom();
  }
}

function speakLatestResponse() {
  const responses = document.querySelectorAll("[data-spoken-response]");
  const latest = responses[responses.length - 1];
  if (!latest || latest.dataset.spoken) return;
  latest.dataset.spoken = "true";

  const localAudio = latest.querySelector("[data-server-audio]");
  if (localAudio) {
    activePlayer = localAudio;
    setVoiceState("speaking", "Luna responding... Press [Esc] to stop", "SPEAKING // MACOS");

    localAudio.addEventListener("play", () => {
      setVoiceState("speaking", "Luna responding... Press [Esc] to stop", "SPEAKING // MACOS");
    }, { once: true });

    localAudio.addEventListener("ended", () => {
      activePlayer = null;
      setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    }, { once: true });

    localAudio.play().catch(() => {
      setVoiceState("idle", "Click play on audio clip to listen", "IDLE // READY");
    });
    return;
  }

  if (!window.speechSynthesis) {
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    return;
  }

  window.speechSynthesis.cancel();
  const rawText = (latest.querySelector(".log-text")?.dataset?.rawText || latest.querySelector(".log-text")?.textContent || latest.textContent || "").trim();
  const text = sanitizeForSpeech(rawText);
  if (!text) {
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    return;
  }
  const lang = languageFor(text);
  const voice = voiceFor(lang);

  if (lang === "ru-RU" && !voice) {
    // Prevent English voice from speaking Russian
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    return;
  }

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = voice?.lang || lang;
  if (voice) {
    utterance.voice = voice;
  }

  utterance.addEventListener("start", () => {
    setVoiceState("speaking", "Luna responding... Press [Esc] to stop", "SPEAKING // SYNTH");
  });

  utterance.addEventListener("end", () => {
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
  });

  utterance.addEventListener("error", (event) => {
    if (event.error !== "canceled" && event.error !== "interrupted") {
      setVoiceState("idle", `Audio error: ${event.error}`, "ERR // SYNTH");
    } else {
      setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    }
  });

  window.speechSynthesis.speak(utterance);
}

function currentVoiceSelection() {
  return document.querySelector("#voice-select")?.value || null;
}

async function resynthesizeAndPlay(entry, voice, setDefault) {
  const conversationEl = document.querySelector("[data-conversation-id]");
  const conversationId = conversationEl?.dataset?.conversationId;
  const turnIndex = entry?.dataset?.turnIndex;
  const turnId = entry?.dataset?.turnId || null;
  if (!conversationId || turnIndex == null || turnIndex === "") {
    showToast("// cannot re-synthesize this message");
    return;
  }
  if (window.isAudioPaused && typeof resumeAudioPlayback === "function") {
    resumeAudioPlayback();
  }
  setVoiceState("thinking", "Re-synthesizing speech...", "TTS // SYNTH");
  try {
    const resp = await fetch(`/api/conversations/${conversationId}/turns/${turnIndex}/resynthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice, set_default: Boolean(setDefault), turn_id: turnId }),
    });
    if (!resp.ok) {
      throw new Error((await resp.text()) || resp.statusText);
    }
    const data = await resp.json();
    await enqueueAudioChunk(null, entry, data.audio_base64, data.mime_type, null, data.turn_id, data.clip_id, data.text, true);
    const requested = data.requested_voice || voice;
    const actual = data.voice;
    if (data.fallback && actual) {
      // Be explicit: the chosen voice could not produce this text, so a
      // different local voice was used.
      showToast(`// ГОЛОС ${requested} НЕДОСТУПЕН -> ЗВУЧИТ ${actual}`);
    } else if (data.set_default && actual) {
      const voiceSelect = document.querySelector("#voice-select");
      if (voiceSelect) {
        voiceSelect.value = actual;
        // Reuse the standard change handler so the session default is
        // persisted in localStorage/cookie and synced to the server.
        voiceSelect.dispatchEvent(new Event("change"));
      }
      showToast(`// ГОЛОС ПО УМОЛЧАНИЮ: ${actual}`);
    } else {
      showToast(`// ОЗВУЧЕНО: ${actual || requested} (${data.tts_engine || "?"})`);
    }
  } catch (err) {
    showToast(`// re-synthesis failed: ${err.message || err}`);
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
  }
}

async function replayLatestResponse(targetEntry = null) {
  const responses = document.querySelectorAll("[data-spoken-response]");
  const latest = targetEntry || responses[responses.length - 1];
  if (!latest) {
    showToast("// buffer empty. no response to replay");
    return;
  }
  const text = sanitizeForSpeech(
    (latest.querySelector(".log-text")?.dataset?.rawText || latest.querySelector(".log-text")?.textContent || "").trim()
  );
  if (!text) return;

  // Always re-synthesize through the full engine with the current voice (D3).
  stopSpeaking();
  showToast("// replaying last response");
  await resynthesizeAndPlay(latest, currentVoiceSelection(), false);
}

let revoiceTargetEntry = null;

function openRevoiceDialog(entry) {
  const dialog = document.querySelector("#revoice-dialog");
  const select = document.querySelector("#revoice-voice-select");
  const defaultCheck = document.querySelector("#revoice-set-default");
  if (!dialog || !select) return;

  select.innerHTML = "";
  const source = document.querySelector("#voice-select");
  if (source) {
    const seen = new Set();
    Array.from(source.options).forEach((option) => {
      // Skip placeholders and voices whose local model is not downloaded:
      // re-voice synthesizes immediately, so an uninstalled voice can only
      // silently fall back to a different voice.
      if (!option.value || option.value.startsWith("__")) return;
      if (option.dataset.installed === "false") return;
      if (seen.has(option.value)) return;
      seen.add(option.value);
      const clone = document.createElement("option");
      clone.value = option.value;
      clone.textContent = option.textContent;
      select.appendChild(clone);
    });
    if (source.value && seen.has(source.value)) select.value = source.value;
  }
  if (select.options.length === 0) {
    showToast("// нет установленных голосов для re-voice");
    return;
  }
  if (defaultCheck) defaultCheck.checked = false;
  revoiceTargetEntry = entry;
  dialog.hidden = false;
}

function closeRevoiceDialog() {
  const dialog = document.querySelector("#revoice-dialog");
  if (dialog) dialog.hidden = true;
  revoiceTargetEntry = null;
}

function initRevoiceDialog() {
  const dialog = document.querySelector("#revoice-dialog");
  if (!dialog || dialog.dataset.initialized) return;
  dialog.dataset.initialized = "true";
  document.querySelector("#revoice-cancel")?.addEventListener("click", closeRevoiceDialog);
  document.querySelector("#revoice-speak")?.addEventListener("click", () => {
    const entry = revoiceTargetEntry;
    if (!entry) {
      closeRevoiceDialog();
      return;
    }
    const voice = document.querySelector("#revoice-voice-select")?.value || null;
    const setDefault = Boolean(document.querySelector("#revoice-set-default")?.checked);
    closeRevoiceDialog();
    resynthesizeAndPlay(entry, voice, setDefault);
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeRevoiceDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dialog.hidden) {
      event.stopPropagation();
      closeRevoiceDialog();
    }
  }, true);
}

async function startRecording(recordBtn) {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setVoiceState("idle", "Audio input unsupported by browser", "ERR // NO_MIC");
    showToast("// microphone not supported");
    return;
  }

  // BARGE-IN: Stop current output immediately
  stopSpeaking();
  clearInterimTranscript();

  try {
    if (typeof window.recordVadTraceSample === "function" && window.vadTraceEnabled) {
      window.recordVadTraceSample(0, window.VAD_VOLUME_THRESHOLD || 0.055, false, null, "manualRestart");
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    activeRecordingStream = stream;
    await initAudioAnalyser(stream);

    audioChunks = [];
    speechEndDetectedAt = null;
    isRecordingActive = true;
    streamingPcmUpload = Boolean(window.isDirectPcmActive && socket?.readyState === WebSocket.OPEN);
    if (streamingPcmUpload) {
      socket.send(JSON.stringify({
        type: "audio_stream_start",
        sample_rate: audioContext?.sampleRate || 44100,
      }));
    }

    if (!window.isDirectPcmActive) {
      recorder = new MediaRecorder(stream);
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) audioChunks.push(event.data);
      });
      recorder.addEventListener("stop", async () => {
        await finishRecording(stream, recordBtn);
      });
      recorder.start();
    }

    const msg = window.vadEnabled
      ? "Listening... (auto-stop on silence)"
      : "Listening... Press [Space] or click to finish";
    setVoiceState("listening", msg, "LISTENING // MIC");
  } catch (error) {
    isRecordingActive = false;
    stopAudioAnalyser();
    setVoiceState("idle", `Mic access denied: ${error.message}`, "ERR // MIC_DENIED");
    showToast(`// mic error: ${error.message}`);
  }
}

async function finishRecording(stream, recordBtn) {
  const recordingStoppedAt = performance.now();
  const speechEndedAt = speechEndDetectedAt || recordingStoppedAt;
  lastSpeechEndTime = speechEndedAt;
  firstAudioPlayTime = null;
  firstAudioSoundOffsetMs = null;
  const sampleRate = audioContext?.sampleRate || 44100;
  const rawPcm = pcmSamples;
  stopAudioAnalyser();
  if (stream && stream.getTracks) {
    stream.getTracks().forEach((track) => track.stop());
  }

  setVoiceState("thinking", "Processing via local whisper.cpp...", "PROCESSING // STT");
  const audioUrl = recordBtn?.dataset?.audioUrl || document.querySelector("[data-record]")?.dataset.audioUrl;

  let audioBlob = null;
  const audioEncodeStartedAt = performance.now();
  if (rawPcm.length > 0) {
    try {
      const merged = mergeBuffers(rawPcm);
      const resampled = resampleTo16k(merged, sampleRate);
      audioBlob = encodeWav16k(resampled);
    } catch (e) {
      console.warn("PCM WAV encoding fallback to MediaRecorder blob:", e);
    }
  }
  if (!audioBlob) {
    audioBlob = new Blob(audioChunks, { type: recorder?.mimeType || "audio/webm" });
  }

  await sendRecording(audioUrl, audioBlob, {
    endpoint_delay_ms: Math.round(recordingStoppedAt - speechEndedAt),
    audio_encode_ms: Math.round(performance.now() - audioEncodeStartedAt),
  });
}

async function stopRecording(recordBtn) {
  if (!isRecordingActive) return;
  isRecordingActive = false;
  window.vadSpeechDetected = false;
  window.vadSilenceStartTime = null;
  window.speechStartTime = null;
  window.vadNoiseFloor = null;

  if (streamingPcmUpload && socket?.readyState === WebSocket.OPEN) {
    const recordingStoppedAt = performance.now();
    const speechEndedAt = speechEndDetectedAt || recordingStoppedAt;
    lastSpeechEndTime = speechEndedAt;
    firstAudioPlayTime = null;
    firstAudioSoundOffsetMs = null;
    stopAudioAnalyser();
    if (activeRecordingStream?.getTracks) {
      activeRecordingStream.getTracks().forEach((track) => track.stop());
    }
    streamingPcmUpload = false;
    socket.send(JSON.stringify({
      type: "audio_timing",
      endpoint_delay_ms: Math.round(recordingStoppedAt - speechEndedAt),
      audio_encode_ms: 0,
    }));
    socket.send(JSON.stringify({ type: "audio_stream_end" }));
    setVoiceState("thinking", "Transcribing microphone stream...", "STREAM // STT");
    return;
  }

  if (recorder && recorder.state === "recording") {
    recorder.stop();
  } else {
    // Direct AudioWorklet / ScriptProcessor exclusive pipeline
    await finishRecording(activeRecordingStream, recordBtn);
  }
}

async function sendRecording(url, audioBlob, timing = null) {
  if (!audioBlob) return;

  if (socket && socket.readyState === WebSocket.OPEN) {
    setVoiceState("thinking", "Uploading stream via WebSocket...", "UPLOADING // WS");
    try {
      const arrayBuffer = await audioBlob.arrayBuffer();
      if (socket && socket.readyState === WebSocket.OPEN) {
        if (timing) socket.send(JSON.stringify({ type: "audio_timing", ...timing }));
        socket.send(arrayBuffer);
        return;
      }
    } catch (wsErr) {
      console.warn("// WS audio send failed, falling back to HTTP fetch:", wsErr);
    }
  }

  if (!url) return;
  setVoiceState("thinking", "Requesting Codex turn/start...", "QUERY // CODEX");

  const body = new FormData();
  const filename = audioBlob.type === "audio/wav" ? "recording.wav" : "recording.webm";
  body.append("audio", audioBlob, filename);

  try {
    const response = await fetch(url, { method: "POST", body });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const html = await response.text();
    const conversationEl = document.querySelector("#conversation");
    if (conversationEl) {
      conversationEl.innerHTML = html;
      if (window.htmx) {
        window.htmx.process(conversationEl);
      }
    }
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    speakLatestResponse();
    scrollFeedToBottom();
  } catch (error) {
    lastSpeechEndTime = null;
    firstAudioPlayTime = null;
    firstAudioSoundOffsetMs = null;
    setVoiceState("idle", `Turn failed: ${error.message}`, "ERR // TURN");
    showToast(`// turn error: ${error.message}`);
  }
}

function scrollFeedToBottom() {
  const feed = document.getElementById("messages-feed");
  if (feed) {
    feed.scrollTop = feed.scrollHeight;
  }
}

function copyTranscript() {
  const entries = document.querySelectorAll(".log-entry");
  if (!entries.length) {
    showToast("// buffer empty");
    return;
  }

  let textLines = [];
  entries.forEach((entry) => {
    const role = entry.classList.contains("user") ? "USER" : "LUNA";
    const textEl = entry.querySelector(".log-text");
    const text = (textEl?.dataset?.rawText || textEl?.textContent || "").trim();
    textLines.push(`[${role}] ${text}`);
  });

  const fullText = textLines.join("\n\n");
  navigator.clipboard.writeText(fullText).then(
    () => showToast("// transcript copied to clipboard"),
    () => showToast("// clipboard copy failed")
  );
}

// Global Click Dispatcher
document.addEventListener("click", (event) => {
  // Record Trigger (Main mic button or Radar)
  const recordTrigger = event.target.closest("[data-record]");
  if (recordTrigger) {
    if (isRecordingActive) {
      stopRecording(recordTrigger);
    } else {
      startRecording(recordTrigger);
    }
    return;
  }

  // Stop Speaking Button (Barge-in) — dock and per-bubble.
  if (event.target.closest("[data-stop-speaking]") || event.target.closest("[data-bubble-stop]")) {
    stopSpeaking();
    return;
  }

  // Pause/Resume Button — per-bubble transport.
  if (event.target.closest("[data-bubble-pause]")) {
    togglePauseSpeaking();
    return;
  }

  // Re-voice: open the per-message voice picker.
  const revoiceTrigger = event.target.closest("[data-revoice]");
  if (revoiceTrigger) {
    openRevoiceDialog(revoiceTrigger.closest("[data-spoken-response]"));
    return;
  }

  // Replay the response whose control was activated.
  const replayTrigger = event.target.closest("[data-replay]");
  if (replayTrigger) {
    replayLatestResponse(replayTrigger.closest("[data-spoken-response]"));
    return;
  }

  // Copy Transcript
  if (event.target.closest("[data-copy-transcript]")) {
    copyTranscript();
    return;
  }

  // Reset Chat
  const resetTrigger = event.target.closest("[data-reset-chat], .btn-term-reset");
  if (resetTrigger) {
    event.preventDefault();
    event.stopPropagation();
    resetChat(resetTrigger);
    return;
  }
});

async function resetChat(resetBtn) {
  if (resetBtn) {
    resetBtn.disabled = true;
    resetBtn.style.opacity = "0.5";
  }
  stopSpeaking();
  if (isRecordingActive) {
    try {
      await stopRecording();
    } catch (_) {}
  }

  // Explicitly close existing WebSocket connection and clear reference
  if (socket) {
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    try {
      socket.close();
    } catch (_) {}
    socket = null;
  }

  // Clear streaming state and audio queue
  currentStreamingEntry = null;
  audioQueue = [];
  isPlayingAudio = false;
  latestTiming = null;
  const latencyHud = document.querySelector("#latency-metrics");
  if (latencyHud) latencyHud.style.display = "none";

  const convContainer = document.querySelector("[data-conversation-id]");
  const convId = convContainer?.dataset?.conversationId;
  const url = resetBtn?.getAttribute("hx-delete") || (convId ? `/conversations/${convId}` : "/conversations");

  try {
    const response = await fetch(url, {
      method: "DELETE",
      headers: { "HX-Request": "true", "HX-Target": "conversation" },
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const html = await response.text();
    const conversationEl = document.querySelector("#conversation");
    if (conversationEl) {
      conversationEl.innerHTML = html;
      if (window.htmx) {
        window.htmx.process(conversationEl);
      }
    }
    setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    initPluginSelector();
    initVadToggle();
    document.querySelectorAll(".log-text").forEach((el) => {
      if (!el.querySelector(".term-link") && !el.querySelector(".log-sources")) {
        formatTerminalText(el);
      }
    });
    scrollFeedToBottom();
    connectWebSocket();
    showToast("// session reset");
  } catch (err) {
    console.error("// reset chat failed:", err);
    showToast(`// reset failed: ${err.message}`);
    if (resetBtn) {
      resetBtn.disabled = false;
      resetBtn.style.opacity = "";
    }
  }
}

// Keyboard Navigation
document.addEventListener("keydown", (event) => {
  const activeTag = document.activeElement?.tagName?.toLowerCase();
  const isInputFocused = activeTag === "textarea" || activeTag === "input";

  // Escape: Stop speaking
  if (event.key === "Escape") {
    stopSpeaking();
    return;
  }

  // P: Pause/resume speech playback
  if ((event.key === "p" || event.key === "P" || event.key === "з" || event.key === "З") && !isInputFocused) {
    event.preventDefault();
    togglePauseSpeaking();
    return;
  }

  // V: Toggle VAD (Voice Activity Detection)
  if ((event.key === "v" || event.key === "V" || event.key === "м" || event.key === "М") && !isInputFocused) {
    event.preventDefault();
    toggleVad();
    return;
  }

  // Spacebar: Toggle recording when not typing
  if (event.code === "Space" && !isInputFocused) {
    event.preventDefault();
    const recordBtn = document.querySelector("[data-record]");
    if (recordBtn) {
      if (isRecordingActive) {
        stopRecording();
      } else {
        startRecording(recordBtn);
      }
    }
  }
});

// HTMX Lifecycle Hooks
document.body.addEventListener("htmx:beforeRequest", (event) => {
  stopSpeaking();
  if (isRecordingActive) {
    stopRecording();
  }
  if (event.detail.elt.tagName === "FORM") {
    setVoiceState("thinking", "Requesting Codex turn/start...", "QUERY // CODEX");
  }
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "conversation") {
    latestTiming = null;
    const latencyHud = document.querySelector("#latency-metrics");
    if (latencyHud) latencyHud.style.display = "none";
    connectWebSocket();
    initPluginSelector();
    initVadToggle();
    initLiveTranscriptToggle();
    initRevoiceDialog();
    renderLiveTranscriptChip();
    updateBubbleTransportVisibility(lastVoiceUiState);
    document.querySelectorAll(".log-text").forEach((el) => {
      if (!el.querySelector(".term-link") && !el.querySelector(".log-sources")) {
        formatTerminalText(el);
      }
    });
    const responses = document.querySelectorAll("[data-spoken-response]");
    if (responses.length === 0) {
      setVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    } else {
      speakLatestResponse();
    }
    scrollFeedToBottom();
  }
});

// Intercept prompt-dock text submissions in capture phase to prevent duplicate HTMX POST
document.addEventListener("submit", (event) => {
  const form = event.target.closest(".cmd-form");
  if (!form) return;
  const input = form.querySelector("input[name='text']");
  const text = input?.value?.trim();
  if (!text) return;

  if (socket && socket.readyState === WebSocket.OPEN) {
    event.preventDefault();
    event.stopImmediatePropagation();
    stopSpeaking();
    lastSpeechEndTime = performance.now();
    firstAudioPlayTime = null;
    firstAudioSoundOffsetMs = null;
    currentStreamingEntry = null;
    socket.send(JSON.stringify({ type: "text", text }));
    input.value = "";
  }
}, { capture: true });

// Explicit guard: prevent HTMX from issuing AJAX requests for .cmd-form when WebSocket is open
document.addEventListener("htmx:configRequest", (event) => {
  if (event.target.closest?.(".cmd-form") && socket && socket.readyState === WebSocket.OPEN) {
    event.preventDefault();
  }
});


function applyLiveTranscriptState(data) {
  liveTranscriptAvailable = Boolean(data.live_transcript_available);
  liveTranscriptEnabled = data.live_transcript !== false;
  if (Object.prototype.hasOwnProperty.call(data, "live_transcript_model")) {
    liveTranscriptModel = data.live_transcript_model || null;
  }
  renderLiveTranscriptChip();
}

function renderLiveTranscriptChip() {
  const chip = document.querySelector("#live-transcript-toggle");
  if (!chip) return;
  const model = liveTranscriptModel;
  if (!model) {
    // Backward compatible payloads may still only carry the availability flag.
    if (liveTranscriptAvailable) {
      chip.hidden = false;
      chip.classList.remove("is-download");
      stopLiveTranscriptPolling();
      const savedFlag = localStorage.getItem("voice_of_luna_live_transcript");
      setLiveTranscriptUi(savedFlag === null ? liveTranscriptEnabled !== false : savedFlag !== "false");
      return;
    }
    chip.hidden = true;
    stopLiveTranscriptPolling();
    return;
  }
  const status = model.status || (model.installed ? "ready" : "not_installed");
  if (status === "ready") {
    chip.hidden = false;
    chip.classList.remove("is-download");
    stopLiveTranscriptPolling();
    const saved = localStorage.getItem("voice_of_luna_live_transcript");
    setLiveTranscriptUi(saved === null ? liveTranscriptEnabled !== false : saved !== "false");
    return;
  }
  // Not installed / downloading / error: expose download progress.
  chip.hidden = false;
  chip.classList.remove("is-auto", "is-manual");
  chip.classList.add("is-download");
  chip.dataset.liveTranscript = "download";
  chip.setAttribute("aria-pressed", "false");
  const textEl = chip.querySelector(".live-transcript-state-text");
  const percent = model.progress_percent != null ? model.progress_percent : 0;
  if (status === "downloading") {
    if (textEl) textEl.textContent = `↓ ${percent}%`;
    chip.title = `Скачивается streaming-модель ${model.name || ""} (${percent}%)`;
    startLiveTranscriptPolling(model.id);
  } else if (status === "error") {
    stopLiveTranscriptPolling();
    if (textEl) textEl.textContent = "!";
    chip.title = `Ошибка загрузки: ${model.error || "неизвестно"}`;
  } else {
    stopLiveTranscriptPolling();
    if (textEl) textEl.textContent = `↓ ${Math.round(model.size_mb || 0)}MB`;
    chip.title = `Скачать streaming-модель ${model.name || ""}`;
  }
}

function startLiveTranscriptPolling(modelId) {
  if (liveTranscriptPollTimer && liveTranscriptPollingId === modelId) return;
  stopLiveTranscriptPolling();
  liveTranscriptPollingId = modelId;
  liveTranscriptPollTimer = window.setInterval(async () => {
    try {
      const resp = await fetch(`/api/stt/models/${encodeURIComponent(modelId)}/status`);
      if (!resp.ok) return;
      const status = await resp.json();
      if (!liveTranscriptModel || liveTranscriptModel.id !== modelId) {
        stopLiveTranscriptPolling();
        return;
      }
      liveTranscriptModel = status;
      if (status.status === "ready") {
        liveTranscriptAvailable = true;
        liveTranscriptEnabled = true;
        showToast("// STREAMING STT ГОТОВ: LIVE ВКЛ");
      }
      renderLiveTranscriptChip();
    } catch (_) {
      // keep polling; transient network errors are expected
    }
  }, 1500);
}

function stopLiveTranscriptPolling() {
  if (liveTranscriptPollTimer) {
    window.clearInterval(liveTranscriptPollTimer);
    liveTranscriptPollTimer = null;
  }
  liveTranscriptPollingId = null;
}

function requestLiveTranscriptDownload(modelId) {
  fetch(`/api/stt/models/${encodeURIComponent(modelId)}/download`, { method: "POST" }).catch(() => {});
  if (liveTranscriptModel) {
    liveTranscriptModel.status = "downloading";
    liveTranscriptModel.progress_percent = 0;
  }
  renderLiveTranscriptChip();
  showToast("// ЗАГРУЗКА STREAMING-МОДЕЛИ...");
}

function setLiveTranscriptUi(enabled) {
  const chip = document.querySelector("#live-transcript-toggle");
  if (!chip) return;
  chip.classList.toggle("is-auto", enabled);
  chip.classList.toggle("is-manual", !enabled);
  const textEl = chip.querySelector(".live-transcript-state-text");
  if (textEl) textEl.textContent = enabled ? "ON" : "OFF";
  chip.setAttribute("aria-pressed", enabled ? "true" : "false");
  chip.dataset.liveTranscript = enabled ? "true" : "false";
}

function initLiveTranscriptToggle() {
  const chip = document.querySelector("#live-transcript-toggle");
  if (!chip || chip.dataset.initialized) return;
  chip.dataset.initialized = "true";
  chip.addEventListener("click", () => {
    if (liveTranscriptModel && liveTranscriptModel.status === "downloading") return;
    if (liveTranscriptModel && liveTranscriptModel.status !== "ready") {
      requestLiveTranscriptDownload(liveTranscriptModel.id);
      return;
    }
    const next = chip.dataset.liveTranscript !== "true";
    localStorage.setItem("voice_of_luna_live_transcript", next ? "true" : "false");
    setLiveTranscriptUi(next);
    sendSettingsUpdate({ live_transcript: next });
    showToast(next ? "// LIVE TRANSCRIPT: ON" : "// LIVE TRANSCRIPT: OFF");
  });
}

function initRemoteWarmupToggle() {
  const btn = document.querySelector("#remote-warmup-toggle");
  if (!btn || btn.dataset.initialized) return;

  let enabled = localStorage.getItem("voice_of_luna_remote_warmup") !== "false";
  const updateUi = () => {
    const textEl = btn.querySelector(".warmup-state-text");
    btn.classList.toggle("is-auto", enabled);
    btn.classList.toggle("is-manual", !enabled);
    if (textEl) textEl.textContent = enabled ? "ON" : "OFF";
    btn.setAttribute(
      "title",
      enabled
        ? "Технический прогрев Codex включён: одна короткая реплика квоты на новый диалог и выбранные модель с режимом"
        : "Технический прогрев Codex выключен"
    );
  };

  updateUi();
  btn.dataset.initialized = "true";
  btn.addEventListener("click", () => {
    enabled = !enabled;
    localStorage.setItem("voice_of_luna_remote_warmup", enabled ? "true" : "false");
    document.cookie = `voice_of_luna_remote_warmup=${enabled ? "true" : "false"}; path=/; max-age=31536000; SameSite=Lax`;
    const model = document.querySelector("#model-select")?.value;
    const effort = document.querySelector("#effort-select")?.value;
    sendSettingsUpdate({ model, effort, remote_warmup: enabled });
    updateUi();
    showToast(enabled ? "// CODEX WARMUP: ON" : "// CODEX WARMUP: OFF");
  });
}

function expandOtherVoices(keepValue) {
  const select = document.querySelector("#voice-select");
  if (!select) return false;
  if (select.querySelector("#other-voices-group")) {
    if (keepValue) {
      select.value = keepValue;
    }
    return true;
  }

  const tpl = document.querySelector("#other-voices-template");
  if (!tpl) return false;

  const clone = tpl.content.cloneNode(true);
  select.querySelector("#active-other-voice")?.remove();
  const expandOpt = select.querySelector('option[value="__expand_other__"]');
  if (expandOpt) {
    expandOpt.remove();
  }
  select.appendChild(clone);

  if (!select.querySelector('option[value="__collapse_other__"]')) {
    const collapseOpt = document.createElement("option");
    collapseOpt.value = "__collapse_other__";
    collapseOpt.textContent = "▲ Скрыть другие языки";
    select.appendChild(collapseOpt);
  }

  if (keepValue) {
    select.value = keepValue;
  }
  return true;
}

function collapseOtherVoices(fallbackValue) {
  const select = document.querySelector("#voice-select");
  if (!select) return false;
  const group = select.querySelector("#other-voices-group");
  const collapseOpt = select.querySelector('option[value="__collapse_other__"]');
  if (!group) return false;

  let tpl = document.querySelector("#other-voices-template");
  if (!tpl) {
    tpl = document.createElement("template");
    tpl.id = "other-voices-template";
    select.parentNode.appendChild(tpl);
    tpl.content.appendChild(group.cloneNode(true));
  }

  const selectedOption = Array.from(group.options).find((option) => option.value === fallbackValue);
  const selectedLabel = selectedOption?.textContent?.trim() || fallbackValue;
  group.remove();
  if (collapseOpt) collapseOpt.remove();

  if (!select.querySelector('option[value="__expand_other__"]')) {
    const expandOpt = document.createElement("option");
    expandOpt.value = "__expand_other__";
    const otherCount = tpl.content.querySelectorAll("option").length;
    expandOpt.textContent = `▶ Другие языки (+${otherCount || "100"})...`;
    select.appendChild(expandOpt);
  }

  if (fallbackValue) {
    const previousActive = select.querySelector("#active-other-voice");
    if (previousActive) previousActive.remove();
    const activeOption = document.createElement("option");
    activeOption.id = "active-other-voice";
    activeOption.value = fallbackValue;
    activeOption.textContent = selectedLabel;
    activeOption.selected = true;
    select.insertBefore(activeOption, select.querySelector('option[value="__expand_other__"]'));
    select.value = fallbackValue;
  }
  return true;
}

// Voice selection & persistence
function initVoiceSelector() {
  const select = document.querySelector("#voice-select");
  if (!select) return;

  select.dataset.lastVoice = select.value;

  const saved = localStorage.getItem("voice_of_luna_voice");
  if (saved) {
    let hasOption = Array.from(select.options).some((opt) => opt.value === saved);
    if (!hasOption) {
      const tpl = document.querySelector("#other-voices-template");
      if (tpl && tpl.content.querySelector(`option[value="${CSS.escape(saved)}"]`)) {
        expandOtherVoices(saved);
        hasOption = true;
      }
    }
    if (hasOption) {
      const opt = select.querySelector(`option[value="${CSS.escape(saved)}"]`);
      const isUninstalled = opt && (opt.dataset.installed === "false" || opt.textContent.includes("[↓"));
      if (select.value !== saved || isUninstalled) {
        select.value = saved;
        select.dataset.lastVoice = saved;
        updateVoiceAttributes(saved);
        sendVoiceUpdate(saved);
      }
    }
  }

  // Resume tracking for any active model downloads
  fetch("/api/tts/models")
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (data && data.models) {
        for (const m of data.models) {
          if (m.status === "downloading") {
            const vName = m.voices && m.voices.length > 0 ? m.voices[0] : m.name;
            pollModelStatus(m.id, vName);
          }
        }
      }
    })
    .catch(() => {});

  select.addEventListener("change", (e) => {
    const chosenVal = e.target.value;
    const last = select.dataset.lastVoice || select.dataset.russianVoice || select.options[0]?.value;

    if (chosenVal === "__expand_other__") {
      expandOtherVoices(last);
      select.value = last;
      return;
    }

    if (chosenVal === "__collapse_other__") {
      const fallback = last;
      collapseOtherVoices(fallback);
      select.value = fallback;
      if (fallback !== last) {
        select.dataset.lastVoice = fallback;
        localStorage.setItem("voice_of_luna_voice", fallback);
        document.cookie = `voice_of_luna_voice=${encodeURIComponent(fallback)}; path=/; max-age=31536000; SameSite=Lax`;
        updateVoiceAttributes(fallback);
        sendVoiceUpdate(fallback);
        showToast(`// VOICE ACTIVE: ${fallback.toUpperCase()}`);
      }
      return;
    }

    const opt = select.querySelector(`option[value="${CSS.escape(chosenVal)}"]`);
    const isUninstalled = opt && (opt.dataset.installed === "false" || opt.textContent.includes("[↓"));

    select.dataset.lastVoice = chosenVal;
    localStorage.setItem("voice_of_luna_voice", chosenVal);
    document.cookie = `voice_of_luna_voice=${encodeURIComponent(chosenVal)}; path=/; max-age=31536000; SameSite=Lax`;
    updateVoiceAttributes(chosenVal);
    sendVoiceUpdate(chosenVal);
    if (select.querySelector(`#other-voices-group option[value="${CSS.escape(chosenVal)}"]`)) {
      collapseOtherVoices(chosenVal);
    }
    if (isUninstalled) {
      showToast(`// ИНИЦИАЛИЗАЦИЯ ЗАГРУЗКИ: ${chosenVal.toUpperCase()}`);
    } else {
      showToast(`// VOICE ACTIVE: ${chosenVal.toUpperCase()}`);
    }
  });
}

function updateVoiceAttributes(voiceName) {
  const chip = document.querySelector(".voice-selector-chip");
  if (chip) chip.dataset.russianVoice = voiceName;
  document.body.dataset.russianVoice = voiceName;
}

function updateDownloadUI(modelId, voiceName, status) {
  const chip = document.querySelector(".voice-selector-chip");
  const badge = document.getElementById("voice-download-badge");
  const banner = document.getElementById("model-download-banner");
  const nameEl = document.getElementById("download-model-name");
  const pctEl = document.getElementById("download-pct");
  const fillEl = document.getElementById("download-bar-fill");
  const sizeEl = document.getElementById("download-size");
  const etaEl = document.getElementById("download-eta");
  const helpEl = document.getElementById("download-help");
  const actionsEl = document.getElementById("download-actions");
  const retryBtn = document.getElementById("download-retry-btn");
  const closeBtn = document.getElementById("download-close-btn");
  const select = document.querySelector("#voice-select");
  const opt = select ? select.querySelector(`option[value="${CSS.escape(voiceName)}"]`) : null;
  const footerEl = document.querySelector("[data-footer-meta]");

  if (status.status === "downloading") {
    if (chip) chip.classList.add("is-downloading");
    const pct = status.progress_percent || 0;
    const downloaded = status.downloaded_mb != null ? Number(status.downloaded_mb).toFixed(1) : "0.0";
    const total = status.total_mb != null ? Number(status.total_mb).toFixed(1) : "60.0";
    const speed = status.speed_kbps ? `${Math.round(status.speed_kbps)} КБ/с` : "загрузка…";
    const eta = status.eta_seconds ? `~${status.eta_seconds} сек` : "вычисление времени…";

    if (badge) {
      badge.hidden = false;
      badge.textContent = `[⟳ ${pct}%]`;
    }

    if (banner) {
      banner.hidden = false;
      banner.classList.remove("is-complete", "is-error");
      if (nameEl) nameEl.textContent = (status.name || modelId).toUpperCase();
      if (pctEl) pctEl.textContent = `${pct}%`;
      if (fillEl) fillEl.style.width = `${pct}%`;
      if (sizeEl) sizeEl.textContent = `${downloaded} / ${total} МБ (${speed})`;
      if (etaEl) etaEl.textContent = `осталось ${eta}`;
      if (helpEl) helpEl.textContent = "Фоновая загрузка оффлайн-модели. После завершения голос включится автоматически.";
      if (actionsEl) actionsEl.hidden = true;
    }

    if (footerEl) {
      footerEl.textContent = `STT:LOCAL // TTS:DOWNLOADING (${pct}%) // LLM:CODEX`;
    }

    if (opt && (opt.textContent.includes("[↓") || opt.textContent.includes("[⟳"))) {
      opt.textContent = `${voiceName} [⟳ ${pct}%]`;
    }
  } else if (status.status === "ready") {
    if (chip) chip.classList.remove("is-downloading");
    if (badge) badge.hidden = true;

    if (banner) {
      banner.hidden = false;
      banner.classList.add("is-complete");
      banner.classList.remove("is-error");
      if (nameEl) nameEl.textContent = (status.name || modelId).toUpperCase();
      if (pctEl) pctEl.textContent = `100%`;
      if (fillEl) fillEl.style.width = `100%`;
      if (sizeEl) sizeEl.textContent = `${status.total_mb || 60} МБ установлено`;
      if (etaEl) etaEl.textContent = `✔ ГОТОВО К РАБОТЕ`;
      if (helpEl) helpEl.textContent = `Модель успешно загружена! Голос активирован.`;
      if (actionsEl) actionsEl.hidden = true;

      setTimeout(() => {
        banner.style.transition = "opacity 0.5s ease, transform 0.5s ease";
        banner.style.opacity = "0";
        setTimeout(() => {
          banner.hidden = true;
          banner.style.opacity = "";
          banner.style.transition = "";
        }, 500);
      }, 4000);
    }

    if (opt) {
      opt.dataset.installed = "true";
      opt.textContent = `${voiceName} ★`;
    }

    showToast(`// ГОЛОС АКТИВИРОВАН: ${voiceName.toUpperCase()}`);
    updateFooterStatus(voiceName);
  } else if (status.status === "error") {
    if (chip) chip.classList.remove("is-downloading");
    if (badge) {
      badge.hidden = false;
      badge.textContent = `[✖ СБОЙ]`;
    }

    if (banner) {
      banner.hidden = false;
      banner.classList.add("is-error");
      banner.classList.remove("is-complete");
      if (nameEl) nameEl.textContent = (status.name || modelId).toUpperCase();
      if (pctEl) pctEl.textContent = `СБОЙ`;
      if (fillEl) fillEl.style.width = `100%`;
      if (sizeEl) sizeEl.textContent = `Ошибка загрузки`;
      if (etaEl) etaEl.textContent = status.error || "Сбой соединения при загрузке файлов";
      if (helpEl) helpEl.textContent = "Не удалось скачать файлы модели. Проверьте интернет или скачайте веса вручную: `make setup`";
      if (actionsEl) actionsEl.hidden = false;

      if (retryBtn) {
        retryBtn.onclick = () => {
          if (actionsEl) actionsEl.hidden = true;
          if (etaEl) etaEl.textContent = "перезапуск загрузки…";
          fetch(`/api/tts/models/${encodeURIComponent(modelId)}/download`, { method: "POST" })
            .then(() => pollModelStatus(modelId, voiceName))
            .catch((err) => {
              console.error("// retry download failed:", err);
              if (actionsEl) actionsEl.hidden = false;
            });
        };
      }

      if (closeBtn) {
        closeBtn.onclick = () => {
          banner.hidden = true;
          if (badge) badge.hidden = true;
        };
      }
    }
  }
}

function pollModelStatus(modelId, voiceName) {
  updateDownloadUI(modelId, voiceName, {
    status: "downloading",
    progress_percent: 1,
    downloaded_mb: 0,
    total_mb: 60,
  });

  const interval = setInterval(() => {
    fetch(`/api/tts/models/${encodeURIComponent(modelId)}/status`)
      .then((r) => r.json())
      .then((status) => {
        updateDownloadUI(modelId, voiceName, status);
        if (status.status === "ready" || status.status === "error") {
          clearInterval(interval);
        }
      })
      .catch((err) => {
        console.warn("// status poll error:", err);
      });
  }, 1000);
}

function sendVoiceUpdate(voiceName) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "set_voice", voice: voiceName }));
  }
  fetch("/api/voice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voice: voiceName }),
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (data && data.auto_downloading && data.model_id) {
        pollModelStatus(data.model_id, voiceName);
      }
    })
    .catch((err) => console.warn("// voice sync error:", err));
}

// Model & Reasoning Effort selection & persistence
function initSettingsSelectors() {
  const modelSelect = document.querySelector("#model-select");
  const effortSelect = document.querySelector("#effort-select");

  if (modelSelect) {
    let savedModel = localStorage.getItem("voice_of_luna_model");
    const migratedToGpt6 = localStorage.getItem("voice_of_luna_migrated_gpt6");
    const hasLegacyCookie = document.cookie.split("; ").some((c) => c.startsWith("voice_of_luna_model=gpt-5.6"));
    const hasGpt6Option = Array.from(modelSelect.options).some((opt) => opt.value === "gpt-6-luna");

    if (!migratedToGpt6) {
      // Migrate legacy default (gpt-5.6-luna) to gpt-6-luna
      if (hasGpt6Option && (savedModel === "gpt-5.6-luna" || savedModel === "gpt-5.6" || modelSelect.value === "gpt-5.6-luna" || hasLegacyCookie || !savedModel)) {
        savedModel = "gpt-6-luna";
        localStorage.setItem("voice_of_luna_model", "gpt-6-luna");
        document.cookie = `voice_of_luna_model=${encodeURIComponent("gpt-6-luna")}; path=/; max-age=31536000; SameSite=Lax`;
        modelSelect.value = "gpt-6-luna";
        sendSettingsUpdate({ model: "gpt-6-luna" });
      }
      localStorage.setItem("voice_of_luna_migrated_gpt6", "true");
    } else if (savedModel) {
      const hasOption = Array.from(modelSelect.options).some((opt) => opt.value === savedModel);
      if (hasOption && modelSelect.value !== savedModel) {
        modelSelect.value = savedModel;
        sendSettingsUpdate({ model: savedModel });
      }
    }

    modelSelect.addEventListener("change", (e) => {
      const chosenModel = e.target.value;
      localStorage.setItem("voice_of_luna_model", chosenModel);
      document.cookie = `voice_of_luna_model=${encodeURIComponent(chosenModel)}; path=/; max-age=31536000; SameSite=Lax`;
      sendSettingsUpdate({ model: chosenModel, remote_warmup: localStorage.getItem("voice_of_luna_remote_warmup") !== "false" });
      showToast(`// MODEL: ${chosenModel}`);
    });
  }

  if (effortSelect) {
    const savedEffort = localStorage.getItem("voice_of_luna_effort");
    if (savedEffort) {
      const hasOption = Array.from(effortSelect.options).some((opt) => opt.value === savedEffort);
      if (hasOption && effortSelect.value !== savedEffort) {
        effortSelect.value = savedEffort;
        sendSettingsUpdate({ effort: savedEffort });
      }
    }

    effortSelect.addEventListener("change", (e) => {
      const chosenEffort = e.target.value;
      localStorage.setItem("voice_of_luna_effort", chosenEffort);
      document.cookie = `voice_of_luna_effort=${encodeURIComponent(chosenEffort)}; path=/; max-age=31536000; SameSite=Lax`;
      sendSettingsUpdate({ effort: chosenEffort, remote_warmup: localStorage.getItem("voice_of_luna_remote_warmup") !== "false" });
      showToast(`// EFFORT: ${chosenEffort.toUpperCase()}`);
    });
  }
}

function sendSettingsUpdate(settings) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "set_settings", ...settings }));
  }
  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  }).catch((err) => console.warn("// settings sync error:", err));
}

// Plugin & Mode selection & persistence
function initPluginSelector() {
  const pluginSelectors = document.querySelectorAll(".plugin-select, #plugin-select, #session-plugin-select");
  const modeSelect = document.querySelector("#mode-select");
  const modeChip = document.querySelector("#mode-chip");
  if (pluginSelectors.length === 0) return;

  const pluginsData = window._PLUGINS || [];
  const updateProjectRootVisibility = (pluginId) => {
    const projectRootChip = document.querySelector(".plugin-panel");
    if (!projectRootChip) return;
    const plugin = pluginsData.find((p) => p.id === pluginId);
    const hidden = !plugin?.panel;
    projectRootChip.hidden = hidden;
    projectRootChip.setAttribute("aria-hidden", String(hidden));
  };

  function updateModeOptions(pluginId, selectedMode) {
    if (!modeSelect) return;
    const plugin = pluginsData.find((p) => p.id === pluginId);
    const modes = plugin?.modes || [{"id": "default", "label": "Default"}];

    modeSelect.innerHTML = "";
    modes.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      if (m.id === selectedMode) opt.selected = true;
      modeSelect.appendChild(opt);
    });

    if (modeChip) {
      if (modes.length > 1) {
        modeChip.style.display = "inline-flex";
      } else {
        modeChip.style.display = "none";
      }
    }
  }

  window._updatePluginModeOptions = updateModeOptions;

  const savedPlugin = localStorage.getItem("voice_of_luna_plugin") || window._ACTIVE_PLUGIN || pluginSelectors[0].value;
  const savedMode = localStorage.getItem("voice_of_luna_plugin_mode") || window._ACTIVE_PLUGIN_MODE || "default";

  pluginSelectors.forEach((sel) => {
    if (savedPlugin) {
      const hasOption = Array.from(sel.options).some((opt) => opt.value === savedPlugin);
      if (hasOption) {
        sel.value = savedPlugin;
      }
    }

    if (!sel.dataset.initialized) {
      sel.dataset.initialized = "true";
      sel.addEventListener("change", (e) => {
        const chosenPlugin = e.target.value;
        pluginSelectors.forEach((s) => { s.value = chosenPlugin; });
        localStorage.setItem("voice_of_luna_plugin", chosenPlugin);
        document.cookie = `voice_of_luna_plugin=${encodeURIComponent(chosenPlugin)}; path=/; max-age=31536000; SameSite=Lax`;
        updateModeOptions(chosenPlugin, "default");
        updateProjectRootVisibility(chosenPlugin);
        const chosenMode = modeSelect ? modeSelect.value : "default";
        localStorage.setItem("voice_of_luna_plugin_mode", chosenMode);
        document.cookie = `voice_of_luna_plugin_mode=${encodeURIComponent(chosenMode)}; path=/; max-age=31536000; SameSite=Lax`;

        sendPluginUpdate(chosenPlugin, chosenMode);
        showToast(`// PLUGIN: ${chosenPlugin.toUpperCase()}`);
      });
    }
  });

  updateModeOptions(pluginSelectors[0].value, savedMode);
  updateProjectRootVisibility(pluginSelectors[0].value);

  if (modeSelect && !modeSelect.dataset.initialized) {
    modeSelect.dataset.initialized = "true";
    modeSelect.addEventListener("change", (e) => {
      const chosenMode = e.target.value;
      const currentPlugin = pluginSelectors[0].value;
      localStorage.setItem("voice_of_luna_plugin_mode", chosenMode);
      document.cookie = `voice_of_luna_plugin_mode=${encodeURIComponent(chosenMode)}; path=/; max-age=31536000; SameSite=Lax`;
      sendPluginUpdate(currentPlugin, chosenMode);
      showToast(`// MODE: ${chosenMode.toUpperCase()}`);
    });
  }
}

function sendPluginUpdate(pluginId, mode = "default") {
  const settings = {};
  document.querySelectorAll("[data-plugin-setting]").forEach((input) => {
    const value = input.value.trim();
    if (value) settings[input.dataset.pluginSetting] = value;
  });
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "set_plugin", plugin_id: pluginId, mode: mode, settings }));
  }
  const convElem = document.querySelector("[data-conversation-id]");
  const convId = convElem?.dataset?.conversationId;
  if (convId) {
    fetch(`/api/conversations/${convId}/plugin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin_id: pluginId, mode: mode, settings }),
    })
      .then(async (res) => {
        if (res.ok) return res.json();
        const payload = await res.json().catch(() => ({}));
        const message = payload.detail || `HTTP ${res.status}`;
        showToast(`// PLUGIN ERROR: ${message}`);
        throw new Error(message);
      })
      .then((data) => {
        if (data && Object.prototype.hasOwnProperty.call(data, "panel")) renderPluginPanel(data.panel, data.plugin_id, data.settings || {});
        if (data && data.voice) {
          const voiceSelect = document.querySelector("#voice-select");
          if (voiceSelect) {
            expandOtherVoices(data.voice);
            voiceSelect.value = data.voice;
            voiceSelect.dataset.lastVoice = data.voice;
            updateVoiceAttributes(data.voice);
          }
        }
      })
      .catch((err) => console.warn("// plugin sync error:", err));
  }
}

function escapePluginHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function showPluginModal(title, contentHtml) {
  const modal = document.getElementById("plugin-result-modal");
  const titleEl = document.getElementById("plugin-result-title");
  const contentEl = document.getElementById("plugin-result-content");
  if (!modal || !contentEl) return;
  if (titleEl) titleEl.textContent = title || "// PLUGIN OUTPUT";
  contentEl.innerHTML = contentHtml;
  modal.hidden = false;
}

function hidePluginModal() {
  const modal = document.getElementById("plugin-result-modal");
  if (modal) modal.hidden = true;
}

function initPluginModal() {
  const modal = document.getElementById("plugin-result-modal");
  const closeBtn = document.getElementById("plugin-modal-close");
  if (!modal || modal.dataset.initialized === "true") return;
  modal.dataset.initialized = "true";
  closeBtn?.addEventListener("click", hidePluginModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) hidePluginModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) {
      hidePluginModal();
    }
  });
}

function showDebriefModal(debrief) {
  const elapsed = debrief.active_elapsed_seconds ?? 0;
  const duration = debrief.duration_seconds ?? 0;
  const html = `
    <div class="debrief-summary">
      <div class="debrief-stat-grid">
        <div class="debrief-stat"><span class="debrief-stat-label">PHASE:</span> <span class="debrief-stat-val">${escapePluginHtml(debrief.phase || "ended")}</span></div>
        <div class="debrief-stat"><span class="debrief-stat-label">REASON:</span> <span class="debrief-stat-val">${escapePluginHtml(debrief.ended_reason || "completed")}</span></div>
        <div class="debrief-stat"><span class="debrief-stat-label">ACTIVE TIME:</span> <span class="debrief-stat-val">${elapsed}s / ${duration}s</span></div>
        <div class="debrief-stat"><span class="debrief-stat-label">REFLECTIONS:</span> <span class="debrief-stat-val">${debrief.reflections ?? 0}</span></div>
        <div class="debrief-stat"><span class="debrief-stat-label">REQUIREMENTS:</span> <span class="debrief-stat-val">${debrief.requirements_completed ?? 0} / ${debrief.requirements_total ?? 0}</span></div>
        <div class="debrief-stat"><span class="debrief-stat-label">REPLAYS:</span> <span class="debrief-stat-val">${debrief.replays ?? 0}</span></div>
        <div class="debrief-stat"><span class="debrief-stat-label">REJECTED:</span> <span class="debrief-stat-val">${debrief.rejected_actions ?? 0}</span></div>
      </div>
    </div>
  `;
  showPluginModal("// DEBRIEF: SESSION SUMMARY", html);
}

function showProjectCardModal(card) {
  const text = card ? escapePluginHtml(card) : "// No project card indexed";
  showPluginModal("// PROJECT ROOM: INDEX CARD", `<pre class="terminal-modal-pre">${text}</pre>`);
}

async function handlePluginActionResponse(convId, action, data) {
  if (data.debrief) {
    showDebriefModal(data.debrief);
    showToast("// DEBRIEF READY");
    return;
  }
  if (data.card !== undefined) {
    showProjectCardModal(data.card);
    showToast("// PROJECT CARD UPDATED");
    return;
  }
  if (data.speak_request && data.speak_request.text) {
    showToast(`// REPEATING: "${data.speak_request.text.slice(0, 30)}…"`);
    try {
      const speakRes = await fetch(`/api/conversations/${convId}/plugin/speak`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: data.speak_request.text }),
      });
      if (speakRes.ok) {
        const clip = await speakRes.json();
        if (clip.audio_url && window.playAudioQueue) {
          window.playAudioQueue([clip.audio_url]);
        }
      }
    } catch (err) {
      console.warn("// Repeat speech failed:", err);
    }
    return;
  }
  if (data.session) {
    const s = data.session;
    const status = s.status || "active";
    const elapsed = Math.round((s.active_elapsed_ms || 0) / 1000);
    const total = s.config?.duration_seconds || 0;
    showToast(`// SESSION: ${status.toUpperCase()} [${elapsed}s / ${total}s]`);
    return;
  }
  showToast(`// PLUGIN ACTION: ${action.toUpperCase()}`);
}

function renderPluginPanel(panel, pluginId, settings = {}) {
  const existing = document.querySelector("[data-plugin-panel]");
  if (existing) existing.remove();
  if (!panel || (!Array.isArray(panel.fields) && !Array.isArray(panel.actions))) return;

  const fields = panel.fields || [];
  const actions = panel.actions || [];
  const hasTextarea = fields.some((f) => f.type === "textarea");
  const isRich = hasTextarea || fields.length > 2;

  const wrapper = document.createElement("div");
  wrapper.className = `sys-chip plugin-panel${isRich ? " has-rich-fields" : ""}`;
  wrapper.dataset.pluginPanel = pluginId || "";

  const fieldsContainer = document.createElement("div");
  fieldsContainer.className = "plugin-fields-container";

  fields.forEach((field) => {
    const item = document.createElement("div");
    item.className = `plugin-field-item${field.type === "textarea" ? " is-textarea" : ""}`;

    const label = document.createElement("label");
    label.className = "session-plugin-label";
    label.htmlFor = `plugin-setting-${field.name}`;
    label.textContent = `${field.label || field.name}:`;

    if (field.help) {
      const help = document.createElement("button");
      help.type = "button";
      help.className = "plugin-field-help";
      help.textContent = "?";
      help.title = field.help;
      help.setAttribute("aria-label", field.help);
      label.appendChild(help);
    }

    let input;
    if (field.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = field.rows || 3;
      input.className = "voice-input plugin-setting plugin-textarea";
    } else {
      input = document.createElement("input");
      input.type = field.type === "directory" ? "text" : (field.type || "text");
      input.className = "voice-input plugin-setting";
    }
    input.id = label.htmlFor;
    input.dataset.pluginSetting = field.name;
    input.placeholder = field.placeholder || `${field.label || field.name}…`;
    input.setAttribute("aria-label", field.label || field.name);
    if (settings[field.name] !== undefined && settings[field.name] !== null) {
      input.value = String(settings[field.name]);
    }

    item.append(label, input);
    fieldsContainer.appendChild(item);
  });
  wrapper.appendChild(fieldsContainer);

  const actionsContainer = document.createElement("div");
  actionsContainer.className = "plugin-actions-container";

  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "sys-chip-button plugin-settings-apply";
  apply.textContent = "APPLY";
  actionsContainer.appendChild(apply);

  actions.forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sys-chip-button plugin-action";
    button.dataset.pluginAction = action.name;
    button.textContent = action.label || action.name;
    actionsContainer.appendChild(button);
  });
  wrapper.appendChild(actionsContainer);

  document.querySelector(".session-plugin-chip")?.after(wrapper);
  initProjectRootApply();
  initProjectRoomActions();
}

function initProjectRootApply() {
  const button = document.querySelector(".plugin-settings-apply");
  if (!button || button.dataset.initialized) return;
  button.dataset.initialized = "true";
  button.addEventListener("click", () => {
    const selector = document.querySelector("#session-plugin-select, #plugin-select, .plugin-select");
    sendPluginUpdate(selector?.value || "neutral", document.querySelector("#mode-select")?.value || "default");
  });
}

function initProjectRoomActions() {
  const convId = document.querySelector("[data-conversation-id]")?.dataset?.conversationId;
  if (!convId) return;
  document.querySelectorAll("[data-plugin-action]").forEach((button) => {
    if (button.dataset.actionInitialized) return;
    button.dataset.actionInitialized = "true";
    button.addEventListener("click", async () => {
      const action = button.dataset.pluginAction;
      if (action === "forget" && !window.confirm("Forget this plugin data?")) return;

      // Automatically sync input settings before executing plugin actions so user doesn't need to click APPLY
      const selector = document.querySelector("#session-plugin-select, #plugin-select, .plugin-select");
      const modeSelector = document.querySelector("#mode-select");
      if (document.querySelectorAll("[data-plugin-setting]").length > 0) {
        sendPluginUpdate(selector?.value || "neutral", modeSelector?.value || "default");
      }

      try {
        const response = await fetch(`/api/conversations/${convId}/plugin/action`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });
        const payload = await response.json().catch(() => ({}));
        if (response.ok) {
          if (action === "start_session") {
            showToast("// ТРЕНИРОВКА АКТИВНА: Говорите [Space] или напишите в чат");
          } else if (action === "reset") {
            showToast("// СЕССИЯ СБРОШЕНА");
          } else {
            showToast(`// PLUGIN ACTION: ${action.toUpperCase()}`);
          }
        } else {
          showToast(`// ОШИБКА ДЕЙСТВИЯ: ${payload.detail || response.statusText}`);
        }
      } catch (err) {
        showToast(`// СЕТЕВАЯ ОШИБКА: ${err.message}`);
      }
    });
  });
}

async function pollToolApprovals() {
  const convId = document.querySelector("[data-conversation-id]")?.dataset?.conversationId;
  const dialog = document.querySelector("#tool-approval-dialog");
  if (!convId || !dialog) return;
  const response = await fetch(`/api/conversations/${convId}/tool-approval`).catch(() => null);
  const data = response?.ok ? await response.json() : null;
  const request = data?.requests?.[0];
  if (!request) { dialog.hidden = true; return; }
  dialog.hidden = false;
  dialog.textContent = `Разрешить ${request.tool} в ${request.target || "выбранном проекте"}? `;
  const approve = document.createElement("button"); approve.textContent = "APPROVE";
  approve.onclick = async () => { await fetch(`/api/conversations/${convId}/tool-approval`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({request_id: request.id, args_hash: request.args_hash})}); pollToolApprovals(); };
  dialog.appendChild(approve);
}

window.initProjectRootApply = initProjectRootApply;
window.initProjectRoomActions = initProjectRoomActions;
window.pollToolApprovals = pollToolApprovals;
window.initPluginModal = initPluginModal;


function sendLocaleUpdate(locale) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "set_locale", locale }));
  } else {
    fetch("/api/locale", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locale }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && data.recommended_voice) {
          const voiceSelect = document.querySelector("#voice-select");
          if (voiceSelect) {
            expandOtherVoices(data.recommended_voice);
            voiceSelect.value = data.recommended_voice;
            voiceSelect.dataset.lastVoice = data.recommended_voice;
            updateVoiceAttributes(data.recommended_voice);
          }
        }
      })
      .catch((err) => console.warn("// locale sync error:", err));
  }
}

function initLocaleSelector() {
  const select = document.querySelector("#locale-select");
  if (!select) return;

  const saved = localStorage.getItem("voice_of_luna_locale");
  if (saved && Array.from(select.options).some((opt) => opt.value === saved)) {
    if (select.value !== saved) {
      select.value = saved;
      document.documentElement.lang = saved.startsWith("en") ? "en" : "ru";
    }
  }

  select.addEventListener("change", (e) => {
    const chosenLocale = e.target.value;
    localStorage.setItem("voice_of_luna_locale", chosenLocale);
    document.cookie = `voice_of_luna_locale=${encodeURIComponent(chosenLocale)}; path=/; max-age=31536000; SameSite=Lax`;
    document.documentElement.lang = chosenLocale.startsWith("en") ? "en" : "ru";
    sendLocaleUpdate(chosenLocale);
    showToast(`// LOCALE: ${chosenLocale.toUpperCase()}`);
  });
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
  if (window.speechSynthesis) {
    populateVoices();
  }
  initLocaleSelector();
  initVoiceSelector();
  initSettingsSelectors();
  initPluginSelector();
  initPluginModal();
  initProjectRootApply();
  initProjectRoomActions();
  pollToolApprovals();
  window.setInterval(pollToolApprovals, 1000);
  initVadToggle();
  initRemoteWarmupToggle();
  initLiveTranscriptToggle();
  initRevoiceDialog();
  updateBubbleTransportVisibility(lastVoiceUiState);
  document.querySelectorAll(".log-text").forEach((el) => {
    if (!el.querySelector(".term-link") && !el.querySelector(".log-sources")) {
      formatTerminalText(el);
    }
  });
  scrollFeedToBottom();
  connectWebSocket();
});
