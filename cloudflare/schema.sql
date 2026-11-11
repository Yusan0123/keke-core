CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  tag TEXT NOT NULL DEFAULT '',
  trigger TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('active', 'pending', 'deleted')),
  content_hash TEXT NOT NULL,
  dedupe_key TEXT UNIQUE,
  embedding TEXT,
  original_json TEXT,
  index_state TEXT NOT NULL DEFAULT 'pending',
  index_mutation TEXT,
  index_submitted_at TEXT,
  index_error TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  lease_until INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  approval_batch TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_kind ON memories(kind, timestamp, id);
CREATE INDEX IF NOT EXISTS memories_content_hash ON memories(content_hash, kind);
CREATE INDEX IF NOT EXISTS memories_index_queue ON memories(index_state, lease_until, kind);
CREATE TABLE IF NOT EXISTS approval_jobs (
  id TEXT PRIMARY KEY,
  total INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
