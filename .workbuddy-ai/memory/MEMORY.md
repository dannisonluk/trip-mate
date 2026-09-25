# Trip Mate — 長期備忘

> 只留仍影響決策者；做完的細節留 `YYYY-MM-DD.md`。

## 授權與環境

- **可直接刪 `trip-mate/` 內的檔案，不需逐次詢問**；專案外不在此授權。
- 後端 Python：`.../python/envs/default/Scripts/python.exe`（repo 無 `.venv`，
  `resolvePython()` 退回裸 `python` → 缺 uvicorn。一律設 `E2E_PYTHON`。）
- 建置／E2E 要 `-u NODE_OPTIONS`；本機有 proxy，打 localhost 要 `NO_PROXY=*`。
- `ENV` 只接受 `development`／`staging`／`production`（`config.py` 是 `Literal`），**無 `test`**。

## ⭐ 頭號缺陷模式：授權／狀態綁錯層級（#9／#20／#21 同源）

授權要綁**具體資源**，不是綁人與人的關係，也不是客戶端可指定的範圍。

- 評價授權綁「雙方共同參與的那趟行程」（`reviews.py::_shared_trip_ids`）；只查
  「這兩人有沒有一起出遊過」= 配對層級 + 可指定 `trip_post_id` = **可重放**。
- **`trip_post_id IS NULL` 的唯一性只能靠應用層**（SQL 的 NULL 互不相等，
  `uq_review_once_per_trip` 對 NULL 完全無效）→ 用 `Review.trip_post_id.is_(None)` 去重。
- **`moderation.block_profile` 回傳 `(block, created)`**：幂等，呼叫端**必須**用 `created`
  決定是否寫 `USER_BLOCKED` 稽核列；解除封鎖同理（`if removed:`）。
- **封鎖後 404 覆蓋「整個 profile 表面」**（`/profiles/{id}`、`/histories`、`/reviews`、
  `/reviews/summary`），且同前綴端點要用**同一個** `resolve_profile()`，
  否則對 id 的意義會分歧。

## ⭐ 第二號缺陷模式：同一不變式、多條寫入路徑、只在一處防守（B1／B3／B5／B8）

**不變式要從一處執行** —— 抽共用 helper，並盡量讓 DB 約束當仲裁者。

- capacity：`_enforce_capacity(db, post, pending_accept=)` 是**唯一**住處，accept 與
  `PATCH /trips/{id}` 都走它。`pending_accept` 明確傳不推斷 —— 兩呼叫端算術不同。
- **並行寫入的不變式，用「路由盤點測試」而非人手清單查。** B8 報告說 5 個 DELETE 無限流，
  讀 `app.routes` 後實際是 **10 個**，且報告的行號已過期。**人手清單會腐化。**
- **slowapi 陷阱**：被包裝的函式**必須**有 `request: Request` 參數，否則 **import app 就拋**
  `No "request" or "websocket" argument on function`。只加裝飾器是炸在**啟動**，不是請求。
- **路由盤點測試要防自己 vacuous**（附一條「不是所有 GET 都看起來有限流」的檢查）。

## DB 與 ORM

- `DATABASE_URL` 一律 async driver（`+asyncpg`／`+aiosqlite`）。
- **SQLite 一律 `PRAGMA foreign_keys=ON`**（`db/session.py` 的 `connect` 事件），否則
  `ondelete=` 在 dev/test 是裝飾品、在 PG 生效（同一操作兩種語意）。
  **Alembic 例外**：`env.py` 走自己的連線，刻意保持外鍵關閉（batch 重建需要）。
- **`is_deleted` 是 `SoftDeleteMixin` 的 Python `@property`，不是欄位** → 放進 `select()`
  會在建構查詢時爆。查詢一律用 `deleted_at.is_(None)`。
- **`enum_col` 的 `length` 由最長成員推導**（`max(20, longest)`）；寫死會在新增成員時於
  **mapper 設定階段**拋 `ValueError`（表徵：無法 import）。改動後跑 `alembic check`。
