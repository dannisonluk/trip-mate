# Trip Mate — Security & Privacy Implementation Map

本文件將《Security & Privacy Spec》的每一條要求，對照到 Trip Mate 的實際實作位置。
狀態：✅ 已實作　🟡 部分實作／需生產配置　🔜 待補

> 本表以**實際程式碼**為準，不以設計意圖為準。凡標記 🟡 者，均於 §7「已知風險」說明。

---

## 1. 身份驗證與授權

### 1.1 JWT 認證機制

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| Dual-Token（Access + Refresh） | ✅ | `core/security.py::create_access_token / create_refresh_token` |
| Access Token 15 分鐘、`Authorization: Bearer` | ✅ | `ACCESS_TOKEN_EXPIRE_MINUTES=15`；`core/deps.py::bearer_scheme` |
| Refresh Token 7 天、`HttpOnly + Secure + SameSite=Strict` Cookie | ✅ | `api/v1/auth.py::_set_refresh_cookie` |
| RS256（生產）／HS256（開發） | ✅ | `security.py::_signing_key()/_verify_key()` 依 `JWT_ALGORITHM` 自動切換 |
| 密鑰嚴禁硬編碼 | 🟡 | 由 `.env` 讀取；**部署前必須更換 `SECRET_KEY`** |
| Payload 僅含 `sub/type/role/profile_id/iat/exp/jti`，禁存 PII | ✅ | `security.py::_create_token` 只寫入上述 claims |
| Token 類型混淆防護 | ✅ | `decode_token(expected_type=...)`；access 與 refresh 不可互換使用 |
| **Refresh Token 可即時撤銷** | ✅ | `services/token_store.py`：登出／輪替時把 `jti` 加入 deny-list（TTL = 剩餘效期，清單不會無限增長） |
| **Refresh Token 輪替（rotation）** | ✅ | `api/v1/auth.py::refresh` 每次成功都撤銷舊 token 並簽發新的 |
| **Reuse detection（盜用偵測）** | ✅ | 已輪替的 token 再次出現 → 判定為洩漏 → `bump_epoch()` 使該用戶**所有**未到期 refresh token 一次失效，強制重新登入 |
| 改密碼即登出其他裝置 | ✅ | `change_password` 會 `bump_epoch()` |

**Refresh Cookie 設定**
```python
response.set_cookie(
    key="tripmate_refresh", value=token,
    httponly=True,                    # JS 無法讀取 → 防 XSS 竊取
    secure=settings.COOKIE_SECURE,    # 生產須為 True（HTTPS/WSS）
    samesite=settings.COOKIE_SAMESITE,# strict → 防 CSRF
    max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
    path=f"{settings.API_V1_PREFIX}/auth",  # 僅認證端點可送出，縮小暴露面
)
```
> `path` 限定為 `/api/v1/auth` 是關鍵設計：一般 API 請求根本不會攜帶此 Cookie，
> 即使發生 CSRF 也無法用它換取新的 Access Token。

### 1.2 密碼安全

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| Argon2id 雜湊 | ✅ | `security.py`：`PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)` |
| 雜湊不阻塞 event loop | ✅ | Router 呼叫 `hash_password_async` / `verify_password_async`，以 `anyio.to_thread.run_sync` 移出；argon2 釋放 GIL 故並發雜湊可平行。線程池以 `PASSWORD_HASH_MAX_CONCURRENCY` 設上限（每次 64 MiB） |
| 最小 8 位 + 大小寫 + 數字 | ✅ | `security.py::validate_password_policy`（註冊與改密碼皆驗證） |
| 防止帳號枚舉（訊息層） | ✅ | 登入失敗與不存在帳號回傳**相同** `401 Invalid credentials` |
| 防止帳號枚舉（時間層） | ✅ | `login()` 先判斷帳號存在性；不存在時呼叫 `security.py::verify_password_dummy()` 對固定 dummy hash 執行**一次等價 Argon2 驗證**，再回傳同一個 401。實測兩條路徑中位數 **38 ms vs 36 ms（1.05×）** |
| 註冊衝突不洩漏 | 🟡 | 回 `400 Registration failed. Please try different details.`，但 HTTP 狀態碼與登入不同 |
| 自動升級雜湊參數 | ✅ | `needs_rehash()` 於登入成功時靜默重新雜湊 |
| 手機號驗證（OTP） | ✅ | `services/otp.py` + `api/v1/auth.py::request_otp / verify_otp`，驗證碼以 `hmac.compare_digest` 定時比較，成功即消耗 |
| OTP 實際發送 | ✅ | `services/sms.py`：`console`（開發，只記日誌）／`webhook`（生產，POST 至自建閘道）。發送失敗**不會**讓請求失敗，只記錯誤日誌——避免 SMS 故障看起來像註冊功能壞掉，也避免洩漏號碼是否已註冊 |

