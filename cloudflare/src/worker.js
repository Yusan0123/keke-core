const MODEL = "@cf/baai/bge-m3";
const DIMENSIONS = 1024;
const LEASE_SECONDS = 120;
const STOP_WORDS = ["Lumi", "lumi", "Claude", "claude", "克克", "我们以前", "我们之前", "我们", "以前", "之前",
  "聊过", "说过", "提过", "记得", "还记得", "那个", "关于", "有没有", "是不是", "什么", "的事", "的事情",
  "帮我", "翻翻", "博物馆", "搜一下", "搜搜", "找找", "找一下"];

function cleanQuery(query) {
  let text = String(query || "");
  for (const word of [...STOP_WORDS].sort((a, b) => b.length - a.length)) text = text.split(word).join("");
  return text.trim() || String(query || "");
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function digest(text) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, "0")).join("");
}

export function hongKongTime() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date());
}

export function splitText(content, limit = 400) {
  const text = content.trim();
  const characters = Array.from(text);
  if (characters.length <= limit) return text ? [text] : [];
  const pieces = text.match(/[^。！？!?；;\n]*[。！？!?；;\n]|[^。！？!?；;\n]+$/gu) || [];
  const parts = [];
  let current = "";
  for (let piece of pieces) {
    if (!piece.trim()) continue;
    let chars = Array.from(piece);
    while (chars.length > limit) {
      if (current) parts.push(current.trim());
      current = "";
      parts.push(chars.slice(0, limit).join("").trim());
      chars = chars.slice(limit);
    }
    piece = chars.join("");
    if (Array.from(current + piece).length > limit && current) {
      parts.push(current.trim());
      current = "";
    }
    current += piece;
  }
  if (current.trim()) parts.push(current.trim());
  return parts.filter(Boolean);
}

export function validVector(values) {
  return Array.isArray(values) && values.length === DIMENSIONS && values.every(Number.isFinite);
}

async function authorize(request, env, pathKey) {
  const authorization = request.headers.get("Authorization") || "";
  const token = pathKey || request.headers.get("X-Memory-Token") ||
    (authorization.startsWith("Bearer ") ? authorization.slice(7) : "");
  if (!token || !env.MEMORY_API_TOKEN || await digest(token) !== await digest(env.MEMORY_API_TOKEN)) {
    throw new HttpError(401, "钥匙不对");
  }
}

function statement(env, sql, ...values) {
  return env.DB.prepare(sql).bind(...values);
}

async function rows(env, sql, ...values) {
  return (await statement(env, sql, ...values).all()).results;
}

function publicMemory(row, score) {
  return {
    ...(score === undefined ? {} : { score: Math.round(score * 1000) / 1000 }),
    id: row.id, timestamp: row.timestamp, content: row.content,
    source: row.source, tag: row.tag,
  };
}

async function embeddings(env, texts) {
  const result = await env.AI.run(MODEL, {
    text: texts.map(text => Array.from(text).slice(0, 512).join("")),
  });
  if (!Array.isArray(result.data) || result.data.length !== texts.length || !result.data.every(validVector)) {
    throw new Error("向量服务返回了不符合1024维要求的数据");
  }
  return result.data;
}

