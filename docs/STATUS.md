# Trip Mate — 專案狀態報告

> 本文是**現況快照**：完成了什麼、為什麼這樣設計、還有什麼問題。
> 所有數字都是**實際跑指令取得**的，不是從其他文件抄來的。
>
> 最後更新：2026-09-23（Deep Check + UI 逐頁檢視之後）

---

## 0. 一句話總結

規格要求的 P0／P1／P2 **全部完成並通過驗證**；後端 295 個測試、前端 15 個瀏覽器測試全綠。
**UI 已逐頁檢視確認完工**（11 條路由，無佔位頁，見 §3.8）。
剩下的是**多副本部署前置清單**（6 項同源問題，目前單副本皆不觸發，見 §4.1）、
3 項其餘取捨，以及 P3 行程地圖（**選型已定為 Leaflet**，但其阻塞點是隱私規則而非圖層，見 §4.4）。

---

## 1. 進度

### 1.1 功能完成度

| 優先級 | 模組 | 狀態 |
|--------|------|------|
| **P0** | 認證（手機 + OTP + Argon2id + Dual-Token JWT）、Refresh 輪替與洩漏重用偵測、真實 SMS 抽象層、首個 Alembic 遷移、前端 E2E | ✅ |
| **P1** | 個人檔案／旅遊足跡、行程發佈與篩選、智能配對、申請與審批、WebSocket 聊天、評價制度、防騷擾（封鎖／檢舉／限流）、PDPO 合規（同意軌跡／註銷／EXIF 抹除）、平台免責、站內通知、管理後台 | ✅ |
| **P2** | 標籤 join table、可觀測性與稽核軌跡、內容審核、多語系 i18n | ✅ |
| **P3** | 行程地圖 | ⬜ 未動工（見 §4） |

### 1.2 驗證數字（全部實跑）

| 檢查 | 指令 | 結果 |
|------|------|------|
| 後端測試 | `pytest -q` | **295 passed**（28 秒） |
| 前端型別 | `npm run typecheck` | **0 errors** |
| 前端靜態分析 | `npm run lint` | **0 warnings** |
| i18n 完整性 | `npm run check:i18n` | **332 鍵**、zh-HK/en 同步、零硬編碼、零未使用鍵 |
| 前端建置 | `npm run build` | **12 routes**（10 靜態預渲染 + 2 動態） |
| production 標頭／CSP | `npm run check:prod` | **7 條路由全 200、6 標頭齊備、CSP violation 0**（真實 production build + Chromium） |
| 瀏覽器 E2E | `npx playwright test` | **15 passed**（4 個 spec，真實 Chromium × 真實 uvicorn） |
| 遷移一致性 | `alembic check` | **無漂移** |
| 資料庫結構 | `Base.metadata` | **14 表 / 28 索引** |
| API 端點 | `app.routes` | **50 HTTP + 1 WebSocket**（另有 `/health`，不在 `/api/v1` 下） |

### 1.3 程式碼規模

```
backend/app/      62 個 Python 模組
backend/tests/    16 個測試檔（295 個測試）
backend/alembic/  4 支遷移
frontend/src/     38 個 TS/TSX（11 頁面 + 8 元件 + 12 UI + 6 lib）
frontend/e2e/     4 個 spec（15 個測試）
docs/             3 份文件（API / ARCHITECTURE / SECURITY）
```

---

## 2. 設計

### 2.1 分層

```
前端 Next.js 14 (App Router)
  └ lib/api.ts（記憶體 access token + HttpOnly refresh cookie，401 自動刷新）
  └ lib/auth.tsx / lib/i18n/（Context + Provider）
  └ components/ + components/ui/（Tailwind + Shadcn 風格，自有原始碼）
        │  REST /api/v1/*  +  WSS /api/v1/ws/chat/*
        ▼
後端 FastAPI（全鏈路 async）
  ├ api/v1/*     只負責 HTTP 語義（狀態碼、授權、schema 轉換）
  ├ services/*   商業規則（matching / kv / otp / sms / token_store / pii
  │              / storage / moderation / notifications / audit / content_filter）
  ├ models/*     14 張表（SQLAlchemy 2.0 async，`lazy=` 一律明確宣告）
  └ core/*       config / security / deps / rate_limit / logging / metrics
                 / middleware / request_meta
        │
        ▼
PostgreSQL 15（生產）/ SQLite（開發測試）· Redis · S3/R2
```

