# Trip Mate — 專案狀態報告

> 本文是**現況快照**：完成了什麼、為什麼這樣設計、還有什麼問題。
> 所有數字都是**實際跑指令取得**的，不是從其他文件抄來的。
>
> 最後更新：2026-09-23（Deep Check + UI 逐頁檢視之後）

---

## 0. 一句話總結

規格要求的 P0／P1／P2 **全部完成並通過驗證**；後端 349 個測試、前端 22 個瀏覽器測試全綠。
**UI 已逐頁檢視確認完工**（11 條路由，無佔位頁，見 §3.8）。
剩下的是**多副本部署前置清單**（6 項同源問題，目前單副本皆不觸發，見 §4.1）、
3 項其餘取捨。P3 行程地圖**已完成**（Leaflet + OpenStreetMap）；選型的阻塞點是隱私規則
而非圖層，兩者一併解決，見 §4.4。

---

## 1. 進度

### 1.1 功能完成度

| 優先級 | 模組 | 狀態 |
|--------|------|------|
| **P0** | 認證（手機 + OTP + Argon2id + Dual-Token JWT）、Refresh 輪替與洩漏重用偵測、真實 SMS 抽象層、首個 Alembic 遷移、前端 E2E | ✅ |
| **P1** | 個人檔案／旅遊足跡、行程發佈與篩選、智能配對、申請與審批、WebSocket 聊天、評價制度、防騷擾（封鎖／檢舉／限流）、PDPO 合規（同意軌跡／註銷／EXIF 抹除）、平台免責、站內通知、管理後台 | ✅ |
| **P2** | 標籤 join table、可觀測性與稽核軌跡、內容審核、多語系 i18n | ✅ |
| **P3** | 行程地圖（Leaflet + OSM）+ 城市參考表 | ✅ |

### 1.2 驗證數字（全部實跑）

| 檢查 | 指令 | 結果 |
|------|------|------|
| 後端測試 | `pytest -q` | **403 passed**（257 秒） |
| 前端型別 | `npm run typecheck` | **0 errors** |
| 前端靜態分析 | `npm run lint` | **0 warnings** |
| i18n 完整性 | `npm run check:i18n` | **341 鍵**、zh-HK/en 同步、零硬編碼、零未使用鍵 |
| 前端建置 | `npm run build` | **12 routes**（10 靜態預渲染 + 2 動態） |
| production 標頭／CSP | `npm run check:prod` | **7 條路由全 200、6 標頭齊備、CSP violation 0**（真實 production build + Chromium） |
| 瀏覽器 E2E | `npx playwright test` | **22 passed**（5 個 spec，真實 Chromium × 真實 uvicorn） |
| 遷移一致性 | `alembic check` | **無漂移** |
| 資料庫結構 | `Base.metadata` | **15 表 / 32 索引** |
| API 端點 | `app.routes` | **52 HTTP + 1 WebSocket**（另有 `/health`，不在 `/api/v1` 下） |

### 1.3 程式碼規模

