#!/usr/bin/env node
/**
 * Production-build smoke check.
 *
 * Verifies the headers and hydration of a **production** build (`next build` +
 * `next start`) in a real browser. Run with `npm run check:prod`.
 *
 * ## Why this exists alongside the Playwright suite
 *
 * The E2E suite runs against `next dev`, which renders every route per request.
 * That difference hides a whole class of defect:
 *
 *   * **Prerendered HTML is generated at build time**, so anything that depends
 *     on the request — a CSP nonce, a per-request header — cannot appear in it.
 *   * Development also skips the production minifier, the CSS extraction step,
 *     and `'unsafe-eval'`-free script execution.
 *
 * This was not hypothetical. A nonce-based `script-src` was implemented, the
 * E2E suite stayed green, and the production build produced **18 CSP violations
 * per static page** — blocking even the external chunk files, because
 * `'strict-dynamic'` makes `'self'` apply to nothing once a nonce is present.
 * Ten of the twelve routes were unusable. Only a check like this one catches it.
 *
 * Requires `npm run build` to have been run first; this script starts and stops
 * `next start` itself.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import process from "node:process";

import { chromium } from "@playwright/test";

const PORT = Number(process.env.PROD_CHECK_PORT ?? 3099);
const BASE = `http://127.0.0.1:${PORT}`;

/** Routes spanning both rendering modes: prerendered, dynamic, and unmatched. */
const ROUTES = [
  "/",
  "/login",
  "/register",
  "/trips",
  "/notifications",
  "/trips/00000000-0000-0000-0000-000000000000",
  "/profile/00000000-0000-0000-0000-000000000000",
];

const REQUIRED_HEADERS = [
  "content-security-policy",
  "x-frame-options",
  "x-content-type-options",
  "referrer-policy",
  "permissions-policy",
  "cross-origin-opener-policy",
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The build can be missing, or destroyed by a `next dev` run in between
 * (`next dev` writes its own `.next`, wiping the production output). Check the
 * build id before blaming the server, and say which of the two it is —
 * "could not reach the server" sends people looking at ports and proxies for a
 * problem that is actually a missing artefact.
 */
function hasProductionBuild() {
  return existsSync(new URL("../.next/BUILD_ID", import.meta.url));
}

async function waitForServer(timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/login`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await sleep(500);
  }
  return false;
}

// Fail before spawning anything: starting a server that cannot work, then
// waiting a minute for it, buries the real message in a timeout.
if (!hasProductionBuild()) {
  console.error(
    "No production build found (.next/BUILD_ID is missing).\n" +
      "Run `npm run build` first, then re-run this check.\n\n" +
      "Note: running `next dev` since the build also removes it — `next dev`\n" +
      "writes its own .next and does not leave the production output behind.",
  );
  process.exit(1);
}

const server = spawn(
  process.platform === "win32" ? "npx.cmd" : "npx",
  ["next", "start", "-H", "127.0.0.1", "-p", String(PORT)],
  { stdio: "ignore", env: process.env, shell: process.platform === "win32" },
);

const problems = [];
let browser;

try {
  if (!(await waitForServer())) {
    console.error(
      `Started \`next start\` on ${BASE} but it never became ready.\n` +
        "A production build IS present, so this is not a missing-build problem.\n" +
        "Check whether the port is already in use, or whether the server crashed " +
        "(run `npx next start -H 127.0.0.1 -p " +
        `${PORT}\` directly to see its output).`,
    );
    process.exit(1);
  }

  browser = await chromium.launch();
  const page = await browser.newPage();

  // Console errors that indicate a blocked resource rather than app noise.
  let violations = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to execute|Refused to load|violates/i.test(message.text())) {
      violations.push(message.text());
    }
  });

  console.log(`Checking the production build at ${BASE}\n`);

  for (const route of ROUTES) {
    violations = [];
    const response = await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    const status = response?.status() ?? 0;
    const headers = response?.headers() ?? {};

    const missing = REQUIRED_HEADERS.filter((h) => !headers[h]);
    const ok = status < 400 && violations.length === 0 && missing.length === 0;

    console.log(
      `  ${ok ? "ok  " : "FAIL"} ${route.padEnd(44)} ${String(status).padEnd(4)}` +
        ` violations: ${violations.length}` +
        (missing.length ? `  missing headers: ${missing.join(", ")}` : ""),
    );

    if (status >= 400) problems.push(`${route}: HTTP ${status}`);
    if (missing.length) problems.push(`${route}: missing header(s) ${missing.join(", ")}`);
    violations.slice(0, 2).forEach((v) => {
      console.log(`         ${v.slice(0, 150)}`);
      problems.push(`${route}: ${v.slice(0, 100)}`);
    });
  }

  // The CSP must not have silently regressed to something weaker than intended.
  const csp = (await page.goto(`${BASE}/login`))?.headers()["content-security-policy"] ?? "";
  for (const directive of ["default-src 'self'", "frame-ancestors 'none'", "object-src 'none'"]) {
    if (!csp.includes(directive)) problems.push(`CSP is missing ${directive}`);
  }
} finally {
  await browser?.close();
  server.kill();
}

if (problems.length) {
  console.error(`\nProduction check FAILED (${problems.length} problem(s)):\n`);
  problems.forEach((p) => console.error(`  ${p}`));
  process.exit(1);
}

console.log("\nProduction check passed — headers present, no CSP violations, all routes served.");
