import { existsSync } from "node:fs";
import path from "node:path";

/**
 * The interpreter that actually has the backend dependencies.
 *
 * Lives in its own module because **two** callers need the same answer: the
 * Playwright config (to start uvicorn) and `helpers.ts` (to run the operator
 * CLI). Duplicating it produced a real failure — the config was given an
 * explicit `E2E_PYTHON`, the helper was not, and the helper silently fell back
 * to a bare `python` without `sqlalchemy`, so only the tests that promoted an
 * admin failed.
 *
 * A bare `python` is not safe: on a machine with several interpreters it often
 * resolves to one lacking fastapi/uvicorn, and the server then dies with
 * `ModuleNotFoundError` before the first test — which surfaces as a baffling
 * "webServer timed out" rather than the real cause.
 *
 * Order: explicit override → backend/.venv → repo-root .venv → plain python.
 */
export function resolvePython(): string {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;

  const win = process.platform === "win32";
  // `__dirname` is `frontend/e2e`, so two levels up is the repo root.
  const repoRoot = path.resolve(__dirname, "../..");

  for (const venv of [path.join(repoRoot, "backend", ".venv"), path.join(repoRoot, ".venv")]) {
    const exe = win
      ? path.join(venv, "Scripts", "python.exe")
      : path.join(venv, "bin", "python");
    if (existsSync(exe)) return exe;
  }

  return win ? "python" : "python3";
}

/** Quoted, because it is interpolated into a shell command by the config. */
export function quotedPython(): string {
  return `"${resolvePython()}"`;
}

export const BACKEND_DIR = path.resolve(__dirname, "../../backend");