- **遷移鐵則**：刪欄／改結構前必須**先回填**；downgrade 不用方言專屬型別；
  新增 NOT NULL 欄位到非空表必須給 `server_default`。
- **標籤唯一正規化入口**：`models/trip.py::normalise_tags()`。**遷移刻意不 import
  應用程式碼**（否則 helper 改名會改變歷史遷移的結果）。
- **查詢形式用 `EXPLAIN QUERY PLAN` 實測定**：SQLite 不重排相關子查詢，`EXISTS` 對上
  非選擇性外層條件一定全表掃描；`IN`（半連接）可由索引驅動。

## 城市參考表（P3，已完成）

- **座標只有城市層級**（GeoNames 中心點），**永不儲存使用者提供的位置**；
  `ON DELETE SET NULL`（非 CASCADE：重新匯入會**刪掉使用者行程**）。
- **席次加成是主排序鍵**：`PPLC`(3) > `PPLA*`(2) > else(0)，排在人口之前。要釘住它，
  種子資料必須存在**「兩鍵矛盾」的前綴**。**`feature_code` 不放進 `CityOut`** → 斷言可觀測後果。
- **`city_id` 寫入驗證**用共用 `resolve_city_id()`，查不到 → 422；選擇器 **select-only**
  （配對引擎逐字比對，自由輸入會靜默永不匹配）。**`react-leaflet@5` 需 React 19** →
  本專案 React 18.3.1，**必須 `4.2.1`**。權威來源：`SECURITY.md` §2.2／§2.3。

## 🚨 認證與 Cookie（真 bug，皆已修）

- **refresh 重放偵測**：`auth.py::refresh` 每次成功都**撤銷**舊 token；再出示已撤銷的
  → 視為盜用 → `bump_epoch` → **所有 session 失效**（同 cookie 三個並行 → 1×200、2×401）。
- **前端 `tryRefresh()` 必須 single-flight**（完成後只在「還是自己」時清空；
  `setAccessToken` 也要清它）。每呼叫者各發一次 ⇒ 發多個並行認證請求的頁面會被登出。
  > 通則：**競態在測試環境不可觀測，就對 API 直接重現**（curl），不要靠 E2E 擲骰子。
- **`COOKIE_DOMAIN` 必須為空**（host-only）→ `core/config.py::
  _cookie_domain_must_be_empty_in_production`（非空 → 拒絕啟動）。**不可用 `__Host-` 取代**
  （它要求 `Path=/` 且無 `Domain`，而 refresh cookie path 刻意收窄到 `/api/v1/auth`）。
  ⚠️ **依賴欄位順序**：`info.data.get("ENV")` 要 `ENV` 宣告在前，改順序**靜默失效**。
  測試用 `_env_file=None`（否則本機 `.env` 覆蓋 `ENV`）。

## 🚨 多副本：量測、時鐘、presence（#3／#6／#8／#21／#22a／#22b，全部已解）

**同源母題：「量測是每行程的，描述對象卻是整個部署」。** 單副本時恰好等於全域上限
—— 這就是它們長期存活且沒測試的原因。**完整推導見 `ARCHITECTURE.md` §16.8／§17／§18。**

- **配額／上限一律分散式**：WS 速率 → `kv.DistributedTokenBucket`；HTTP 速率 →
  `rate_limit.SharedCounter`（用**同步** client，因 slowapi 在 `async_wrapper` 內同步求值）；
  雜湊並發 → `kv.DistributedSemaphore`（**全域**，與 anyio thread limiter 的**每行程**
  角色不可互換；後者是 Redis 掛掉時的最後防線）。`PASSWORD_HASH_MAX_CONCURRENCY` 是
  **記憶體預算**（`64 MiB`／次 × 副本數）→ 啟動要 log **算式**，不是設定值。
- **降級方向必須是「額度變大」不是「功能壞掉」**：拿不到許可 → 等 2.5 秒**不計數放行**，
  永不拒絕登入，但**大聲告警**。**降級時本地投遞照常**（否則 Redis 故障升級為**總聊天故障**）。
