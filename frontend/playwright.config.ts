import { existsSync } from "node:fs";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import { BACKEND_PORT, BACKEND_URL, BASE_URL, E2E_DB_FILE, FRONTEND_PORT } from "./e2e/constants";
import { quotedPython } from "./e2e/python";

/**
 * End-to-end config for the Trip Mate web app.
 *
 * Two servers are started automatically so the suite is self-contained:
 *   - the FastAPI backend on :8099, pointed at a throwaway SQLite file
 *   - the Next.js frontend on :3099, pointed at that backend
 *
 * `RATE_LIMIT_ENABLED=false` is required — the login/register/OTP limits are
 * a handful per minute, which the suite would trip immediately.
 */

const PYTHON = quotedPython();

/**
 * Escape hatch for environments where Playwright must not manage the servers
 * itself — a sandbox that proxies localhost (its readiness probe can never see
 * a 2xx), or a developer iterating who does not want the servers restarted on
 * every run.
 *
 * Usage: start the API and the web app yourself, then
 *   E2E_NO_WEBSERVER=1 npm run test:e2e
 */
const NO_WEBSERVER = process.env.E2E_NO_WEBSERVER === "1";

export default defineConfig({
  testDir: "./e2e",
  // Every flow shares one backend database, so run them serially.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: NO_WEBSERVER
    ? undefined
    : [
    {
      command: `${PYTHON} -m uvicorn app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: "../backend",
      url: `${BACKEND_URL}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        ENV: "development",
        SECRET_KEY: "e2e-secret-key-not-for-production",
        DATABASE_URL: `sqlite+aiosqlite:///./${E2E_DB_FILE}`,
        RATE_LIMIT_ENABLED: "false",
        STORAGE_BACKEND: "local",
        LOCAL_UPLOAD_DIR: "./var/e2e-uploads",
        // Makes the OTP readable in the UI, so the spec can complete a real
        // verification round-trip instead of stubbing the endpoint.
        OTP_DEV_ECHO: "true",
        SMS_PROVIDER: "console",
        BACKEND_CORS_ORIGINS: `${BASE_URL},http://localhost:${FRONTEND_PORT}`,
        // Deliberately unreachable: exercises the KV circuit breaker so the
        // suite never blocks on a Redis that is not there.
        REDIS_URL: "redis://127.0.0.1:6399/0",
      },
    },
    {
      // `-H 127.0.0.1` is load-bearing. With a bare `next dev -p <port>` the
      // server binds to `localhost`, which on a dual-stack machine resolves to
      // IPv6 `::1` — while Playwright probes the IPv4 literal below. The probe
      // then never connects and the run dies with a bare "Timed out waiting
      // 180000ms from config.webServer" that says nothing about the cause.
      command: `npm run dev -- -H 127.0.0.1 -p ${FRONTEND_PORT}`,
      cwd: ".",
      url: `${BASE_URL}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        NEXT_PUBLIC_API_URL: BACKEND_URL,
        NEXT_PUBLIC_WS_URL: BACKEND_URL.replace(/^http/, "ws"),
      },
    },
  ],
});
