/**
 * Web Audio buffers, WAV encoding, and audio queue playback scheduler.
 */

function mergeBuffers(buffers) {
  let totalLength = 0;
  for (let i = 0; i < buffers.length; i++) {
    totalLength += buffers[i].length;
  }
  const result = new Float32Array(totalLength);
  let offset = 0;
  for (let i = 0; i < buffers.length; i++) {
    result.set(buffers[i], offset);
    offset += buffers[i].length;
  }
  return result;
}

function resampleTo16k(audioBuffer, sourceSampleRate) {
  if (sourceSampleRate === 16000) return audioBuffer;
  const ratio = sourceSampleRate / 16000;
  const newLength = Math.round(audioBuffer.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const index = i * ratio;
    const low = Math.floor(index);
    const high = Math.min(low + 1, audioBuffer.length - 1);
    const weight = index - low;
    result[i] = audioBuffer[low] * (1 - weight) + audioBuffer[high] * weight;
  }
  return result;
}

function encodeWav16k(samples) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  function writeString(offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }

  return new Blob([view], { type: "audio/wav" });
}
var playbackAudioContext = null;
var audioQueue = [];
var activeScheduledSources = [];
var nextAudioChunkStartTime = 0;
var isAudioQueuePlaying = false;
var currentAudioElement = null;
var activePlayer = null;
var currentStreamingEntry = null;
var firstAudioPlayTime = null;
var firstAudioSoundOffsetMs = null;
var lastSpeechEndTime = null;
var latestTiming = null;
var isAudioPaused = false;

function notifyVoiceState(state, message, modeLabel) {
  if (typeof setVoiceState === "function") {
    setVoiceState(state, message, modeLabel);
  } else if (typeof window.setVoiceState === "function") {
    window.setVoiceState(state, message, modeLabel);
  }
}

function notifyLatencyHud(timing, clientE2e) {
  if (typeof updateLatencyHud === "function") {
    updateLatencyHud(timing, clientE2e);
  } else if (typeof window.updateLatencyHud === "function") {
    window.updateLatencyHud(timing, clientE2e);
  }
}

function sendPlaybackLifecycle(state, item, deliveredText = null) {
  if (typeof socket === "undefined" || !socket || socket.readyState !== WebSocket.OPEN) return;
  if (!item || !item.clipId || !item.turnId) return;
  socket.send(JSON.stringify({
    type: "output_event",
    state,
    turn_id: item.turnId,
    clip_id: item.clipId,
    delivered_text: deliveredText,
    replay: Boolean(item.replay),
  }));
}

function measureFirstSoundOffset(audioBuffer) {
  if (!audioBuffer || !audioBuffer.length || !audioBuffer.sampleRate) return null;
  const threshold = 0.003;
  const channels = audioBuffer.numberOfChannels || 1;
  for (let index = 0; index < audioBuffer.length; index++) {
    let audible = false;
    for (let channel = 0; channel < channels; channel++) {
      if (Math.abs(audioBuffer.getChannelData(channel)[index]) > threshold) {
        audible = true;
        break;
      }
    }
    if (audible) return Math.round((index / audioBuffer.sampleRate) * 1000);
  }
  return null;
}

function stopAudioPlayback() {
  // Any paused-but-started item has already emitted "started"; report it as
  // interrupted now that barge-in stops it. Not-yet-started queue items are
  // dropped silently because they never began playback.
  audioQueue.forEach((item) => {
    if (item._wasStarted && !item._interrupted) {
      item._interrupted = true;
      sendPlaybackLifecycle("interrupted", item);
    }
  });
  audioQueue = [];
  isAudioQueuePlaying = false;
  isAudioPaused = false;
  nextAudioChunkStartTime = 0;

  activeScheduledSources.forEach((src) => {
    if (src._lunaItem) {
      src._lunaItem._interrupted = true;
      sendPlaybackLifecycle("interrupted", src._lunaItem);
    }
    try {
      src.stop();
      src.disconnect();
    } catch (_) {}
  });
  activeScheduledSources = [];

  if (currentAudioElement) {
    const fallbackItem = currentAudioElement._lunaItem;
    if (fallbackItem && fallbackItem._wasStarted && !fallbackItem._interrupted) {
      fallbackItem._interrupted = true;
      sendPlaybackLifecycle("interrupted", fallbackItem);
    }
    try {
      currentAudioElement.pause();
      currentAudioElement.currentTime = 0;
    } catch (_) {}
    currentAudioElement = null;
  }
}

