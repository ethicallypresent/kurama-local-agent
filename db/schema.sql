-- Long-term memory store. core/memory.py creates this automatically via
-- executescript() on first run; this file is a readable reference copy,
-- not something the app reads at startup.
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  timestamp TEXT,
  role TEXT,
  kind TEXT,
  content TEXT,
  verified INTEGER,
  embedding TEXT,
  meta TEXT,
  status TEXT,
  embed_space TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id);

CREATE TABLE IF NOT EXISTS world_entities (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  attributes TEXT,
  source TEXT,
  verified INTEGER,
  first_seen TEXT,
  last_seen TEXT,
  mentions INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_world_kind_name ON world_entities(kind, name);