- **semaphore 的 TTL 是洩漏閥，不是過期語意**（持有者死掉不會 `DECR` → key 棘輪到永遠滿）；
  用 `INCR` 後才判斷。**斷路器是 publisher 不是真相來源**，只在啟動 `ping()` 成功後才裝。
- **B7：跨副本的窗要發布 wall-clock deadline，不可發布 monotonic。** `monotonic()` 無共同
  epoch → 發布絕對值會讓在錯誤時刻重啟的副本繼承一個立刻過期（→ 繼續打掛掉的 Redis）或
  永不過期的窗。修法：發布 `deadline = time.time() + _COOLDOWN_SECONDS`；讀取時換成**時長**
  `remaining = published - time.time()` 再鉗到 `min(remaining, _COOLDOWN_SECONDS)`；
  `self.cooldown_until` 保持 **monotonic** 且只與 `time.monotonic()` 比。負值跳過，非數值忽略。
- **`broadcast()` = 本地投遞 + 發布；`deliver_local()` = 只本地**（誤用 → 無限互轉）。
  **降級決定用 `publish()` 回傳值，不是 `enabled`**；**交付非同步** → 測試要 `_settle()`。
  **任何持有 loop 的東西（`asyncio.Event`／`Task`）不可在 import 期建構。**
- **presence 要有存活語意，不能只是共享 SET**：**被 SIGKILL 的副本永不呼叫 `disconnect`**
  → 成員永遠留在集合，且**無法分辨鬼影與安靜的真人**。→ 每副本一個 TTL 條目，心跳
  **重寫整份名單**（`delete`+`sadd`+`expire`，不只延長 TTL —— 這是聯集讀自我修復的來源），
  讀取取**未過期條目的聯集**（鬼影**由構造界定**）。`online_profiles` 改為 **async**；
  `is_member_online` 保持同步本副本語意。**presence 永不拋錯** → Redis 掛掉退回本副本名單。
- **要驗證一個寫入，就要從看不到本地狀態的地方讀**（另一副本，手上沒有 socket）。

## ⭐ 序列化鎖、CAS，與「不可斷言不存在的順序保證」
- **列鎖必須無條件取得，不可只掛在需要它的分支上。** `decide_application` 的列鎖原本寫在
  `if decision == ACCEPTED:` 之內 → REJECT 完全不設防；`NullPool` 讓每請求各有連線 →
  兩者都讀到 `PENDING`、都走到 CAS → **後寫者勝出**（實測 2/12：accept 宣告成功卻不持久）。
- **TOCTOU 兩層不可互換**：列鎖擋「兩個**不同**申請同時通過容量檢查」；
  CAS（`UPDATE ... WHERE status='PENDING'` 的 `rowcount`）擋「**同一**申請被決定兩次」。
  SQLite 逼寫鎖用對該列的 no-op write（`SET col=col`）；`SELECT ... FOR UPDATE` 是 PG-only。
- **同時發出的兩個請求，勝負由排程器決定**（曾斷言「ACCEPT 必贏」→ 實測 ~17% 是 REJECT 贏）。
  這是**測試發明了不存在的保證**。要斷言**不變式**：只有一個贏、資料列等於**贏家**的決定。

## 前端檢查與 E2E

- 必跑：`lint`、`typecheck`、`check:i18n`、`build`、`npx playwright test`，外加四支
  守衛腳本 `check:prod`／`check:refresh`／`check:ws-guard`／`check:profile-history`。
  **`tsc` 過不代表沒事**（lint 抓過 9 個 `exhaustive-deps`）。**抽字串後一定要跑 E2E**
  ——E2E 同時是預設語言的回歸測試（以中文標籤定位）。
- **`t(...)` 加進既有 `useCallback`／`useEffect` 必須把 `t` 加進依賴**。**唯一例外：
  會建立連線的 effect**（WebSocket）→ `useRoomSocket` 的 callback／resolver 一律經 **ref** 讀，
  effect 只依賴 `roomId`/`profileId`；把 `t` 列進去會在**每次切語言時重建 WebSocket**。
  ESLint：Next 14 要 `eslint@8` + `eslint-config-next@14.2.35`（ESLint 9 需 flat config）。
