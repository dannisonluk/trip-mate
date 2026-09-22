"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, MessageSquarePlus, Send, ShieldAlert, Users, Wifi, WifiOff } from "lucide-react";

import { API_PREFIX, WS_URL, api, errorMessage, getAccessToken } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { MessageKey } from "@/lib/i18n/dictionaries";
import type { ChatRoom, Message, RoomMember } from "@/lib/types";
import DisclaimerBanner from "@/components/DisclaimerBanner";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/** A message arriving over the wire may omit server-only fields. */
type WireMessage = Partial<Message> & { type?: string };

function otherMember(room: ChatRoom, myProfileId?: string): RoomMember | null {
  return room.members.find((m) => m.profile_id !== myProfileId) ?? room.members[0] ?? null;
}

function roomLabel(
  room: ChatRoom,
  myProfileId: string | undefined,
  t: (key: MessageKey) => string,
): string {
  if (room.title) return room.title;
  if (room.room_type === "DIRECT") {
    return otherMember(room, myProfileId)?.profile?.nickname ?? t("chat.direct");
  }
  return t("chat.group");
}

function ChatInner() {
  const { profile } = useAuth();
  const { t, formatDateTime } = useI18n();
  const searchParams = useSearchParams();
  const initialRoom = searchParams.get("room");

  const [rooms, setRooms] = useState<ChatRoom[]>([]);
  const [roomsLoading, setRoomsLoading] = useState(true);
  const [activeRoomId, setActiveRoomId] = useState<string | null>(initialRoom);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<"idle" | "connecting" | "open" | "closed">("idle");
  const [online, setOnline] = useState<string[]>([]);
  const [peerTyping, setPeerTyping] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks whether the current socket ever reached OPEN. A handshake rejected by
  // the server (bad token / non-member) surfaces in the browser as a close with
  // code 1006 — never 1008 — so "closed without ever opening" is the reliable
  // signal that the connection was refused rather than dropped mid-session.
  const openedRef = useRef(false);

  const myProfileId = profile?.id;

  // `t` changes with the locale. Listing it on the socket effect below would
  // tear the connection down and rebuild it on a language switch, so the
  // handlers read it through a ref instead: always current, never a re-subscribe.
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const loadRooms = useCallback(async () => {
    try {
      const list = await api.listRooms();
      setRooms(list);
      setActiveRoomId((current) => current ?? list[0]?.id ?? null);
    } catch (err) {
      setNotice(errorMessage(err, t("chat.loadRoomsError")));
    } finally {
      setRoomsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadRooms();
  }, [loadRooms]);

  // Load history, then open the socket whenever the active room changes.
  useEffect(() => {
    if (!activeRoomId) return;

    let cancelled = false;
    setMessages([]);
    setOnline([]);
    setPeerTyping(false);

    api
      .listMessages(activeRoomId)
      .then((res) => {
        if (!cancelled) setMessages(res.items);
      })
      .catch((err) => {
        if (!cancelled) setNotice(errorMessage(err, tRef.current("chat.loadMessagesError")));
      });

    const token = getAccessToken();
    if (!token) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    openedRef.current = false;
    const ws = new WebSocket(
      `${WS_URL}${API_PREFIX}/ws/chat/${activeRoomId}?token=${encodeURIComponent(token)}`,
    );
    socketRef.current = ws;

    ws.onopen = () => {
      openedRef.current = true;
      setStatus("open");
    };
    ws.onclose = (event) => {
      setStatus("closed");
      if (!openedRef.current) {
        // Rejected during the handshake (server closes before accept → the
        // browser reports 1006/1008 depending on transport).
        setNotice(
          event.code === 1008
            ? tRef.current("chat.wsForbidden")
            : tRef.current("chat.wsFailed"),
        );
      }
    };
    ws.onerror = () => setStatus("closed");

    ws.onmessage = (event) => {
      let data: WireMessage;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      switch (data.type) {
        case "message": {
          const incoming = data as Message;
          setMessages((prev) =>
            prev.some((m) => m.id === incoming.id) ? prev : [...prev, incoming],
          );
          break;
        }
        case "presence":
          setOnline((data as unknown as { online?: string[] }).online ?? []);
          break;
        case "typing":
          setPeerTyping(Boolean((data as unknown as { is_typing?: boolean }).is_typing));
          break;
        case "safety_hint":
          setNotice((data as unknown as { detail?: string }).detail ?? null);
          break;
        case "error": {
          const detail = (data as unknown as { detail?: string; code?: string }).detail;
          const code = (data as unknown as { code?: string }).code ?? "";
          setNotice(detail ?? tRef.current("chat.sendFailed", { code }));
          break;
        }
        default:
          break;
      }
    };

    // Heartbeat keeps intermediaries from dropping an idle connection.
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, 25000);

    return () => {
      cancelled = true;
      clearInterval(ping);
      ws.close();
      socketRef.current = null;
    };
  }, [activeRoomId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Auto-clear the "peer is typing" hint shortly after the last event.
  useEffect(() => {
    if (!peerTyping) return;
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => setPeerTyping(false), 3000);
    return () => {
      if (typingTimer.current) clearTimeout(typingTimer.current);
    };
  }, [peerTyping]);

  const activeRoom = useMemo(
    () => rooms.find((r) => r.id === activeRoomId) ?? null,
    [rooms, activeRoomId],
  );

  const peer = activeRoom ? otherMember(activeRoom, myProfileId) : null;
  const peerOnline = Boolean(peer && online.includes(peer.profile_id));

  function send() {
    const content = draft.trim();
    const ws = socketRef.current;
    if (!content || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "message", content }));
    setDraft("");
  }

  function notifyTyping() {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "typing", is_typing: true }));
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 py-6">
      <DisclaimerBanner compact />

      {notice && (
        <button
          type="button"
          onClick={() => setNotice(null)}
          className="flex w-full items-center gap-2 rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-left text-sm text-foreground"
        >
          <ShieldAlert className="h-4 w-4 shrink-0 text-accent" />
          <span className="flex-1">{notice}</span>
          <span className="text-xs text-muted-foreground">{t("common.close")}</span>
        </button>
      )}

      <div className="grid gap-4 md:grid-cols-[280px_1fr]">
        {/* --- room list --- */}
        <Card className="flex max-h-[70vh] flex-col overflow-hidden p-0">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h2 className="text-sm font-semibold">{t("chat.rooms")}</h2>
            <Badge variant="secondary">{rooms.length}</Badge>
          </div>

          <div className="scrollbar-thin flex-1 overflow-y-auto">
            {roomsLoading ? (
              <div className="flex justify-center py-10 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            ) : rooms.length === 0 ? (
              <p className="px-4 py-10 text-center text-xs text-muted-foreground">
                {t("chat.noRooms")}
                <br />
                {t("chat.noRoomsHint")}
              </p>
            ) : (
              rooms.map((room) => {
                const member = otherMember(room, myProfileId);
                const isDirect = room.room_type === "DIRECT";
                const label = roomLabel(room, myProfileId, t);
                const active = room.id === activeRoomId;

                return (
                  <button
                    key={room.id}
                    type="button"
                    onClick={() => setActiveRoomId(room.id)}
                    className={cn(
                      "flex w-full items-center gap-3 border-b px-3 py-3 text-left transition-colors last:border-b-0",
                      active ? "bg-secondary" : "hover:bg-secondary/60",
                    )}
                  >
                    {isDirect ? (
                      <Avatar className="h-9 w-9">
                        {member?.profile?.avatar_url && (
                          <AvatarImage src={member.profile.avatar_url} alt="" />
                        )}
                        <AvatarFallback className="text-xs">
                          {label.slice(0, 1)}
                        </AvatarFallback>
                      </Avatar>
                    ) : (
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                        <Users className="h-4 w-4" />
                      </span>
                    )}

                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{label}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {isDirect
                          ? t("chat.direct")
                          : t("chat.memberCount", { count: room.members.length })}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </Card>

        {/* --- message panel --- */}
        <Card className="flex max-h-[70vh] min-h-[420px] flex-col overflow-hidden p-0">
          {!activeRoom ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
              <MessageSquarePlus className="h-8 w-8" />
              <p className="text-sm">{t("chat.selectRoom")}</p>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 border-b px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold">
                    {roomLabel(activeRoom, myProfileId, t)}
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    {activeRoom.room_type === "DIRECT" ? (
                      <>
                        <span
                          className={cn(
                            "inline-block h-1.5 w-1.5 rounded-full",
                            peerOnline ? "bg-emerald-500" : "bg-muted-foreground/40",
                          )}
                        />
                        {peerOnline ? t("chat.online") : t("chat.offline")}
                      </>
                    ) : (
                      <span>{t("chat.memberCount", { count: activeRoom.members.length })}</span>
                    )}
                  </div>
                </div>

                <Badge variant={status === "open" ? "default" : "secondary"}>
                  {status === "open" ? (
                    <Wifi className="h-3 w-3" />
                  ) : (
                    <WifiOff className="h-3 w-3" />
                  )}
                  {status === "open"
                    ? t("chat.statusOpen")
                    : status === "connecting"
                      ? t("chat.statusConnecting")
                      : t("chat.statusClosed")}
                </Badge>
              </div>

              <div ref={logRef} className="scrollbar-thin flex-1 space-y-3 overflow-y-auto px-4 py-4">
                {messages.length === 0 ? (
                  <p className="py-16 text-center text-sm text-muted-foreground">
                    {t("chat.noMessages")}
                  </p>
                ) : (
                  messages.map((m) => {
                    const mine = m.sender_id === myProfileId;
                    const sender = activeRoom.members.find(
                      (member) => member.profile_id === m.sender_id,
                    );
                    return (
                      <div
                        key={m.id}
                        className={cn("flex gap-2", mine ? "justify-end" : "justify-start")}
                      >
                        {!mine && (
                          <Avatar className="mt-0.5 h-7 w-7 shrink-0">
                            {sender?.profile?.avatar_url && (
                              <AvatarImage src={sender.profile.avatar_url} alt="" />
                            )}
                            <AvatarFallback className="text-[10px]">
                              {(sender?.profile?.nickname ?? "?").slice(0, 1)}
                            </AvatarFallback>
                          </Avatar>
                        )}

                        <div
                          className={cn(
                            "max-w-[75%] rounded-2xl px-3.5 py-2 text-sm",
                            mine
                              ? "rounded-br-sm bg-primary text-primary-foreground"
                              : "rounded-bl-sm bg-secondary text-secondary-foreground",
                          )}
                        >
                          {m.is_deleted ? (
                            <em className="opacity-70">{t("chat.messageDeleted")}</em>
                          ) : (
                            <p className="whitespace-pre-wrap break-words">{m.content}</p>
                          )}
                          <span
                            className={cn(
                              "mt-1 block text-right text-[10px]",
                              mine ? "text-primary-foreground/70" : "text-muted-foreground",
                            )}
                          >
                            {formatDateTime(m.created_at)}
                          </span>
                        </div>
                      </div>
                    );
                  })
                )}

                {peerTyping && (
                  <p className="text-xs text-muted-foreground">{t("chat.peerTyping")}</p>
                )}
              </div>

              <div className="flex items-center gap-2 border-t px-3 py-3">
                <Input
                  value={draft}
                  onChange={(e) => {
                    setDraft(e.target.value);
                    notifyTyping();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  placeholder={t("chat.composerPlaceholder")}
                  maxLength={4000}
                  className="flex-1"
                />
                <Button onClick={send} disabled={status !== "open" || !draft.trim()}>
                  <Send className="h-4 w-4" />
                  {t("chat.send")}
                </Button>
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function ChatPage() {
  return (
    <RequireAuth>
      <Suspense
        fallback={
          <div className="flex justify-center py-24 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        }
      >
        <ChatInner />
      </Suspense>
    </RequireAuth>
  );
}
