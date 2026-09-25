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
│ Domain Model      SQLAlchemy 2.0 Async ORM（15 張表）         │
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
     └─ await manager.allow_message() → kv.DistributedTokenBucket 2/s（跨副本共享），超限回 error:rate_limited
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

共 15 張表：`users`、`profiles`、`travel_histories`、`trip_posts`、`trip_post_tags`、
`trip_applications`、`chat_rooms`、`chat_room_members`、`chat_messages`、`reviews`、
`blocks`、`reports`、`notifications`、`audit_logs`、`cities`。

刪除策略：
- `users` → `profiles` → `travel_histories`：`CASCADE`（硬刪除時連鎖清除）
- `trip_posts` → `trip_post_tags` / `trip_applications`：`CASCADE`
- `notifications.recipient`：`CASCADE`；`actor` / `trip_post` / `chat_room`：`SET NULL`
- `trip_posts.city_id` / `travel_histories.city_id` → `cities.id`：**`SET NULL`**
  （重新匯入 GeoNames 會替換 `cities` 的所有列，`CASCADE` 會連帶刪掉使用者行程；
  城市消失不應讓行程消失，只是失去可驗證的座標鍵）
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

**字典按功能拆成 namespace 檔**（359 鍵／34 個前綴 → 13 個檔，放在
`src/lib/i18n/namespaces/`）。`dictionaries.ts` 本身退化成一個薄的**組裝器**：

```ts
import * as admin from "./namespaces/admin";   // …共 13 個
const zhHK = { ...admin.zhHK, ...auth.zhHK, … };
export type MessageKey = keyof typeof zhHK;
const en: Record<MessageKey, string> = { ...admin.en, ...auth.en, … };
```

用**展開**而不是 `Object.assign`：展開在型別層會被檢查**重複鍵**，撞鍵是編譯錯誤；
`Object.assign` 會安靜地後者覆蓋前者。`MessageKey` 與 `Record<MessageKey, string>`
的寫法完全不變，所以「缺翻譯是編譯錯誤」這個保證原封不動。

`NAMESPACES` 這個 export 列出所有模組，供 `check:i18n` 之類的工具使用。
**新增一個 namespace 檔卻忘了 import 進 `dictionaries.ts`** 是這個佈局唯一的新失效模式 ——
`check:i18n` 因此加了一條「磁碟上每個 namespace 檔都被 `dictionaries.ts` import 過」的檢查。

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

### 13.6 防止回退：`npm run check:i18n` + 後端 `check_no_hardcoded_cjk.py`

`tsc` 能保證「字典裡的鍵都翻譯了」，但**看不到**一句從未被搬進字典的字面值。
所以另有一支 `scripts/check-i18n.mjs`：

1. 走訪 `src/`，任何非字典檔（`dictionaries.ts` 或 `src/lib/i18n/namespaces/` 之下的檔）
   出現**漢字**即失敗。
2. 檢查 `zh-HK` 與 `en` 的鍵集合完全相同、且沒有重複鍵。
3. 檢查沒有**定義了卻沒被引用**的鍵——那通常代表抽字串抽到一半。
4. 檢查磁碟上每個 namespace 檔都被 `dictionaries.ts` import 過（見 §13.2）。

實作上有兩個坑值得記住：

- 讀鍵集合**不能**用文字比對 `const zhHK = {` 區塊——字典拆檔後那區塊不再存在，
  而且值裡面出現 `}` 會讓簡單的掃描提早結束。改為**走括號配對**，並逐一讀 namespace 檔。
- 第 3 步的「引用語料」要**排除** namespace 檔（否則每個鍵都引用自己，全部通過），
  但必須**包含** `dictionaries.ts`（`LABEL_KEYS`、`SERVER_CODES` 是真正的引用點）。
  一開始把兩者都排除，結果 359 個鍵全部被報成「定義了卻沒被引用」。

它刻意**只**抓漢字（`U+3400–4DBF`、`U+4E00–9FFF`、`U+F900–FAFF`，加上
CJK 標點 `U+3000–303F`），**不含**全形符號區（`U+FF00–FFEF`）——那個區塊
大半是標點與全形拉丁字母，單獨一個 `；` 不是「未翻譯的內容」，
為它設計迂迴寫法對讀者毫無意義。真正值得讓建置失敗的是漏掉的漢字。

**但它只掃前端。** 後端把一句中文塞進 `detail=` 時，前端字典再完整也救不了，
而上面這支腳本完全看不到（`docs/AUDIT-2026-09-26.md` B6）。
因此後端另有一支 `backend/scripts/check_no_hardcoded_cjk.py`，
以 AST 掃描「位置會送到客戶端的字串」：

- 抓 `detail=` / `title=` / `body=` / `message=` 這幾個**呼叫關鍵字**，
  以及同名的 **dict key**（`{"detail": ...}`，WS 訊框與原始回應用這種寫法）。
- **放行**註解與 docstring（那是給下一個讀者看的，不是給使用者的）、
  **放行** `seed.py`（種子資料本來就該長得像真實 zh-HK 使用者）、
  **放行** log 呼叫（log 是給維運看的）。
- 已知限制：賦值給變數後再傳進 `detail=` 的情況抓不到（不做資料流追蹤）。
  實際出現過的形態是「字面值直接寫在呼叫裡」，那會被抓到。

## 13A. 前端資料擷取 hook（`src/hooks/`）

本專案**沒有**資料擷取庫（技術債 #7，刻意）。代價是每個大頁面都得自己寫
fetch／生命週期簿記，而這正是 F2／F3 那類 bug 的來源。折衷做法是把最複雜的兩塊
抽成 hook，**頁面只留狀態與呈現**：

| Hook | 來源頁面 | 負責 |
|---|---|---|
| `useRoomSocket` | `app/chat/page.tsx` | 單一房間的 WebSocket 連線、心跳、訊息串、輸入中、線上人數 |
| `useTravelHistories` | `app/profile/page.tsx` | 足跡清單的載入／新增／刪除與其 busy 狀態 |

