# Trip Mate — Architecture

## 1. 分層設計

```
┌─────────────────────────────────────────────────────────────┐
│ Presentation      Next.js 14 App Router 頁面 + Shadcn 元件    │
├─────────────────────────────────────────────────────────────┤
│ Client State      lib/api.ts（Token/重試）· lib/auth.tsx     │
├─────────────────────────────────────────────────────────────┤
│ Transport         REST /api/v1/*  +  WSS /api/v1/ws/chat/*   │
├─────────────────────────────────────────────────────────────┤
│ API Layer         FastAPI routers（驗證、授權、序列化）        │
├─────────────────────────────────────────────────────────────┤
│ Service Layer     matching · kv · otp · sms · token_store · pii ·        │
│                   crypto · storage · moderation                          │
├─────────────────────────────────────────────────────────────┤
│ Domain Model      SQLAlchemy 2.0 Async ORM（14 張表）         │
├─────────────────────────────────────────────────────────────┤
│ Infrastructure    PostgreSQL · Redis · S3/R2                 │
└─────────────────────────────────────────────────────────────┘
```

**分層原則**
- Router 只負責 HTTP 語義（狀態碼、授權、schema 轉換），商業規則下沉到 `services/`。
- 所有 PII 輸出必經 `services/pii.py`，任何 router 都不直接回傳原始電話號碼。
- 跨模組的權限檢查集中在 `services/moderation.py`，避免各處重複實作封鎖邏輯。
- **全鏈路 async**：`AsyncSession` 從 router 一路傳到 service，WS handler 直接使用
  `AsyncSessionLocal`，不再有 threadpool 跳轉。

## 2. 非同步 ORM 規範（重要）

SQLAlchemy 2.0 async 模式下，**在 async 內容中觸發 lazy load 會拋出 `MissingGreenlet`**。
本專案以明確宣告杜絕此類隱性 IO：

| 宣告 | 使用場合 |
|------|----------|
| `lazy="selectin"` | 序列化時**一定會**用到的關聯（`TripPost.creator`、`ChatRoom.members`、`ChatRoomMember.profile`、`Review.reviewer/reviewee`、`Profile.travel_histories`） |
| `lazy="raise"` | 不該被隱式載入的關聯——一旦誤用立即報錯，而非靜默發出 N+1 查詢 |
| `passive_deletes=True` | 交由資料庫 `ON DELETE` 處理，避免 ORM 逐筆載入再刪除 |

其他跨方言處理：
- **Enum 欄位**一律 `native_enum=False` + `values_callable` + `validate_strings=True`，
  以 `VARCHAR + CHECK` 儲存，使同一份模型可同時跑 SQLite（開發/測試）與 PostgreSQL（生產）。
- **SQLite 引擎使用 `NullPool`**，避免連線跨 event loop 重用（測試中尤為關鍵）。

### 2.1 CPU-bound 工作必須離開 event loop

`async def` 不代表裡面的每一行都不阻塞。任何 CPU-bound 呼叫若直接寫在
async 函式內，會凍結整個 event loop —— 期間**所有**其他請求（包括 `/health`）都無法被排程。

本專案的例子是 Argon2：依 OWASP 建議參數（`time_cost=3, memory_cost=64 MiB`）
單次約 33 ms，登入／註冊／改密碼／註銷都會用到。實測 12 次雜湊內聯執行：

| 做法 | 耗時 | 期間 event loop 排程次數 |
|------|------|--------------------------|
| 直接內聯（`hash_password`） | 446 ms | **0**（完全凍結） |
| `anyio.to_thread.run_sync` | 235 ms | 14 |

所以 router **一律**呼叫 `security.py` 的 async 版本：

```python
password_hash=await hash_password_async(payload.password)
if not await verify_password_async(payload.password, user.password_hash): ...
await verify_password_dummy_async(payload.password)   # 反枚舉用，見 §3.1
```

同步原語（`hash_password` / `verify_password`）保持公開，因為 CLI 腳本
（`app/seed.py`）沒有 event loop 要保護。

**為何線程池有效**：argon2 在 C 層會**釋放 GIL**，所以並發雜湊是真正的平行運算，
不只是「不阻塞」——吞吐量同時提升（本機實測約 1.9–2.3×）。

**線程池必須設上限**：anyio 預設 40 條線程，而每次雜湊佔用 64 MiB，
全滿時峰值約 2.5 GB，足以讓小型容器 OOM。因此 `main.py` 的 lifespan 明確設定：

```python
to_thread.current_default_thread_limiter().total_tokens = settings.PASSWORD_HASH_MAX_CONCURRENCY
```

`PASSWORD_HASH_MAX_CONCURRENCY` 預設 8（≈512 MiB 峰值）。注意這是**每程序**上限，
多副本部署時總並發為「副本數 × 上限」。

回歸測試：`tests/test_password_offload.py` 以**線程身分**（非計時）證明工作真的離開
event loop，另有一項以 300 ms 假雜湊製造極大裕度的「loop 仍可排程」測試。

### 2.2 序列化 ORM 物件時，`lazy="raise"` 會讓端點回 500

`lazy="raise"` 的價值是「誤用立即報錯」，但**報錯的位置取決於誰先讀到那個屬性**。
`TripPostDetail.model_validate(post)` 會去讀 `post.applications`，
而 `db.get(TripPost, id)` 並不會 eager-load 它 —— 於是 Pydantic 把
`InvalidRequestError` 包成 `ValidationError`，端點直接回 **500**。

