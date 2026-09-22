/**
 * Translation dictionaries.
 *
 * `zhHK` is the **source of truth for the key set**: `MessageKey` is derived from
 * it, and every other locale is typed as `Record<MessageKey, string>`. That makes
 * a missing translation a *compile error* rather than a string that silently
 * falls back at runtime — which is the whole reason this is a typed module and
 * not a JSON file.
 *
 * Adding a key therefore means: add it to `zhHK`, then TypeScript will point at
 * every locale that still needs it.
 *
 * ## Trade-off: no per-locale URLs
 *
 * The locale lives in a cookie, not in the path (`/en/trips`). That keeps every
 * existing route, link and E2E assertion untouched, and this app is an
 * authenticated product where per-locale URLs buy little. What it costs:
 *
 *   * Server-rendered `metadata` cannot vary by locale (see `app/layout.tsx`) —
 *     crawlers always see the default locale.
 *   * The first paint after a locale switch to a non-default language is the
 *     default language until the cookie is read on the client.
 *
 * If localised URLs ever matter, the fix is to move the routes under `[locale]`
 * and read the segment server-side; this module's API does not have to change.
 */

export const LOCALES = ["zh-HK", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "zh-HK";

/** Shown in the language switcher. Each label is written in its own language,
 *  which is the convention users actually expect from a language picker. */
export const LOCALE_LABEL: Record<Locale, string> = {
  "zh-HK": "繁體中文",
  en: "English",
};

export const LOCALE_COOKIE = "tripmate_locale";

// ---------------------------------------------------------------------------
// zh-HK — the key set is defined here
// ---------------------------------------------------------------------------

const zhHK = {
  // --- generic / shared ---
  "common.loading": "載入中…",
  "common.reload": "重新載入",
  "common.cancel": "取消",
  "common.close": "關閉",
  "common.all": "全部",
  "common.none": "無",

  // --- document metadata (default locale only — see the note above) ---
  "meta.title": "Trip Mate — 旅伴配對平台",
  "meta.description":
    "Trip Mate 是一個為香港旅客而設的旅伴配對平台：發佈行程、依旅遊足跡配對志同道合的旅伴，並安全地即時通訊。",
  "admin.title": "管理後台",
  "admin.subtitle": "檢舉處理與安全稽核紀錄。稽核紀錄為唯讀且不可刪改。",
  "admin.tabReports": "檢舉管理",
  "admin.tabAudit": "稽核紀錄",
  "admin.statusOpen": "待處理",
  "admin.statusReviewing": "處理中",
  "admin.statusActioned": "已處理",
  "admin.statusDismissed": "已駁回",
  "admin.reasonHarassment": "騷擾",
  "admin.reasonSpam": "垃圾訊息",
  "admin.reasonScam": "詐騙",
  "admin.reasonInappropriate": "不當內容",
  "admin.reasonOther": "其他",
  "admin.auditBlocked": "封鎖使用者",
  "admin.auditUnblocked": "解除封鎖",
  "admin.auditReportSubmitted": "提交檢舉",
  "admin.auditReportStatusChanged": "檢舉狀態變更",
  "admin.auditAccountDeleted": "帳號刪除",
  "admin.auditAccountAnonymized": "帳號匿名化",
  "admin.auditQueueViewed": "檢視檢舉佇列",
  "admin.loadAuditError": "無法載入稽核紀錄。",
  "admin.auditTotal": "共 {total} 筆紀錄",
  "admin.noAudit": "這個篩選條件下沒有稽核紀錄。",
  "admin.actor": "操作人：",
  "admin.accountDeleted": "（帳號已刪除）",
  "admin.targetProfile": "對象：",
  "admin.targetOther": "目標：",
  "admin.pageIndicator": "第 {page} / {lastPage} 頁",
  "admin.loadReportsError": "無法載入檢舉清單。",
  "admin.updateStatusError": "無法更新狀態。",
  "admin.showing": "目前顯示 {count} 筆",
  "admin.showingOpen": "，其中 {count} 筆待處理",
  "admin.noReports": "這個篩選條件下沒有檢舉紀錄。",
  "admin.reporter": "檢舉人：",
  "admin.reported": "被檢舉人：",
  "admin.markReviewing": "標記處理中",
  "admin.markActioned": "已處理",
  "admin.dismiss": "駁回",
  "admin.forbiddenTitle": "沒有權限",
  "admin.forbiddenBody": "此頁面僅限管理員存取。若你認為這是錯誤，請聯絡平台管理團隊。",
  "admin.backToTrips": "返回探索行程",

  "myProfile.saved": "已儲存個人檔案。",
  "myProfile.saveError": "儲存失敗。",
  "myProfile.historyAdded": "已新增旅遊足跡。",
  "myProfile.addError": "新增失敗。",
  "myProfile.deleteError": "刪除失敗。",
  "myProfile.eraseConfirm": "此操作將無法復原，確定要繼續嗎？",
  "myProfile.eraseError": "註銷失敗。",
  "myProfile.verified": "已驗證",
  "myProfile.unverified": "未驗證手機",
  "myProfile.tabProfile": "個人檔案",
  "myProfile.tabHistories": "旅遊足跡",
  "myProfile.tabPrivacy": "帳號與私隱",
  "myProfile.profileSubtitle": "完整的檔案能大幅提升配對成功率。",
  "myProfile.nicknameLabel": "顯示名稱",
  "myProfile.bioLabel": "自我介紹",
  "myProfile.notSet": "未設定",
  "myProfile.genderLabel": "性別",
  "myProfile.styleLabel": "旅遊風格標籤",
  "myProfile.languagesLabel": "語言（以逗號分隔）",
  "myProfile.saveChanges": "儲存變更",
  "myProfile.addHistoryTitle": "新增旅遊足跡",
  "myProfile.addHistoryHint": "足跡是配對引擎最重要的訊號；私人足跡只有你自己看得到。",
  "myProfile.countryLabel": "國家",
  "myProfile.cityLabel": "城市 / 地區",
  "myProfile.startDateLabel": "開始日期",
  "myProfile.endDateLabel": "結束日期",
  "myProfile.visibilityLabel": "公開狀態",
  "myProfile.public": "公開",
  "myProfile.summaryLabel": "心得摘要",
  "myProfile.summaryPlaceholder": "這趟旅程最難忘的是…",
  "myProfile.addHistory": "新增足跡",
  "myProfile.myHistories": "我的足跡（{count}）",
  "myProfile.noHistories": "還沒有足跡紀錄。",
  "myProfile.deleteAria": "刪除",
  "myProfile.privacyTitle": "帳號與私隱",
  "myProfile.privacyBody": "根據香港《個人資料（私隱）條例》，你有權要求刪除個人資料。「匿名化」會保留行程內容但移除你的身份；「完全刪除」將永久移除所有資料。",
  "myProfile.passwordPlaceholder": "輸入密碼以確認",
  "myProfile.modeAnonymize": "匿名化",
  "myProfile.modeHardDelete": "完全刪除",
  "myProfile.eraseAccount": "註銷帳號",

  "profileView.notFound": "找不到此用戶，或你無權查看。",
  "profileView.reviewSent": "評價已送出，感謝你的分享！",
  "profileView.reviewError": "評價失敗。",
  "profileView.statTrips": "行程",
  "profileView.statReviews": "評價",
  "profileView.statAvg": "平均分",
  "profileView.travelStyles": "旅遊風格",
  "profileView.languages": "語言",
  "profileView.writeReview": "撰寫評價",
  "profileView.reviewTitle": "評價 {nickname}",
  "profileView.reviewHint": "只有一起完成過行程的旅伴才能評價，確保評價真實可信。",
  "profileView.ratingLabel": "評分",
  "profileView.tagsLabel": "標籤",
  "profileView.commentLabel": "評語",
  "profileView.commentPlaceholder": "分享你們的旅遊經驗…",
  "profileView.submitReview": "送出評價",
  "profileView.editMine": "編輯我的檔案",
  "profileView.histories": "旅遊足跡（{count}）",
  "profileView.noHistories": "尚未分享旅遊足跡。",
  "profileView.private": "私人",
  "profileView.reviews": "旅伴評價（{count}）",
  "profileView.noReviews": "還沒有評價。",

  "tripDetail.notFound": "找不到此行程，或你無權查看。",
  "tripDetail.applied": "已送出申請！對方接受後即可開始對話。",
  "tripDetail.applyError": "申請失敗，請稍後再試。",
  "tripDetail.openChatError": "無法開啟對話。",
  "tripDetail.openGroupError": "無法開啟行程群組。",
  "tripDetail.blockConfirm": "確定要封鎖此用戶？對方將無法再與你互動。",
  "tripDetail.blocked": "已封鎖此用戶。",
  "tripDetail.blockError": "封鎖失敗。",
  "tripDetail.reportPrompt": "請輸入檢舉原因（harassment / spam / scam / inappropriate / other）",
  "tripDetail.reported": "已收到你的檢舉，我們會盡快處理。",
  "tripDetail.reportError": "檢舉失敗。",
  "tripDetail.accepted": "已接受申請，並開啟對話。",
  "tripDetail.rejected": "已婉拒該申請。",
  "tripDetail.actionError": "操作失敗。",
  "tripDetail.lookingForGender": "徵求：{value}",
  "tripDetail.lookingForCount": "徵 {count} 人",
  "tripDetail.dm": "私訊",
  "tripDetail.block": "封鎖",
  "tripDetail.report": "檢舉",
  "tripDetail.applyCta": "申請成為旅伴",
  "tripDetail.applyDialogBody": "簡單介紹自己，並說明為何想一起同行。對方接受後會自動開啟對話。",
  "tripDetail.applyMessageLabel": "申請訊息",
  "tripDetail.applyMessagePlaceholder": "你好！我也計劃去…",
  "tripDetail.applications": "收到的申請（{count}）",
  "tripDetail.applicationsHint": "接受申請後即可開啟行程群組，與所有已接受的旅伴一起討論。",
  "tripDetail.openGroup": "開啟行程群組對話",
  "tripDetail.groupNeedsMember": "需要先接受至少一位旅伴的申請",
  "tripDetail.group": "行程群組",
  "tripDetail.noApplications": "目前還沒有人申請。",
  "tripDetail.traveller": "旅伴",
  "tripDetail.submitApplication": "送出申請",
  "tripDetail.accept": "接受",
  "tripDetail.reject": "婉拒",

  "chat.direct": "私人對話",
  "chat.group": "行程群組",
  "chat.loadRoomsError": "無法載入對話列表。",
  "chat.loadMessagesError": "無法載入訊息。",
  "chat.wsForbidden": "連線被拒：你沒有此對話的存取權限。",
  "chat.wsFailed": "無法建立連線，請重新登入後再試。",
  "chat.sendFailed": "傳送失敗（{code}）。",
  "chat.rooms": "對話",
  "chat.noRooms": "還沒有對話。",
  "chat.noRoomsHint": "申請行程被接受後會自動開啟。",
  "chat.memberCount": "{count} 位成員",
  "chat.selectRoom": "選擇一個對話開始聊天。",
  "chat.online": "在線",
  "chat.offline": "離線",
  "chat.statusOpen": "已連線",
  "chat.statusConnecting": "連線中…",
  "chat.statusClosed": "已中斷",
  "chat.noMessages": "還沒有訊息，打個招呼吧！",
  "chat.messageDeleted": "此訊息已被刪除",
  "chat.peerTyping": "對方正在輸入…",
  "chat.composerPlaceholder": "輸入訊息…（每秒最多 2 則）",
  "chat.send": "傳送",

  "notificationsPage.summary": "共 {total} 則",
  "notificationsPage.summaryUnread": "共 {total} 則，其中 {unread} 則未讀",
  "notificationsPage.unreadOnly": "只看未讀",
  "notificationsPage.emptyUnread": "沒有未讀通知。",
  "notificationsPage.empty": "目前沒有任何通知。",
  "notificationsPage.goExplore": "去探索行程",
  "notificationsPage.unreadBadge": "未讀",
  "notificationsPage.deleteAria": "刪除通知",
  "notificationsPage.openError": "無法開啟通知。",
  "notificationsPage.deleteError": "無法刪除通知。",
  "notificationsPage.pageIndicator": "第 {page} / {totalPages} 頁",

  "newTrip.subtitle": "只會公開國家與城市，請勿填寫精確住址或個人聯絡方式。",
  "newTrip.titleLabel": "行程標題",
  "newTrip.titlePlaceholder": "例如：東京櫻花季攝影之旅",
  "newTrip.descriptionLabel": "行程說明",
  "newTrip.descriptionPlaceholder": "說明你的計劃、想找什麼樣的旅伴、預算與節奏。",
  "newTrip.countryLabel": "目的地國家",
  "newTrip.cityLabel": "城市 / 地區（選填）",
  "newTrip.startLabel": "出發日期（選填）",
  "newTrip.endLabel": "回程日期（選填）",
  "newTrip.genderLabel": "徵求性別",
  "newTrip.countLabel": "徵求人數",
  "newTrip.styleLabel": "旅遊風格標籤",
  "newTrip.publishError": "發佈失敗，請稍後再試。",

  "trips.loadError": "載入行程時發生錯誤。",
  "trips.recommendedForYou": "為你推薦",
  "trips.recommendedWhy": "根據你的旅遊足跡、風格標籤與語言排序。",
  "trips.matchScore": "配對分 {score}",
  "trips.allTrips": "所有行程",
  "trips.filterCountry": "目的地國家",
  "trips.filterCountryPlaceholder": "例如 Japan",
  "trips.filterCity": "城市 / 地區",
  "trips.filterCityPlaceholder": "例如 Tokyo",
  "trips.filterBudget": "預算",
  "trips.filterStyle": "旅遊風格",
  "trips.filterStartDate": "出發日期（之後）",
  "trips.resetFilters": "重設篩選",
  "trips.prevPage": "上一頁",
  "trips.nextPage": "下一頁",
  "trips.pageIndicator": "第 {page} / {totalPages} 頁 · 共 {total} 篇",
  "trips.empty": "目前沒有符合條件的行程，試試成為第一個發佈的人！",

  "login.title": "登入",
  "login.subtitle": "使用你的香港手機號碼與密碼登入 Trip Mate。",
  "login.phoneLabel": "手機號碼",
  "login.passwordLabel": "密碼",
  "login.submit": "登入",
  "login.failed": "登入失敗，請稍後再試。",
  "login.noAccount": "還沒有帳號？",
  "login.registerLink": "免費註冊",

  "register.title": "建立帳號",
  "register.subtitle": "密碼需至少 8 位，並包含大小寫字母及數字。",
  "register.nicknameLabel": "顯示名稱",
  "register.phoneLabel": "香港手機號碼",
  "register.passwordLabel": "密碼",
  "register.consentPrivacy": "我已閱讀並同意《私隱政策》，並了解平台僅收集服務所需之個人資料。",
  "register.consentTerms": "我已閱讀並同意《服務條款》及平台免責聲明。",
  "register.consentRequired": "請先同意《私隱政策》與《服務條款》。",
  "register.submit": "建立帳號",
  "register.failed": "註冊失敗，請稍後再試。",
  "register.haveAccount": "已有帳號？",
  "register.loginLink": "登入",
  "register.createdTitle": "帳號已建立",
  "register.createdBody": "建議先驗證手機號碼，以提高配對成功率並解鎖完整功能。",
  "register.sendOtp": "發送驗證碼",
  "register.otpLabel": "驗證碼",
  "register.otpPlaceholder": "6 位數字",
  "register.verify": "驗證",
  "register.later": "稍後再說",
  "register.otpDevNotice": "開發模式：驗證碼為 {code}（正式環境將以簡訊發送）",
  "register.otpSent": "驗證碼已以簡訊發送。",
  "register.otpSendError": "無法發送驗證碼。",
  "register.otpVerified": "手機號碼驗證成功！",
  "register.otpVerifyError": "驗證失敗。",

  "home.badge": "香港 · 旅伴配對",
  "home.titleLine1": "找到與你同步的",
  "home.titleLine2": "下一位旅伴",
  "home.intro":
    "Trip Mate 讓你發佈行程、說明想去的地方與旅遊風格。系統會依據你的旅遊足跡與興趣，推薦最合拍的旅伴；內建即時通訊、封鎖與檢舉機制，讓配對過程安全可控。",
  "home.ctaStart": "立即開始",
  "home.ctaBrowse": "瀏覽行程",
  "home.stat.steps.value": "3 步",
  "home.stat.steps.label": "發佈 → 配對 → 對話",
  "home.stat.pdpo.value": "PDPO",
  "home.stat.pdpo.label": "符合香港私隱條例",
  "home.stat.rate.value": "2 msg/s",
  "home.stat.rate.label": "防騷擾速率限制",
  "home.feature.match.title": "智能配對",
  "home.feature.match.body": "依你的旅遊足跡、風格標籤與語言，為每篇行程計算配對分數，並解釋推薦原因。",
  "home.feature.chat.title": "安全對話",
  "home.feature.chat.body": "只有房間成員能收發訊息；封鎖後即時失效，偵測到電話號碼時主動提醒。",
  "home.feature.privacy.title": "私隱優先",
  "home.feature.privacy.body": "只收集必要資料、足跡可設公開或私人，並支援一鍵註銷帳號與資料匿名化。",
  "home.feature.review.title": "評價制度",
  "home.feature.review.body": "只有真正一起出遊過的旅伴才能互相評價，杜絕刷分與惡意負評。",
  "footer.disclaimer":
    "Trip Mate · 本平台僅提供資訊媒合服務。請參閱《私隱政策》及《服務條款》。",

  // --- nav ---
  "nav.trips": "探索行程",
  "nav.newTrip": "發佈行程",
  "nav.chat": "訊息",
  "nav.profile": "我的檔案",
  "nav.admin": "管理",
  "nav.logout": "登出",
  "nav.login": "登入",
  "nav.register": "免費註冊",
  "nav.language": "語言",

  // --- auth gate ---
  "authGate.title": "請先登入",
  "authGate.body": "Trip Mate 只向已註冊用戶展示旅伴資訊，以保障社群安全。",
  "authGate.action": "登入",

  // --- notifications ---
  "notifications.title": "通知",
  "notifications.unreadAria": "通知，{count} 則未讀",
  "notifications.markAllRead": "全部已讀",
  "notifications.empty": "目前沒有通知",
  "notifications.viewAll": "查看全部通知",
  "notifications.loadError": "無法載入通知。",
  "notifications.markReadError": "無法標為已讀。",

  // --- avatar uploader ---
  "avatar.change": "更換頭像",
  "avatar.remove": "移除頭像",
  "avatar.upload": "上傳頭像",
  "avatar.replace": "更換",
  "avatar.wrongType": "只接受 JPEG、PNG 或 WebP 格式的圖片。",
  "avatar.tooLarge": "圖片不可超過 5 MB（你選擇的是 {mb} MB）。",
  "avatar.updated": "頭像已更新。",
  "avatar.removed": "已移除頭像。",
  "avatar.uploadError": "上傳失敗，請稍後再試。",
  "avatar.removeError": "無法移除頭像。",
  "avatar.exifNotice": "上傳時會自動移除相片的 EXIF 位置資訊。",

  // --- disclaimer ---
  "disclaimer.heading": "安全提示：",
  "disclaimer.body": "本平台僅提供資訊媒合服務，線下見面與旅遊期間之個人人身安全、財物損失及消費糾紛，平台概不承擔法律責任。",
  "disclaimer.noMoney": "切勿在建立信任前進行金錢交易。",

  // --- trip card ---
  "tripCard.lookingFor": "徵 {count} 人",

  // --- enum labels ---
  "budget.BUDGET": "省錢",
  "budget.MODERATE": "中等",
  "budget.LUXURY": "豪華",
  "tripStatus.OPEN": "招募中",
  "tripStatus.CLOSED": "已滿團",
  "tripStatus.CANCELLED": "已取消",
  "applicationStatus.PENDING": "待回覆",
  "applicationStatus.ACCEPTED": "已接受",
  "applicationStatus.REJECTED": "已婉拒",
  "gender.MALE": "男",
  "gender.FEMALE": "女",
  "gender.OTHER": "其他",
  "gender.ANY": "不限",

  // Travel-style tags (profile form). The API value is the key; the label is
  // what a user reads, so these cannot stay as raw enum strings.
  "travelStyle.BACKPACKER": "背包客",
  "travelStyle.PHOTOGRAPHY": "攝影",
  "travelStyle.FOOD": "美食",
  "travelStyle.CULTURE": "文化",
  "travelStyle.HIKING": "登山",
  "travelStyle.NATURE": "自然",
  "travelStyle.ADVENTURE": "冒險",
  "travelStyle.CAFE": "咖啡",
  "travelStyle.NIGHTLIFE": "夜生活",
  "travelStyle.SHOPPING": "購物",
  "travelStyle.BEACH": "海灘",
  "travelStyle.SKI": "滑雪",

  // Review tags (post-trip rating form).
  "notificationType.APPLICATION_RECEIVED": "新申請",
  "notificationType.APPLICATION_ACCEPTED": "申請獲接受",
  "notificationType.APPLICATION_REJECTED": "申請被婉拒",
  "notificationType.NEW_MESSAGE": "新訊息",
  "notificationType.REVIEW_RECEIVED": "新評價",

  "reviewTag.PUNCTUAL": "準時",
  "reviewTag.FRIENDLY": "友善",
  "reviewTag.GOOD_PLANNER": "規劃周全",
  "reviewTag.FLEXIBLE": "隨和",
  "reviewTag.TIDY": "整潔",
  "reviewTag.FUN": "有趣",
  "reviewTag.KNOWLEDGEABLE": "見識廣",
  "reviewTag.RESPECTFUL": "尊重他人",
} as const;

export type MessageKey = keyof typeof zhHK;

// ---------------------------------------------------------------------------
// en — exhaustiveness is enforced by the type
// ---------------------------------------------------------------------------

const en: Record<MessageKey, string> = {
  "common.loading": "Loading…",
  "common.reload": "Reload",
  "common.cancel": "Cancel",
  "common.close": "Close",
  "common.all": "All",
  "common.none": "None",

  "meta.title": "Trip Mate — Travel companion matching",
  "meta.description":
    "Trip Mate is a travel-companion matching platform for Hong Kong travellers: post a trip, get matched with like-minded companions by travel history, and chat safely in real time.",
  "admin.title": "Admin",
  "admin.subtitle": "Report handling and the security audit trail. The audit trail is read-only and cannot be altered.",
  "admin.tabReports": "Reports",
  "admin.tabAudit": "Audit trail",
  "admin.statusOpen": "Open",
  "admin.statusReviewing": "Reviewing",
  "admin.statusActioned": "Actioned",
  "admin.statusDismissed": "Dismissed",
  "admin.reasonHarassment": "Harassment",
  "admin.reasonSpam": "Spam",
  "admin.reasonScam": "Scam",
  "admin.reasonInappropriate": "Inappropriate",
  "admin.reasonOther": "Other",
  "admin.auditBlocked": "User blocked",
  "admin.auditUnblocked": "User unblocked",
  "admin.auditReportSubmitted": "Report submitted",
  "admin.auditReportStatusChanged": "Report status changed",
  "admin.auditAccountDeleted": "Account deleted",
  "admin.auditAccountAnonymized": "Account anonymised",
  "admin.auditQueueViewed": "Report queue viewed",
  "admin.loadAuditError": "Could not load the audit trail.",
  "admin.auditTotal": "{total} entries",
  "admin.noAudit": "No audit entries match this filter.",
  "admin.actor": "Actor:",
  "admin.accountDeleted": "(account deleted)",
  "admin.targetProfile": "Subject:",
  "admin.targetOther": "Target:",
  "admin.pageIndicator": "Page {page} / {lastPage}",
  "admin.loadReportsError": "Could not load reports.",
  "admin.updateStatusError": "Could not update the status.",
  "admin.showing": "Showing {count}",
  "admin.showingOpen": ", {count} still open",
  "admin.noReports": "No reports match this filter.",
  "admin.reporter": "Reporter:",
  "admin.reported": "Reported:",
  "admin.markReviewing": "Mark reviewing",
  "admin.markActioned": "Actioned",
  "admin.dismiss": "Dismiss",
  "admin.forbiddenTitle": "No access",
  "admin.forbiddenBody": "This page is for administrators only. If you believe this is a mistake, contact the platform team.",
  "admin.backToTrips": "Back to trips",

  "myProfile.saved": "Profile saved.",
  "myProfile.saveError": "Could not save.",
  "myProfile.historyAdded": "Travel history added.",
  "myProfile.addError": "Could not add.",
  "myProfile.deleteError": "Could not delete.",
  "myProfile.eraseConfirm": "This cannot be undone. Continue?",
  "myProfile.eraseError": "Could not erase the account.",
  "myProfile.verified": "Verified",
  "myProfile.unverified": "Mobile not verified",
  "myProfile.tabProfile": "Profile",
  "myProfile.tabHistories": "Travel history",
  "myProfile.tabPrivacy": "Account & privacy",
  "myProfile.profileSubtitle": "A complete profile improves your match rate a great deal.",
  "myProfile.nicknameLabel": "Display name",
  "myProfile.bioLabel": "About me",
  "myProfile.notSet": "Not set",
  "myProfile.genderLabel": "Gender",
  "myProfile.styleLabel": "Travel style tags",
  "myProfile.languagesLabel": "Languages (comma separated)",
  "myProfile.saveChanges": "Save changes",
  "myProfile.addHistoryTitle": "Add travel history",
  "myProfile.addHistoryHint": "Travel history is the strongest signal for matching; private entries are visible only to you.",
  "myProfile.countryLabel": "Country",
  "myProfile.cityLabel": "City / region",
  "myProfile.startDateLabel": "Start date",
  "myProfile.endDateLabel": "End date",
  "myProfile.visibilityLabel": "Visibility",
  "myProfile.public": "Public",
  "myProfile.summaryLabel": "Summary",
  "myProfile.summaryPlaceholder": "What you will remember most from this trip…",
  "myProfile.addHistory": "Add entry",
  "myProfile.myHistories": "My history ({count})",
  "myProfile.noHistories": "No travel history yet.",
  "myProfile.deleteAria": "Delete",
  "myProfile.privacyTitle": "Account & privacy",
  "myProfile.privacyBody": "Under Hong Kong's Personal Data (Privacy) Ordinance you have the right to have your personal data erased. \"Anonymise\" keeps your trip content but removes your identity; \"Delete completely\" removes everything permanently.",
  "myProfile.passwordPlaceholder": "Enter your password to confirm",
  "myProfile.modeAnonymize": "Anonymise",
  "myProfile.modeHardDelete": "Delete completely",
  "myProfile.eraseAccount": "Erase account",

  "profileView.notFound": "User not found, or you do not have access to this profile.",
  "profileView.reviewSent": "Review submitted — thanks for sharing!",
  "profileView.reviewError": "Could not submit the review.",
  "profileView.statTrips": "Trips",
  "profileView.statReviews": "Reviews",
  "profileView.statAvg": "Average",
  "profileView.travelStyles": "Travel styles",
  "profileView.languages": "Languages",
  "profileView.writeReview": "Write a review",
  "profileView.reviewTitle": "Review {nickname}",
  "profileView.reviewHint": "Only travellers who completed a trip together can leave a review, which keeps them honest.",
  "profileView.ratingLabel": "Rating",
  "profileView.tagsLabel": "Tags",
  "profileView.commentLabel": "Comment",
  "profileView.commentPlaceholder": "Share what travelling together was like…",
  "profileView.submitReview": "Submit review",
  "profileView.editMine": "Edit my profile",
  "profileView.histories": "Travel history ({count})",
  "profileView.noHistories": "No travel history shared yet.",
  "profileView.private": "Private",
  "profileView.reviews": "Reviews ({count})",
  "profileView.noReviews": "No reviews yet.",

  "tripDetail.notFound": "Trip not found, or you do not have access to it.",
  "tripDetail.applied": "Application sent! You can chat once they accept.",
  "tripDetail.applyError": "Could not send the application. Please try again.",
  "tripDetail.openChatError": "Could not open the conversation.",
  "tripDetail.openGroupError": "Could not open the trip group.",
  "tripDetail.blockConfirm": "Block this user? They will no longer be able to interact with you.",
  "tripDetail.blocked": "This user has been blocked.",
  "tripDetail.blockError": "Could not block this user.",
  "tripDetail.reportPrompt": "Enter a reason (harassment / spam / scam / inappropriate / other)",
  "tripDetail.reported": "Your report has been received. We will look into it shortly.",
  "tripDetail.reportError": "Could not submit the report.",
  "tripDetail.accepted": "Application accepted and the conversation is open.",
  "tripDetail.rejected": "The application has been declined.",
  "tripDetail.actionError": "Something went wrong.",
  "tripDetail.lookingForGender": "Looking for: {value}",
  "tripDetail.lookingForCount": "Looking for {count}",
  "tripDetail.dm": "Message",
  "tripDetail.block": "Block",
  "tripDetail.report": "Report",
  "tripDetail.applyCta": "Apply to join",
  "tripDetail.applyDialogBody": "Introduce yourself briefly and say why you would like to travel together. A conversation opens automatically once they accept.",
  "tripDetail.applyMessageLabel": "Application message",
  "tripDetail.applyMessagePlaceholder": "Hi! I am also planning to go…",
  "tripDetail.applications": "Applications received ({count})",
  "tripDetail.applicationsHint": "Accept an application to open the trip group and discuss with everyone who joined.",
  "tripDetail.openGroup": "Open the trip group chat",
  "tripDetail.groupNeedsMember": "Accept at least one companion first",
  "tripDetail.group": "Trip group",
  "tripDetail.noApplications": "No applications yet.",
  "tripDetail.traveller": "Traveller",
  "tripDetail.submitApplication": "Send application",
  "tripDetail.accept": "Accept",
  "tripDetail.reject": "Decline",

  "chat.direct": "Direct message",
  "chat.group": "Trip group",
  "chat.loadRoomsError": "Could not load your conversations.",
  "chat.loadMessagesError": "Could not load messages.",
  "chat.wsForbidden": "Connection refused: you do not have access to this conversation.",
  "chat.wsFailed": "Could not connect. Please log in again and retry.",
  "chat.sendFailed": "Could not send ({code}).",
  "chat.rooms": "Conversations",
  "chat.noRooms": "No conversations yet.",
  "chat.noRoomsHint": "One opens automatically when your application is accepted.",
  "chat.memberCount": "{count} members",
  "chat.selectRoom": "Pick a conversation to start chatting.",
  "chat.online": "Online",
  "chat.offline": "Offline",
  "chat.statusOpen": "Connected",
  "chat.statusConnecting": "Connecting…",
  "chat.statusClosed": "Disconnected",
  "chat.noMessages": "No messages yet — say hello!",
  "chat.messageDeleted": "This message was deleted",
  "chat.peerTyping": "Typing…",
  "chat.composerPlaceholder": "Type a message… (max 2 per second)",
  "chat.send": "Send",

  "notificationsPage.summary": "{total} notifications",
  "notificationsPage.summaryUnread": "{total} notifications, {unread} unread",
  "notificationsPage.unreadOnly": "Unread only",
  "notificationsPage.emptyUnread": "No unread notifications.",
  "notificationsPage.empty": "No notifications yet.",
  "notificationsPage.goExplore": "Explore trips",
  "notificationsPage.unreadBadge": "Unread",
  "notificationsPage.deleteAria": "Delete notification",
  "notificationsPage.openError": "Could not open the notification.",
  "notificationsPage.deleteError": "Could not delete the notification.",
  "notificationsPage.pageIndicator": "Page {page} / {totalPages}",

  "newTrip.subtitle": "Only the country and city are published — never a precise address or personal contact details.",
  "newTrip.titleLabel": "Trip title",
  "newTrip.titlePlaceholder": "e.g. Tokyo cherry blossom photography trip",
  "newTrip.descriptionLabel": "Trip description",
  "newTrip.descriptionPlaceholder": "Describe your plan, the kind of companion you are looking for, the budget and the pace.",
  "newTrip.countryLabel": "Destination country",
  "newTrip.cityLabel": "City / region (optional)",
  "newTrip.startLabel": "Departure date (optional)",
  "newTrip.endLabel": "Return date (optional)",
  "newTrip.genderLabel": "Looking for",
  "newTrip.countLabel": "How many companions",
  "newTrip.styleLabel": "Travel style tags",
  "newTrip.publishError": "Could not publish the trip. Please try again.",

  "trips.loadError": "Could not load trips.",
  "trips.recommendedForYou": "Recommended for you",
  "trips.recommendedWhy": "Ranked by your travel history, style tags and languages.",
  "trips.matchScore": "Match {score}",
  "trips.allTrips": "All trips",
  "trips.filterCountry": "Destination country",
  "trips.filterCountryPlaceholder": "e.g. Japan",
  "trips.filterCity": "City / region",
  "trips.filterCityPlaceholder": "e.g. Tokyo",
  "trips.filterBudget": "Budget",
  "trips.filterStyle": "Travel style",
  "trips.filterStartDate": "Departing on or after",
  "trips.resetFilters": "Reset filters",
  "trips.prevPage": "Previous",
  "trips.nextPage": "Next",
  "trips.pageIndicator": "Page {page} / {totalPages} · {total} trips",
  "trips.empty": "No trips match these filters yet — be the first to post one!",

  "login.title": "Log in",
  "login.subtitle": "Log in to Trip Mate with your Hong Kong mobile number and password.",
  "login.phoneLabel": "Mobile number",
  "login.passwordLabel": "Password",
  "login.submit": "Log in",
  "login.failed": "Could not log in. Please try again.",
  "login.noAccount": "No account yet?",
  "login.registerLink": "Sign up free",

  "register.title": "Create your account",
  "register.subtitle": "Your password needs at least 8 characters, including upper case, lower case and a digit.",
  "register.nicknameLabel": "Display name",
  "register.phoneLabel": "Hong Kong mobile number",
  "register.passwordLabel": "Password",
  "register.consentPrivacy": "I have read and agree to the Privacy Policy, and understand that the platform collects only the personal data needed to provide the service.",
  "register.consentTerms": "I have read and agree to the Terms of Service and the platform disclaimer.",
  "register.consentRequired": "Please agree to the Privacy Policy and the Terms of Service first.",
  "register.submit": "Create account",
  "register.failed": "Could not register. Please try again.",
  "register.haveAccount": "Already have an account?",
  "register.loginLink": "Log in",
  "register.createdTitle": "Account created",
  "register.createdBody": "We recommend verifying your mobile number to improve your match rate and unlock everything.",
  "register.sendOtp": "Send code",
  "register.otpLabel": "Verification code",
  "register.otpPlaceholder": "6 digits",
  "register.verify": "Verify",
  "register.later": "Later",
  "register.otpDevNotice": "Development mode: your code is {code} (sent by SMS in production)",
  "register.otpSent": "The code has been sent by SMS.",
  "register.otpSendError": "Could not send the code.",
  "register.otpVerified": "Mobile number verified!",
  "register.otpVerifyError": "Verification failed.",

  "home.badge": "Hong Kong · Travel companion matching",
  "home.titleLine1": "Find the next",
  "home.titleLine2": "traveller in sync with you",
  "home.intro":
    "Trip Mate lets you post a trip and describe where you want to go and how you like to travel. We match you with compatible companions based on your travel history and interests, with built-in chat, blocking and reporting to keep it safe.",
  "home.ctaStart": "Get started",
  "home.ctaBrowse": "Browse trips",
  "home.stat.steps.value": "3 steps",
  "home.stat.steps.label": "Post → Match → Chat",
  "home.stat.pdpo.value": "PDPO",
  "home.stat.pdpo.label": "Compliant with HK privacy law",
  "home.stat.rate.value": "2 msg/s",
  "home.stat.rate.label": "Anti-harassment rate limit",
  "home.feature.match.title": "Smart matching",
  "home.feature.match.body": "Every trip gets a match score from your travel history, style tags and languages — with the reasons spelled out.",
  "home.feature.chat.title": "Safe conversations",
  "home.feature.chat.body": "Only room members can send or receive messages. Blocks take effect immediately, and phone numbers are flagged as you type.",
  "home.feature.privacy.title": "Privacy first",
  "home.feature.privacy.body": "We collect only what is needed, your travel history can be public or private, and you can erase or anonymise your account in one step.",
  "home.feature.review.title": "Verified reviews",
  "home.feature.review.body": "Only travellers who actually completed a trip together can rate each other, which rules out score-gaming and drive-by negativity.",
  "footer.disclaimer":
    "Trip Mate · This platform only provides information matching. See our Privacy Policy and Terms of Service.",

  "nav.trips": "Explore trips",
  "nav.newTrip": "Post a trip",
  "nav.chat": "Messages",
  "nav.profile": "My profile",
  "nav.admin": "Admin",
  "nav.logout": "Log out",
  "nav.login": "Log in",
  "nav.register": "Sign up free",
  "nav.language": "Language",

  "authGate.title": "Please log in",
  "authGate.body": "Trip Mate only shows traveller profiles to registered users, to keep the community safe.",
  "authGate.action": "Log in",

  "notifications.title": "Notifications",
  "notifications.unreadAria": "Notifications, {count} unread",
  "notifications.markAllRead": "Mark all read",
  "notifications.empty": "No notifications yet",
  "notifications.viewAll": "View all notifications",
  "notifications.loadError": "Could not load notifications.",
  "notifications.markReadError": "Could not mark as read.",

  "avatar.change": "Change avatar",
  "avatar.remove": "Remove avatar",
  "avatar.upload": "Upload avatar",
  "avatar.replace": "Change",
  "avatar.wrongType": "Only JPEG, PNG or WebP images are accepted.",
  "avatar.tooLarge": "Images must be under 5 MB (yours is {mb} MB).",
  "avatar.updated": "Avatar updated.",
  "avatar.removed": "Avatar removed.",
  "avatar.uploadError": "Upload failed. Please try again.",
  "avatar.removeError": "Could not remove the avatar.",
  "avatar.exifNotice": "EXIF location data is stripped from photos on upload.",

  "disclaimer.heading": "Safety notice: ",
  "disclaimer.body": "This platform only provides information matching. It accepts no legal liability for personal safety, loss of property or disputes arising from meeting offline or during travel.",
  "disclaimer.noMoney": "Never send money before trust is established.",

  "tripCard.lookingFor": "Looking for {count}",

  "budget.BUDGET": "Budget",
  "budget.MODERATE": "Moderate",
  "budget.LUXURY": "Luxury",
  "tripStatus.OPEN": "Open",
  "tripStatus.CLOSED": "Full",
  "tripStatus.CANCELLED": "Cancelled",
  "applicationStatus.PENDING": "Pending",
  "applicationStatus.ACCEPTED": "Accepted",
  "applicationStatus.REJECTED": "Declined",
  "gender.MALE": "Male",
  "gender.FEMALE": "Female",
  "gender.OTHER": "Other",
  "gender.ANY": "Any",

  "travelStyle.BACKPACKER": "Backpacker",
  "travelStyle.PHOTOGRAPHY": "Photography",
  "travelStyle.FOOD": "Food",
  "travelStyle.CULTURE": "Culture",
  "travelStyle.HIKING": "Hiking",
  "travelStyle.NATURE": "Nature",
  "travelStyle.ADVENTURE": "Adventure",
  "travelStyle.CAFE": "Cafés",
  "travelStyle.NIGHTLIFE": "Nightlife",
  "travelStyle.SHOPPING": "Shopping",
  "travelStyle.BEACH": "Beach",
  "travelStyle.SKI": "Ski",

  "notificationType.APPLICATION_RECEIVED": "New application",
  "notificationType.APPLICATION_ACCEPTED": "Application accepted",
  "notificationType.APPLICATION_REJECTED": "Application declined",
  "notificationType.NEW_MESSAGE": "New message",
  "notificationType.REVIEW_RECEIVED": "New review",

  "reviewTag.PUNCTUAL": "Punctual",
  "reviewTag.FRIENDLY": "Friendly",
  "reviewTag.GOOD_PLANNER": "Good planner",
  "reviewTag.FLEXIBLE": "Flexible",
  "reviewTag.TIDY": "Tidy",
  "reviewTag.FUN": "Fun",
  "reviewTag.KNOWLEDGEABLE": "Knowledgeable",
  "reviewTag.RESPECTFUL": "Respectful",
};

export const DICTIONARIES: Record<Locale, Record<MessageKey, string>> = {
  "zh-HK": zhHK,
  en,
};

// ---------------------------------------------------------------------------
// Lookup helpers
// ---------------------------------------------------------------------------

/**
 * Replace `{name}` placeholders.
 *
 * An unknown placeholder is left **as-is** rather than blanked, so a missing
 * variable shows up as a literal `{count}` in the UI — visible and reportable —
 * instead of silently rendering "You have  new messages".
 */
export function interpolate(
  template: string,
  vars?: Record<string, string | number>,
): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );
}