// The database is authoritative. Index updates are repeatable derived operations.
export async function processQueue(env, ids = [], limit = 16) {
  const now = Math.floor(Date.now() / 1000);
  const selection = ids.length ? ` AND id IN (${ids.map(() => "?").join(",")})` : "";
  const claimed = await rows(env, `
    UPDATE memories SET lease_until = ?, attempts = attempts + 1
    WHERE id IN (
      SELECT id FROM memories WHERE kind IN ('active', 'deleted')
      AND index_state = 'pending' AND lease_until <= ?${selection}
      ORDER BY updated_at, id LIMIT ?
    ) RETURNING *`, now + LEASE_SECONDS, now, ...ids, limit);
  if (!claimed.length) return { processed: 0 };
  try {
    const missing = claimed.filter(row => row.kind === "active" && !row.embedding);
    if (missing.length) {
      const values = await embeddings(env, missing.map(row => row.content));
      await env.DB.batch(missing.map((row, index) => {
        row.embedding = JSON.stringify(values[index]);
        return statement(env, "UPDATE memories SET embedding = ? WHERE id = ? AND revision = ? AND kind = 'active'",
          row.embedding, row.id, row.revision);
      }));
    }
    const active = claimed.filter(row => row.kind === "active");
    const deleted = claimed.filter(row => row.kind === "deleted");
    let activeMutation = "";
    let deletedMutation = "";
    if (active.length) {
      const vectors = active.map(row => {
        const values = JSON.parse(row.embedding);
        if (!validVector(values)) throw new Error("已存向量格式不符合索引要求");
        return { id: row.id, values };
      });
      activeMutation = (await env.VECTORIZE.upsert(vectors)).mutationId;
    }
    if (deleted.length) {
      deletedMutation = (await env.VECTORIZE.deleteByIds(deleted.map(row => row.id))).mutationId;
    }
    await env.DB.batch(claimed.flatMap(row => [
      statement(env, `UPDATE memories SET index_state = ?, index_mutation = ?, index_submitted_at = ?,
        index_error = NULL, lease_until = 0 WHERE id = ? AND revision = ? AND kind = ?`,
      row.kind === "deleted" ? "deleted" : "submitted", row.kind === "deleted" ? deletedMutation : activeMutation,
      new Date().toISOString(), row.id, row.revision, row.kind),
      statement(env, `UPDATE memories SET index_state = 'pending', lease_until = 0
        WHERE id = ? AND revision <> ?`, row.id, row.revision),
    ]));
    return { processed: claimed.length };
  } catch (error) {
    await env.DB.batch(claimed.map(row => statement(env,
      "UPDATE memories SET index_error = ?, lease_until = 0 WHERE id = ? AND index_state = 'pending'",
      String(error.message).slice(0, 500), row.id)));
    return { processed: 0, indexing_pending: true };
  }
}

async function indexSaved(env, ids) {
  await processQueue(env, ids, Math.min(ids.length, 16));
  await refreshIndexState(env);
  const pending = await statement(env,
    `SELECT COUNT(*) AS n FROM memories WHERE id IN (${ids.map(() => "?").join(",")}) AND index_state <> 'indexed'`,
    ...ids).first("n");
  return pending > 0;
}

async function search(env, query, requestedTopK = 5) {
  if (typeof query !== "string" || !query.trim()) throw new HttpError(422, "请填写要查找的内容");
  const topK = Math.max(1, Math.min(10, Math.trunc(Number(requestedTopK)) || 5));
  const [vector] = await embeddings(env, [query]);
  const matches = await env.VECTORIZE.query(vector, { topK: Math.min(100, topK * 3) });
  const ids = matches.matches.map(match => match.id);
  if (!ids.length) return { results: [] };
  const records = await rows(env,
    `SELECT id, content, timestamp, source, tag FROM memories WHERE kind = 'active' AND id IN (${ids.map(() => "?").join(",")})`, ...ids);
  const byId = new Map(records.map(row => [row.id, row]));
  return { results: matches.matches.filter(match => match.score > 0.3 && byId.has(match.id))
    .slice(0, topK).map(match => publicMemory(byId.get(match.id), match.score)) };
}

async function insertMemory(env, record, dedupeKey) {
  const now = new Date().toISOString();
  const hash = await digest(record.content);
  const id = record.id || crypto.randomUUID();
  const result = await statement(env, `INSERT INTO memories
    (id, content, timestamp, source, tag, trigger, kind, content_hash, dedupe_key, embedding,
     original_json, index_state, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING`,
  id, record.content, record.timestamp || "", record.source || "", record.tag || "", record.trigger || "",
  record.kind, hash, dedupeKey, record.embedding ? JSON.stringify(record.embedding) : null,
  record.original_json || null, record.kind === "pending" ? "waiting" : "pending", now, now).run();
  const saved = result.meta.changes ? { id } : await statement(env,
    "SELECT id FROM memories WHERE dedupe_key = ? OR id = ? LIMIT 1", dedupeKey, id).first();
  return { id: saved.id, added: result.meta.changes > 0 };
}

