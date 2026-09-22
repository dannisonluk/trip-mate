"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck, Inbox, Loader2, Trash2 } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth } from "@/lib/auth";
import type { Notification, NotificationPage } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const PAGE_SIZE = 20;

function hrefFor(n: Notification): string {
  if (n.chat_room_id) return `/chat?room=${n.chat_room_id}`;
  if (n.trip_post_id) return `/trips/${n.trip_post_id}`;
  return "/profile";
}

function NotificationsContent() {
  const router = useRouter();
  const { t, label, formatDateTime } = useI18n();
  const [data, setData] = useState<NotificationPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listNotifications({
        page,
        page_size: PAGE_SIZE,
        unread_only: unreadOnly,
      });
      setData(res);
    } catch (err) {
      setError(errorMessage(err, t("notifications.loadError")));
    } finally {
      setLoading(false);
    }
  }, [page, unreadOnly, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function openItem(n: Notification) {
    setBusyId(n.id);
    try {
      if (!n.read_at) await api.markNotificationRead(n.id);
      router.push(hrefFor(n));
    } catch (err) {
      setError(errorMessage(err, t("notificationsPage.openError")));
      setBusyId(null);
    }
  }

  async function removeItem(id: string) {
    setBusyId(id);
    try {
      await api.deleteNotification(id);
      await load();
    } catch (err) {
      setError(errorMessage(err, t("notificationsPage.deleteError")));
    } finally {
      setBusyId(null);
    }
  }

  async function markAll() {
    try {
      await api.markAllNotificationsRead();
      await load();
    } catch (err) {
      setError(errorMessage(err, t("notifications.markReadError")));
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Bell className="h-5 w-5" />
            {t("notifications.title")}
          </h1>
          {data && (
            <p className="mt-1 text-sm text-muted-foreground">
              {data.unread > 0
                ? t("notificationsPage.summaryUnread", { total: data.total, unread: data.unread })
                : t("notificationsPage.summary", { total: data.total })}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={unreadOnly ? "secondary" : "outline"}
            size="sm"
            onClick={() => {
              setUnreadOnly((v) => !v);
              setPage(1);
            }}
          >
            {t("notificationsPage.unreadOnly")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={markAll}
            disabled={!data || data.unread === 0}
          >
            <CheckCheck className="h-4 w-4" />
            {t("notifications.markAllRead")}
          </Button>
        </div>
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
      ) : !data || data.items.length === 0 ? (
        <Card>
          <CardContent className="grid place-items-center py-20 text-center">
            <Inbox className="h-10 w-10 text-muted-foreground/40" />
            <p className="mt-4 text-sm text-muted-foreground">
              {unreadOnly ? t("notificationsPage.emptyUnread") : t("notificationsPage.empty")}
            </p>
            <Button asChild variant="outline" size="sm" className="mt-5">
              <Link href="/trips">{t("notificationsPage.goExplore")}</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <ul className="space-y-2">
          {data.items.map((n) => (
            <li key={n.id}>
              <Card
                className={cn(
                  "transition-colors",
                  !n.read_at && "border-primary/30 bg-primary/[0.03]",
                )}
              >
                <CardContent className="flex items-start gap-3 py-4">
                  <button
                    onClick={() => openItem(n)}
                    disabled={busyId === n.id}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={n.read_at ? "outline" : "default"}>
                        {label("notificationType", n.type)}
                      </Badge>
                      {!n.read_at && (
                        <span className="text-[11px] font-semibold text-primary">
                          {t("notificationsPage.unreadBadge")}
                        </span>
                      )}
                      <span className="text-[11px] text-muted-foreground">
                        {formatDateTime(n.created_at)}
                      </span>
                    </div>
                    <p
                      className={cn(
                        "mt-2 text-sm",
                        !n.read_at ? "font-semibold" : "font-medium text-muted-foreground",
                      )}
                    >
                      {n.title}
                    </p>
                    {n.body && (
                      <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{n.body}</p>
                    )}
                  </button>

                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("notificationsPage.deleteAria")}
                    disabled={busyId === n.id}
                    onClick={() => removeItem(n.id)}
                    className="shrink-0 text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {data && totalPages > 1 && (
        <div className="mt-6 flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            {t("trips.prevPage")}
          </Button>
          <span className="text-sm text-muted-foreground">
            {t("notificationsPage.pageIndicator", { page, totalPages })}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            {t("trips.nextPage")}
          </Button>
        </div>
      )}
    </div>
  );
}

export default function NotificationsPage() {
  return (
    <RequireAuth>
      <NotificationsContent />
    </RequireAuth>
  );
}
