"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { API_PREFIX, WS_URL, api, errorMessage, getAccessToken } from "@/lib/api";
import { serverCodeKey } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n/dictionaries";
import type { Message } from "@/lib/types";

/**
 * The chat socket, extracted from `app/chat/page.tsx`.
 *
 * Why this is a hook and not inline
 * ---------------------------------
 * The F2 finding was that a stale socket's handlers could still write state
 * after the effect had torn down. The fix (guard every handler on the socket's
 * own identity) is easy to write once and easy to get subtly wrong on the next
 * edit — and while it lived inside a 500-line page there was nothing to test
 * it against except the whole page.
 *
 * The two non-obvious rules this hook owns, both learned the hard way:
 *
 * 1. **Every handler is guarded on `isCurrent()`.** `ws.close()` is
 *    asynchronous: the old socket's `onclose`/`onmessage` can fire after a new
 *    socket is installed. The socket *identity* is the only correct
 *    discriminator — a closed-over `roomId` is stale by construction, because
 *    the fact that the effect re-ran is what made it stale.
 *
 * 2. **"Did this socket open?" is per-connection, so it is a `WeakSet` of
 *    sockets, never a boolean.** Switching rooms closes the old socket and
 *    opens a new one; the old `onclose` fires after a shared flag was reset, so
 *    it reads the *new* socket's state and misreports a clean room switch as a
 *    refused handshake.
 *
 * The resolver is passed in rather than imported so the hook stays free of
 * `useI18n` — which matters because a `t` in these dependencies would rebuild
 * the connection on every language switch. Callers pass a ref-backed function.
 */

export type SocketStatus = "idle" | "connecting" | "open" | "closed";

/** A message arriving over the wire may omit server-only fields. */
type WireMessage = Partial<Message> & {
  type?: string;
  online?: string[];
  is_typing?: boolean;
  code?: string;
  detail?: unknown;
};

export interface UseRoomSocketOptions {
  /** The room to connect to. `null` disconnects without connecting. */
  roomId: string | null;
  /** Resolver for dictionary keys. Must be stable (a ref-backed reader). */
  resolve: (key: MessageKey, vars?: Record<string, string | number>) => string;
  /** Called with the loaded history when a room is entered. */
  onHistory: (items: Message[]) => void;
  /** Called with a user-facing notice, already resolved to text. */
  onNotice: (text: string) => void;
  /** Called when the room changes, before the new connection is made. */
  onRoomReset?: () => void;
}

export interface RoomSocket {
  status: SocketStatus;
  messages: Message[];
  online: string[];
  peerTyping: boolean;
  /** Append a message locally if it is not already present. */
  addMessage: (message: Message) => void;
  /** Send a chat message. No-op unless the socket is open. */
  send: (content: string) => boolean;
  /** Tell the peer we are typing. No-op unless the socket is open. */
  notifyTyping: () => void;
}

const HEARTBEAT_MS = 25_000;
const TYPING_LINGER_MS = 3_000;

