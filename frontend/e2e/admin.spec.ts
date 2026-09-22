import { expect, test } from "@playwright/test";

import { createTrip, newUserContext, promoteToAdmin, registerUser } from "./helpers";

/**
 * The admin surface, end to end.
 *
 * This is the one area that had no browser coverage at all, and the reason was
 * structural: there is deliberately no HTTP route that grants a role, so a test
 * had no way to *become* an administrator. `promoteToAdmin` drives the operator
 * CLI instead — which is also the path a real deployment uses.
 *
 * The flow is the genuine one: a user reports another user, an administrator
 * resolves the report, and the audit trail is asserted to contain the events
 * that just happened. Asserting on the audit entries is the point — it is the
 * only place where "the audit trail actually records what it claims to" can be
 * checked from outside the database.
 */

test("一般使用者看不到管理後台", async ({ browser }) => {
  const context = await newUserContext(browser);
  const page = await context.newPage();
  try {
    await registerUser(page, "Ordinary User");

    // The nav link is only a convenience — the API is the real gate — so it must
    // be absent for a non-admin.
    await expect(page.getByRole("link", { name: "管理" })).toHaveCount(0);

    // ...and typing the URL must be refused by the API, not merely hidden.
    await page.goto("/admin");
    await expect(page.getByRole("heading", { name: "沒有權限" })).toBeVisible();
  } finally {
    await context.close();
  }
});

test("管理員可以處理檢舉，稽核紀錄會記下整個過程", async ({ browser }) => {
  const aliceContext = await newUserContext(browser);
  const bobContext = await newUserContext(browser);
  const alice = await aliceContext.newPage();
  const bob = await bobContext.newPage();

  try {
    // --- Alice publishes a trip ------------------------------------------
    const aliceUser = await registerUser(alice, "Alice Admin");
    const tripTitle = `管理後台測試行程 ${Date.now()}`;
    // Navigate by id rather than hunting the listing: the list is paginated and
    // the database persists between runs, so "find my new trip in the list" is a
    // test that breaks for reasons unrelated to the admin surface.
    const tripId = await createTrip(alice, { title: tripTitle });

    // --- Bob registers and reports Alice ---------------------------------
    await registerUser(bob, "Bob Reporter");
    await bob.goto(`/trips/${tripId}`);
    await expect(bob.getByRole("heading", { name: tripTitle })).toBeVisible();

    // The report reason is collected with `window.prompt`, so the dialog has to
    // be answered before the click resolves.
    bob.once("dialog", (dialog) => dialog.accept("harassment"));
    await bob.getByRole("button", { name: "檢舉" }).click();
    await expect(bob.getByText("已收到你的檢舉")).toBeVisible();

    // --- Promote Alice, then reload so the client picks up the new role ---
    promoteToAdmin(aliceUser.phone);
    await alice.goto("/admin");

    // --- The report queue is reachable and shows the report ---------------
    await expect(alice.getByRole("heading", { name: "管理後台" })).toBeVisible();
    await expect(alice.getByText("騷擾").first()).toBeVisible();

    // --- Resolve it --------------------------------------------------------
    // The click must be scoped to the report's own card. The filter row above the
    // list uses the *same* labels as the per-report actions ("已處理" is both a
    // filter and an action), so an unscoped `.first()` silently applies a filter
    // and changes nothing — which is exactly what happened the first time, and
    // the audit trail is what made it visible (`status_filter=actioned`).
    const reportCard = alice.locator("li").filter({ hasText: "騷擾" }).first();
    await reportCard.getByRole("button", { name: "已處理" }).click();
    await expect(reportCard.getByText("已處理")).toBeVisible();

    // --- The audit tab contains the events that just happened -------------
    await alice.getByRole("tab", { name: "稽核紀錄" }).click();
    await expect(alice.getByText(/共 \d+ 筆紀錄/)).toBeVisible();

    // The transition itself is recorded, not just the destination. This chip is
    // rendered from the entry's `detail` JSON, so it also proves the API returns
    // the structured context rather than a bare action name.
    await expect(alice.getByText("to=actioned").first()).toBeVisible();
    await expect(alice.getByText("from=open").first()).toBeVisible();

    // Reading the queue and changing a status are both audited actions.
    await expect(alice.getByText("檢視檢舉佇列").first()).toBeVisible();
    await expect(alice.getByText("檢舉狀態變更").first()).toBeVisible();
  } finally {
    await aliceContext.close();
    await bobContext.close();
  }
});

test("稽核紀錄可以依動作篩選", async ({ browser }) => {
  const context = await newUserContext(browser);
  const page = await context.newPage();
  try {
    const admin = await registerUser(page, "Filter Admin");
    promoteToAdmin(admin.phone);
    await page.goto("/admin");
    await page.getByRole("tab", { name: "稽核紀錄" }).click();
    await expect(page.getByText(/共 \d+ 筆紀錄/)).toBeVisible();

    // Filtering to an action nobody has performed must produce the empty state
    // rather than the unfiltered list — that is the difference between a working
    // filter and a button that only looks like one.
    await page.getByRole("button", { name: "帳號匿名化" }).click();
    await expect(page.getByText("這個篩選條件下沒有稽核紀錄。")).toBeVisible();

    // And back to the unfiltered list.
    await page.getByRole("button", { name: "全部" }).first().click();
    await expect(page.getByText("這個篩選條件下沒有稽核紀錄。")).toHaveCount(0);
  } finally {
    await context.close();
  }
});