這個坑真正可怕的地方是**它不會出現在已被覆蓋的路徑上**：當時唯一測到該端點的案例是
「被封鎖者 → 404」，而那條分支在驗證之前就 `return` 了，happy path 從未被執行。
它是被前端 E2E 首跑抓出來的，不是被單元測試。

因此本專案的規則是：

> **schema 欄位若對應到 `lazy="raise"` 關聯，就不要直接 `model_validate(orm_obj)`。**
> 改為先驗證「無關聯的視圖」（例如 `TripPostOut`），再用它的 `model_dump()`
> 建出完整 schema，需要時才顯式補上關聯資料。

```python
# ✗ 讀到 lazy="raise" 的 applications → 500
detail = TripPostDetail.model_validate(post)

# ✓ 由無關聯的視圖組出，applications 一律從空清單開始
detail = TripPostDetail(**TripPostOut.model_validate(post).model_dump())
if post.creator_id == profile.id:        # 只有行程作者看得到申請清單
    detail.applications = [...]
```

附帶好處是**權限變成結構性的**：`applications` 永遠從空清單開始，
不可能因為某個呼叫點忘記清空而外洩給非作者。

回歸測試：`tests/test_api_flow.py::test_trip_detail_readable_and_hides_applications`。

## 3. 關鍵資料流

### 3.1 認證流程

```
POST /auth/register 或 /auth/login
  └─ verify_password (Argon2id, time_cost=3 / memory_cost=64MiB / parallelism=4)
  └─ create_access_token  → JSON body（15 分鐘，前端只放記憶體）
  └─ create_refresh_token → HttpOnly + Secure + SameSite=Strict Cookie（7 天）
                            Path 限定 /api/v1/auth

後續請求：Authorization: Bearer <access>
  └─ get_current_user → decode_token(expected_type="access") → db.get(User)

Access 過期（401）：
  └─ 前端 api.ts 自動 POST /auth/refresh（帶 Cookie）
  └─ 成功 → 換新 Access → 重試原請求（僅一次，避免無限迴圈）

手機驗證（OTP）：
  POST /auth/otp/request → otp.issue_otp()          → kv 存放驗證碼（TTL）
                         → sms.send_verification_code()  發送失敗只記日誌，不讓請求失敗
  POST /auth/otp/verify  → otp.verify_otp()         → hmac.compare_digest 定時比較 → is_verified=true

Refresh Token 撤銷與輪替：
  POST /auth/refresh
    └─ decode_token(expected_type="refresh")
    └─ token_store.is_revoked(jti)?  是 → 判定為洩漏 → bump_epoch() 使該用戶全部 token 失效
    └─ payload.epoch == token_store.current_epoch(user)?  否 → 401
    └─ token_store.revoke(舊 jti, 剩餘效期)   ← 輪替：舊的立刻失效
    └─ 簽發新 access + 新 refresh（帶當前 epoch）

  POST /auth/logout      → revoke(當前 jti) 後才清 Cookie（否則登出只是客戶端假象）
  POST /auth/password    → bump_epoch()，改密碼即登出所有裝置
```

**為何需要 deny-list 與 epoch 兩套機制？**
兩者回答不同問題：deny-list 回答「這個 token 失效了嗎」（登出、輪替）；
epoch 回答「這個時間點之前簽發的都失效了嗎」。有了 epoch，reuse detection 才有意義 ——
當一個已輪替的 token 再次出現，我們無法分辨持有者是本人還是攻擊者，
最安全的做法是讓雙方都重新登入，而不是猜測哪一個是真的。

**防帳號列舉**：帳號不存在與密碼錯誤**回傳完全相同的** `401 Invalid credentials`，
且兩條路徑都執行一次 Argon2 驗證（對不存在帳號驗證 dummy hash），使回應時間無從區分。

### 3.2 配對流程

```
GET /trips/recommendations
  └─ matching.recommend_trips(db, profile)
       ├─ 取出 profile 訊號：足跡國家/城市、旅遊風格標籤、語言、偏好預算
       ├─ 排除：自己的貼文、被封鎖者
       ├─ 對每篇 open 貼文評分：
       │     目的地命中城市 +3.0 / 國家 +2.0
       │     旅遊風格標籤 ∩ 貼文 tags +1.5 × 命中數
       │     與作者共同語言 +1.0（有交集即加，不隨語言數累加）
       │     預算類型相符 +0.8
       │     3 天內發佈 +1.0 / 14 天內 +0.4
       │     出發日在未來 +0.6
       └─ 排序 → 回傳 score + reasons[]
```

設計取捨：刻意採用**可解釋的規則式評分**而非黑盒模型。旅伴配對屬於高信任敏感場景，
使用者需要理解「為什麼推薦這個人」才會採取行動，因此每個加權訊號都對應一句人話。

### 3.3 聊天流程

```
1. 建立房間
   申請被接受 → trips.py::decide_application
                → _ensure_direct_room() 自動建立（或沿用）DIRECT room + 2 筆 members

2. 建立連線
   ws://…/api/v1/ws/chat/{room_id}?token=<access>
     └─ _authenticate(token)        失敗 → close(1008)（在 accept() 之前）
     └─ _authorize_room(room, me)   失敗 → close(1008)
     └─ manager.connect() → broadcast presence(join)

   注意：未 accept() 即 close() 會被 ASGI 伺服器轉譯為 HTTP 403 握手拒絕，
   因此真實客戶端看到的是 403（瀏覽器 onclose 1006），而非 WS 1008。
   前端據「從未 OPEN 即關閉」判斷被拒。

3. 收發訊息
   收到 {type:"message", content}
     └─ manager.allow_message()  → token bucket 2/s，超限回 error:rate_limited
     └─ _persist_message()       → 再次檢查封鎖關係 → 寫入 DB
     └─ manager.broadcast({type:"message", ...})
     └─ is_phone_like() 為真 → 對發送者回 safety_hint
```

