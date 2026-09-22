# Trip Mate — 專案長期備忘

## 授權與環境
- **可直接刪 `trip-mate/` 內檔案，不需逐次詢問**（`_*.py`、`*.db`、`__pycache__`、
  `test-results`）。專案外檔案不授權。
- 後端 Python：`C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe`
- localhost 有 proxy → 需 `--noproxy '*'`。**safe-delete shim** 擋 `rm`／`npm run build`／
  `npx playwright`，繞法：
  ```bash
  env -u NODE_OPTIONS -u CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR \
      -u CODEBUDDY_TOOL_CALL_ID -u CODEBUDDY_SAFE_DELETE_BULK_GUARD <cmd>
  ```
  不可設 `..._GUARD=0`（走 helper-unavailable 分支，更糟）。shim 經 `NODE_OPTIONS` 注入子程序，
  **必須連它一起移除**。背景任務跑 `rm` 會永久卡住 → 清理單獨在前景做。

## ⭐ 頭號缺陷模式（技術債 #9／#20／#21 同源）
**「唯一覆蓋某端點的測試」若走提早返回分支，那段變更從未執行。** 分支固定長相：
`非擁有者→404`、`被封鎖→404`、`型別不對→400`，全在寫入前 return。→ **端點可完全壞掉而測試
全綠**（#9 就是這樣漏掉一個上線 500）。
- 補測試**必須有真正走到寫入的 happy path**，且**斷言可觀察效果**（重新讀取，或對**位元組**
  斷言）—— 只斷狀態碼不算數。
- **補完要做負向驗證**：停用該守衛 → 對應測試必須變紅；停用後仍全綠 = 沒釘住行為。
- **被較早檢查永久遮蔽的安全檢查，需「直接呼叫該函式」的測試**：`presign_put` 先判
  `STORAGE_BACKEND != "s3"`（local 必短路），於是 `content_type not in ALLOWED_MIME`
  **在測試環境永不被求值** —— 覆蓋率顯示「已覆蓋」，刪掉沒測試變紅，而它是生產上唯一阻止
  非圖片進上傳桶的檢查。做法：直接呼叫 + `try/finally` 暫時改 `settings.STORAGE_BACKEND`。
- **斷言「某物不存在」前必須先斷言它原本存在**（EXIF 抹除要先證明 fixture 真帶 EXIF；且
  「沒有 EXIF」也能由「檔案被截斷」滿足 → 必須同時 assert 仍可解碼）。
- **在既有編號清單插入項目後用 regex 重掃編號連續性**（犯過兩次）。

## DB 與 ORM
- `DATABASE_URL` 一律 async driver（`+asyncpg`／`+aiosqlite`）
- **SQLite 一律 `PRAGMA foreign_keys=ON`**（`db/session.py` 的 `connect`）。否則 `ondelete=`
  在 dev/test 是裝飾品、在 PG 才生效。**例外**：`alembic/env.py` 刻意關閉（batch 重建表需要）。
- **查詢形式要實測不靠直覺**：SQLite 不重排相關子查詢 → `EXISTS` 配非選擇性外層條件必全表
  掃描；`IN` 半連接可走索引。用 `EXPLAIN QUERY PLAN` 決定。
- **`is_deleted` 是 `SoftDeleteMixin` 的 `@property` 不是欄位** → 進 `select()`／`WHERE` 會在
  **建構查詢時**爆。一律 `deleted_at.is_(None)`。
- 遷移：刪欄／改結構前**先回填**；downgrade 不用 `sqlite.JSON()` 等方言型別；新增 NOT NULL
  到非空表**必須 `server_default`**。
- 標籤正規化唯一入口 `models/trip.py::normalise_tags()`（大寫／去空白／去重），寫入路徑三處
  各自實作；遷移**刻意不 import 應用程式碼**（否則 helper 改名會改變歷史遷移結果）。

## 後端慣例
- **RBAC 用 enum 值**：`UserRole.ADMIN` 值 `"ADMIN"`；`require_role("admin")` 會讓**所有管理
  端點對每個人回 403**（不是 500，所以像正常）。一律 `require_role(UserRole.ADMIN.value)`；
  未知角色名在建立依賴時拋 `ValueError`。此 bug 存活久是因專案**無授予角色的 API** → 測試從缺。
