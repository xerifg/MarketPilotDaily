CREATE TABLE portfolio_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  revision INTEGER NOT NULL DEFAULT 0,
  cash TEXT,
  cash_as_of TEXT,
  updated_at TEXT NOT NULL
);
INSERT INTO portfolio_state (id, updated_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE TABLE investor_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  horizon TEXT CHECK (horizon IN ('today', 'short', 'long')),
  max_drawdown TEXT,
  max_position TEXT,
  email_paused INTEGER NOT NULL DEFAULT 1 CHECK (email_paused IN (0, 1))
);
INSERT INTO investor_profile (id) VALUES (1);

CREATE TABLE positions (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  exchange TEXT NOT NULL CHECK (exchange IN ('SH', 'SZ', 'BJ')),
  asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'etf')),
  currency TEXT NOT NULL CHECK (currency = 'CNY'),
  quantity TEXT NOT NULL CHECK (CAST(quantity AS REAL) > 0),
  average_cost TEXT CHECK (average_cost IS NULL OR CAST(average_cost AS REAL) >= 0),
  horizon TEXT CHECK (horizon IN ('today', 'short', 'long')),
  thesis TEXT NOT NULL DEFAULT '',
  verified_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE UNIQUE INDEX active_position_identity ON positions(symbol) WHERE deleted_at IS NULL;

CREATE TRIGGER positions_insert_revision AFTER INSERT ON positions BEGIN
  UPDATE portfolio_state SET revision = revision + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1;
END;
CREATE TRIGGER positions_update_revision AFTER UPDATE ON positions BEGIN
  UPDATE portfolio_state SET revision = revision + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1;
END;
CREATE TRIGGER profile_update_revision AFTER UPDATE ON investor_profile BEGIN
  UPDATE portfolio_state SET revision = revision + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1;
END;
