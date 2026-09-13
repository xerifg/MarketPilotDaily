CREATE TABLE daily_runs (
  id TEXT PRIMARY KEY,
  report_date TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('daily', 'test')),
  github_run_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('collecting', 'ready', 'failed')),
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  failure_code TEXT,
  UNIQUE(report_date, mode)
);
CREATE TABLE reports (
  id TEXT PRIMARY KEY REFERENCES daily_runs(id),
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE deliveries (
  report_id TEXT PRIMARY KEY REFERENCES reports(id),
  state TEXT NOT NULL CHECK (state IN ('sending', 'smtp_accepted', 'smtp_rejected', 'failed_before_data', 'delivery_uncertain')),
  error_code TEXT,
  updated_at TEXT NOT NULL
);
