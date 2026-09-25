/**
 * Negative validation for F2 — the per-socket handler guard in the chat page.
 *
 * `ws.close()` is asynchronous, so the previous socket's `onclose`/`onmessage`
 * still fire after the room-switch effect has torn down and a new socket is
 * installed. Unguarded, those late events write state describing a room the user
 * has already left: a stale `presence` list, a message appended to the wrong
 * thread, or a "connection failed" notice over a healthy connection.
 *
 * The bug is also a *shared-ref* bug: `openedRef` was one boolean for the whole
 * component, but "did this socket open?" is per-connection. The teardown reset
 * the flag, so the stale `onclose` read the *new* socket's state and reported a
 * clean room switch as a refused handshake.
 *
 * Driving the real React component needs a DOM + React renderer that this tree
 * does not have installed (no jsdom, no @testing-library). So this harness
 * exercises the same logic extracted as a small state machine which the page
 * uses verbatim — the guard is `socketRef.current === ws`, and the harness
 * asserts exactly that predicate's consequences.
 *
 * Run:  node scripts/check-ws-socket-guard.mjs
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..");

let failures = 0;
function check(name, passed, detail = "") {
  console.log(`${passed ? "PASS" : "FAIL"}  ${name}`);
  if (!passed) {
    failures += 1;
    if (detail) console.log(`        ${detail}`);
  }
}

/**
 * Mirrors the page's socket bookkeeping exactly.
 *
 * The page keeps `socketRef` (the live socket), `openedSockets` (a WeakSet
 * keyed on the socket), and a `cancelled` flag from the effect closure. Every
 * handler begins with `isCurrent()`.
 */
function makeRoomSession() {
  const state = {
    status: "idle",
    online: [],
    messages: [],
    notice: null,
    roomId: null,
  };
  let socketRef = null;
  let cancelled = false;
  const openedSockets = new WeakSet();

  function FakeSocket(roomId, shouldOpen) {
    return {
      roomId,
      readyState: 1,
      handlers: {},
      closed: false,
      addEventListenerless: true,
      _shouldOpen: shouldOpen,
      close() {
        this.closed = true;
      },
    };
  }

  // --- connect(roomId): the effect body, minus React ------------------------
  function connect(roomId, { shouldOpen = true } = {}) {
    cancelled = false;
    state.roomId = roomId;
    state.status = "connecting";
    state.messages = [];
    state.online = [];

    const ws = FakeSocket(roomId, shouldOpen);
    socketRef = ws;

    const isCurrent = () => socketRef === ws && !cancelled;

    ws.handlers.open = () => {
      if (!isCurrent()) return;
      openedSockets.add(ws);
      state.status = "open";
    };
    ws.handlers.close = (event) => {
      if (!isCurrent()) return;
      state.status = "closed";
      if (!openedSockets.has(ws)) {
        state.notice = event.code === 1008 ? "forbidden" : "failed";
      }
    };
    ws.handlers.presence = (online) => {
      if (!isCurrent()) return;
      state.online = online;
    };
    ws.handlers.message = (id) => {
      if (!isCurrent()) return;
      if (!state.messages.some((m) => m.id === id)) state.messages.push({ id });
    };

    // Effect cleanup.
    const cleanup = () => {
      cancelled = true;
      ws.close();
      if (socketRef === ws) socketRef = null;
    };

    return { ws, cleanup };
  }

  return { state, connect, get status() {
    return state.status;
  } };
}