**為何在寫入時再檢查一次封鎖？**
封鎖必須「立即生效」。若只在建立連線時檢查，已建立的連線仍能繼續傳訊；
因此每次寫入前都重新查詢 `blocks`，並以 `error:blocked` 回報。

## 4. 資料模型關聯

```
users 1───1 profiles 1───* travel_histories
              │
              ├──* trip_posts ──* trip_applications
              │       │
              │       └──* trip_post_tags（一列一標籤）
              │
              ├──* chat_room_members ──* chat_rooms ──* chat_messages
              │
              ├──* reviews（reviewer / reviewee，需共同出遊過）
              ├──* blocks（blocker / blocked，雙向生效）
              ├──* reports（reporter / reported）
              └──* notifications（recipient / actor）
```

共 14 張表：`users`、`profiles`、`travel_histories`、`trip_posts`、`trip_post_tags`、
`trip_applications`、`chat_rooms`、`chat_room_members`、`chat_messages`、`reviews`、
`blocks`、`reports`、`notifications`、`audit_logs`。

刪除策略：
- `users` → `profiles` → `travel_histories`：`CASCADE`（硬刪除時連鎖清除）
- `trip_posts` → `trip_post_tags` / `trip_applications`：`CASCADE`
- `notifications.recipient`：`CASCADE`；`actor` / `trip_post` / `chat_room`：`SET NULL`
- `chat_messages.sender_id`：`SET NULL`（匿名化後訊息保留但去識別化）
- `chat_rooms.trip_post_id`：`SET NULL`（刪行程不影響既有對話）
- `audit_logs.actor_profile_id`：`SET NULL`（**不是** `CASCADE` —— 否則註銷權會變成洗白濫用紀錄的手段）；`target_id` 刻意不是外鍵，因為目標常正是被刪掉的那一列

> ⚠️ **SQLite 預設不強制外鍵**。`PRAGMA foreign_keys=ON` 必須在每個連線上執行，
> 否則上述所有 `ondelete=` 規則在 dev/test 方言上都不會生效，而在 PostgreSQL（生產）
> 會 —— 同一個操作、兩種語意。`app/db/session.py` 因此在 SQLite 的 `connect` 事件中
> 開啟它；Alembic 走自己的連線，刻意保持關閉（batch 重建資料表時外鍵必須關）。

## 5. 前端架構

| 檔案 | 職責 |
|------|------|
| `lib/api.ts` | 唯一 HTTP 出口。管理記憶體中的 Access Token、統一 401 自動刷新重試、拋出結構化 `ApiError`、`errorMessage()` 攤平 FastAPI 錯誤 |
| `lib/auth.tsx` | `AuthProvider` Context + `RequireAuth` 頁面閘門。掛載時嘗試靜默還原 Session（依賴 HttpOnly Cookie） |
| `lib/types.ts` | 與後端 Pydantic schema 一一對應的型別 |
| `lib/utils.ts` | `cn()`、日期格式化、Enum 中文標籤對照表 |
| `components/ui/*` | Shadcn 風格元件（Radix primitives + CVA），共 11 個 |
| `components/SafeHtml.tsx` | 所有 Rich Text 的唯一渲染路徑，內建 DOMPurify allow-list |
| `components/DisclaimerBanner.tsx` | 法律免責聲明，強制出現於行程頁與聊天頁 |

**Token 存放策略**：Access Token **只存在記憶體**（不寫 localStorage），避免 XSS 竊取；
Refresh Token 由後端放在 HttpOnly Cookie，JS 完全無法讀取。這是本專案最重要的前端安全設計。

**樣式系統**：Tailwind CSS 3.4 + HSL CSS 變數主題（`:root` / `.dark`），
以 `tailwind.config.ts` 的 `darkMode: ["class"]` 支援深色模式。

## 6. 已知取捨與擴展路徑

| 主題 | 現況 | 建議做法 |
|------|------|----------|
| ~~JSON 陣列篩選~~ | ✅ 已解：`trip_post_tags` join table（複合主鍵 + `ix_trip_post_tags_tag`），篩選為索引驅動的半連接，精確且無上限。詳見 §10 | 若單一標籤列數成長到數十萬，重新評估 `IN` vs `EXISTS` 的記憶體取捨 |
| 水平擴展 WebSocket | `ConnectionManager` 為單程序記憶體內狀態 | 改用 Redis Pub/Sub 廣播，使多個 API 副本共享房間狀態 |
| 背景任務 | 同步處理 | 引入 Celery / ARQ 處理通知、圖片後處理、內容審核 |
| 搜尋 | `ILIKE` 查詢 | PostgreSQL `tsvector` + GIN index，或引入 Meilisearch |
| 審計 | 尚未記錄 | 新增 `audit_logs` 表，記錄封鎖、檢舉、註銷、管理員操作 |
| 遷移 | ✅ 已建立初始遷移；`init_db()` 偵測到 `alembic_version` 即讓位給 Alembic，生產則完全跳過 | 新 schema 變更一律 `alembic revision --autogenerate`，並以 `alembic check` 驗證無漂移 |

## 7. 臨時狀態層（`services/kv.py`）

OTP 驗證碼與已撤銷的 JWT id 都屬於**不該落庫**的臨時狀態，統一走 `services/kv.py`：
Redis 可達時用 Redis，否則退回程序內 TTL dict。

