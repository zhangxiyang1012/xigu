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
CREATE TABLE IF NOT EXISTS minute_quotes (
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
  trade_time timestamp NOT NULL,
  interval_minutes smallint NOT NULL DEFAULT 30,
  open numeric(16,4) NOT NULL, high numeric(16,4) NOT NULL,
  low numeric(16,4) NOT NULL, close numeric(16,4) NOT NULL,
  volume numeric(22,2) NOT NULL DEFAULT 0, amount numeric(22,2) NOT NULL DEFAULT 0,
  source varchar(32) NOT NULL DEFAULT 'baostock', updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(stock_code,trade_time,interval_minutes)
);
CREATE INDEX IF NOT EXISTS minute_quotes_time_idx ON minute_quotes(trade_time DESC);
CREATE TABLE IF NOT EXISTS backtest_priority_stocks (
  stock_code char(6) PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
  batch_name varchar(48) NOT NULL,
  target_trading_days integer NOT NULL DEFAULT 548,
  source varchar(32) NOT NULL DEFAULT 'wechat_screenshot',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS minute_backfill_coverage (
  batch_name varchar(48) NOT NULL,
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
  range_start date NOT NULL,
  range_end date NOT NULL,
  status varchar(16) NOT NULL,
  rows_written integer NOT NULL DEFAULT 0,
  completed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(batch_name,stock_code,range_start,range_end)
);
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
CREATE TABLE IF NOT EXISTS watchlist (
  stock_code char(6) PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS portfolio_positions (
  stock_code char(6) PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
  quantity numeric(18,2), cost_price numeric(16,4), note text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS stock_fundamentals (
  stock_code char(6) PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
  company_name text, main_business text, company_intro text,
  concepts jsonb NOT NULL DEFAULT '[]'::jsonb,
  report_date date, report_name varchar(32),
  revenue numeric(22,2), revenue_yoy numeric(12,4),
  net_profit numeric(22,2), net_profit_yoy numeric(12,4),
  gross_margin numeric(12,4), roe numeric(12,4),
  total_shares numeric(22,2), free_shares numeric(22,2),
  source varchar(32) NOT NULL DEFAULT 'eastmoney',
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS strategy_backtest_runs (
  id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  start_date date NOT NULL, end_date date NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'running', stock_count integer NOT NULL DEFAULT 0,
  event_count integer NOT NULL DEFAULT 0, trade_count integer NOT NULL DEFAULT 0,
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb, error text
);
CREATE TABLE IF NOT EXISTS strategy_backtest_events (
  id bigserial PRIMARY KEY, run_id bigint NOT NULL REFERENCES strategy_backtest_runs(id) ON DELETE CASCADE,
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
  side varchar(8) NOT NULL, signal_date date NOT NULL, execution_date date NOT NULL,
  execution_price numeric(16,4) NOT NULL, signal_level varchar(16) NOT NULL,
  strategy_name varchar(48) NOT NULL, matched_rules text[] NOT NULL DEFAULT ARRAY[]::text[],
  forward_returns jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id,stock_code,side,signal_date)
);
ALTER TABLE strategy_backtest_events ADD COLUMN IF NOT EXISTS execution_time timestamp;
ALTER TABLE strategy_backtest_events ADD COLUMN IF NOT EXISTS execution_mode varchar(24) NOT NULL DEFAULT 'daily_next_open';
CREATE INDEX IF NOT EXISTS backtest_events_run_side_idx ON strategy_backtest_events(run_id,side,signal_date);
CREATE TABLE IF NOT EXISTS strategy_backtest_trades (
  id bigserial PRIMARY KEY, run_id bigint NOT NULL REFERENCES strategy_backtest_runs(id) ON DELETE CASCADE,
  stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
  buy_signal_date date NOT NULL, buy_date date NOT NULL, buy_price numeric(16,4) NOT NULL,
  buy_strategy varchar(48) NOT NULL, buy_rules text[] NOT NULL DEFAULT ARRAY[]::text[],
  sell_signal_date date, sell_date date, sell_price numeric(16,4),
  sell_strategy varchar(48), sell_rules text[] NOT NULL DEFAULT ARRAY[]::text[],
  holding_days integer NOT NULL DEFAULT 0, return_pct numeric(12,4), status varchar(12) NOT NULL
);
ALTER TABLE strategy_backtest_trades ADD COLUMN IF NOT EXISTS buy_time timestamp;
ALTER TABLE strategy_backtest_trades ADD COLUMN IF NOT EXISTS sell_time timestamp;
ALTER TABLE strategy_backtest_trades ADD COLUMN IF NOT EXISTS execution_mode varchar(24) NOT NULL DEFAULT 'daily_next_open';
CREATE TABLE IF NOT EXISTS strategy_backtest_summaries (
  run_id bigint NOT NULL REFERENCES strategy_backtest_runs(id) ON DELETE CASCADE,
  side varchar(12) NOT NULL, horizon varchar(8) NOT NULL, trading_days integer NOT NULL,
  sample_count integer NOT NULL, avg_return_pct numeric(12,4), median_return_pct numeric(12,4),
  win_rate_pct numeric(10,4), best_return_pct numeric(12,4), worst_return_pct numeric(12,4),
  PRIMARY KEY(run_id,side,horizon)
);
INSERT INTO discipline_rules(rule_key,rule_name,side,category,priority,description,parameters) VALUES
('B0_EXCLUSION','B0 基础排除','buy','gate',100,'ST/退市整理、上市不足120日、近20日均成交额低于1亿元、连续一字板、放量破位未止跌、行业退潮或系统性风险时不开新仓。','{}'::jsonb),
('B1_MARKET','B1 市场环境门槛','buy','gate',95,'市场不得处于系统性风险，主线核心股不可批量破位；大级别下降期只允许小仓位反弹策略。','{}'::jsonb),
('B2_INDUSTRY','B2 行业门槛','buy','gate',90,'行业综合强度前10名可正常参与；第11-20名仅在行业非高风险且个股技术结构满足时条件参与；20名以后不作标准买入确认。','{"preferredMaxRank":10,"conditionalMaxRank":20,"rejectHighRisk":true}'::jsonb),
('B3_CORE','B3 核心个股门槛','buy','selection',85,'优先行业核心股：相对行业强、MA20向上、站上MA20，且MA5≥MA10≥MA20或MA5上穿MA10。','{}'::jsonb),
('B4A_PULLBACK','B4-A 主线强势股缩量回调','buy','entry',80,'明确上升趋势中回调2-5日、量能递减至20日均量70%以下、振幅≤5%、不破MA20；至少两个拐点条件确认后才买入。','{"pullbackDays":[2,5],"maxVolumeRatio20":0.7,"maxAmplitudePct":5}'::jsonb),
('B4B_RECOVERY','B4-B 主线龙头深跌修复','buy','entry',75,'仅限历史验证的行业核心股；高点回撤15%-30%，出现止跌结构，行业未系统性下降，重新站上MA5且量能恢复。','{"drawdownMin":-30,"drawdownMax":-15}'::jsonb),
('B4C_ACTIVE','B4-C 活跃股二次参与','buy','entry',70,'近期出现可成交放量涨停或连续两根以上放量中长阳；等待5-10日缩量调整、支撑确认和再次转强。','{"limitPct":9.8,"cycleDays":[5,10]}'::jsonb),
('B5_NO_CHASE','B5 禁止追高','buy','risk',65,'上涨5-10日、反弹周期后段放量、高位巨量滞涨、长上影或破位未收复时禁止开新仓。','{}'::jsonb),
('B6_POSITION','B6 仓位纪律','buy','risk',60,'单笔风险0.5%-1%，单股≤15%、激进策略≤8%、单行业≤35%；无法预先定义防守位不得买入。','{}'::jsonb),
('S0_HARD_STOP','S0 硬止损','sell','exit',100,'跌破预设防守位、放量跌破MA20/平台、MA20次日未收回、浮亏达到1-1.5ATR或活跃股破位时退出。','{"atrStop":[1,1.5]}'::jsonb),
('S1_TREND','S1 趋势减仓与退出','sell','exit',90,'放量跌破MA10先减半；放量跌破MA20、反弹无量再破前低或破位反弹未收复时退出。','{}'::jsonb),
('S2_INDUSTRY','S2 行业与市场退出','sell','risk',80,'行业跌出前3并连续3日走弱、多只核心股同时破位或风险向市场扩散时，减仓或退出。','{"weakDays":3}'::jsonb),
('S3_PROFIT','S3 止盈纪律','sell','profit',70,'盈利8%-12%先兑现约1/3；剩余仓位沿MA5/MA10移动止盈，高位放量滞涨、长上影或顶背离时分批退出。','{"profitPct":[8,12]}'::jsonb),
('S4_TIME','S4 时间止损','sell','time',60,'买入超过10个交易日仍未走强，或预期的随后转强未出现时，减仓或退出并取消原交易逻辑。','{"maxDays":10}'::jsonb)
ON CONFLICT(rule_key) DO UPDATE SET rule_name=excluded.rule_name,side=excluded.side,
category=excluded.category,priority=excluded.priority,description=excluded.description,
parameters=excluded.parameters,updated_at=now();
UPDATE discipline_rules SET enabled = rule_key IN (
  'B0_EXCLUSION','B1_MARKET','B2_INDUSTRY','B3_CORE','B4A_PULLBACK','B4B_RECOVERY','B4C_ACTIVE',
  'B5_NO_CHASE','B6_POSITION','S0_HARD_STOP','S1_TREND','S2_INDUSTRY','S3_PROFIT','S4_TIME'
);