### 13A.1 為什麼用 ref 讀 callback，而不是列進依賴

連線類 effect **只能**依賴 `roomId`／`profileId`。callbacks（`resolve`、`onHistory`、
`onNotice`）與 resolver 都經由 **ref** 讀取：

```ts
const resolveRef = useRef(resolve);      // 每次 render 更新
useEffect(() => { resolveRef.current = resolve; }, [resolve]);

useEffect(() => { …建 socket… }, [roomId]);   // ← 沒有 t、沒有 callback
```

若把 `t` 列進依賴（`react-hooks/exhaustive-deps` 會這樣建議，而它一般來說是對的），
**切換語言就會重建 WebSocket**。所以這裡用的是刻意例外：把會變的東西放進 ref，
讓 effect 只對真正代表「換了一個資源」的值反應。

### 13A.2 hook 內保留的守衛

抽 hook 時**沒有**把 F2／F3 的守衛留在頁面裡——否則修好一次、下次改頁面又漏。
守衛跟著邏輯走進 hook：

- `useRoomSocket`：每個 handler 都先過 `isCurrent()`（舊 socket 的 `onclose`
  不得覆寫新房間狀態）；`openedSockets` 是 `useRef(new WeakSet<WebSocket>())`
  ——**per-connection**，不是全模組共用。
- `useTravelHistories`：`const seq = ++requestId.current` 丟棄過期回應；
  `add`／`remove` 各有 busy guard。

兩支對應的建置期檢查（`check:ws-guard`、`check:profile-history`）也**一併改指向 hook**——
它們讀的是原始碼，所以檔案一搬就得跟著搬，否則檢查會安靜地變成 0 個守衛（看起來像通過）。

## 15. 物件儲存的命名空間（`services/storage.py`）

物件鍵一律由 `build_key()` 產生，格式是 `{prefix}/{uuid4().hex}.{ext}` ——
**檔名完全由伺服器決定**，唯一的可變部分是 `prefix`。

`prefix` **不再來自請求 body**（原設計讓客戶端自選，已於 B4 移除）。
`PresignRequest` 現在只有 `content_type`，`prefix` 由伺服器推導為
`profiles/{profile.id}`。理由見 `docs/AUDIT-2026-09-26.md` B4：一個不存在於
schema 的欄位無法被偽造，這比在 router 端驗證更徹底。

在此之上仍有三道防護，缺一不可：

1. **`_validate_prefix()` 白名單**。`storage.py` 內層接受
   `("uploads", "avatars", "trips")`，或精確符合 `^profiles/<uuid>$` 的字串。
   這道防護留在 `storage.py` 內層而非 router，否則任何未來的呼叫點都會
   重新暴露同一個問題。
2. **儲存桶本身即邊界**。物件鍵只存在於 `S3_BUCKET` / `LOCAL_UPLOAD_DIR` 之內，
   且 `presign_put` 產生的是**單一 key + 單一 content-type** 的簽名 URL ——
   拿到網址的人只能寫那一個鍵，不能改路徑、不能改型別。跨租戶的隔離靠桶的
   存取政策，不靠前綴的命名習慣。
3. **本機後備路徑有獨立的遍歷防護**。`_local_path()` 以
   `Path.resolve()` 正規化後檢查是否仍位於 root 之下，因此 `../` 之類的鍵
   即使傳進來也會被拒。

> 注意：presign 只支援 S3 後端，所以第 3 道防護**在 presign 路徑上到不了** ——
> 這正是 B4 不能只依賴它的原因。`_validate_prefix` 必須是**獨立**的一道，
> 不能因為「`_local_path()` 已經擋了」就省略。

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

---

## 15. 城市參考表（`cities`）與選擇器

### 15.1 為何城市是「選單」而不是文字欄位

配對引擎以**字串逐字**比較目的地城市（`services/matching.py`），所以 `Osaka`、
`osaka`、`Ōsaka`、`大阪` 是四個不同的值。使用者若輸入了與對方不同的拼寫，
配對**靜默地**永遠不成立 —— 他看到的是一個較低的分數，而沒有任何線索說明原因。

因此城市只能從一份已知集合中挑選。這不是 UX 偏好，是配對正確性的前提：
**無法驗證的輸入不能參與精確比對。**

### 15.2 為何是本地資料集而不是地理編碼 API

Google Geocoding API 的條款限制快取不得超過 30 天、要求結果必須顯示於 Google 地圖、
且禁止大量地理編碼以建立儲存資料集 —— 三者都與「建立一份可離線查詢、可長期保存的
城市參考表」直接衝突。GeoNames `cities5000`（CC BY 4.0）沒有這些限制，
也沒有運行時故障模式。署名要求見 `docs/ATTRIBUTION.md`。

### 15.3 只保留 19 欄中的 10 欄

丟棄 `cc2`、`elevation`、`dem`（對本用途無用）與 `alternatenames`
（每列可達 10,000 字元，會讓資料表膨脹數倍而無人讀取）。
`country_name` 與 `admin1_name` 由代碼查表展開後**反正規化**儲存，
避免列表查詢要再 join。

### 15.4 搜尋比對 `asciiname`，且必須帶 `COLLATE NOCASE`

14,419 個顯示名稱帶有重音，而使用者通常以純 ASCII 鍵盤輸入。
比對 `name` 會讓 `Ōsaka` 找不到。

但把 `LIKE` 用在沒有 collation 的欄位上會讓索引失效。這是**實測**而非推論的：

```
WHERE name LIKE 'osa%'          -> full table SCAN
WHERE asciiname LIKE 'osa%'     -> index range SEARCH
```

200 次查詢於 69,740 列的資料表上：**1,350 ms vs 30 ms（44x）**，且差距隨資料量成長。

