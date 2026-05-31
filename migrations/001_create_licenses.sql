CREATE TABLE IF NOT EXISTS licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    activated_at TEXT,
    expires_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    max_accounts INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    machine_id TEXT,
    hostname TEXT,
    os_fingerprint TEXT,
    machine_bound_at TEXT,
    revoked_at TEXT,
    last_validated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses (license_key);
CREATE INDEX IF NOT EXISTS idx_licenses_email ON licenses (email);
