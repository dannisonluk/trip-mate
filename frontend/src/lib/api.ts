// Thin API client: in-memory access token + HttpOnly refresh cookie, auto-refresh on 401.

import type {
  ApplicationStatus,
  AuditAction,
  ChatRoom,
  CitySuggestionPage,
  CitySuggestion,
  Message,
  Notification,
  NotificationPage,
  PaginatedAuditLogs,
  PaginatedTrips,
  ProfilePrivate,
  ProfilePublic,
  Recommendation,
  Report,
  ReportStatus,
  Review,
  ReviewSummary,
  TravelHistory,
  TripApplication,
  TripFilters,
  TripPost,
  TripPostDetail,
  UserOut,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
export const API_PREFIX = "/api/v1";

let accessToken: string | null = null;

/** In-flight refresh, shared so concurrent 401s cannot replay the cookie. */
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Bumped by every explicit session change (login, register, logout).
 *
 * `refreshInFlight = null` only unlinks the slot; it does not stop the promise
 * that is already awaiting `fetch`. That promise still holds a body and, when it
 * resolves, still calls `setAccessToken(...)` — so a refresh issued under the
 * *previous* session could reinstall that session's token after a login or
 * logout had already moved on. Clearing the slot was never sufficient on its
 * own, because the write happens inside the attempt, not in the caller that
 * clears it.
 *
 * The generation makes "is this result still wanted?" answerable: an attempt
 * captures the value at the moment it starts and refuses to write its token if
 * the counter has moved since.
 */
let sessionGeneration = 0;

/**
 * Set (or clear) the access token.
 *
 * Also drops any in-flight refresh: every caller of this is an *explicit*
 * session change (login, register, logout), and a refresh that resolves after
 * one of those must not reinstall the previous user's token.
 */
export function setAccessToken(token: string | null) {
  accessToken = token;
  refreshInFlight = null;
  sessionGeneration += 1;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "Request failed");
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Flatten a FastAPI error payload into a single human-readable string.
 *
 * `fallback` is **required**: this module is not a React component, so it cannot
 * reach the translation hook. Every call site already knows which message fits,
 * and passing it explicitly is what keeps a hard-coded default language from
 * living in a shared library.
 *
 * `t` is optional and only needed when the server may answer with a structured
 * `{code, params}` detail — the content filter does exactly that, because
 * sending a rendered Chinese sentence would read wrong for an English-locale
 * caller. Without `t` such a detail degrades to `fallback`, which is no worse
 * than the pre-existing behaviour for an unrecognised shape.
 */
export function errorMessage(
  err: unknown,
  fallback: string,
  // Structural, not `MessageKey`: importing the i18n module here would make this
  // shared HTTP client depend on the dictionary (and the harness that compiles
  // this file standalone would have to stub it). The caller's `t` is assignable
  // to this shape, so call sites pass `t` unchanged.
  t?: (key: never, vars?: Record<string, string | number>) => string,
): string {
  if (!(err instanceof ApiError)) return fallback;
  const d = err.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    // A locale-neutral separator on purpose: this module has no access to the
    // active locale, and a full-width semicolon would be wrong in English while a
    // half-width one reads oddly in Chinese. A middle dot is idiomatic in both.
    return d
      .map((item: any) => item?.msg ?? JSON.stringify(item))
      .join(" · ");
  }
  if (t && d && typeof d === "object" && typeof (d as { code?: unknown }).code === "string") {
    const { code, params } = d as { code: string; params?: Record<string, string | number> };
    // The key is resolved by name here rather than through the dictionary's
    // table, because this module cannot import it. The prefix keeps the lookup
    // from colliding with an unrelated key, and an unknown code falls through to
    // `fallback` — the message must stay actionable, and a raw key is not.
    const key = `contentRejected.${code}` as never;
    const resolved = t(key, params);
    // `translate` returns the key itself when it has no entry, which is how an
    // unknown code is detected without a table to consult.
    return resolved === (key as unknown as string) ? fallback : resolved;
  }
  return fallback;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  skipRefresh?: boolean;
}