- **抽 hook 時守衛要跟著邏輯搬，守衛腳本也要一併改指向新檔案** —— 否則腳本讀舊路徑會
  安靜地變成「0 個守衛 = 通過」。**改檔案位置後，一定要重跑讀原始碼的檢查。**
- **定位元件的陷阱**：`getByRole("button",{name}).first()` 在標籤重複時會點錯
  （「已處理」同時是篩選與動作）→ 限定卡片內；用 id 導航（`/trips/{id}`）比列表可靠。
  **不要用會隨周圍文字改變的 accessible name 定位受控元件**（按鈕名會串上外層 `<Label>`）
  → 用 `page.locator("#city")`。地圖上疊視覺隱藏的文字副本會讓 `getByText` 變歧義。
- **`globalSetup` 會刪 `e2e.db`**，但它在 `webServer` **之前**跑 → 正常路徑安全；
  `E2E_NO_WEBSERVER=1` 時後端已開著舊檔 → **跳過刪除、仍跑 seed**。
- **可靠跑法**：自起兩個 server（後端**必須帶 `BACKEND_CORS_ORIGINS`**；前端**必須同時給
  `NEXT_PUBLIC_API_URL` 與 `NEXT_PUBLIC_WS_URL`**），再 `E2E_NO_WEBSERVER=1 E2E_SKIP_SEED=1
  npx playwright test`。

## 測試環境的 SQLite 陷阱（會造成「假失敗」）

- **`Uuid` 存 32 位十六進位無連字號** → 帶連字號下 `WHERE id = ?` 永不匹配，要
  `.replace("-", "")`（連 raw DB 斷言也要，否則計數讀成 0 而**看似**通過）。
  **`JSON` 欄位把 `None` 存成文字 `null`** → raw 讀回要 `json.loads`。
- **測試 DB 全 session 共用、只 `create_all` 從不 drop** → 斷言「恰好一列」前先清空該表。
- **無授予角色的 API** → 測試直接 `UPDATE users SET role='ADMIN'`（去連字號）。

## 負向驗證（專案鐵則）

- **停用守衛 → 對應測試必須變紅。仍全綠 = 沒釘住行為。**
- **「沒釘住」先代表測試有洞，不代表行為正確。** D6 實例：block 的突變一開始打的是
  `block_profile` 的**前置檢查**，而競態走的是 `IntegrityError` 分支 ——
  **被突變的那一行在該情境下不可達**，什麼都沒證明。改打競態路徑後，才暴露測試只斷言
  block 列數、從不斷言稽核列（而 `created` 正是 B5 的全部重點）。
  > **假陰性（突變不可達）長得和「行為正確」一模一樣。** 改突變位置前先確認它會執行。
- **突變測試只對「決定性」行為有效；對時序相依的賽跑，延遲才是讓突變可觀測的東西。**
  實例：`AuditTrail` 守衛停用後整套仍綠 ——不是「沒釘住」，是**競態沒發生**。
  做法：`page.route()` 加延遲讓舊回應最後落地，斷言**同時**涵蓋「新狀態成立」與
  「舊狀態被拒絕」兩側。
- **只斷言「新結果出現」通常等於什麼都沒斷言**：曾寫出「篩選後清單為空」，停用守衛仍通過。
  **假的安心比沒有斷言更糟。**
- **併發測試要同時斷言兩側**：liveness（≥1 成功，否則「拒絕所有人」也能滿足不變式）
  與 safety（持久化狀態守住不變式）。只斷狀態碼會在「被拒的寫入已套用」時通過；
  只斷狀態會在「全部因無關理由被拒」時通過。**`TestClient` 表達不出競態**
  （context manager 序列化請求）→ 要 `httpx.ASGITransport` + `asyncio.gather` + `NullPool`。
