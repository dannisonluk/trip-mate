import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { BASE_URL, E2E_DB_FILE } from "./constants";
import { BACKEND_DIR, resolvePython } from "./python";

/**
 * Shared flows for the E2E specs.
 *
 * Selectors are taken from the real components rather than guessed — the UI is
 * Chinese, so a typo in a label is the single most likely cause of a flaky
 * suite. Each helper documents the element it drives.
 */

/** Password for every throwaway account. Satisfies the server rule
 *  (>= 8 chars, with upper case, lower case and a digit). */
export const PASSWORD = "TripMate!2026";

export interface TestUser {
  phone: string;
  password: string;
  nickname: string;
}

/**
 * A fresh, valid HK mobile number per call.
 *
 * The E2E database file persists between local runs (so a failing run keeps its
 * data for inspection), which means a fixed number would collide on the second
 * run and registration would fail with "phone already registered".
 *
 * Format must satisfy the server's `^\+852[456789]\d{7}$`.
 */
export function uniquePhone(): string {
  const head = ["5", "6", "9"][Math.floor(Math.random() * 3)];
  const tail = String(Math.floor(Math.random() * 9_000_000) + 1_000_000);
  return `+852${head}${tail}`;
}

/** A fresh browsing context. Passed an explicit baseURL because
 *  `browser.newContext()` does not inherit the config's `use` options — without
 *  it, `page.goto("/trips")` fails with "Cannot navigate to invalid URL".
 *
 *  A separate context per user is what makes the two-party flows real: each has
 *  its own cookie jar, so the two sessions cannot leak into each other. */
export async function newUserContext(browser: Browser): Promise<BrowserContext> {
  return browser.newContext({ baseURL: BASE_URL });
}

/**
 * Register a brand-new account, verify the phone via OTP, and land on /trips.
 *
 * The OTP is read from the on-screen dev notice (`OTP_DEV_ECHO=true`), so this
 * exercises the genuine send → verify round-trip rather than stubbing it.
 */
export async function registerUser(
  page: Page,
  nickname: string,
  { verify = true }: { verify?: boolean } = {},
): Promise<TestUser> {
  const phone = uniquePhone();

  await page.goto("/register");
  await page.fill("#nickname", nickname);
  await page.fill("#phone", phone);
  await page.fill("#password", PASSWORD);

  // Two mandatory consents; the form refuses to submit without both.
  const consents = page.getByRole("checkbox");
  await consents.nth(0).check();
  await consents.nth(1).check();

  await page.getByRole("button", { name: "建立帳號" }).click();
  await expect(page.getByText("帳號已建立")).toBeVisible();

  if (verify) {
    await page.getByRole("button", { name: "發送驗證碼" }).click();

    const notice = page.getByText(/開發模式：驗證碼為/);
    await expect(notice).toBeVisible();
    const match = (await notice.innerText()).match(/驗證碼為\s*(\d{4,8})/);
    if (!match) throw new Error("dev OTP code not found in the notice text");
    await page.fill("#otp", match[1]);

    // `exact` matters: without it "驗證" would also match "發送驗證碼".
    await page.getByRole("button", { name: "驗證", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "稍後再說" }).click();
  }

  await page.waitForURL("**/trips");
  return { phone, password: PASSWORD, nickname };
}

/** Sign in with an existing account and wait for the post-login redirect. */
export async function loginUser(
  page: Page,
  user: Pick<TestUser, "phone" | "password">,
): Promise<void> {
  await page.goto("/login");
  await page.fill("#phone", user.phone);
  await page.fill("#password", user.password);
  await page.getByRole("button", { name: "登入", exact: true }).click();
  await page.waitForURL("**/trips");
}

/** Sign out via the nav bar and wait for the landing page. */
export async function logoutUser(page: Page): Promise<void> {
  await page.getByRole("button", { name: "登出" }).click();
  await page.waitForURL((url) => url.pathname === "/");
}

/** Publish a trip and return its id, taken from the redirect target. */
export async function createTrip(
  page: Page,
  {
    title,
    description = "這是一趟由端到端測試建立的行程，說明文字需要至少十個字元才符合驗證規則。",
    country = "日本",
    city = "東京",
  }: { title: string; description?: string; country?: string; city?: string },
): Promise<string> {
  await page.goto("/trips/new");
  await page.fill("#title", title);
  await page.fill("#description", description);
  await page.fill("#country", country);
  await page.fill("#city", city);

  // The nav bar has a "發佈行程" *link*; this role-based lookup targets the
  // submit *button* and so cannot collide with it.
  await page.getByRole("button", { name: "發佈行程", exact: true }).click();

  await page.waitForURL((url) => /^\/trips\/[0-9a-f-]{36}$/i.test(url.pathname));
  return page.url().split("/").pop() as string;
}

/**
 * The notification bell's accessible name encodes the unread count
 * ("通知，3 則未讀"), which makes it a far more precise assertion target than
 * scraping the badge span.
 */
export function bellLabel(unread: number): string {
  return unread > 0 ? `通知，${unread} 則未讀` : "通知";
}

export function promoteToAdmin(phone: string): void {
  const databaseUrl = `sqlite+aiosqlite:///${path
    .join(BACKEND_DIR, E2E_DB_FILE)
    .split(path.sep)
    .join("/")}`;

  execFileSync(resolvePython(), ["scripts/manage_roles.py", "promote", phone], {
    cwd: BACKEND_DIR,
    env: {
      ...process.env,
      ENV: "development",
      SECRET_KEY: process.env.SECRET_KEY ?? "e2e-secret-key-not-for-production",
      DATABASE_URL: databaseUrl,
    },
    stdio: "pipe",
  });
}