async function ingest(env, body) {
  if (typeof body.content !== "string" || !body.content.trim()) throw new HttpError(422, "记忆正文不能为空");
  if (Array.from(body.content).length > 3000) throw new HttpError(413, "太长了，请拆成每条不超过3000字");
  const timestamp = String(body.date || hongKongTime()).slice(0, 20);
  const tag = String(body.tag || "").slice(0, 30);
  const source = String(body.source || "ChatGPT").slice(0, 80);
  const parts = splitText(body.content);
  const requestKey = await digest(JSON.stringify({ content: body.content.trim(), date: body.date || "", tag, source }));
  const saved = [];
  for (const [index, part] of parts.entries()) {
    const content = `日期：${timestamp}\n${tag ? `标签：${tag}\n` : ""}${parts.length > 1 ? `（第${index + 1}/${parts.length}段）\n` : ""}内容：${part}`;
    const hash = await digest(content);
    const existing = await statement(env, "SELECT id FROM memories WHERE content_hash = ? AND kind = 'active' LIMIT 1", hash).first();
    saved.push(existing ? { id: existing.id, added: false } : await insertMemory(env,
      { content, timestamp, source, tag, kind: "active" }, `active:${requestKey}:${index}`));
  }
  const added = saved.filter(row => row.added);
  const indexingPending = saved.length ? await indexSaved(env, [...new Set(saved.map(row => row.id))]) : false;
  return { ok: true, added: added.length, skipped: saved.length - added.length,
    ids: added.map(row => row.id), saved_ids: saved.map(row => row.id), indexing_pending: indexingPending };
}

async function savePending(env, body) {
  if (typeof body.content !== "string" || !body.content.trim()) throw new HttpError(422, "记忆正文不能为空");
  const trigger = String(body.trigger || "来自MCP客户端").slice(0, 200);
  const result = await insertMemory(env, { content: body.content, timestamp: hongKongTime(),
    trigger, source: trigger, tag: "", kind: "pending" }, "pending:" + await digest(trigger + "\0" + body.content));
  return { ok: true, id: result.id, added: result.added ? 1 : 0 };
}

async function pendingList(env) {
  const items = await rows(env, "SELECT id, content, timestamp, trigger FROM memories WHERE kind = 'pending' ORDER BY created_at, id");
  return items.map(row => ({ ...row, status: "pending" }));
}

async function approve(env, id) {
  const row = await statement(env, "SELECT * FROM memories WHERE id = ? AND kind IN ('pending', 'active')", String(id)).first();
  if (!row) throw new HttpError(404, "找不到这条记忆");
  if (row.kind === "pending") await statement(env, `UPDATE memories SET kind = 'active', index_state = 'pending',
    dedupe_key = NULL, lease_until = 0, revision = revision + 1, updated_at = ? WHERE id = ? AND kind = 'pending'`, new Date().toISOString(), row.id).run();
  return { ok: true, indexing_pending: await indexSaved(env, [row.id]) };
}

async function remove(env, id, kind) {
  const result = await statement(env, `UPDATE memories SET kind = 'deleted', index_state = 'pending', dedupe_key = NULL,
    lease_until = 0, revision = revision + 1, updated_at = ? WHERE id = ? AND kind = ?`, new Date().toISOString(), String(id), kind).run();
  if (!result.meta.changes) throw new HttpError(404, "找不到这条记忆");
  await processQueue(env, [String(id)], 1);
  return { ok: true, removed: result.meta.changes };
}

async function approveAll(env) {
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  await env.DB.batch([
    statement(env, "INSERT INTO approval_jobs(id, total, created_at) SELECT ?, COUNT(*), ? FROM memories WHERE kind = 'pending'", id, now),
    statement(env, `UPDATE memories SET kind = 'active', index_state = 'pending', dedupe_key = NULL,
      lease_until = 0, revision = revision + 1, approval_batch = ?, updated_at = ? WHERE kind = 'pending'`, id, now),
  ]);
  const total = await statement(env, "SELECT total FROM approval_jobs WHERE id = ?", id).first("total");
  await processQueue(env, [], 12);
  return { ok: true, total };
}

