import { expect, test } from "@playwright/test";

/**
 * Language switcher.
 *
 * These assertions are deliberately about *both* locales. Checking only that
 * English appears would pass even if switching destroyed the page, so each test
 * also checks that switching back restores the default-locale text.
 *
 * The default locale's strings are asserted by the other specs (they match on
 * 中文 labels), which is what makes this suite double as a regression check that
 * the default locale was not changed when the strings moved into the dictionary.
 */

/** The switcher's accessible name, which is itself translated. */
const SWITCHER = { "zh-HK": "語言", en: "Language" } as const;

test("語言切換：中文 → English → 中文", async ({ page }) => {
  await page.goto("/login");

  // Default locale: the page is Chinese and the switcher announces itself in Chinese.
  await expect(page.getByRole("heading", { name: "登入" })).toBeVisible();
  await expect(page.getByRole("button", { name: "登入", exact: true })).toBeVisible();

  const switcher = page.getByRole("button", { name: SWITCHER["zh-HK"] });
  await expect(switcher).toBeVisible();
  await switcher.click();
  await page.getByRole("button", { name: "English" }).click();

  // Switched: the heading, the submit button and the switcher all changed language.
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Log in", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: SWITCHER.en })).toBeVisible();

  // `lang` follows the choice, so screen readers and font fallback do too.
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  // Switching back restores the default locale.
  await page.getByRole("button", { name: SWITCHER.en }).click();
  await page.getByRole("button", { name: "繁體中文" }).click();
  await expect(page.getByRole("heading", { name: "登入" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-Hant-HK");
});

test("語言選擇會在重新載入後保留", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: SWITCHER["zh-HK"] }).click();
  await page.getByRole("button", { name: "English" }).click();
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();

  // A fresh navigation — the choice lives in a cookie, not in component state.
  await page.goto("/register");
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  // ...and it survives a full reload, not just client-side routing.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
});

test("未登入時也可以切換語言", async ({ page }) => {
  // Someone who cannot read the default language needs this before registering.
  await page.goto("/");
  await expect(page.getByRole("button", { name: SWITCHER["zh-HK"] })).toBeVisible();
  await page.getByRole("button", { name: SWITCHER["zh-HK"] }).click();
  await page.getByRole("button", { name: "English" }).click();

  await expect(page.getByRole("link", { name: "Log in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign up free" })).toBeVisible();
});
