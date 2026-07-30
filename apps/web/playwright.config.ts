import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "tsx tests/e2e/mock-agent-server.ts",
      url: "http://127.0.0.1:18001/healthz",
      reuseExistingServer: true,
    },
    {
      command: "pnpm dev --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
      env: {
        SESSION_SECRET: "playwright-local-session-secret-32-bytes",
        AGENT_SERVICE_URL: "http://127.0.0.1:18001",
        WEB_DATABASE_URL: "file:./data/playwright.sqlite",
      },
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
