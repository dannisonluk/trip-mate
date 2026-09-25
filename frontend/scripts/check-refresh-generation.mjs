/**
 * Negative validation for F1 — the refresh generation guard.
 *
 * `tryRefresh` writes an access token when it resolves. If the session changed
 * *while the refresh was outstanding*, that token belongs to a session that no
 * longer exists, and installing it means requests made under the new session
 * carry the old one.
 *
 * The race is not observable through Playwright — a `fetch` round trip against
 * a local server resolves far too fast to interleave deliberately — so this
 * drives the real module directly and *controls the interleaving* by resolving a
 * stubbed fetch on command. Same reasoning the project applies to the backend
 * races: when a race cannot be scheduled in the integration environment,
 * reproduce the failure mode against the unit and take the deterministic
 * ordering as the evidence.
 *
 * `api.ts` is compiled with the project's own `tsc` (the only compiler
 * available here — no esbuild/tsx in the tree) into a temporary directory, and
 * the emitted module is imported. That way the code under test is the real
 * source, not a transcription of it.
 *
 * Run:  node scripts/check-refresh-generation.mjs
 */
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..");
const SRC = join(FRONTEND, "src", "lib", "api.ts");

/**
 * Compile `api.ts` **in process** with the TypeScript compiler API.
 *
 * Not `execFileSync`: on this sandboxed Windows toolchain `spawnSync` fails with
 * EINVAL for any child process, and `next build` works only because Next spawns
 * differently. Using the API sidesteps the launcher entirely — no child process,
 * nothing to be blocked.
 */
async function loadApiModule() {
  const dir = mkdtempSync(join(tmpdir(), "tripmate-f1-"));
  const outfile = join(dir, "api.mjs");

  const ts = await import(
    pathToFileURL(
      join(FRONTEND, "node_modules", "typescript", "lib", "typescript.js"),
    ).href
  );
  const tsc = ts.default ?? ts;

  const source = readFileSync(SRC, "utf8");
  const program = tsc.transpileModule(source, {
    compilerOptions: {
      module: tsc.ModuleKind.ESNext,
      target: tsc.ScriptTarget.ES2022,
      moduleResolution: tsc.ModuleResolutionKind.Bundler,
      isolatedModules: true,
    },
    fileName: SRC,
  });

  // The type-only import of `./types` has no runtime counterpart; drop it so
  // the emitted module is importable on its own.
  let code = program.outputText;
  code = code.replace(/^\s*import\s+type\s+.*?;\s*$/gm, "");
  code = code.replace(/^\s*import\s*\{[^}]*\}\s*from\s*["']\.\/types["'];?\s*$/gm, "");
  writeFileSync(outfile, code, "utf8");
  return import(pathToFileURL(outfile).href);
}

/** A fetch stub whose responses are resolved by the test, not by the network. */
function makeControlledFetch() {
  const pending = [];
  const fetch = (url) => {
    let resolve;
    const promise = new Promise((r) => {
      resolve = r;
    });
    pending.push({ url, resolve });
    return promise;
  };
  return { fetch, pending };
}

let failures = 0;
function check(name, passed, detail = "") {
  console.log(`${passed ? "PASS" : "FAIL"}  ${name}`);
  if (!passed) {
    failures += 1;
    if (detail) console.log(`        ${detail}`);
  }
}

async function main() {
  const api = await loadApiModule();
  const { fetch, pending } = makeControlledFetch();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetch;

  const jsonResponse = (body) => ({
    ok: true,
    status: 200,
    json: async () => body,
  });

  try {
    // --- the defect: a session change during an in-flight refresh ----------
    api.setAccessToken("old-token");
    pending.length = 0;

    const inflight = api.tryRefresh();
    // No await before this: the session change must land while the refresh is
    // still unresolved. That interleaving is the whole point.
    api.setAccessToken("new-session-token");
    // Only now does the stale refresh resolve.
    pending[0].resolve(jsonResponse({ access_token: "stale-refresh-token" }));
    const ok = await inflight;

    check(
      "a refresh that lost the session race does not install its token",
      api.getAccessToken() === "new-session-token",
      `token=${JSON.stringify(api.getAccessToken())}, expected "new-session-token"`,
    );
    check(
      "the losing refresh reports failure rather than success",
      ok === false,
      `tryRefresh returned ${JSON.stringify(ok)}; the token was discarded, so ` +
        `reporting success would make the caller retry against a dead session`,
    );

    // --- the happy path must still work -----------------------------------
    api.setAccessToken("session-a");
    pending.length = 0;
    const good = api.tryRefresh();
    pending[0].resolve(jsonResponse({ access_token: "refreshed-a" }));
    const goodOk = await good;

    check(
      "an uncontested refresh still installs its token",
      api.getAccessToken() === "refreshed-a" && goodOk === true,
      `token=${JSON.stringify(api.getAccessToken())} returned=${JSON.stringify(goodOk)}`,
    );

    // --- logout during a refresh must not resurrect the token -------------
    api.setAccessToken("session-b");
    pending.length = 0;
    const duringLogout = api.tryRefresh();
    api.setAccessToken(null); // logout
    pending[0].resolve(jsonResponse({ access_token: "resurrected" }));
    await duringLogout;

    check(
      "a logout during a refresh is not undone by the refresh",
      api.getAccessToken() === null,
      `token=${JSON.stringify(api.getAccessToken())}, expected null after logout`,
    );

    // --- single-flight is preserved ---------------------------------------
    api.setAccessToken("session-c");
    pending.length = 0;
    const first = api.tryRefresh();
    const second = api.tryRefresh();
    check(
      "concurrent callers share one request (no rotating-cookie replay)",
      pending.length === 1,
      `${pending.length} requests issued; more than one replays the rotating cookie ` +
        `and the backend treats a replay as compromise, killing every session`,
    );
    pending[0].resolve(jsonResponse({ access_token: "refreshed-c" }));
    const [a, b] = await Promise.all([first, second]);
    check("both concurrent callers observe the same result", a === true && b === true);
    check(
      "the shared refresh installs its token",
      api.getAccessToken() === "refreshed-c",
      `token=${JSON.stringify(api.getAccessToken())}`,
    );

    // --- a failed refresh installs nothing --------------------------------
    api.setAccessToken("session-d");
    pending.length = 0;
    const failing = api.tryRefresh();
    pending[0].resolve({ ok: false, status: 401, json: async () => ({}) });
    const failingOk = await failing;
    check(
      "a rejected refresh leaves the existing token alone",
      api.getAccessToken() === "session-d" && failingOk === false,
      `token=${JSON.stringify(api.getAccessToken())} returned=${JSON.stringify(failingOk)}`,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  console.log(
    failures === 0
      ? "\n=== all F1 checks pass ==="
      : `\n=== ${failures} F1 check(s) FAILED ===`,
  );
  return failures === 0 ? 0 : 1;
}

process.exit(await main());
