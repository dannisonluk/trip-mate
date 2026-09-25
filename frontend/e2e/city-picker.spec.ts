import { expect, test } from "@playwright/test";

import { createTrip, newUserContext, pickCity, registerUser } from "./helpers";

/**
 * The select-only city picker.
 *
 * The product rule is that a destination city must be one of a known set, not
 * free text: the matching engine compares strings literally, so a user who types
 * "Osaka" would silently never match a trip that stored "Ōsaka". These specs
 * assert the consequences of that rule — that typing does not set a city, and
 * that only the reference table can.
 *
 * The control is located by `#city`. Its accessible name is the surrounding
 * `<Label>` concatenated with the button text, which differs between the two
 * forms, so a `getByRole(..., { name })` lookup is not portable across them.
 */
test.describe("城市選擇器", () => {
  test("發佈行程時可從建議清單選城市，並帶出國家", async ({ browser }) => {
    const ctx = await newUserContext(browser);
    const page = await ctx.newPage();

    try {
      await registerUser(page, "城市測試");
      await page.goto("/trips/new");
      await page.fill("#title", `城市選擇測試 ${Date.now()}`);
      await page.fill(
        "#description",
        "這是一趟由端到端測試建立的行程，說明文字需要至少十個字元才符合驗證規則。",
      );

      // Nothing chosen yet: the trigger shows the placeholder.
      await expect(page.locator("#city")).toContainText("選擇城市（選填）");

      await pickCity(page, "Tokyo");

      // Picking a city resolves the country, which removes the chance of a
      // hand-typed country disagreeing with the city's actual country.
      await expect(page.locator("#city")).toContainText("Tokyo");
      await expect(page.locator("#country")).toHaveValue("Japan");
    } finally {
      await ctx.close();
    }
  });

  test("輸入自由文字不會設定城市，只有點選建議才會", async ({ browser }) => {
    const ctx = await newUserContext(browser);
    const page = await ctx.newPage();

    try {
      await registerUser(page, "自由文字測試");
      await page.goto("/trips/new");

      await page.locator("#city").click();
      const search = page.getByPlaceholder("輸入城市名稱…");
      await search.fill("Atlantis");

      // No suggestion matches, so the list reports it rather than accepting the
      // text. This is the assertion that the control cannot be used as free text.
      await expect(
        page.getByText("找不到相符的城市。你可以留空，或改用更完整的拼寫。"),
      ).toBeVisible();
      await expect(page.getByRole("option")).toHaveCount(0);

      // Dismissing without choosing leaves the field unset.
      await page.keyboard.press("Escape");
      await expect(page.locator("#city")).toContainText("選擇城市（選填）");
    } finally {
      await ctx.close();
    }
  });

  test("足跡城市留空時顯示提示，說明留空會影響配對分數", async ({ browser }) => {
    const ctx = await newUserContext(browser);
    const page = await ctx.newPage();

    try {
      await registerUser(page, "足跡提示測試");
      await page.goto("/profile");

      // The form lives on the "旅遊足跡" tab, so the hint is not on screen until
      // that tab is selected.
      await page.getByRole("tab", { name: "旅遊足跡" }).click();

      // The hint is the user-facing mitigation for a blank city silently costing
      // match weight; without it the omission is invisible.
      await expect(
        page.getByText("選填。填寫去過的城市可提高配對分數；留空不會影響發佈。"),
      ).toBeVisible();
    } finally {
      await ctx.close();
    }
  });

  test("已選城市可被清除，清除後回到未選狀態", async ({ browser }) => {
    const ctx = await newUserContext(browser);
    const page = await ctx.newPage();

    try {
      await registerUser(page, "清除城市測試");
      await page.goto("/trips/new");

      await pickCity(page, "Bangkok");
      await expect(page.locator("#city")).toContainText("Bangkok");

      await page.getByRole("button", { name: "清除已選城市" }).click();
      await expect(page.locator("#city")).toContainText("選擇城市（選填）");
    } finally {
      await ctx.close();
    }
  });

  test("發佈的行程保留了所選城市", async ({ browser }) => {
    const ctx = await newUserContext(browser);
    const page = await ctx.newPage();

    try {
      await registerUser(page, "城市持久化測試");
      const tripId = await createTrip(page, {
        title: `城市持久化 ${Date.now()}`,
        country: "Japan",
        city: "Tokyo",
      });

      // A city that does not survive publication would look chosen on the form
      // and never match afterwards — the exact failure the FK exists to prevent.
      //
      // Asserted on the map's accessible name (`role="img"`, `"Tokyo, Japan"`),
      // which is built from the row the id resolves to. A bare
      // `getByText("Tokyo")` also matches that name by substring and trips
      // strict mode.
      await page.goto(`/trips/${tripId}`);
      await expect(page.getByRole("img", { name: "Tokyo, Japan" })).toBeVisible();
    } finally {
      await ctx.close();
    }
  });
});


test.describe("行程詳情的地圖", () => {
  test("有城市時顯示座標；沒有城市時不顯示地圖區塊", async ({ browser }) => {
    const ctx = await newUserContext(browser);
    const page = await ctx.newPage();

    try {
      await registerUser(page, "地圖測試");

      // --- with a city: the stored coordinate is shown ---
      const tripId = await createTrip(page, {
        title: `地圖顯示 ${Date.now()}`,
        country: "Japan",
        city: "Tokyo",
      });
      await page.goto(`/trips/${tripId}`);

      // Asserted on the coordinate text, not on a rendered tile. Tiles come from
      // openstreetmap.org, which the test environment may not reach — making the
      // tile the assertion target would produce a test that fails for a network
      // reason and looks like a product bug. The coordinate is what the trip
      // actually stores, and it is what the fallback shows too.
      await expect(page.getByText("35.6895, 139.69171")).toBeVisible();

      // --- without a city: no map section at all ---
      const noCityId = await createTrip(page, {
        title: `無城市 ${Date.now()}`,
        country: "Japan",
        // no `city` — the field is optional and this is the supported blank case
      });
      await page.goto(`/trips/${noCityId}`);
      await expect(page.getByText("35.6895, 139.69171")).toHaveCount(0);
    } finally {
      await ctx.close();
    }
  });
});
