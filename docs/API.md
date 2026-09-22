# Trip Mate — API Reference

Base URL：`http://localhost:8000`
所有端點前綴：`/api/v1`
互動式文件：`GET /docs`（生產環境自動停用）

**認證**：除 `/health`、`/api/v1/disclaimer`、`/auth/register`、`/auth/login`、`/auth/refresh`、
`/auth/logout`、`/auth/otp/*` 外，其餘端點皆需 `Authorization: Bearer <access_token>`。

> `/auth/logout` 刻意不要求 access token：登出時 token 可能已經過期，而「把已過期的 session 清掉」
> 正是它存在的理由。它只依賴 HttpOnly refresh cookie，且對未登入的呼叫回 `204`，不洩漏任何狀態。

**雙 Token 機制**
- **Access Token**（15 分鐘）：回傳於 JSON，由前端**只存放於記憶體**，不寫入 `localStorage`。
- **Refresh Token**（7 天）：`HttpOnly` + `Secure` + `SameSite=Strict` Cookie，
  `Path` 限定為 `/api/v1/auth`，因此不會隨一般 API 請求送出。

**錯誤格式**
```json
{ "detail": "Invalid credentials" }
```
驗證錯誤（422）為 FastAPI 標準格式：
```json
{ "detail": [ { "loc": ["body", "phone_number"], "msg": "...", "type": "..." } ] }
```
前端以 `errorMessage()` 統一攤平為單一字串。

---

## Meta

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/health` | 健康檢查 |
| GET | `/api/v1/disclaimer` | 平台免責聲明標準文案 |

---

## Auth `/api/v1/auth`

| 方法 | 路徑 | 限流 | 說明 |
|------|------|------|------|
| POST | `/register` | 5/min | 註冊（手機號 + 密碼 + 暱稱 + 兩項同意）→ 回 Access Token |
| POST | `/login` | 5/min | 登入（失敗一律回 `401 Invalid credentials`，防帳號列舉） |
| POST | `/refresh` | — | 以 Cookie 換新 Access Token |
| POST | `/logout` | — | 清除 Refresh Cookie |
| POST | `/password` | — | 修改密碼（需提供現有密碼） |
| GET | `/me` | — | 目前使用者資訊 |
| POST | `/otp/request` | 5/min | 發送手機驗證碼（`OTP_DEV_ECHO=true` 時回傳 `dev_code`） |
| POST | `/otp/verify` | 5/min | 驗證手機號碼，成功後 `is_verified=true` |

**註冊請求**
```json
{
  "phone_number": "+85291234567",
  "password": "Passw0rd123",
  "nickname": "Alice",
  "consent_privacy": true,
  "consent_terms": true
}
```
> 手機號須為香港格式 `+852` 加 8 位數字。密碼政策：至少 8 位，需含大小寫字母與數字。

**Token 回應**
```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 900 }
```

**OTP 請求 / 回應**
```json
// POST /otp/request  →  { "sent": true, "expires_in": 300, "dev_code": "123456" }
// POST /otp/verify   →  { "phone_number": "...", "code": "123456" }
```

---

## Users `/api/v1/users`

| 方法 | 路徑 | 說明 |
|------|------|------|
| DELETE | `/me` | 註銷帳號。Body：`{ "password": "...", "mode": "anonymize" \| "hard_delete" }` |
| GET | `/me/rooms` | 我參與的聊天室 ID 清單 |

> PDPO 合規：`anonymize` 保留統計資料但抹除可識別欄位；`hard_delete` 連鎖刪除所有關聯資料。

---

## Profiles `/api/v1/profiles`

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/me` | 我的完整檔案（含 `user_id`、`is_verified`） |
| PUT | `/me` | 更新檔案 |
| GET | `/{user_id}` | 他人公開檔案（接受 **user_id 或 profile_id**；被封鎖者回 404） |
| GET | `/{user_id}/histories` | 旅遊足跡（他人僅見 `is_public=true`） |
| POST | `/me/histories` | 新增旅遊足跡 |
| DELETE | `/me/histories/{entry_id}` | 刪除旅遊足跡 |
| POST | `/me/upload-url` | 取得 Pre-signed PUT URL。Query：`content_type` |
| POST | `/{profile_id}/block` | 封鎖用戶 |
| DELETE | `/{profile_id}/block` | 解除封鎖 |
| GET | `/me/blocks` | 我的封鎖清單 |
| GET | `/{user_id}/reviews` | 某用戶收到的評價（被封鎖者回 404；詳見下方 Reviews 段） |
| GET | `/{user_id}/reviews/summary` | 評價摘要（被封鎖者回 404） |

