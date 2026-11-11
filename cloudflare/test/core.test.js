import test from "node:test";
import assert from "node:assert/strict";
import { digest, hongKongTime, splitText, validVector } from "../src/worker.js";

test("long memories retain all Unicode characters without splitting emoji", () => {
  const original = "Lumi🦑记忆。".repeat(200);
  const parts = splitText(original);
  assert.equal(parts.join(""), original);
  assert.ok(parts.length > 1);
  assert.ok(parts.every(part => Array.from(part).length <= 400));
  assert.ok(parts.every(part => !part.includes("\ufffd")));
});

test("blank input and 400 character boundaries remain predictable", () => {
  assert.deepEqual(splitText("  \n  "), []);
  assert.deepEqual(splitText("字".repeat(400)), ["字".repeat(400)]);
  assert.equal(splitText("字".repeat(401)).length, 2);
});

test("invalid legacy vectors cannot enter the searchable index", () => {
  assert.equal(validVector(Array(1024).fill(0.1)), true);
  assert.equal(validVector(Array(768).fill(0.1)), false);
  assert.equal(validVector([...Array(1023).fill(0.1), NaN]), false);
  assert.equal(validVector([...Array(1023).fill(0.1), Infinity]), false);
});

test("content fingerprints are stable and distinguish changed originals", async () => {
  assert.equal(await digest("来自MCP客户端"), await digest("来自MCP客户端"));
  assert.notEqual(await digest("来自MCP客户端"), await digest("ChatGPT"));
  assert.match(hongKongTime(), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
});
