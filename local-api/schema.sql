CREATE TABLE IF NOT EXISTS stocks (
  code char(6) PRIMARY KEY, name text NOT NULL, market varchar(16) NOT NULL,
  listed_date date, industry_code varchar(16), industry_name text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stocks_name_idx ON stocks USING gin (to_tsvector('simple', name));
CREATE INDEX IF NOT EXISTS stocks_market_idx ON stocks (market);
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
