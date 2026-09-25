import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import path from "node:path";

import { E2E_DB_FILE } from "./constants";
import { BACKEND_DIR, resolvePython } from "./python";

/**
 * Playwright global setup: start every run from a clean throwaway database, then
 * give it the reference data the UI depends on.
 *
 * Why the reset exists
 * -------------------
 * The database used to persist between local runs and merely accumulate: users,
 * trips and audit rows from previous runs were all still there. That made the
 * suite's behaviour a function of its own history rather than of the code. It
 * produced a real, confusing failure — `稽核紀錄可以依動作篩選` asserts that
 * filtering to an action nobody performed shows the empty state, and after
 * enough runs the list had grown past `AUDIT_PAGE_SIZE`, so the assertions were
 * exercising a paginated, non-empty list instead of the case they describe.
 *
 * Deleting the file is safe *because* the fallback below recreates it: the
 * backend's lifespan runs `create_all`, and `app.seed` fills the reference rows.
 * A test database that carries state across runs is not reproducible.
 *
 * Why the seed exists
 * ------------------
 * `app.main`'s lifespan creates tables but never seeds anything, and the
 * production source of `cities` is `scripts/import_cities.py` reading a 5.7 MB
 * GeoNames dump that must not be a prerequisite for running the test suite.
 * Without this step the `cities` table is empty, the select-only city picker has
 * nothing to offer, and every spec that wants to choose a city would have to
 * skip — which is the kind of "green suite that tests nothing" this project
 * treats as a defect.
 *
 * `python -m app.seed` populates a small set of *real* GeoNames rows
 * (`app/seed.py::DEMO_CITIES`) and is idempotent, so running it on every test
 * run is safe.
 *
 * The WAL sidecars go with the main file. Leaving a stale `-wal` next to a
 * deleted database makes SQLite recover the *old* contents on first connect,
 * which would silently defeat the reset.
 *
 * Ordering caveat
 * ---------------
 * Playwright runs `globalSetup` *before* starting `webServer`, so the reset here
 * is correctly ahead of the backend's first connection in the normal path.
 * Under `E2E_NO_WEBSERVER=1` the servers are started by the caller and therefore
 * already hold the old file open — deleting it would leave the backend writing
 * to an unlinked inode while the assertions read a fresh one. The reset is
 * skipped in that mode; the caller is responsible for a clean database, which is
 * the same responsibility they took on by managing the servers.
 *
 * Why the seed can be skipped
 * ---------------------------
 * `E2E_SKIP_SEED=1` turns the seed into a warning. It exists because some
 * environments forbid spawning a child process at all: inside the WorkBuddy
 * sandbox *every* `spawnSync` fails with `EBUSY` (measured — even
 * `node --version` and `bash -c echo`), so `execFileSync` here cannot work
 * regardless of which interpreter `resolvePython()` picks. Without this escape
 * hatch the whole run hangs at global setup with no test output, which reads as
 * a mysterious timeout rather than "the environment blocks subprocesses".
 *
 * It is only safe when the database already holds the reference rows — i.e. when
 * `E2E_NO_WEBSERVER=1` and the caller has seeded once. The specs that need cities
 * will fail loudly otherwise, which is the correct outcome.
 */
export default function globalSetup(): void {
  // Skipped under E2E_NO_WEBSERVER=1: the caller's backend already has the old
  // file open (see the ordering caveat above). The seed below still runs,
  // because it is idempotent and the picker needs its reference rows either way.
  if (process.env.E2E_NO_WEBSERVER !== "1") {
    for (const suffix of ["", "-shm", "-wal"]) {
      rmSync(path.join(BACKEND_DIR, E2E_DB_FILE + suffix), { force: true });
    }
  }

  if (process.env.E2E_SKIP_SEED === "1") {
    console.warn(
      "[global-setup] E2E_SKIP_SEED=1 — not seeding. Run it yourself:\n" +
        `  cd backend && ENV=development DATABASE_URL=sqlite+aiosqlite:///<abs path to ${E2E_DB_FILE}> \\\n` +
        "    <python with backend deps> -m app.seed",
    );
    return;
  }

  const databaseUrl = `sqlite+aiosqlite:///${path
    .join(BACKEND_DIR, E2E_DB_FILE)
    .split(path.sep)
    .join("/")}`;

  try {
    execFileSync(resolvePython(), ["-m", "app.seed"], {
      cwd: BACKEND_DIR,
      env: {
        ...process.env,
        ENV: "development",
        SECRET_KEY: process.env.SECRET_KEY ?? "e2e-secret-key-not-for-production",
        DATABASE_URL: databaseUrl,
      },
      stdio: "pipe",
    });
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code !== "EBUSY" && code !== "EPERM") throw err;
    // The environment forbids child processes. Say so precisely instead of
    // letting the run stall; the caller decides whether to seed and re-run.
    throw new Error(
      `[global-setup] Could not spawn ${resolvePython()} (${code}).\n` +
        "This environment blocks child-process creation, so the seed step cannot run.\n" +
        "Seed the database out-of-band, then re-run with E2E_SKIP_SEED=1 E2E_NO_WEBSERVER=1:\n" +
        `  cd backend && ENV=development DATABASE_URL=${databaseUrl} <python> -m app.seed`,
    );
  }
}
