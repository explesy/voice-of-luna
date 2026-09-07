/**
 * AudioWorklet processor for low-latency Float32 PCM audio capture.
 * Runs on the dedicated audio rendering thread, preventing UI jank from dropping audio samples.
 */

class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.isRecording = true;
    this.bufferSize = 1024;
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;

    this.port.onmessage = (event) => {
      if (event.data && event.data.command === "stop") {
        this.flush();
        this.isRecording = false;
      } else if (event.data && event.data.command === "start") {
        this.bufferIndex = 0;
        this.isRecording = true;
      }
    };
  }

  flush() {
    if (this.bufferIndex > 0) {
      const chunk = this.buffer.slice(0, this.bufferIndex);
      this.port.postMessage(
        {
          type: "pcm_data",
          buffer: chunk.buffer,
        },
        [chunk.buffer]
      );
      this.bufferIndex = 0;
    }
  }

  process(inputs) {
    if (!this.isRecording) {
      return true;
    }

    const input = inputs[0];
    if (input && input.length > 0) {
      const channelData = input[0];
      if (channelData && channelData.length > 0) {
        let offset = 0;
        while (offset < channelData.length) {
          const toCopy = Math.min(channelData.length - offset, this.bufferSize - this.bufferIndex);
          this.buffer.set(channelData.subarray(offset, offset + toCopy), this.bufferIndex);
          this.bufferIndex += toCopy;
          offset += toCopy;

          if (this.bufferIndex >= this.bufferSize) {
            this.port.postMessage(
              {
                type: "pcm_data",
                buffer: this.buffer.buffer,
              },
              [this.buffer.buffer]
            );
            this.buffer = new Float32Array(this.bufferSize);
            this.bufferIndex = 0;
          }
        }
      }
    }

    return true;
  }
}

registerProcessor("pcm-recorder-processor", PcmRecorderProcessor);
