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
            pcmSamples.push(new Float32Array(event.data.buffer));
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
          pcmSamples.push(new Float32Array(input));
        };
        micSourceNode.connect(pcmProcessorNode);
        pcmProcessorNode.connect(audioContext.destination);
        workletInitialized = true;
      } catch (err) {
        console.warn("// ScriptProcessor failed, falling back to MediaRecorder:", err);
      }
    }

    window.isDirectPcmActive = workletInitialized;

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

      const stage = document.querySelector(".radar-stage");
      if (stage) {
        stage.style.setProperty("--volume", normalizedVolume.toFixed(3));
      }

      // VAD (Voice Activity Detection) during active recording
      if (window.vadEnabled && isRecordingActive) {
        const now = performance.now();
        const threshold = window.VAD_VOLUME_THRESHOLD || 0.055;
        const minDuration = window.VAD_MIN_SPEECH_DURATION_MS || 350;
        const silenceTimeout = window.VAD_SILENCE_TIMEOUT_MS || 450;

        if (normalizedVolume >= threshold) {
          if (!window.vadSpeechDetected) {
            window.vadSpeechDetected = true;
            window.speechStartTime = now;
          }
          window.vadSilenceStartTime = null;
          speechEndDetectedAt = null;
        } else if (window.vadSpeechDetected && window.speechStartTime && (now - window.speechStartTime) >= minDuration) {
          if (!window.vadSilenceStartTime) {
            window.vadSilenceStartTime = now;
            speechEndDetectedAt = now;
          } else if (now - window.vadSilenceStartTime >= silenceTimeout) {
            console.log("// VAD auto-stop: silence detected for", Math.round(now - window.vadSilenceStartTime), "ms");
            window.vadSpeechDetected = false;
            window.vadSilenceStartTime = null;
            window.speechStartTime = null;
            stopRecording();
            return;
          }
        }
      }

      animationFrameId = requestAnimationFrame(updateVolume);
    }

    updateVolume();
  } catch (error) {
    console.warn("Web Audio API analyser error:", error);
  }
}

