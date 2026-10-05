CREATE TABLE report_shares (
  report_id TEXT PRIMARY KEY REFERENCES reports(id),
  token TEXT NOT NULL UNIQUE,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);
