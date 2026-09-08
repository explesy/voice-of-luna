import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/browser",
  fullyParallel: false,
  timeout: 30_000,
  reporter: [["list"], ["junit", { outputFile: "test-results/playwright.xml" }]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8765",
    url: "http://127.0.0.1:8765/",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
