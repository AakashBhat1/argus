import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { getSession, setSession } from "./auth.ts";
import { CSRF_HEADERS, ensureFreshSession, refreshSession } from "./session.ts";

// Minimal browser surface: localStorage + fetch.
class MemoryStorage {
  private items = new Map<string, string>();
  getItem(key: string) { return this.items.get(key) ?? null; }
  setItem(key: string, value: string) { this.items.set(key, value); }
  removeItem(key: string) { this.items.delete(key); }
}
// Neither module touches window at import time.
const g = globalThis as unknown as { window: { localStorage: MemoryStorage }; fetch: typeof fetch };
g.window = { localStorage: new MemoryStorage() };

let calls: { url: string; init?: RequestInit }[] = [];
let responses: Response[] = [];

function sessionBody(expiresInMs: number) {
  return JSON.stringify({
    username: "ops",
    role: "operator",
    access_expires_at: new Date(Date.now() + expiresInMs).toISOString(),
  });
}

beforeEach(() => {
  g.window.localStorage = new MemoryStorage();
  calls = [];
  responses = [];
  g.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (!next) throw new Error("unexpected fetch");
    return next;
  }) as typeof fetch;
});

test("concurrent refreshes share one request", async () => {
  setSession({ username: "ops", role: "operator", accessExpiresAt: Date.now() - 1 });
  responses.push(new Response(sessionBody(600_000), { status: 200 }));
  const results = await Promise.all([refreshSession(), refreshSession(), ensureFreshSession()]);
  assert.deepEqual(results, [true, true, true]);
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/auth\/refresh$/);
  assert.equal(calls[0].init?.credentials, "include");
  assert.deepEqual(calls[0].init?.headers, CSRF_HEADERS);
  assert.ok((getSession()?.accessExpiresAt ?? 0) > Date.now());
});

test("a lost cross-tab race is retried once", async () => {
  setSession({ username: "ops", role: "operator", accessExpiresAt: Date.now() - 1 });
  responses.push(new Response("{}", { status: 401 }), new Response(sessionBody(600_000), { status: 200 }));
  assert.equal(await refreshSession(), true);
  assert.equal(calls.length, 2);
});

test("an ended session clears the hint", async () => {
  setSession({ username: "ops", role: "operator", accessExpiresAt: Date.now() - 1 });
  responses.push(new Response("{}", { status: 401 }), new Response("{}", { status: 401 }));
  assert.equal(await refreshSession(), false);
  assert.equal(getSession(), null);
});

test("fresh sessions are not refreshed; signed-out users are not either", async () => {
  setSession({ username: "ops", role: "operator", accessExpiresAt: Date.now() + 120_000 });
  assert.equal(await ensureFreshSession(), true);
  g.window.localStorage = new MemoryStorage();
  assert.equal(await ensureFreshSession(), false);
  assert.equal(calls.length, 0);
});

test("a legacy bearer token left in storage is removed", () => {
  g.window.localStorage.setItem("argus_token", "eyJ...");
  assert.equal(getSession(), null);
  assert.equal(g.window.localStorage.getItem("argus_token"), null);
});