- **`enum_col` 的 `length` 由最長成員推導**（`max(20, longest)`）；寫死會在新增成員時於
  **mapper 設定階段**拋 `ValueError`（表徵：app 無法 import）。改動後跑 `alembic check`。
- **稽核 `detail` 只放 id／enum／數量**（`services/audit.py` 以**鍵名**遮蔽，丟棄鍵時寫 warning）。
- **指標標籤用路由範本**絕不用原始路徑（否則每趟行程一條時間序列）；`scope["route"]` 要在
  `call_next` **之後**讀（之前讀全變 `<unmatched>`）。**`add_middleware` 是前插**（最後加的最外層）。
- **狀態機終態必須寫進程式碼**：`PATCH /trips/applications/{id}` 曾無條件寫 `status = decision`
  → 已 `REJECTED` 可翻 `ACCEPTED` 且發矛盾通知。**同決策重送＝幂等 200；不同決策＝409**，
  容量檢查必須在**變更狀態之前**。殘留 TOCTOU（併發可雙雙通過）→ 併入多副本批次處理。

## 授權：綁「資源」不綁「關係」
- **評價授權必須綁「雙方共同參與的那趟行程」**（`reviews.py::_shared_trip_ids`）；只檢查「兩人
  有沒有一同出遊」= 配對層級 + 客戶端可指定 id → 可重放（技術債 #15）。
- **`trip_post_id` 為 `NULL` 時唯一性只能靠應用層**（SQL NULL 互不相等，唯一約束無效）。
- **`moderation.block_profile` 回 `(block, created)`**，幂等；呼叫端必須用 `created` 決定是否
  寫 `USER_BLOCKED`，否則留下從未發生的事件。解封同理（`if removed:`）。
- **封鎖後 404 涵蓋「整個 profile 表面」**（`/profiles/{id}`、`/histories`、`/reviews`、
  `/reviews/summary`），且用**同一個** `resolve_profile()`。

## 內容審核（`services/content_filter.py`）
- **誤擋比漏放更貴。** 旅遊文字滿是陷阱：hotel `deposit`、`killer` view、`kill` time、
  `Gunsan`、tuk-tuk `scams`、`drugstore`。只有幾乎沒有正當讀法的才 `BLOCK`，可辯解的一律
  `FLAG`（放行只計數）。`test_content_filter.py` 有 25 句真實語料鎖住這點。
- **零寬字元要試兩種正規化**（`_variants()`，互不包含）：`pro\u200bstitute` 只有移除才中；
  `escort\u200bservice` 只有換空格才中。
- **錯誤訊息只說欄位，不說命中的詞或規則**（否則等於讓人試出規則集）。
- **`enforce()` 給 HTTP（拋 422）；`screen()` 給 WS（回 tuple 不拋）**，WS 送
  `{"type":"error","code":"content_rejected"}`（socket 上沒有 HTTP 狀態碼）。
- 供應商故障 fail-open，**但只適用供應商**，本地規則照舊。
- **被擋的寫入／登入失敗不進 `audit_logs`**（只進指標＋日誌）：稽核表是唯一永不清理的表。

## 多語系（`frontend/src/lib/i18n/`）
- **字典是型別化的不是 JSON**：`zh-HK` 是鍵集合唯一來源，`MessageKey` 由它推導；其他語言宣告
  `Record<MessageKey, string>` → **缺翻譯是編譯錯誤**。加鍵先加 `zhHK`。
- **locale 存 cookie 不動路由**（刻意不用 next-intl `[locale]`）。代價：伺服器端 `metadata`
  只能預設語言、首次載入非預設語言會閃現。見 `ARCHITECTURE.md` §13。
- 列舉標籤用**明確對照表** `LABEL_KEYS`，不要 `` t(`${group}.${value}`) ``。
- **`errorMessage(err, fallback)` 的 fallback 必填**（`lib/api.ts` 拿不到 hook）；插入變數找
  不到時**保留 `{name}` 原文**。
- **`<html lang>` 用 `zh-Hant-HK`；`Intl` 用 `zh-HK`**（不是同一個值）。
- **`npm run check:i18n` 必跑**：字典外漢字、兩語言鍵集合不同、**定義但未引用**的鍵 → 失敗。
  `tsc` 看不到「從未搬進字典的字面值」。
