import { clearSession, getSession, setSession, type SessionHint } from "./auth.ts";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

/** Proves a cookie-authenticated request comes from this page (CSRF). */
export const CSRF_HEADERS: Record<string, string> = { "X-Argus-CSRF": "1" };

/** Refresh this long before the access token expires. */
const REFRESH_MARGIN_MS = 15_000;

interface SessionBody {
  username: string;
  role: string;
  access_expires_at: string;
}

function hintFrom(body: SessionBody): SessionHint {
  return {
    username: body.username,
    role: body.role,
    accessExpiresAt: Date.parse(body.access_expires_at),
  };
}

/** Sign in; the server answers with httpOnly session cookies. */
export async function signIn(username: string, password: string): Promise<void> {
  const form = new URLSearchParams({ username, password });
  const res = await fetch(`${API_BASE}/auth/session`, {
    method: "POST",
    credentials: "include",
    headers: { ...CSRF_HEADERS, "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  if (!res.ok) {
    throw new Error(res.status === 401 ? "Invalid username or password" : `Sign-in failed (${res.status})`);
  }
  setSession(hintFrom((await res.json()) as SessionBody));
}

export async function signOut(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include", headers: CSRF_HEADERS });
  } finally {
    clearSession();
  }
}

async function requestRefresh(): Promise<boolean> {
  // Two attempts: when another tab rotated the refresh token a moment ago,
  // the first attempt loses the race but the browser already holds the
  // winner's new cookie.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: CSRF_HEADERS,
      });
    } catch {
      return false; // network trouble: keep the session, try again later
    }
    if (res.ok) {
      setSession(hintFrom((await res.json()) as SessionBody));
      return true;
    }
    if (res.status !== 401) return false;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  clearSession();
  return false;
}

let inFlight: Promise<boolean> | null = null;

/** Rotate the session once, however many callers ask at the same time. */
export function refreshSession(): Promise<boolean> {
  if (!inFlight) {
    inFlight = requestRefresh().finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}

/** True when a usable access token is (or has just been made) available. */
export async function ensureFreshSession(): Promise<boolean> {
  const hint = getSession();
  if (!hint) return false;
  if (hint.accessExpiresAt - Date.now() > REFRESH_MARGIN_MS) return true;
  return refreshSession();
}
