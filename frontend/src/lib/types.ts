// Shared API types — mirror the backend Pydantic schemas.

export type BudgetType = "BUDGET" | "MODERATE" | "LUXURY";
export type TripStatus = "OPEN" | "CLOSED" | "CANCELLED";
export type ApplicationStatus = "PENDING" | "ACCEPTED" | "REJECTED";
export type Gender = "MALE" | "FEMALE" | "OTHER";
export type TargetGender = "MALE" | "FEMALE" | "ANY";

export interface UserOut {
  id: string;
  phone_number: string;
  is_verified: boolean;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TravelHistory {
  id: string;
  country: string;
  city?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  budget_type: BudgetType;
  summary?: string | null;
  photo_urls: string[];
  is_public: boolean;
  created_at: string;
}

export interface ProfileStats {
  trips_count: number;
  reviews_count: number;
  average_rating: number | null;
}

export interface ProfilePublic {
  id: string;
  nickname: string;
  avatar_url?: string | null;
  bio?: string | null;
  mbti?: string | null;
  travel_style_tags: string[];
  languages: string[];
  gender?: Gender | null;
  created_at: string;
  stats: ProfileStats;
}

export interface ProfilePrivate extends ProfilePublic {
  user_id: string;
  is_verified: boolean;
  updated_at: string;
}

export interface ProfileSummary {
  id: string;
  nickname: string;
  avatar_url?: string | null;
  mbti?: string | null;
  gender?: Gender | null;
}

export interface TripPost {
  id: string;
  creator_id: string;
  creator?: ProfileSummary | null;
  title: string;
  description: string;
  destination_country: string;
  destination_city?: string | null;
  /** GeoNames `geonameid`, or null when the trip has no city (the field is
   *  optional). Coordinates are resolved from this, never sent by the client;
   *  it also becomes null if the reference row is later removed. */
  city_id?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  budget_type: BudgetType;
  target_gender: TargetGender;
  tags: string[];
  looking_for_count: number;
  status: TripStatus;
  created_at: string;
}

export interface TripApplication {
  id: string;
  trip_post_id: string;
  applicant_id: string;
  message?: string | null;
  status: ApplicationStatus;
  created_at: string;
  applicant?: ProfileSummary | null;
}

export interface TripPostDetail extends TripPost {
  applications: TripApplication[];
}

export interface Recommendation {
  post: TripPost;
  score: number;
  reasons: string[];
}

export interface PaginatedTrips {
  items: TripPost[];
  total: number;
  page: number;
  limit: number;
}

/** One city from the reference table, as returned by `GET /cities`.
 *
 * The extra fields beyond `name` are not decoration: `Santa Cruz` occurs 16
 * times in the real 69,740-row table, so a suggestion list of bare names cannot
 * be chosen from. Everything needed to tell candidates apart travels with each
 * one. */
export interface CitySuggestion {
  /** GeoNames `geonameid` — the value stored as `city_id`. */
  id: number;
  /** Display name, with diacritics (`Sant Julià de Lòria`). */
  name: string;
  country_code: string;
  country_name: string;
  /** First-level division (`Osaka`, `California`); null for city-states. */
  admin1_name: string | null;
  population: number;
  latitude: number;
  longitude: number;
}

export interface CitySuggestionPage {
  /** Echoed normalised term, so a client can discard superseded responses. */
  query: string;
  suggestions: CitySuggestion[];
}

export interface Review {
  id: string;
  reviewer_id: string;
  reviewee_id: string;
  trip_post_id?: string | null;
  rating: number;
  tags: string[];
  comment?: string | null;
  created_at: string;
  reviewer?: ProfileSummary | null;
}

export interface ReviewSummary {
  count: number;
  average_rating: number | null;
  distribution: Record<string, number>;
}

export interface RoomMember {
  profile_id: string;
  joined_at?: string | null;
  profile?: ProfileSummary | null;
}

export interface ChatRoom {
  id: string;
  room_type: string;
  trip_post_id?: string | null;
  title?: string | null;
  created_at: string;
  members: RoomMember[];
}

export interface Message {
  id: string;
  room_id: string;
  sender_id?: string | null;
  content: string;
  is_deleted: boolean;
  created_at: string;
}

export interface TripFilters {
  country?: string;
  city?: string;
  budget_type?: BudgetType | "";
  tags?: string;
  start_date?: string;
  page?: number;
  limit?: number;
}

// --- notifications ---------------------------------------------------------

export type NotificationType =
  | "APPLICATION_RECEIVED"
  | "APPLICATION_ACCEPTED"
  | "APPLICATION_REJECTED"
  | "NEW_MESSAGE"
  | "REVIEW_RECEIVED";

export interface Notification {
  id: string;
  type: NotificationType;
  /**
   * Language-neutral key the server stores instead of a rendered sentence.
   * Resolved through the dictionary so one row reads correctly in either
   * language — the row outlives the locale it was written in.
   */
  code: string;
  /** Values interpolated into the template. Ids and scalars only. */
  params?: Record<string, string | number> | null;
  /** Preview of the triggering content — already user text, shown verbatim. */
  body?: string | null;
  read_at?: string | null;
  created_at: string;
  actor?: ProfileSummary | null;
  trip_post_id?: string | null;
  chat_room_id?: string | null;
}

export interface NotificationPage {
  items: Notification[];
  total: number;
  unread: number;
  page: number;
  page_size: number;
}

// --- moderation / admin ----------------------------------------------------

export type ReportStatus = "open" | "reviewing" | "actioned" | "dismissed";

export interface Report {
  id: string;
  reporter_profile_id: string;
  reported_profile_id: string;
  reason: string;
  detail?: string | null;
  status: ReportStatus;
  created_at: string;
}

// --- audit trail -----------------------------------------------------------

export type AuditAction =
  | "USER_BLOCKED"
  | "USER_UNBLOCKED"
  | "REPORT_SUBMITTED"
  | "REPORT_STATUS_CHANGED"
  | "ACCOUNT_DELETED"
  | "ACCOUNT_ANONYMIZED"
  | "ADMIN_QUEUE_VIEWED";

export interface AuditLogEntry {
  id: string;
  action: AuditAction;
  actor_profile_id?: string | null;
  /** Resolved server-side; null once a hard-deleted actor's row is orphaned. */
  actor_nickname?: string | null;
  target_type?: string | null;
  target_id?: string | null;
  /** Ids, enums and counts only — the API never returns free text here. */
  detail?: Record<string, unknown> | null;
  request_id?: string | null;
  created_at: string;
}

export interface PaginatedAuditLogs {
  items: AuditLogEntry[];
  total: number;
  page: number;
  limit: number;
}
