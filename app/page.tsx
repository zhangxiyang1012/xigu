"use client";
import { useEffect, useMemo, useRef, useState } from "react";

type Stock = {
  code: string;
  name: string;
  market: string;
  price: number;
  change: number;
  volume: number;
  amount: number;
  industry_name?: string;
  industry_phase?: string;
  industry_risk?: string;
  industry_risk_score?: number;
  tags?: string[];
  tag_keys?: string[];
};
type Tag = {
  key: string;
  name: string;
  category: string;
  direction: "up" | "down" | "neutral";
  stock_count: number;
};
type Row = {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
  change: number;
  turnover: number;
  ma5?: number;
  ma10?: number;
  ma20?: number;
  dif?: number;
};
type Signal = { type: "底背离" | "顶背离"; date: string; index: number };
type Industry = {
  name: string;
  date: string;
  index: number;
  avg_change_pct: number;
  return_20d: number;
  amount: number;
  amount_ratio: number;
  above_ma20_pct: number;
  limit_up_count: number;
  up_count: number;
  down_count: number;
  rotation_score: number;
  phase: string;
  phase_days: number;
  risk_score: number;
  risk_level: string;
  risk_reasons: string[];
  history: {date:string;index:number;score:number;phase:string;risk:number;riskLevel:string;breadth:number}[];
};
type IndustryLeader = {
  stock_code:string; name:string; industry_name:string; strategy_role:string;
  source_mentions:string[]; correlation_90d:number; direction_match_pct:number;
  amplitude_ratio:number; lead_lag_days:number; lead_lag_correlation:number;
  turning_signal:string; turning_date?:string; turning_reasons:string[];
};
type RadarSignal = Stock & {
  trade_date:string; buy_score:number; sell_score:number; buy_level:string; sell_level:string;
  buy_model:string; buy_signals:string[]; sell_signals:string[]; blockers:string[];
  defense_price:number; stop_atr_price:number; volume_ratio_20:number; pullback_days:number;
};
type DisciplineRule = {rule_key:string;rule_name:string;side:string;category:string;priority:number;description:string};
const reasonText = (value: unknown) =>
  Array.isArray(value) ? value.join("；") : typeof value === "string" ? value : "";
const fallback: Stock[] = [
  {
    code: "600519",
    name: "贵州茅台",
    market: "沪市",
    price: 0,
    change: 0,
    volume: 0,
    amount: 0,
  },
  {
    code: "300750",
    name: "宁德时代",
    market: "创业板",
    price: 0,
    change: 0,
    volume: 0,
    amount: 0,
  },
];
const API_BASE=process.env.NEXT_PUBLIC_API_BASE??"";
const money = (n: number) =>
  n >= 1e8
    ? `${(n / 1e8).toFixed(1)}亿`
    : n >= 1e4
      ? `${(n / 1e4).toFixed(1)}万`
    : String(n || "—");
