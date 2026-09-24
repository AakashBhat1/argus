/**
 * Client-side view of the dashboard session.
 *
 * The credentials themselves are httpOnly cookies set by the server: page
 * scripts (and therefore any XSS) cannot read them. What is stored here is a
 * non-secret hint for the UI: who is signed in and when the short-lived
 * access token expires, so it can be refreshed ahead of time. The server
 * decides on every request regardless of this hint.
 */

const SESSION_KEY = "argus_session";
// Bearer token stored by dashboards before cookie sessions; never kept.
const LEGACY_TOKEN_KEY = "argus_token";

export interface SessionHint {
  username: string;
  role: string;
  /** Epoch milliseconds. */
  accessExpiresAt: number;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function setSession(hint: SessionHint): void {
  const store = storage();
  store?.removeItem(LEGACY_TOKEN_KEY);
  store?.setItem(SESSION_KEY, JSON.stringify(hint));
}

export function getSession(): SessionHint | null {
  const store = storage();
  if (!store) return null;
  store.removeItem(LEGACY_TOKEN_KEY);
  const raw = store.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const hint = JSON.parse(raw) as Partial<SessionHint>;
    if (typeof hint.username !== "string" || typeof hint.accessExpiresAt !== "number") return null;
    return { username: hint.username, role: String(hint.role ?? ""), accessExpiresAt: hint.accessExpiresAt };
  } catch {
    return null;
  }
}

export function clearSession(): void {
  const store = storage();
  store?.removeItem(SESSION_KEY);
  store?.removeItem(LEGACY_TOKEN_KEY);
}

export const isAuthenticated = (): boolean => getSession() !== null;