**關鍵設計：斷路器（circuit breaker）。**
單純的「先試 Redis，失敗就退回記憶體」是不夠的 —— Redis 掛掉時**每一次**呼叫
都要先付連線失敗的成本。在 Windows 上該成本約 4 秒（`localhost` 同時解析為
`::1` 與 `127.0.0.1`，無法連線的 IPv6 嘗試必須先逾時才會試 IPv4）。
而 KV 查詢位於登入／刷新路徑上，等於每個請求都卡 4 秒。

因此實作三層防護：

1. **明確的短逾時**（`socket_connect_timeout=0.5s`）—— 單次嘗試不會無限等待。
2. **斷路器** —— 一次失敗後，冷卻 30 秒內完全跳過 Redis 直走記憶體。
   只有故障後的第一次呼叫需要付代價。
3. **延遲建立連線** —— import 此模組不會觸發任何網路行為。

> ⚠️ 記憶體後備是**每程序**的。多副本部署時 OTP 與 token 撤銷狀態不會共享，
> 因此生產環境 Redis 是**必要**元件，而非選用。

## 8. OTP 發送抽象層（`services/sms.py`）

`services/sms.py` 以 `SMS_PROVIDER` 設定選擇實作，讓「開發可跑、生產可接」不需要改程式碼：

| 值 | 行為 | 適用 |
|----|------|------|
| `console`（預設） | 將驗證碼寫入日誌 | 本機開發 |
| `webhook` | `POST SMS_WEBHOOK_URL`，帶 `Authorization: Bearer <SMS_WEBHOOK_TOKEN>`；可自訂 `SMS_SENDER_ID` 與逾時 | 生產（對接電訊商或簡訊服務商） |

**核心約束：`send_verification_code()` 永不拋出例外。** 它回傳 `bool`，
失敗只記 `warning` 日誌。原因有兩個：

1. 驗證碼已寫入 KV，使用者「稍後可重送」；發送失敗不該讓整個請求變成 500。
2. 回應內容不可因電話號碼是否已註冊而不同 —— 否則 OTP 端點本身就成了帳號列舉工具。

因此 `/auth/otp/request` 一律回傳 `sent` 布林值（代表已交付給供應商），
而非「號碼是否存在」。

**啟動時檢查**：`main.py` 的 lifespan 會呼叫 `provider_is_production_ready()`，
在 `ENV=production` 卻仍用 `console` 供應商時記錄 `ERROR` 級警告，
避免「以為有發簡訊、其實只寫了日誌」的靜默失效。

---

## 9. 通知系統（`services/notifications.py`）

通知一律由**服務層**建立，而非散落在各 router —— 因為有兩條不變式必須在任何呼叫點都成立：

| 不變式 | 為什麼 |
|--------|--------|
| **永不通知自己** | 自己的動作不該把自己叫醒（例如自己送出的訊息） |
| **永不跨越封鎖** | 任一方封鎖另一方時，通知直接不建立。少了這道閘門，通知會從提醒變成騷擾管道 —— 而封鎖的意義正是「我不想再收到你的任何東西」 |

兩者都在 `create()` 內強制，呼叫點不必記得檢查。這與「在每個呼叫點自行檢查」的差別是：
後者只要漏掉一處就破功，而且沒有測試能證明它沒漏。

### 觸發點

| 事件 | 類型 | 收件人 |
|------|------|--------|
| 有人申請行程 | `APPLICATION_RECEIVED` | 行程作者 |
| 申請被接受／婉拒 | `APPLICATION_ACCEPTED` / `APPLICATION_REJECTED` | 申請者 |
| 房間內有新訊息 | `NEW_MESSAGE` | 其他成員 |
| 收到評價 | `REVIEW_RECEIVED` | 被評價者 |

所有建立動作都與**觸發它的交易在同一個 transaction** 內 commit ——
「通知了一件其實沒存進去的事」比沒有通知更糟。

### 去重策略

`NEW_MESSAGE` 以 `(recipient_profile_id, chat_room_id)` upsert
（索引 `ix_notifications_message_dedupe`）：同一房間的未讀訊息只佔一格，
徽章數字代表「有幾個房間在等你」，而不是訊息總量。

### 刪除規則（FK）

| 欄位 | 規則 | 理由 |
|------|------|------|
| `recipient_profile_id` | `CASCADE` | 收件人註銷 → 通知一併消失（被遺忘權） |
| `actor_profile_id` | `SET NULL` | 觸發者註銷不該**重寫收件人的歷史**；該則通知仍有意義 |
| `trip_post_id` / `chat_room_id` | `SET NULL` | 同上；點進去會落到已刪除的頁面，但不至於讓通知整批消失 |

### 讀取授權

所有查詢都在 WHERE 子句過濾 `recipient_profile_id`，因此他人的 id 得到的是 `404`
（與不存在同義），而不是 `403` —— 後者等於承認「這則通知存在，只是不是你的」。
也刻意**不提供** `GET /notifications/{id}`：by-id 查詢是 IDOR 的溫床，而前端不需要它。

### 前端：為何是輪詢而非 WebSocket

`NotificationBell` 每 30 秒打一次 `/notifications/unread-count`，並在 `window.focus`
時重同步。未讀數變動極少、資料量極小，輪詢的實作與除錯成本遠低於為通知再開一條 socket；
而且它不依賴聊天 WebSocket —— 就算聊天連線斷了，通知照樣會到。

---

## 10. 標籤儲存：為何是 join table 而不是 JSON