> **為何 collation 放在欄位而不是索引表達式**：SQLAlchemy 無法可靠地 diff 表達式索引 ——
> autogenerate 會把儲存的索引回報成「無欄位」，`alembic check` 於是永遠因幽靈漂移而失敗。
> 放在欄位上時，metadata 與資料庫一致，漂移檢查才有意義。

### 15.5 席次加成優先於人口

`case()` 表達式是**主要**排序鍵，排在人口之前：

| feature code | 加成 | 意義 |
|--------------|------|------|
| `PPLC` | 3 | 國家首都 |
| `PPLA` / `PPLA2` … | 2 | 一級／二級行政中心 |
| 其他 | 0 | 一般城鎮 |

效果：`San Marino`（首都，人口 4,500）排在美國的 `San Marino`（人口 13,464）之前。
行政中心比同名的較大城鎮更可能是使用者指的那一個。

> **測試這個加成需要「兩個排序鍵互相矛盾」的資料。** 若種子資料中首都同時也是人口
> 最大的，把 `PPLC:3` 改成 `2` 不會改變任何排序 —— 突變**不可觀測**，測試看起來
> 卻仍「通過」。`tests/test_cities.py` 因此刻意挑選**首都小於同前綴行政中心**的
> 前綴（`San Mar`、`Fun`、`Abb`），並在斷言中明確要求人口反轉。
> 這條規則已用負向驗證確認：停用加成 → 對應測試必須變紅。

### 15.6 為何 `city_id` 必須在寫入時驗證

一個**無法解析**的 `city_id` 比拼錯的字串更糟：該列看起來是填好的（非 `NULL`），
於是每一層都當它有效，而配對永遠不成立 —— 一個沒有錯誤訊息的錯誤答案。

`cities.py::resolve_city_id()` 因此在寫入時就查一次參考表，不存在則回 **422**，
三個寫入路徑（`POST /trips`、`PATCH /trips/{id}`、`POST /profiles/me/histories`）
共用同一個 helper，避免三處各自實作而產生分歧。

### 15.7 `SET NULL` 而非 `CASCADE`（關鍵取捨）

`trip_posts.city_id` 與 `travel_histories.city_id` 指向 `cities.id`，動作為
**`ON DELETE SET NULL`**。

`CASCADE` 在這裡是**災難性**的：重新匯入 GeoNames（`scripts/import_cities.py`）
會替換參考表的列。若動作為 `CASCADE`，一次匯入就會**刪掉使用者的行程與足跡**。

`SET NULL` 的語意才是對的 —— 城市消失不應讓行程消失，只是失去可驗證的座標鍵；
顯示用的 `destination_city` 字串仍在，行程本身完整。

> 已用測試實證：`test_deleting_the_city_row_does_not_delete_the_trip` 刪掉城市列，
> 斷言行程存活、`city_id` 變 `NULL`、`destination_city` 不變。
> **不是斷言 FK 的定義，而是實際刪一列再看結果。**

### 15.8 前端選擇器：受控 `open` 與過期回應防護

三個不寫下來就會重犯的點：

1. **Radix `Popover` 不會因為點了裡面的按鈕而關閉。** 選完一個城市後彈層會留在畫面上，
   使用者的下一個點擊會把它關掉而不是做原本想做的事。必須用受控的 `open` state 並在選取後
   `setOpen(false)`。
2. **`onOpenAutoFocus` 必須被阻止。** 否則焦點會落在第一個建議上，按 Enter 就會選中
   一個使用者從未挑選的城市。焦點應給搜尋框。
3. **回應可能亂序。** 慢的 `os` 回應可能在快的 `osaka` 回應之後落地，用過期清單覆蓋正確的。
   以遞增的 request counter 丟棄被取代的回應。

### 15.9 E2E 需要種子城市（`e2e/global-setup.ts`）

`app.main` 的 lifespan 只建立資料表、不種資料，而生產的 `cities` 來自一個 5.7 MB
的 dump —— 不能當成跑測試的前提。少了這一步，`cities` 是空的，選擇器沒有東西可選，
任何想選城市的 spec 都只能跳過（一種「測不到東西的全綠」）。

`playwright.config.ts` 的 `globalSetup` 因此跑 `python -m app.seed`，
它會插入 `DEMO_CITIES` 的**真實 GeoNames 列**，且是幂等的 ——
E2E 的資料庫檔在本機執行之間會保留，所以重複執行必須安全。

### 15.10 地圖：`CityMapLoader` 與 `CityMap` 的分工

`GET /cities/{city_id}` 提供解析單一 id 的端點，前端據此把 `trip.city_id`
換成座標。兩個元件的分工不是風格問題，是**渲染環境**問題：

| 元件 | 是否可 SSR | 職責 |
|------|-----------|------|
| `CityMapLoader.tsx` | 是（server-safe） | 解析 `city_id`、決定值不值得畫地圖、失敗時降級為文字 |
| `CityMap.tsx` | **否** | 實際的 Leaflet 地圖，經 `next/dynamic` + `ssr: false` 載入 |

Leaflet 在 module scope 讀 `window`／`document`，SSR 時 import 就會拋錯。所以
「要不要畫」與「怎麼畫」必須分開 —— 前者可以安全地在伺服器端決定。

**降級契約（`docs/SECURITY.md` §2.3）：地圖只是呈現，任何一部分都不能弄壞頁面。**
三種失敗都不向使用者顯示錯誤：

1. `city_id` 為 `NULL`（欄位可選）→ 整個元件回 `null`；
2. id 解析不到 —— GeoNames 重新匯入會替換參考列，而 FK 刻意是 `ON DELETE SET NULL`，
   所以**行程可以失去城市**。此處的 `404` 是**預期結果，不是例外**；
3. 其他請求失敗，或圖磚一直載不出來。