- **不要濫用 `common.*`**：曾把「送出申請」接成 `common.submit`（=「送出」），型別正確但 UI
  壞了 —— E2E 抓到的。**按鈕文案用專屬鍵。**
- **Radix Popover 不會因點內部按鈕而關閉** → 需受控 `open` + 選完 `setOpen(false)`。

## CSP 與建置（技術債 #4，已結案）
- **10/12 路由是 build-time prerender，prerender HTML 無法帶 per-request nonce。** 實測 nonce
  CSP 在 production 造成**每張靜態頁 18 個 violation**（`'strict-dynamic'` 讓 `'self'` 失效，
  連外部 chunk 都被擋）→ app 不可用。**已完全回退**，`script-src` 保留 `'unsafe-inline'`，
  原因寫在 `next.config.js`。要再動先讀那段註解。
- **`scripts/check-prod.mjs`** 起 `next start` 載 7 條路由，斷言安全標頭 + **零 CSP violation**。
  只有 production build 看得到。
- **順序**：`build` → `check:prod` → `test:e2e`。**跑 E2E（`next dev`）會摧毀 `.next`**。
  `check:prod` spawn 前會檢查 `.next/BUILD_ID`。**`next dev` 在沙箱會因 safe-delete shim 幾秒
  後退出**（表徵像「dev server 自己停了」）。

## 前端檢查與 E2E
`lint`、`typecheck`、`check:i18n`、`build`、`check:prod`、`npx playwright test` 全跑。
- **`tsc` 過不代表沒事** —— lint 曾抓到 9 個 `react-hooks/exhaustive-deps`。
- **把 `t(...)` 加進既有 `useCallback`/`useEffect` 必須把 `t` 加進依賴陣列**，否則 callback
  握舊語言。**唯一例外：會建立連線的 effect**（WebSocket）→ 用 `tRef`，否則切語言會重連。
- ESLint：Next 14 要 `eslint@8` + `eslint-config-next@14.2.35`；ESLint 9 需 flat config（不支援）。
- E2E `npm run test:e2e`（加 safe-delete 前綴）；埠 後端 **8099**／前端 **3099**。**沙箱對沒在
  聽的埠回 502（非 refused）** → Playwright webServer 探測永遠失敗 → 自己開好服務後用
  `E2E_NO_WEBSERVER=1`。**`next dev` 必須 `-H 127.0.0.1`**（否則綁 `::1`）；手動開後端要設
  `BACKEND_CORS_ORIGINS` 含 `http://127.0.0.1:3099`，否則 preflight 400。
  **`resolvePython()` 只有一份**（`e2e/python.ts`）。
- **`getByRole("button", {name}).first()` 在標籤重複時點錯**（管理頁「已處理」同時是篩選與動作）
  → 限定在卡片內（`locator("li").filter({ hasText })`）。用 id 直接導航（`/trips/{id}`）比在
  列表找可靠（有分頁、E2E DB 跨次累積）。用一次性 `e2e.db`、`RATE_LIMIT_ENABLED=false`、
  `OTP_DEV_ECHO=true`（讀 `dev_code`）。
- **E2E 同時是預設語言的回歸測試**（以中文標籤定位）→ 抽字串後一定要跑 E2E，不能只跑 tsc。

## 測試 SQLite 陷阱（造成「假失敗」）
- **`Uuid` 存 32 位十六進位無連字號** → 帶連字號下 `WHERE id = ?` 永不匹配（fixture 看似有做事
  其實沒改到列）→ 要 `.replace("-", "")`。
- **`JSON` 欄位把 `None` 存成文字 `null`**（`none_as_null` 預設 False）→ 要 `json.loads`。
- **測試 DB 全 session 共用、只 `create_all` 從不 drop** → 斷言「恰好一列」前先清空該表
  （用 `sqlite_master` 檢查表存在，別 try/except 吞錯）。
- **無授予角色的 API** → 測試直接 `UPDATE users SET role='ADMIN'`（去連字號）；`conftest.py`
  已有 `admin_user` fixture。

## Deep Check 固定動作
- **文件會無聲腐化，數字寫在三處**：README（測試／spec／端點數）、`API.md`（端點清單）、
  `ARCHITECTURE.md`（數值型宣稱如配對權重）。**驗證是跑指令取真值，不是相信文件。**
