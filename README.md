# Trip Mate 🧭

> 為香港旅客而設的**旅伴配對平台**。發佈行程 → 智能配對 → 安全對話 → 結伴出發。
>
> 本專案依《Trip Mate System Specification》與《Security & Privacy Specification》實作，
> 並已統一更名為 **Trip Mate**。

---

## 目錄

1. [核心功能與目標使用者](#1-核心功能與目標使用者)
2. [技術架構與現有程式碼](#2-技術架構與現有程式碼)
3. [模組清單](#3-模組清單)
4. [使用流程與主要使用情境](#4-使用流程與主要使用情境)
5. [快速開始](#5-快速開始)
6. [專案結構](#6-專案結構)

---

## 1. 核心功能與目標使用者

### 1.1 一句話定位

Trip Mate 解決「**想找人一起去旅行，但不知道找誰、也不敢隨便找**」這個問題：
它把「配對」與「安全」放在同等重要的位置 —— 既能依旅遊足跡、風格與語言推薦合拍的旅伴，
也用技術手段（封鎖、速率限制、私隱遮蔽、評價防刷分）把風險降到最低。

### 1.2 核心功能（已實作）

| # | 功能 | 說明 |
|---|------|------|
| 1 | **手機帳號與認證** | 香港手機號註冊（`+852` + 8 位）、SMS OTP 驗證（可插拔供應商）、Argon2id 密碼雜湊、Dual-Token JWT（15 分鐘 Access + 7 天 Refresh 存 HttpOnly Cookie）、**Refresh 輪替與洩漏重用偵測**（登出／改密碼即時失效） |
| 2 | **個人檔案** | 暱稱、簡介、MBTI、性別、旅遊風格標籤、語言、頭像；統計出遊次數與平均評價 |
| 3 | **旅遊足跡** | 國家／城市、起訖日期、預算類型、摘要、相片；可逐筆設定公開或私人 |
| 4 | **行程發佈** | 標題、說明、目的地（僅國家／城市）、日期、預算類型、徵求性別、標籤、徵求人數、狀態 |
| 5 | **智能配對** | 依「足跡城市／國家 × 風格標籤 × 共同語言 × 預算 × 新鮮度 × 出發時間」加權評分，並回傳**推薦理由**（可解釋而非黑盒） |
| 6 | **申請與審批** | 旅伴申請 → 行程作者接受／婉拒 → 接受後**自動開啟一對一聊天室** |
| 7 | **即時通訊** | WebSocket 聊天（DIRECT 私訊 / TRIP 群組）、輸入中提示、上線狀態、心跳保活；REST 提供歷史訊息 |
| 8 | **評價制度** | 只有**真正一起出遊過**的旅伴才能互評，杜絕刷分；1–5 星 + 8 種標籤 + 文字評論 |
| 9 | **防騷擾** | 雙向封鎖（封鎖後無法搜尋、私訊、申請、開房）、檢舉（5 種原因）、聊天 2 msg/s 限速、電話號碼偵測提醒 |
| 10 | **私隱合規** | PDPO 明示同意軌跡、電話號碼永不外洩至他人、一鍵註銷（匿名化 / 完全刪除）、精確位置不外洩、EXIF 全數抹除 |
| 11 | **平台免責** | 行程頁與聊天室固定顯示安全與法律免責聲明 |
| 12 | **站內通知** | 申請、審批結果、新訊息、新評價 → Navbar 鈴鐺徽章（30 秒輪詢 + 視窗回焦重同步）；**永不通知自己、永不跨越封鎖**（否則通知會變成騷擾管道）；訊息類通知以**房間**為單位去重 |
| 13 | **管理後台** | `/admin` 雙頁籤：**檢舉佇列**（角色閘門、狀態篩選、逐筆處置）＋**稽核紀錄**（唯讀、依動作篩選、分頁） |
| 14 | **可觀測性與稽核** | 結構化日誌（JSON／文字雙格式、請求關聯 id、**敏感欄位在 formatter 統一遮蔽**）、`X-Request-ID` 貫穿日誌與回應、Prometheus `/metrics`（以**路由範本**為標籤，避免基數爆炸）、**唯讀稽核軌跡**（封鎖／檢舉／註銷／管理員操作） |
| 15 | **內容審核** | 所有使用者產生的文字（行程標題／說明、暱稱／簡介、足跡摘要、評價、聊天）都經過 `content_filter`：**本地高精確度規則**可 `BLOCK`（422 拒寫），可疑但仍可辯解的內容只 `FLAG`（放行並計數）；可插拔外部審核供應商（`local` / `webhook`），**供應商故障一律 fail-open** |
| 16 | **多語系（繁中／English）** | 332 個字串集中在型別化字典：`zh-HK` 是鍵集合來源，其他語言宣告為 `Record<MessageKey, string>`，**缺翻譯是編譯錯誤**。locale 存 cookie，**不動任何路由**；NavBar 有語言切換器；`npm run check:i18n` 防止字串回退成硬編碼 |

### 1.3 目標使用者

| 客群 | 痛點 | Trip Mate 的價值 |
|------|------|------------------|
| **18–35 歲香港自由行旅客**（主力） | 朋友時間對不上、不想獨遊又想有人分攤住宿 | 依風格與目的地配對，快速找到合拍旅伴 |
| **獨遊背包客 / 攝影愛好者** | 特定題材（攝影、登山）需要同好 | 旅遊風格標籤 + 足跡配對，精準命中同好 |
| **首次嘗試結伴的謹慎使用者** | 擔心人身與財產安全 | 封鎖、檢舉、私隱遮蔽、免責提示四重保障 |
| **小團體發起人** | 想組織 2–4 人小團 | 徵求人數上限、行程群組聊天室 |

**非目標客群**：旅行社／商業導遊（平台定位為 C2C 資訊媒合，非交易平台，不處理金流）。

---

## 2. 技術架構與現有程式碼

### 2.1 技術棧

| 層 | 技術 | 選擇理由 |
|----|------|----------|
| 前端 | **Next.js 14.2 (App Router) + React 18 + TypeScript** | App Router 支援 Server/Client 元件分工；`next.config.js` 直接設定安全標頭 |
| 前端樣式 | **Tailwind CSS 3.4 + Shadcn 風格元件**（Radix UI + CVA） | 以 HSL CSS 變數做主題、支援深色模式；元件為本專案自有原始碼，無額外執行期依賴 |
| 後端 | **FastAPI 0.115 + Pydantic v2 + SQLAlchemy 2.0（Async）** | 全鏈路 async：`AsyncSession` 從 router 直通 service 與 WebSocket，事件迴圈零阻塞 |
| 資料庫 | **PostgreSQL 15+**（`asyncpg`）／開發與測試用 **SQLite**（`aiosqlite`） | 同一份 ORM 模型跨方言；Enum 以 `VARCHAR + CHECK` 儲存確保相容 |
| 即時通訊 | **原生 WebSocket**（FastAPI WebSocket + 連線管理器） | 免額外訊息中介，房間成員校驗在握手階段完成 |
| 快取／限流 | **Redis + slowapi** | 敏感 API 每分鐘 5 次；WS 走獨立 token bucket；OTP 與 Token 撤銷清單走 `services/kv.py`（Redis TTL，含記憶體後備與**斷路器**） |
| 儲存 | **本機（開發）／ S3 / Cloudflare R2（生產）** | Pre-signed URL 上傳；伺服器端抹除 EXIF |
| 密碼學 | **Argon2id**、**python-jose (RS256/HS256)**、**Fernet** | 密碼雜湊、JWT 簽章、預留敏感欄位加密 |
| 前端安全 | **DOMPurify**（`isomorphic-dompurify`） | Rich Text 唯一渲染路徑，嚴格 allow-list |

### 2.2 架構圖

```
┌──────────────────────────┐        ┌────────────────────────────────────┐
│  Next.js 14 (port 3000)  │        │  FastAPI (port 8000)               │
│  ├ 頁面: / /login        │  HTTPS │  ├ /api/v1/auth      認證 & Token   │
│  │  /register /trips     │◄──────►│  ├ /api/v1/users     帳號註銷       │
│  │  /trips/[id] /chat    │  WSS   │  ├ /api/v1/profiles  檔案/足跡/封鎖 │
│  │  /profile/[id]        │◄──────►│  ├ /api/v1/trips     行程/申請/推薦 │
│  ├ lib/api.ts  自動刷新  │        │  ├ /api/v1/reviews   評價           │
│  ├ lib/auth.tsx 記憶體   │        │  ├ /api/v1/chat      房間/歷史訊息  │
│  │  Access Token         │        │  ├ /api/v1/reports   檢舉           │
│  ├ Tailwind + Shadcn     │        │  ├ /api/v1/uploads   EXIF 抹除      │
│  └ DOMPurify 淨化渲染    │        │  └ /api/v1/ws/chat/{id} WebSocket   │
└──────────────────────────┘        └───────┬──────────────┬─────────────┘
                                            │              │
                                    ┌───────▼──────┐ ┌───────▼────────┐
                                    │ PostgreSQL 15│ │ Redis 7        │
                                    │ 業務資料      │ │ 限流 / OTP /    │
                                    │              │ │ Token 撤銷清單   │
                                    └──────────────┘ └────────────────┘
                                            │
                                    ┌───────▼──────────────────────┐
                                    │ S3 / R2（圖片）· 本機（開發） │
                                    └──────────────────────────────┘
```

### 2.3 資料模型（15 張表）

| 表 | 用途 | 關鍵設計 |
|----|------|----------|
| `cities` | 城市參考表 | **唯讀參考資料**（只由 `scripts/import_cities.py` 寫入，應用程式永不寫）：GeoNames `cities5000` 的 69,740 列，只保留 19 欄中的 10 欄。`id` 用 GeoNames `geonameid` 而非 UUID（已是穩定唯一鍵，且是回寫原始資料集的 join key）；`asciiname` 帶 `COLLATE NOCASE` 讓前綴搜尋真的走得到 B-tree（實測 1,350 ms → 30 ms）；**唯一**的座標來源，只有市中心一點 |
| `users` | 帳號 | `phone_number` 唯一且為登入識別碼；`is_verified`；`consent_privacy/terms/consent_at` 同意軌跡；`is_anonymized` |
| `profiles` | 公開檔案 | `nickname`、`mbti`、`gender`；`travel_style_tags` / `languages` 用 JSON；**無電話欄位** |
| `travel_histories` | 旅遊足跡 | `budget_type`、`photo_urls`、`is_public` 逐筆控制可見性；`city` 僅為顯示字串，`city_id`（FK，`SET NULL`）才是配對用的可驗證鍵 |
| `trip_posts` | 行程貼文 | 只有 `destination_country/city`，**無精確地址欄位**；`city_id`（FK，`SET NULL`）指向 `cities`，寫入前經 `resolve_city_id()` 驗證；`budget_type`、`target_gender`、`looking_for_count`、`status` |
| `trip_post_tags` | 行程標籤 | **一列一標籤**（複合主鍵 `(trip_post_id, tag)`）取代 JSON 欄位；`ix_trip_post_tags_tag` 讓標籤篩選走索引而非記憶體掃描；標籤一律正規化為大寫、去重 |
| `trip_applications` | 旅伴申請 | 狀態機 `PENDING → ACCEPTED/REJECTED`；`UNIQUE(trip_post_id, applicant_id)` 防重複申請 |
| `chat_rooms` / `chat_room_members` | 聊天室 | 成員表是 WS 授權的**唯一依據**；`room_type` 為 `DIRECT` / `TRIP` |
| `chat_messages` | 訊息 | `sender_id` 為 `SET NULL`，註銷後可安全去識別化 |
| `reviews` | 評價 | `UNIQUE(reviewer_id, reviewee_id, trip_post_id)`；寫入前須通過「共同出遊」檢查 |
| `blocks` | 封鎖 | 唯一約束防重複；查詢時視為**雙向** |
| `reports` | 檢舉 | 狀態：`open` / `reviewing` / `actioned` / `dismissed` |
| `notifications` | 站內通知 | `recipient` 為 `CASCADE`（被遺忘權）、`actor`／`trip_post`／`chat_room` 為 `SET NULL`（不重寫收件人歷史）；索引 `(recipient, read_at, created_at)` 與 `(recipient, chat_room_id)` 去重 |
| `audit_logs` | 稽核軌跡 | **結構性唯讀**：沒有 `updated_at`，也沒有 UPDATE／DELETE 路徑。`actor_profile_id` 為 `SET NULL`（若用 `CASCADE`，註銷權就變成洗白濫用紀錄的手段）、`target_id` 刻意**不是外鍵**（目標常正是被刪掉的那一列）；`detail` 只放 id／enum／數量 |

> **跨方言 Enum**：所有列舉欄位以 `native_enum=False` + `values_callable` + `validate_strings=True`
> 存成 `VARCHAR + CHECK`，因此同一份模型可同時跑 SQLite 與 PostgreSQL。

### 2.4 程式碼規模

```
backend/app/     62 個 Python 檔案（53 個模組 + 9 個 package `__init__.py`）
frontend/src/    38 個 TS/TSX 檔案（11 個頁面 + 8 個元件 + 12 個 UI 元件 + 6 個 lib，含 i18n 字典與 provider）
backend/tests/   344 個測試（整合流程 + 遷移 + 標籤 + Token 撤銷 + KV 容錯 + SMS + 雜湊卸載 + 通知 + 群組房 + 稽核 + 可觀測性 + 評價授權 + 內容審核 + 申請決策 + 上傳與足跡寫入 + 城市查詢 + 城市關聯寫入驗證 + 角色 CLI）
frontend/e2e/    22 個 Playwright 測試（5 個 spec，真實瀏覽器 × 真實後端）
API 端點         52 個 HTTP 端點 + 1 個 WebSocket 端點（另有 `/health` 與 `/metrics`；`/api/v1/cities` 與 `/api/v1/cities/{city_id}` 為本次新增的城市端點）
```

---

## 3. 模組清單

### ✅ 已完成（可直接運行）

**後端 — 核心**
- `core/config.py` — 12-factor 設定；`sync_database_url` 供 Alembic 自動推導
- `core/security.py` — Argon2id、密碼政策、Dual-Token JWT（RS256/HS256）、Token 類型校驗
- `core/deps.py` — `get_current_user` / `get_current_profile` / `require_role`（RBAC）
- `core/rate_limit.py` — slowapi 限流 + 策略常數（登入／註冊／OTP 5 次/分鐘）

**後端 — 資料層**
- `db/session.py` — Async engine、`AsyncSessionLocal`、SQLite 用 `NullPool`；**SQLite 連線時開啟 `PRAGMA foreign_keys=ON`**（否則 `ondelete=` 在 dev/test 方言上形同裝飾）
- `models/*` — 14 張表 + UUID／時間戳／軟刪除 mixin；`lazy="selectin"` / `lazy="raise"` 明確宣告
- `schemas/*` — 對應的 Pydantic v2 模型（`auth` / `profile` / `trip` / `review` / `chat` / `moderation`）

**後端 — API**
- `api/v1/auth.py` — 註冊、登入、刷新、登出、改密碼、`/me`、**OTP 發送與驗證**
- `api/v1/users.py` — 帳號註銷（`hard_delete` / `anonymize`）、我的房間清單
- `api/v1/profiles.py` — 檔案讀寫、足跡 CRUD、上傳 URL、封鎖／解除、封鎖清單、公開檔案（接受 user_id 或 profile_id）
- `api/v1/trips.py` — 行程 CRUD、多條件篩選、推薦、申請與審批（接受後自動開房）
- `api/v1/reviews.py` — 評價建立（共同出遊閘門）+ 列表 + 摘要
- `api/v1/chat.py` — 房間建立／列表、歷史訊息、REST 發訊、安全掃描
- `api/v1/moderation.py` — 檢舉 + 管理員審核佇列
- `api/v1/uploads.py` — 圖片上傳（EXIF 抹除）+ Pre-signed URL
- `api/v1/notifications.py` — 站內通知（列表／未讀數／標已讀／全部已讀／刪除）；**刻意不提供 by-id 查詢**

**後端 — 服務與即時通訊**
- `services/matching.py` — 可解釋推薦引擎（回傳 `score` + `reasons[]`）
- `services/kv.py` — 共用臨時狀態層（Redis → 記憶體 TTL dict 後備）；**斷路器**避免 Redis 故障時每次請求都付連線逾時代價
- `services/otp.py` — 委由 `kv` 儲存、`hmac.compare_digest` 定時比較、成功即消耗
- `services/token_store.py` — Refresh Token 撤銷清單（`jti` deny-list）+ 每用戶 epoch；**洩漏重用偵測**
- `services/sms.py` — 可插拔 OTP 發送（`console` 開發 / `webhook` 生產）；發送失敗永不使請求失敗
- `services/notifications.py` — 通知建立；**永不通知自己、永不跨越封鎖**（在服務層強制，呼叫點不必記得）
- `services/content_filter.py` — 文字審核：**本地高精確度規則**（`BLOCK` / `FLAG`）
  + 可插拔外部供應商；**供應商故障 fail-open**；錯誤訊息不洩漏命中的詞或規則
- `services/audit.py` — 唯讀稽核軌跡的唯一寫入入口；**不 commit**（與呼叫者共用交易）
- `services/pii.py` / `storage.py` / `moderation.py` / `crypto.py`
- `ws/manager.py` + `ws/routes.py` — 連線管理、token bucket、握手鑑權、寫入時重檢封鎖
- `seed.py` — 3 位示範用戶（含 MBTI／標籤／足跡）+ 3 篇行程

**前端**
- 頁面（11）：`/`、`/login`、`/register`（含 OTP 兩步流程）、`/trips`（5 條件篩選 + 推薦 + 分頁）、`/trips/new`、`/trips/[id]`（申請／審批／私訊／封鎖／檢舉／**開行程群組**）、`/profile`（三頁籤：檔案／足跡／帳號私隱）、`/profile/[id]`（公開檔案 + 足跡 + 評價）、`/chat`（房間列表 + WebSocket）、`/notifications`（未讀篩選 + 分頁）、`/admin`（檢舉佇列，角色閘門）
- `lib/api.ts` — 記憶體 Access Token + 401 自動刷新重試 + `errorMessage()` 錯誤攤平
- `lib/auth.tsx` — `AuthProvider` + `RequireAuth` 頁面閘門；靜默登入還原
- `lib/types.ts` / `lib/utils.ts` — 型別與標籤對照表
- `components/ui/*` — 12 個 Shadcn 風格元件（button / card / input / textarea / label / badge / avatar / dialog / select / tabs / separator / popover）
- `components/*` — `NavBar`、`NotificationBell`（30 秒輪詢 + 回焦重同步）、`AvatarUploader`、`TripCard`、`StarRating`、`DisclaimerBanner`、`SafeHtml`
- `e2e/*` — Playwright：`constants`（埠單一來源）、`helpers`（註冊／登入／發文）、`python`（解譯器單一來源）、4 個 spec

**工程**
- `docker-compose.yml`（Postgres + Redis + API + Web）
- 兩份 Dockerfile、兩份 `.env.example`、`.gitignore`
- **Alembic 遷移**（5 支，`alembic check` 回報無漂移）：
  - `c75898845cf5_initial_schema_11_tables.py` — 初始結構（11 張表）
  - `390180f865f7_add_notifications_table.py` — 新增 `notifications`（累計 12 張表 / 24 索引）
  - `cafc59ae9ecc_trip_post_tags_join_table.py` — `trip_posts.tags` JSON → `trip_post_tags` join table；**先回填再刪欄**，升級不丟資料，降級可由 join table 重建 JSON
  - `619d0806a0cd_add_audit_logs_table.py` — 新增 `audit_logs`（累計 13 張表 / 28 索引）；兩個複合索引分別對應「這個帳號做過什麼」與「這週所有封鎖」
  - `2cc02c1a66dd_add_cities_reference_table_and_city_id_.py` — 新增 `cities` 參考表，並在 `trip_posts` / `travel_histories` 加上 `city_id`（FK，`ON DELETE SET NULL`，累計 **15 張表 / 32 索引**）；`SET NULL` 而非 `CASCADE` 是刻意的 —— 重新匯入 GeoNames 會替換所有參考列，`CASCADE` 會連帶刪掉使用者行程
  `init_db()` 偵測到 `alembic_version` 即讓位給 Alembic，生產環境完全跳過 `create_all`
- 測試 344 個：
  - `tests/test_api_flow.py` — 端到端主流程 + 登入反枚舉／時間差 + **行程詳情不得 500**（23）
  - `tests/test_content_filter.py` — 本地規則判定、**合法旅遊文字不得被誤擋**（25 句語料）、零寬／全形繞過、**錯誤訊息不得洩漏命中的詞或規則**、供應商 fail-open／fail-closed、每個寫入端點與 WebSocket（98）
  - `tests/test_cities.py` — 城市建議端點：**前綴比對走 asciiname 而非 name**（帶重音的名稱仍可用純 ASCII 查到）、`LIKE` 特殊字元轉義、最小查詢長度、國家篩選、**席次加成優先於人口**（首都勝過同名前綴的較大城市）、三段席次排序、以及 `city_id` 寫入驗證與 `ON DELETE SET NULL`（49）
  - `tests/test_observability.py` — 遮蔽（含遞迴與子樹整棵遮蔽）、請求 id 的 per-task 隔離、**日誌注入防禦**、指標基數（路由範本而非原始路徑）（38）
  - `tests/test_reviews.py` — **評價必須綁定雙方共同參與的那趟行程**（不可用別的行程 id 重放）、已拒絕／待處理的申請不解鎖評價、同一對旅伴的多趟行程可各評一次、封鎖後評價不可讀（10）
  - `tests/test_manage_roles.py` — 角色 CLI 的**護欄**：撤銷最後一位管理員需 `--force`、已刪除的帳號不得升為管理員、目標資料庫會遮蔽密碼（14）
  - `tests/test_audit.py` — **RBAC 大小寫**、稽核列與狀態變更同一個交易、`detail` 只留 enum／數量、註銷後 `SET NULL`、**唯讀性（結構＋HTTP）**（33）
  - `tests/test_notifications.py` — 通知生命週期、封鎖靜音、跨用戶 404、聊天 upsert（12）
  - `tests/test_upload_and_history_writes.py` — **補上四條此前只走到「提早返回分支」的寫入 happy path**：上傳成功並回傳可用 URL、**EXIF 確實被抹除（對位元組斷言）**、足跡刪除真的刪掉（並驗證他人不可刪、二次刪除回 404）、presign 的後端與 content-type 兩道守衛、以及物件鍵由伺服器產生（11）
  - `tests/test_sms_provider.py` — SMS 供應商切換、失敗處理與生產不外洩（11）
  - `tests/test_migrations.py` — 遷移與模型結構一致性（8）
  - `tests/test_tags.py` — 標籤正規化／精確比對、**超過 1000 筆不再被掃描窗截斷**、更新剪除孤兒列、刪文連帶清除、SQLite 外鍵確實生效（8）
  - `tests/test_token_revocation.py` — 撤銷／輪替／重用偵測（8）
  - `tests/test_password_offload.py` — Argon2 卸載至工作線程、event loop 不被阻塞（7）
  - `tests/test_trip_group_room.py` — 行程群組成員推導、擁有者權限、重複開房（6）
  - `tests/test_kv_resilience.py` — Redis 故障容錯與斷路器（5）
  - `tests/test_applications.py` — 申請決策：只有行程擁有者可接受／拒絕、已決定的申請不得再改、拒絕後可重新申請（3）
- `scripts/manage_roles.py` — **授予／撤銷 ADMIN 的維運 CLI**（`list` / `promote` / `demote`）。
  刻意**不提供 HTTP 端點**：能改角色的端點就是一個等著被設錯的提權路徑。
  這也是「第一個管理員」唯一的建立方式 —— 沒有它，管理介面根本進不去。
  護欄：撤銷最後一位管理員需要 `--force`（否則等於把自己鎖在門外）；
  已刪除的帳號不能升為管理員；每次執行都印出目標資料庫（並遮蔽密碼）。
- `scripts/verify_runtime.py` — 對**真實 uvicorn** 執行的 24 項驗證（時序量測、Cookie 屬性、
  輪替／重用偵測、登出撤銷、OTP、**並發登入下的 event loop 回應性**）
- **`frontend/e2e/` — 15 個 Playwright 測試**，對真實後端與真實瀏覽器執行：
  - `auth.spec.ts`（7）— 註冊 → 讀取畫面回顯的 `dev_code` 完成 OTP → 登入／登出、
    密碼錯誤的通用訊息、重複註冊拒絕、受保護頁面閘門
  - `trip-flow.spec.ts`（2）— **兩個獨立瀏覽器 context** 扮演 Alice 與 Bob：
    發佈 → 申請 → 鈴鐺收到通知 → 接受 → 對方鈴鐺收到決定 → 開行程群組 → 送訊息 → 對方讀到；
    另一條驗證「沒有已接受的旅伴時，行程群組按鈕保持停用」
  - `i18n.spec.ts`（3）— 語言切換：中文 → English → 中文（含 `<html lang>`）、
    選擇在重新載入後保留、未登入也能切換
  - `admin.spec.ts`（3）— 一般使用者看不到管理後台；管理員處理檢舉 →
    **稽核頁籤必須出現 `from=open` / `to=actioned` 的結構化細節**；動作篩選。
    管理員是用 `scripts/manage_roles.py` 現場升出來的（`e2e/helpers.ts` 的
    `promoteToAdmin`），因為沒有 HTTP 端點可以改角色 —— 這也正是這個介面
    過去完全沒有瀏覽器覆蓋的原因

> 這 12 條同時是**預設語言的回歸測試**：它們以中文標籤定位元素，
> 所以字串抽到字典後若有一個字變了，這裡就會紅。實際上就是這套測試抓到
> 「送出申請」被誤接成通用的 `common.submit`（變成「送出」）。

### 🔜 建議補充（依優先級）

| 優先級 | 模組 | 建議做法 |
|--------|------|----------|
| ✅ **P0** | Refresh Token 撤銷清單 | **已完成**：`services/token_store.py` 以 `jti` deny-list + 每用戶 epoch 實作撤銷、輪替與洩漏重用偵測；登出／改密碼即時失效 |
| ✅ **P0** | 首個 Alembic 遷移 | **已完成**：`c75898845cf5_initial_schema_11_tables.py`；並有測試證明其與 `create_all` 結構一致 |
| ✅ **P0** | 真實 SMS gateway | **已完成**：`services/sms.py` 可插拔供應商（`console` / `webhook`）；生產啟動時檢查並警告 |
| ✅ **P0** | 前端 E2E 測試 | **已完成**：Playwright 22 個測試覆蓋「註冊 → OTP → 發文 → 申請 → 接受 → 通知 → 行程群組 → 聊天」，另加語言切換、管理後台與城市選擇器；**上線首跑即抓到一個 500 真 bug**（見下方技術債 #9） |
| ✅ **P1** | 頭像上傳 UI | **已完成**：`components/AvatarUploader.tsx`（hover 上傳、前端先驗證型別／大小再走 multipart） |
| ✅ **P1** | 通知系統 | **已完成**：`notifications` 表 + 5 個端點 + Navbar 鈴鐺（30 秒輪詢 + `window.focus` 重同步）；**封鎖即靜音** |
| ✅ **P1** | 管理後台 | **已完成**：`/admin` 檢舉佇列（角色閘門、狀態篩選、403 視為預期結果） |
| ✅ **P1** | 行程群組聊天室 UI | **已完成**：`/trips/[id]` 的「行程群組」按鈕（需先接受至少一位旅伴）；後端改為由 ACCEPTED 申請推導成員 |
| ✅ **P2** | 標籤改用 join table | **已完成**：`trip_post_tags`（複合主鍵 + `ix_trip_post_tags_tag`）取代 JSON 欄位。篩選改為索引驅動的半連接，**精確且無上限**；標籤統一正規化為大寫並去重。附遷移（先回填再刪欄）與 8 個測試 |
| ✅ **P2** | 內容審核 | **已完成**：`services/content_filter.py` —— 本地高精確度規則（`BLOCK` / `FLAG`）+ 可插拔供應商，接上全部 6 個文字寫入面（含 WebSocket），附 98 個測試 |
| ✅ **P2** | 多語系 i18n | **已完成**：`lib/i18n/` 型別化字典（341 鍵 × 2 語言）+ cookie locale + 語言切換器；**全數 21 個檔案、約 340 行字串已抽離**，`npm run check:i18n` 把「不得再出現硬編碼漢字」與「不得有沒人用的鍵」變成可執行的檢查 |
| ✅ **P2** | 可觀測性 | **已完成**：結構化日誌（雙格式 + 請求關聯 + 統一遮蔽）、`/metrics`（路由範本標籤）、唯讀稽核軌跡表 + `/admin` 稽核頁籤；附 71 個測試 |
| ✅ **P3** | 行程地圖 + 城市參考表 | **已完成**：Leaflet + OpenStreetMap 圖磚；座標來源是 GeoNames `cities5000` 參考表（`city_id` FK），**使用者永遠無法寫入座標**。含城市選擇器、`/cities` 端點、匯入腳本、降級契約與 3 個 E2E。設計見 `docs/ARCHITECTURE.md` §15 |

#### P3 行程地圖：選型與隱私衝突的解法

**採用 Leaflet。** 理由：

1. **規模匹配。** 本功能只需要「在世界地圖上標城市點」——
   Leaflet 的 42 KB、穩定的 API、成熟生態完全夠用，不需要 WebGL 向量渲染。
2. **複雜度更低。** MapLibre 的優勢在向量圖磚、平滑縮放、大量圖層；
   這些在這個用例用不到，卻要付出樣式規格（style JSON）的學習與維護成本。
3. **授權最寬鬆。** Leaflet 為 BSD-2-Clause，可自由商用與修改，無歸屬義務負擔。

**圖磚來源**：用 OpenStreetMap 官方圖磚（遵守 attribution 與使用政策）；
流量上升後換 CDN 或自架，程式碼不需改動（只換 tile URL）。
**不使用天地圖**：其唯一優勢是「中國境內合規」，而本 App 已確認為非境內使用；
加上它是為境內設計的服務（海外存取延遲較高），且免費條款以境內主體為前提，
對境外使用語焉不詳 —— 引入它反而增加合規不確定性。

**原本的阻塞點不是圖層，而是隱私規則 —— 已解決。**

`models/trip.py` 原本只存 `destination_country` / `destination_city` 兩個自由文字
欄位、沒有座標，而寬度粗化正是為了遵守精確位置隱私規則。在圖上標點就得新增經緯度，
等於提高精度。解法是**把座標來源做成參考表，而不是使用者輸入**：

| 問題 | 解法 |
|------|------|
| 精度與隱私規則衝突 | 座標只從 `cities` 參考表取**城市中心**（約公里級）。城市名本身就洩漏同等資訊，故**不增加**可識別性；`docs/SECURITY.md` §2.3 |
| 自由文字城市名的歧義 | 城市改為從 GeoNames 挑選（`city_id` FK），**廢除自由文字寫入路徑** |
| 地理編碼鏈（歧義、查不到、亂填） | **不需要** —— 沒有自由文字，就沒有地理編碼。這是選參考表的主要原因 |
| 配對因拼寫不一致而靜默失敗 | 同上：無法驗證的輸入不能參與逐字比對 |
| 重新匯入 GeoNames 會刪掉使用者資料 | FK 動作為 `ON DELETE SET NULL`，非 `CASCADE` |

`destination_city` 的字串欄位**保留**：它是卡片渲染用的顯示值，且在城市列被移除後仍存活。
`city_id` 則讓城市**可驗證**，也是地圖取得座標的途徑。

### ⚠️ 已知技術債

1. ~~**登入時間差**~~ **已解**：登入改為先判斷帳號存在性，不存在時呼叫
   `verify_password_dummy()` 對固定 dummy hash 執行一次等價 Argon2 驗證，
   再回傳同一個 `401 Invalid credentials`。兩條路徑的回應時間與訊息皆無從區分。
2. ~~**Argon2 阻塞 event loop**~~ **已解**：`hash_password` / `verify_password` 改用
   `anyio.to_thread.run_sync` 移到工作線程（router 一律呼叫 `*_async` 版本）。
   argon2 會釋放 GIL，所以不只是「不阻塞」——並發雜湊真的能並行。
   A/B 實測 12 次雜湊：**內聯 446 ms 且 event loop 完全凍結（ticker 0 次）；
   移出後 235 ms、ticker 14 次**（1.9× 加速）。
   線程池上限由 `PASSWORD_HASH_MAX_CONCURRENCY`（預設 8）控制 ——
   每次雜湊佔用 64 MiB，anyio 預設的 40 條線程可能衝到 2.5 GB。
3. **限流退化為單機**：Redis 不可用時限流自動退回記憶體模式（並記錄警告），
   服務不中斷，但多副本部署下各副本各自計數、限流不再共享。啟動時會以 TCP 探測
   決定儲存後端，執行期若 Redis 出錯則 fail-open（放行請求），避免 Redis 故障擴散為全面 500。
4. **CSP 的 `script-src` 含 `unsafe-inline`（已調查，結論是「暫時無解」，非疏漏）**：
   一度實作 nonce-based CSP 並用真實 production build 驗證，結論如下。
   - **本 App 有 10/12 條路由是 build-time prerender**（`next build` 輸出顯示
     `○ (Static)` 10 條、`ƒ (Dynamic)` 2 條）。**預先渲染的 HTML 無法帶 per-request
     nonce** —— nonce 每次請求都不同，靜態檔案卻是建置時產生一次。
   - 實測後果：動態的 2 條路由正常，**每張靜態頁產生 18 個 CSP violation**，
     且因為加了 nonce 後 `strict-dynamic` 會讓 `'self'` 失效，連**外部 chunk 檔案**
     都被擋掉（不只是 inline script）→ 10 條路由整頁不可用。
   - 因此這是**二選一的架構取捨，不是可以順手修掉的 bug**：要嚴格 `script-src`
     就得放棄靜態預先渲染（改 `export const dynamic = "force-dynamic"`，代價是每條
     路由每次請求都要伺服器渲染），要保留預先渲染就得留 `unsafe-inline`。
     目前選擇保留預先渲染。
   - **防護網**：`npm run check:prod`（`frontend/scripts/check-prod.mjs`）會在
     production build 上實際開瀏覽器載入 7 條路由，斷言安全標頭齊全且
     **CSP violation 為 0**。這類問題只有 production build 看得出來 ——
     `next dev` 每條路由都即時渲染，E2E 全綠也照樣漏掉。
     該腳本已用人工注入的損壞 CSP 反向驗證過「會失敗」（測到 23 個問題、exit 1）。
   - 決定要改動前先讀 `frontend/next.config.js` 上方那段註解。
5. **`services/crypto.py` 無呼叫點**：電話改為登入識別碼後不再是 profile 欄位，此模組目前為預留程式碼。
   其 docstring 原本寫「生產環境請設定 `FIELD_ENCRYPTION_KEY`」—— **但這個設定不存在**，
   照著做不會有任何效果（與技術債 #4 同類的「文件與實作不符」）。
   已改為寫出真實的兩項前置條件：金鑰由 `SECRET_KEY` 衍生（**輪換即讓既有密文無法解開**，
   且 `decrypt_value` 靜默回 `None`），以及 `_fernet` 在 import 時建立。
6. ~~**WS 狀態存於單程序記憶體**：多副本部署需改用 Redis Pub/Sub。~~ **已解**：
   `ws/pubsub.py` 提供 Redis Pub/Sub 跨副本扇出，`ConnectionManager.broadcast` 改為
   「先本地投遞，再發布到房間 channel」。**降級契約**與 `kv.py` 同源：Redis 不可用時
   退回單程序行為（本地照常投遞）並**記錄一次 warning**——因為「只送達一半房間」從送出端
   看起來與成功完全一致，日誌是唯一的外部訊號。
   兩個必須知道的細節：(a) 自有回音以 `instance_id` 過濾（否則每則訊息重複投遞）；
   (b) `exclude` 隨訊息跨副本傳遞，由持有該 socket 的副本套用（否則被排除者會經由
   另一副本收到自己的 typing）。**已釘住**：停用任一守衛 → `tests/test_ws_fanout.py` 變紅（4/4）。
   仍未覆蓋（需真實 Redis 的整合測試）：認證、伺服器端斷線後重連、斷路器對真實不可達主機的行為。
7. **前端未做狀態管理庫**：目前用 React Context，規模變大後建議引入 TanStack Query 處理快取。
8. **雜湊線程池為程序層級**：`PASSWORD_HASH_MAX_CONCURRENCY` 是**每程序**上限，
   多副本部署時總並發為「副本數 × 上限」，需據此調整記憶體規劃。
9. ~~**`GET /trips/{id}` 一律回 500**~~ **已解（由 E2E 首跑發現）**：
   `TripPost.applications` 是 `lazy="raise"`，而 `db.get()` 不會 eager-load 它，
   所以 `TripPostDetail.model_validate(post)` 直接拋 `InvalidRequestError` ——
   **每一位檢視者（包括行程擁有者自己）都拿到 500**，前端只顯示「找不到此行程」。
   單元測試之所以漏掉：唯一覆蓋此端點的案例是「被封鎖者 → 404」，
   而那條分支在驗證之前就 `return` 了，happy path 從未被真正執行。
  修法：改由 `TripPostOut` 組出 detail，再只為擁有者補上申請清單 ——
  這也讓「申請清單不公開」成為**結構性**保證（`applications` 一律從空的開始），
  而不是仰賴呼叫點記得清空。已補上 `test_trip_detail_readable_and_hides_applications`。
10. ~~**SQLite 上所有 `ondelete=` 規則形同裝飾**~~ **已解**：SQLite 預設
   `PRAGMA foreign_keys=OFF`，而 `ondelete=` 無法從連線字串開啟 —— 於是
   「刪行程貼文」在 dev/test 方言上不會連帶刪除申請與通知，在 PostgreSQL
   （生產）卻會。同一個操作在兩個方言上語意不同，正是技術債 #9 那類 bug 的溫床。
   修法：`db/session.py` 在 SQLite 連線上 `PRAGMA foreign_keys=ON`（僅 app engine；
   Alembic 走自己的連線，batch 重建資料表時必須保持外鍵關閉）。
   `test_deleting_a_trip_cascades_to_applications` 以 `lazy="raise"` +
   `passive_deletes=True` 的關係作為最銳利的探針 —— ORM 永遠不會載入、因此永遠不會
   自行刪除子列，唯一能刪掉那一列的就是資料庫的 cascade。
11. ~~**標籤篩選曾有隱形上限**~~ **已解**：`tags` 存 JSON 時無法跨方言做包含查詢，
   舊寫法是撈最多 1000 筆到記憶體比對 —— 超過就**默默漏掉**，且 `total` 只是
   掃描窗內的數量。已改為 `trip_post_tags` join table（見 §2.3）。

12. ~~**整個管理後台對所有人回 403（包括管理員）**~~ **已解（寫稽核測試時發現）**：
   `require_role("admin")` 比對的是 `user.role not in roles`，而 `UserRole.ADMIN`
   這個 `str` Enum 的值是 **`"ADMIN"`** —— 小寫的 `"admin"` 永遠不相等。
   三個管理端點（檢舉佇列、狀態變更、稽核查詢）因此全部對**每一個人**回 403。
   之所以長期沒被發現：回的是 403 而不是 500，前端把 403 當成預期結果處理，
   而整個測試套件裡**沒有任何一個管理員測試**（沒有 API 能授予角色，
   所以也沒人想過要建 fixture）。
   修法：`require_role` 兩邊都正規化為大寫，且**未知角色名稱在建立依賴時就拋錯**
   ——`require_role("admni")` 同樣是靜默全面鎖死，讓它在啟動時就炸掉，
   比讓管理員安靜地被擋在門外好。已補 `admin_user` fixture 與 3 個 RBAC 測試。
13. ~~**檢舉的稽核紀錄悄悄丟失「檢舉原因」**~~ **已解（測試斷言 `detail` 時發現）**：
   `moderation.create_report` 寫入 `detail={"reason": payload.reason}`，
   但 `services/audit.py` 的敏感鍵提示清單裡有 `"reason"` —— 遮蔽器以**鍵名**
   判斷，於是把它整個丟掉，每一筆 `REPORT_SUBMITTED` 都失去了唯一的上下文。
   而真正該防的免費文字（`Report.detail`，上限 2000 字）**本來就沒有被複製進來**。
   兩個問題一起修：把 `"reason"` 從提示清單移除（`ReportCreate._valid_reason`
   已把它限制為 5 個值的封閉 enum，不是散文），並讓**丟棄鍵時寫一筆 warning**
   —— 會靜默失敗的警報器不算警報器。已補 3 個測試。
14. ~~**`enum_col` 的 `length=20` 是顆定時炸彈**~~ **已解**：長度寫死 20，
   而新增的 `REPORT_STATUS_CHANGED` 是 21 個字元 —— SQLAlchemy 在
   **mapper 設定階段**就拋 `ValueError: length must be larger or equal than the
   length of the longest enum value. 20 < 21`，表徵是「應用程式無法 import」。
   修法：長度改由最長成員推導（`max(20, longest)`，地板值確保既有欄位寬度不變，
   並以 `alembic check` 驗證無漂移）。
15. ~~**評價授權只看「這一對人」而不是「這一趟行程」**~~ **已解**：
   `_has_travelled_together(a, b)` 回答的是「這兩個人有沒有一起出遊過」——
   是**配對層級**的是非題；但去重約束是 `(reviewer_id, reviewee_id, trip_post_id)`，
   而 `trip_post_id` 由**客戶端提供且從未驗證**。於是只要有一趟共同行程，
   就能靠**每次換一個行程 id**（包括自己隨便開的行程）無限刷評價，
   每一筆都掛在雙方根本沒去過的行程上 —— 正是這段程式碼註解說要防的刷分。
   原有測試沒抓到：它每次都重用**同一個** `trip_post_id`，而那條路徑確實會被去重擋下。
   修法：改為 `_shared_trip_ids()` 回傳**共同行程 id 集合**，並要求 `trip_post_id`
   必須在其中（`trip_post_id` 為 `NULL` 時另外以 `IS NULL` 去重 ——
   SQL 的 NULL 互不相等，資料庫唯一約束在那條路徑上完全幫不上忙）。
   已補 10 個測試，並以「還原修正 → 測試必須失敗」驗證其有效性。
16. ~~**重複封鎖會多寫一筆 `USER_BLOCKED` 稽核列**~~ **已解**：
   `moderation.block_profile` 是幂等的（已存在就回傳既有列、不新增），
   但 router 無條件寫稽核 —— 於是「封鎖一個已經封鎖的人」會在軌跡裡
   留下第二筆**從未發生**的事件。解除封鎖那半邊本來就有 `if removed:` 守衛，
   兩邊互相矛盾。修法：`block_profile` 回傳 `(block, created)`，只在 `created` 時記錄。
17. ~~**封鎖後仍可讀取對方的評價**~~ **已解**：`GET /profiles/{id}` 與 `/histories`
   對被封鎖者回 404（§1.3 不洩漏存在性），但 `/profiles/{id}/reviews` 與
   `/reviews/summary` 沒有做同樣檢查，回 200 附帶資料 —— 被封鎖者因此仍能
   確認封鎖者的存在。順帶修好同前綴兩端點對 id 的解讀不一致
   （一個接受 profile id 或 user id，另一個只接受 profile id）。

18. ~~**`useCallback` 抓到舊語言的 `t`**~~ **已解（導入 ESLint 後發現）**：
   抽取字串時把 `t(...)` 加進了既有 `useCallback` 的函式體，但沒有加進依賴陣列。
   `t` 是**跟著 locale 改變的**（`useMemo` 依賴 `locale`），所以切換語言後，
   那些 callback 仍握著舊的 `t` —— 之後才產生的錯誤訊息會是**上一種語言**。
   型別完全正確，只有 `react-hooks/exhaustive-deps` 看得出來。9 處全部修好；
   其中 chat 的 **WebSocket effect 例外**：把 `t` 加進依賴會在每次切語言時
   拆掉重連 socket，因此改用 ref 讀取（永遠最新，且不重新訂閱）。

19. ~~**`tripmate_rate_limit_tripped_total` 永遠是 0**~~ **已解（Deep Check 時發現）**：
   指標在 `core/metrics.py` 宣告、也在 `/metrics` 上暴露，但 `observe_rate_limit_trip()`
   **從來沒有任何呼叫點** —— 於是它永遠讀到 0。一個永遠為零的計數器**比沒有更糟**：
   在憑證填充攻擊期間，儀表板會顯示「沒有任何請求被限流」，而那是主動誤導。
   修法：包住 slowapi 的 `RateLimitExceeded` handler，在轉交前先計數。
   測試是**結構性**的（斷言註冊的不是 slowapi 的預設 handler），因為要真的執行它
   需要一個真實的 `Limit` 物件與填好的 `request.state`。
20. ~~**申請決策可以被覆寫，且 `looking_for_count` 從未被強制**~~ **已解**：
   `PATCH /trips/applications/{id}` 唯一的寫入動作是 `application.status = decision` ——
   **沒有任何狀態檢查**。兩個後果，都是一般客戶端就能觸發的：
   - **決策不是最終的**：對一個已 `REJECTED` 的申請再送 `decision=ACCEPTED`，
     會回 **200 ACCEPTED** 把它翻過來，而被拒絕的申請人**同時收到**
     `APPLICATION_REJECTED` 與 `APPLICATION_ACCEPTED` 兩則互相矛盾的站內通知。
   - **`looking_for_count` 形同裝飾**：它在 schema 上有驗證（`ge=1, le=20`）、
     也有落庫，但**沒有任何程式碼把它讀回來**，所以建立者可以在宣稱徵 1 人的行程上
     接受任意數量的旅伴。
   為何既有測試沒抓到：它確實驗證了「非建立者 → 404」，但**那條分支在寫入之前就
   return 了**，所以那段變更從未被執行第二次。
   修法：已結案的申請**不得改變決策**（同一個決策重送視為幂等、回 200 且不重複通知；
   不同決策回 **409**），並在接受前檢查 `already_accepted >= looking_for_count`（回 409）。
   容量檢查刻意放在變更狀態**之前**，這樣被拒絕的接受不會留下部分寫入。
   新增 `tests/test_applications.py`（3 個測試），並以「停用守衛 → 2 個測試必須失敗」
   反向驗證過測試真的抓得住。
21. **四個寫入端點只被「提早返回分支」覆蓋（已補測試）**：這一項不是單一 bug，
   而是本專案**反覆出現的同一種測試盲區**（#9、#20 都源於此）：
   > 「唯一覆蓋該端點的測試」走的是在**寫入之前就 return** 的分支，
   > 所以那段變更從未被任何測試執行過。端點可以完全壞掉而測試全綠。

   本次逐條核對 29 個寫入端點，找出四個真正的盲點：
   | 端點 | 原本的覆蓋 |
   |------|-----------|
   | `POST /uploads/image`（**成功路徑**） | 唯一測試送 SVG 斷言 `400` → 只執行到 `sniff_mime`，`store_image`／重編碼／`_put_local` 全未執行 |
   | `DELETE /profiles/me/histories/{id}` | 零覆蓋 |
   | `POST /profiles/me/upload-url` | 零覆蓋 |
   | `POST /uploads/presign` | 零覆蓋 |

   已補 `tests/test_upload_and_history_writes.py`（11 個測試），每一條都**斷言成功狀態
   ＋可觀察的寫入效果**（重新讀取、或對**位元組**斷言）。其中 EXIF 那條特別值得記：
   `storage.py` 的 docstring 宣稱「重編碼會抹除全部 metadata（含 EXIF GPS）」，
   但**從來沒有任何測試驗證過** —— 而且 `201` 本身無法區分「已抹除」與「原樣存回」。
   所以測試直接**從磁碟找出該物件並解析其 EXIF**；同時 assert 它仍是一張可解碼的圖，
   否則「沒有 EXIF」也可以靠「檔案被截斷成零位元組」而通過。

   **反向驗證**：停用四道守衛（足跡的擁有權檢查、二次刪除的 404、content-type 白名單、
   EXIF 清除）→ **恰好這 4 條測試失敗**，其餘 7 條通過；還原後 11 條全綠。

   順帶查出的一個**結構性事實**：`presign_put` 的兩道守衛是
   `STORAGE_BACKEND != "s3"` **先**、`content_type not in ALLOWED_MIME` **後**。
   因此在 local 後端下（dev／test 的預設）**content-type 白名單在 HTTP 層完全不可達** ——
   刪掉它不會有任何測試變紅。而它正是生產環境上**唯一**阻止 `application/pdf`
   之類的型別被存入上傳桶的檢查。測試因此改為**直接呼叫 service 函式**把這道守衛釘住，
   HTTP 層則只斷言「local 後端下一律是那個 400」。

22. **多副本部署前置清單（已全部結案）**：以下問題**同源** —— 全都來自
   「一個**每行程**各算一次的量測，被當成整個部署的事實」。單副本時兩者完全等價，
   這正是它們長期只被記錄、沒有被修的原因。一旦水平擴展（K8s、多台 VM、或僅是
   `uvicorn --workers N`），**全部會同時觸發**。

   | 項目 | 現狀 | 多副本下的表徵 |
   |------|------|--------------|
   | **#6 WS 狀態** | ✅ **已解**：`ws/pubsub.py` Redis Pub/Sub 扇出 + 每人配額外部化 + presence 註冊表 | 聊天訊息單向丟失且不報錯 → **已修**。`_buckets` → `kv.DistributedTokenBucket`（跨副本共享）。**presence** → `kv.PresenceRegistry`（每副本一個 TTL 條目，讀取取聯集；心跳重寫名單，所以漏掉的 `leave` 會自我修復）。`is_member_online` **刻意保持本副本語意**（它回答「我能不能投遞」，是路由問題） |
   | **#3 限流** | ✅ **已解**：`rate_limit.SharedCounter` 走同步 redis client + 斷路器 | 實際限額 = 副本數 × 限額 → **已修**：Redis 健康時配額全域共享。Redis 不可用時**仍降級為每副本**（刻意，可用性優先），但會告警並說明配額被放大 |
   | **#8 雜湊池** | ✅ **已解**：`kv.DistributedSemaphore` 取全域 permit；anyio 線程池降為**每行程後備** | 總並發 = 副本數 × 8；Argon2 每次約 64 MiB → 4 副本峰值約 2 GB，易被 OOM kill → **已修**。取不到 permit 時**等待 2.5 秒再放行**，絕不因忙碌而拒絕登入；Redis 不可用時啟動即**以記憶體算術告警** |
   | **#21 TOCTOU** | ✅ **已解**：狀態轉移改**條件式 UPDATE**（`rowcount` 仲裁）＋ 讀取前先取**寫鎖**（PG：`FOR UPDATE`；SQLite：對該列的 no-op 寫入） | 兩個併發接受可雙雙通過容量檢查 → **安靜超收** → **已修**。**實測**：只留 CAS 而移除寫鎖 → 兩個並行接受都回 200、`looking_for_count=1` 的行程有 2 位旅伴；補上寫鎖 → `[200, 409]`、1 位 |
   | **#22a KV 斷路器** | ✅ **已解**：狀態移入 `kv._BreakerState`；`RedisBreakerBackend` 發布窗口 | 故障期間每個副本各吃一輪失敗請求 → **已修**。**刻意仍是 publisher 而非依賴源** —— 讀取失敗一律退回本地，否則「需要 Redis 才知道 Redis 掛了」 |
   | **#22b `app.seed`** | ✅ **已解**：seed 全程包在 `pg_advisory_lock` 內 | 多副本同時 seed → 重複種子資料或競態。逐列存在性檢查**只對循序重跑有效**，併發時大家都在任何 commit 之前讀到「不存在」 |

   **特別注意**：很多人以為「還沒上 K8s，只是加幾個 `--workers`」很安全 ——
   **不對**。`uvicorn --workers 4` 就是多行程，**#6 已經觸發**。

   建議順序：~~#6~~ → ~~#3~~ → ~~#8~~ → ~~#21~~ → ~~#22a~~ → ~~#22b~~（**全部完成**）。
   負向驗證：多副本硬化 9 個突變全紅（`tests/test_multi_replica_hardening.py`）＋
   presence 10 個突變全紅（`tests/test_presence_registry.py`）。
   **#6 的最後一項 presence 已於 2026-09-27 修完**，多副本前置清單全清。

   > ⚠️ **這一輪最貴的一課**：**「#21 修好了」在單執行緒測試下無法證明。**
   > 第一版修法（只在 PG 加 `FOR UPDATE` ＋ CAS）在既有測試與新增的循序測試下**全綠**，
   > 但用 `httpx.ASGITransport` 真的併發送出兩個接受請求時，**兩個都回 200、超收 2 位**。
   > **對賽跑，延遲與真正併發才是讓突變可觀測的東西**；循序測試只證明「檢查還在」，
   > 不證明「檢查有效」。修好後的測試同時斷言**回應狀態**與**落地狀態**兩側。

---

## 4. 使用流程與主要使用情境

### 4.1 主流程（Happy Path）

```
① 註冊 ──► ② 手機驗證 ──► ③ 完善檔案 ──► ④ 瀏覽／發佈行程
   │            │                │                  │
 勾選私隱同意  輸入 SMS 驗證碼    填 MBTI/標籤/足跡   系統依足跡推薦
                                                       │
                        ┌──────────────────────────────┘
                        ▼
              ⑤ 申請／被申請 ──► ⑥ 自動開聊天室 ──► ⑦ 結伴出發 ──► ⑧ 互相評價
```

**逐步說明**

| 步驟 | 使用者動作 | 系統行為 |
|------|-----------|----------|
| ① 註冊 | 填手機號／密碼／暱稱，勾選兩項同意 | 驗證手機格式與密碼強度 → Argon2id 雜湊 → 建立 User + Profile → 發 Access Token（Body）+ Refresh Token（HttpOnly Cookie） |
| ② 手機驗證 | 取得 OTP 並輸入 | `otp.issue_otp()` 產生 6 位碼存 Redis（TTL 5 分鐘）→ 驗證以定時比較、成功即消耗 → `is_verified=true` |
| ③ 完善檔案 | 填簡介、MBTI、性別、風格標籤、語言、旅遊足跡 | 足跡成為配對引擎的輸入；可逐筆設定公開或私人 |
| ④ 發佈行程 | 填標題、說明、目的地、日期、預算、標籤、人數 | 只儲存國家／城市；寫入 `trip_posts` |
| ⑤ 申請 | 對他人行程送出申請訊息 | 檢查封鎖關係 → 建立 `trip_applications`（唯一約束防重複） |
| ⑥ 審批與對話 | 作者接受 → 雙方進入聊天室 | `_ensure_direct_room()` 自動建立 direct room + 兩筆成員 → WebSocket 連線（握手驗 token + 驗成員）→ 訊息限速 2 msg/s |
| ⑦ 風險控制 | 感到不適 → 封鎖／檢舉 | 封鎖即時生效：對方無法搜尋、私訊、申請、開房（含已開啟的 WS 連線） |
| ⑧ 互相評價 | 行程結束後撰寫評價 | 系統驗證雙方確實共同出遊過才允許評分 |

### 4.2 主要使用情境

**情境 A：攝影愛好者找同好**
Alice 上傳東京賞櫻足跡（風格：PHOTOGRAPHY）→ 發佈「東京櫻花季攝影之旅」→ 系統對其他用戶推薦該行程並標示理由「你曾到訪 Tokyo」「共同旅遊風格：PHOTOGRAPHY」→ Bob 申請 → Alice 接受 → 自動開聊天室討論器材與行程 → 行程結束後互相給予 5 星評價。

**情境 B：安全地拒絕騷擾**
Carla 收到不受歡迎的私訊 → 點「封鎖」→ 後端 `POST /profiles/{id}/block` → 該用戶對 Carla 的檔案與行程一律回 `404`（不洩漏存在性）→ 對方亦無法再建立聊天室或申請行程，**即使是已開啟的 WebSocket 連線也會在下次寫入時被拒絕**。

**情境 C：行使私隱刪除權**
使用者決定退出 → 個人檔案頁選「匿名化」並輸入密碼 → 後端清除暱稱、簡介、MBTI、性別、標籤、語言、足跡摘要與相片、訊息內容、行程描述、申請訊息，並將電話換為 ghost 號、`is_active=False` → 帳號立即停用，但既有對話結構保留。

**情境 D：聊天中的防詐騙提示**
使用者在聊天室輸入疑似電話號碼 → 後端 `is_phone_like()` 偵測 → 回傳 `safety_hint` → 前端提示「為保障私隱，請避免在建立信任前交換聯絡方式或進行金錢交易」。

**情境 E：管理者處理檢舉**
使用者檢舉 → 寫入 `reports`（狀態 open）→ 管理員 `GET /admin/reports?status_filter=open` 查看 → `PATCH` 更新為 `actioned` 或 `dismissed`。

---

## 5. 快速開始

### 方式一：Docker（最省事）

```bash
docker compose up --build
# 前端 http://localhost:3000    後端 http://localhost:8000/docs
# 示範帳號：+85290000001 / Passw0rd123
```

> **`.dockerignore` 是必要的，不是可選的。** 兩個 Dockerfile 都用了 `COPY . .`，
> 所以被建置上下文帶進去的東西就是會被寫進映像檔的東西：
> - 一個真實的 `.env` 會被**烤進映像層**。之後用 `RUN rm` 刪掉沒用 ——
>   `docker history` 依然看得到每一層。**`.gitignore` 保護 repo，但不保護映像檔。**
> - 本機的 `node_modules/`（實測 ~638 MB）與 `.next/`（~176 MB）會被複製進去，
>   再被映像檔自己的安裝蓋掉 —— 既慢又會製造「我這邊可以跑」的分歧。
>
> `backend/.dockerignore` 與 `frontend/.dockerignore` 已就位，且
> **`!.env.example` 的例外有保留**（範本要進映像檔，密鑰不要）。

### 方式二：本機執行

**後端**

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate      # Windows
pip install -r requirements.txt
cp .env.example .env                                  # 依需求修改
# 最快路徑：把 DATABASE_URL 設為 sqlite+aiosqlite:///./tripmate.db 即可免裝 Postgres
python -m app.seed
uvicorn app.main:app --reload --port 8000
```

> ⚠️ `DATABASE_URL` **必須使用 async driver**：`postgresql+asyncpg://…` 或 `sqlite+aiosqlite:///…`。
> Alembic 會自動由它推導出同步 URL。

**前端**

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

**測試**

```bash
cd backend && pytest -q          # 469 passed
cd frontend && npm run typecheck # 0 errors
cd frontend && npm run lint      # 0 warnings（next/core-web-vitals）
cd frontend && npm run check:i18n # 字典同步、無硬編碼字串
cd frontend && npm run build     # 12 routes（10 static + 2 dynamic）
cd frontend && npm run check:prod # production build 實測：標頭齊全、CSP violation 為 0
cd frontend && npm run test:e2e  # 22 passed (Playwright，自行啟動前後端)
```

`check:prod` 需要先跑過 `npm run build`（它自己起停 `next start`，不負責建置）。
**注意執行順序**：`next dev` 會用自己的 `.next` 蓋掉 production build，
所以 `build` 之後就不能再跑 dev server。腳本會先檢查 `.next/BUILD_ID`
是否存在，缺少時直接指名原因（而不是等 60 秒後說「連不上伺服器」——
那會讓人往埠與 proxy 的方向查，但問題其實是產物不見了）。

存在的理由是：**`next dev` 每條路由即時渲染，E2E 全綠也看不出 prerender 專屬的缺陷** ——
nonce CSP 就是這樣在 E2E 通過的情況下弄壞 10 條路由（見技術債 #4）。

**E2E 測試（`frontend/e2e/`）**

`playwright.config.ts` 會自行啟動兩個伺服器，不需事先開好服務：

| 服務 | 位址 | 設定 |
|------|------|------|
| 後端 | `127.0.0.1:8099` | 一次性 `e2e.db`、`RATE_LIMIT_ENABLED=false`、`OTP_DEV_ECHO=true`、CORS 對齊前端 |
| 前端 | `127.0.0.1:3099` | `NEXT_PUBLIC_API_URL` 指向上述後端 |

刻意使用非預設埠：3000 / 8000 是開發者最可能已經開著的埠，而「安靜地測到錯的後端」比埠衝突更糟。

兩點在 CI 之外容易踩到：

1. **Python 解譯器**：`python` 未必是有裝 fastapi／uvicorn 的那個。設定會依序找
   `E2E_PYTHON` 環境變數 → `backend/.venv` → 專案根 `.venv` → `python`。
   若你的虛擬環境在別處，用 `E2E_PYTHON=/path/to/python npm run test:e2e`。
2. **不要讓 Playwright 代管伺服器**：若環境有本機 proxy／沙箱會攔截 localhost
   （Playwright 的 readiness 探測拿不到 2xx，會以 `Timed out waiting 180000ms
   from config.webServer` 收場），請自己開好兩個服務後執行：

```bash
# 後端（記得 BACKEND_CORS_ORIGINS 要含前端來源，否則瀏覽器的 preflight 會 400）
cd backend && DATABASE_URL="sqlite+aiosqlite:///./e2e.db" RATE_LIMIT_ENABLED=false \
  OTP_DEV_ECHO=true BACKEND_CORS_ORIGINS="http://127.0.0.1:3099" \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8099

# 前端（-H 127.0.0.1 是必要的，見下）
cd frontend && NEXT_PUBLIC_API_URL=http://127.0.0.1:8099 \
  NEXT_PUBLIC_WS_URL=ws://127.0.0.1:8099 npx next dev -H 127.0.0.1 -p 3099

# 然後
cd frontend && E2E_NO_WEBSERVER=1 npm run test:e2e
```

> `next dev -H 127.0.0.1` 是必要的：`next dev -p <port>` 會綁到 `localhost`，
> 在雙棧機器上解析為 IPv6 `::1`，而探測用的是 IPv4 字面值，兩者永遠對不上。

### 已驗證項目

| 檢查 | 指令 | 結果 |
|------|------|------|
| 後端測試 | `pytest -q` | **469 passed**（300 秒；26 個 spec 檔） |
| 前端型別檢查 | `npm run typecheck` | **0 errors** |
| i18n 完整性 | `npm run check:i18n` | **359 鍵、zh-HK 與 en 完全同步、字典外零漢字、且無未被引用的鍵** |
| 前端靜態分析 | `npm run lint` | **0 warnings**（`next/core-web-vitals`；首次導入即抓到 9 個 `react-hooks/exhaustive-deps`，見技術債 #18） |
| 前端生產建置 | `npm run build` | **✓ 12 routes**（10 靜態預渲染 + 2 動態） |
| production 標頭與 CSP | `npm run check:prod` | **7 條路由全 200、6 個安全標頭齊備、CSP violation 0**（在真實 production build 上開 Chromium；已用注入的損壞 CSP 反向驗證會失敗） |
| 前端 E2E | `npm run test:e2e` | **22 passed**（真實 Chromium × 真實 uvicorn；含雙人配對全流程、管理後台、城市選擇器與稽核清單亂序防護） |
| 路由註冊 | `app.routes` | 52 個 HTTP（`/api/v1`）+ 1 個 WebSocket + `/health` |
| 稽核軌跡唯讀 | 結構掃描 + HTTP | 無任何 `POST/PUT/PATCH/DELETE` 路由指向 `/admin/audit-logs`；模型亦無 `updated_at` 欄位 |
| 日誌注入防禦 | `X-Request-ID` 夾帶換行 | 非 `[A-Za-z0-9._-]` 一律拒絕並改發新 id；偽造字串不出現在回應標頭或任何日誌行 |
| 指標基數 | `/api/v1/trips/<uuid>` | 標籤為 `/api/v1/trips/{trip_id}`；回應內**不含**任何真實 id |
| RBAC | 管理員 vs 一般使用者 | 管理員 200、一般使用者 403、匿名 401；`require_role("admni")` 於啟動時拋 `ValueError` |
| 示範資料 | `python -m app.seed` | 3 用戶 / 3 行程 / 足跡 / MBTI |
| 安全標頭 | 任意回應 | `CSP`、`X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy`、`Permissions-Policy` 齊備 |
| WebSocket 鑑權 | 錯誤 token / 非成員 | 握手階段拒絕（真實客戶端收到 **HTTP 403**；in-process 測試客戶端收到 `1008`） |
| WebSocket 限速 | 連續 5 則訊息 | 第 3 則起回 `rate_limited` |
| 上傳防護 | 上傳非圖片 | 回 `400`（magic bytes 不符） |
| 限流生效 | 連續 6 次登入 | 第 6 次回 `429 Rate limit exceeded: 5 per 1 minute` |
| Redis 容錯 | 關閉 Redis 後登入 | 自動退回記憶體限流，登入仍 `200`（不影響可用性） |
| 登入時間差防護 | 不存在帳號 vs 密碼錯誤（真實伺服器各 7 次取中位數） | **38 ms vs 36 ms（1.05×）** —— 兩者皆執行一次 Argon2 驗證（dummy hash），時間無從區分 |
| Event loop 不被雜湊阻塞 | 12 個並發登入，同時量測 `/health` | 並發登入 266 ms 完成（序列化需 ~420 ms）；`/health` p95 **39 ms**、最低 3 ms |
| Argon2 卸載 A/B | 12 次雜湊：內聯 vs 工作線程 | 內聯 **446 ms 且 ticker 0 次**（loop 完全凍結）→ 卸載後 **235 ms、ticker 14 次**（1.9×） |
| 生產不外洩 OTP | `ENV=production` 且 `OTP_DEV_ECHO=true` | 回應 `dev_code` 為 `null`（設定被強制忽略） |
| 遷移一致性 | `alembic upgrade head` vs `create_all` | 以 SQLite PRAGMA 逐欄比對，**結構完全相同**（14 張表 / 28 索引） |
| 遷移無漂移 | `alembic check` | `No new upgrade operations detected` |
| 遷移可回滾 | `alembic downgrade base` | 僅剩 `alembic_version` |
| 標籤遷移不丟資料 | 於舊 revision 塞入 `["photography","FOOD","Photography"]` → `upgrade head` | 正規化為**單一** `PHOTOGRAPHY` 列；`downgrade` 後 JSON 還原為 `["FOOD","PHOTOGRAPHY"]` |
| 標籤篩選無上限 | 1005 筆同標籤貼文 | `total` 為 **1005**（舊寫法為 1000）；`EXPLAIN QUERY PLAN` 顯示走 `ix_trip_post_tags_tag` |
| Token 撤銷 | 登出後重放 Cookie | `401`（撤銷清單生效） |
| Token 輪替 | 舊 refresh 再用 | `401`，且**同一 token family 全部失效**（重用偵測） |
| 改密碼登出所有裝置 | 改密碼後用舊 refresh | `401`（epoch 提升） |
| 端到端流程 | 建行程 → 申請 → 接受 → 開房 → 聊天 → WS 廣播 | 19 項檢查全部通過 |

測試涵蓋：健康檢查、安全標頭、同意驗證、弱密碼拒絕、手機格式驗證、登入反枚舉
（含**時間差**防護）、OTP 流程、檔案更新與 MBTI 驗證、足跡私隱、雙 ID 解析、
行程建立與多條件篩選、申請／重複申請／封鎖流程、接受後自動開房、推薦排序、
評價共同出遊閘門、帳號匿名化註銷、非圖片上傳拒絕、
WebSocket 握手鑑權／廣播／速率限制、Alembic 遷移結構一致性與可回滾性、
Refresh Token 撤銷／輪替／重用偵測、KV 斷路器容錯、SMS 供應商切換與失敗處理。

---

## 6. 專案結構

```
trip-mate/
├── README.md                    ← 你正在閱讀的文件
├── docker-compose.yml
├── docs/
│   ├── ARCHITECTURE.md          模組設計、資料流、async ORM 規範
│   ├── SECURITY.md              規格條文 ↔ 實作對照表
│   ├── STATUS.md                專案狀態報告（進度／設計／問題）
│   └── API.md                   端點清單（50 HTTP + 1 WS；另有 /health）
├── backend/
│   ├── Dockerfile  alembic.ini  requirements.txt  .env.example
│   ├── .dockerignore            避免密鑰與 venv 進入映像檔
│   ├── alembic/                 資料庫遷移
│   ├── app/
│   │   ├── main.py              應用入口（安全標頭、CORS、限流、lifespan）
│   │   ├── seed.py              示範資料
│   │   ├── core/                config · security · deps · rate_limit
│   │   │                        logging · metrics · middleware · request_meta
│   │   ├── db/                  base · session
│   │   ├── models/              enums · user · profile · trip · chat · review
│   │   │                        moderation · notification · audit
│   │   ├── schemas/             對應的 Pydantic 模型
│   │   ├── api/v1/              auth · users · profiles · trips · reviews · chat
│   │   │                        moderation · uploads · notifications
│   │   ├── services/            matching · kv · otp · sms · token_store · pii
│   │   │                        crypto · storage · moderation · notifications
│   │   │                        audit · content_filter
│   │   └── ws/                  manager · routes
│   └── tests/                   conftest · test_api_flow · test_migrations
│                                test_tags · test_token_revocation
│                                test_kv_resilience · test_sms_provider
│                                test_password_offload · test_notifications
│                                test_trip_group_room · test_audit
│                                test_observability · test_reviews
│                                test_content_filter · test_manage_roles
│                                test_applications · test_upload_and_history_writes
└── frontend/
    ├── Dockerfile  .dockerignore  next.config.js  tsconfig.json
    ├── tailwind.config.ts  postcss.config.js  package.json  .env.local.example
    ├── playwright.config.ts
    ├── scripts/check-i18n.mjs      字典同步 + 禁止硬編碼漢字
    ├── scripts/check-prod.mjs      production build 實測（標頭 + CSP violation 為 0）
    ├── e2e/                     constants · helpers · python · auth.spec
    │                            trip-flow.spec · i18n.spec · admin.spec
    └── src/
        ├── app/                 11 個頁面 + layout + globals.css
        ├── components/          NavBar · NotificationBell · AvatarUploader · TripCard
        │                        StarRating · DisclaimerBanner · SafeHtml
        │   └── ui/              12 個 Shadcn 風格元件
        └── lib/                 api.ts · auth.tsx · types.ts · utils.ts
```

---

## 授權與合規

- 本專案為示範實作，部署至生產環境前請務必：更換 `SECRET_KEY`、啟用 RS256、開啟 `COOKIE_SECURE`、
  設定真實 CORS 來源、關閉 `OTP_DEV_ECHO`、接上真實 SMS gateway、完成法律文件審閱。
- 平台僅提供資訊媒合服務，不承擔線下見面與旅遊期間之人身、財物及消費糾紛責任。
