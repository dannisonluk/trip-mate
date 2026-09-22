"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileText,
  History,
  Loader2,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  UserCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { api, ApiError, errorMessage } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import type { AuditAction, AuditLogEntry, Report, ReportStatus } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n/dictionaries";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// Labels live in the dictionary; these tables map a value to its key so a
// wrong key is a compile error rather than a string that silently goes missing.
const REPORT_FILTERS: { value: ReportStatus | ""; key: MessageKey }[] = [
  { value: "", key: "common.all" },
  { value: "open", key: "admin.statusOpen" },
  { value: "reviewing", key: "admin.statusReviewing" },
  { value: "actioned", key: "admin.statusActioned" },
  { value: "dismissed", key: "admin.statusDismissed" },
];

const REASON_KEYS: Record<string, MessageKey> = {
  harassment: "admin.reasonHarassment",
  spam: "admin.reasonSpam",
  scam: "admin.reasonScam",
  inappropriate: "admin.reasonInappropriate",
  other: "admin.reasonOther",
};

const STATUS_META: Record<ReportStatus, { key: MessageKey; className: string }> = {
  open: { key: "admin.statusOpen", className: "bg-destructive/10 text-destructive border-destructive/30" },
  reviewing: { key: "admin.statusReviewing", className: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  actioned: {
    key: "admin.statusActioned",
    className: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  },
  dismissed: { key: "admin.statusDismissed", className: "bg-muted text-muted-foreground" },
};

// --- audit trail -----------------------------------------------------------

const AUDIT_ACTIONS: AuditAction[] = [
  "USER_BLOCKED",
  "USER_UNBLOCKED",
  "REPORT_SUBMITTED",
  "REPORT_STATUS_CHANGED",
  "ACCOUNT_DELETED",
  "ACCOUNT_ANONYMIZED",
  "ADMIN_QUEUE_VIEWED",
];

const ACTION_META: Record<
  AuditAction,
  { key: MessageKey; className: string; icon: LucideIcon }
> = {
  USER_BLOCKED: {
    key: "admin.auditBlocked",
    className: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
    icon: Ban,
  },
  USER_UNBLOCKED: {
    key: "admin.auditUnblocked",
    className: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
    icon: UserCheck,
  },
  REPORT_SUBMITTED: {
    key: "admin.auditReportSubmitted",
    className: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    icon: AlertTriangle,
  },
  REPORT_STATUS_CHANGED: {
    key: "admin.auditReportStatusChanged",
    className: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
    icon: FileText,
  },
  ACCOUNT_DELETED: {
    key: "admin.auditAccountDeleted",
    className: "bg-destructive/10 text-destructive border-destructive/30",
    icon: Trash2,
  },
  ACCOUNT_ANONYMIZED: {
    key: "admin.auditAccountAnonymized",
    className: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
    icon: ShieldCheck,
  },
  ADMIN_QUEUE_VIEWED: {
    key: "admin.auditQueueViewed",
    className: "bg-muted text-muted-foreground",
    icon: History,
  },
};

const AUDIT_PAGE_SIZE = 25;

/** `detail` is ids/enums/counts only, so a flat `k: v` chip row is enough. */
function DetailChips({ detail }: { detail: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(detail ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="rounded-md border bg-muted/40 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
        >
          {key}={value === null ? "null" : String(value)}
        </span>
      ))}
    </div>
  );
}

