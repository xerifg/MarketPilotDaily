CREATE TABLE ai_calls (
  id TEXT PRIMARY KEY,
  month TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('reserved', 'settled', 'uncertain')),
  reserved_micros INTEGER NOT NULL CHECK (reserved_micros > 0),
  charged_micros INTEGER CHECK (charged_micros >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (state != 'settled' OR charged_micros IS NOT NULL)
);
CREATE INDEX ai_calls_by_month ON ai_calls(month);