`trip_posts.tags` 原本是 JSON 欄位。要篩選時，JSON 的包含查詢在 SQLite 與 PostgreSQL
上語法不同，於是舊寫法退化成「撈最多 1000 筆到記憶體、用 Python 集合交集比對」：

```python
# ✗ 舊寫法：結果被靜默截斷，total 只是掃描窗內的數量
rows = (await db.execute(stmt.limit(_TAG_SCAN_LIMIT))).scalars().all()
matched = [p for p in rows if wanted_tags & {t.upper() for t in (p.tags or [])}]
total = len(matched)
```

超過 1000 筆時，符合的貼文會**默默消失**，而回應裡的 `total` 看起來一切正常。

現在是一列一標籤的 `trip_post_tags`（複合主鍵 `(trip_post_id, tag)`）：

```python
# ✓ 精確、無上限、走索引
stmt = stmt.where(
    TripPost.id.in_(
        select(TripPostTag.trip_post_id).where(TripPostTag.tag.in_(wanted_tags))
    )
)
```

### 為何是半連接（`IN`）而不是相關子查詢（`EXISTS`）

兩者語意相同，但 SQLite 不會重排相關子查詢，所以 `EXISTS` 一定得對 `trip_posts`
逐列做一次索引查找 —— **不論標籤多冷門都是全表掃描**。50k 貼文 / 50k 標籤列的實測
（三次取最快）：

| 情境 | `EXISTS` | `IN` |
|------|---------:|-----:|
| 冷門標籤（50 筆命中） | 57.9 ms | **0.1 ms** |
| 熱門標籤（50k 筆命中，全表） | **57.0 ms** | 159 ms |

「用標籤篩選」這個動作本身就是為了縮小結果集，所以冷門標籤才是設計情境。代價是
`IN` 會把命中的標籤列具體化，而 `EXISTS` 的記憶體用量是平坦的 —— 若單一標籤的列數
成長到數十萬，就該重新評估這個取捨。

`EXPLAIN QUERY PLAN` 確認 `IN` 形式由 `ix_trip_post_tags_tag` 驅動（`LIST SUBQUERY`），
而不是掃 `trip_posts`。

### 正規化：為何在寫入時做

`normalise_tags()` 把標籤轉成大寫、去空白、去重，並且在三條寫入路徑上都會執行
（Pydantic schema、ORM setter、遷移腳本各自實作一份）。若容許 `photography` 與
`PHOTOGRAPHY` 並存，`tag IN (...)` 就只會命中其中一種拼法，同一篇行程會依客戶端
當次的大小寫時有時無 —— 這種「有時對」的 bug 最難查。

`TripPost.tags` 是 `@property`（讀）＋ setter（寫），底層是 `_tag_rows` 關係。
關係宣告為 `lazy="selectin"` **不是為了效能**：`TripPostOut.model_validate()` 每次
序列化都會讀 `tags`，所以這裡若用 `lazy="raise"` 或惰性載入，就會重演 §2.2 那個
「序列化時才炸」的 500。

---

## 11. 可觀測性與稽核軌跡

三個獨立關注點，刻意不做成一個框架：**日誌**（發生什麼事）、**指標**（整體健康）、
**稽核**（誰對誰做了什麼）。前兩者可以輪替、可以丟；第三個是證據。

### 11.1 結構化日誌（`core/logging.py`）

| 決策 | 理由 |
|------|------|
| 生產用 JSON、開發用文字 | 免費文字日誌答不出「這個帳號這一小時失敗了哪些請求」，除非做 grep 體操；JSON 物件可以。但沒人會自願用 JSON 讀 stack trace |
| **遮蔽在 formatter，不在呼叫點** | 一個進到 `extra=` 的密碼或 token 會**即使開發者忘了**也被抹掉 —— 這是唯一能在死線壓力下存活的版本 |
| 提示清單刻意很窄 | 加一個 `"code"` 進去會連 `status_code` 一起遮蔽，而**過度熱心的遮蔽器會被第一個被它妨礙的人關掉** |
| 請求 id 用 `ContextVar` | 它是 per-async-task 的，所以並發請求看不到彼此的 id。用全域變數會讓 A 請求的 id 出現在 B 的日誌裡 —— 比沒有 id 更糟 |

`X-Request-ID` 會被回寫進回應標頭，也會寫進該請求的每一行日誌，因此它是一個
**日誌注入**向量：在文字格式下，一個換行加上偽造的 `level` 就足以製造假紀錄。
所以接受的字母表小到 `[A-Za-z0-9._-]` 且上限 64 字元，其餘一律拒絕並改發新 id。

`TextFormatter` 只顯示 id 前 8 碼供肉眼對照；完整 id 仍在回應標頭裡。

### 11.2 指標（`core/metrics.py`）

標籤用**路由範本**（`/api/v1/trips/{trip_id}`），**絕不用原始路徑**。
用原始路徑會為每一趟行程建立一條時間序列 —— 這是「指標端點拖垮監控系統」
而不是幫助它的經典方式。同理排除 user id、request id 與 query string。

`prometheus_client` 是防禦性匯入（與 `core/rate_limit.py` 對待 `slowapi` 一致）：
選用依賴缺失不該讓服務起不來；缺席時 `AVAILABLE=False`，`/metrics` 不掛載，
每個 `observe_*` 變成 no-op。

`/metrics` 依設計**不帶驗證**（scraper 不帶 JWT），因此預設關閉
（`METRICS_ENABLED=false`）—— 開啟必須是有意識的決定，且應置於內網或 allowlist 之後。

### 11.3 中介層順序