> 測試：`tests/test_api_flow.py::test_login_is_generic_on_failure`、
> `test_login_burns_hash_time_for_unknown_account`（以呼叫計數驗證，不依賴量測時序，避免 CI 負載造成偽陽性）、
> `tests/test_sms_provider.py::test_dev_code_never_returned_in_production`、`test_otp_flow`

### 1.3 存取控制（RBAC）

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 依賴注入驗證資源所有權 | ✅ | `core/deps.py::get_current_user / get_current_profile` |
| 只能改自己的 Profile / 足跡 / 行程 | ✅ | `api/v1/profiles.py`；`api/v1/trips.py` 比對 `creator_id` |
| 未授權存取回 403 或 404 | ✅ | 聊天室一律回 **404**（連存在性都不洩漏，較 403 更嚴） |
| 角色權限 | ✅ | `deps.py::require_role("admin")`，以 `Depends()` 套用於 `/admin/*` |
| 管理端點預設拒絕 | ✅ | `require_role` 未通過即 403，不依賴前端隱藏按鈕 |

---

## 2. 數據隱私與 PII 保護

### 2.1 PDPO 合規

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 最小化收集 | ✅ | 註冊只需 phone / password / nickname；MBTI、性別、標籤、語言全部選填 |
| 明示同意（註冊時勾選） | ✅ | `schemas/auth.py`：`consent_privacy`/`consent_terms` 必填且須為 `True`；寫入 `users.consent_*` + `consent_at` |
| 數據刪除權 `DELETE /api/v1/users/me` | ✅ | `api/v1/users.py::delete_my_account` |
| 硬刪除（Hard Delete） | ✅ | `mode="hard_delete"` → `db.delete(user)`，FK `ondelete=CASCADE` 連鎖清除 |
| 去識別化（Anonymization） | ✅ | `mode="anonymize"` → 電話換為 ghost 號、密碼重設為隨機、`is_active=False`、`is_anonymized=True`；訊息內容改 `[deleted]` 且 `sender_id=NULL`；行程描述、申請訊息、足跡摘要一併抹除 |
| 註銷需重新驗證身份 | ✅ | 需提供密碼，驗證失敗回 401 |

> 測試：`tests/test_api_flow.py::test_account_deletion_anonymize`

### 2.2 PII 遮蔽

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 電話號碼永不外洩至他人 | ✅ | `schemas/profile.py::ProfilePublic` **不含**電話欄位；`ProfileSummary` 亦然 |
| 遮蔽工具單一來源 | ✅ | `services/pii.py::mask_phone`（`+852 9*** *123`）／`mask_email` |
| 聊天中洩漏電話的主動提醒 | ✅ | `services/pii.py::is_phone_like`，命中時 WS 對發送者回 `safety_hint` |
| 精確位置隱私（只存國家／城市） | ✅ | `models/trip.py` **沒有**地址欄位；`trip_posts` 僅 `destination_country/city` |
| 不強迫公開住宅地址 | ✅ | 城市欄位選填 |
| 欄位級加密（Fernet） | 🟡 | `services/crypto.py` 已實作，但**目前無呼叫點**——電話改為登入識別碼後不再作為 profile 欄位儲存 |

> ⚠️ `services/crypto.py` 現為預留程式碼（dead code）。若未來要儲存任何可識別的聯絡欄位，
> 必須經此模組加密，不得直接落庫。
>
> **接上前必須先處理的兩件事**（已寫入模組 docstring）：
> 1. 金鑰是由 `SECRET_KEY` 衍生的，所以**輪換 `SECRET_KEY` 會讓既有密文全部無法解開**
>    （`decrypt_value` 靜默回 `None`，不會拋錯）。目前**沒有** `FIELD_ENCRYPTION_KEY`
>    這個設定 —— 舊版 docstring 曾叫人去設它，那是錯的（設定不存在，照做不會有任何效果，
>    已修正）。真要上線使用，必須先引入一把可獨立輪換的金鑰。
> 2. `_fernet` 在 **import 時**建立，所以 import 此模組要求 `SECRET_KEY` 已載入。

---

## 3. 即時通訊安全與防騷擾