**PUT /me 請求範例**
```json
{
  "nickname": "Alice",
  "bio": "攝影愛好者，去過 20 個國家",
  "mbti": "ENFP",
  "gender": "FEMALE",
  "travel_style_tags": ["PHOTOGRAPHY", "FOOD", "HIKING"],
  "languages": ["cantonese", "english", "japanese"]
}
```
> `mbti` 須符合 `^[EI][SN][TF][JP]$`。`gender` ∈ `MALE | FEMALE | OTHER`。

**新增旅遊足跡**
```json
{
  "country": "Japan",
  "city": "Tokyo",
  "start_date": "2026-04-01",
  "end_date": "2026-04-07",
  "budget_type": "MODERATE",
  "summary": "櫻花季攝影之旅",
  "photo_urls": ["https://..."],
  "is_public": true
}
```
> `budget_type` ∈ `BUDGET | MODERATE | LUXURY`。

---

## Trips `/api/v1/trips`

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `` | 建立行程 |
| GET | `` | 列表。Query：`country`、`city`、`budget_type`、`tags`、`start_date`、`page`、`limit` |
| GET | `/recommendations` | 個人化推薦（含可解釋原因）。Query：`limit` |
| GET | `/mine` | 我發佈的行程 |
| GET | `/{trip_id}` | 行程詳情（作者另見申請清單） |
| PATCH | `/{trip_id}` | 更新行程（僅作者） |
| DELETE | `/{trip_id}` | 刪除行程（僅作者） |
| POST | `/{trip_id}/apply` | 申請成為旅伴 |
| GET | `/{trip_id}/applications` | 申請清單（僅作者） |
| PATCH | `/applications/{application_id}` | 審批。Query：`decision=ACCEPTED\|REJECTED` |

**審批的狀態機**：決策**只能下達一次**。

| 情況 | 回應 |
|------|------|
| 申請為 `PENDING`，且（若是 ACCEPTED）行程仍有名額 | `200`，狀態更新 |
| 重送**同一個**決策（客戶端逾時重試） | `200`，狀態不變，**不重複發通知** |
| 送出**不同**決策（例如已 REJECTED 再送 ACCEPTED） | `409`，決策不可變更 |
| 接受時 `looking_for_count` 已滿 | `409`，且**不改變**申請狀態 |
| 非行程作者 | `404`（不洩漏申請是否存在） |

`409` 與 `200` 的區分是刻意的：重試是同一個決策，不該顯示成錯誤；
換一個決策則是真正的衝突，必須讓呼叫端知道它沒有生效。

**建立行程**
```json
{
  "title": "東京櫻花季攝影之旅",
  "description": "計劃四月去東京拍櫻花…",
  "destination_country": "Japan",
  "destination_city": "Tokyo",
  "start_date": "2027-04-01",
  "end_date": "2027-04-07",
  "budget_type": "MODERATE",
  "target_gender": "ANY",
  "tags": ["PHOTOGRAPHY", "CULTURE"],
  "looking_for_count": 2
}
```
> `target_gender` ∈ `MALE | FEMALE | ANY`；`status` ∈ `OPEN | CLOSED | CANCELLED`。

**標籤（`tags`）語意**

標籤一律正規化為**大寫、去首尾空白、去重**，最多 12 個、每個最長 40 字元；回應一律回傳正規化後的形式（送 `["hiking"]` 會拿回 `["HIKING"]`）。排序依字母，讓同一篇行程每次讀到的順序一致。

`GET /trips?tags=` 的比對規則：

| 行為 | 說明 |
|------|------|
| 大小寫 | 不敏感（`hiking` / `HiKiNg` 都命中 `HIKING`） |
| 範圍 | **整顆標籤**比對，不是前綴或子字串（`HIK` 不會命中 `HIKING`） |
| 多個標籤 | 以逗號分隔，彼此為 **OR**（命中任一個即算） |
| 分頁 | `total` 為符合條件的**真實總數**，不受分頁或任何掃描窗影響 |
| 與其他條件 | 與 `country` / `city` / `budget_type` / `start_date` **同時**成立（AND） |

**推薦回應**
```json
[
  {
    "post": { "id": "...", "title": "..." },
    "score": 6.1,
    "reasons": ["你曾到訪 Tokyo", "共同旅遊風格：PHOTOGRAPHY", "預算習慣相近", "近期發佈"]
  }
]
```
> 評分為規則式可解釋模型，非黑箱；`reasons[]` 直接對應加權訊號。

**申請回應**
```json
{ "id": "...", "trip_post_id": "...", "applicant_id": "...", "message": "...", "status": "PENDING", "created_at": "..." }
```

> ⚠️ 審批為 `ACCEPTED` 時，系統會自動建立（或沿用）一對一 `DIRECT` 聊天室。
> 同一使用者對同一行程重複申請會回 `409`。

---