**分層原則**：router 不做商業判斷，PII 輸出必經 `services/pii.py`。

### 2.2 十個關鍵設計決策

每一條都是**有意識的取捨**，不是預設值：

| # | 決策 | 理由 | 代價 |
|---|------|------|------|
| 1 | **全鏈路 async**（router 直通 service 與 WebSocket） | 事件迴圈零阻塞 | 不能在 async 路徑上做惰性載入 → `lazy=` 必須明確宣告 |
| 2 | **Enum 存 `VARCHAR + CHECK`**（非 PG native enum） | 同一份模型跨 SQLite/PG | 長度由最長成員推導，新增成員時要跑 `alembic check` |
| 3 | **SQLite 連線強制開 `PRAGMA foreign_keys=ON`** | 否則所有 `ondelete=` 在 dev 是裝飾品、在生產才生效 | Alembic 走自己的連線，刻意保持關閉（batch 重建需要） |
| 4 | **標籤用 join table + 半連接 `IN`** | 舊的 JSON 欄位有 1000 筆隱形上限；`EXISTS` 在 SQLite 對非選擇性條件是全表掃描（實測 57.9ms vs 0.1ms） | 寫入時要多維護一張表 |
| 5 | **可解釋的規則式配對**（非 ML） | 旅伴配對是高信任場景，使用者要懂「為什麼推薦這個人」 | 權重是人工調的（見 §3 的教訓：文件與程式碼曾不一致） |
| 6 | **稽核軌跡結構性唯讀**（無 `updated_at`、無 UPDATE/DELETE 路徑） | 可以被編輯的稽核列不是證據 | 無法修正誤寫的紀錄，只能新增 |
| 7 | **稽核列與狀態變更共用交易**（`record()` 不 commit） | 為 rollback 的動作留紀錄，比沒有更糟 | 呼叫端必須記得 commit |
| 8 | **內容審核以「誤擋比漏放更貴」為原則** | 旅遊 App 的正常文字滿是陷阱（hotel **deposit**、**killer** view、**Gunsan**） | 可疑但可辯解的只 `FLAG`，需要人工看 |
| 9 | **i18n 用 cookie locale，不動路由** | 12 個路由、所有連結、所有 E2E 斷言都不必改 | 伺服器端 `metadata` 只能是預設語言；首次載入非預設語言會閃現 |
| 10 | **角色變更只走 CLI，不做 HTTP 端點** | 能改角色的端點就是等著被設錯的提權路徑 | 需要資料庫存取權才能建立第一個管理員 |

### 2.3 安全機制（規格條文對照）

| 機制 | 實作要點 |
|------|----------|
| 登入反枚舉 | 帳號不存在時仍燒一次等價 Argon2（`verify_password_dummy_async`），兩條路徑時間與訊息無從區分 |
| Refresh 輪替 | `jti` deny-list + 每用戶 epoch；**重複使用已撤銷的 token 視為洩漏**，直接 bump epoch 強制重新登入 |
| RBAC | `require_role` 兩邊正規化大小寫，**未知角色名稱在建立依賴時就拋錯**（啟動即炸） |
| 被封鎖者 | 一律 `404`（非 403）以免洩漏存在性；涵蓋 profile／histories／reviews／summary |
| 評價防刷分 | 授權綁定**雙方共同參與的那趟行程**（不是「這兩個人有沒有一起出遊過」） |
| 日誌注入 | `X-Request-ID` 只接受 `[A-Za-z0-9._-]` 且 ≤64 字元，其餘拒絕並改發新 id |
| 敏感欄位 | 遮蔽在 **formatter** 而非呼叫點（呼叫點會忘，formatter 不會） |
| 指標基數 | 標籤用**路由範本**而非原始路徑，否則每趟行程一條時間序列 |
| 上傳 | 驗證 magic bytes → **重新編碼**儲存（EXIF/GPS 全數丟棄） |

---

## 3. Deep Check 記錄：發現並修正的問題

### 3.1 文件與程式碼不一致（7 處，已修）

文件會**無聲腐化**。同一組數字（測試數、spec 數、端點數）在三個地方各寫一次，改一處不會提醒你改另外兩處。