export function useRoomSocket({
  roomId,
  resolve,
  onHistory,
  onNotice,
  onRoomReset,
}: UseRoomSocketOptions): RoomSocket {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<SocketStatus>("idle");
  const [online, setOnline] = useState<string[]>([]);
  const [peerTyping, setPeerTyping] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // See rule 2 in the module docstring: per-connection, not per-component.
  const openedSockets = useRef(new WeakSet<WebSocket>());

  // The callbacks and the resolver are read through refs so that the connection
  // effect below depends on `roomId` *only*. Adding them to the dependency
  // array would tear the socket down and rebuild it whenever a parent render
  // produced a new closure — including on every locale change.
  const resolveRef = useRef(resolve);
  const onHistoryRef = useRef(onHistory);
  const onNoticeRef = useRef(onNotice);
  const onRoomResetRef = useRef(onRoomReset);
  useEffect(() => {
    resolveRef.current = resolve;
    onHistoryRef.current = onHistory;
    onNoticeRef.current = onNotice;
    onRoomResetRef.current = onRoomReset;
  });

  const addMessage = useCallback((message: Message) => {
    setMessages((prev) => (prev.some((m) => m.id === message.id) ? prev : [...prev, message]));
  }, []);

  /**
   * Skip the current room's messages on the next connect.
   *
   * If this is not reset, a reconnect after a transport drop finds the room
   * already marked "skipped" and the user sees an empty thread — the history
   * never loads and nothing reports an error.
   */
  const skipHistoryRef = useRef(false);

  const send = useCallback((content: string) => {
    const ws = socketRef.current;
    if (!content || !ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: "message", content }));
    return true;
  }, []);

  const notifyTyping = useCallback(() => {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "typing", is_typing: true }));
    }
  }, []);

  useEffect(() => {
    if (!roomId) return;

    let cancelled = false;
    setMessages([]);
    setOnline([]);
    setPeerTyping(false);
    onRoomResetRef.current?.();

    if (!skipHistoryRef.current) {
      api
        .listMessages(roomId)
        .then((res) => {
          if (!cancelled) onHistoryRef.current(res.items);
        })
        .catch((err) => {
          if (!cancelled) {
            onNoticeRef.current(errorMessage(err, resolveRef.current("chat.loadMessagesError")));
          }
        });
    }
    // Reset for the next connect; see `skipHistoryRef`.
    skipHistoryRef.current = false;

    const token = getAccessToken();
    if (!token) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    const ws = new WebSocket(
      `${WS_URL}${API_PREFIX}/ws/chat/${roomId}?token=${encodeURIComponent(token)}`,
    );
    socketRef.current = ws;

    const isCurrent = () => socketRef.current === ws && !cancelled;

    ws.onopen = () => {
      if (!isCurrent()) return;
      openedSockets.current.add(ws);
      setStatus("open");
    };

    ws.onclose = (event) => {
      if (!isCurrent()) return;
      setStatus("closed");
      // Asked of *this* socket: a shared flag would already have been reset by
      // the teardown that preceded this event.
      if (!openedSockets.current.has(ws)) {
        onNoticeRef.current(
          event.code === 1008
            ? resolveRef.current("chat.wsForbidden")
            : resolveRef.current("chat.wsFailed"),
        );
      }
    };

    ws.onerror = () => {
      if (!isCurrent()) return;
      setStatus("closed");
    };

    ws.onmessage = (event) => {
      if (!isCurrent()) return;
      let data: WireMessage;
      try {
        data = JSON.parse(event.data) as WireMessage;
      } catch {
        return;
      }

      switch (data.type) {
        case "message":
          addMessage(data as Message);
          break;
        case "presence":
          setOnline(data.online ?? []);
          break;
        case "typing":
          setPeerTyping(Boolean(data.is_typing));
          break;
        case "safety_hint": {
          // The frame carries a `code`, not the sentence: sending zh-HK text
          // from the server meant an English-locale user read Traditional
          // Chinese, and `check:i18n` could not see it because it only scans
          // frontend sources. Resolved here through the dictionary.
          const key = serverCodeKey(data.code ? `ws.safetyHint.${data.code}` : null);
          if (key) onNoticeRef.current(resolveRef.current(key));
          break;
        }
        case "error": {
          // A content-filter rejection arrives as a structured detail
          // (`{code: "content_rejected", params: {...}}`); the older shape was a
          // plain string. Both are handled so a rolling deploy cannot blank the
          // notice.
          const structuredCode =
            data.detail && typeof data.detail === "object"
              ? (data.detail as { code?: string }).code
              : undefined;
          const structuredKey = serverCodeKey(
            structuredCode ? `contentRejected.${structuredCode}` : null,
          );
          onNoticeRef.current(
            structuredKey
              ? resolveRef.current(structuredKey)
              : typeof data.detail === "string"
                ? data.detail
                : resolveRef.current("chat.sendFailed", { code: data.code ?? "" }),
          );
          break;
        }
        default:
          break;
      }
    };

    // Heartbeat keeps intermediaries from dropping an idle connection.
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, HEARTBEAT_MS);

    return () => {
      cancelled = true;
      clearInterval(ping);
      ws.close();
      // Only clear the shared ref if it still points at *this* socket. A new
      // effect may already have installed its own, and nulling that would make
      // the new connection's handlers fail `isCurrent()` and go silent.
      if (socketRef.current === ws) socketRef.current = null;
    };
  }, [roomId, addMessage]);

  // Auto-clear the "peer is typing" hint shortly after the last event.
  useEffect(() => {
    if (!peerTyping) return;
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => setPeerTyping(false), TYPING_LINGER_MS);
    return () => {
      if (typingTimer.current) clearTimeout(typingTimer.current);
    };
  }, [peerTyping]);

  return { status, messages, online, peerTyping, addMessage, send, notifyTyping };
}