## Reviews `/api/v1/reviews`

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/reviews` | 撰寫評價 |
| GET | `/profiles/{user_id}/reviews` | 某用戶收到的評價 |
| GET | `/profiles/{user_id}/reviews/summary` | 評價摘要（平均分與分佈） |

**撰寫評價**
```json
{
  "reviewee_id": "<profile_id>",
  "trip_post_id": "<trip_id>",
  "rating": 5,
  "tags": ["PUNCTUAL", "FRIENDLY", "GOOD_PLANNER"],
  "comment": "很準時，規劃得很周到！"
}
```
> **只有真正一起出遊過的旅伴才能互相評價**（通過 `_shared_trip_ids` 檢查），否則回 `403`。
> 同一 `(reviewer, reviewee, trip_post)` 組合僅能評價一次。

**摘要回應**
```json
{ "count": 12, "average_rating": 4.7, "distribution": { "5": 9, "4": 3 } }
```

---

## Chat `/api/v1/chat`

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/rooms` | 建立房間（`DIRECT` 需 `other_profile_id`；`TRIP` 需 `trip_post_id`） |
| GET | `/rooms` | 我的房間清單 |
| GET | `/rooms/{room_id}/messages` | 歷史訊息（分頁）。Query：`page`、`page_size` |
| POST | `/rooms/{room_id}/messages` | REST 發訊（WS 的後備路徑） |
| GET | `/safety/scan` | 檢查文字是否疑似含電話號碼。Query：`text` |

> 非成員存取房間一律回 `404`（而非 `403`），避免洩漏房間是否存在。

---

## WebSocket `/api/v1/ws/chat/{room_id}`

**連線**
```
ws://localhost:8000/api/v1/ws/chat/<room_id>?token=<access_token>
```
握手即驗證 JWT 與房間成員資格。失敗時在 `accept()` **之前**呼叫 `close(code=1008)`：

- **in-process 測試客戶端**（Starlette `TestClient`）觀察到 `WebSocketDisconnect(1008)`。
- **真實網路客戶端**（瀏覽器 / `websockets`）觀察到的是 **HTTP 403 握手失敗**——
  ASGI 伺服器會把「未 accept 即 close」轉譯為拒絕升級。瀏覽器端表現為
  `onerror` + `onclose(code=1006)`，**不會**看到 1008。

> 前端因此以「連線從未進入 OPEN 狀態即關閉」作為被拒絕的判斷依據，而非比對 1008。
> 兩種情況下連線都不會建立，也不會交換任何資料，安全效果相同。

**客戶端 → 伺服器**

| 訊息 | 說明 |
|------|------|
| `{"type":"message","content":"..."}` | 發送訊息（限 2 msg/s，單則上限 4000 字） |
| `{"type":"typing","is_typing":true}` | 輸入中提示 |
| `{"type":"ping"}` | 心跳 |

**伺服器 → 客戶端**

| 訊息 | 說明 |
|------|------|
| `{"type":"message","id","room_id","sender_id","content","created_at"}` | 新訊息廣播 |
| `{"type":"presence","event":"join"\|"leave","profile_id","online":[...]}` | 上線狀態 |
| `{"type":"typing","profile_id","is_typing"}` | 對方輸入中（不回送給發送者） |
| `{"type":"safety_hint","code":"possible_phone","detail":"..."}` | 私隱提醒（僅發送者） |
| `{"type":"error","code":"rate_limited"\|"blocked"\|"too_long"\|"empty_message"\|"unsupported_type"}` | 錯誤 |
| `{"type":"pong","ts":"..."}` | 心跳回應 |

> 封鎖狀態在**寫入時**重新檢查，因此對已開啟的連線亦即時生效。

---

## Notifications `/api/v1/notifications`

| 方法 | 路徑 | 限流 | 說明 |
|------|------|------|------|
| GET | `` | read | 我的通知（新到舊，分頁）。Query：`page`、`page_size`、`unread_only` |
| GET | `/unread-count` | read | 未讀數量（Navbar 徽章輪詢用） |
| POST | `/{notification_id}/read` | write | 標記單則為已讀（冪等，重複呼叫不再變更 `read_at`） |
| POST | `/read-all` | write | 全部標為已讀，回 `{unread: 0}` |
| DELETE | `/{notification_id}` | write | 刪除單則，回 `204` |

列表回應為 `{items, total, unread, page, page_size}`；`unread` 一併回傳，讓 Navbar 每次輪詢省下一次往返。

通知類型：`APPLICATION_RECEIVED`、`APPLICATION_ACCEPTED`、`APPLICATION_REJECTED`、`NEW_MESSAGE`、`REVIEW_RECEIVED`。

**兩個不變式**在 `services/notifications.py::create()` 內強制執行，而非只在呼叫點檢查：

1. **永不通知自己** —— 否則自己的動作會把自己叫醒。
2. **永不跨越封鎖** —— 任一方封鎖另一方時通知直接不建立。少了這道閘門，通知會從提醒變成騷擾管道。