三種情況下行**程詳情頁仍以自己的文字**顯示城市與國家，所以「去哪裡」永遠說得出來。
那段文字才是主要呈現，地圖是附加的。

其餘兩個實作決定：

- **`CITY_ZOOM = 10`**（約「一座城市與其近郊」）。座標是 GeoNames 的**城市中心**，
  不是地址；更近的縮放會暗示我們沒有主張的精確度。`scrollWheelZoom` 關閉，
  否則嵌入地圖會劫持頁面捲動 —— 那是最常見的「地圖壞掉了」來源。
- **`role="img"` + `aria-label="城市, 國家"`** 作為可存取名稱。**不要再加一份
  `sr-only` 的城市文字**：標題已經寫了城市與國家，重複的副本會讓
  `getByText("Tokyo")` 這類查詢變成 ambiguous（實測 strict-mode violation，兩個元素）。

### 15.11 E2E 的資料庫必須在 `globalSetup` 重設

`app.seed` 的幂等性只保證「再跑一次不會出錯」，**不保證測試環境乾淨**：
`app.main` 的 lifespan 只 `create_all`、從不 drop，所以 E2E 的 `.db` 檔會跨次執行
累積真實歷史。實測在一次失敗排查中已累積 **119 位使用者、52 筆稽核列**。

代價是隱形的：斷言「恰好一列」的測試、或依賴分頁與排序的測試，其行為會變成
**自身歷史的函數** —— 今天綠、明天紅，而程式碼沒動。因此 `globalSetup` 在種子之前
先刪掉 `E2E_DB_FILE` 與它的 `-wal` / `-shm` 側car（只刪主檔會留下先前執行的已提交頁）。

> **順序陷阱**：Playwright 的 `globalSetup` 跑在 `webServer` **之前**，所以正常路徑下
> 刪檔是安全的。但 `E2E_NO_WEBSERVER=1` 表示後端由外部啟動、已持有舊檔案 handle，
> 這時刪檔會留下一個已開啟但無目錄項的 inode。刪除因此**只在
> `E2E_NO_WEBSERVER !== "1"` 時執行**，而種子照跑（它幂等，且選擇器需要它的參考列）。

### 15.12 稽核清單的過期回應防護

`admin/page.tsx` 的 `AuditTrail` 與 `CityPicker` 用同一個 `requestId` ref 守衛，
理由相同但觸發路徑不同：

**`reactStrictMode: true` 在開發模式下會雙重觸發 mount effect**，於是同一組參數的
`GET /admin/audit-logs` 會發兩次。當回應亂序落地（慢的先發、後到），畫面會被
**較舊的清單**覆蓋。使用者實際看到的是：管理員剛處理完一筆檢舉、切到稽核頁，
**自己那次操作的稽核列不見了** —— 而資料庫裡有。

> 已從 trace 實證：兩次 `GET /admin/audit-logs` 相隔 400 ms，DB 確認
> `REPORT_STATUS_CHANGED` 存在，而 DOM 只有 3 列且不含它。

**為何這個守衛的負向驗證連錯兩次（值得單獨記下）：**

| 嘗試 | 做法 | 結果 |
|------|------|------|
| 1 | 停用守衛、跑整套 | **1/3 釘住** |
| 2 | 加測試，斷言「篩選後的清單為空」 | 停用守衛**仍通過** |
| 3 | 對被篩選的請求加 1200 ms 路由延遲，且**同時**斷言新列出現與舊列消失 | **3/3 釘住** |

第 1 次錯在**賽跑取決於時序**：沒有延遲時舊回應通常不會最後落地，突變因此
**不可觀測**。第 2 次錯在斷言**什麼都沒釘住** —— 新回應落地時畫面本來就是空的。

> **通則**：突變測試只對**決定性**的行為有效。對時序相依的賽跑，
> **延遲才是讓突變可觀測的東西**；斷言必須同時涵蓋「新狀態成立」**與**
> 「舊狀態被拒絕」兩側，否則寫出的是永遠通過的測試。
>
> 附帶的教訓：第 2 次寫出的測試是**假的安心**。一個不改變結果的斷言，
> 比沒有斷言更糟 —— 它會讓人以為覆蓋到了。
>
> `e2e/admin.spec.ts` 的 `稽核紀錄：慢的舊回應不得覆蓋新回應` 就是第 3 次的做法；
> 負向驗證 harness 在 `frontend/var/_negate_audit_race.py`。

---

## 16. WebSocket 跨副本扇出（`ws/pubsub.py`）

### 16.1 為何房間狀態不能在行程記憶體

`ConnectionManager._rooms` 是 `room_id → {profile_id → WebSocket}`。WebSocket 物件
**無法跨行程共享**，所以房間表本身留在行程內是對的；錯的是**扇出**：`broadcast()`
只走自己這張表。

單副本看不出問題。**`uvicorn --workers 4` 就是四行程**，此時 A 副本的使用者與 B 副本
的使用者互相看不見 —— 訊息送出後拿到正常 ack、資料列也寫入了資料庫，卻從未送達
房間的另一半，**且不報錯**。`is_member_online()` 同樣開始說謊。

> 這是本專案**唯一**一類「外部行為完全正常、只有一個使用者覺得對方沒回話」的缺陷，
> 因此它需要的是**主動記錄**（見 16.3），而不是靠測試發現。

### 16.2 分工：`broadcast` 與 `deliver_local`

兩者的區分是這個設計的核心不變量：

| 方法 | 做什麼 | 誰呼叫 |
|------|--------|--------|
| `broadcast()` | 本地投遞 **+ 發布到 channel** | 產品程式碼（`ws/routes.py`） |
| `deliver_local()` | **只**本地投遞，永不發布 | `on_remote_frame()`（收到遠端訊息時） |

若 `on_remote_frame` 呼叫 `broadcast`，兩個副本會把同一則訊息**互相無限轉發**。
這不是理論風險：naive 的實作（「收到就再廣播一次」）必然產生訊息風暴。
`test_remote_frame_is_not_republished` 就是釘住這條線。

