/**
 * Values shared between `playwright.config.ts` and the specs.
 *
 * Kept in one module so the ports have exactly one source of truth — a spec
 * that hard-codes 3099 while the config moved to 3098 would fail in a very
 * confusing way (the browser would silently talk to whatever else is listening).
 */

/** Deliberately not 8000/3000: those are what a developer is most likely to
 *  already have running, and testing against the wrong server is worse than
 *  a port clash. */
export const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? 8099);
export const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? 3099);

export const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
export const BASE_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

/** Throwaway SQLite file used by the backend during the run. */
export const E2E_DB_FILE = "e2e.db";