function main() {
  // --- 1. a stale socket's close must not raise a notice --------------------
  {
    const s = makeRoomSession();
    const first = s.connect("room-1");
    first.ws.handlers.open(); // opened cleanly
    check("the first connection reports open", s.state.status === "open", s.state.status);

    // User switches rooms: old socket is closed asynchronously.
    first.cleanup();
    const second = s.connect("room-2");
    second.ws.handlers.open();

    // The *old* socket's onclose finally arrives, after the switch.
    first.ws.handlers.close({ code: 1006 });

    check(
      "a late close from the previous room does not report a failure",
      s.state.notice === null,
      `notice=${JSON.stringify(s.state.notice)}; switching rooms would show a ` +
        `spurious "connection failed" over a healthy connection`,
    );
    check(
      "a late close from the previous room does not disturb the new status",
      s.state.status === "open",
      `status=${s.state.status}; the new socket is open and must stay reported as open`,
    );
  }

  // --- 2. stale presence must not overwrite the new room's list -------------
  {
    const s = makeRoomSession();
    const first = s.connect("room-1");
    first.ws.handlers.open();
    first.ws.handlers.presence(["old-peer-a", "old-peer-b"]);

    first.cleanup();
    const second = s.connect("room-2");
    second.ws.handlers.open();
    second.ws.handlers.presence(["new-peer"]);

    // Late presence frame from the abandoned room.
    first.ws.handlers.presence(["old-peer-a", "old-peer-b"]);

    check(
      "a late presence frame does not replace the current room's members",
      JSON.stringify(s.state.online) === JSON.stringify(["new-peer"]),
      `online=${JSON.stringify(s.state.online)}; showing the previous room's members ` +
        `is a privacy leak as well as a UI bug`,
    );
  }

  // --- 3. stale messages must not land in the new thread --------------------
  {
    const s = makeRoomSession();
    const first = s.connect("room-1");
    first.ws.handlers.open();

    first.cleanup();
    const second = s.connect("room-2");
    second.ws.handlers.open();
    second.ws.handlers.message("m-2");

    first.ws.handlers.message("m-1-from-old-room");

    check(
      "a late message does not append to the current room's thread",
      s.state.messages.length === 1 && s.state.messages[0].id === "m-2",
      `messages=${JSON.stringify(s.state.messages)}`,
    );
  }

  // --- 4. the original defect: a shared opened flag -------------------------
  //
  // The direction matters, and it is not the obvious one. A *late close* from
  // the old socket is harmless with a shared flag, because the new socket has
  // already set it true. The damage runs the other way: the old socket's late
  // **open** sets the flag true for a socket that never opened, so the new
  // socket's genuine rejection finds `opened === true` and is silently swallowed
  // — the user sees a dead chat with no explanation.
  {
    let opened = false;
    let notice = null;

    function connectShared() {
      const ws = { handlers: {} };
      opened = false;
      ws.handlers.open = () => {
        opened = true;
      };
      ws.handlers.close = (event) => {
        // No isCurrent() guard, and no per-socket identity — the original code.
        if (!opened) notice = event.code === 1008 ? "forbidden" : "failed";
      };
      return ws;
    }

    const first = connectShared();
    const second = connectShared();

    // The first socket opens *late*, after the second connect reset the flag.
    first.handlers.open();

    // The second socket was refused by the server. This must be reported.
    second.handlers.close({ code: 1008 });

    check(
      "DEMONSTRATION: the unguarded version swallows a real rejection",
      notice === null,
      `notice=${JSON.stringify(notice)} — expected the bug to reproduce (null notice), ` +
        `but the unguarded version reported the rejection. If this inverts, the ` +
        `scenario above is no longer the failure mode being fixed.`,
    );
  }

  // --- 5. a genuinely refused handshake still reports ----------------------
  {
    const s = makeRoomSession();
    const only = s.connect("room-1");
    // Never opened; the server rejects it.
    only.ws.handlers.close({ code: 1008 });

    check(
      "a real handshake rejection is still reported",
      s.state.notice === "forbidden" && s.state.status === "closed",
      `notice=${JSON.stringify(s.state.notice)} status=${s.state.status}; a guard that ` +
        `suppressed this would hide the actual failure`,
    );
  }

  // --- 6. the real render-path guard: mutate the page source ----------------
  //
  // The checks above run a transcription of the logic. This one reads the actual
  // file and fails if the guard disappears from it, so the harness cannot keep
  // passing after somebody deletes the guard from the component.
  //
  // The socket bookkeeping moved out of the page into `useRoomSocket` (D4), so
  // these assertions now read the hook. Reading the page would have found no
  // handlers at all and failed — which is how this was caught. A future move
  // should update this path rather than delete the checks.
  {
    const hookPath = join(FRONTEND, "src", "hooks", "useRoomSocket.ts");
    const src = readFileSync(hookPath, "utf8");
    const handlerBodies = src.slice(src.indexOf("ws.onopen"));
    // One guard per socket handler: onopen, onclose, onerror, onmessage. The
    // message-type switch lives *inside* onmessage, so it is covered by that
    // handler's guard and must not be counted separately.
    const guardCount = (handlerBodies.match(/if \(!isCurrent\(\)\) return;/g) ?? []).length;
    check(
      "every socket handler guards on socket identity",
      guardCount >= 4,
      `found ${guardCount} guard(s) in the handler block; expected at least 4 ` +
        `(onopen, onclose, onerror, onmessage)`,
    );
    check(
      "the hook keys 'did it open' on the socket, not a shared boolean",
      // Both names must appear on the *same line*: asserting them separately
      // passed even after the declaration was changed to a plain `Set`, because
      // `WeakSet` still occurred elsewhere in the file (in a comment). A check
      // that a comment can satisfy is not a check.
      /const\s+openedSockets\s*=\s*useRef\(\s*new\s+WeakSet</.test(src),
      "the per-socket open tracking is gone — a shared flag misattributes one " +
        "connection's handshake result to another",
    );
    check(
      "the teardown only clears socketRef when it still owns it",
      src.includes("if (socketRef.current === ws) socketRef.current = null;"),
      "unconditionally nulling socketRef would make the new connection's handlers " +
        "fail isCurrent() and go silent",
    );
  }

  console.log(
    failures === 0
      ? "\n=== all F2 checks pass ==="
      : `\n=== ${failures} F2 check(s) FAILED ===`,
  );
  return failures === 0 ? 0 : 1;
}

process.exit(main());