`NEW_MESSAGE` 以**房間為單位 upsert**（索引 `ix_notifications_message_dedupe`），因此徽章數字代表「幾個房間有新訊息」，而不是訊息總量 —— 否則一句對話就能把徽章推到 99+。

> 刻意**沒有** `GET /notifications/{id}`：by-id 查詢是 IDOR 的溫床，而前端不需要它。所有查詢都在 WHERE 子句過濾 `recipient_profile_id`，因此他人的 id 只會得到 `404`（與不存在同義），不洩露存在性。

---

## Moderation

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| POST | `/api/v1/reports` | 使用者 | 檢舉。Body：`{reported_profile_id, reason, detail}` |
| GET | `/api/v1/admin/reports` | admin | 檢舉佇列。Query：`status_filter` |
| PATCH | `/api/v1/admin/reports/{report_id}` | admin | 更新狀態。Query：`new_status` |
| GET | `/api/v1/admin/audit-logs` | admin | 唯讀稽核軌跡，新到舊。Query：`action`、`actor_profile_id`、`page`、`limit`（≤200） |

`reason` 可選值：`harassment`、`spam`、`scam`、`inappropriate`、`other`

**稽核軌跡（`audit_logs`）**

| 動作 | 觸發點 |
|------|--------|
| `USER_BLOCKED` / `USER_UNBLOCKED` | 封鎖／解除封鎖（解除只在實際移除時記錄） |
| `REPORT_SUBMITTED` | 送出檢舉（`detail` 只留 `reason` 這個封閉 enum，不含免費文字） |
| `REPORT_STATUS_CHANGED` | 管理員改狀態（`detail` 記錄 `from` → `to`） |
| `ACCOUNT_DELETED` / `ACCOUNT_ANONYMIZED` | 帳號註銷 |
| `ADMIN_QUEUE_VIEWED` | 管理員讀取檢舉佇列 |

> **唯讀且不可刪改**：沒有 `POST/PUT/PATCH/DELETE` 路由指向稽核端點，模型也沒有 `updated_at`。`ip_address` 與 `user_agent` 有存但**不透過 API 回傳** —— 它們供資料庫層的事故調查使用，把每位使用者的位址發給任何持有管理員 session 的人是純粹的私隱成本。
>
> 讀取**稽核端點本身刻意不記錄**（與檢舉佇列不同）：自我稽核會自我否定 —— 每看一頁就多一列，列數隨「查看」這個動作成長，分頁會在讀者腳下位移。

---

## Uploads `/api/v1/uploads`

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/image` | `multipart/form-data`。驗證 magic bytes → 抹除 EXIF → 儲存，回 `{url, content_type}` |
| POST | `/presign` | 取得 Pre-signed PUT URL（僅 S3 後端）。Body：`{content_type, prefix}` |

限制：僅 `image/jpeg`、`image/png`、`image/webp`；最大 5 MB；`413` 表示過大、`400` 表示類型不合法。

> 圖片一律**重新編碼**儲存，`exif` 參數刻意不傳遞，因此 GPS、拍攝裝置等中繼資料全數被丟棄。

---

## 內容審核（橫切所有寫入端點）

所有使用者產生的文字在寫入前都會經過 `services/content_filter.py`：
行程 `title` / `description`、個人檔案 `nickname` / `bio`、足跡 `summary`、
評價 `comment`、聊天訊息（REST **與** WebSocket）。

| 判定 | 行為 | HTTP |
|------|------|------|
| `ALLOW` | 寫入 | 正常 |
| `FLAG` | **照常寫入**，只累加 `tripmate_content_flagged_total` | 正常 |
| `BLOCK` | 拒寫，並累加 `tripmate_content_blocked_total` | `422` |

`422` 的 `detail` 會指出**欄位**（例如「行程說明：這段內容含有不符合社群規範的資訊…」），
但**不會**指出命中的詞或規則 —— 後者會讓使用者一次一條地把規則集試出來。

WebSocket 走不同的訊號路徑（HTTP 狀態碼在 socket 上沒有意義）：

```json
{"type": "error", "code": "content_rejected", "detail": "訊息：這段內容含有不符合社群規範的資訊…"}
```

> **`FLAG` 不是「軟性阻擋」。** 它放行內容，存在的唯一目的是讓「這種內容是不是變多了」
> 這個問題有答案，而不必靠猜。真正能擋下寫入的只有 `BLOCK`。
>
> **誤擋比漏放更貴。** 在旅遊 App 上，「hotel deposit」「killer view」「Gunsan」
> 都是正常文字，所以本地規則刻意保守：只有幾乎沒有正當讀法的語句才 `BLOCK`，
> 可辯解的一律 `FLAG`。`tests/test_content_filter.py` 用 25 句真實旅遊語料把這一點鎖住。
