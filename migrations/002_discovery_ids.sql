CREATE TABLE IF NOT EXISTS discovery_ids (
    source_job_id TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_run_id INTEGER NOT NULL,
    last_run_id INTEGER NOT NULL,
    FOREIGN KEY(first_run_id) REFERENCES runs(id),
    FOREIGN KEY(last_run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_discovery_last_seen ON discovery_ids(last_seen_at);
