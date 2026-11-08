"use client";

import { useEffect, useSyncExternalStore } from "react";

/**
 * Run `task(false)` once, then `task(true)` every `intervalMs` ("background"
 * refreshes may skip spinners). Keep `task` stable with useCallback.
 */
export function usePolling(task: (background: boolean) => unknown, intervalMs: number): void {
  useEffect(() => {
    const first = setTimeout(() => void task(false), 0);
    const every = setInterval(() => void task(true), intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(every);
    };
  }, [task, intervalMs]);
}

const noopSubscribe = () => () => {};

/** False during SSR and hydration, true afterwards (no setState-in-effect). */
export function useMounted(): boolean {
  return useSyncExternalStore(noopSubscribe, () => true, () => false);
}