`add_middleware` 是**前插**，所以最後加入的是最外層。`RequestContextMiddleware`
刻意最後加入，才能看到每一個回應（包括由它下層中介層產生的 CORS preflight 拒絕），
並在全部回應上蓋 `X-Request-ID`。

兩個踩過的坑：

- **`scope["route"]` 只有路由之後才存在。** 在 `call_next` **之前**取樣，
  會讓每一個請求都被標成 `<unmatched>` —— 包括那些完美匹配的。
- **`route` 是 4xx/5xx 也拿得到**，因為路由先於依賴執行。一個未帶 token 的
  `/trips/{id}` 探測會回 401，但指標標籤仍是正確的範本，而不是 `<unmatched>`。

### 11.4 稽核軌跡（`models/audit.py`、`services/audit.py`）

涵蓋 Security Spec 點名的**四類**：封鎖、檢舉、註銷、管理員操作。
刻意**不是**通用的「記錄所有事件」表 —— 會累積 PII，而且沒人能在裡面找到真正
重要的那四件事。

| 設計 | 理由 |
|------|------|
| 沒有 `updated_at`、沒有 UPDATE／DELETE 路徑 | 可以被編輯的稽核列不是證據，是註解。模型連「編輯」需要的欄位都不提供 |
| `actor_profile_id` 是 `SET NULL`，**不是** `CASCADE` | 若註銷會一併抹掉「這個人做過什麼」的紀錄，被遺忘權就變成了**洗白濫用紀錄**的手段。硬刪除後該列仍在，只是 actor 為 null |
| `target_id` 刻意**不是外鍵** | 目標經常正是被這個動作刪掉的那一列（被封鎖關係、被註銷的帳號）。真外鍵不是把證據連帶刪掉，就是拒絕刪除 |
| `detail` 只放 id／enum／數量 | 稽核表是唯一刻意活得比註銷更久的表，任何免費文字寫進來都會變成使用者要求刪除之資料的永久副本 |
| **與狀態變更共用同一個交易** | 為一個後來 rollback 的動作留下稽核列，比完全沒有更糟：那是從未發生的證據。因此 `record()` 與 `services/notifications.create` 一樣**不 commit** |
| 註銷路徑**先寫稽核再刪除** | 這是唯一一處順序反過來的地方，這樣 `ON DELETE SET NULL` 才能發揮作用 |

**丟棄 `detail` 的鍵時要寫 warning。** 遮蔽器以**鍵名**判斷，而鍵名區塊清單
永遠會在某處出錯：`"reason"` 曾在清單裡，於是 `REPORT_SUBMITTED` 的
`detail={"reason": ...}` 被整個丟掉 —— 而 `Report.reason` 是
`ReportCreate._valid_reason` 限制的**封閉 enum**，不是散文；真正該防的免費文字
（`Report.detail`，上限 2000 字）本來就沒有被複製進來。**會靜默失敗的警報器
不算警報器**，所以現在每次丟棄都會留一行 warning。

### 11.5 RBAC 的大小寫陷阱

`UserRole.ADMIN` 是 `str` Enum，值為 `"ADMIN"`，因此 `require_role("admin")`
的 `user.role not in roles` 永遠不相等 —— **三個管理端點對每一個人回 403，
管理員也一樣**。回的是 403 而非 500，前端又把 403 當成預期結果處理，
所以整個管理介面只是「沒有人進得去」，看起來一切正常。

`require_role` 現在把兩邊都正規化為大寫，並且**未知角色名稱在建立依賴時就拋
`ValueError`**：`require_role("admni")` 是同一種靜默全面鎖死，讓它在啟動時炸掉，
比讓管理員安靜地被擋在門外好。

> 這個 bug 之所以能長期存在，是因為**測試套件裡沒有任何管理員測試** ——
> 沒有 API 能授予角色，所以也沒人想過要建 fixture。修好之後補上了 `admin_user`。

### 11.6 為何稽核端點本身不記錄讀取

讀檢舉佇列**會**記錄（`ADMIN_QUEUE_VIEWED`）：看別人的檢舉是行使特權。
但讀**稽核端點**刻意不記 —— 自我稽核會自我否定：每看一頁就多一列，
列數隨「查看」這個動作成長，分頁會在讀者腳下位移。存取日誌已經記下這個請求。

---

## 12. 內容審核（`services/content_filter.py`）

### 12.1 為什麼不是一份敏感詞清單

直覺做法是「一份禁詞表 + 命中就擋」。在旅遊 App 上這會立刻出事，因為正常文字
充滿了會被粗略比對命中的詞：

| 正常文字 | 會被什麼誤擋 |
|----------|--------------|
| 「share the hotel **deposit**」 | 任何以 `deposit` 為詐騙關鍵字的規則 |
| 「a **killer** view from the summit」 | `kill` |
| 「We will **kill time** at the airport」 | `kill` |
| 「visit **Gunsan** in Korea」 | `gun` |
| 「how to avoid tuk-tuk **scams**」 | `scam`（在討論如何避免詐騙！） |
| 「the **drugstore** is next to the station」 | `drug` |

**誤擋的代價是「真實使用者無法發文」，比多一筆檢舉佇列更貴。** 所以本地規則是
刻意**高精確度**的：只有幾乎沒有正當讀法的語句才 `BLOCK`，可辯解的一律 `FLAG`。

| 判定 | 規則例子 | 行為 |
|------|----------|------|
| `BLOCK` | `escort service`、`western union`、第一人稱 + 意圖 + 人稱受詞的暴力威脅、帶買賣動詞的毒品 | `422` 拒寫 |
| `FLAG` | 要求匯款給個人、交換 WhatsApp／Telegram、短網址 | **放行**，只累加計數 |