async function refreshIndexState(env) {
  const info = await env.VECTORIZE.describe();
  if (info.processedUpToMutation) {
    const processedAt = info.processedUpToDatetime ? new Date(info.processedUpToDatetime).toISOString() : "";
    await statement(env, `UPDATE memories SET index_state = 'indexed' WHERE kind = 'active'
      AND index_state = 'submitted' AND (index_mutation = ? OR index_submitted_at <= ?)`,
    info.processedUpToMutation, processedAt).run();
  }
  return info;
}

async function status(env) {
  const info = await refreshIndexState(env);
  const counts = await statement(env, `SELECT
    SUM(kind = 'active') AS chronicle, SUM(kind = 'pending') AS pending,
    SUM(kind = 'active' AND index_state <> 'indexed') AS indexing_pending,
    SUM(kind = 'active' AND index_error IS NOT NULL) AS indexing_failed FROM memories`).first();
  const job = await statement(env, "SELECT * FROM approval_jobs ORDER BY created_at DESC LIMIT 1").first();
  const done = job ? await statement(env,
    "SELECT COUNT(*) AS n FROM memories WHERE approval_batch = ? AND (index_state = 'indexed' OR kind = 'deleted')", job.id).first("n") : 0;
  return { backend: "cloudflare", chronicle: counts.chronicle || 0, pending: counts.pending || 0,
    indexing_pending: counts.indexing_pending || 0, indexing_failed: counts.indexing_failed || 0,
    indexed_vectors: info.vectorCount,
    running: Boolean(job && done < job.total), done, failed: [], total: job?.total || 0 };
}

async function importBatch(env, body) {
  if (!Array.isArray(body.records) || body.records.length > 10) throw new HttpError(422, "每批最多导入10条");
  const ids = [];
  let added = 0;
  for (const record of body.records) {
    if (!record.id || new TextEncoder().encode(record.id).length > 64 || typeof record.content !== "string") {
      throw new HttpError(422, "导入记录缺少有效编号或正文");
    }
    if (!['active', 'pending'].includes(record.kind)) throw new HttpError(422, "导入状态无效");
    if (record.kind === "active" && !validVector(record.embedding)) throw new HttpError(422, "导入向量维度无效");
    const existing = await statement(env, "SELECT content, source, timestamp FROM memories WHERE id = ?", record.id).first();
    if (existing && (existing.content !== record.content || existing.source !== (record.source || "") || existing.timestamp !== (record.timestamp || ""))) {
      throw new HttpError(409, "记录编号已被不同内容占用");
    }
    const result = await insertMemory(env, record, null);
    added += Number(result.added);
    if (record.kind === "active") ids.push(record.id);
  }
  if (ids.length) await processQueue(env, ids, Math.min(100, ids.length));
  return { ok: true, added, skipped: body.records.length - added };
}

const MCP_TOOLS = [
  { name: "museum_search", description: "在Lumi和Claude的记忆博物馆里按意思搜索长期记忆。想回忆过去的事、确认她说过什么时用。query写关键词，不要带人名。", inputSchema: {
    type: "object", properties: { query: { type: "string", description: "要找的内容，用关键词或一句话" }, top_k: { type: "integer", description: "返回几条，默认5，最多10" } }, required: ["query"] } },
  { name: "museum_save", description: "把一条想记得的事情记下来，放进分享日记，Lumi有时候也会看。一两句话。", inputSchema: {
    type: "object", properties: { content: { type: "string", description: "想记住的内容" } }, required: ["content"] } },
];

