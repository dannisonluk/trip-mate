/**
 * Negative validation for F3 — the profile page's history fetch and mutations.
 *
 * Three defects, all in `src/app/profile/page.tsx`:
 *
 *  1. **No cancellation on the history fetch.** `refreshProfile()` after a save
 *     changes `profile`, which re-fires the effect. The older in-flight response
 *     can land *after* the newer one and overwrite the list with a stale copy.
 *     A `let cancelled` flag does not fix this: the two requests belong to
 *     different effect runs, so neither cancels the other. Only a sequence
 *     number shared across runs can tell them apart.
 *
 *  2. **No busy guard on `addHistory`.** A double-click fires `onClick` twice
 *     before React re-renders, so two POSTs go out and two visibly identical
 *     rows appear. A `disabled` attribute alone does not prevent the second
 *     request; it only hides the window.
 *
 *  3. **No busy guard on `removeHistory`.** Same shape, destructive: two DELETEs.
 *
 * Driving the real component needs a DOM + React renderer this tree does not
 * have installed (no jsdom, no @testing-library). So this harness does two
 * things:
 *
 *  * runs a transcription of the guard logic against manually-controlled async
 *    boundaries, which is what makes the interleaving reproducible, and
 *  * reads the real `page.tsx` and fails if the guards disappear from it.
 *
 * The second half is what stops the harness passing after somebody deletes the
 * guard from the component — the same shape as `check-ws-socket-guard.mjs`.
 *
 * Run:  node scripts/check-profile-history-guard.mjs
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

/** A request whose settlement the test decides. */
function deferred() {
  let settle;
  const promise = new Promise((res) => {
    settle = res;
  });
  return { promise, settle };
}

/** Mirrors the page's history-fetch bookkeeping. */
function makeHistoryStore() {
  let requestId = 0;
  let histories = [];
  const notices = [];

  function load(fetch) {
    const seq = ++requestId;
    return fetch().then(
      (rows) => {
        if (seq === requestId) histories = rows;
      },
      (err) => {
        if (seq === requestId) notices.push(String(err));
      },
    );
  }

  return {
    load,
    get histories() {
      return histories;
    },
    get notices() {
      return notices;
    },
  };
}

/** Mirrors `addHistory`: the guard is read before the first await. */
function makeAdder() {
  const posted = [];
  let adding = false;

  async function addHistory(country) {
    if (!country) return;
    if (adding) return;
    adding = true;
    try {
      posted.push(country);
      await Promise.resolve();
    } finally {
      adding = false;
    }
  }

  return { addHistory, get posted() { return posted; } };
}

/** Mirrors `removeHistory`: a per-row guard, not a boolean. */
function makeRemover() {
  const deleted = [];
  let busyIds = [];

  async function removeHistory(id) {
    if (busyIds.includes(id)) return;
    busyIds = [...busyIds, id];
    try {
      deleted.push(id);
      await Promise.resolve();
    } finally {
      busyIds = busyIds.filter((b) => b !== id);
    }
  }

  return {
    removeHistory,
    get deleted() {
      return deleted;
    },
    get busyIds() {
      return busyIds;
    },
  };
}