### 16.3 降級契約：Redis 不可用時退回單程序

與 `services/kv.py` 同源（同樣的短逾時、同樣的斷路器、同樣的惰性連線），理由也相同：
「先試 Redis、失敗就退回」會讓**每次**呼叫都付一次連線失敗成本，而 Windows 上
`localhost` 同時解析到 `::1` 與 `127.0.0.1`，不可達的 IPv6 要先逾時才試 IPv4，
一次約 4 秒。

**決定要不要記錄降級的是 `publish()` 的回傳值，不是 `enabled`。**
`enabled` 是不碰網路的**預測**，冷啟動且 Redis 掛掉時它仍讀 `True`（因為斷路器還沒被觸發）
—— 用 `enabled` 判斷會讓 warning **永遠不觸發**。回傳值才是「剛剛真的發生了什麼」。

降級時**本地投遞照常**，只失去跨副本投遞。反過來（整條 `broadcast` 失敗）會讓
Redis 故障升級成**總聊天故障**，連正常連線的使用者都收不到訊息。

### 16.4 兩個容易寫錯的細節

- **自有回音**：Redis 會把發布者自己的訊息送回來。每個 transport 有 `instance_id`，
  `_dispatch` 丟棄 `origin == self.instance_id` 的訊息 —— 否則每則訊息對發布者的每個
  socket **重複投遞一次**。
  > `instance_id` 是**可注入的**，不是直接讀模組常數。同一行程內兩個 transport
  > （測試會這樣做）若共用 id，會互丟對方的訊息當成自己的回音 —— 那是只在拆行程後
  > 才出現的靜默失敗。
- **`exclude` 隨訊息跨副本**：`exclude` 是**發送者**的 profile，而發送者的 socket
  可能在另一個副本。若只在本地套用 `exclude`，使用者的 socket 一旦落在別的 worker，
  就會看到自己的 typing 指示 —— 因為 `--workers 2` 的分配是非決定性的，
  這是一個**間歇性**缺陷，極難重現。

### 16.5 訂閱建立的競態與「已訂閱」狀態

`start()` 會**短暫等待**（上限 0.5 秒）訂閱建立完成。這不是為了正確性（發布在訂閱前的
訊息仍會本地投遞），而是為了讓「正在監聽」與「還沒開始監聽」**可被區分**。

`subscribed`（事實）與 `enabled`（預測）是兩個不同的問題：
連線中斷到重新訂閱之間，副本是**聽不見的**（deaf window），此時 `enabled` 仍為 `True`。
`_listen_forever` 因此是**迴圈**而非單次 `async for` —— 掉訂閱後副本照樣接受 socket、
照樣發布，但收不到任何東西，是「看起來健康卻半聾」的狀態。

### 16.6 交付是非同步的（測試必須讓出執行權）

`publish()` 把訊息交給 Redis 就返回；**接收端由另一個 listener task 投遞**。
因此 `await broadcast()` 返回的那一刻，其他副本**尚未**投遞完成。
`tests/test_ws_fanout.py::_settle()` 就是為此而存在 —— 這不是測試瑕疵，
而是設計的性質：**產品程式碼不得依賴「廣播返回即已送達」**。

### 16.7 測試的誠實邊界

`tests/test_ws_fanout.py` 用**行程內 broker**取代 Redis（此環境無 Redis 服務）。
它足以釘住**我們的邏輯**：信封格式、自有回音過濾、`exclude` 傳遞、本地投遞、降級。
它**不足以**證明 Redis Pub/Sub 本身可用。

未覆蓋、需要真實 Redis 的整合測試：認證、真實伺服器上的 channel 命名、
**伺服器端斷線後的重連**、斷路器對真實不可達主機的行為。

> 撰寫這個 fake 時的第一版把 `psubscribe("prefix*")` 當成精確比對，
> 於是**每一條跨副本測試都失敗**，而生產程式碼其實是對的。
> **一個不模擬被取代物語意的 fake，測的是 fake。**

### 16.8 每人配額也要跨副本（#3 + #6 殘留）

**扇出**解了「訊息送不到」，但**配額**仍是每副本計數。兩個地方：

| 限制 | 舊行為 | 現行為 |
|------|--------|--------|
| WS 訊息 2 msg/s／人 | `ConnectionManager._buckets` 行程內 dict → N 副本 = 2N msg/s | `kv.DistributedTokenBucket`，token 存共享儲存 |
| HTTP 5 次／分鐘／IP | slowapi `memory://` 儲存（Redis 不可用時的降級）→ N 副本 = 5N 次／分 | `rate_limit.SharedCounter`，`INCR`+`EXPIRE` 走同步 redis client |

三者同源：**「每人配額」是全域概念，不是行程概念**。故共用 `services/kv.py` 的
分散式原語，而非各自實作一次。

**為何 HTTP 用同步 client。** slowapi 在 `async_wrapper` 內**同步**求值
（`__evaluate_limits` → `storage.incr`，見 `slowapi/extension.py`），
所以計數無法 await 非同步 client。`redis` 套件同時提供同步與非同步版本，
同步版可在執行中的 loop 內直接呼叫；單次讀寫為次毫秒級的本機操作，
故可接受阻塞。慢於此的做法都不該用這條路。

**降級契約（與扇出同構）。** Redis 不可用時**仍降級為每副本計數**，
而非 fail-open：斷流期間放行等於配額**完全消失**，比放大 N 倍更糟。
但會寫一則 warning **明說配額被放大**，且每秒最多一次。

**為何 fixed window。** `INCR` + `EXPIRE` 是不用 Lua 即可在單一 key 上
原子完成的最小操作。固定視窗允許視窗邊界的 2× 突發，但這裡防的是**持續猛打**，
不是精確計量 —— 換取滑動視窗要多一套 key 佈局或 Lua，機器比這個守衛需要得多。