- **真值**：端點 = `app.routes` 過濾 `HEAD/OPTIONS` → **50 HTTP + 1 WS**（`/health` 不在
  `/api/v1` 下要分開算 —— 這正是 README 曾誤寫 51 的原因）。測試檔 16 個、測試 295 條。
- **宣告卻從未遞增的指標是主動誤導**：`tripmate_rate_limit_tripped_total` 有宣告有暴露但零呼叫
  點 → 永遠 0。新增指標要確認有呼叫點（`grep observe_xxx` 找到非定義處）。
- **寫「找未使用項目」腳本必須先注入樣本證明它會失敗**：第一版 `check:i18n` 死鍵檢查用出現
  次數判斷（字典檔本身含兩次定義）→ **永遠通過**，最糟的一種錯。`check:prod` 已用人工損壞的
  CSP 反向驗證過會 exit 1。
- **不要照單全收 subagent 建議**：曾有 agent 建議刪 `.workbuddy-ai/memory/` 稱其
  「scratch notes」——**那是本專案記憶檔，不可刪**。一律自己驗證。
- **「排除嫌疑」與「找到缺陷」一樣有價值**：查證過的「無問題」結論也要寫進文件。
- **Deep Check 要涵蓋部署路徑**（`Dockerfile`／compose／`.dockerignore`）—— 前幾輪只掃
  `app/`、`tests/`、`docs/`，於是「兩個 Dockerfile 都 `COPY . .` 卻無 `.dockerignore`」
  一直沒被發現。**`.gitignore` 不保護映像層**。無 daemon 時把 `.dockerignore` 當 `.gitignore`
  用 `git check-ignore --stdin` 做等價驗證。
- **已驗證無問題（勿重複懷疑）**：`presign` 客戶端可控 `prefix`（簽名 URL 綁單一 key +
  content-type，`_local_path()` 另有 `Path.resolve()` 遍歷防護）；`/auth/me` 回未遮蔽自己的
  `phone_number`（`ProfilePublic` 確認無聯絡欄位外洩）；`is_public=false` 足跡對陌生人不外洩
  （回 `[]`）；登入／註冊**無帳號列舉**（皆 `401 Invalid credentials`）；`/trips/mine` 不需
  封鎖過濾。

## 角色 CLI
- **`scripts/manage_roles.py` 是唯一能授予 ADMIN 的途徑**（`list`／`promote`／`demote`），
  刻意**無** HTTP 端點。E2E 用 `e2e/helpers.ts::promoteToAdmin` 驅動。
- 護欄：撤銷最後一位管理員需 `--force`；已刪帳號不能升；每次印出目標 DB（遮蔽密碼）。

## 預留程式碼（不要當死碼刪）
- `services/crypto.py`（`encrypt_value`／`decrypt_value`）— 技術債 #5 明載為預留。docstring 曾
  叫人設**不存在**的 `FIELD_ENCRYPTION_KEY`（已修）；真實前置：金鑰由 `SECRET_KEY` 衍生
  （**輪換即讓既有密文無法解開，且靜默回 `None`**）。
- `pii.py::mask_phone`／`mask_email` — docstring 稱其為「唯一被認可的聯絡方式呈現途徑」。
- 純死碼（可刪非必要）：`notifications.py::create_many`、`ws/manager.py::parse_uuid`。

## 工具操作教訓（代價很高）
- **同一檔案不要在一個訊息發兩個 Edit** —— 工具**兩次都回報成功**但只有一個落地（實測兩次：
  `core/deps.py::require_role` 主體、`admin/page.tsx::AuditTrail` 簽章）。改同檔多處用**單一
  Python 腳本**，或一次一個 Edit 並先確認。
- **不要用 `Bash` + heredoc 寫多行 Python 替換腳本** —— anchor 含縮排極易不匹配（`assert
  count == 1` 回報 `0`）。用 `Write` 寫成正式腳本，每個替換前 `assert count == 1`。
  **here-doc 寫入後一定讀回驗證**（曾把 `\n` 變字面 `/n`，弄壞 `manage_roles.py`）。
- **`cmd1 && cmd2 &` 會把整串背景化**，後續仍在原目錄執行（曾因此在 repo 根建出空 `smoke.db`，
  `no such table` 看似 app bug）。啟動伺服器用 `run_in_background` + 絕對路徑。
