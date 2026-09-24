import { test } from "node:test";
import assert from "node:assert/strict";
import { containedPointToNormalized } from "./geometry.ts";

const rect = { left: 100, top: 50, width: 800, height: 600 };

test("pillarboxed 16:9 content inside a 4:3 box", () => {
  // 1280x720 scaled to 800x450, centred vertically with 75px bars.
  assert.deepEqual(containedPointToNormalized(rect, 1280, 720, 100 + 400, 50 + 75 + 225), { x: 0.5, y: 0.5 });
  assert.deepEqual(containedPointToNormalized(rect, 1280, 720, 100, 50 + 75), { x: 0, y: 0 });
});

test("clicks on the letterbox bars are ignored", () => {
  assert.equal(containedPointToNormalized(rect, 1280, 720, 500, 50 + 10), null);
  assert.equal(containedPointToNormalized(rect, 1280, 720, 500, 50 + 590), null);
});

test("naive element-relative mapping would be off by the bar height", () => {
  const point = containedPointToNormalized(rect, 1280, 720, 500, 50 + 75 + 45);
  assert.ok(point);
  assert.ok(Math.abs(point.y - 0.1) < 1e-9);
  const naive = (75 + 45) / rect.height;
  assert.notEqual(Number(naive.toFixed(3)), 0.1);
});

test("degenerate sizes return null", () => {
  assert.equal(containedPointToNormalized({ ...rect, width: 0 }, 1280, 720, 0, 0), null);
  assert.equal(containedPointToNormalized(rect, 0, 720, 0, 0), null);
});
