"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck, Inbox } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import type { Notification } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** How often to re-poll the badge. Polling (rather than a socket) because the
 *  count is tiny, changes rarely, and this keeps the notification channel
 *  working even if the chat WebSocket is disconnected. */
const POLL_MS = 30_000;
const PREVIEW_COUNT = 8;

function hrefFor(n: Notification): string {
  if (n.chat_room_id) return `/chat?room=${n.chat_room_id}`;
  if (n.trip_post_id) return `/trips/${n.trip_post_id}`;
  return "/notifications";
}

function typeAccent(type: string): string {
  switch (type) {
    case "APPLICATION_ACCEPTED":
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
    case "APPLICATION_REJECTED":
      return "bg-muted text-muted-foreground";
    case "NEW_MESSAGE":
      return "bg-sky-500/15 text-sky-600 dark:text-sky-400";
    case "REVIEW_RECEIVED":
      return "bg-amber-500/15 text-amber-600 dark:text-amber-400";
    default:
      return "bg-primary/15 text-primary";
  }
}

export default function NotificationBell() {
  const router = useRouter();
  const { t, serverText, formatDateTime } = useI18n();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // Avoids setting state after unmount (React 18 no longer warns, but a stray
  // poll resolving late would still clobber a newer response).
  const aliveRef = useRef(true);

  const loadCount = useCallback(async () => {
    try {
      const { unread: n } = await api.unreadCount();
      if (aliveRef.current) setUnread(n);
    } catch {
      /* transient — the next poll retries */
    }
  }, []);

  const loadPreview = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = await api.listNotifications({ page_size: PREVIEW_COUNT });
      if (!aliveRef.current) return;
      setItems(page.items);
      setUnread(page.unread);
    } catch (err) {
      if (aliveRef.current) setError(errorMessage(err, t("notifications.loadError")));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    aliveRef.current = true;
    loadCount();
    const timer = setInterval(loadCount, POLL_MS);
    // Re-sync when the user comes back to the tab — otherwise a notification
    // that arrived while the tab was hidden waits up to POLL_MS to appear.
    const onFocus = () => loadCount();
    window.addEventListener("focus", onFocus);
    return () => {
      aliveRef.current = false;
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [loadCount]);

  useEffect(() => {
    if (open) loadPreview();
  }, [open, loadPreview]);

  async function openNotification(n: Notification) {
    // Optimistic: navigate immediately, mark read in the background. If the
    // mark-read fails the badge self-corrects on the next poll.
    setItems((prev) =>
      prev.map((x) => (x.id === n.id ? { ...x, read_at: x.read_at ?? new Date().toISOString() } : x)),
    );
    if (!n.read_at) setUnread((u) => Math.max(0, u - 1));

    setOpen(false);
    router.push(hrefFor(n));

    if (!n.read_at) {
      try {
        await api.markNotificationRead(n.id);
      } catch {
        loadCount();
      }
    }
  }

  async function markAllRead() {
    const previous = items;
    setItems((prev) => prev.map((x) => ({ ...x, read_at: x.read_at ?? new Date().toISOString() })));
    setUnread(0);
    try {
      await api.markAllNotificationsRead();
    } catch (err) {
      setItems(previous);
      setError(errorMessage(err, t("notifications.markReadError")));
      loadCount();
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="relative gap-1.5"
          aria-label={
            unread > 0
              ? t("notifications.unreadAria", { count: unread })
              : t("notifications.title")
          }
        >
          <Bell className="h-4 w-4" />
          <span className="hidden sm:inline">{t("notifications.title")}</span>
          {unread > 0 && (
            <span
              className={cn(
                "absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full",
                "bg-destructive px-1 text-[10px] font-bold leading-none text-destructive-foreground",
              )}
            >
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-[22rem] p-0">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <span className="text-sm font-semibold">{t("notifications.title")}</span>
          {unread > 0 && (
            <button
              onClick={markAllRead}
              className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <CheckCheck className="h-3.5 w-3.5" />
              {t("notifications.markAllRead")}
            </button>
          )}
        </div>

        <div className="max-h-96 overflow-y-auto scrollbar-thin">
          {loading && items.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {t("common.loading")}
            </p>
          ) : error ? (
            <p className="px-4 py-8 text-center text-sm text-destructive">{error}</p>
          ) : items.length === 0 ? (
            <div className="px-4 py-10 text-center">
              <Inbox className="mx-auto h-8 w-8 text-muted-foreground/50" />
              <p className="mt-3 text-sm text-muted-foreground">{t("notifications.empty")}</p>
            </div>
          ) : (
            <ul className="divide-y">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => openNotification(n)}
                    className={cn(
                      "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/60",
                      !n.read_at && "bg-primary/[0.04]",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10px] font-bold",
                        typeAccent(n.type),
                      )}
                      aria-hidden
                    >
                      {n.actor?.nickname?.slice(0, 1) ?? "•"}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-start gap-2">
                        <span
                          className={cn(
                            "line-clamp-2 text-sm",
                            !n.read_at ? "font-semibold" : "font-medium text-muted-foreground",
                          )}
                        >
                          {serverText("notif", n.code, n.params)}
                        </span>
                        {!n.read_at && (
                          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                        )}
                      </span>
                      {n.body && (
                        <span className="mt-0.5 line-clamp-1 block text-xs text-muted-foreground">
                          {n.body}
                        </span>
                      )}
                      <span className="mt-1 block text-[11px] text-muted-foreground/80">
                        {formatDateTime(n.created_at)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="border-t px-4 py-2">
          <Link
            href="/notifications"
            onClick={() => setOpen(false)}
            className="block text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("notifications.viewAll")}
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
