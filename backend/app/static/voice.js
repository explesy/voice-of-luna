let recorder;
let chunks = [];

function status(text) {
  const element = document.querySelector("[data-voice-status]");
  if (element) element.textContent = text;
}

function speakLatestResponse() {
  if (!window.speechSynthesis) return;
  const responses = document.querySelectorAll("[data-spoken-response]");
  const latest = responses[responses.length - 1];
  if (!latest || latest.dataset.spoken) return;
  latest.dataset.spoken = "true";
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(latest.textContent));
}

async function startRecording(button) {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    status("Recording is not supported by this browser");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      stream.getTracks().forEach((track) => track.stop());
      button.textContent = "Start recording";
      button.dataset.recording = "false";
      await sendRecording(button.dataset.audioUrl, recorder.mimeType || "audio/webm");
    });
    recorder.start();
    button.textContent = "Stop recording";
    button.dataset.recording = "true";
    status("Listening… click Stop recording when you finish");
  } catch (error) {
    status(`Microphone unavailable: ${error.message}`);
  }
}

async function sendRecording(url, type) {
  status("Thinking…");
  const body = new FormData();
  body.append("audio", new Blob(chunks, { type }), "recording.webm");
  try {
    const response = await fetch(url, { method: "POST", body });
    if (!response.ok) throw new Error(await response.text());
    document.querySelector("#conversation").innerHTML = await response.text();
    status("Microphone is off");
    speakLatestResponse();
  } catch (error) {
    status(`Voice message failed: ${error.message}`);
  }
}

document.addEventListener("click", (event) => {
  const recordButton = event.target.closest("[data-record]");
  if (recordButton) {
    if (recorder?.state === "recording") recorder.stop();
    else startRecording(recordButton);
  }
  if (event.target.closest("[data-stop-speaking]")) window.speechSynthesis?.cancel();
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "conversation") speakLatestResponse();
});