### 3.1 WebSocket 鑑權

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 握手以 `?token=` 驗 JWT | ✅ | `ws/routes.py::_authenticate(token)` |
| 驗證失敗立即中斷 | ✅ | `await websocket.close(code=1008)`（**在 accept 之前**關閉）→ 真實網路客戶端收到 **HTTP 403 握手拒絕**；in-process 測試客戶端收到 `WebSocketDisconnect(1008)`。兩種情況都不會建立連線、不交換資料 |
| 進入房間前校驗 `chat_room_members` | ✅ | `ws/routes.py::_authorize_room` |
| REST 端點亦校驗成員 | ✅ | `api/v1/chat.py::require_membership` |
| 帳號狀態即時檢查 | ✅ | `_authenticate` 內檢查 `is_active` / `is_deleted` |

### 3.2 防騷擾

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 封鎖 `POST /profiles/{id}/block` | ✅ | `api/v1/profiles.py` → `services/moderation.py::block_profile` |
| 被封鎖者無法私訊／申請／開房 | ✅ | 申請：`trips.py::apply_to_trip`；開房：`chat.py::create_room`；發訊：`chat.py::send_message` |
| 封鎖對**已開啟**的連線亦立即生效 | ✅ | `ws/routes.py::_persist_message` 於每次寫入前重新檢查封鎖關係 |
| 封鎖為雙向生效 | ✅ | `moderation.py::is_blocked_between` 查兩個方向 |
| WS 訊息限速 2 msg/s | ✅ | `ws/manager.py::TokenBucket`（rate=2, capacity=2） |
| 敏感 API 限流 5 次／分鐘 | ✅ | `core/rate_limit.py`：`LOGIN_RATE` / `REGISTER_RATE` / `OTP_RATE` 套用於 `auth.py` |
| 檢舉機制 | ✅ | `POST /api/v1/reports` + `GET/PATCH /api/v1/admin/reports`（僅 admin） |
| 評價防刷分 | ✅ | `api/v1/reviews.py::_shared_trip_ids`——只有共同出遊過才能互評，否則 403；同一組合僅能評一次 |

> 補充：`GET /api/v1/profiles/{id}` 對被封鎖者回 **404**，避免對方探測帳號是否存在。

---

## 4. 數據傳輸與儲存安全

### 4.1 傳輸層

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 全站 TLS 1.3 / HTTPS + WSS | 🟡 | 需於反向代理（Nginx / Cloudflare）終結 TLS；應用層已支援 `COOKIE_SECURE` 與 WSS |
| HSTS Header | ✅ | `main.py`：生產環境自動加 `Strict-Transport-Security: max-age=31536000; includeSubDomains` |

### 4.2 資料庫與儲存

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 靜態加密（EBS / TDE） | 🟡 | 屬基礎設施層設定（見下方「部署前檢查清單」） |
| 圖片 Pre-signed URL | ✅ | `services/storage.py::presign_put` + `api/v1/uploads.py::presign` |
| 前端不得取得儲存桶權限 | ✅ | 憑證只存在後端環境變數，永不回傳前端 |
| MIME 驗證（jpeg/png/webp） | ✅ | `storage.py::sniff_mime` 以 **magic bytes** 判斷，不信任 client 宣告 |
| 檔案大小上限 5MB | ✅ | `MAX_UPLOAD_BYTES=5242880`；`uploads.py` 讀取後立即檢查並回 413 |
| 自動抹除 EXIF 地理位置 | ✅ | `storage.py::sanitize_image`：Pillow 重新編碼且**不傳** `exif=`，所有 metadata 一併消失。**已由測試對位元組斷言**（`test_uploaded_image_has_its_exif_stripped`：上傳帶 EXIF 的 JPEG → 從磁碟取回物件 → 解析其 EXIF 必須為空，同時 assert 它仍是可解碼的圖，以免「檔案被截斷」也能假通過） |
| 防路徑穿越 | ✅ | `storage.py::_local_path` 檢查解析後路徑仍位於上傳根目錄內 |

---