| 位置 | 文件寫的 | 實際 |
|------|----------|------|
| README 測試清單 | 265 個、`test_audit` (32)、漏列 `test_manage_roles` | 279 → 281、(33)、14 |
| README P0 列 | Playwright 9 個測試 | 15 |
| README 專案樹 | e2e 2 個 spec；「50 HTTP」 | 4 個 spec；51 |
| README 可觀測性列 | 附 68 個測試 | 71 |
| `docs/API.md`、`docs/SECURITY.md` | `_has_travelled_together` | 已改名為 `_shared_trip_ids` |
| `docs/API.md` 免認證清單 | 漏了 `/auth/logout` | 它確實不需要 token（已補並說明理由） |
| `docs/ARCHITECTURE.md` §3.2 | 預算 **+1.2**、共同語言 **+1.0 × 命中數** | 程式是 **+0.8**、**flat +1.0** |

**配對權重那兩條最實質**：讀者會以為預算的權重比實際高 50%，並以為語言是累加的。
另外推薦理由的範例字串（「共同的旅遊風格」「預算相符（中等）」）也與程式輸出不符。

### 3.2 `tripmate_rate_limit_tripped_total` 永遠是 0（真 bug，已修）

指標宣告了、`/metrics` 也暴露了，但 `observe_rate_limit_trip()` **零呼叫點**。
**一個永遠為零的計數器比沒有更糟** —— 在憑證填充攻擊期間，儀表板會顯示「沒有任何請求被限流」，
那是主動誤導。已包住 slowapi 的 `RateLimitExceeded` handler，並補上**結構性**測試
（斷言註冊的不是 slowapi 的預設 handler）。

### 3.3 9 個沒人用的 i18n 字典鍵（已刪）

`common.save / confirm / delete / edit / back / submit / retry / optional / genericError`。
已刪除（341 → 332 鍵），並**擴充 `check-i18n.mjs` 讓它會抓「定義了但沒人引用」的鍵**。

> 這個新檢查第一次寫錯：用「出現次數 > 2」判斷，但字典檔本身就含兩次定義，
> 所以**永遠通過**。用注入死鍵的方式驗證後才發現並修正 ——
> 一個「永遠通過」的驗證器是最糟的一種錯。

### 3.4 已驗證為乾淨的項目

- **前端型別 vs Pydantic schema**：逐欄比對（含 optionality/nullability 與列舉集合）**無漂移**。
- **前端 16 個 runtime 依賴**：全部都有被 import。
- **無 TODO/FIXME**、無殘留暫存檔、無孤兒遷移。

### 3.5 CSP nonce：已實作、已證實不可行、已回退（技術債 #4）

技術債 #4 一直寫著「建議改用 nonce-based CSP」。**照著做了，然後用真實 production build 推翻了它。**

過程：

1. 在 `src/middleware.ts` 實作 per-request nonce，並讓 `script-src` 改為
   `'self' 'nonce-xxx' 'strict-dynamic'`（移除 `unsafe-inline`）。
2. **E2E 全綠** —— 15 個瀏覽器測試通過，看不出任何異常。
3. 對 production build 實際量測，結果：
   - 動態渲染的 **2** 條路由：正常。
   - **build-time prerender 的 10 條路由：每張頁面 18 個 CSP violation。**
   - 失敗的不是只有 inline script —— 加了 nonce 後 `'strict-dynamic'` 會讓 `'self'`
     對 script 完全失效，**連外部 chunk 檔案也被擋**。整頁白畫面。
4. 全面回退，`next.config.js` 保留 `unsafe-inline` 並在上方寫明原因；
   `npm run build` 輸出可見 `○ (Static)` 10 條、`ƒ (Dynamic)` 2 條 —— 就是這個比例的成因。

**根因**：nonce 每次請求都不同，而**預先渲染的 HTML 在建置時就產生完畢**，
結構上不可能帶上一個還沒被決定的值。這不是實作瑕疵，是二選一。

**留下的防護網**：新增 `npm run check:prod`（`frontend/scripts/check-prod.mjs`）——
啟動 `next start`、用 Chromium 載入 7 條路由（涵蓋靜態、動態、萬用字元），斷言
6 個安全標頭齊備、CSP 關鍵指令未被削弱、且 **violation 為 0**。

> **這個腳本已用人工注入的損壞 CSP 反向驗證過會失敗**：注入
> `script-src 'self'` 並移除 `X-Frame-Options` 後，它報出 23 個問題並以 exit 1 結束，
> 而且**獨立重現了原本的發現**（`Executing inline script violates ... script-src 'self'`）。
> 一個只會通過的檢查沒有價值 —— 這是 `check:i18n` 死鍵檢查教過的同一課。

