"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, MessageSquarePlus, Send, ShieldAlert, Users, Wifi, WifiOff } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useRoomSocket } from "@/hooks/useRoomSocket";
import { cn } from "@/lib/utils";
import type { MessageKey } from "@/lib/i18n/dictionaries";
import type { ChatRoom, Message, RoomMember } from "@/lib/types";
import DisclaimerBanner from "@/components/DisclaimerBanner";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";


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
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const logRef = useRef<HTMLDivElement | null>(null);

  const myProfileId = profile?.id;

  // `t` changes with the locale. Passing it straight into the socket hook would
  // rebuild the connection on a language switch, so the hook receives a
  // ref-backed reader: always current, never a re-subscribe. See `useRoomSocket`.
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

  const resolve = useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) => tRef.current(key, vars),
    [],
  );
  // The hook owns the message list; this page only renders it. `onHistory` is
  // therefore a no-op — it exists so a future caller can hook the transition
  // without the hook needing another option.
  const handleHistory = useCallback(() => {}, []);
  const handleNotice = useCallback((text: string) => setNotice(text), []);

  const { status, messages, online, peerTyping, send, notifyTyping } = useRoomSocket({
    roomId: activeRoomId,
    resolve,
    onHistory: handleHistory,
    onNotice: handleNotice,
  });

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const activeRoom = useMemo(
    () => rooms.find((r) => r.id === activeRoomId) ?? null,
    [rooms, activeRoomId],
  );

  const peer = activeRoom ? otherMember(activeRoom, myProfileId) : null;
  const peerOnline = Boolean(peer && online.includes(peer.profile_id));

  function handleSend() {
    // The draft is cleared only when the socket actually accepted the message;
    // clearing first would discard the user's text on a mid-send disconnect.
    if (send(draft.trim())) setDraft("");
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
                      handleSend();
                    }
                  }}
                  placeholder={t("chat.composerPlaceholder")}
                  maxLength={4000}
                  className="flex-1"
                />
                <Button onClick={handleSend} disabled={status !== "open" || !draft.trim()}>
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