`FLAG` 不是「軟性阻擋」，它放行內容；存在的唯一目的是讓「這種內容是不是變多了」
有答案，而不必靠猜。

### 12.2 錯誤訊息不能說出命中的是什麼

`422` 的 `detail` 指出**欄位**（「行程說明：…」），但**不說**命中的詞或規則。
說出來就等於讓使用者一次一條地把規則集試出來 —— 這是把自己的過濾器當成
訓練資料送給對方。命中的原因只寫進日誌與指標標籤（封閉集合，不會爆基數）。

### 12.3 供應商故障一律 fail-open

與限流器在 Redis 故障時 fail-open 是同一個取捨：**審核服務中斷不該變成全面寫入中斷**。
供應商逾時或回錯 → 回傳「無意見」，本地判定照舊生效。

注意 fail-open 只適用於**供應商**，不適用於本地規則：供應商掛掉時，
`escort service` 依然被擋。`CONTENT_FILTER_FAIL_OPEN=false` 可以改成 fail-closed，
但那會讓供應商成為每一次寫入的硬依賴，很少是你想要的。

### 12.4 WebSocket 走不同的訊號路徑

`socket` 上沒有 HTTP 狀態碼，所以 `content_filter` 拆成兩層：

* `enforce()` — 適合 HTTP 呼叫點：擋下時直接拋 `422`。
* `screen()` — 回傳 `(verdict, detail)`，不拋。WS 用它送出可渲染的錯誤框：

```json
{"type": "error", "code": "content_rejected", "detail": "訊息：…"}
```

### 12.5 零寬字元：兩個正規化都要試

`_variants()` 回傳**兩種**正規化形式，因為「移除零寬字元」與「把零寬字元換成空格」
各有各的盲點，互不包含：

| 輸入 | 移除 → | 換成空格 → |
|------|--------|-----------|
| `pro\u200bstitute` | `prostitute` ✅ | `pro stitute` ✗ |
| `escort\u200bservice` | `escortservice` ✗ | `escort service` ✅ |

只做移除，第二種就整類繞過（這正是實作時踩到的坑）；只做換空格，第一種就繞過。
兩者都比對一次，成本是一次額外掃描。NFKC 另外處理全形字。

**這不是安全邊界。** 空白分隔（`k i l l`）刻意沒有處理 —— 那要刪掉所有空格，
會在每個詞邊界製造誤擋。這裡的定位是「把成本從舉手之勞提高到稍微麻煩」，
真正擋人的是檢舉與封鎖流程。

### 12.6 為何不寫進稽核軌跡

被擋下的寫入**不進 `audit_logs`**。稽核表只涵蓋 Security Spec 點名的四類
（封鎖／檢舉／註銷／管理員操作），而「一次被拒的寫入」是高頻的**運維訊號**，
不是問責紀錄。理由與登入失敗不進稽核表完全相同：**不能讓一個重試的濫用者
把唯一永不清理的表養大。** 這類事件進指標（`tripmate_content_blocked_total`）
與日誌（含 request id）。

---

## 13. 多語系（`lib/i18n/`）

### 13.1 為何不用 `[locale]` 路由

`next-intl` 的標準做法是把每個路由搬到 `app/[locale]/` 之下。這裡刻意不那樣做：
**locale 存在 cookie，網址不變**。代價與好處都很具體：

| | 採用 cookie（本專案） | 採用 `[locale]` 路由 |
|---|---|---|
| 既有路由、連結、E2E 斷言 | **完全不動** | 全部要改 |
| 伺服器渲染的 `metadata` | 只能是預設語言 | 可依 locale 變化（SEO 較好） |
| 首次載入非預設語言 | 短暫閃現預設語言 | 無閃現 |
| 每語言獨立網址 | 無 | 有 |

這是一個已登入的產品，公開頁面只有登入／註冊，per-locale URL 的價值有限，
而「不動 12 個路由」的價值很具體。**若日後真的需要**，把路由搬到 `[locale]`
並在伺服器端讀取該區段即可 —— 這一層的 API 不需要改動。

### 13.2 型別即保證：缺翻譯是編譯錯誤

`zhHK` 是**鍵集合的唯一來源**，`MessageKey` 由它推導；其他語言宣告為
`Record<MessageKey, string>`：

```ts
const zhHK = { "nav.trips": "探索行程", … } as const;
export type MessageKey = keyof typeof zhHK;
const en: Record<MessageKey, string> = { … };   // 少一個鍵 → tsc 直接報錯
```

所以「新增一個鍵」的流程是：加進 `zhHK` → TypeScript 會指出每個還沒翻譯的語言。
這比 JSON 字典 + 執行期 fallback 好，因為後者只會在使用者眼前才發現。

### 13.3 列舉標籤用明確對照表，不用字串拼接

伺服器回傳的是列舉值（`BUDGET`、`OPEN`、`MALE`…）。標籤放在字典裡，
但**不是**用模板字串硬湊鍵名（`` t(`${group}.${value}`) ``），而是明確對照表：

```ts
const LABEL_KEYS: Record<LabelGroup, Record<string, MessageKey>> = {
  budget: { BUDGET: "budget.BUDGET", MODERATE: "budget.MODERATE", LUXURY: "budget.LUXURY" },
  …
};
```

理由有兩個：拼錯鍵名會是**編譯錯誤**；而對照表查不到的值（後端新增了列舉成員）
會**退回原始字串**——醜，但不會變空白。看不見的列舉值比難看的列舉值危險得多。

### 13.4 插入變數：找不到的佔位符要留在原地