**測試的誠實邊界（比扇出更微妙）。** `tests/test_shared_quota.py` 用
**各自獨立的 `kv` 模組**（`importlib` 從同一原始檔載入）建模副本，
共用一個 fake Redis。

> ⚠️ **第一版測試用「同一行程的兩個 manager」當兩個副本 → 5 個突變全綠。**
> 同一行程的兩個物件**共享模組全域狀態**，所以連「每副本 dict」都看起來是共享的。
> **兩個物件不是兩個副本；副本是兩個行程。**
>
> ⚠️ **patch `_redis` 會繞過斷路器** —— 斷路器檢查就在 `_redis` 內。
> 要驗斷路器必須 patch 更低一層的 client class（`redis.Redis.from_url`）。
>
> ⚠️ **`conftest.py` 把 `RATE_LIMIT_ENABLED` 設為 false**，而 `_redis()` 在
> 限流關閉時直接短路 → 若不先打開，測試跑不到 Redis 卻斷言「0 次重試」，
> **看起來像斷路器完美運作，其實 Redis 從未被查詢**。

未覆蓋、需要真實 Redis：key 在高負載下的過期、副本間時鐘偏移、真實 failover。

## 17. 多副本前置清單結案（#8／#21／#22a／#22b）

這四項與 §16.8 是**同一個根因的四個面孔**：

> **一個「每個行程各算一次」的量測，被當成整個部署的事實。**

單副本時，per-process 與全域完全等價 —— 這正是它們長期只被記錄、沒有被修的原因。
`uvicorn --workers 4` 就會同時觸發全部四項。

### 17.1 共用原語：`kv.DistributedSemaphore`

`DistributedTokenBucket` 管的是「單位時間內幾次」，semaphore 管的是「同時幾個」。
兩者都必須存在於 Redis，否則 N 副本就是 N 倍。

| | 憑證狀態 | 全域上限 | 降級方向 |
|---|---|---|---|
| `DistributedTokenBucket` | `tokens\|updated` 字串 | — | 每副本（額度被放大） |
| `DistributedSemaphore` | 一個計數 key（`INCR`／`DECR`） | `limit` | 每副本（上限被放大） |

**`INCR` 先做、再判斷**，不是先讀再寫：後者會讓兩個副本都讀到 `limit - 1`，雙雙取走最後一個憑證。

**TTL 是洩漏閥，不是過期語意。** 持證的副本死掉就沒人 `DECR`，計數會單調爬到「永遠滿」。
每次取得都刷新 TTL，所以活著的部署維持一個長命 key，而被遺棄的 key 會老化 → 上限自癒。
刻意如此：修法（每個憑證一個 key ＋ 持有者註冊表）遠比這個複雜，而「暫時偏寬」遠比洩漏後
永久卡死安全。

### 17.2 #8：Argon2 的上限是**記憶體預算**，不是吞吐旋鈕

`PASSWORD_HASH_MAX_CONCURRENCY` 的意義是「整個部署同時最多跑幾個 64 MiB 的雜湊」。
只設 anyio 線程池 = 只設了**每個行程**的上限，於是 `8 × 副本數` 才是實際峰值。

兩層，不可互換：

1. `kv.DistributedSemaphore`（**跨副本**）—— `security.py::_hash_slot()`
2. anyio 線程池上限（**每行程**後備）—— `main.py` lifespan

第 2 層才是最後防線：**Redis 掛掉時它仍然生效**。這也決定了降級契約的方向：

> **取不到憑證時等待最多 2.5 秒，然後「不算數」地放行 —— 絕不拒絕登入。**

配額的失敗方向必須是「限得更鬆」，不能是「功能壞掉」。這與 §16.8 的
degrade-not-fail-open 是同一條原則的兩面：**存在性不能因依賴故障而消失**。

啟動時若 Redis 不可達，`main.py` 會**用記憶體算術告警**（`8 × 64 MiB`），
而不是只說「Redis 不可用」—— 因為設定值本身看起來永遠是對的，錯的是它的**作用範圍**。

### 17.3 #21：TOCTOU 需要**兩層**，任一層單獨都不夠

```
count = SELECT COUNT(*) WHERE status='ACCEPTED'   -- 讀
if count < looking_for_count: UPDATE ...          -- 寫
```

| 只做 | 擋不住 |
|---|---|
| `SELECT ... FOR UPDATE` | 無列鎖的方言（SQLite） |
| 條件式 UPDATE（CAS） | 兩個**不同**申請同時通過容量檢查 |
| **只對 ACCEPTED 取鎖** | **同一申請的 ACCEPT 與 REJECT 互相交錯** |

所以兩者都要：

1. 對 trip 列取列鎖（PostgreSQL `FOR UPDATE`；SQLite 用 no-op write 逼出寫鎖），**再重讀**容量（鎖之前讀的值必然是舊的）
2. 狀態轉移寫成 `UPDATE ... WHERE status='PENDING'`，**用 `rowcount` 仲裁**

`rowcount != 1` → rollback → 重讀實際結果再回報（同決定 → idempotent 200，異決定 → 409）。
**先寫狀態再做容量檢查是錯的**：請求回報失敗、資料列卻是 ACCEPTED，超收沒有任何訊號。

#### 17.3.1 列鎖必須**無條件**取得，不能只掛在 ACCEPTED 分支

第三行是實作後的修正。原本列鎖寫在 `if decision == ACCEPTED:` 之內（因為只有容量檢查需要它），
於是 REJECT 完全不設防。`NullPool` 讓每個請求各有自己的連線，兩者都讀到 `status == PENDING`
就都走到 CAS，**後寫的那個決定勝出**。

實測（`probe_cas.py`，12 輪）：**2/12 出現 `codes=[409, 200] final=['REJECTED']`** ——
ACCEPT 回報 409，資料列卻停在 REJECTED，即 accept 宣告成功卻不持久。

