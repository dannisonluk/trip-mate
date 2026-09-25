"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { TravelHistory } from "@/lib/types";

/**
 * The caller's travel-history list, extracted from `app/profile/page.tsx`.
 *
 * Why this is a hook and not inline
 * ---------------------------------
 * F3 found three lost-race defects in this one list, all of them the kind of
 * bookkeeping that is easy to write once and easy to lose on the next edit:
 *
 * 1. **A shared sequence counter, not a `let cancelled`.** Two things put the
 *    fetch out of order, and neither is theoretical: `refreshProfile()` after
 *    saving changes `profile` and re-fires the effect, and a slow first fetch
 *    can race a fast second one. A `cancelled` flag is per-effect-run, so it
 *    cannot distinguish two *different* runs — only a counter shared across
 *    runs can tell them apart.
 *
 * 2. **The add guard is read before the first `await`.** A double-click fires
 *    `onClick` twice before React re-renders, so a `disabled` attribute does
 *    not stop the second request — it only hides the window in which it could
 *    happen. Two POSTs create two visibly identical rows.
 *
 * 3. **The delete guard is per-row, not a shared boolean.** A single boolean
 *    greys out the whole list *and* still lets a double-click on a different
 *    row issue two concurrent deletes.
 *
 * The error paths are part of the contract, not an afterthought: an empty list
 * and a failed fetch render identically, and only one of them is the user's own
 * data. `onError` receives an already-resolved message.
 */

export interface UseTravelHistoriesOptions {
  /** The profile whose history to load. `undefined` disables the hook. */
  profileId: string | undefined;
  /**
   * Resolves a raw error to a user-facing message. Receives the operation so
   * the caller can pick a specific key — "could not load" and "could not add"
   * are different problems and telling the user the wrong one is worse than
   * telling them nothing.
   */
  describeError: (err: unknown, op: "load" | "add" | "remove") => string;
  /** Called with a resolved, user-facing message when an operation fails. */
  onError: (message: string) => void;
  /** Called after a successful add/remove, for a confirmation notice. */
  onSuccess?: (op: "add" | "remove") => void;
}

export interface TravelHistories<T> {
  histories: T[];
  loading: boolean;
  /** True while an add is in flight, so the form can disable its button. */
  adding: boolean;
  /** Ids with an in-flight delete, so each row can disable independently. */
  busyIds: string[];
  add: (payload: Record<string, unknown>) => Promise<boolean>;
  remove: (id: string) => Promise<boolean>;
  /** Re-fetch from scratch. */
  reload: () => void;
}

export function useTravelHistories({
  profileId,
  describeError,
  onError,
  onSuccess,
}: UseTravelHistoriesOptions): TravelHistories<TravelHistory> {
  const [histories, setHistories] = useState<TravelHistory[]>([]);
  const [loading, setLoading] = useState(Boolean(profileId));
  const [adding, setAdding] = useState(false);
  const [busyIds, setBusyIds] = useState<string[]>([]);

  // Bumped by every fetch so a superseded response cannot write. Shared across
  // effect runs on purpose — see rule 1 in the module docstring.
  const requestId = useRef(0);

  // Read through refs so the fetch effect depends on `profileId` alone.
  const describeRef = useRef(describeError);
  const onErrorRef = useRef(onError);
  const onSuccessRef = useRef(onSuccess);
  useEffect(() => {
    describeRef.current = describeError;
    onErrorRef.current = onError;
    onSuccessRef.current = onSuccess;
  });

  const report = useCallback((err: unknown, op: "load" | "add" | "remove") => {
    onErrorRef.current(describeRef.current(err, op));
  }, []);

  const load = useCallback(async (id: string) => {
    const seq = ++requestId.current;
    try {
      const rows = await api.listHistories(id);
      // The guard: only the newest request may write. Without the counter a
      // stale response silently replaces a fresher one.
      if (seq === requestId.current) setHistories(rows);
    } catch (err) {
      if (seq === requestId.current) {
        report(err, "load");
      }
    } finally {
      if (seq === requestId.current) setLoading(false);
    }
  }, [report]);

  useEffect(() => {
    if (!profileId) return;
    setLoading(true);
    void load(profileId);
  }, [profileId, load, report]);

  const reload = useCallback(() => {
    if (profileId) void load(profileId);
  }, [profileId, load]);

  const add = useCallback(
    async (payload: Record<string, unknown>) => {
      // Read before the first await. `disabled` on the button is for the user;
      // this is the guard. See rule 2 in the module docstring.
      if (adding) return false;
      setAdding(true);
      try {
        const created = await api.addHistory(payload);
        setHistories((prev) => [created, ...prev]);
        onSuccessRef.current?.("add");
        return true;
      } catch (err) {
        report(err, "add");
        return false;
      } finally {
        setAdding(false);
      }
    },
    [adding, report],
  );

  const remove = useCallback(
    async (id: string) => {
      // Per row, so one in-flight delete does not freeze the others. See rule 3.
      if (busyIds.includes(id)) return false;
      setBusyIds((prev) => [...prev, id]);
      try {
        await api.deleteHistory(id);
        setHistories((prev) => prev.filter((row) => row.id !== id));
        onSuccessRef.current?.("remove");
        return true;
      } catch (err) {
        report(err, "remove");
        return false;
      } finally {
        setBusyIds((prev) => prev.filter((busyId) => busyId !== id));
      }
    },
    [busyIds, report],
  );

  return { histories, loading, adding, busyIds, add, remove, reload };
}