export function translate(
  locale: Locale,
  key: MessageKey,
  vars?: Record<string, string | number>,
): string {
  const table = DICTIONARIES[locale] ?? DICTIONARIES[DEFAULT_LOCALE];
  // Falling back to the default locale keeps a half-translated build usable
  // rather than showing raw keys to users.
  const template = table[key] ?? DICTIONARIES[DEFAULT_LOCALE][key] ?? key;
  return interpolate(template, vars);
}

/** Groups of server-supplied enum values that have display labels. */
export type LabelGroup =
  | "budget"
  | "tripStatus"
  | "applicationStatus"
  | "gender"
  | "travelStyle"
  | "reviewTag"
  | "notificationType";

/**
 * Maps API enum values to message keys.
 *
 * An explicit table rather than a template-literal cast (`t(\`${group}.${value}\`)`)
 * so that a wrong key is a compile error. A value the table does not know about
 * degrades to the raw API string, which is ugly but never blank — an unrecognised
 * enum from a newer backend should be visible, not invisible.
 */
const LABEL_KEYS: Record<LabelGroup, Record<string, MessageKey>> = {
  budget: {
    BUDGET: "budget.BUDGET",
    MODERATE: "budget.MODERATE",
    LUXURY: "budget.LUXURY",
  },
  tripStatus: {
    OPEN: "tripStatus.OPEN",
    CLOSED: "tripStatus.CLOSED",
    CANCELLED: "tripStatus.CANCELLED",
  },
  applicationStatus: {
    PENDING: "applicationStatus.PENDING",
    ACCEPTED: "applicationStatus.ACCEPTED",
    REJECTED: "applicationStatus.REJECTED",
  },
  gender: {
    MALE: "gender.MALE",
    FEMALE: "gender.FEMALE",
    OTHER: "gender.OTHER",
    ANY: "gender.ANY",
  },
  travelStyle: {
    BACKPACKER: "travelStyle.BACKPACKER",
    PHOTOGRAPHY: "travelStyle.PHOTOGRAPHY",
    FOOD: "travelStyle.FOOD",
    CULTURE: "travelStyle.CULTURE",
    HIKING: "travelStyle.HIKING",
    NATURE: "travelStyle.NATURE",
    ADVENTURE: "travelStyle.ADVENTURE",
    CAFE: "travelStyle.CAFE",
    NIGHTLIFE: "travelStyle.NIGHTLIFE",
    SHOPPING: "travelStyle.SHOPPING",
    BEACH: "travelStyle.BEACH",
    SKI: "travelStyle.SKI",
  },
  notificationType: {
    APPLICATION_RECEIVED: "notificationType.APPLICATION_RECEIVED",
    APPLICATION_ACCEPTED: "notificationType.APPLICATION_ACCEPTED",
    APPLICATION_REJECTED: "notificationType.APPLICATION_REJECTED",
    NEW_MESSAGE: "notificationType.NEW_MESSAGE",
    REVIEW_RECEIVED: "notificationType.REVIEW_RECEIVED",
  },
  reviewTag: {
    PUNCTUAL: "reviewTag.PUNCTUAL",
    FRIENDLY: "reviewTag.FRIENDLY",
    GOOD_PLANNER: "reviewTag.GOOD_PLANNER",
    FLEXIBLE: "reviewTag.FLEXIBLE",
    TIDY: "reviewTag.TIDY",
    FUN: "reviewTag.FUN",
    KNOWLEDGEABLE: "reviewTag.KNOWLEDGEABLE",
    RESPECTFUL: "reviewTag.RESPECTFUL",
  },
};

export function labelKey(group: LabelGroup, value: string | null | undefined): MessageKey | null {
  if (!value) return null;
  return LABEL_KEYS[group][value] ?? null;
}

/** BCP-47 tag for `Intl` formatting. */
export function intlLocale(locale: Locale): string {
  return locale === "en" ? "en-GB" : "zh-HK";
}

/**
 * BCP-47 tag for the `<html lang>` attribute.
 *
 * Kept separate from `intlLocale` because the two are not the same thing:/n * `zh-Hant-HK` is the precise script+region tag a document should declare,
 * while `zh-HK` is what `Intl` expects. Using one for both would either
 * misdeclare the document or format dates with the wrong calendar rules.
 */
export function htmlLang(locale: Locale): string {
  return locale === "en" ? "en" : "zh-Hant-HK";
}
