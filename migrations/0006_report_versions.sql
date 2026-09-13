CREATE TABLE daily_runs_v2 (
  id TEXT PRIMARY KEY,
  report_date TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('daily', 'test')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version BETWEEN 1 AND 3),
  github_run_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('collecting', 'ready', 'failed')),
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  failure_code TEXT,
  UNIQUE(report_date, mode, version)
);
CREATE TABLE reports_v2 (
  id TEXT PRIMARY KEY REFERENCES daily_runs_v2(id),
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE deliveries_v2 (
  report_id TEXT PRIMARY KEY REFERENCES reports_v2(id),
  state TEXT NOT NULL CHECK (state IN ('sending', 'smtp_accepted', 'smtp_rejected', 'failed_before_data', 'delivery_uncertain')),
  error_code TEXT,
  updated_at TEXT NOT NULL
);
INSERT INTO daily_runs_v2 SELECT id, report_date, mode, 1, github_run_id, state, snapshot_json, created_at, failure_code FROM daily_runs;
INSERT INTO reports_v2 SELECT * FROM reports;
INSERT INTO deliveries_v2 SELECT * FROM deliveries;
DROP TABLE deliveries;
DROP TABLE reports;
DROP TABLE daily_runs;
ALTER TABLE daily_runs_v2 RENAME TO daily_runs;
ALTER TABLE reports_v2 RENAME TO reports;
ALTER TABLE deliveries_v2 RENAME TO deliveries;