**為什麼這件事重要**：這類缺陷**只有 production build 看得出來**。
`next dev` 每條路由即時渲染，所以 E2E 永遠測不到 prerender 專屬的問題。
「15 個測試全綠」與「10 條路由不可用」可以同時為真。

### 3.6 申請決策可被覆寫、`looking_for_count` 從未強制（真 bug，已修）

前一節的教訓是「green test suite 可以蓋住壞掉的行為」。順著同一條思路去查
**狀態機與授權**，在申請審批上找到兩個真缺陷。

`PATCH /trips/applications/{id}` 的寫入路徑只有一行 `application.status = decision` ——
**沒有狀態檢查、沒有容量檢查**。以真實請求驗證：

```
1st decision REJECTED -> 200 REJECTED
2nd decision ACCEPTED -> 200 ACCEPTED          ← 已結案的申請被翻轉
Bob's notifications: ['APPLICATION_ACCEPTED', 'APPLICATION_REJECTED']  ← 兩則互相矛盾
```

兩個獨立後果：

| # | 問題 | 實際影響 |
|---|------|----------|
| 1 | **決策不是最終的** | 對已 `REJECTED` 的申請重送 `ACCEPTED` 會成功。被拒絕的人可以變成被接受，而申請人同時收到「已婉拒」與「已接受」兩則通知 |
| 2 | **`looking_for_count` 形同裝飾** | 它在 schema 有驗證（`ge=1, le=20`）、有落庫，但**沒有任何程式碼讀回來** —— 宣稱徵 1 人的行程可以接受任意數量旅伴 |

**既有測試為何沒抓到**：`test_api_flow` 確實驗證了「非建立者 → 404」，
但**那條分支在寫入之前就 `return` 了**，所以那段變更從未被執行第二次。
這與技術債 #9（`lazy="raise"` 導致 500）是**同一個模式**：
測試只覆蓋了提前返回的分支，happy path 從未被真正執行。

**修法**（`api/v1/trips.py::decide_application`）：
- 已結案的申請**不得改變決策**。重送**同一個**決策回 `200` 且**不重複發通知**
  （客戶端逾時重試不該顯示成錯誤）；送出**不同**決策回 **`409`**。
- 接受前檢查 `already_accepted >= looking_for_count`，滿了回 `409`。
  檢查放在變更狀態**之前**，所以被拒絕的接受不會留下部分寫入。
- 新增 `tests/test_applications.py`（3 個測試），並在 `docs/API.md` 寫明狀態機。

> **測試以反向驗證確認有效**：停用兩個守衛後，3 個測試中有 **2 個失敗**
> （`expected 409 conflict, got 200`）；還原後全過。
> 只斷言「不會 500」的測試沒有價值 —— 要能指出**具體的錯誤行為**。

### 3.7 四個寫入端點只被「提早返回分支」覆蓋（已補測試，技術債 #21）

§3.6 的那個 bug 與 §3.2 的 #9 是**同一個模式的第三次出現**，所以這一節不再只修單一
缺陷，而是**逐條核對全部 29 個寫入端點**，把盲點一次清掉。

**這個模式長什麼樣**：唯一覆蓋某端點的測試，走的是**在寫入之前就 `return`** 的分支
（「非擁有者 → 404」「被封鎖 → 404」「型別不對 → 400」）。於是那段變更**從未被任何
測試執行過**，端點可以完全壞掉而測試全綠。

核對結果：29 個端點中 25 個有真正的 happy path（其中 `register`／`create_trip` 由
fixture 驅動，有執行但未斷言回應內容），**4 個是盲點**：

| 端點 | 原本的覆蓋 | 風險 |
|------|-----------|------|
| `POST /uploads/image`（成功路徑） | 唯一測試送 SVG 斷言 `400` | 只執行到 `sniff_mime`；`store_image`／重編碼／`_put_local` 全未執行 |
| `DELETE /profiles/me/histories/{id}` | 零覆蓋 | 刪除是否真的落庫、擁有權檢查是否生效，都無人驗證 |
| `POST /profiles/me/upload-url` | 零覆蓋 | 錯誤是否為 400（而非 500）無人驗證 |
| `POST /uploads/presign` | 零覆蓋 | 兩道守衛都未被釘住 |

**已補** `tests/test_upload_and_history_writes.py`（11 個測試）。每條都遵循同一原則：
**斷言成功狀態 ＋ 可觀察的寫入效果**，只斷言狀態碼不算數。