- **跑不到測試（0 collected）不可報成「未釘住」**：那是 harness 故障，要留 log。
  **失敗偵測器不能找 `assert` 字樣**（移除守衛可能以 `IntegrityError`／HTTP 錯誤失敗）
  → 用 `failed and collected`。
- **fake 不模擬被取代物語意，測的是 fake**（fake broker 把 `psubscribe("prefix*")`
  當精確比對 → 每條跨副本測試都紅，而生產程式碼是對的）。
- **`alembic check` 要在「新建並 upgrade head 的 DB」上跑**（否則「not up to date」是
  未套用遷移，不是漂移）。**SQLite 的 `LIKE` 對 ASCII 折疊大小寫** → `PPLA%` 改小寫
  在 SQLite **永不變紅**。
- **flaky 測試會讓整份突變報告失去意義**（突變曾被報成 PINNED，只因無關 flake）。
  **「未釘住」要能重現才算發現** → 每個突變重跑多次。
- **量測期間不要有其他東西在動工作樹**（harness 就地改寫原始檔、同時跑 pytest 會 import
  到半突變的樹）→ **先懷疑量測方法**。改完一定 `diff -q` 對備份確認 byte-identical。
- **寫「找出未使用項目」腳本，必須先注入樣本證明它會失敗** —— 第一版 `check:i18n` 死鍵檢查
  寫錯方向（永遠通過），最糟的一種錯。

## 多語系（`frontend/src/lib/i18n/`）

**改動前先讀 `docs/ARCHITECTURE.md` §13（含 §13A hook 節）。** 要點：
- **字典是型別化的，不是 JSON**（`zh-HK` 是鍵集合唯一來源，其他語言
  `Record<MessageKey, string>` → **缺翻譯是編譯錯誤**）。
- **字典拆成 13 個 namespace 檔**（359 鍵），`dictionaries.ts` 是薄的**展開式組裝器**
  （`...admin.zhHK, …` 而非 `Object.assign`：展開在型別層檢查重複鍵）。
  **新增 namespace 檔要記得 import 進 `dictionaries.ts`**（`check:i18n` 有檢查）。
- **拆大檔／改字典一定要做 lossless diff**：曾把 `myProfile.privacyBody` 的內嵌引號
  雙重轉義（`\\"` 而非 `\"`）。**`LABEL_KEYS` 不可憑記憶重寫** —— 用
  `git show HEAD:<file>` 原樣還原（我第一版發明了不存在的成員、又漏了 `gender.ANY`）。
- **locale 存 cookie，不動路由**（代價：`metadata` 只能是預設語言、首次載入閃現）。
- **`<html lang>` 用 `zh-Hant-HK`；`Intl` 用 `zh-HK`**（不同值）；**不要濫用 `common.*`**。

## 其他規範（審核／稽核／RBAC／CSP）

- **內容審核（`content_filter.py`，詳見 `SECURITY.md`）：誤擋比漏放更貴。**
  只有幾乎沒有正當讀法的才 `BLOCK`，可辯解的一律 `FLAG` 放行（旅遊文字滿是陷阱：
  hotel `deposit`、`killer` view、`kill` time、`Gunsan`、tuk-tuk `scams`、`drugstore`）。
  零寬字元要試**兩種正規化**。供應商故障 fail-open，**且只適用供應商**。
  **被擋下的寫入不進 `audit_logs`**。
- **稽核 `detail` 只放 id／enum／數量。** `services/audit.py` 以**鍵名**遮蔽，且**丟棄任何
  鍵時寫 warning**。別把免費文字塞進去：稽核表刻意活得比註銷更久。
- **指標標籤用路由範本**（`/api/v1/trips/{trip_id}`），**絕不用原始路徑**。
  `scope["route"]` 要在 `call_next` **之後**才讀得到（之前讀會讓全部請求變 `<unmatched>`）。
  **`add_middleware` 是前插**（最後加入的最外層）。**宣告了但從未遞增的指標是主動誤導**。