```
backend/app/      66 個 Python 檔案（57 模組 + 9 個 `__init__.py`）
backend/tests/    24 個測試檔（403 個測試）
backend/alembic/  5 支遷移
frontend/src/     39 個 TS/TSX（11 頁面 + 9 元件 + 12 UI + 6 lib，含 i18n 字典）
frontend/e2e/     5 個 spec（22 個測試）
docs/             5 份文件（API / ARCHITECTURE / SECURITY / STATUS / ATTRIBUTION）
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
  ├ models/*     15 張表（SQLAlchemy 2.0 async，`lazy=` 一律明確宣告）
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

### 3.0 賽跑無法只靠突變證明：三次才量對

本輪最有價值的一個方法論教訓，寫在這裡因為它會重犯。

`AuditTrail` 的 `requestId` 守衛是為了修掉一個**已從 trace 實證**的真 bug（開發模式下
React strict mode 雙重觸發 mount effect → 同一組參數的稽核清單請求發兩次 → 慢的舊回應
覆蓋新的，管理員看不到自己剛產生那筆稽核）。但我為它做負向驗證時，連錯兩次：

| 嘗試 | 做法 | 結果 | 為什麼錯 |
|------|------|------|----------|
| 1 | 停用守衛、跑整套 | 報 **1/3 釘住** | 賽跑**取決於時序**。沒有延遲時舊回應通常不會最後落地，突變因此**不可觀測**，測試照樣綠 |
| 2 | 新增測試，只斷言「篩選後清單為空」 | 停用守衛**仍通過** | 這個斷言**什麼都沒釘住** —— 新回應落地時畫面本來就是空的 |
| 3 | 對被篩選的請求加 1200 ms 路由層延遲，並**同時**斷言「新結果出現」與「舊列消失」 | **3/3 釘住** | — |

**通則**：突變測試只對「決定性」的行為有效。對時序相依的賽跑，**延遲才是讓突變可觀測的東西**，
而斷言必須同時涵蓋「新狀態成立」與「舊狀態被拒絕」兩側，否則會寫出一個永遠通過的測試。

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
已刪除（原本 341 → 332 鍵；後續功能新增鍵後，本輪實測為 **341 鍵**），並**擴充 `check-i18n.mjs` 讓它會抓「定義了但沒人引用」的鍵**。

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
| `pytest -q` | **362 passed** |
| `npm run typecheck` / `lint` / `check:i18n` | 0 errors ／ 0 warnings ／ 341 鍵同步 |
| `npm run build` | 12 routes（10 靜態 + 2 動態） |
| `npm run check:prod` | 7 路由全 200、CSP violation **0** |
| `npx playwright test` | **22 passed** |
| `alembic check` | 無漂移 |
| 端點真值 | 52 HTTP `/api/v1` + 1 `/health` + 1 WS |
| `.dockerignore` 等價驗證 | `.env` / `.git` **正確排除**；`.env.example` / `app/` 正確納入 |
| 指標呼叫點 | 6 個 `observe_*` **全部有非定義處呼叫**（含曾永遠為 0 的 `observe_rate_limit_trip`） |
| 文件數字一致性 | README / STATUS 皆為 349 測試、52+1 端點、17 測試檔、15 表 |
| 負向驗證（端點守衛） | **7/7** 釘住；停用後對應測試變紅，還原後 sha256 一致 |
| 負向驗證（`city_id` 寫入） | **4/4** 釘住；同上 |
| 負向驗證（稽核清單亂序） | **3/3** 釘住（第三版才量對，見下方「賽跑無法只靠突變證明」） |

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
| 6 | ~~WebSocket 狀態存**單程序記憶體**~~ **已解** | ~~跨副本廣播失效 → 聊天訊息單向丟失（不報錯）~~ **已修**：`ws/pubsub.py` Redis Pub/Sub 扇出 + 每人配額外部化 + **presence 註冊表** | ✅ 完成。扇出：`pubsub.py`；**每人限流**：`manager.allow_message` → `kv.DistributedTokenBucket`（跨副本共享）；**presence**：`manager.online_profiles` → `kv.PresenceRegistry`（每副本一個 TTL 條目，讀取取聯集）。`is_member_online` **刻意保持本副本語意**（它回答的是「我現在能不能投遞」，是路由問題，已於 docstring 說明） |
| 3 | ~~Redis 不可用時限流退回**記憶體**（fail-open）→ 多副本下實際限額 = 副本數 × 限額~~ **已修** | ~~各副本各自計數~~ **已修**：HTTP 計數改走 `rate_limit.SharedCounter`（同步 redis client + 斷路器），WS 改走 `kv.DistributedTokenBucket`。Redis 不可用時**仍降級為每副本**（刻意，可用性優先），但會**告警並說明配額被放大**，且日誌每秒最多一次 | ✅ 完成。共用原語：`services/kv.py`。負向驗證 5/5（見 `tests/test_shared_quota.py`） |
| 8 | ~~雜湊線程池是**每程序**上限~~ **已解** | ~~總並發 = 副本數 × `PASSWORD_HASH_MAX_CONCURRENCY`（Argon2 每次約 64 MiB）~~ **已修**：新增 `kv.DistributedSemaphore`，`hash_password_async`／`verify_password_async`／`verify_password_dummy_async` 每次雜湊都先取一個**全域** permit。anyio 的線程池上限保留為**每行程後備**（Redis 不可用時仍生效）。Redis 不可用 → 降級為每副本並在啟動時**以記憶體算術告警**（`8 × 副本數 × 64 MiB`） | ✅ 完成。取不到 permit 時**等待最多 2.5 秒再放行**（不拒絕登入），逾時記 warning |
| 21 | ~~接受申請的容量檢查有 **TOCTOU** 窗口~~ **已解** | ~~併發下可雙雙通過 → 安靜地超收旅伴~~ **已修**：兩層防護 —— (a) 狀態轉移改為**條件式 UPDATE**（`WHERE status='PENDING'`，`rowcount` 為仲裁者）；(b) PostgreSQL 上先取 trip 列的 `SELECT ... FOR UPDATE`，再重讀容量 | ✅ 完成。`rowcount != 1` → rollback 後回報實際結果（同決定 idempotent 200、異決定 409） |
| 22a | ~~`kv.py` 的斷路器是**行程內變數**~~ **已解** | ~~Redis 故障時每個副本各學一次 → 各吃一輪失敗請求~~ **已修**：斷路器狀態移入 `kv._BreakerState`，可用 `set_breaker_backend()` 接上共享後端；`RedisBreakerBackend` 把窗口發布到 Redis hash（TTL 防止永久卡住）。**刻意仍是 publisher 而非依賴源** —— 讀取失敗一律退回本地狀態，否則「需要 Redis 才能知道 Redis 掛了」 | ✅ 完成。啟動時 ping 成功才安裝；`rate_limit` 用同一 seam |
| 22b | ~~`app.seed` 在啟動時執行~~ **已解** | ~~多副本同時 seed → 重複種子資料或競態~~ **已修**：seed 全程包在 PostgreSQL advisory lock（`pg_advisory_lock`／`unlock`）內。逐列存在性檢查只擋得住**循序**重跑，擋不住併發（大家都在任何 commit 之前讀到「不存在」）。SQLite 無此機制 → **誠實的 no-op** | ✅ 完成。`docker-compose.yml` 已標為 one-shot init 步驟 |

> ⚠️ **最容易被低估的一項**：很多人以為「還沒上 K8s，只是加幾個 `--workers`」很安全 ——
> **不對**。`uvicorn --workers 4` 就是多行程，**#6 已經觸發**。
>
> 建議順序：~~#6~~ → ~~#3~~ → ~~#8~~ → ~~#21~~ → ~~#22a~~ → ~~#22b~~。
> **多副本前置清單已全部結案。**
>
> **進度（2026-09-24）**：#6 的**訊息扇出**已實作並釘住（4/4 突變皆變紅，見
> `tests/test_ws_fanout.py`）。
>
> **進度（2026-09-26）**：**#3 + #6 殘留（每人配額）已合併修完**。兩個限流器
> （WS 2 msg/s、HTTP 5/min）過去都存**行程內計數**，N 副本即 N 倍配額；現共用
> `services/kv.py` 的分散式原語，Redis 健康時配額全域共享。負向驗證 5/5
> （見 `tests/test_shared_quota.py`）。
> **寫測試的代價很高的教訓**：第一版測試用「同一行程的兩個 manager」當兩個副本 →
> 突變全綠、等於什麼都沒釘住。**同一行程的兩個物件不是兩個副本** —— 副本要用
> 各自獨立的 `kv` 模組（`importlib`）建模。同理，patch `_redis` 會繞過斷路器
> （斷路器在 `_redis` 內），必須 patch 更低一層的 client class。
>
> **進度（2026-09-26b）**：**#8 + #21 + #22a + #22b 一次修完**，多副本前置清單結案。
> 四項同源（**per-process 的量測被當成全域的事實**）：
> - **#8**　新增 `kv.DistributedSemaphore`；`security.py` 三個雜湊入口各取一個全域
>   permit，anyio 線程池降為每行程後備。取不到時**等待 2.5 秒再放行** ——
>   這裡的失敗方向必須是「配額變大」，不能是「登入壞掉」。
> - **#21**　條件式 UPDATE（`WHERE status='PENDING'` + `rowcount`）＋ **無條件**取得列鎖
>   （PG `SELECT ... FOR UPDATE`；SQLite 用 no-op write 逼出寫鎖）後**重讀**容量。
>   兩層缺一不可：只有鎖擋不住無鎖方言，只有 CAS 擋不住兩個不同申請同時通過容量檢查。
>   ⚠️ **列鎖必須在 `if decision == ACCEPTED:` 之外** —— 初版把它寫在分支內，
>   於是 REJECT 完全不設防，ACCEPT 與 REJECT 對同一申請可互相交錯（實測 2/12
>   出現 `codes=[409,200] final=['REJECTED']`，即 accept 宣告成功卻不持久）。
> - **#22a**　斷路器移入 `kv._BreakerState`，`RedisBreakerBackend` 發布窗口。
>   **刻意設計成 publisher 而非依賴**：讀取失敗一律退回本地 ——
>   「需要 Redis 才知道 Redis 掛了」的斷路器比沒有更糟。
> - **#22b**　seed 包在 `pg_advisory_lock` 內。逐列存在性檢查只對**循序**重跑有效。
>
> 負向驗證 **9/9 PINNED**（另 1 例聲明不可觀測、排除分母），見
> `tests/test_multi_replica_hardening.py`（16 個測試）。教訓沿用上一輪：
> **同一行程的兩個物件不是兩個副本**，副本要用 `importlib` 從同一原始檔載入獨立模組；
> `rate_limit` 與 `kv` 各有**自己的** `_breaker`，只重設一個會讓斷路器測試因為
> 「被永久停用」而**假通過**。
>
> ⚠️ **本次最重要的教訓：flaky 測試會讓突變報告的數字失去意義。**
> `test_concurrent_contradictory_decisions_on_one_application` 原本斷言「同時發出的
> ACCEPT 必贏」—— 實測 **~17% 是 REJECT 贏**，因為兩個請求同時發出、勝負由排程器決定，
> **沒有任何機制規定誰先**。它同時讓突變 I 被誤報成 PINNED（那一列的失敗是這個 flake
> 造成的）。修法是斷言**不變式**（只有一個贏、資料列等於贏家的決定），修後 15/15 穩定、
> 突變 I 重跑 8/8 全紅。harness 現對每個突變重跑多次，且只把聲明不可觀測的 case 視為
> 負向控制、排除在分母外。
>
> **進度（2026-09-27）**：**#6 最後一項 —— presence 已修**，多副本前置清單真正全清。
> `ConnectionManager.online_profiles` 過去只能回答「連到**本副本**的人」，N 副本下
> 房間名單只報出其中一半，**哪一半還取決於負載平衡器**（不是不完整而已，是不確定）。
> 修法**不是**一個共享 SET —— 那沒有存活語意：副本被 SIGKILL 不會呼叫 `disconnect`，
> 它的成員會**永遠**留在集合裡，而且系統**無法分辨鬼影與安靜的真人**。因此改為
> **每副本一個帶 TTL 的條目**（`tripmate:presence:{room}:{replica}` → `"a|b"`），
> 由心跳刷新；讀取是**未過期條目的聯集** → 鬼影**由構造界定**，不靠記帳。
> - 心跳**重寫整份名單**（delete + sadd + expire），不只是延長 TTL —— 這才是讓
>   聯集讀**自我修復**的原因：漏掉的 `leave` 會被下一次心跳覆蓋掉。
> - **`online_profiles` 改為 `async`**（刻意的）：呼叫端不應該能把「本副本的切片」
>   誤當成整個房間。
> - **presence 永不拋錯**（聊天熱路徑：每次 join／leave 都呼叫）。Redis 掛掉 →
>   退回本副本名單，也就是**修之前**的行為：少報，不失敗。
> - 負向驗證 **10/10 PINNED**，見 `tests/test_presence_registry.py`（17 個測試）。
>   ⚠️ 其中一個 case 起初是 NOT-PINNED：用 `online_profiles` 驗證 `connect` 有寫入
>   presence 是**假的**——本副本讀取光靠 socket 表就會回傳該成員，把 `join` 拿掉測試
>   照樣綠。必須改由**另一個副本**（手上沒有任何 socket）觀察才真的釘住。
> - 附帶修掉一個心跳任務洩漏：`asyncio.Task` 若活得比建立它的 loop 久，就會產生
>   「Task was destroyed but it is pending」。兩層修法 —— 迴圈在最後一個房間清空時
>   **自行結束**，且心跳**預設關閉**（`heartbeat_enabled=False`），只有應用程式
>   singleton 明示開啟。「有人記得呼叫 `shutdown()`」不是保證，測試直接建 manager
>   永遠不會呼叫它。

### 4.1b 其餘已記錄的取捨

| # | 問題 | 影響 | 處理方向 |
|---|------|------|----------|
| 4 | CSP 的 `script-src` 含 `unsafe-inline` | 削弱 XSS 防護 | **已調查：與靜態預渲染無法並存**（見 §3.5）。要嚴格 CSP 就得放棄預渲染，需先做這個取捨 |
| 5 | `services/crypto.py` 無呼叫點 | 預留程式碼（電話已非 profile 欄位） | docstring 已修正：原本叫人設**不存在的** `FIELD_ENCRYPTION_KEY`。接上前須先解決金鑰輪換會使既有密文失效的問題 |
| 7 | 前端無狀態管理庫（只有 React Context） | 規模變大後快取／重試會變手工 | 引入 TanStack Query（成長觸發，非缺陷） |

### 4.1c 全面審查（2026-09-26）— 新發現，**全部已修**

完整報告：**`docs/AUDIT-2026-09-26.md`**。基線 403 passed、lint/tsc/i18n 全綠、56 endpoints。
以下**全部**在綠燈下存活 —— 現有測試綠不等於沒有 bug。

每一項都做了**負向驗證**：停用該守衛 → 對應測試必須變紅。詳見 AUDIT 各節。

| # | 問題 | 影響 | 狀態 |
|---|------|------|------|
| B1 | **重複 review → 永久 500**。`reviews.py` 用 `.scalar_one_or_none()`；`trip_post_id` 可為 NULL 而 SQL 的 NULL 互不相等 → 唯一約束對 NULL 無效 → 兩列匹配 → `MultipleResultsFound` 未捕捉。**已重現**（植入兩列後打一次 endpoint → `unhandled_exception`），且**永久無法經 API 恢復** | **高** | ✅ 已修 |
| B3 | **`PATCH /trips/{id}` 可超收**。`looking_for_count` 與 `status` 皆可由客戶端設定，且**不重查已 ACCEPTED 數** —— accept 路徑在無條件列鎖下防守此不變式，這個端點繞過去。**已重現**（limit 3→1 而有 2 位已接受 → **200 OK**，之後**無端點可修**） | **高** | ✅ 已修 |
| B2 | **DIRECT 房間可重複建立**：`chat_rooms` 只對**成員**有 unique，pair 無約束 → 並行 `POST /chat/rooms` 產生兩房 | **高** | ✅ 已修 |
| B4 | **`presign` 信任客戶端 `prefix`** → 可為任意 key 前綴取得 signed PUT（路徑穿越未轉義；S3-only 故本地防護不適用）。現有測試釘的是 library helper，**不是 route** | **高** | ✅ 已修 |
| F1 | **前端 refresh 競態（獨立於 single-flight）**：`setAccessToken` 清的是**槽**，但已排入的 `attempt` 仍會寫 token → **登出／換帳號後被舊 token 覆寫**。`finally` 只保護槽 | **高** | ✅ 已修 |
| B5 | 並行 `block` → `IntegrityError` → **500**（契約是 idempotent 201）；`uq_block_pair` 存在但程式不處理違反 | 中 | ✅ 已修 |
| F2 | chat 頁舊 socket `onclose` **覆蓋新房間狀態**；handler 無 per-socket 守衛，`openedRef` 為全連接共用 | 中 | ✅ 已修 |
| B6 | **後端硬編碼中文**（`ws/routes.py:220`／`:80`、`content_filter.py:246`）→ 英文 locale 收到中文。`check:i18n` 只掃前端，抓不到 | 中 | ✅ 已修 |
| F3 | profile 頁 histories fetch **無取消守衛**；`addHistory`／`removeHistory` **無 busy guard**（只 `saveProfile` 有） | 中 | ✅ 已修 |
| B7 | 跨副本 breaker 用 `time.monotonic()` **跨行程比較** → 無共同 epoch，#22a 的跨副本那半不成立 | 中 | ✅ 已修 |
| B8 | 五個 `DELETE` 端點**無 rate limit**（POST 對應端點都有）——**實際是 10 個**（見下） | 低 | ✅ 已修 |
| B9 | `matching.py:113` 用本地 `date.today()` 比 UTC timestamp → 時區相依的排序 | 低 | ✅ 已修 |
| D4 | 前端頁面過大（`chat` 455、`profile` 509 行），fetch／生命週期簿記手工 | 低 | ✅ 已修 |
| D5 | i18n 字典單體 938 行，兩語言內嵌 | 低 | ✅ 已修 |
| D6 | 沒有系統性併發測試覆蓋輸掉競態的不變式 | Info | ✅ 已修 |

> **B8 的教訓：審查報告低估了自己。** 報告說「五個 DELETE 無 rate limit」，但改用
> **路由盤點測試**（讀 `app.routes`）代替人手核對後，另外找到**五個真的沒有限流的寫入端點**：
> `POST /auth/refresh`、`/auth/logout`、`/auth/password`、`PATCH /admin/reports/{report_id}`、
> `POST /uploads/presign`。實際是 **10 個**，不是 5 個。報告裡 `notifications.py:141` 的行號也已過期
> —— 該處本來就有裝飾器。**人手核對清單會腐化；讀路由的路徑不會。**
>
> **B7 的教訓：報告診斷正確，但修法要更精確。** 光是把 `monotonic()` 換成 `time.time()` 不夠 ——
> 發布端與讀取端必須**各自**正確：發布絕對 wall-clock deadline，讀取端把它換成**時長**
> （`remaining = published - time.time()`）再鉗制到 `min(remaining, _COOLDOWN_SECONDS)`，
> 而本地窗仍然只用 `monotonic()`。如此一台在錯誤時刻重啟的副本不會繼承一個立刻過期
> （→ 繼續打掛掉的 Redis）或永不過期（→ 永遠不回原）的窗。
>
> **B9 的教訓：修法本身要讓它可測。** 把 `date.today()` 改成 `datetime.now(timezone.utc).date()`
> 是一行；但**行內的** `date.today()` 讀的是測試控制不到的主機時區。所以順手把評分抽成純函數
> `score_post(post, signals, today)` —— 時鐘由呼叫者交給它。這才是讓「用 UTC」這件事**可斷言**的原因：
> 六個純規則測試直接釘住新鮮度分層，第七個測試釘住「呼叫者傳的是 UTC 日期」。
> **把時間當參數傳進去，是把時序邏輯變可測的最省力做法。**

> **⭐ 結構性母題（D3）：同一個不變式、多條寫入路徑、只在其中一處防守。**
> B1／B3／B5 全是這個形狀：capacity 在 `decide_application` 有鎖、在 PATCH 沒有；
> review 唯一性對非 NULL 靠 DB、對 NULL 只有非原子的 app 檢查；block 靠約束但程式不處理違反。
> **不變式要從一處執行** —— 抽共用 helper，並盡量讓 DB 約束當仲裁者。
> 這是本專案第二個反覆出現的缺陷母題（第一個是「量測是每行程的，描述對象卻是整個部署」）。
>
> **D6（已修）：系統性併發測試** —— B1／B2／B5 全是輸掉的競態，所以
> `tests/test_concurrency_invariants.py` 用**同一套 recipe** 為每個不變式寫一個測試。
> 兩條規則寫在檔案開頭，讓下一個不變式可以「複製模式」而不是「重新發明」：
> ① `TestClient` **表達不出競態**（它的 context manager 會序列化請求，用寫的「併發」測試對壞碼照樣綠）
> —— 要 `httpx.ASGITransport` + `asyncio.gather` 加 `NullPool`，兩個請求才真的拿到兩條連線；
> ② 每個測試**同時**斷言兩側 —— liveness（至少一個成功，否則「拒絕所有人」也能滿足不變式）
> 與 safety（持久化狀態守住不變式）。只斷言狀態碼會在「被拒的寫入其實已套用」時通過；
> 只斷言狀態會在「所有寫入都因無關理由被拒」時通過。兩種都被這個專案咬過。
>
> 負向驗證 **4/4**。兩個過程中的教訓值得留著：我第一次的 block 突變打的是函式的**前置檢查**
> （`if existing:`）而不是競態真正走到的 `IntegrityError` 分支 —— 也就是說**被突變的那一行在真實競態下
> 根本不可達**，什麼都沒證明。改打競態路徑後，才暴露測試真正的洞：它只斷言 block 列數，
> 從不斷言稽核列，所以一個對既有列回報 `created=True` 的 `block_profile` 會照樣通過。
> 補上 `USER_BLOCKED` 稽核列計數後，突變才變紅。**「沒釘住」通常先代表測試有洞，不代表行為正確。**

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

### 4.4 已完結：行程地圖（原 P3）

**選型：Leaflet**（而非 MapLibre —— 本用例只需在世界地圖上標點與連線，不需要 WebGL
向量渲染；Leaflet 的 42 KB、穩定 API、BSD-2-Clause 更合適）。圖磚用 OpenStreetMap
官方圖磚，流量上升後換 CDN／自架，程式碼不需改動。
**不使用天地圖**：其唯一優勢是「中國境內合規」，而本 App 已確認為**非境內使用**，
且其免費條款以境內主體為前提，對境外使用語焉不詳 —— 引入它反而增加合規不確定性。

**原阻塞點是隱私規則，不是圖層，已一併解決。** 原本 `models/trip.py` 只存
`destination_country` / `destination_city` 兩個自由文字欄位、沒有座標，而 docstring
明說寬度粗化是為了遵守精確位置隱私規則 —— 在圖上標點就得新增經緯度，等於提高精度。

解法是**把座標的來源做成參考表，而不是使用者輸入**：

| 問題 | 解法 |
|------|------|
| 精度與 §2.2 衝突 | 座標只從 `cities` 參考表取**城市中心**（約公里級）。城市名本身就洩漏同等資訊，故**不增加**可識別性。§2.3 已重寫記錄此論證 |
| 自由文字城市名的歧義 | 城市改為從 GeoNames `cities5000` 挑選（`city_id` FK），**廢除了自由文字寫入路徑** |
| 配對因拼寫不一致而靜默失敗 | 同上：無法驗證的輸入不能參與逐字比對（`docs/ARCHITECTURE.md` §15.1） |
| 重新匯入 GeoNames 會刪掉使用者資料 | FK 動作為 `ON DELETE SET NULL`，非 `CASCADE`（§15.7） |

實作：`app/api/v1/cities.py`（搜尋 + by-id）、`scripts/import_cities.py`、
`components/CityPicker.tsx`、`components/CityMapLoader.tsx` + `CityMap.tsx`。
設計取捨見 `docs/ARCHITECTURE.md` §15.1–15.12、`docs/SECURITY.md` §2.3。

> **剩下的是部署層面的待辦，不是功能缺口**：圖磚服務的可用性與 CSP 白名單需在
> 生產環境確認（`img-src` 必須包含 `*.tile.openstreetmap.org`）。

---

## 5. 常用指令

```bash
# 後端
cd backend && pytest -q                     # 362 passed
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