最值得記的是 **EXIF 那條**。`storage.py` 的 docstring 宣稱「重編碼會抹除全部 metadata
（含 EXIF GPS）」—— 這是本專案的核心私隱承諾之一，但**從來沒有任何測試驗證過**。
而且 `201` 本身無法區分「已抹除」與「原樣存回」。所以測試：

1. 先斷言 fixture **真的帶有** EXIF（否則測試會在「檔案本來就沒 metadata」的情況下
   假通過 —— 這是「只會通過的檢查」的另一種變體）；
2. 上傳後**從磁碟找出該物件**並解析其 EXIF，斷言標籤全部消失；
3. 同時 assert 它**仍是一張可解碼的圖** —— 否則「沒有 EXIF」也可以靠
   「檔案被截斷成零位元組」而通過。

**反向驗證**：停用四道守衛（足跡的擁有權檢查、二次刪除的 404、content-type 白名單、
EXIF 清除）→ **恰好這 4 條測試失敗**，其餘 7 條通過；還原後 11 條全綠。

#### 順帶查出的結構性事實：`presign_put` 的白名單在 HTTP 層不可達

```python
if settings.STORAGE_BACKEND != "s3":        # ← 先執行，local 後端在此就 400
    raise UploadError("Pre-signed uploads require the S3 storage backend.")
if content_type not in ALLOWED_MIME:        # ← 永遠到不了
    raise UploadError("Unsupported content type.")
```

在 local 後端下（**dev 與 test 的預設值**），content-type 白名單**完全不可達** ——
也就是說，**把它整段刪掉不會有任何測試變紅**。而它正是生產環境上**唯一**阻止
`application/pdf`（或呼叫者任意命名的型別）被寫入上傳桶的檢查。

這是「覆蓋率」這個指標的典型盲區：那幾行有被 import、有被「執行到函式」，
但在測試環境中**永遠不會被求值**。因此測試改為**直接呼叫 service 函式**並暫時切換
`STORAGE_BACKEND` 把這道守衛釘住（`generate_presigned_url` 是本地簽章，
不需要真憑證也不會連 AWS，所以「允許的型別會回傳 URL」本身就是白名單放行的正面訊號）；
HTTP 層則只斷言「local 後端下一律是那個 400」。

> **可推廣的規則**：當一段安全檢查被另一段較早的檢查**在測試環境中永久遮蔽**時，
> 它需要一條**直接呼叫該函式**的測試。否則它是一行程式碼意義上的裝飾品。

### 3.8 UI 完成度評估（2026-09-23，以真實瀏覽器逐頁檢視）

方法：以 Playwright 驅動真實 Chromium 走**完整註冊 → OTP → 驗證**流程後，
逐頁截圖檢視 11 條路由。**結論：UI 已基本完工，無佔位頁、無半成品。**

| 路由 | 狀態 | 內容 |
|------|------|------|
| `/` | ✅ 完整 | Hero、3 個統計、4 張功能卡、Footer |
| `/login` `/register` | ✅ 完整 | 雙同意勾選、OTP 開發模式提示、防列舉錯誤訊息 |
| `/trips` | ✅ 完整 | 安全提示橫幅、「為你推薦」、**全部 6 個篩選器**（國家／城市／預算／標籤／日期／分頁）、行程卡（配對分數 + 標籤） |
| `/trips/new` | ✅ 完整 | 隱私提醒（「只會公開國家與城市」）、日期選擇、標籤 chips |
| `/trips/[id]` | ✅ 完整 | 申請、行程群組、檢舉／封鎖 |
| `/profile` | ✅ 完整 | 頭像上傳（含 EXIF 提示）、已驗證徽章、**3 個頁籤**（個人檔案／旅遊足跡／帳號與私隱）、MBTI／性別／標籤／語言 |
| `/profile/[id]` | ✅ 完整 | 公開檔案、評價、檢舉／封鎖 |
| `/notifications` | ✅ 完整 | 「只看未讀」篩選、「全部已讀」動作、空狀態 |
| `/chat` | ✅ 完整 | 對話列表 + 訊息面板、正確空狀態 |
| `/admin` | ✅ 完整 | 雙頁籤（檢舉佇列 + 稽核紀錄）、角色閘門 |