async function mcp(env, message) {
  if (message.id === undefined) return null;
  const ok = result => ({ jsonrpc: "2.0", id: message.id, result });
  if (message.method === "initialize") return ok({ protocolVersion: message.params?.protocolVersion || "2025-03-26",
    capabilities: { tools: {} }, serverInfo: { name: "museum", version: "2.0.0" } });
  if (message.method === "ping") return ok({});
  if (message.method === "tools/list") return ok({ tools: MCP_TOOLS });
  if (message.method !== "tools/call") return { jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "不支持的方法" } };
  const args = message.params?.arguments || {};
  try {
    let text;
    if (message.params.name === "museum_search") {
      const result = await search(env, cleanQuery(args.query), args.top_k);
      text = result.results.length ? result.results.map((row, index) =>
        `#${index + 1} [相关度${row.score}] ${row.timestamp}\n${row.content}`).join("\n\n---\n\n") + "\n\n（以上是博物馆的全部结果）" : "博物馆已经翻过了：没有找到相关记忆。";
    } else if (message.params.name === "museum_save") {
      await savePending(env, { content: args.content, trigger: "来自MCP客户端" });
      text = "已放进分享日记，Lumi看过后会收进博物馆。";
    } else throw new HttpError(404, "没有这个工具");
    return ok({ content: [{ type: "text", text }] });
  } catch (error) {
    return ok({ content: [{ type: "text", text: "博物馆服务暂时出错：" + error.message }], isError: true });
  }
}

async function handle(request, env) {
  const url = new URL(request.url);
  const route = url.pathname;
  if (route === "/health") return Response.json({ ok: true, backend: "cloudflare" });
  const mcpPath = route.match(/^\/mcp(?:\/([^/]+))?$/);
  await authorize(request, env, mcpPath?.[1]);
  if (mcpPath) {
    if (request.method !== "POST") return new Response(null, { status: 405, headers: { Allow: "POST" } });
    const body = await request.json();
    const result = Array.isArray(body) ? (await Promise.all(body.map(item => mcp(env, item)))).filter(Boolean) : await mcp(env, body);
    return result === null || Array.isArray(result) && !result.length ? new Response(null, { status: 202 }) : Response.json(result);
  }
  if (request.method === "GET") {
    if (route === "/memory/search") return Response.json(await search(env, url.searchParams.get("q"), url.searchParams.get("top_k")));
    if (route === "/memory/status") return Response.json(await status(env));
    if (route === "/pending") return Response.json(await pendingList(env));
    if (route === "/memory/export") {
      const limit = Math.max(1, Math.min(100, Number(url.searchParams.get("limit")) || 50));
      const cursor = url.searchParams.get("cursor") || "";
      const records = await rows(env, "SELECT * FROM memories WHERE id > ? ORDER BY id LIMIT ?", cursor, limit);
      return Response.json({ records, next_cursor: records.length === limit ? records.at(-1).id : null });
    }
  }
  if (request.method === "POST") {
    const body = await request.json();
    if (route === "/memory/ingest") return Response.json(await ingest(env, body));
    if (route === "/memory/save") return Response.json(await savePending(env, body));
    if (route === "/pending/approve") return Response.json(await approve(env, body.id));
    if (route === "/memory/approve_all") return Response.json(await approveAll(env));
    if (route === "/memory/forget") return Response.json(await remove(env, body.id, "active"));
    if (route === "/pending/delete" || route === "/memory/delete_pending") return Response.json(await remove(env, body.id, "pending"));
    if (route === "/admin/import") return Response.json(await importBatch(env, body));
    if (route === "/admin/reindex") return Response.json(await processQueue(env));
    if (route === "/admin/embed") {
      if (!Array.isArray(body.texts) || body.texts.length > 16 || !body.texts.every(text => typeof text === "string")) throw new HttpError(422, "每批最多16段文字");
      return Response.json({ data: await embeddings(env, body.texts) });
    }
  }
  throw new HttpError(404, "没有这个接口");
}

export default {
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch (error) { return Response.json({ detail: error.status ? error.message : "记忆服务暂时不可用，已保存的数据保留在数据库中" }, { status: error.status || 502 }); }
  },
  async scheduled(controller, env) {
    await processQueue(env);
  },
};