```ts
template.replace(/\{(\w+)\}/g, (m, name) =>
  hasOwnProperty(vars, name) ? String(vars[name]) : m);   // 未知 → 保留 {name}
```

把未知佔位符**留著**而不是清空，是刻意的：漏傳變數時畫面會出現字面上的
`{count}`，看得見、能被回報；清空則會安靜地產生「你共有  則通知」。

### 13.5 不寫進字典的東西

- **`<html lang>` 與 `Intl` 用的標籤不是同一個**：文件要宣告 `zh-Hant-HK`
  （script + region），`Intl` 要的是 `zh-HK`。用同一個值不是宣告錯誤就是日期格式錯誤。
- **`errorMessage()` 的 fallback 改成必填**：`lib/api.ts` 不是 React 模組，
  拿不到 hook。讓它保留一個中文預設值，就等於在共用模組裡留一句永遠不會被翻譯的字。
  每個呼叫點本來就知道該顯示什麼訊息。
- **多筆驗證錯誤的分隔符用 `·`**：模組拿不到 locale，而全形 `；` 在英文裡不對、
  半形 `;` 在中文裡彆扭。`·` 兩種語言都自然。

### 13.6 防止回退：`npm run check:i18n`

`tsc` 能保證「字典裡的鍵都翻譯了」，但**看不到**一句從未被搬進字典的字面值。
所以另有一支 `scripts/check-i18n.mjs`：

1. 走訪 `src/`，任何非 `dictionaries.ts` 的檔案出現**漢字**即失敗。
2. 檢查 `zh-HK` 與 `en` 的鍵集合完全相同、且沒有重複鍵。

它刻意**只**抓漢字（`U+3400–4DBF`、`U+4E00–9FFF`、`U+F900–FAFF`，加上
CJK 標點 `U+3000–303F`），**不含**全形符號區（`U+FF00–FFEF`）——那個區塊
大半是標點與全形拉丁字母，單獨一個 `；` 不是「未翻譯的內容」，
為它設計迂迴寫法對讀者毫無意義。真正值得讓建置失敗的是漏掉的漢字。

## 14. 物件儲存的命名空間（`services/storage.py`）

物件鍵一律由 `build_key()` 產生，格式是 `{prefix}/{uuid4().hex}.{ext}` ——
**檔名完全由伺服器決定**，唯一的可變部分是 `prefix`。

`POST /uploads/presign` 的 `prefix` 來自請求 body，因此客戶端可以選定
自己的命名空間（例如把檔案寫到 `profiles/` 開頭之類的位置）。這是**刻意的**：
該端點的用途就是呼叫端自行決定分類前綴。安全邊界不依賴 `prefix`，而是靠兩件事：

1. **儲存桶本身即邊界**。物件鍵只存在於 `S3_BUCKET` / `LOCAL_UPLOAD_DIR` 之內，
   且 `presign_put` 產生的是**單一 key + 單一 content-type** 的簽名 URL ——
   拿到網址的人只能寫那一個鍵，不能改路徑、不能改型別。跨租戶的隔離靠桶的
   存取政策，不靠前綴的命名習慣。
2. **本機後備路徑有獨立的遍歷防護**。`_local_path()` 以
   `Path.resolve()` 正規化後檢查是否仍位於 root 之下，因此 `../` 之類的鍵
   即使傳進來也會被拒。這道防護必須留在 `storage.py` 內層，不能只做在 router ——
   否則任何未來的呼叫點都會重新暴露同一個問題。

> 為何現在不改：前綴不是信任邊界，收緊它只會限制一個尚未被使用的功能
> （前端沒有任何 `presign` 呼叫點）。若日後要讓使用者選擇前綴，正確做法是
> 在 router 端以白名單正規化，而不是把規則散進 `storage.py`。

### 14.1 `presign_put` 的兩道守衛有先後，且後者在本機不可達

```python
if settings.STORAGE_BACKEND != "s3":        # ← 先執行
    raise UploadError("Pre-signed uploads require the S3 storage backend.")
if content_type not in ALLOWED_MIME:        # ← local 後端下永遠到不了
    raise UploadError("Unsupported content type.")
```

`STORAGE_BACKEND` 在 dev／test 的預設是 `"local"`，所以第一道守衛**必定**
在第一時間短路，content-type 白名單在 HTTP 層**完全不可達**。

這件事有兩個後果，一個是設計上的、一個是測試上的：

- **設計上沒問題**：local 後端本來就不提供 pre-sign，兩個 400 的訊息不同但語意一致
  （「這個部署不支援」）。先判後端也更省事 —— 不必為一個沒有 S3 的部署去驗型別。
- **測試上必須補位**：既然 HTTP 層到不了，**把白名單整段刪掉不會有任何測試變紅**。
  而它正是生產環境上**唯一**阻止 `application/pdf`（或呼叫端任意命名的型別）
  被寫入上傳桶的檢查。因此 `tests/test_upload_and_history_writes.py` 中有一條
  **直接呼叫 `presign_put`** 並暫時把 `STORAGE_BACKEND` 切成 `"s3"` 的測試。
  `generate_presigned_url` 是**本地簽章**，不需要真憑證也不會連 AWS，
  所以「允許的型別會回傳一個 URL」本身就是白名單放行的**正面訊號**。

> **可推廣的規則**：當一段安全檢查被另一段較早的檢查**在測試環境中永久遮蔽**時，
> 它需要一條直接呼叫該函式的測試。覆蓋率工具只會顯示「這行被執行過」——
> 但在測試環境裡它其實**從未被求值**。