function pauseAudioPlayback() {
  if (isAudioPaused) return;
  if (activeScheduledSources.length === 0 && !currentAudioElement) return;
  isAudioPaused = true;
  const ctx = playbackAudioContext;
  const now = ctx ? ctx.currentTime : 0;
  const pausedItems = [];
  activeScheduledSources.forEach((src) => {
    const item = src._lunaItem;
    if (item && !item._interrupted && item.audioBuffer) {
      const startedAt = item._startTime != null ? item._startTime : now;
      const elapsed = Math.max(0, now - startedAt);
      // Remember the playback offset so resume continues mid-clip (D2).
      // ``_playOffset`` is the offset this playback started from.
      item._pausedOffset = Math.min((item._playOffset || 0) + elapsed, item.audioBuffer.duration);
      pausedItems.push(item);
    }
    // Suppress the synthetic "ended" that stop() fires so we do not report a
    // paused clip as completed.
    src._pausing = true;
    try {
      src.stop();
      src.disconnect();
    } catch (_) {}
  });
  activeScheduledSources = [];
  audioQueue = [...pausedItems, ...audioQueue];
  isAudioQueuePlaying = false;
  nextAudioChunkStartTime = 0;
  if (currentAudioElement) {
    try {
      currentAudioElement.pause();
    } catch (_) {}
  }
}

function resumeAudioPlayback() {
  if (!isAudioPaused) return;
  isAudioPaused = false;
  if (currentAudioElement) {
    currentAudioElement.play().catch(() => {});
    return;
  }
  scheduleAudioPlayback();
}

function getPlaybackContext() {
  if (!playbackAudioContext || playbackAudioContext.state === "closed") {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) {
      playbackAudioContext = new AudioCtx();
    }
  }
  if (playbackAudioContext && playbackAudioContext.state === "suspended") {
    playbackAudioContext.resume().catch(() => {});
  }
  return playbackAudioContext;
}
async function enqueueAudioChunk(url, entry, audioBase64 = null, mimeType = "audio/wav", rawArrayBuffer = null, turnId = null, clipId = null, text = null, replay = false) {
  const targetEntry = entry || currentStreamingEntry;
  let blobUrl = null;
  let audioBuffer = null;

  if (rawArrayBuffer) {
    try {
      const effectiveMime = mimeType || (url && url.endsWith(".mp3") ? "audio/mpeg" : "audio/wav");
      const blob = new Blob([rawArrayBuffer], { type: effectiveMime });
      blobUrl = URL.createObjectURL(blob);

      const ctx = getPlaybackContext();
      if (ctx && ctx.decodeAudioData) {
        audioBuffer = await ctx.decodeAudioData(rawArrayBuffer.slice(0));
      }
    } catch (e) {
      console.warn("// inline binary audio decode error:", e);
    }
  } else if (audioBase64) {
    try {
      const binary = atob(audioBase64);
      const len = binary.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      const effectiveMime = mimeType || (url && url.endsWith(".mp3") ? "audio/mpeg" : "audio/wav");
      const blob = new Blob([bytes], { type: effectiveMime });
      blobUrl = URL.createObjectURL(blob);

      const ctx = getPlaybackContext();
      if (ctx && ctx.decodeAudioData) {
        audioBuffer = await ctx.decodeAudioData(bytes.buffer.slice(0));
      }
    } catch (e) {
      console.warn("// inline audio decode error:", e);
    }
  }

  audioQueue.push({ url, blobUrl, audioBuffer, entry: targetEntry, turnId, clipId, text, replay });
  if (firstAudioSoundOffsetMs == null && audioBuffer) {
    firstAudioSoundOffsetMs = measureFirstSoundOffset(audioBuffer);
    if (firstAudioSoundOffsetMs != null) {
      const clientE2e = firstAudioPlayTime && lastSpeechEndTime ? Math.round(firstAudioPlayTime - lastSpeechEndTime) : null;
      notifyLatencyHud(latestTiming, clientE2e);
    }
  }
  scheduleAudioPlayback();
}

