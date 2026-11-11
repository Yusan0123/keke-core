import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import worker, { processQueue } from "../src/worker.js";

function localDatabase() {
  const sqlite = new DatabaseSync(":memory:");
  function prepare(sql, values = []) {
    return {
      bind(...parameters) { return prepare(sql, parameters); },
      execute() { return { meta: { changes: Number(sqlite.prepare(sql).run(...values).changes) } }; },
      async run() { return this.execute(); },
      async all() { return { results: sqlite.prepare(sql).all(...values) }; },
      async first(column) {
        const result = sqlite.prepare(sql).get(...values);
        return column ? result?.[column] ?? null : result ?? null;
      },
    };
  }
  return {
    prepare,
    async batch(statements) {
      sqlite.exec("BEGIN");
      try {
        const results = statements.map(statement => statement.execute());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
    close() { sqlite.close(); },
  };
}

async function fixture(t) {
  const DB = localDatabase();
  t.after(() => DB.close());
  const schema = await readFile(new URL("../schema.sql", import.meta.url), "utf8");
  await DB.batch(schema.split(";").filter(sql => sql.trim()).map(sql => DB.prepare(sql)));
  const vectors = new Map();
  const upserts = new Map();
  const state = { failAI: false, failDelete: false, mutation: 0 };
  const env = {
    DB, MEMORY_API_TOKEN: "local-only-key",
    AI: { async run(model, input) {
      if (state.failAI) throw new Error("local simulated vector service failure");
      return { data: input.text.map(() => Array(1024).fill(0.1)) };
    } },
    VECTORIZE: {
      async upsert(records) {
        for (const record of records) {
          vectors.set(record.id, record.values);
          upserts.set(record.id, (upserts.get(record.id) || 0) + 1);
        }
        return { mutationId: String(++state.mutation) };
      },
      async deleteByIds(ids) {
        if (state.failDelete) throw new Error("local simulated index deletion failure");
        ids.forEach(id => vectors.delete(id));
        return { mutationId: String(++state.mutation) };
      },
      async describe() {
        return { vectorCount: vectors.size, processedUpToMutation: state.mutation ? String(state.mutation) : null,
          processedUpToDatetime: new Date().toISOString() };
      },
      async query() { return { matches: [...vectors.keys()].map(id => ({ id, score: 1 })) }; },
    },
  };
  async function call(path, body) {
    const response = await worker.fetch(new Request("https://local.invalid" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-Memory-Token": env.MEMORY_API_TOKEN, "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }), env);
    assert.equal(response.status, 200);
    return response.json();
  }
  return { env, state, vectors, upserts, call };
}

test("an embedding failure preserves the original and a retry creates no duplicate", async t => {
  const { env, state, call } = await fixture(t);
  const body = { content: "local-only durability test", date: "2026-11-08 21:33", tag: "情感", source: "来自MCP客户端" };
  state.failAI = true;
  const first = await call("/memory/ingest", body);
  assert.equal(first.added, 1);
  assert.equal(first.indexing_pending, true);
  const record = await env.DB.prepare("SELECT * FROM memories").first();
  assert.ok(record.content.endsWith(body.content));
  assert.equal(record.source, body.source);
  assert.equal(record.timestamp, body.date);
  const failedRetry = await call("/memory/ingest", body);
  assert.equal(failedRetry.added, 0);
  assert.equal(failedRetry.indexing_pending, true);
  state.failAI = false;
  const retry = await call("/memory/ingest", body);
  assert.equal(retry.added, 0);
  assert.equal(retry.skipped, 1);
  assert.equal(retry.indexing_pending, false);
  assert.equal(await env.DB.prepare("SELECT COUNT(*) AS n FROM memories").first("n"), 1);
});

test("a stale vector cannot expose a forgotten local test record", async t => {
  const { env, state, vectors, call } = await fixture(t);
  const saved = await call("/memory/save", { content: "local-only pending test", trigger: "来自MCP客户端" });
  await call("/pending/approve", { id: saved.id });
  assert.equal((await call("/memory/search?q=local")).results.length, 1);
  state.failDelete = true;
  await call("/memory/forget", { id: saved.id });
  assert.equal(vectors.size, 1);
  assert.equal((await call("/memory/search?q=local")).results.length, 0);
  assert.equal(await env.DB.prepare("SELECT kind FROM memories WHERE id = ?").bind(saved.id).first("kind"), "deleted");
  state.failDelete = false;
  await processQueue(env);
  assert.equal(vectors.size, 0);
});

test("concurrent index workers claim each local record once", async t => {
  const { env, upserts, call } = await fixture(t);
  await call("/memory/save", { content: "local-only first record" });
  await call("/memory/save", { content: "local-only second record" });
  await env.DB.prepare("UPDATE memories SET kind = 'active', index_state = 'pending'").run();
  const results = await Promise.all([processQueue(env), processQueue(env)]);
  assert.equal(results.reduce((sum, result) => sum + result.processed, 0), 2);
  assert.deepEqual([...upserts.values()], [1, 1]);
});

test("an undated retry across minutes keeps the first saved timestamp", async t => {
  const OriginalDate = Date;
  let clock = OriginalDate.parse("2026-11-11T00:00:00Z");
  globalThis.Date = class extends OriginalDate {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  };
  t.after(() => { globalThis.Date = OriginalDate; });
  const { env, state, call } = await fixture(t);
  state.failAI = true;
  const body = { content: "local-only retry across minutes", source: "来自MCP客户端" };
  const first = await call("/memory/ingest", body);
  const timestamp = await env.DB.prepare("SELECT timestamp FROM memories WHERE id = ?").bind(first.saved_ids[0]).first("timestamp");
  clock += 120000;
  state.failAI = false;
  const retry = await call("/memory/ingest", body);
  assert.equal(retry.added, 0);
  assert.deepEqual(retry.saved_ids, first.saved_ids);
  assert.equal(await env.DB.prepare("SELECT COUNT(*) AS n FROM memories").first("n"), 1);
  assert.equal(await env.DB.prepare("SELECT timestamp FROM memories").first("timestamp"), timestamp);
});
