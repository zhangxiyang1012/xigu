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
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name_pinyin varchar(160);
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name_initials varchar(48);
CREATE INDEX IF NOT EXISTS stocks_name_pinyin_idx ON stocks (name_pinyin);
CREATE INDEX IF NOT EXISTS stocks_name_initials_idx ON stocks (name_initials);
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
CREATE TABLE IF NOT EXISTS discipline_rules (
  rule_key varchar(48) PRIMARY KEY,
  rule_name varchar(80) NOT NULL,
  side varchar(12) NOT NULL,
  category varchar(24) NOT NULL,
  priority integer NOT NULL DEFAULT 50,
  description text NOT NULL,
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS stock_discipline_signals (
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
  trade_date date NOT NULL,
  close numeric(16,4) NOT NULL,
  ma5 numeric(16,4), ma10 numeric(16,4), ma20 numeric(16,4), atr14 numeric(16,4),
  volume_ratio_5 numeric(10,4), volume_ratio_20 numeric(10,4),
  drawdown_20d numeric(10,4), drawdown_60d numeric(10,4),
  pullback_days integer NOT NULL DEFAULT 0,
  industry_rank integer, industry_rotation_score numeric(10,4),
  industry_risk_level varchar(8),
  buy_score numeric(10,2) NOT NULL DEFAULT 0,
  sell_score numeric(10,2) NOT NULL DEFAULT 0,
  buy_level varchar(16) NOT NULL,
  sell_level varchar(16) NOT NULL,
  buy_model varchar(32) NOT NULL DEFAULT '无',
  buy_signals text[] NOT NULL DEFAULT ARRAY[]::text[],
  sell_signals text[] NOT NULL DEFAULT ARRAY[]::text[],
  blockers text[] NOT NULL DEFAULT ARRAY[]::text[],
  defense_price numeric(16,4), stop_atr_price numeric(16,4),
  calculated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS discipline_signal_latest_idx
  ON stock_discipline_signals (trade_date DESC, buy_score DESC, sell_score DESC);
CREATE INDEX IF NOT EXISTS discipline_signal_stock_idx
  ON stock_discipline_signals (stock_code, trade_date DESC);
INSERT INTO discipline_rules(rule_key,rule_name,side,category,priority,description,parameters) VALUES
('B_MAIN_PULLBACK','主线缩量回调','buy','entry',90,'行业非高风险、股价位于MA20上方，连续2至5日缩量回调并出现拐点确认。','{"pullbackDays":[2,5],"maxVolumeRatio20":0.8,"requiresMA20":true}'::jsonb),
('B_ACTIVE_SECOND','活跃股二次参与','buy','entry',80,'近20日出现涨停或连续放量长阳，回调后重新站上MA5。','{"limitPct":9.8,"longBarPct":4,"lookback":20}'::jsonb),
('B_LEADER_RECOVERY','核心股深跌修复','buy','entry',70,'60日高点回撤15%至30%，重新站上MA5且量能恢复。','{"drawdownMin":-30,"drawdownMax":-15}'::jsonb),
('X_INDUSTRY_HIGH_RISK','行业高风险禁买','block','risk',100,'行业轮动风险为高时，禁止生成买入确认。','{}'::jsonb),
('S_BREAK_MA10','跌破MA10减仓','sell','exit',70,'收盘跌破MA10且成交量不萎缩，进入减仓预警。','{"minVolumeRatio20":1}'::jsonb),
('S_BREAK_MA20','放量跌破MA20退出','sell','exit',100,'收盘跌破MA20且成交量高于20日均量，触发退出信号。','{"minVolumeRatio20":1}'::jsonb),
('S_LIMIT_DOWN','跌停风险退出','sell','exit',100,'当日跌幅小于等于-9.8%，触发高优先级退出。','{"limitPct":-9.8}'::jsonb)
ON CONFLICT(rule_key) DO UPDATE SET rule_name=excluded.rule_name,side=excluded.side,
category=excluded.category,priority=excluded.priority,description=excluded.description,
parameters=excluded.parameters,updated_at=now();