function stopAudioAnalyser() {
  window.vadSpeechDetected = false;
  window.vadSilenceStartTime = null;
  window.speechStartTime = null;
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
  const stage = document.querySelector(".radar-stage");
  const modePill = document.querySelector("[data-mode-pill]");
  const statusEl = document.querySelector("[data-voice-status]");
  const recordBtns = document.querySelectorAll("[data-record]");
  const stopBtns = document.querySelectorAll("[data-stop-speaking]");

  if (stage) {
    stage.classList.remove("state-idle", "state-listening", "state-thinking", "state-speaking");
    stage.classList.add(`state-${state}`);
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
    if (state === "speaking") {
      btn.classList.add("is-active");
    } else {
      btn.classList.remove("is-active");
    }
  });
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
  const activePlugin = document.querySelector(".plugin-select")?.value || localStorage.getItem("voice_of_luna_plugin");
  if (activePlugin === "spanish_buddy" || /[áéíóúüñ¿¡]/i.test(text)) return "es-ES";
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

  if (parts.length > 0) {
    val.textContent = parts.join(" | ");
    hud.style.display = "inline-flex";
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
  feed.appendChild(entry);

  scrollFeedToBottom();
  return entry;
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
      if (currentVoice || currentModel || currentEffort) {
        socket.send(JSON.stringify({
          type: "set_settings",
          voice: currentVoice,
          model: currentModel,
          effort: currentEffort,
          binary_audio: true,
        }));
      } else {
        socket.send(JSON.stringify({
          type: "set_settings",
          binary_audio: true,
        }));
      }
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
        enqueueAudioChunk(
          meta.audio_url || (meta.clip_id ? `/speech/${meta.clip_id}` : null),
          currentStreamingEntry,
          null,
          meta.mime_type || "audio/wav",
          audioBuffer
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
  } else if (data.type === "plugin_updated") {
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
    setVoiceState(data.state, data.message, data.mode_label);
  } else if (data.type === "transcript") {
    appendMessageToFeed("user", data.text);
    currentStreamingEntry = null;
  } else if (data.type === "delta") {
    if (!currentStreamingEntry) {
      currentStreamingEntry = appendMessageToFeed("assistant", "");
    }
    const textEl = currentStreamingEntry.querySelector(".log-text");
    if (textEl) {
      textEl.textContent += data.delta;
      scrollFeedToBottom();
    }
  } else if (data.type === "audio_chunk") {
    enqueueAudioChunk(data.audio_url, currentStreamingEntry, data.audio_base64, data.mime_type);
  } else if (data.type === "turn_completed") {
    if (data.tts_engine) {
      updateFooterStatus(data.tts_engine);
    }
    if (data.timing) {
      latestTiming = data.timing;
      const clientE2e = firstAudioPlayTime && lastSpeechEndTime ? Math.round(firstAudioPlayTime - lastSpeechEndTime) : null;
      updateLatencyHud(data.timing, clientE2e);
    }
    if (currentStreamingEntry) {
      const textEl = currentStreamingEntry.querySelector(".log-text");
      if (textEl) {
        formatTerminalText(textEl);
      }
    }
    currentStreamingEntry = null;
    updateTurnsCount();
  } else if (data.type === "error") {
    showToast(`// error: ${data.message}`);
    setVoiceState("idle", data.message, "ERR // SERVER");
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

async function replayLatestResponse() {
  stopSpeaking();
  const responses = document.querySelectorAll("[data-spoken-response]");
  const latest = responses[responses.length - 1];
  if (!latest) {
    showToast("// buffer empty. no response to replay");
    return;
  }

  showToast("// replaying last response");

  // 1. If in-memory audio chunk URLs are cached for this response, replay them directly
  if (latest._audioUrls && latest._audioUrls.length > 0) {
    playAudioQueue(latest._audioUrls);
    return;
  }

  // 2. If server audio element exists in DOM and is playable
  const localAudio = latest.querySelector("[data-server-audio]");
  if (localAudio && localAudio.src) {
    activePlayer = localAudio;
    localAudio.currentTime = 0;
    setVoiceState("speaking", "Replaying audio output...", "SPEAKING // REPLAY");
    try {
      await localAudio.play();
      return;
    } catch (_) {
      // Audio element failed or expired, fall through to synthesis
    }
  }

  const rawText = (latest.querySelector(".log-text")?.dataset?.rawText || latest.querySelector(".log-text")?.textContent || latest.textContent || "").trim();
  const text = sanitizeForSpeech(rawText);
  if (!text) return;

  // 3. For Cyrillic text, attempt server-side synthesis via LocalMacOsSpeaker (say -v Milena)
  if (/\p{Script=Cyrillic}/u.test(text)) {
    try {
      const chosenVoice = document.querySelector("#voice-select")?.value || null;
      const resp = await fetch("/api/speech/synthesize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: chosenVoice }),
      });
      if (resp.ok) {
        const blob = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        latest._audioUrls = [blobUrl];
        playAudioQueue([blobUrl]);
        return;
      }
    } catch (err) {
      console.warn("Server speech synthesis failed, falling back to browser synth:", err);
    }
  }

  // 4. Fallback to browser SpeechSynthesis
  if (!window.speechSynthesis) {
    setVoiceState("idle", "Speech synthesis unsupported", "ERR // NO_TTS");
    return;
  }

  const lang = languageFor(text);
  const voice = voiceFor(lang);

  if (lang === "ru-RU" && !voice) {
    showToast("// no Russian voice found in browser");
    setVoiceState("idle", "No Russian voice available", "ERR // NO_VOICE");
    return;
  }

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = voice?.lang || lang;
  if (voice) {
    utterance.voice = voice;
  }

  utterance.addEventListener("start", () => {
    setVoiceState("speaking", "Replaying speech output...", "SPEAKING // REPLAY");
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

async function startRecording(recordBtn) {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setVoiceState("idle", "Audio input unsupported by browser", "ERR // NO_MIC");
    showToast("// microphone not supported");
    return;
  }

  // BARGE-IN: Stop current output immediately
  stopSpeaking();

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    activeRecordingStream = stream;
    await initAudioAnalyser(stream);

    audioChunks = [];
    speechEndDetectedAt = null;
    isRecordingActive = true;

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

  // Stop Speaking Button (Barge-in)
  if (event.target.closest("[data-stop-speaking]")) {
    stopSpeaking();
    return;
  }

  // Replay Last Response
  if (event.target.closest("[data-replay]")) {
    replayLatestResponse();
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
    if (hasOption && select.value !== saved) {
      select.value = saved;
      select.dataset.lastVoice = saved;
      updateVoiceAttributes(saved);
      sendVoiceUpdate(saved);
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
      const isVoiceInGroup = select.querySelector(`#other-voices-group option[value="${CSS.escape(last)}"]`);
      const fallback = isVoiceInGroup ? (select.dataset.russianVoice || select.options[0]?.value) : last;
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

    select.dataset.lastVoice = chosenVal;
    localStorage.setItem("voice_of_luna_voice", chosenVal);
    document.cookie = `voice_of_luna_voice=${encodeURIComponent(chosenVal)}; path=/; max-age=31536000; SameSite=Lax`;
    updateVoiceAttributes(chosenVal);
    sendVoiceUpdate(chosenVal);
    showToast(`// VOICE ACTIVE: ${chosenVal.toUpperCase()}`);
  });
}

function updateVoiceAttributes(voiceName) {
  const chip = document.querySelector(".voice-selector-chip");
  if (chip) chip.dataset.russianVoice = voiceName;
  document.body.dataset.russianVoice = voiceName;
}

function getOrCreateDownloadCard(modelId) {
  const container = document.getElementById("toast-container");
  if (!container) return null;
  let card = document.getElementById(`tts-download-card-${modelId}`);
  if (!card) {
    card = document.createElement("div");
    card.id = `tts-download-card-${modelId}`;
    card.className = "toast-progress";
    card.innerHTML = `
      <div class="toast-progress-header">
        <span class="toast-title"><span class="tts-spinner">⟳</span> ЗАГРУЗКА МОДЕЛИ...</span>
        <span class="toast-pct">0%</span>
      </div>
      <div class="toast-progress-bar-bg">
        <div class="toast-progress-bar-fill"></div>
      </div>
      <div class="toast-progress-meta">
        <span class="toast-size">0.0 / 60.0 МБ</span>
        <span class="toast-eta">соединение...</span>
      </div>
    `;
    container.appendChild(card);
  }
  return card;
}

function updateDownloadUI(modelId, voiceName, status) {
  const chip = document.querySelector(".voice-selector-chip");
  const card = getOrCreateDownloadCard(modelId);
  const select = document.querySelector("#voice-select");
  const opt = select ? select.querySelector(`option[value="${CSS.escape(voiceName)}"]`) : null;

  if (status.status === "downloading") {
    if (chip) chip.classList.add("is-downloading");
    const pct = status.progress_percent || 0;
    const downloaded = status.downloaded_mb != null ? Number(status.downloaded_mb).toFixed(1) : "0.0";
    const total = status.total_mb != null ? Number(status.total_mb).toFixed(1) : "60.0";
    const speed = status.speed_kbps ? `${Math.round(status.speed_kbps)} КБ/с` : "загрузка...";
    const eta = status.eta_seconds ? `~${status.eta_seconds} сек` : "вычисление времени...";

    if (card) {
      const titleEl = card.querySelector(".toast-title");
      const pctEl = card.querySelector(".toast-pct");
      const fillEl = card.querySelector(".toast-progress-bar-fill");
      const sizeEl = card.querySelector(".toast-size");
      const etaEl = card.querySelector(".toast-eta");

      if (titleEl) titleEl.innerHTML = `<span class="tts-spinner">⟳</span> СКАЧИВАНИЕ: ${(status.name || modelId).toUpperCase()}`;
      if (pctEl) pctEl.textContent = `${pct}%`;
      if (fillEl) fillEl.style.width = `${pct}%`;
      if (sizeEl) sizeEl.textContent = `${downloaded} / ${total} МБ (${speed})`;
      if (etaEl) etaEl.textContent = `осталось ${eta}`;
    }

    if (opt && (opt.textContent.includes("[↓") || opt.textContent.includes("[⟳"))) {
      opt.textContent = `${voiceName} [⟳ ${pct}%]`;
    }
  } else if (status.status === "ready") {
    if (chip) chip.classList.remove("is-downloading");
    if (card) {
      card.classList.add("is-complete");
      const titleEl = card.querySelector(".toast-title");
      const pctEl = card.querySelector(".toast-pct");
      const fillEl = card.querySelector(".toast-progress-bar-fill");
      const sizeEl = card.querySelector(".toast-size");
      const etaEl = card.querySelector(".toast-eta");

      if (titleEl) titleEl.innerHTML = `✔ МОДЕЛЬ ГОТОВА: ${(status.name || modelId).toUpperCase()}`;
      if (pctEl) pctEl.textContent = `100%`;
      if (fillEl) fillEl.style.width = `100%`;
      if (sizeEl) sizeEl.textContent = `${status.total_mb || 60} МБ установлено`;
      if (etaEl) etaEl.textContent = `голос активен`;

      setTimeout(() => {
        card.style.transition = "opacity 0.5s ease, transform 0.5s ease";
        card.style.opacity = "0";
        card.style.transform = "translateY(-10px)";
        setTimeout(() => card.remove(), 500);
      }, 3500);
    }

    if (opt) {
      opt.dataset.installed = "true";
      opt.textContent = `${voiceName} ★`;
    }
  } else if (status.status === "error") {
    if (chip) chip.classList.remove("is-downloading");
    if (card) {
      card.classList.add("is-error");
      const titleEl = card.querySelector(".toast-title");
      const sizeEl = card.querySelector(".toast-size");
      const etaEl = card.querySelector(".toast-eta");

      if (titleEl) titleEl.innerHTML = `✖ ОШИБКА: ${modelId.toUpperCase()}`;
      if (sizeEl) sizeEl.textContent = status.error || "Сбой загрузки";
      if (etaEl) etaEl.textContent = "повторите попытку";
      setTimeout(() => {
        card.remove();
      }, 6000);
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
    const savedModel = localStorage.getItem("voice_of_luna_model");
    if (savedModel) {
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
        const chosenMode = modeSelect ? modeSelect.value : "default";
        localStorage.setItem("voice_of_luna_plugin_mode", chosenMode);
        document.cookie = `voice_of_luna_plugin_mode=${encodeURIComponent(chosenMode)}; path=/; max-age=31536000; SameSite=Lax`;

        if (chosenPlugin === "spanish_buddy") {
          const voiceSelect = document.querySelector("#voice-select");
          if (voiceSelect) {
            expandOtherVoices();
            const spanishOpt = Array.from(voiceSelect.options).find(
              (opt) => /mónica|monica|paulina|es_/i.test(opt.text) || /es_/i.test(opt.value)
            );
            if (spanishOpt) {
              voiceSelect.value = spanishOpt.value;
              voiceSelect.dataset.lastVoice = spanishOpt.value;
              updateVoiceAttributes(spanishOpt.value);
              sendVoiceUpdate(spanishOpt.value);
            }
          }
        }

        sendPluginUpdate(chosenPlugin, chosenMode);
        showToast(`// PLUGIN: ${chosenPlugin.toUpperCase()}`);
      });
    }
  });

  updateModeOptions(pluginSelectors[0].value, savedMode);

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
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "set_plugin", plugin_id: pluginId, mode: mode }));
  }
  const convElem = document.querySelector("[data-conversation-id]");
  const convId = convElem?.dataset?.conversationId;
  if (convId) {
    fetch(`/api/conversations/${convId}/plugin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin_id: pluginId, mode: mode }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
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
  initVadToggle();
  initRemoteWarmupToggle();
  document.querySelectorAll(".log-text").forEach((el) => {
    if (!el.querySelector(".term-link") && !el.querySelector(".log-sources")) {
      formatTerminalText(el);
    }
  });
  scrollFeedToBottom();
  connectWebSocket();
});
