import { test } from "node:test";
import assert from "node:assert/strict";

import { auditQuery } from "./audit-query.ts";

test("auditQuery drops empty filters and trims values", () => {
  assert.equal(auditQuery({}), "");
  assert.equal(auditQuery({ action: " camera.* ", actor: "", limit: 100 }), "?action=camera.*&limit=100");
});

test("auditQuery encodes the cursor and timestamps", () => {
  const qs = auditQuery({ cursor: "a+b/c=", since: "2026-01-01T00:00:00Z" });
  assert.equal(qs, "?cursor=a%2Bb%2Fc%3D&since=2026-01-01T00%3A00%3A00Z");
});