修法：把列鎖提到 `if decision == ACCEPTED:` **之前**，兩個決定共用同一把鎖。
ACCEPT 分支內只留下「重讀容量」，不再重複取鎖。

### 17.4 #22a：斷路器狀態外部化 —— 但**不可以依賴它要保護的東西**

`_BreakerState` 預設仍是行程內變數（單副本足夠，且**Redis 掛掉時仍然可用**）。
`set_breaker_backend()` 是可選接縫；`RedisBreakerBackend` 把窗口發布到一個 Redis hash。

關鍵設計約束：

> **斷路器必須能在 Redis 掛掉時運作。需要 Redis 才能知道 Redis 掛了的斷路器，比沒有更糟。**

因此共享後端是 **publisher，不是真相來源**：

- 讀取一律包 try/except，失敗退回本地狀態
- `is_open()` 在查詢後端**之前**就已可能因本地窗口回 True
- 後端整個拋錯時，本地斷路器照常運作（有測試）

啟動時**先 ping 成功才安裝** —— 否則每次斷路器讀取都會先撞一次連線逾時，
把一個延遲優化變成延遲來源。

### 17.5 #22b：seed 需要互斥，不只是幂等

逐列存在性檢查只對**循序**重跑有效：併發時每個副本都在任何 commit 之前讀到「不存在」，
然後全部插入。PostgreSQL advisory lock 讓整個 seed 互斥。SQLite 沒有此機制 →
**誠實的 no-op**，不假造保證。

### 17.6 這一批的測試教訓（沿用 §16.8 且再次應驗）

> ⚠️ **同一行程的兩個物件不是兩個副本。** 副本要用 `importlib` 從**同一原始檔**
> 載入獨立模組，各有自己的 `_memory`／`_redis_client`／`_breaker`。
>
> ⚠️ **`kv` 與 `rate_limit` 各有自己的 `_breaker`。** 只重設一個，會讓斷路器測試因為
> 「被永久停用」而不是「被觸發」而通過 —— **假通過，且移除斷路器仍然全綠**。
>
> ⚠️ **Windows 上的原始檔是 CRLF。** 用 `\n` 寫多行 anchor 永遠不匹配 →
> 突變被報成 `ANCHOR-MISS`。**`ANCHOR-MISS` 不是「通過」，是「沒測到」**，
> harness 要把它與 `NOT-PINNED` 分開顯示（本案 8 個突變，首輪 3 紅 5 錨點失配）。
>
> ⚠️ **多行 anchor 一定要 `assert count == 1` 且寫入後讀回驗證。**
>
> ⚠️ **「未釘住」要能重現才算發現。** 突變 I 曾被報成 PINNED，但同一個突變單獨重跑
> 6 次有 4 次全綠 —— 那次「PINNED」是賽跑剛好發生，不是守衛被釘住。
> harness 現在對每個突變**重跑 `REPEATS` 次**，只有真的紅才算 PINNED。
>
> ⚠️ **不要斷言一個不存在的順序保證。** `test_concurrent_contradictory_decisions_on_one_application`
> 原本斷言「同時發出的 ACCEPT 一定贏」，實測 **~17% 會是 REJECT 贏**（`codes=[409, 200]`）——
> 兩個請求同時發出，勝負由排程器決定，沒有任何機制規定誰先。
> 要斷言的是**不變式**：只有一個贏、資料列等於**贏家**的決定、輸家被告知輸了。
>
> ⚠️ **聲明「不可觀測」的突變是對 harness 的負向控制。** 若它竟然讓測試變紅，
> 代表標籤是錯的 —— 而且代表先前那次把它報成 PINNED 的那一輪，報的是 flake。
> harness 對這種 case 只接受 NOT-PINNED，並排除在分母之外（本案 9/9，另 1 例聲明不可觀測）。
>
> ⚠️ **量測期間不要有其他東西在動工作樹。** 突變 harness 會**就地**把突變寫進原始檔；
> 同時跑 pytest 會 import 到半突變的樹，製造出「8 次有 5 次失敗」的幻覺結果，
> 樹恢復乾淨後即消失。**看到無法解釋的紅，先查是哪個行程在跑。**

未覆蓋：真實 Redis 上的 semaphore 競爭、`pg_advisory_lock` 在兩個真實連線上的行為、
PostgreSQL 的 `FOR UPDATE` 序列化。這三項都需要真資料庫，屬整合測試範圍。

---

## 18. #6 presence：房間成員必須有**存活語意**，不能只是一個共享集合

### 18.1 缺陷的形狀

`ConnectionManager` 持有 socket，而 **socket 無法跨行程**。所以
`online_profiles` 只能回答「連到**本副本**的人是誰」。`uvicorn --workers 2` 之下，
成員分散在兩個 worker 的房間，`presence` 訊框只報出其中一半 —— 而且**哪一半由負載
平衡器決定**。這比「不完整」更糟：它是**不確定**的，同一個房間在不同請求下可能
報出不同的名單，且**不拋任何錯誤**，UI 只是顯示錯的清單。

### 18.2 為什麼不能只是「一個共享 SET」

最直覺的修法 —— 每個房間在 Redis 放一個 `SADD` 集合 —— 在生產會以一種**只有生產
才會出現**的方式壞掉：

**被 SIGKILL 或當掉的副本永遠不會呼叫 `disconnect`。** 它的成員會**永遠**留在集合
裡。更糟的是，系統**沒有任何資訊**可以分辨這種鬼影與一個安靜的真人 —— 兩者在集合
裡長得一模一樣。沒有任何簿記能救，因為要記的資訊根本沒被產生。

因此存活語意必須由**唯一有能力提供它的實體**提供：副本自己。它必須不斷地說
「這些是我的，我還活著」。