**檢查「是否有未完成痕跡」**：全 repo 掃 `TODO` / `FIXME` / `WIP` / `尚未實作`
→ **零命中**（所有 `placeholder` 命中都是 HTML input 屬性，非佔位內容）。

**空狀態處理**：`/chat`、`/notifications` 都有設計過的空狀態與引導動作，
不是空白頁 —— 這是常被忽略但已做好的部分。

### 3.9 本輪驗證（2026-09-23，全部實跑）

| 檢查 | 結果 |
|------|------|
| `pytest -q` | **295 passed** |
| `npm run typecheck` / `lint` / `check:i18n` | 0 errors ／ 0 warnings ／ 332 鍵同步 |
| `npm run build` | 12 routes（10 靜態 + 2 動態） |
| `npm run check:prod` | 7 路由全 200、CSP violation **0** |
| `npx playwright test` | **15 passed** |
| `alembic check` | 無漂移 |
| 端點真值 | 50 HTTP `/api/v1` + 1 `/health` + 1 WS |
| `.dockerignore` 等價驗證 | `.env` / `.git` **正確排除**；`.env.example` / `app/` 正確納入 |
| 指標呼叫點 | 6 個 `observe_*` **全部有非定義處呼叫**（含曾永遠為 0 的 `observe_rate_limit_trip`） |
| 文件數字一致性 | README / STATUS 皆為 295 測試、50+1 端點、16 測試檔 |

> **本輪兩個「假失敗」值得記錄**（都是環境設定問題，非產品缺陷）：
> 1. 手動起後端時 DB 用了 `e2e_check.db`，但 `promoteToAdmin` helper 寫入
>    `backend/e2e.db` → 管理員測試失敗。**測試本身沒問題，是我給錯了 DB 路徑。**
> 2. 倉庫內**沒有 `.venv`**，`resolvePython()` 因此回退到裸 `python`（缺 uvicorn）
>    → webServer 起不來。**正解是設 `E2E_PYTHON`**（`e2e/python.ts` 的第一順位）。

---

## 4. 仍開放的問題

### 4.1 多副本部署前置清單（技術債 #3／#6／#8／#21／#22）

這些**不是缺陷**，是**單副本部署下的合理選擇**。目前 `docker-compose.yml` 的 `api`
是單一實例、`Dockerfile` 的 `CMD` 也沒有 `--workers`，所以**沒有一項會被觸發**。
一旦水平擴展（K8s／多台 VM／`uvicorn --workers N`），**全部必須一起處理** ——
它們同源（行程內狀態／缺乏跨行程協調），分開修等於同一個決策改五次。

| # | 問題 | 影響 | 處理方向 |
|---|------|------|----------|
| 6 | WebSocket 狀態存**單程序記憶體** | **最嚴重**：跨副本廣播失效 → 聊天訊息單向丟失（不報錯） | 改 Redis Pub/Sub |
| 3 | Redis 不可用時限流退回**記憶體**（fail-open） | 多副本下各副本各自計數，實際限額 = 副本數 × 限額 | 監控告警 Redis 健康；必要時改 fail-closed |
| 8 | 雜湊線程池是**每程序**上限 | 總並發 = 副本數 × `PASSWORD_HASH_MAX_CONCURRENCY`（Argon2 每次約 64 MiB） | 依記憶體規劃調整 |
| 21 | 接受申請的容量檢查有 **TOCTOU** 窗口 | 併發下可雙雙通過 → **安靜地超收旅伴** | 列鎖（PG）或條件式更新 + `rowcount` 檢查 |
| 22a | `kv.py` 的斷路器是**行程內變數** | Redis 故障時每個副本各學一次 → 各吃一輪失敗請求 | 斷路器狀態外部化（Redis 或共享快取） |
| 22b | `app.seed` 在啟動時執行 | 多副本同時 seed → 重複種子資料或競態 | 改為獨立 init job |

> ⚠️ **最容易被低估的一項**：很多人以為「還沒上 K8s，只是加幾個 `--workers`」很安全 ——
> **不對**。`uvicorn --workers 4` 就是多行程，**#6 已經觸發**。
>
> 建議順序：**#6 → #3 → #8 → #21 → #22a → #22b**。

### 4.1b 其餘已記錄的取捨

