import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { BASE_URL, E2E_ADMIN_PHONE, E2E_DB_FILE } from "./constants";
import { requestedAdminPhones } from "./out-of-band-roles";
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
  { verify = true, phone: fixedPhone }: { verify?: boolean; phone?: string } = {},
): Promise<TestUser> {
  // A caller may pin the number (see `E2E_ADMIN_PHONE`): the admin specs need a
  // phone an out-of-band grant could already have promoted, because the operator
  // CLI is unreachable when the environment blocks child processes.
  const phone = fixedPhone ?? uniquePhone();

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
    city,
  }: { title: string; description?: string; country?: string; city?: string },
): Promise<string> {
  await page.goto("/trips/new");
  await page.fill("#title", title);
  await page.fill("#description", description);
  await page.fill("#country", country);
  // `#city` is a button-triggered combobox, not a text field, so `fill` would
  // fail. The city is optional, so it is left unset unless asked for; the
  // reference rows a lookup needs come from the global setup (`app.seed`).
  if (city) await pickCity(page, city);

  // The nav bar has a "發佈行程" *link*; this role-based lookup targets the
  // submit *button* and so cannot collide with it.
  await page.getByRole("button", { name: "發佈行程", exact: true }).click();

  await page.waitForURL((url) => /^\/trips\/[0-9a-f-]{36}$/i.test(url.pathname));
  return page.url().split("/").pop() as string;
}

/**
 * Drive the select-only city picker on whichever form is open.
 *
 * The control is a button that opens a popover containing a search box and a
 * listbox, so it cannot be driven with `fill`. Selecting a city must be done by
 * *clicking a suggestion*, which is the product rule this asserts: there is no
 * way to set an arbitrary city by typing one.
 *
 * Located by `#city` rather than by accessible name. The accessible name is the
 * surrounding `<Label>` concatenated with the button's own text, and the two
 * forms label this field differently — 「城市 / 地區（選填）」 on the trip form and
 * 「城市 / 地區」 on the travel-history form — so an `{ name }` lookup that works
 * on one silently times out on the other. The id is stable and unique per page.
 */
export async function pickCity(page: Page, query: string): Promise<void> {
  await page.locator("#city").click();
  const search = page.getByPlaceholder("輸入城市名稱…");
  await expect(search).toBeVisible();
  await search.fill(query);

  // Wait for a suggestion rather than assuming the debounce has elapsed.
  const options = page.getByRole("option");
  await expect(options.first()).toBeVisible({ timeout: 15_000 });
  await options.first().click();
}


/**
 * The notification bell's accessible name encodes the unread count
 * ("通知，3 則未讀"), which makes it a far more precise assertion target than
 * scraping the badge span.
 */
export function bellLabel(unread: number): string {
  return unread > 0 ? `通知，${unread} 則未讀` : "通知";
}

/**
 * Promote a phone number to ADMIN, driving the real operator CLI.
 *
 * There is deliberately no HTTP route that grants a role, so this is the only
 * honest way for a test to become an administrator — it exercises the same path
 * a real deployment uses.
 *
 * Fallback: some environments forbid child processes entirely. Inside the
 * WorkBuddy sandbox **every** `spawnSync` fails with `EBUSY` (measured, with
 * `node --version` as a control), so the CLI cannot be invoked. In that case the
 * grant is left to `e2e/out-of-band-roles.ts`, driven by `E2E_ADMIN_PHONES`, and
 * this function only warns — the *tests* then either pass against an
 * already-promoted account or fail loudly, which is the correct outcome.
 *
 * The warning is deliberate: a green admin run under this path proves the admin
 * UI and the audit trail work, and proves **nothing** about `manage_roles.py`.
 * `backend/tests/test_manage_roles.py` is where that guarantee lives.
 */
export function promoteToAdmin(phone: string): void {
  const databaseUrl = `sqlite+aiosqlite:///${path
    .join(BACKEND_DIR, E2E_DB_FILE)
    .split(path.sep)
    .join("/")}`;

  try {
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
    return;
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code !== "EBUSY" && code !== "EPERM") throw err;

    const already = requestedAdminPhones();
    if (already.includes(phone)) {
      console.warn(
        `[helpers] spawnSync blocked (${code}); ${phone} was granted ADMIN ` +
          "out-of-band. The operator CLI was NOT exercised by this run.",
      );
      return;
    }
    throw new Error(
      `[helpers] promoteToAdmin could not run the operator CLI: spawnSync ` +
        `${resolvePython()} failed with ${code}, and this environment blocks ` +
        "child processes.\n" +
        "Grant the role out-of-band, then re-run with E2E_ADMIN_PHONES set:\n" +
        `  cd backend && ENV=development DATABASE_URL=${databaseUrl} \\\n` +
        `    E2E_ADMIN_PHONES="${phone}" ${resolvePython()} -m e2e_out_of_band_promote`,
    );
  }
}