### 18.3 採用的設計

    tripmate:presence:{room}:{replica}   ->   "profile_a|profile_b"   (TTL = stale_after)

**每副本一個帶 TTL 的條目**，由該副本的心跳刷新；**讀取是未過期條目的聯集**。
死掉的副本停止刷新，整個條目在一個 stale 窗口內過期 —— **鬼影由構造界定，而不是
靠記帳**。

三個關鍵細節：

- **心跳重寫整份名單**（`delete` + `sadd` + `expire` 在同一個 pipeline），不只是延長
  TTL。這才是讓聯集讀**自我修復**的原因：如果某次 `leave` 丟了（Redis 剛好掛掉那一次、
  或 teardown 跑到一半行程就死了），下一次心跳就會發布權威的本地名單，把過期成員清掉。
  只延長 TTL 的話，那個成員會被**每一次後續心跳**持續續命，永遠不會消失。
- **`online_profiles` 改為 `async`**，這是刻意的：答案現在真的在別的地方，呼叫端
  **不應該有能力**把「本副本的切片」誤當成整個房間。同步簽名會讓這個錯誤變成打字錯誤。
- **`is_member_online` 刻意保持同步、本副本語意。** 它回答的是「我現在能不能投遞給
  他們？」，這是關於**本行程**的路由問題。問「這個人在這個房間裡嗎？」（也就是
  `presence` 訊框要問的）要用 `online_profiles`。兩個問題不同，不該共用一個函式。

### 18.4 明文寫出的取捨

一個成員，若他的副本活著、但心跳停頓超過 `stale_after`，會短暫從 presence 消失，
直到下一次心跳。這是**刻意的**：`stale_after` 是**心跳的倍數**（45 秒 vs 15 秒），
不是 session 壽命。

- 短暫**缺席** = 外觀錯誤（美觀問題）。
- 永久**鬼影** = 正確性錯誤（系統說房間裡有人，但沒有）。

兩害相權，選前者。

### 18.5 降級契約：presence 永不拋錯

`online_profiles` 跑在聊天熱路徑上（**每次 join 和每次 leave**）。在那裡拋錯會為了
一個純資訊性的訊框而**弄壞 socket**。所以 Redis 不可用時 → 退回本副本名單，也就是
**修 #6 之前的行為**：**少報，不失敗**。

這與專案其它原語同一條紀律：*錯的答案必須是「更大的配額」或「更小的 presence」，
永遠不能是「失敗」*。

### 18.6 心跳任務不可活得比它的 event loop 久

一個在 import 期建立的 `asyncio.Task` 會綁到**第一個存在的 loop**（這個錯誤在本專案
已經付過 95 個測試的代價）。這次交出 presence 後立刻遇到它的變體：
`pytest -k "ws or room or presence"` → 33 passed 但 **6 個
「Task was destroyed but it is pending!」**，全部指向 `_heartbeat_loop`。原因是
`test_ws_fanout.py` 每個測試用 `asyncio.run()` 自建 `ConnectionManager`，從不呼叫
`shutdown()`，於是待處理的任務被 loop 的死亡沒收。

**兩層修法，缺一不可：**

1. **迴圈在最後一個房間清空時自行結束**（`_beat_once()` 回傳 `False`）。
   「有人記得呼叫 `shutdown()`」**不是保證** —— 測試直接建 manager，永遠不會呼叫它。
   讓任務的存續與它所維護的狀態綁在一起，正確性就不依賴任何人的簿記。
2. **心跳預設關閉**（`heartbeat_enabled=False`），只有應用程式 singleton 明示開啟。
   背景計時器只對長命伺服器有意義；隱式啟動會讓每個**只是用到** manager 的短命 loop
   背上取消一個它從未要求的任務的責任。

### 18.7 平滑重啟：entry 要及早清掉

`main.py` 的 lifespan `finally:` 呼叫 `await ws_manager.shutdown()`，**在**
`await pubsub.stop()` **之前**，並且逐一 `forget_replica`。理由是 rolling restart：
新副本上線時，舊副本的成員若不主動清除，會**與新副本的成員並存**直到 TTL 過期 ——
每個房間會短暫地把所有人都列**兩次**。

### 18.8 這一批的測試教訓

- **同一行程的兩個物件不是兩個副本**（§16.8 再現）。副本用 `importlib` 從同一原始檔
  載入獨立模組 —— 各自有自己的 `_memory`、`_redis_client`、`_breaker`，只共享假 Redis。
  這正是 `--workers N` 給你的東西。
- ⚠️ **用被測物件的「順路輸出」去驗證它，等於什麼都沒驗證。** 一個突變起初是
  NOT-PINNED：測試用 `manager.online_profiles()` 驗證 `connect` 有寫入 presence，
  但**本副本的讀取光靠 socket 表就會回傳該成員** —— 把 `join` 整個拿掉，測試照樣綠。
  正確做法是改由**另一個副本**（手上沒有任何 socket）觀察。**要驗證一個寫入，
  就要從看不到本地狀態的地方讀。**
- 突變清單 10/10 PINNED（`mutate_presence.py`）。涵蓋：移除聯集、`join` 不設 TTL、
  `leave` 刪整個房間鍵、心跳只延長 TTL、心跳不刷新 TTL、shutdown 不清理、
  presence 改為拋錯、manager 只讀本地、`connect` 不寫、`disconnect` 不清。
- ⚠️ Windows 原始檔是 CRLF，而 harness 的錨點用 `\n` 寫 → **`ANCHOR-MISS` 不是通過，
  是「沒有測到」**。harness 在讀取時正規化、寫回時還原，且錨點找不到就明說。

未覆蓋：真實 Redis 的 `KEYS` 延遲、複寫延遲下的聯集讀、跨主機時鐘偏移。目前用
`KEYS prefix*` 掃描（O(n) 但房間數是數十級），若房間數到數千級需改用 `SCAN`。


