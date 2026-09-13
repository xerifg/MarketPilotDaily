ALTER TABLE portfolio_state ADD COLUMN cash_usd TEXT;
ALTER TABLE portfolio_state ADD COLUMN cash_usd_as_of TEXT;

CREATE TABLE positions_next (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  exchange TEXT NOT NULL CHECK (exchange IN ('SH', 'SZ', 'BJ', 'US')),
  asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'etf')),
  currency TEXT NOT NULL CHECK (
    (exchange = 'US' AND currency = 'USD') OR
    (exchange IN ('SH', 'SZ', 'BJ') AND currency = 'CNY')
  ),
  quantity TEXT NOT NULL CHECK (CAST(quantity AS REAL) > 0),
  average_cost TEXT CHECK (average_cost IS NULL OR CAST(average_cost AS REAL) >= 0),
  horizon TEXT CHECK (horizon IN ('today', 'short', 'long')),
  thesis TEXT NOT NULL DEFAULT '',
  verified_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
INSERT INTO positions_next SELECT * FROM positions;
DROP TABLE positions;
ALTER TABLE positions_next RENAME TO positions;
CREATE UNIQUE INDEX active_position_identity ON positions(symbol) WHERE deleted_at IS NULL;

CREATE TRIGGER positions_insert_revision AFTER INSERT ON positions BEGIN
  UPDATE portfolio_state SET revision = revision + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1;
END;
CREATE TRIGGER positions_update_revision AFTER UPDATE ON positions BEGIN
  UPDATE portfolio_state SET revision = revision + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1;
END;