async function rawRequest(path: string, options: RequestOptions = {}): Promise<Response> {
  const { skipRefresh: _skip, body, ...init } = options;

  const headers = new Headers(init.headers);
  if (body !== undefined && !(body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  return fetch(`${API_URL}${API_PREFIX}${path}`, {
    ...init,
    headers,
    credentials: "include",
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let response = await rawRequest(path, options);

  if (response.status === 401 && !options.skipRefresh) {
    if (await tryRefresh()) response = await rawRequest(path, options);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) throw new ApiError(response.status, data?.detail ?? response.statusText);
  return data as T;
}

export async function tryRefresh(): Promise<boolean> {
  // Single-flight. The backend rotates the refresh token on every success and
  // treats a *replayed* token as compromise: it bumps the user's epoch and
  // invalidates every session (see `auth.py::refresh`). Two concurrent callers
  // therefore do not merely both fail — the loser destroys the winner's session
  // and the user is logged out. That is reachable on any page that fires more
  // than one authenticated request, because each 401 triggers its own refresh.
  // Sharing one in-flight promise means only one request ever carries the cookie.
  if (refreshInFlight) return refreshInFlight;

  // Captured before the request, not after: the question is whether the session
  // changed *while this attempt was outstanding*.
  const generation = sessionGeneration;

  const attempt = (async (): Promise<boolean> => {
    try {
      const res = await fetch(`${API_URL}${API_PREFIX}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) return false;
      const data = await res.json();
      // The guard that actually matters. `setAccessToken` cannot be used here:
      // it bumps the generation, so calling it would invalidate the very attempt
      // this check is protecting. Writing the token directly keeps the decision
      // in one place — did anyone explicitly change the session while we were
      // waiting? If so, this token belongs to a session that no longer exists.
      if (generation !== sessionGeneration) return false;
      accessToken = data.access_token;
      return true;
    } catch {
      return false;
    }
  })();

  refreshInFlight = attempt;
  try {
    return await attempt;
  } finally {
    // Only clear the slot if it still holds *this* attempt. `setAccessToken`
    // clears it eagerly on an explicit session change, and a later refresh may
    // already have installed a new one — clearing unconditionally would drop it.
    if (refreshInFlight === attempt) refreshInFlight = null;
  }
}

function qs(params: Record<string, string | number | boolean | undefined | null>) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    // Skip empty values so the backend sees an absent param rather than "".
    // Note `false` must still be sent (e.g. unread_only=false is meaningful).
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  });
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const api = {
  // --- auth ---
  register: (body: {
    phone_number: string;
    password: string;
    nickname: string;
    consent_privacy: boolean;
    consent_terms: boolean;
  }) => request<{ access_token: string; expires_in: number }>("/auth/register", { method: "POST", body }),

  login: (body: { phone_number: string; password: string }) =>
    request<{ access_token: string; expires_in: number }>("/auth/login", { method: "POST", body }),

  logout: () => request<void>("/auth/logout", { method: "POST", skipRefresh: true }),
  me: () => request<UserOut>("/auth/me"),

  requestOtp: (phone_number: string) =>
    request<{ sent: boolean; expires_in: number; dev_code: string | null }>("/auth/otp/request", {
      method: "POST",
      body: { phone_number },
    }),
  verifyOtp: (phone_number: string, code: string) =>
    request<void>("/auth/otp/verify", { method: "POST", body: { phone_number, code } }),

  // --- profiles ---
  myProfile: () => request<ProfilePrivate>("/profiles/me"),
  updateProfile: (body: Record<string, unknown>) =>
    request<ProfilePrivate>("/profiles/me", { method: "PUT", body }),
  getProfile: (userId: string) => request<ProfilePublic>(`/profiles/${userId}`),
  listHistories: (userId: string) => request<TravelHistory[]>(`/profiles/${userId}/histories`),
  addHistory: (body: Record<string, unknown>) =>
    request<TravelHistory>("/profiles/me/histories", { method: "POST", body }),
  deleteHistory: (id: string) =>
    request<void>(`/profiles/me/histories/${id}`, { method: "DELETE" }),
  uploadUrl: (contentType = "image/jpeg") =>
    request<{ object_key: string; upload_url: string; expires_in: number }>(
      `/profiles/me/upload-url${qs({ content_type: contentType })}`,
      { method: "POST" },
    ),
  blockProfile: (id: string) => request<unknown>(`/profiles/${id}/block`, { method: "POST" }),
  unblockProfile: (id: string) => request<void>(`/profiles/${id}/block`, { method: "DELETE" }),
  reportProfile: (body: { reported_profile_id: string; reason: string; detail?: string }) =>
    request<unknown>("/reports", { method: "POST", body }),
  deleteAccount: (body: { password: string; mode: string }) =>
    request<void>("/users/me", { method: "DELETE", body }),

  // --- cities (reference data) ---
  /** Ranked suggestions for a prefix. An empty `q` is legal and returns an empty
   *  list — that is what the picker sends when the user clears the field. */
  suggestCities: (params: { q: string; country?: string; limit?: number }) =>
    request<CitySuggestionPage>(
      `/cities${qs(params as Record<string, string | number | undefined>)}`,
    ),

  /** One city by GeoNames id, with its coordinates.
   *
   *  Distinct from `suggestCities` on purpose: a trip stores `city_id`, and
   *  re-deriving its location from a *name* search can return a different city
   *  with the same name. Answers 404 once the row is gone (a GeoNames re-import
   *  can remove it), which the caller treats as "no map", not as a failure. */
  getCity: (cityId: number) => request<CitySuggestion>(`/cities/${cityId}`),

  // --- trips ---
  listTrips: (filters: TripFilters = {}) =>
    request<PaginatedTrips>(`/trips${qs(filters as Record<string, string | number | undefined>)}`),
  recommendations: (limit = 6) => request<Recommendation[]>(`/trips/recommendations${qs({ limit })}`),
  myTrips: () => request<TripPost[]>("/trips/mine"),
  getTrip: (id: string) => request<TripPostDetail>(`/trips/${id}`),
  createTrip: (body: Record<string, unknown>) =>
    request<TripPost>("/trips", { method: "POST", body }),
  applyToTrip: (id: string, message: string) =>
    request<TripApplication>(`/trips/${id}/apply`, { method: "POST", body: { message } }),
  decideApplication: (applicationId: string, decision: ApplicationStatus) =>
    request<TripApplication>(`/trips/applications/${applicationId}${qs({ decision })}`, {
      method: "PATCH",
    }),

  // --- reviews ---
  createReview: (body: {
    reviewee_id: string;
    trip_post_id?: string | null;
    rating: number;
    tags?: string[];
    comment?: string;
  }) => request<Review>("/reviews", { method: "POST", body }),
  listReviews: (userId: string) => request<Review[]>(`/profiles/${userId}/reviews`),
  reviewSummary: (userId: string) =>
    request<ReviewSummary>(`/profiles/${userId}/reviews/summary`),

  // --- chat ---
  listRooms: () => request<ChatRoom[]>("/chat/rooms"),
  createRoom: (body: Record<string, unknown>) =>
    request<ChatRoom>("/chat/rooms", { method: "POST", body }),
  listMessages: (roomId: string, page = 1) =>
    request<{ items: Message[]; total: number }>(`/chat/rooms/${roomId}/messages${qs({ page })}`),

  // --- notifications ---
  listNotifications: (params: { page?: number; page_size?: number; unread_only?: boolean } = {}) =>
    request<NotificationPage>(`/notifications${qs(params as Record<string, string | number | boolean | undefined>)}`),
  unreadCount: () => request<{ unread: number }>("/notifications/unread-count"),
  markNotificationRead: (id: string) =>
    request<Notification>(`/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () =>
    request<{ unread: number }>("/notifications/read-all", { method: "POST" }),
  deleteNotification: (id: string) =>
    request<void>(`/notifications/${id}`, { method: "DELETE" }),

  // --- uploads ---
  // Multipart: the client sends the raw bytes so the server can validate magic
  // bytes and strip EXIF before storing. Never send a data: URL — the server
  // would reject it, and base64 in a JSON body inflates the payload ~33%.
  uploadImage: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ url: string; content_type: string }>("/uploads/image", {
      method: "POST",
      body: form,
    });
  },

  // --- admin ---
  listReports: (statusFilter?: ReportStatus | "") =>
    request<Report[]>(`/admin/reports${qs({ status_filter: statusFilter || undefined })}`),
  updateReportStatus: (reportId: string, newStatus: ReportStatus) =>
    request<Report>(`/admin/reports/${reportId}${qs({ new_status: newStatus })}`, {
      method: "PATCH",
    }),
  listAuditLogs: (params: { action?: AuditAction | ""; page?: number; limit?: number } = {}) =>
    request<PaginatedAuditLogs>(
      `/admin/audit-logs${qs(params as Record<string, string | number | undefined>)}`,
    ),
};
