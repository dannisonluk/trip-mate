import { expect, test } from "@playwright/test";

import { bellLabel, createTrip, newUserContext, registerUser } from "./helpers";

/**
 * The end-to-end matching journey, driven by two real browser sessions.
 *
 * Two separate contexts (not two tabs in one) is the whole point: each gets its
 * own cookie jar, so "Alice" and "Bob" are genuinely independent sessions and
 * the notification/authorisation logic is exercised for real.
 */
test.describe("行程配對流程", () => {
  test("發佈 → 申請 → 接受 → 通知 → 行程群組 → 聊天", async ({ browser }) => {
    const aliceCtx = await newUserContext(browser);
    const bobCtx = await newUserContext(browser);
    const alice = await aliceCtx.newPage();
    const bob = await bobCtx.newPage();

    try {
      // --- 1. two independent accounts -----------------------------------
      await registerUser(alice, "Alice");
      await registerUser(bob, "Bob");

      // --- 2. Alice publishes a trip -------------------------------------
      const tripTitle = `東京之旅 ${Date.now()}`;
      const tripId = await createTrip(alice, { title: tripTitle });

      // --- 3. Bob applies -------------------------------------------------
      await bob.goto(`/trips/${tripId}`);
      await expect(bob.getByRole("heading", { name: tripTitle })).toBeVisible();

      await bob.getByRole("button", { name: "申請成為旅伴" }).click();
      await bob.fill("#apply-message", "你好！我也計劃去東京，想一起拍照。");
      await bob.getByRole("button", { name: "送出申請" }).click();
      await expect(bob.getByText("已送出申請！")).toBeVisible();

      // --- 4. the application reaches Alice's bell -----------------------
      // Reloading re-mounts the bell, which polls immediately on mount;
      // without it this assertion would race the 30 s poll interval.
      await alice.reload();
      const aliceBell = alice.getByRole("button", { name: bellLabel(1) });
      await expect(aliceBell).toBeVisible();
      await aliceBell.click();
      await expect(alice.getByText(`Bob 申請加入「${tripTitle}」`)).toBeVisible();
      await alice.keyboard.press("Escape");

      // --- 5. Alice accepts ----------------------------------------------
      await expect(alice.getByText("收到的申請（1）")).toBeVisible();
      await alice.getByRole("button", { name: "接受", exact: true }).click();
      await expect(alice.getByText("已接受申請，並開啟對話。")).toBeVisible();

      // --- 6. the decision reaches Bob's bell ----------------------------
      await bob.reload();
      const bobBell = bob.getByRole("button", { name: bellLabel(1) });
      await expect(bobBell).toBeVisible();
      await bobBell.click();
      await expect(bob.getByText(`Alice 接受了你的申請：${tripTitle}`)).toBeVisible();
      await bob.keyboard.press("Escape");

      // --- 7. Alice opens the trip group and sends a message -------------
      const groupButton = alice.getByRole("button", { name: "行程群組" });
      await expect(groupButton).toBeEnabled();
      await groupButton.click();

      await alice.waitForURL(
        (url) => url.pathname === "/chat" && url.searchParams.has("room"),
      );

      const composer = alice.getByPlaceholder(/輸入訊息/);
      await expect(composer).toBeVisible();
      // The panel reports "已連線" only after the WebSocket handshake succeeded,
      // which is what proves the organiser was authorised into the group room.
      await expect(alice.getByText("已連線")).toBeVisible();

      const groupMessage = `行程群組測試訊息 ${Date.now()}`;
      await composer.fill(groupMessage);

      // Order matters: the send button is disabled while the draft is empty as
      // well as while the socket is down, so it only proves the connection is
      // live once there is something to send.
      const sendButton = alice.getByRole("button", { name: "傳送" });
      await expect(sendButton).toBeEnabled();
      await sendButton.click();
      await expect(alice.getByText(groupMessage)).toBeVisible();

      // --- 8. Bob was seeded into the group and can read it --------------
      await bob.goto("/chat");
      await bob.getByRole("button", { name: tripTitle }).click();
      await expect(bob.getByText(groupMessage)).toBeVisible();
    } finally {
      await aliceCtx.close();
      await bobCtx.close();
    }
  });

  test("沒有已接受的旅伴時，行程群組按鈕保持停用", async ({ browser }) => {
    const ownerCtx = await newUserContext(browser);
    const applicantCtx = await newUserContext(browser);
    const owner = await ownerCtx.newPage();
    const applicant = await applicantCtx.newPage();

    try {
      await registerUser(owner, "群組擁有者");
      await registerUser(applicant, "待審申請者");

      const tripTitle = `群組門檻測試 ${Date.now()}`;
      const tripId = await createTrip(owner, { title: tripTitle });

      // With no applicants a group room would contain only the organiser, which
      // is exactly the bug this control exists to prevent.
      await expect(owner.getByRole("button", { name: "行程群組" })).toBeDisabled();

      await applicant.goto(`/trips/${tripId}`);
      await applicant.getByRole("button", { name: "申請成為旅伴" }).click();
      await applicant.fill("#apply-message", "想一起同行！");
      await applicant.getByRole("button", { name: "送出申請" }).click();
      await expect(applicant.getByText("已送出申請！")).toBeVisible();

      // A PENDING applicant must not unlock the group — only ACCEPTED does.
      await owner.reload();
      await expect(owner.getByText("收到的申請（1）")).toBeVisible();
      await expect(owner.getByRole("button", { name: "行程群組" })).toBeDisabled();

      await owner.getByRole("button", { name: "接受", exact: true }).click();
      await expect(owner.getByRole("button", { name: "行程群組" })).toBeEnabled();
    } finally {
      await ownerCtx.close();
      await applicantCtx.close();
    }
  });
});