function scheduleAudioPlayback() {
  if (isAudioPaused) return;
  const ctx = getPlaybackContext();

  while (audioQueue.length > 0) {
    const item = audioQueue[0];
    if (ctx && item.audioBuffer) {
      audioQueue.shift();
      isAudioQueuePlaying = true;
      notifyVoiceState("speaking", "Luna responding... Press [Esc] to stop", "SPEAKING // STREAM");

      const now = ctx.currentTime;
      const offset = item._pausedOffset || 0;
      const startTime = Math.max(now + 0.005, nextAudioChunkStartTime);
      const source = ctx.createBufferSource();
      source._lunaItem = item;
      source.buffer = item.audioBuffer;
      source.connect(ctx.destination);
      source.start(startTime, offset);
      item._startTime = startTime;
      item._playOffset = offset;
      item._pausedOffset = 0;
      if (!item._wasStarted) sendPlaybackLifecycle("started", item);
      item._wasStarted = true;
      activeScheduledSources.push(source);
      nextAudioChunkStartTime = startTime + Math.max(0, item.audioBuffer.duration - offset);

      if (!firstAudioPlayTime && lastSpeechEndTime) {
        firstAudioPlayTime = performance.now();
        const clientE2e = Math.round(firstAudioPlayTime - lastSpeechEndTime);
        notifyLatencyHud(latestTiming, clientE2e);
      }

      source.onended = () => {
        if (source._pausing) return;
        if (!item._interrupted) sendPlaybackLifecycle("completed", item, item.text);
        const idx = activeScheduledSources.indexOf(source);
        if (idx !== -1) activeScheduledSources.splice(idx, 1);
        if (activeScheduledSources.length === 0) {
          if (audioQueue.length > 0) {
            scheduleAudioPlayback();
          } else {
            isAudioQueuePlaying = false;
            nextAudioChunkStartTime = 0;
            notifyVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
          }
        }
      };
    } else {
      if (!isAudioQueuePlaying && activeScheduledSources.length === 0) {
        playNextAudioChunkFallback();
      }
      break;
    }
  }
}

async function playNextAudioChunkFallback() {
  if (audioQueue.length === 0) {
    if (activeScheduledSources.length === 0) {
      isAudioQueuePlaying = false;
      currentAudioElement = null;
      notifyVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    }
    return;
  }

  isAudioQueuePlaying = true;
  const item = audioQueue.shift();
  const url = item.url;
  const blobUrl = item.blobUrl;
  const entry = item.entry;

  let playUrl = blobUrl;
  if (!playUrl && url) {
    try {
      const res = await fetch(url);
      if (res.ok) {
        const blob = await res.blob();
        playUrl = URL.createObjectURL(blob);
      }
    } catch (_) {
      playUrl = url;
    }
  }

  const audio = new Audio(playUrl);
  audio._lunaItem = item;
  item._wasStarted = true;
  sendPlaybackLifecycle("started", item);
  currentAudioElement = audio;
  activePlayer = audio;
  notifyVoiceState("speaking", "Luna responding... Press [Esc] to stop", "SPEAKING // STREAM");

  audio.addEventListener("play", () => {
    if (!firstAudioPlayTime && lastSpeechEndTime) {
      firstAudioPlayTime = performance.now();
      const clientE2e = Math.round(firstAudioPlayTime - lastSpeechEndTime);
      notifyLatencyHud(latestTiming, clientE2e);
    }
  }, { once: true });

  function onChunkFinished() {
    if (!item._interrupted) sendPlaybackLifecycle("completed", item, item.text);
    isAudioQueuePlaying = false;
    currentAudioElement = null;
    activePlayer = null;
    if (audioQueue.length > 0) {
      scheduleAudioPlayback();
    } else if (activeScheduledSources.length === 0) {
      nextAudioChunkStartTime = 0;
      notifyVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
    }
  }

  audio.addEventListener("ended", onChunkFinished, { once: true });
  audio.addEventListener("error", onChunkFinished, { once: true });

  audio.play().catch(() => {
    onChunkFinished();
  });
}

function playAudioQueue(urls) {
  if (!urls || urls.length === 0) return;
  if (typeof stopSpeaking === "function") {
    stopSpeaking();
  } else {
    stopAudioPlayback();
  }
  notifyVoiceState("speaking", "Replaying audio output...", "SPEAKING // REPLAY");

  let index = 0;
  function playNext() {
    if (index >= urls.length) {
      activePlayer = null;
      currentAudioElement = null;
      notifyVoiceState("idle", "Press [Space] or click radar to speak", "IDLE // READY");
      return;
    }
    const url = urls[index++];
    const audio = new Audio(url);
    activePlayer = audio;
    currentAudioElement = audio;

    audio.addEventListener("ended", () => {
      playNext();
    }, { once: true });

    audio.addEventListener("error", () => {
      playNext();
    }, { once: true });

    audio.play().catch(() => {
      playNext();
    });
  }

  playNext();
}


window.stopAudioPlayback = stopAudioPlayback;
window.pauseAudioPlayback = pauseAudioPlayback;
window.resumeAudioPlayback = resumeAudioPlayback;
window.mergeBuffers = mergeBuffers;
window.resampleTo16k = resampleTo16k;
window.encodeWav16k = encodeWav16k;
window.getPlaybackContext = getPlaybackContext;
window.enqueueAudioChunk = enqueueAudioChunk;
window.scheduleAudioPlayback = scheduleAudioPlayback;
window.playNextAudioChunkFallback = playNextAudioChunkFallback;
window.playAudioQueue = playAudioQueue;
window.sendPlaybackLifecycle = sendPlaybackLifecycle;
