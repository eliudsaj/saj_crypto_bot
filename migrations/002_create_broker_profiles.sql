CREATE TABLE IF NOT EXISTS broker_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    broker_type TEXT NOT NULL,
    account TEXT,
    server TEXT,
    password TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    is_disabled INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_broker_profiles_active
ON broker_profiles(is_active, is_disabled);