## 5. 前端安全與注入防護

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| CSP | ✅ | `frontend/next.config.js`（`headers()`）＋ `backend/app/main.py`（API 回應標頭）。`script-src` 含 `unsafe-inline`，為與靜態預渲染並存的必要取捨（見 §7）；`npm run check:prod` 會在 production build 上驗證標頭齊備且無 violation |
| `X-Frame-Options: DENY` | ✅ | 前後端皆設定 |
| `X-Content-Type-Options: nosniff` | ✅ | 前後端皆設定 |
| `Referrer-Policy` / `Permissions-Policy` | ✅ | `main.py::SECURITY_HEADERS` |
| SQL 注入防護（強制 ORM、禁拼接 Raw SQL） | ✅ | 全專案僅使用 SQLAlchemy 2.0 `select()` / ORM，**無任何 f-string SQL** |
| XSS：Rich Text 用 DOMPurify 淨化 | ✅ | `components/SafeHtml.tsx`（嚴格 allow-list，`ALLOW_DATA_ATTR: false`） |
| 聊天訊息渲染淨化 | ✅ | 聊天訊息以 React 文字節點渲染（自動轉義），不經 `dangerouslySetInnerHTML` |
| CORS 僅允許特定來源，禁 `*` | ✅ | `main.py`：`allow_origins=settings.cors_origins`（精確來源清單） |
| 前端不落地 Access Token | ✅ | `lib/api.ts` 僅存於模組作用域變數，不寫 `localStorage` / `sessionStorage` |

---

## 6. 免責聲明

| 要求 | 狀態 | 實作位置 |
|------|------|----------|
| 貼文頁與聊天室頂部標示免責聲明 | ✅ | `components/DisclaimerBanner.tsx`，用於 `/trips/[id]` 與 `/chat` |
| 文案單一來源 | ✅ | 後端 `GET /api/v1/disclaimer` 提供標準文案 |

---

## 7. 已知風險與緩解

| 風險 | 影響 | 現況 | 緩解建議 |
|------|------|------|----------|
| ~~登入時間差可區分帳號是否存在~~ | 低度帳號列舉 | ✅ 已解 | 對不存在帳號驗證固定 dummy hash，兩條路徑皆執行一次等價 Argon2（實測 38 ms vs 36 ms） |
| Argon2 為 CPU-bound，可被用作資源耗盡 | 併發登入可拖慢整個服務 | 🟡 部分緩解 | 已移出 event loop（不凍結其他請求）並以 `PASSWORD_HASH_MAX_CONCURRENCY` 限制並發；但登入端點仍以每分鐘 5 次限流為主要防線，且限流在 Redis 故障時 fail-open |
| Access Token 無法即時撤銷 | 登出後 access token 仍有效至到期 | 🟡 刻意取捨：access 效期僅 15 分鐘，換取完全無狀態驗證 | 縮短效期，或加入 `jti` 黑名單檢查（會讓每個請求都查 KV） |
| 撤銷狀態存於單機（無 Redis 時） | 多副本下各副本撤銷狀態不一致 | 🟡 無 Redis 時退回每程序記憶體 | 生產必須部署 Redis；`services/kv.py` 已就緒 |
| OTP 於開發模式回傳 `dev_code` | 生產若誤設 `OTP_DEV_ECHO=true` 會洩漏驗證碼 | ✅ 已雙重防護：`is_production` 時強制不回傳，啟動時另發警告 | — |
| CSP 的 `script-src` 含 `unsafe-inline`（`style-src` 亦然） | 降低 XSS 防護強度：注入的 inline script 不被瀏覽器攔截 | 🟡 **已調查，取捨明確**：nonce-based CSP 已實作並以真實 production build 驗證，**無法與靜態預渲染並存** —— 12 條路由有 10 條是 build-time prerender，預先渲染的 HTML 帶不了 per-request nonce，實測每張靜態頁 18 個 violation（且 nonce 讓 `strict-dynamic` 使 `'self'` 失效，連外部 chunk 都被擋）。目前選擇保留預渲染 | 二選一：改 `force-dynamic` 放棄預渲染以換取嚴格 `script-src`；或以 hash-based CSP 覆蓋已知 inline 腳本。**改動前必跑 `npm run check:prod`**，它會在 production build 上斷言 violation 為 0 |
| `services/crypto.py` 無呼叫點 | 死碼，易誤以為欄位已加密；且其 docstring 曾叫人設一個**不存在的** `FIELD_ENCRYPTION_KEY` | 🟡 | 已修正 docstring 說出真實的兩項前置條件（金鑰由 `SECRET_KEY` 衍生 → 輪換即失效；`_fernet` 於 import 時建立）。保留作為未來聯絡欄位的唯一加密入口，或移除 |
| WS 狀態存於單程序記憶體 | 多副本部署時房間狀態不一致 | 🔜 | Redis Pub/Sub 廣播（見 `docs/ARCHITECTURE.md` §6） |
| 限流在 Redis 故障時 **fail-open** | 故障期間限流失效，暴力破解防護降級 | 🟡 刻意取捨：可用性優先於嚴格限流 | 監控告警 Redis 健康狀態；必要時改為 fail-closed 並接受登入中斷 |
| ~~JSON 標籤以記憶體掃描篩選~~ | 貼文量大時查詢成本上升 | ✅ 已解 | 已改為 `trip_post_tags` join table，篩選為索引驅動且無上限（見 `docs/ARCHITECTURE.md` §10） |
| ~~SQLite 不強制外鍵~~ | `ondelete=` 在 dev/test 方言上失效，與生產行為分歧 | ✅ 已解 | `db/session.py` 於 SQLite 連線開啟 `PRAGMA foreign_keys=ON`；`tests/test_tags.py` 以 ORM 無法代勞的 cascade 作為探針 |
| ~~開發期以 `create_all` 建表~~ | 無法安全演進 schema | ✅ 已解 | 已建立初始遷移；`init_db()` 在生產完全跳過 `create_all`，偵測到 `alembic_version` 亦讓位給 Alembic。以 `tests/test_migrations.py` 證明遷移結果與模型定義結構一致 |