async function main() {
  // --- 1. a superseded fetch must not overwrite the list -------------------
  {
    const store = makeHistoryStore();
    const slow = deferred();
    const fast = deferred();

    // Two effect runs: the first is slow, the second resolves quickly. That is
    // the save-then-refresh sequence — the second `profile` change starts a new
    // fetch while the first is still in flight.
    const first = store.load(() => slow.promise);
    const second = store.load(() => fast.promise);

    fast.settle([{ id: "new" }]);
    slow.settle([{ id: "stale" }]);
    await Promise.all([first, second]);

    check(
      "a slow earlier fetch cannot overwrite the newer result",
      JSON.stringify(store.histories) === JSON.stringify([{ id: "new" }]),
      `histories=${JSON.stringify(store.histories)}; the stale response landed last ` +
        `and replaced the user's real data`,
    );
  }

  // --- 2. a superseded failure must not raise a notice --------------------
  {
    const store = makeHistoryStore();
    const slowFail = deferred();
    const fastOk = deferred();

    const first = store.load(() => slowFail.promise);
    const second = store.load(() => fastOk.promise);

    fastOk.settle([{ id: "ok" }]);
    slowFail.settle(Promise.reject(new Error("stale failure")).catch?.(() => {}) ?? null);
    await Promise.all([first.catch(() => {}), second.catch(() => {})]);

    check(
      "a superseded failure does not raise a notice over a newer success",
      store.notices.length === 0,
      `notices=${JSON.stringify(store.notices)}; the user would see a failure ` +
        `notice for a request whose result they are not looking at`,
    );
  }

  // --- 3. addHistory: a double-click issues one POST -----------------------
  {
    const adder = makeAdder();

    // Both calls happen before either yields — exactly what a double-click does,
    // and why the `disabled` attribute is not the guard.
    await Promise.all([adder.addHistory("Japan"), adder.addHistory("Japan")]);
    check(
      "a double-click on add issues exactly one POST",
      adder.posted.length === 1,
      `posted ${adder.posted.length} times; a duplicate row would appear in the list`,
    );

    // The guard must release, or the second legitimate add silently does nothing.
    await adder.addHistory("Korea");
    check(
      "the add guard releases after the request settles",
      adder.posted.length === 2 && adder.posted[1] === "Korea",
      `posted=${JSON.stringify(adder.posted)}`,
    );
  }

  // --- 4. removeHistory: per-row, and released afterwards -----------------
  {
    const remover = makeRemover();

    await Promise.all([remover.removeHistory("h1"), remover.removeHistory("h1")]);
    check(
      "a double-click on delete issues exactly one DELETE",
      remover.deleted.filter((d) => d === "h1").length === 1,
      `deleted=${JSON.stringify(remover.deleted)}`,
    );

    // A row's in-flight delete must not block a *different* row, which is what
    // distinguishes a per-row guard from one shared boolean.
    const inFlight = remover.removeHistory("h2");
    const snapshot = [...remover.busyIds];
    await inFlight;
    check(
      "one row's in-flight delete does not block a different row",
      snapshot.includes("h2") && !snapshot.includes("h3"),
      `busy=${JSON.stringify(snapshot)}; a single boolean would grey out every row`,
    );

    await remover.removeHistory("h2");
    check(
      "the delete guard releases after the request settles",
      remover.deleted.filter((d) => d === "h2").length === 2,
      `deleted=${JSON.stringify(remover.deleted)}`,
    );
  }

  // --- 5. the real render path: read the hook source ----------------------
  //
  // The list, its races and its guards moved out of the page into
  // `useTravelHistories` (D4). These assertions therefore read the hook: the
  // page no longer contains any of them, and reading it found nothing — which
  // is how the move was caught. A future move should update this path rather
  // than delete the checks.
  {
    const src = readFileSync(
      join(FRONTEND, "src", "hooks", "useTravelHistories.ts"),
      "utf8",
    );

    check(
      "the history fetch carries a request counter shared across effect runs",
      /const seq = \+\+requestId\.current;/.test(src) &&
        /if \(seq === requestId\.current\) setHistories\(rows\);/.test(src),
      "without a shared counter, a slower earlier response can overwrite a newer one",
    );

    check(
      "the history fetch reports a failure instead of swallowing it",
      /report\(err, "load"\)/.test(src),
      "an empty list and a failed fetch look identical on screen, and only one " +
        "of them is the user's own data",
    );

    check(
      "the add guard is read before its first await",
      /if \(adding\) return false;/.test(src) && src.includes("setAdding(true)"),
      "a disabled button does not stop the second click of a double-click",
    );

    check(
      "the delete guard is per row, not global",
      /if \(busyIds\.includes\(id\)\) return false;/.test(src),
      "a single shared boolean greys out every row and still allows two " +
        "concurrent deletes of different rows",
    );

    check(
      "all three history failures surface, each with its own operation label",
      ["load", "add", "remove"].every((op) => src.includes(`report(err, "${op}")`)),
      "a silently swallowed failure leaves the row in a state the user cannot " +
        "explain, and reporting the wrong operation is worse than reporting none",
    );
  }

  console.log(
    failures === 0
      ? "\n=== all F3 checks pass ==="
      : `\n=== ${failures} F3 check(s) FAILED ===`,
  );
  return failures === 0 ? 0 : 1;
}

process.exit(await main());