function trendStreak(prices:number[]){let best={direction:"平",days:0};for(let n=1;n<Math.min(30,prices.length);n++){const part=prices.slice(-n-1),direction=part.at(-1)!>=part[0]?"上涨":"下跌";let opposite=0;for(let i=1;i<part.length;i++){const d=part[i]-part[i-1];if((direction==="上涨"&&d<0)||(direction==="下跌"&&d>0))opposite++}if(opposite<=1)best={direction,days:n}}return best}
function strictTrend(rows:Row[]){
  if(!rows.length)return {direction:"平",days:0};
  const direction=rows.at(-1)!.change>0?"上涨":rows.at(-1)!.change<0?"下跌":"平";
  let days=0;
  for(let index=rows.length-1;index>=0;index--){
    const change=rows[index].change;
    if((direction==="上涨"&&change>0)||(direction==="下跌"&&change<0))days++;
    else break;
  }
  return {direction,days};
}
function trendStatus(rows:Row[]){
  const current=trendStreak(rows.map(row=>row.close));
  if(!rows.length)return {...current,label:"暂无趋势"};
  const currentStrict=strictTrend(rows);
  const segmentStart=rows.length-currentStrict.days;
  const firstChange=rows[segmentStart]?.change??0;
  const limitDirection=firstChange>9.8?"涨停":firstChange<-9.8?"跌停":"";
  if(limitDirection){
    let limitDays=0;
    for(let index=segmentStart;index<rows.length;index++){
      const change=rows[index].change;
      if((limitDirection==="涨停"&&change>9.8)||(limitDirection==="跌停"&&change<-9.8))limitDays++;
      else break;
    }
    const previous=strictTrend(rows.slice(0,segmentStart));
    const reversed=(limitDirection==="跌停"&&previous.direction==="上涨")||(limitDirection==="涨停"&&previous.direction==="下跌");
    if(reversed&&previous.days>=2){
      const continuation=currentStrict.days>limitDays?`，转${limitDirection==="跌停"?"跌":"涨"}第${currentStrict.days}天`:"";
      return {direction:limitDirection==="跌停"?"下跌":"上涨",days:currentStrict.days,label:`容错连${previous.direction==="上涨"?"涨":"跌"}${previous.days}天，${limitDirection}第${limitDays}天${continuation}`};
    }
  }
  return {...current,label:`容错连${current.direction==="下跌"?"跌":"涨"}第 ${current.days} 天`};
}
function ema(a: number[], n: number) {
  const k = 2 / (n + 1);
  return a
    .map((v, i) => (i ? v * k + (a[i - 1] ?? v) * (1 - k) : v))
    .reduce<number[]>((o, v, i) => {
      o[i] = i ? v * k + o[i - 1] * (1 - k) : v;
      return o;
    }, []);
}
function analyze(input: Row[]) {
  const rows = input.map((r, i, a) => ({
    ...r,
    ma5:
      a.slice(Math.max(0, i - 4), i + 1).reduce((s, x) => s + x.close, 0) /
      Math.min(5, i + 1),
    ma10:
      a.slice(Math.max(0, i - 9), i + 1).reduce((s, x) => s + x.close, 0) /
      Math.min(10, i + 1),
    ma20:
      a.slice(Math.max(0, i - 19), i + 1).reduce((s, x) => s + x.close, 0) /
      Math.min(20, i + 1),
  }));
  const closes = rows.map((r) => r.close),
    e12 = ema(closes, 12),
    e26 = ema(closes, 26);
  rows.forEach((r, i) => (r.dif = e12[i] - e26[i]));
  const pivots: { i: number; low: boolean }[] = [];
  for (let i = 3; i < rows.length - 3; i++) {
    const win = rows.slice(i - 3, i + 4);
    if (rows[i].low === Math.min(...win.map((x) => x.low)))
      pivots.push({ i, low: true });
    if (rows[i].high === Math.max(...win.map((x) => x.high)))
      pivots.push({ i, low: false });
  }
  const signals: Signal[] = [];
  for (let j = 1; j < pivots.length; j++) {
    const a = pivots[j - 1],
      b = pivots[j];
    if (a.low !== b.low || b.i - a.i < 5) continue;
    if (
      b.low &&
      rows[b.i].low < rows[a.i].low &&
      (rows[b.i].dif ?? 0) > (rows[a.i].dif ?? 0)
    )
      signals.push({ type: "底背离", date: rows[b.i].date, index: b.i });
    if (
      !b.low &&
      rows[b.i].high > rows[a.i].high &&
      (rows[b.i].dif ?? 0) < (rows[a.i].dif ?? 0)
    )
      signals.push({ type: "顶背离", date: rows[b.i].date, index: b.i });
  }
  return { rows, signals };
}
function Chart({
  rows,
  days,
  lines,
  signals,
}: {
  rows: Row[];
  days: number;
  lines: Record<string, boolean>;
  signals: Signal[];
}) {
  const ref = useRef<HTMLCanvasElement>(null),
    [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(
      null,
    );
  const view = rows.slice(-days);
  useEffect(() => {
    const el = ref.current;
    if (!el || !view.length) return;
    const box = el.getBoundingClientRect(),
      dpr = devicePixelRatio || 1;
    el.width = box.width * dpr;
    el.height = box.height * dpr;
    const c = el.getContext("2d")!;
    c.scale(dpr, dpr);
    const w = box.width,
      h = box.height,
      L = 24,
      R = 58,
      T = 22,
      B = 30,
      V = 68,
      offset = rows.length - view.length,
      vals = view.flatMap((d) => [d.close, d.ma5!, d.ma10!, d.ma20!]),
      min = Math.min(...vals) * 0.985,
      max = Math.max(...vals) * 1.015,
      x = (i: number) => L + (i / Math.max(1, view.length - 1)) * (w - L - R),
      y = (v: number) => T + ((max - v) / (max - min)) * (h - T - B - V);
    c.strokeStyle = "#e8e9e3";
    c.fillStyle = "#8b8c84";
    c.font = "11px Arial";
    for (let i = 0; i < 5; i++) {
      const yy = T + (i * (h - T - B - V)) / 4;
      c.beginPath();
      c.moveTo(L, yy);
      c.lineTo(w - R, yy);
      c.stroke();
      c.fillText((max - (i * (max - min)) / 4).toFixed(2), w - R + 7, yy + 4);
    }
    const draw = (
      k: "close" | "ma5" | "ma10" | "ma20",
      color: string,
      width = 1.5,
    ) => {
      if (k !== "close" && !lines[k]) return;
      c.beginPath();
      view.forEach((d, i) =>
        i ? c.lineTo(x(i), y(d[k]!)) : c.moveTo(x(i), y(d[k]!)),
      );
      c.strokeStyle = color;
      c.lineWidth = width;
      c.stroke();
    };
    draw("close", "#242923", 2.2);
    draw("ma5", "#e8943a");
    draw("ma10", "#3972b8");
    draw("ma20", "#8f63b7");
    const maxV = Math.max(...view.map((d) => d.volume));
    view.forEach((d, i) => {
      const bh = (d.volume / maxV) * (V - 14);
      c.fillStyle =
        i && d.close >= view[i - 1].close
          ? "rgba(195,63,58,.48)"
          : "rgba(38,139,112,.45)";
      c.fillRect(x(i) - 1.5, h - B - bh, 3, bh);
    });
    signals
      .filter((s) => s.index >= offset)
      .forEach((s) => {
        const i = s.index - offset,
          yy = y(view[i].close);
        c.fillStyle = s.type === "底背离" ? "#168265" : "#c44d47";
        c.beginPath();
        c.arc(x(i), yy, 4, 0, Math.PI * 2);
        c.fill();
        c.fillText(s.type, x(i) + 7, yy - 7);
      });
    if (hover) {
      const xx = x(hover.i),
        yy = y(view[hover.i].close);
      c.setLineDash([4, 4]);
      c.strokeStyle = "#6c746d";
      c.beginPath();
      c.moveTo(xx, T);
      c.lineTo(xx, h - B);
      c.moveTo(L, yy);
      c.lineTo(w - R, yy);
      c.stroke();
      c.setLineDash([]);
      c.fillStyle = "#25332b";
      c.beginPath();
      c.arc(xx, yy, 4, 0, Math.PI * 2);
      c.fill();
    }
  }, [rows, days, lines, signals, hover]);
  const move = (clientX: number, clientY: number) => {
    const box = ref.current!.getBoundingClientRect(),
      L = 24,
      R = 58,
      i = Math.max(
        0,
        Math.min(
          view.length - 1,
          Math.round(
            ((clientX - box.left - L) / (box.width - L - R)) *
              (view.length - 1),
          ),
        ),
      );
    setHover({ i, x: clientX - box.left, y: clientY - box.top });
  };
  const d = hover ? view[hover.i] : null;
  return (
    <div className="chart-wrap">
      <canvas
        className="chart"
        ref={ref}
        onMouseMove={(e) => move(e.clientX, e.clientY)}
        onMouseLeave={() => setHover(null)}
        onTouchMove={(e) => {
          const t = e.touches[0];
          move(t.clientX, t.clientY);
        }}
        onTouchEnd={() => setHover(null)}
      />
      {d && (
        <div
          className={`chart-tooltip ${hover!.x > 500 ? "left" : ""}`}
          style={{ top: Math.max(8, Math.min(205, hover!.y - 45)) }}
        >
          <b>{d.date}</b>
          <div>
            <span>开盘</span>
            {d.open.toFixed(2)}
            <span>最高</span>
            {d.high.toFixed(2)}
          </div>
          <div>
            <span>收盘</span>
            {d.close.toFixed(2)}
            <span>最低</span>
            {d.low.toFixed(2)}
          </div>
          <div>
            <span>涨跌</span>
            <em className={d.change < 0 ? "trend-down" : "trend-up"}>
              {d.change > 0 ? "+" : ""}
              {d.change.toFixed(2)}%
            </em>
            <span>成交量</span>
            {money(d.volume)}
          </div>
          <div>
            <span>MA5</span>
            {d.ma5?.toFixed(2)}
            <span>MA10</span>
            {d.ma10?.toFixed(2)}
          </div>
          <div>
            <span>MA20</span>
            {d.ma20?.toFixed(2)}
            <span>成交额</span>
            {money(d.amount)}
          </div>
        </div>
      )}
    </div>
  );
}

function IndustryRotationMap({
  industries,
  selected,
  onToggle,
}: {
  industries: Industry[];
  selected: string[];
  onToggle: (name: string) => void;
}) {
  const leaders = selected
    .map((name) => industries.find((item) => item.name === name))
    .filter((item): item is Industry => Boolean(item?.history?.length));
  const dates = Array.from(
    new Set(leaders.flatMap((item) => item.history.map((point) => point.date))),
  )
    .sort()
    .slice(-90);
  const color = (score: number, risk: number) => {
    if (risk >= 65) return "#7f3430";
    if (score >= 80) return "#df5149";
    if (score >= 65) return "#ed8179";
    if (score >= 50) return "#efd2ce";
    if (score >= 35) return "#d9e5df";
    if (score >= 20) return "#8fc5ae";
    return "#3f9273";
  };
  if (!leaders.length || !dates.length)
    return <div className="rotation-empty">请从顶部行业筛选器选择行业，查看90日轮动节奏</div>;
  return (
    <div className="rotation-map">
      <div className="rotation-map-head">
        <div>
          <b>过去90个交易日行业轮动图</b>
          <span>每格代表一个交易日，越红表示轮动越强，深色边框表示高风险</span>
        </div>
        <div className="rotation-legend">
          <span><i className="weak" />弱势</span>
          <span><i className="neutral" />中性</span>
          <span><i className="strong" />强势</span>
          <span><i className="danger" />高风险</span>
        </div>
      </div>
      <div className="rotation-scroll">
        <div
          className="rotation-table"
          style={{ gridTemplateColumns: `106px repeat(${dates.length}, 10px)` }}
        >
          <div className="rotation-corner">行业 / 日期</div>
          {dates.map((date, index) => (
            <span
              className="rotation-date"
              key={date}
              title={date}
            >
              {index % 15 === 0 ? date.slice(5) : ""}
            </span>
          ))}
          {leaders.map((item) => {
            const points = new Map(item.history.map((point) => [point.date, point]));
            return [
              <button
                className={`rotation-name ${selected.includes(item.name) ? "active" : ""}`}
                key={`${item.name}-name`}
                onClick={() => onToggle(item.name)}
              >
                {item.name}
              </button>,
              ...dates.map((date) => {
                const point = points.get(date);
                return (
                  <button
                    className={`rotation-cell ${selected.includes(item.name) ? "active" : ""}`}
                    key={`${item.name}-${date}`}
                    style={{
                      background: point ? color(point.score, point.risk) : "#f1f1ee",
                      outline: point?.risk >= 65 ? "1px solid #6f241f" : "none",
                    }}
                    title={
                      point
                        ? `${item.name} ${date}\n轮动强度 ${point.score.toFixed(0)} · ${point.phase}\n风险 ${point.riskLevel} ${point.risk.toFixed(0)} · MA20上方 ${point.breadth.toFixed(0)}%`
                        : `${item.name} ${date} 暂无数据`
                    }
                    onClick={() => onToggle(item.name)}
                    aria-label={`${item.name} ${date}`}
                  />
                );
              }),
            ];
          })}
        </div>
      </div>
      <div className="rotation-axis">
        <span>{dates[0]}</span>
        <span>← 行业强弱随时间迁移 →</span>
        <span>{dates.at(-1)}</span>
      </div>
    </div>
  );
}

const industryLineColors = ["#d94841","#326eaf","#8256b3","#df8b2f","#168265","#b24d79"];
function IndustryGlobalFilter({
  industries,
  selected,
  onToggle,
  onClear,
}: {
  industries: Industry[];
  selected: string[];
  onToggle: (name:string)=>void;
  onClear: ()=>void;
}) {
  return <div className="industry-global-filter">
    <div className="global-filter-head">
      <div><b>全局行业筛选</b><span>热力图、轮动曲线、行业状态与风险明细同步筛选 · 最多6个行业</span></div>
      <div className="compare-actions">
        <select aria-label="全局选择行业" value="" onChange={event=>event.target.value&&onToggle(event.target.value)}>
          <option value="">＋ 选择行业</option>
          {industries.filter(item=>!selected.includes(item.name)).sort((a,b)=>a.name.localeCompare(b.name,"zh-CN")).map(item=><option key={item.name} value={item.name}>{item.name}</option>)}
        </select>
        <button onClick={onClear} disabled={!selected.length}>清空选择</button>
      </div>
    </div>
    <div className="compare-chips">
      {selected.map((name,index)=><button key={name} onClick={()=>onToggle(name)}><i style={{background:industryLineColors[index]}}/>{name}<span>×</span></button>)}
      {!selected.length&&<span className="filter-placeholder">尚未选择行业</span>}
    </div>
  </div>;
}

function IndustryRotationCompare({
  industries,
  selected,
  onRemove,
}: {
  industries: Industry[];
  selected: string[];
  onRemove: (name:string)=>void;
}) {
  const rows=selected.map(name=>industries.find(item=>item.name===name)).filter(Boolean) as Industry[];
  if(!rows.length) return <div className="compare-empty">点击上方行业名称或色块，选择一个或多个行业查看轮动明细</div>;
  const lines=rows.map((item,index)=>{
    const history=item.history.slice(-90),base=history[0]?.index||1;
    const normalized=history.map(point=>({...point,value:point.index/base*100}));
    const values=normalized.map(point=>point.value);
    return {item,index,normalized,min:Math.min(...values),max:Math.max(...values)};
  });
  const globalMin=Math.min(...lines.map(line=>line.min)),globalMax=Math.max(...lines.map(line=>line.max));
  return <div className="rotation-compare">
    <div className="compare-head"><div><b>已选行业轮动明细</b><span>指数统一归一化为100</span></div></div>
    <svg viewBox="0 0 900 180" preserveAspectRatio="none" aria-label="多行业90日轮动对比">
      {[0,1,2,3].map(n=><line key={n} x1="0" x2="900" y1={20+n*45} y2={20+n*45}/>)}
      {lines.map(line=><polyline key={line.item.name} style={{stroke:industryLineColors[line.index]}} points={line.normalized.map((point,i)=>`${i*900/Math.max(1,line.normalized.length-1)},${165-(point.value-globalMin)*145/Math.max(.01,globalMax-globalMin)}`).join(" ")}/>)}
    </svg>
    <div className="compare-table">
      <div className="compare-row compare-labels"><b>行业</b><span>当前阶段</span><span>轮动强度</span><span>20日涨跌</span><span>MA20广度</span><span>风险</span></div>
      {lines.map(line=><button className="compare-row" key={line.item.name} onClick={()=>onRemove(line.item.name)}><b><i style={{background:industryLineColors[line.index]}}/>{line.item.name}</b><span>{line.item.phase}第{line.item.phase_days}天</span><span>{line.item.rotation_score.toFixed(0)}</span><span className={line.item.return_20d<0?"trend-down":"trend-up"}>{line.item.return_20d>0?"+":""}{line.item.return_20d.toFixed(1)}%</span><span>{line.item.above_ma20_pct.toFixed(0)}%</span><span className={`risk-${line.item.risk_level}`}>{line.item.risk_level} {line.item.risk_score.toFixed(0)}</span></button>)}
    </div>
  </div>
}

export default function Home() {
  const [stocks, setStocks] = useState<Stock[]>(fallback),
    [searchResults,setSearchResults]=useState<Stock[]>([]),
    [searching,setSearching]=useState(false),
    [availableTags,setAvailableTags]=useState<Tag[]>([]),
    [selectedTags,setSelectedTags]=useState<string[]>([]),
    [selected, setSelected] = useState<Stock>(fallback[0]),
    [raw, setRaw] = useState<Row[]>([]),
    [industries, setIndustries] = useState<Industry[]>([]),
    [industryLeaders,setIndustryLeaders]=useState<IndustryLeader[]>([]),
    [selectedIndustry, setSelectedIndustry] = useState(""),
    [selectedIndustries, setSelectedIndustries] = useState<string[]>([]),
    [activeView,setActiveView]=useState<"market"|"industry"|"radar">("market"),
    [radarSide,setRadarSide]=useState<"all"|"buy"|"sell">("all"),
    [radarSignals,setRadarSignals]=useState<RadarSignal[]>([]),
    [radarSummary,setRadarSummary]=useState({buy_confirmed:0,candidates:0,reduce:0,exit:0}),
    [disciplineRules,setDisciplineRules]=useState<DisciplineRule[]>([]),
    [query, setQuery] = useState(""),
    [sort,setSort]=useState<"default"|"desc"|"asc">("default"),
    [market, setMarket] = useState("全部A股"),
    [days, setDays] = useState(180),
    [loading, setLoading] = useState(true),
    [historyLoading, setHistoryLoading] = useState(true),
    [historyError, setHistoryError] = useState(""),
    [historyRetry, setHistoryRetry] = useState(0),
    [error, setError] = useState(""),
    [watch, setWatch] = useState(false),
    [lines, setLines] = useState({ ma5: true, ma10: true, ma20: true }),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0);
  useEffect(() => {
    setLoading(true);
    const params=new URLSearchParams({page:String(page),sort});
    if(selectedTags.length)params.set("tags",selectedTags.join(","));
    fetch(`${API_BASE}/api/stocks?${params}`)
      .then((r) => r.json())
      .then((d) => {
        if (!d.stocks?.length) throw Error();
        setStocks(d.stocks);
        setTotal(d.total || 0);
        if (page === 1)
          setSelected(
            d.stocks.find((x: Stock) => x.code === "600519") || d.stocks[0],
          );
        setLoading(false);
      })
      .catch(() => {
        setError("免费行情源暂时不可用");
        setLoading(false);
      });
  }, [page,sort,selectedTags]);
  useEffect(() => {
    const controller = new AbortController();
    setRaw([]);
    setHistoryLoading(true);
    setHistoryError("");
    fetch(`${API_BASE}/api/history?code=${selected.code}`, {
      signal: controller.signal,
    })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw Error(d.error || "历史行情读取失败");
        return d;
      })
      .then((d) => {
        if (!d.rows?.length) throw Error("暂无历史行情");
        setRaw(d.rows);
        const latest = d.rows.at(-1) as Row;
        const latestQuote = {
          price: latest.close,
          change: latest.change,
          volume: latest.volume,
          amount: latest.amount,
          ...(Array.isArray(d.tags) ? { tags: d.tags } : {}),
        };
        setSelected((current) =>
          current.code === selected.code ? { ...current, ...latestQuote } : current,
        );
        setStocks((current) =>
          current.map((stock) =>
            stock.code === selected.code ? { ...stock, ...latestQuote } : stock,
          ),
        );
        setSearchResults((current) =>
          current.map((stock) =>
            stock.code === selected.code ? { ...stock, ...latestQuote } : stock,
          ),
        );
      })
      .catch((e) => {
        if (e.name !== "AbortError") {
          setHistoryError(e.message || "历史行情读取失败，请稍后重试");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [selected.code, historyRetry]);
  useEffect(() => {
    fetch(`${API_BASE}/api/industries`)
      .then((r) => r.json())
      .then((d) => setIndustries((d.industries || []).sort(
        (a: Industry, b: Industry) => b.rotation_score - a.rotation_score,
      )))
      .catch(() => {});
  }, []);
  useEffect(()=>{
    if(selected.industry_name && industries.some(x=>x.name===selected.industry_name)){
      setSelectedIndustry(selected.industry_name);
      setSelectedIndustries(current=>current.length?current:[selected.industry_name!]);
    } else if(!selectedIndustry && industries.length) {
      setSelectedIndustry(industries[0].name);
      setSelectedIndustries(current=>current.length?current:[industries[0].name]);
    }
  },[selected.industry_name,industries,selectedIndustry]);
  useEffect(()=>{fetch(`${API_BASE}/api/industry-leaders`).then(r=>r.json()).then(d=>setIndustryLeaders(d.leaders||[])).catch(()=>setIndustryLeaders([]))},[]);
  useEffect(()=>{fetch(`${API_BASE}/api/tags`).then(r=>r.json()).then(d=>setAvailableTags(d.tags||[])).catch(()=>setAvailableTags([]))},[]);
  useEffect(()=>{
    if(activeView!=="radar")return;
    fetch(`${API_BASE}/api/radar?side=${radarSide}&limit=200`).then(r=>r.json()).then(d=>{setRadarSignals(d.signals||[]);setRadarSummary(d.summary||{})}).catch(()=>setRadarSignals([]));
    fetch(`${API_BASE}/api/discipline-rules`).then(r=>r.json()).then(d=>setDisciplineRules(d.rules||[])).catch(()=>setDisciplineRules([]));
  },[activeView,radarSide]);
  const toggleIndustry=(name:string)=>{
    if(selectedIndustries.includes(name)){
      const next=selectedIndustries.filter(item=>item!==name);
      setSelectedIndustries(next);
      if(selectedIndustry===name)setSelectedIndustry(next.at(-1)||"");
    }else{
      setSelectedIndustry(name);
      setSelectedIndustries([...selectedIndustries.slice(-5),name]);
    }
  };
  useEffect(()=>{if(!query.trim()){setSearchResults([]);setSearching(false);return}setSearching(true);const timer=setTimeout(()=>{const params=new URLSearchParams({q:query.trim(),sort});if(selectedTags.length)params.set("tags",selectedTags.join(","));fetch(`${API_BASE}/api/search?${params}`).then(r=>r.json()).then(d=>setSearchResults(d.stocks||[])).catch(()=>setSearchResults([])).finally(()=>setSearching(false))},250);return()=>clearTimeout(timer)},[query,sort,selectedTags]);
  const { rows, signals } = useMemo(() => analyze(raw), [raw]),
    recent = rows.at(-1),
    last20 = rows.slice(-20),
    avgAmount =
      last20.reduce((s, r) => s + r.amount, 0) / Math.max(1, last20.length),
    avgVol =
      last20.reduce((s, r) => s + r.volume, 0) / Math.max(1, last20.length),
    cv =
      Math.sqrt(
        last20.reduce((s, r) => s + (r.volume - avgVol) ** 2, 0) /
          Math.max(1, last20.length),
      ) / Math.max(1, avgVol),
    latestLimit = rows.reduce(
      (latest, row, index) =>
        index > 0 && row.close / rows[index - 1].close >= 1.098
          ? row.date
          : latest,
      "未检出",
    ),
    bottom = signals.filter((s) => s.type === "底背离"),
    top = signals.filter((s) => s.type === "顶背离"),
    filtered = (query.trim()?searchResults:stocks).filter(
      (s) => market === "全部A股" || s.market === market,
    ),
    pageCount = Math.max(1, Math.ceil(total / 100)),
    stockTrend = trendStatus(rows),
    selectedIndustryRows = selectedIndustries
      .map(name=>industries.find(item=>item.name===name))
      .filter((item):item is Industry=>Boolean(item));
  return (
    <main>
      <header>
        <div className="brand">
          <span className="brandmark">析</span>
          <div>
            <b>析股</b>
            <small>A股技术信号分析</small>
          </div>
        </div>
        <nav>
          <button className={activeView==="market"?"nav-active":""} onClick={()=>setActiveView("market")}>个股分析</button>
          <button className={activeView==="industry"?"nav-active":""} onClick={()=>setActiveView("industry")}>行业分析</button>
          <button className={activeView==="radar"?"nav-active":""} onClick={()=>setActiveView("radar")}>信号雷达</button>
          <button>自选组合</button>
        </nav>
        <div className="status">
          <i /> 免费行情 · 盘中延迟 <button className="avatar">ZX</button>
        </div>
      </header>
      <div className="marketbar">
        <div>
          <span>数据源</span>
          <b>东方财富公开行情</b>
          <em>真实数据</em>
        </div>
        <div>
          <span>覆盖</span>
          <b>{total.toLocaleString()} 只</b>
        </div>
        <p>免费接口可能出现延迟或临时不可用，请勿据此直接交易</p>
      </div>
      {activeView==="market"&&<section className="market-search-panel">
        <div className="market-search-line">
          <div className="market-search-title">
            <b>全市场检索</b>
            <span>股票名称、代码、拼音或标签</span>
          </div>
          <label className="search market-search">
            <span>⌕</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索股票名称 / 代码 / 拼音 / 标签"
            />
          </label>
          <div className="market-search-result">
            <span>{searching ? "搜索中" : loading ? "数据载入中" : query ? `${filtered.length} 个搜索结果` : `${total.toLocaleString()} 只股票`}</span>
            {(query||selectedTags.length>0)&&<button onClick={()=>{setQuery("");setSelectedTags([]);setPage(1)}}>清除筛选</button>}
          </div>
        </div>
        <div className="market-quick-tags">
          <b>快捷标签</b>
          <div className="tag-filter" aria-label="股票标签筛选">
            {availableTags.slice(0,12).map(tag=>(
              <button
                key={tag.key}
                className={`${selectedTags.includes(tag.key)?"active ":""}tag-${tag.direction}`}
                onClick={()=>{setPage(1);setSelectedTags(current=>current.includes(tag.key)?current.filter(key=>key!==tag.key):[...current,tag.key])}}
                title={`${tag.name} · ${tag.stock_count}只`}
              >
                {tag.name}<small>{tag.stock_count}</small>
              </button>
            ))}
          </div>
        </div>
      </section>}
      <section className={`workspace ${activeView!=="market"?"industry-view":""}`}>
        {activeView==="market"&&<aside>
          <div className="aside-head">
            <h2>
              股票列表 <span>{total.toLocaleString()}</span>
            </h2>
            <button>{searching ? "搜索中" : query ? `${filtered.length} 个结果` : loading ? "载入中" : `第 ${page}/${pageCount} 页`}</button>
          </div>
          <div className="tabs">
            {["全部A股", "沪市", "深市", "创业板", "科创板"].map((m) => (
              <button
                key={m}
                className={market === m ? "active" : ""}
                onClick={() => setMarket(m)}
              >
                {m.replace("市", "")}
              </button>
            ))}
          </div>
          <div className="list-head">
            <span>股票 / 价格</span>
            <select aria-label="涨跌幅排序" value={sort} onChange={e=>setSort(e.target.value as "default"|"desc"|"asc")}><option value="default">涨跌幅</option><option value="desc">涨幅↓</option><option value="asc">跌幅↑</option></select>
            <span>市场</span>
          </div>
          <div className="stock-list">
            {filtered.map((s) => (
              <button
                key={s.code}
                onClick={() => {
                  setSelected(s);
                  setError("");
                }}
                className={selected.code === s.code ? "selected" : ""}
              >
                <div>
                  <b>{s.name}</b>
                  <small>{s.code}</small>
                </div>
                <div>
                  <strong className={s.change < 0 ? "trend-down" : "trend-up"}>{s.price || "—"}</strong>
                  <em className={s.change < 0 ? "trend-down" : "trend-up"}>
                    {s.change > 0 ? "+" : ""}
                    {s.change}%
                  </em>
                </div>
                <span className="signal">{s.market}</span>
                {!!s.tags?.length&&<div className="stock-tags">{s.tags.slice(0,3).map((tag,index)=><i key={`${tag}-${index}`} className={tag.includes("跌")||tag.includes("空头")||tag.includes("新低")?"down":tag.includes("涨")||tag.includes("多头")||tag.includes("新高")?"up":""}>{tag}</i>)}</div>}
              </button>
            ))}
            {error && <p className="empty">{error}</p>}
            {query && !searching && !filtered.length && <p className="empty">全市场没有匹配的A股</p>}
          </div>
          {!query && <div className="pagination">
            <button
              disabled={page === 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ‹
            </button>
            <b>{page}</b>
            <span>/ {pageCount}</span>
            <button
              disabled={page >= pageCount || loading}
              onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            >
              ›
            </button>
          </div>}
        </aside>}
        <article>
          {activeView==="market"&&<>
          <div className="stock-title">
            <div>
              <p>
                <span>{selected.code}</span> {selected.market}
              </p>
              <h1>
                {selected.name} <small>{selected.code}</small>
              </h1>
              {selected.industry_name&&<div className="industry-tags">
                <span>{selected.industry_name}</span>
                {selected.industry_phase&&<span>{selected.industry_phase}</span>}
                {selected.industry_risk&&<span className={`risk-${selected.industry_risk}`}>轮动风险 {selected.industry_risk} · {selected.industry_risk_score?.toFixed(0)}</span>}
              </div>}
              {!!selected.tags?.length&&<div className="selected-tags">{selected.tags.map((tag,index)=><span key={`${tag}-${index}`}>{tag}</span>)}</div>}
            </div>
            <div className="quote">
              <b className={(recent?.change ?? selected.change) < 0 ? "trend-down" : "trend-up"}>{recent?.close?.toFixed(2) || selected.price || "载入中"}</b>
              <em
                className={
                  (recent?.change ?? selected.change) < 0 ? "trend-down" : "trend-up"
                }
              >
                {(recent?.change ?? selected.change) > 0 ? "+" : ""}
                {(recent?.change ?? selected.change).toFixed(2)}%
              </em>
              <small>
                今开 {recent?.open || "—"}　最高 {recent?.high || "—"}　最低{" "}
                {recent?.low || "—"}
              </small>
            </div>
            <button
              className={watch ? "watch on" : "watch"}
              onClick={() => setWatch(!watch)}
            >
              {watch ? "★ 已自选" : "☆ 加自选"}
            </button>
          </div>
          <div className="metrics">
            <div>
              <span>趋势状态</span>
              <b className="positive">
                {recent && recent.close > (recent.ma20 || 0)
                  ? "偏多运行"
                  : "偏弱运行"}
              </b>
              <small>依据价格与MA20关系</small>
            </div>
            <div>
              <span>底背离</span>
              <b>{bottom.length} 次</b>
              <small>最近：{bottom.at(-1)?.date || "无"}</small>
            </div>
            <div>
              <span>顶背离</span>
              <b>{top.length} 次</b>
              <small>最近：{top.at(-1)?.date || "无"}</small>
            </div>
            <div>
              <span>最近一次涨停</span>
              <b>{latestLimit}</b>
              <small>按前复权日涨幅≥9.8%估算</small>
            </div>
            <div>
              <span>量能波动</span>
              <b>{cv > 0.6 ? "较高" : cv > 0.3 ? "中等" : "平稳"}</b>
              <small>20日变异系数 {cv.toFixed(2)}</small>
            </div>
            <div>
              <span>个股连续趋势</span>
              <b className={stockTrend.direction === "下跌" ? "trend-down" : "trend-up"}>{stockTrend.label}</b>
              <small>期间允许1个反向交易日</small>
            </div>
          </div>
          <div className="panel">
            <div className="panel-head">
              <div>
                <h2>历史价格走势</h2>
                <p>前复权日线 · 真实成交量</p>
              </div>
              <div className="legends">
                {(["ma5", "ma10", "ma20"] as const).map((k, i) => (
                  <button
                    key={k}
                    className={!lines[k] ? "off" : ""}
                    onClick={() => setLines({ ...lines, [k]: !lines[k] })}
                  >
                    <i className={`l${[5, 10, 20][i]}`} />
                    {k.toUpperCase()}
                  </button>
                ))}
              </div>
              <div className="ranges">
                {[30, 60, 120, 180].map((d) => (
                  <button
                    key={d}
                    className={days === d ? "active" : ""}
                    onClick={() => setDays(d)}
                  >
                    {d}日
                  </button>
                ))}
              </div>
            </div>
            {rows.length ? (
              <Chart rows={rows} days={days} lines={lines} signals={signals} />
            ) : historyLoading ? (
              <div className="chart empty">正在读取真实历史行情…</div>
            ) : (
              <div className="chart empty">
                <p>{historyError || "暂时没有可用的历史行情"}</p>
                <button onClick={() => setHistoryRetry((n) => n + 1)}>
                  重新读取
                </button>
              </div>
            )}
            <div className="date-axis">
              <span>{rows.slice(-days)[0]?.date || "—"}</span>
              <span>前复权</span>
              <span>{recent?.date || "—"}</span>
            </div>
          </div>
          </>}
          {activeView==="industry"&&<div className="industry-panel">
            <div className="section-title"><h2>90日行业轮动与风险</h2><span>申万二级行业 · {industries.length} 个行业</span></div>
            <IndustryGlobalFilter industries={industries} selected={selectedIndustries} onToggle={toggleIndustry} onClear={()=>{setSelectedIndustries([]);setSelectedIndustry("")}} />
            <IndustryRotationMap industries={industries} selected={selectedIndustries} onToggle={toggleIndustry} />
            <IndustryRotationCompare industries={industries} selected={selectedIndustries} onRemove={toggleIndustry} />
            <div className="industry-subtitle"><b>已选行业最新状态</b><span>{selectedIndustryRows.length} 个行业 · 点击卡片可移出筛选</span></div>
            <div className="industry-grid">{selectedIndustryRows.map((x)=><button className="industry-item active" onClick={()=>toggleIndustry(x.name)} key={x.name}><div><b>{x.name}</b><em className={x.avg_change_pct<0?"trend-down":"trend-up"}>{x.avg_change_pct>0?"+":""}{x.avg_change_pct.toFixed(2)}%</em></div><p><span>轮动强度 <strong>{x.rotation_score.toFixed(0)}</strong></span><span>20日 {x.return_20d>0?"+":""}{x.return_20d.toFixed(1)}%</span></p><div className="breadth"><i style={{width:`${x.above_ma20_pct}%`}}/></div><small>MA20上方 {x.above_ma20_pct.toFixed(0)}% · 涨{x.up_count} 跌{x.down_count}</small><strong className={x.phase==="下跌"||x.phase==="退潮"?"trend-down":"trend-up"}>{x.phase}第 {x.phase_days} 天</strong><span className={`risk-pill risk-${x.risk_level}`}>轮动风险 {x.risk_level} {x.risk_score.toFixed(0)}</span></button>)}</div>
            {industries.filter(x=>x.name===selectedIndustry&&selectedIndustries.includes(x.name)).map(x=><div className="industry-detail" key={x.name}>
              <div><b>{x.name} · 近90个交易日节奏</b><span>行业指数</span></div>
              <svg viewBox="0 0 900 120" preserveAspectRatio="none" aria-label={`${x.name}近90日行业指数`}>
                <polyline points={x.history.map((p,i)=>`${i*900/Math.max(1,x.history.length-1)},${110-(p.index-Math.min(...x.history.map(v=>v.index)))*95/Math.max(.01,Math.max(...x.history.map(v=>v.index))-Math.min(...x.history.map(v=>v.index)))}`).join(" ")} />
              </svg>
              <div className="risk-reasons"><b>接下来一段时间风险：{x.risk_level}</b><span>{reasonText(x.risk_reasons)}</span><small>这是基于价格、量能、市场广度与周期位置的概率提示，不构成确定预测。</small></div>
              {!!industryLeaders.filter(v=>v.industry_name===x.name).length&&<div className="leader-analysis">
                <h3>PDF策略提及的行业核心股</h3>
                {industryLeaders.filter(v=>v.industry_name===x.name).map(v=><button key={v.stock_code} onClick={()=>{setSelected(stocks.find(s=>s.code===v.stock_code)||{code:v.stock_code,name:v.name,market:"",price:0,change:0,volume:0,amount:0,industry_name:v.industry_name});setActiveView("market")}}>
                  <div><b>{v.name}</b><small>{v.stock_code} · {v.strategy_role}</small><strong className={v.turning_signal.includes("转弱")||v.turning_signal.includes("偏弱")?"trend-down":"trend-up"}>{v.turning_signal}</strong></div>
                  <p><span>同向率 {v.direction_match_pct.toFixed(0)}%</span><span>相关性 {v.correlation_90d.toFixed(2)}</span><span>涨跌弹性 {v.amplitude_ratio.toFixed(1)}倍</span><span>{v.lead_lag_days>0?`约领先行业 ${v.lead_lag_days} 日`:v.lead_lag_days<0?`约滞后行业 ${-v.lead_lag_days} 日`:"与行业同步"}</span></p>
                  <em>{reasonText(v.turning_reasons)}</em>
                </button>)}
              </div>}
            </div>)}
          </div>}
          {activeView==="radar"&&<div className="radar-panel">
            <div className="section-title"><div><h2>交易纪律信号雷达</h2><small>行情事实 → 纪律条件 → 信号评分 → 风险拦截</small></div><span>每日收盘后计算 · 仅作决策辅助</span></div>
            <div className="radar-summary">
              <div><small>买入确认</small><b>{radarSummary.buy_confirmed}</b></div><div><small>买入候选</small><b>{radarSummary.candidates}</b></div>
              <div><small>减仓信号</small><b>{radarSummary.reduce}</b></div><div><small>退出信号</small><b>{radarSummary.exit}</b></div>
            </div>
            <div className="radar-filters">{(["all","buy","sell"] as const).map(value=><button key={value} className={radarSide===value?"active":""} onClick={()=>setRadarSide(value)}>{value==="all"?"全部信号":value==="buy"?"买入纪律":"卖出纪律"}</button>)}</div>
            <div className="radar-table">
              <div className="radar-row radar-head"><span>股票 / 行业</span><span>收盘 / 涨跌</span><span>买入纪律</span><span>卖出纪律</span><span>模型与依据</span><span>防守位</span></div>
              {radarSignals.map(item=><button className="radar-row" key={item.code} onClick={()=>{setSelected(item);setActiveView("market")}}>
                <span><b>{item.name}</b><small>{item.code} · {item.industry_name||"未分类"}</small></span>
                <span><b>{Number(item.price).toFixed(2)}</b><small className={Number(item.change)<0?"trend-down":"trend-up"}>{Number(item.change)>0?"+":""}{Number(item.change).toFixed(2)}%</small></span>
                <span><strong className={item.buy_level==="禁买"?"level-block":"level-buy"}>{item.buy_level}</strong><small>评分 {Number(item.buy_score).toFixed(0)}</small></span>
                <span><strong className={item.sell_level==="退出"?"level-exit":""}>{item.sell_level}</strong><small>评分 {Number(item.sell_score).toFixed(0)}</small></span>
                <span><b>{item.buy_model}</b><small>{[...(item.buy_signals||[]),...(item.sell_signals||[]),...(item.blockers||[])].slice(0,2).join("；")||"暂无强信号"}</small></span>
                <span><b>{Number(item.defense_price||0).toFixed(2)}</b><small>ATR止损 {Number(item.stop_atr_price||0).toFixed(2)}</small></span>
              </button>)}
              {!radarSignals.length&&<p className="empty">尚未生成纪律信号，请先执行本地信号刷新。</p>}
            </div>
            <div className="radar-rules"><h3>当前启用规则</h3>{disciplineRules.map(rule=><div key={rule.rule_key}><b>{rule.rule_name}</b><span>{rule.description}</span><em>优先级 {rule.priority}</em></div>)}</div>
          </div>}
          {activeView==="market"&&<div className="lower">
            <div className="signal-log">
              <div className="section-title">
                <h2>最近背离信号</h2>
                <span>MACD DIF 与价格枢轴</span>
              </div>
              {signals
                .slice(-2)
                .reverse()
                .map((s) => (
                  <div
                    key={s.date + s.type}
                    className={`event ${s.type === "底背离" ? "bottom-event" : "top-event"}`}
                  >
                    <b>{s.type[0]}</b>
                    <div>
                      <strong>{s.type}确认</strong>
                      <span>{s.date}</span>
                      <p>价格枢轴与MACD DIF方向不一致</p>
                    </div>
                    <em>算法识别</em>
                  </div>
                ))}
              {!signals.length && (
                <p className="empty">近期开盘数据中未识别到背离</p>
              )}
            </div>
            <div className="volume-card">
              <div className="section-title">
                <h2>成交量波动</h2>
                <span>近20日</span>
              </div>
              <div className="gauge">
                <b>{cv.toFixed(2)}</b>
                <span>变异系数</span>
              </div>
              <div className="vol-info">
                <p>
                  <span>平均成交额</span>
                  <b>{money(avgAmount)}</b>
                </p>
                <p>
                  <span>最新换手率</span>
                  <b>{recent?.turnover || 0}%</b>
                </p>
                <p>
                  <span>放量日</span>
                  <b>
                    {last20.filter((r) => r.volume > avgVol * 1.3).length} 天
                  </b>
                </p>
              </div>
            </div>
          </div>}
        </article>
      </section>
      <footer>
        数据来源：东方财富公开行情接口。免费数据可能延迟、缺失或中断；背离与涨停日期由算法估算，不构成投资建议。
      </footer>
    </main>
  );
}