function AuditTrail({ onForbidden }: { onForbidden: () => void }) {
  const { t, formatDateTime } = useI18n();
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [action, setAction] = useState<AuditAction | "">("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listAuditLogs({ action, page, limit: AUDIT_PAGE_SIZE });
      setEntries(res.items);
      setTotal(res.total);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) onForbidden();
      else setError(errorMessage(err, t("admin.loadAuditError")));
    } finally {
      setLoading(false);
    }
  }, [action, page, onForbidden, t]);

  useEffect(() => {
    load();
  }, [load]);

  // Changing the filter must not leave the viewer stranded on a page that no
  // longer exists under the new result count.
  function changeAction(next: AuditAction | "") {
    setAction(next);
    setPage(1);
  }

  const lastPage = Math.max(1, Math.ceil(total / AUDIT_PAGE_SIZE));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {loading ? t("common.loading") : t("admin.auditTotal", { total })}
        </p>
        <Button variant="outline" size="sm" className="gap-1.5" onClick={load} disabled={loading}>
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          {t("common.reload")}
        </Button>
      </div>

      <div className="mb-5 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={action === "" ? "secondary" : "outline"}
          onClick={() => changeAction("")}
        >
          {t("common.all")}
        </Button>
        {AUDIT_ACTIONS.map((a) => (
          <Button
            key={a}
            size="sm"
            variant={action === a ? "secondary" : "outline"}
            onClick={() => changeAction(a)}
          >
            {t(ACTION_META[a].key)}
          </Button>
        ))}
      </div>

      {error && (
        <Card className="mb-4 border-destructive/40">
          <CardContent className="py-3 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {loading ? (
        <div className="grid place-items-center py-24">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : entries.length === 0 ? (
        <Card>
          <CardContent className="grid place-items-center py-20 text-center">
            <History className="h-10 w-10 text-muted-foreground/40" />
            <p className="mt-4 text-sm text-muted-foreground">{t("admin.noAudit")}</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <ul className="space-y-2">
            {entries.map((entry) => {
              const meta = ACTION_META[entry.action] ?? {
                key: "common.none" as MessageKey,
                className: "bg-muted text-muted-foreground",
                icon: History,
              };
              const Icon = meta.icon;
              return (
                <li key={entry.id}>
                  <Card>
                    <CardContent className="py-3.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 rounded-full border border-transparent px-2 py-0.5 text-[11px] font-medium",
                            meta.className,
                          )}
                        >
                          <Icon className="h-3 w-3" />
                          {meta.key === "common.none" ? entry.action : t(meta.key)}
                        </span>
                        <span className="text-[11px] text-muted-foreground">
                          {formatDateTime(entry.created_at)}
                        </span>
                      </div>

                      <dl className="mt-2 grid gap-1.5 text-xs sm:grid-cols-2">
                        <div className="flex gap-1.5">
                          <dt className="text-muted-foreground">{t("admin.actor")}</dt>
                          <dd>
                            {entry.actor_profile_id ? (
                              <Link
                                href={`/profile/${entry.actor_profile_id}`}
                                className="underline-offset-2 hover:underline"
                              >
                                {entry.actor_nickname ?? `${entry.actor_profile_id.slice(0, 8)}…`}
                              </Link>
                            ) : (
                              // A null actor is meaningful, not missing data: it is
                              // what a hard-deleted account leaves behind.
                              <span className="text-muted-foreground">{t("admin.accountDeleted")}</span>
                            )}
                          </dd>
                        </div>
                        {entry.target_id && (
                          <div className="flex gap-1.5">
                            <dt className="text-muted-foreground">
                              {entry.target_type === "profile" ? t("admin.targetProfile") : t("admin.targetOther")}
                            </dt>
                            <dd>
                              {entry.target_type === "profile" ? (
                                <Link
                                  href={`/profile/${entry.target_id}`}
                                  className="font-mono underline-offset-2 hover:underline"
                                >
                                  {entry.target_id.slice(0, 8)}…
                                </Link>
                              ) : (
                                <span className="font-mono">{entry.target_id.slice(0, 8)}…</span>
                              )}
                            </dd>
                          </div>
                        )}
                      </dl>

                      <DetailChips detail={entry.detail} />

                      {entry.request_id && (
                        <p className="mt-2 font-mono text-[10px] text-muted-foreground/70">
                          request_id={entry.request_id}
                        </p>
                      )}
                    </CardContent>
                  </Card>
                </li>
              );
            })}
          </ul>

          <div className="mt-5 flex items-center justify-between">
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
              {t("trips.prevPage")}
            </Button>
            <span className="text-xs text-muted-foreground">
              {t("admin.pageIndicator", { page, lastPage })}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              disabled={page >= lastPage || loading}
              onClick={() => setPage((p) => p + 1)}
            >
              {t("trips.nextPage")}
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// --- report queue ----------------------------------------------------------

function ReportQueue({ onForbidden }: { onForbidden: () => void }) {
  const { t, formatDateTime } = useI18n();
  const [reports, setReports] = useState<Report[]>([]);
  const [filter, setFilter] = useState<ReportStatus | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setReports(await api.listReports(filter));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) onForbidden();
      else setError(errorMessage(err, t("admin.loadReportsError")));
    } finally {
      setLoading(false);
    }
  }, [filter, onForbidden, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function setStatus(report: Report, status: ReportStatus) {
    setBusyId(report.id);
    try {
      const updated = await api.updateReportStatus(report.id, status);
      setReports((prev) =>
        filter === ""
          ? prev.map((r) => (r.id === updated.id ? updated : r))
          : prev.filter((r) => r.id !== updated.id),
      );
    } catch (err) {
      setError(errorMessage(err, t("admin.updateStatusError")));
    } finally {
      setBusyId(null);
    }
  }

  const openCount = reports.filter((r) => r.status === "open").length;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {loading ? t("common.loading") : t("admin.showing", { count: reports.length })}
          {openCount > 0 && t("admin.showingOpen", { count: openCount })}
        </p>
        <Button variant="outline" size="sm" className="gap-1.5" onClick={load} disabled={loading}>
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          {t("common.reload")}
        </Button>
      </div>

      <div className="mb-5 flex flex-wrap gap-2">
        {REPORT_FILTERS.map((f) => (
          <Button
            key={f.value}
            size="sm"
            variant={filter === f.value ? "secondary" : "outline"}
            onClick={() => setFilter(f.value)}
          >
            {t(f.key)}
          </Button>
        ))}
      </div>

      {error && (
        <Card className="mb-4 border-destructive/40">
          <CardContent className="py-3 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {loading ? (
        <div className="grid place-items-center py-24">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : reports.length === 0 ? (
        <Card>
          <CardContent className="grid place-items-center py-20 text-center">
            <CheckCircle2 className="h-10 w-10 text-emerald-500/50" />
            <p className="mt-4 text-sm text-muted-foreground">{t("admin.noReports")}</p>
          </CardContent>
        </Card>
      ) : (
        <ul className="space-y-3">
          {reports.map((r) => {
            const meta = STATUS_META[r.status] ?? STATUS_META.open;
            return (
              <li key={r.id}>
                <Card>
                  <CardContent className="py-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="destructive" className="gap-1">
                        <AlertTriangle className="h-3 w-3" />
                        {REASON_KEYS[r.reason] ? t(REASON_KEYS[r.reason]) : r.reason}
                      </Badge>
                      <span
                        className={cn(
                          "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                          meta.className,
                        )}
                      >
                        {t(meta.key)}
                      </span>
                      <span className="text-[11px] text-muted-foreground">
                        {formatDateTime(r.created_at)}
                      </span>
                    </div>

                    <dl className="mt-3 grid gap-1.5 text-xs sm:grid-cols-2">
                      <div className="flex gap-1.5">
                        <dt className="text-muted-foreground">{t("admin.reporter")}</dt>
                        <dd>
                          <Link
                            href={`/profile/${r.reporter_profile_id}`}
                            className="font-mono underline-offset-2 hover:underline"
                          >
                            {r.reporter_profile_id.slice(0, 8)}…
                          </Link>
                        </dd>
                      </div>
                      <div className="flex gap-1.5">
                        <dt className="text-muted-foreground">{t("admin.reported")}</dt>
                        <dd>
                          <Link
                            href={`/profile/${r.reported_profile_id}`}
                            className="font-mono font-semibold underline-offset-2 hover:underline"
                          >
                            {r.reported_profile_id.slice(0, 8)}…
                          </Link>
                        </dd>
                      </div>
                    </dl>

                    {r.detail && (
                      <p className="mt-3 whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
                        {r.detail}
                      </p>
                    )}

                    <div className="mt-4 flex flex-wrap gap-2">
                      {r.status !== "reviewing" && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busyId === r.id}
                          onClick={() => setStatus(r, "reviewing")}
                        >
                          {t("admin.markReviewing")}
                        </Button>
                      )}
                      {r.status !== "actioned" && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="gap-1.5 text-emerald-600 dark:text-emerald-400"
                          disabled={busyId === r.id}
                          onClick={() => setStatus(r, "actioned")}
                        >
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          {t("admin.markActioned")}
                        </Button>
                      )}
                      {r.status !== "dismissed" && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="gap-1.5 text-muted-foreground"
                          disabled={busyId === r.id}
                          onClick={() => setStatus(r, "dismissed")}
                        >
                          <XCircle className="h-3.5 w-3.5" />
                          {t("admin.dismiss")}
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// --- page shell ------------------------------------------------------------

function Forbidden() {
  const { t } = useI18n();

  return (
    <div className="mx-auto max-w-md py-24 text-center">
      <ShieldAlert className="mx-auto h-10 w-10 text-muted-foreground/50" />
      <h2 className="mt-4 text-xl font-semibold">{t("admin.forbiddenTitle")}</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {t("admin.forbiddenBody")}
      </p>
      <Button asChild variant="outline" className="mt-6">
        <Link href="/trips">{t("admin.backToTrips")}</Link>
      </Button>
    </div>
  );
}

function AdminContent() {
  const { t } = useI18n();
  const { user, loading: authLoading } = useAuth();
  const [forbidden, setForbidden] = useState(false);

  // The cached `role` is enough for the common case, but it can be stale (a
  // demoted admin, an expired token). Each tab reports its own 403 upward rather
  // than the page probing with a throwaway request: `/admin/reports` is itself
  // audited, so a probe would write a spurious ADMIN_QUEUE_VIEWED on every load.
  const markForbidden = useCallback(() => setForbidden(true), []);

  if (!authLoading && user && user.role !== "ADMIN") return <Forbidden />;
  if (forbidden) return <Forbidden />;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <ShieldAlert className="h-5 w-5" />
          {t("admin.title")}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("admin.subtitle")}
        </p>
      </div>

      <Tabs defaultValue="reports">
        <TabsList>
          <TabsTrigger value="reports" className="gap-1.5">
            <AlertTriangle className="h-4 w-4" />
            {t("admin.tabReports")}
          </TabsTrigger>
          <TabsTrigger value="audit" className="gap-1.5">
            <History className="h-4 w-4" />
            {t("admin.tabAudit")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="reports">
          <ReportQueue onForbidden={markForbidden} />
        </TabsContent>
        <TabsContent value="audit">
          <AuditTrail onForbidden={markForbidden} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function AdminPage() {
  return (
    <RequireAuth>
      <AdminContent />
    </RequireAuth>
  );
}