---

## 部署前檢查清單

- [ ] `SECRET_KEY` 換成 `secrets.token_urlsafe(64)` 產生的隨機值
- [ ] `JWT_ALGORITHM=RS256` 並提供 `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`
- [ ] `COOKIE_SECURE=true`、`ENV=production`（自動關閉 `/docs` 並啟用 HSTS）
- [ ] `OTP_DEV_ECHO=false`（生產已強制忽略，但仍應明確關閉）
- [ ] `SMS_PROVIDER=webhook` 且 `SMS_WEBHOOK_URL` 指向真實閘道（啟動時會檢查並告警）
- [ ] `BACKEND_CORS_ORIGINS` 設為正式前端網域（精確來源）
- [ ] `DATABASE_URL` 使用 `postgresql+asyncpg://` 並指向啟用靜態加密的 PostgreSQL
- [ ] `cd frontend && npm run build && npm run check:prod` —— 確認**生產**建置的
      安全標頭齊備且 CSP violation 為 0（nonce CSP 那類缺陷只在 production build 顯現，
      `npm run test:e2e` 對 `next dev` 全綠也測不出來）
- [ ] 執行 `alembic upgrade head`（生產不再使用 `create_all`）
- [ ] Redis 就緒 — **必需**：限流、OTP、token 撤銷皆依賴它；無 Redis 時會退回每程序記憶體，多副本下不一致
- [ ] **建置映像檔前確認 `.dockerignore` 生效**（`backend/` 與 `frontend/` 各一份）。
      兩個 Dockerfile 都使用 `COPY . .`，因此**建置上下文裡的任何東西都會進入映像檔**：
      真實的 `.env` 會被烤進映像層，之後用 `RUN rm` 刪除**不能**讓它從
      `docker history` 消失。`.gitignore` 保護 repository，**不保護映像檔** ——
      這兩者是獨立的防線。範本 `!.env.example` 有保留例外（範本要進去，密鑰不要）。
      > 防護重點不是「記得刪」，而是**一開始就不要放進去**：映像層一旦產生就無法撤回，
      > 唯一可靠的補救是重寫歷史並**輪換所有曾進入該映像的密鑰**。
- [ ] 圖片儲存改用 S3/R2 並設定生命週期與私有 ACL
- [ ] 由反向代理終結 TLS 1.3，並轉發 `X-Forwarded-Proto`
- [x] ~~接上結構化日誌與告警~~ **已完成**：`core/logging.py`（JSON／文字雙格式、請求關聯 id、formatter 層統一遮蔽）、`core/metrics.py`（`/metrics`，以路由範本為標籤）、`audit_logs` 表 + `services/audit.py`，涵蓋**封鎖／檢舉／註銷／管理員操作**四類
- [ ] **`/metrics` 需置於內網或 allowlist 之後**：它依設計不帶驗證（scraper 不帶 JWT），所以預設是關閉的，`METRICS_ENABLED=true` 必須是有意識的決定
- [ ] 內容審核：預設 `CONTENT_FILTER_PROVIDER=local` 走本地規則，**不需要任何外部服務**；
      若要接真實審核 API 再設 `webhook`（並注意內容會離開你的基礎設施，屬第三方資料處理者）
- [ ] `CONTENT_FILTER_FAIL_OPEN` 保持 `true`（預設）：關掉會讓供應商成為每次寫入的硬依賴，
      一次供應商故障就變成全面寫入中斷
- [ ] 若部署在反向代理之後，設 `TRUST_PROXY_HEADERS=true`（僅在代理會**覆寫** `X-Forwarded-For` 時）；否則客戶端可自行偽造稽核軌跡中的來源位址