| # | 問題 | 影響 | 處理方向 |
|---|------|------|----------|
| 4 | CSP 的 `script-src` 含 `unsafe-inline` | 削弱 XSS 防護 | **已調查：與靜態預渲染無法並存**（見 §3.5）。要嚴格 CSP 就得放棄預渲染，需先做這個取捨 |
| 5 | `services/crypto.py` 無呼叫點 | 預留程式碼（電話已非 profile 欄位） | docstring 已修正：原本叫人設**不存在的** `FIELD_ENCRYPTION_KEY`。接上前須先解決金鑰輪換會使既有密文失效的問題 |
| 7 | 前端無狀態管理庫（只有 React Context） | 規模變大後快取／重試會變手工 | 引入 TanStack Query（成長觸發，非缺陷） |

### 4.2 值得留意的死碼（非缺陷）

- `pii.py::mask_phone` / `mask_email` — 零呼叫點。模組 docstring 說它們是
  「唯一被認可的聯絡方式呈現途徑」，但沒有任何使用者。電話已不是 profile 欄位，
  所以這是預留；但**「唯一被認可的途徑」從未被使用過**，值得確認是否符合預期。
- ~~`notifications.py::create_many`、`ws/manager.py::parse_uuid`~~
  **已刪除**：零呼叫點（含測試）確認後移除。`parse_uuid` 是 `manager.py` 中
  `uuid` 的唯一使用者，所以連同 `import uuid` 一併移除 ——
  否則只是把死碼換成未使用的 import。刪除後 281 個測試仍全過。

### 4.3 架構上的已知限制（有意識的）

- **伺服器端 `metadata` 無法依 locale 變化** —— locale 存 cookie，server component 看不到。
  要修就得把路由搬到 `[locale]` 之下（`docs/ARCHITECTURE.md` §13 有完整取捨表）。
- **`/metrics` 依設計不帶驗證**（scraper 不帶 JWT），所以**預設關閉**；
  開啟時必須置於內網或 allowlist 之後。
- **無 `admin` 使用者時管理介面完全不可達** —— 這是刻意的（沒有提權端點），
  但部署時必須記得先跑 `scripts/manage_roles.py promote <phone>`。

### 4.4 尚未動工

- **P3：行程地圖**。**選型已定：Leaflet**（而非 MapLibre —— 本用例只需在世界地圖上標點
  與連線，不需要 WebGL 向量渲染；Leaflet 的 42 KB、穩定 API、BSD-2-Clause 更合適）。
  圖磚起始用 OpenStreetMap 官方圖磚，流量上升後換 CDN／自架，程式碼不需改動。
  **不使用天地圖**：其唯一優勢是「中國境內合規」，而本 App 已確認為**非境內使用**，
  且其免費條款以境內主體為前提，對境外使用語焉不詳 —— 引入它反而增加合規不確定性。

  **真正的阻塞點不是圖層，而是隱私規則**：`models/trip.py` 目前**只存**
  `destination_country` / `destination_city` 兩個自由文字欄位、**沒有座標**，
  且 docstring 明說寬度粗化是為了遵守精確位置隱私規則（§2.2，目前為合規項 ✅）。
  要在圖上標點就得**新增經緯度**，等於**提高位置精度**，與 §2.2 直接衝突。
  因此實作前必須先決定：**放寬隱私規則**，或**改存城市中心點**（精度與「城市」等價）。
  其次要處理**地理編碼**（自由文字城市名 → 座標，含歧義、查不到、亂填），
  這是一條新的資料處理鏈，不是接圖層。

---

## 5. 常用指令

```bash
# 後端
cd backend && pytest -q                     # 295 passed
cd backend && alembic upgrade head && alembic check

# 建立第一個管理員（唯一途徑，非 HTTP）
cd backend && python scripts/manage_roles.py list
cd backend && python scripts/manage_roles.py promote +85291234567

# 對真實 uvicorn 的 24 項安全驗證
cd backend && python scripts/verify_runtime.py

# 前端
cd frontend && npm run lint && npm run typecheck && npm run check:i18n && npm run build
cd frontend && npm run check:prod    # 需先 build：驗證 production 標頭與 CSP violation 為 0
cd frontend && npm run test:e2e

# 對真實 uvicorn 的端到端流程（本機沙箱需繞過 safe-delete shim 與 proxy）
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8099   # 另一個終端
cd frontend && NEXT_PUBLIC_API_URL=http://127.0.0.1:8099 npx next dev -H 127.0.0.1 -p 3099
cd frontend && E2E_NO_WEBSERVER=1 E2E_PYTHON=<venv>/Scripts/python.exe npm run test:e2e
```
