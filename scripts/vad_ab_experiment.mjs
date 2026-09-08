#!/usr/bin/env node

import fs from "node:fs";

export const PROFILES = {
  fast: 300,
  normal: 450,
  patient: 600,
};

// Small deterministic traces keep the harness useful in CI without pretending
// to be production speech measurements. Real traces use the same JSON shape.
const BUILTIN_TRACES = [
  {
    id: "natural_pause",
    expectedResumeAtMs: null,
    samples: [
      [0, 0], [400, 0.08], [800, 0.08], [900, 0], [1300, 0],
    ],
  },
  {
    id: "short_pause_then_resume",
    expectedResumeAtMs: 1220,
    samples: [
      [0, 0], [400, 0.08], [800, 0], [1100, 0], [1220, 0.08], [1500, 0], [2000, 0],
    ],
  },
];

export function detectEndpoints(trace, silenceTimeoutMs, threshold = 0.055, minSpeechMs = 350) {
  let speechStart = null;
  let silenceStart = null;
  const endpoints = [];

  for (const [timeMs, volume] of trace.samples) {
    if (volume >= threshold) {
      if (speechStart === null) speechStart = timeMs;
      silenceStart = null;
      continue;
    }
    if (speechStart === null || timeMs - speechStart < minSpeechMs) continue;
    if (silenceStart === null) silenceStart = timeMs;
    if (timeMs - silenceStart >= silenceTimeoutMs) {
      endpoints.push(timeMs);
      speechStart = null;
      silenceStart = null;
    }
  }
  return endpoints;
}

export function runExperiment(traces) {
  return Object.entries(PROFILES).map(([profile, timeoutMs]) => {
    const rows = traces.map((trace) => {
      const endpoints = detectEndpoints(trace, timeoutMs);
      const falseEnds = trace.expectedResumeAtMs === null
        ? 0
        : endpoints.filter((endpoint) => endpoint < trace.expectedResumeAtMs).length;
      return { id: trace.id, endpoints, falseEnds };
    });
    return {
      profile,
      silenceTimeoutMs: timeoutMs,
      traces: rows,
      endpointCount: rows.reduce((sum, row) => sum + row.endpoints.length, 0),
      falseEndCount: rows.reduce((sum, row) => sum + row.falseEnds, 0),
    };
  });
}

const inputPath = process.argv[2];
const traces = inputPath
  ? JSON.parse(fs.readFileSync(inputPath, "utf8"))
  : BUILTIN_TRACES;
console.log(JSON.stringify({ source: inputPath || "builtin-synthetic", results: runExperiment(traces) }, null, 2));
