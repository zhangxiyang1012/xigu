CREATE TABLE IF NOT EXISTS stocks (
  code char(6) PRIMARY KEY, name text NOT NULL, market varchar(16) NOT NULL,
  listed_date date, industry_code varchar(16), industry_name text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stocks_name_idx ON stocks USING gin (to_tsvector('simple', name));
CREATE INDEX IF NOT EXISTS stocks_market_idx ON stocks (market);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS industry_source varchar(24);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sw_l1_code varchar(12);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sw_l1_name varchar(48);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sw_l2_code varchar(12);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sw_l2_name varchar(48);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sw_l3_code varchar(12);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS sw_l3_name varchar(48);
CREATE TABLE IF NOT EXISTS daily_quotes (
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE, trade_date date NOT NULL,
  open numeric(16,4) NOT NULL, high numeric(16,4) NOT NULL, low numeric(16,4) NOT NULL, close numeric(16,4) NOT NULL,
  change_pct numeric(10,4) NOT NULL, volume bigint NOT NULL, amount numeric(22,2) NOT NULL, turnover numeric(10,4) NOT NULL,
  source varchar(32) NOT NULL DEFAULT 'eastmoney', updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (stock_code, trade_date)
) PARTITION BY RANGE (trade_date);
CREATE TABLE IF NOT EXISTS daily_quotes_default PARTITION OF daily_quotes DEFAULT;
CREATE INDEX IF NOT EXISTS daily_quotes_date_idx ON daily_quotes (trade_date DESC);
CREATE INDEX IF NOT EXISTS daily_quotes_stock_date_idx ON daily_quotes (stock_code, trade_date DESC);
CREATE TABLE IF NOT EXISTS sync_runs (
  id bigserial PRIMARY KEY, sync_type varchar(32) NOT NULL, trade_date date, status varchar(16) NOT NULL,
  rows_written integer NOT NULL DEFAULT 0, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, error text
);
CREATE TABLE IF NOT EXISTS stock_tags (
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
  tag_key varchar(48) NOT NULL,
  tag_name varchar(48) NOT NULL,
  category varchar(24) NOT NULL,
  direction varchar(8) NOT NULL DEFAULT 'neutral',
  value numeric(16,4),
  as_of date,
  source varchar(16) NOT NULL DEFAULT 'system',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (stock_code, tag_key)
);
CREATE INDEX IF NOT EXISTS stock_tags_key_idx ON stock_tags (tag_key, stock_code);
CREATE INDEX IF NOT EXISTS stock_tags_name_idx ON stock_tags (tag_name);
CREATE INDEX IF NOT EXISTS stock_tags_stock_idx ON stock_tags (stock_code);
CREATE TABLE IF NOT EXISTS industry_daily_metrics (
  industry_name text NOT NULL,
  trade_date date NOT NULL,
  member_count integer NOT NULL,
  avg_change_pct numeric(10,4) NOT NULL,
  industry_index numeric(16,4) NOT NULL,
  return_20d numeric(10,4) NOT NULL,
  amount numeric(24,2) NOT NULL,
  amount_ratio numeric(10,4) NOT NULL,
  above_ma20_pct numeric(10,4) NOT NULL,
  limit_up_count integer NOT NULL,
  up_count integer NOT NULL,
  down_count integer NOT NULL,
  rotation_score numeric(10,4) NOT NULL,
  phase varchar(16) NOT NULL,
  phase_days integer NOT NULL,
  risk_score numeric(10,4) NOT NULL,
  risk_level varchar(8) NOT NULL,
  risk_reasons text[] NOT NULL DEFAULT ARRAY[]::text[],
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (industry_name, trade_date)
);
CREATE INDEX IF NOT EXISTS industry_metrics_date_idx
  ON industry_daily_metrics (trade_date DESC, rotation_score DESC);
CREATE TABLE IF NOT EXISTS industry_leader_analysis (
  stock_code text PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
  industry_name text NOT NULL,
  strategy_role text NOT NULL,
  source_mentions jsonb NOT NULL DEFAULT '[]',
  correlation_90d double precision,
  direction_match_pct double precision,
  amplitude_ratio double precision,
  lead_lag_days integer,
  lead_lag_correlation double precision,
  turning_signal text,
  turning_date date,
  turning_reasons jsonb NOT NULL DEFAULT '[]',
  calculated_at timestamptz NOT NULL DEFAULT now()
);
