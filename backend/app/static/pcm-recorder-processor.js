/**
 * AudioWorklet processor for low-latency Float32 PCM audio capture.
 * Runs on the dedicated audio rendering thread, preventing UI jank from dropping audio samples.
 */

class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.isRecording = true;
    this.port.onmessage = (event) => {
      if (event.data && event.data.command === "stop") {
        this.isRecording = false;
      } else if (event.data && event.data.command === "start") {
        this.isRecording = true;
      }
    };
  }

  process(inputs) {
    if (!this.isRecording) {
      return true;
    }

    const input = inputs[0];
    if (input && input.length > 0) {
      const channelData = input[0];
      if (channelData && channelData.length > 0) {
        // Copy channel data to avoid mutating underlying audio thread buffers
        const pcmChunk = new Float32Array(channelData.length);
        pcmChunk.set(channelData);
        this.port.postMessage(
          {
            type: "pcm_data",
            buffer: pcmChunk.buffer,
          },
          [pcmChunk.buffer]
        );
      }
    }

    return true;
  }
}

registerProcessor("pcm-recorder-processor", PcmRecorderProcessor);