- **RBAC：`require_role` 一定要用 enum 值**（`UserRole.ADMIN` 的值是 `"ADMIN"`）；
  寫 `require_role("admin")` 會讓**全部管理端點對每個人回 403**（看起來像正常）。
  未知角色名稱在建立依賴時就拋 `ValueError`。**`scripts/manage_roles.py` 是唯一能授予
  ADMIN 的途徑**，刻意**無** HTTP 端點。
- **CSP／建置（#4 已結案）：`.gitignore` 不保護映像層** —— 兩個 Dockerfile 都 `COPY . .`
  卻無 `.dockerignore` 曾長期沒被發現。無 daemon 時用 Python 自行實作 pattern 語義再比對
  （**`git check-ignore` 讀 `.gitignore`，拿它驗 `.dockerignore` 無效**）。

## 工具操作教訓（代價很高）

- **不要用 `Bash` + heredoc 寫多行腳本**：anchor 含縮排極易不匹配，bash 又會展開 `${...}`
  （`Bad substitution`），且**會把 `\n` 寫成字面 `\n`**。一律用 `Write`，替換前
  `assert count == 1`，寫入後**讀回驗證**。
- **Windows 原始檔是 CRLF** → 多行 anchor 用 `\n` 會**靜默不匹配**。讀用 `newline=""`、
  `replace("\r\n","\n")` 比對、寫回用 `newline="\r\n"`。
- **`Edit` 工具：`old_string` 與 `new_string` 只差一個尾端換行 = 靜默 no-op**（仍報成功）。
  **同一檔案同一訊息只發一個 `Edit`**（第二個會被丟棄，但兩個都報成功）。
- **`cmd1 && cmd2 &` 會把整串背景化**（曾在 repo 根建出空 `smoke.db`）。
  **Git Bash 的 `/tmp` ≠ Python 的 `/tmp`**；暫存檔放 `var/`。- **記憶檔限制以「字元」計**：中文 1 字 = 3 bytes → 別盯 `wc -c`。
- **Playwright 的 `webServer` 探測可能無故逾時**（180s），而手動 `next dev` 3.5s 就 Ready
  → **判斷法是手動起一次**。`next dev` 必須 `-H 127.0.0.1`（否則綁 `::1` 打不到）。
- **`pytest` 全跑約 5 分鐘** → 用 `run_in_background`，不要 `| tail`（會緩衝到沒輸出）。
- **`next build` 可能因殘留 `.next` 被鎖而 EPERM**：`rm -rf .next` 後重試；
  若仍 EPERM，`touch .next/trace` 先建檔再重試。

## Deep Check 固定動作

- **文件會無聲腐化，數字寫在三處**：README、`API.md`、`ARCHITECTURE.md`。
  **驗證是跑指令取真值，不是相信文件。**（README 的「362 passed」曾爛到實際 469。）
- **端點真值用 OpenAPI／`app.routes` 量**，不要用 `grep @router`（多行 decorator 會漏算）。
  `/health` 不在 `/api/v1` 下要分開算。**要涵蓋部署路徑。**
- **已驗證無問題（勿重複懷疑）**：`/auth/me` 回未遮蔽自己的 `phone_number`；
  `is_public=false` 足跡對陌生人不外洩；登入／註冊**無帳號列舉**。
- **`services/crypto.py` 不是死碼**（#5 預留）。金鑰由 `SECRET_KEY` 衍生
  （**輪換即讓既有密文無法解開，靜默回 `None`**）。

## 仍開放（已記錄取捨）

- ⚠️ **`uvicorn --workers N` 就是多行程** —— 不是「還沒上 K8s 所以安全」。
- **E2E DB 重置只在 Playwright 管理 server 時生效**（`E2E_NO_WEBSERVER=1` 由呼叫者負責）。
- 其餘為**已量測並記錄的架構取捨**（CSP `unsafe-inline`、`crypto.py` 預留、無狀態庫），
  非「待修 bug」；要動前先讀 README 對應技術債條目。
- `AUDIT-2026-09-26.md` 的 B1–B9／F1–F3／D4–D6 **全部已修**（`STATUS.md` §4.1c）。