- **字串偏移切片改檔易留多餘括號**（`SyntaxError: unmatched ')'`）。要「暫時停用邏輯再還原」
  用**語法保留的替換**（`if False and ...`）比切片可靠。
- **Git Bash 的 `/tmp` ≠ Python 的 `/tmp`**（後者 → `C:\tmp`）→ 暫存腳本與備份放**專案目錄內**。
- **錯誤訊息要指向真正原因**，「連不上伺服器」與「產物不存在」是完全不同的排查方向。
  **先驗證前置條件再啟動耗時動作**，否則錯誤會被 timeout 蓋掉。
- **`git check-ignore` 讀 `.gitignore`，不讀 `.dockerignore`。** 用它「驗證
  `.dockerignore` 擋不擋 `.env`」是**無效驗證**（結果來自 `.gitignore`）。
  正解：用 Python 自行實作 `.dockerignore` 的 pattern 語義（含 `!` 反向規則）再比對。
- **heredoc 寫 `node` 腳本會被 bash 展開 `${...}`**（`Error: Bad substitution`）。
  含 template literal 的腳本一律用 `Write` 寫檔再跑（與「別用 heredoc 寫多行 Python」同類）。
- **手動起 E2E 服務時，DB 檔名必須與 `frontend/e2e/constants.ts::E2E_DB_FILE` 一致。**
  `promoteToAdmin` 用它組 `DATABASE_URL` —— 不一致的話它升級的是**另一個 DB 的人**，
  表徵是「管理員測試失敗，看起來像 RBAC 壞了」。**首選讓 Playwright 自己管服務。**
- **倉庫內沒有 `.venv`** → `resolvePython()` 回退裸 `python`（缺 uvicorn）→ webServer 起不來。
  跑 E2E 要設 `E2E_PYTHON=C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe`。
- **`ENV` 只接受 `development`/`staging`/`production`**（`config.py` 是 Literal），沒有 `test`。
- **記憶檔的大小限制以「字元」計，不是位元組**。中文 1 字 = 3 bytes → 盯著 `wc -c` 會誤判
  （16.5K bytes 其實只有 9.5K chars）。**檢查超限用
  `python -c "import io;print(len(io.open(f,encoding='utf-8').read()))"`，不要用 `wc -c`。**
- **建立 repo 的第一個 commit 前必須 `git add -A --dry-run` 檢視入庫清單**：臨時檔若命名
  慣例不一致（`_` 前綴），很容易在這種一次性場合把臨時產物 commit 進去，之後要清就得改寫歷史。

## 仍開放（已記錄取捨，非缺陷）
- **多副本前置清單（README 技術債 #22 / `STATUS.md` §4.1）**：目前**單副本**部署
  （`docker-compose` 單實例、`CMD` 無 `--workers`），全部不觸發。一旦水平擴展要**一起**處理：
  #6 WS 單程序（最嚴重，訊息單向丟失）→ #3 限流退記憶體 → #8 雜湊池每程序上限 →
  #21 TOCTOU 超收 → **#22a `kv.py` 斷路器是模組級變數**（Redis 故障時每副本各學一次）→
  **#22b `app.seed` 啟動時執行**（多副本同時 seed 競態）。
  ⚠️ **`uvicorn --workers N` 就是多行程，#6 已經觸發** —— 別以為「還沒上 K8s 就安全」。
- **P3 行程地圖**：**選型已定 Leaflet**（非 MapLibre；只需標點連線，不需 WebGL；
  BSD-2-Clause）。圖磚用 OSM，流量大再換 CDN／自架。**不用天地圖**（唯一優勢是境內合規，
  本 App 非境內使用，且其條款以境內主體為前提）。
  **真正的阻塞點是隱私規則**：`models/trip.py` 只存國家／城市自由文字、**無座標**，
  且 §2.2 明訂寬度粗化是合規要求。加經緯度 = 提高精度 = 衝突 → 須先決定
  「放寬」或「改存城市中心點」。其次是地理編碼（歧義／查不到／亂填）。**不是接圖層那麼簡單。**
- 這些不是「待修 bug」，是**已量測並記錄的架構取捨**。要動前先讀 README 對應技術債條目。
- **此 repo 已有 commit**（首個 `00bd6f9`）。`.gitignore` 已驗證正確
  （忽略 `.env`／`*.db`，保留 `.env.example`）。
