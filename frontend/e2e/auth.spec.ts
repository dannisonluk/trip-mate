import { expect, test } from "@playwright/test";

import { PASSWORD, loginUser, logoutUser, registerUser, uniquePhone } from "./helpers";

/**
 * Account lifecycle: registration, phone verification, login, and the
 * anti-enumeration behaviour of the login error.
 */

test("註冊 → OTP 驗證 → 進入探索行程", async ({ page }) => {
  await registerUser(page, "新註冊用戶");

  await expect(page).toHaveURL(/\/trips$/);
  // The nav bar only renders the session controls once a session exists.
  await expect(page.getByRole("button", { name: "登出" })).toBeVisible();
});

test("未驗證手機仍可進入平台（稍後再說）", async ({ page }) => {
  await registerUser(page, "未驗證用戶", { verify: false });

  await expect(page).toHaveURL(/\/trips$/);
  await expect(page.getByRole("button", { name: "登出" })).toBeVisible();
});

test("登出後可以再次以密碼登入", async ({ page }) => {
  const user = await registerUser(page, "回訪用戶");

  await logoutUser(page);
  await loginUser(page, user);

  await expect(page).toHaveURL(/\/trips$/);
});

test("密碼錯誤時顯示通用錯誤，不洩露帳號是否存在", async ({ page }) => {
  const user = await registerUser(page, "錯誤密碼用戶");
  await logoutUser(page);

  await page.goto("/login");
  await page.fill("#phone", user.phone);
  await page.fill("#password", "DefinitelyWrong!999");
  await page.getByRole("button", { name: "登入", exact: true }).click();

  // The API answers with a single generic string for both "no such account"
  // and "wrong password" (§1.2 anti-enumeration). Asserting the exact text
  // keeps that property from regressing into two distinguishable messages.
  await expect(page.getByText("Invalid credentials")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("未登入時受保護頁面顯示登入提示", async ({ page }) => {
  await page.goto("/trips/new");

  await expect(page.getByRole("heading", { name: "請先登入" })).toBeVisible();
  // Scoped to <main>: the nav bar also has a "登入" link, so an unscoped
  // lookup is a strict-mode violation rather than a flaky assertion.
  await expect(page.getByRole("main").getByRole("link", { name: "登入" })).toBeVisible();
});

test("重複註冊同一手機號碼會被拒絕", async ({ page }) => {
  const user = await registerUser(page, "重複註冊用戶");
  await logoutUser(page);

  await page.goto("/register");
  await page.fill("#nickname", "另一個名字");
  await page.fill("#phone", user.phone);
  await page.fill("#password", PASSWORD);
  const consents = page.getByRole("checkbox");
  await consents.nth(0).check();
  await consents.nth(1).check();
  await page.getByRole("button", { name: "建立帳號" }).click();

  // Stays on the form and surfaces an error instead of creating a second account.
  await expect(page.getByText("帳號已建立")).toBeHidden();
  await expect(page.locator(".text-destructive").first()).toBeVisible();
});

test("未註冊的手機號碼無法登入", async ({ page }) => {
  await page.goto("/login");
  await page.fill("#phone", uniquePhone());
  await page.fill("#password", PASSWORD);
  await page.getByRole("button", { name: "登入", exact: true }).click();

  await expect(page.getByText("Invalid credentials")).toBeVisible();
});
