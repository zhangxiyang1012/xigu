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
type PortfolioPosition = RadarSignal & {quantity?:number;cost_price?:number;note?:string};
type BacktestSummary = {side:string;horizon:string;trading_days:number;sample_count:number;avg_return_pct:number;median_return_pct:number;win_rate_pct:number;best_return_pct:number;worst_return_pct:number};
type BacktestData = {run?:{id:number;status:string;start_date:string;end_date:string;stock_count:number;event_count:number;trade_count:number;parameters:Record<string,unknown>};summaries:BacktestSummary[];trades:Record<string,number>;strategies:{side:string;strategy:string;samples:number;avg_return_pct:number;win_rate_pct:number}[];events:{side:string;signal_date:string;execution_date:string;signal_level:string;strategy_name:string;matched_rules:string[];execution_price:number;code:string;name:string;industry_name:string}[]};
type PositionModel = {market?:{regime:string;factor:number;breadth:number;high_risk_share:number;trade_date:string};positions:{code:string;target_weight_pct:number;risk_weight_pct:number;stop_distance_pct:number;industry_factor:number;signal_factor:number;market_factor:number}[];limits?:Record<string,number>};
type StockAnalysis = {
  fundamental?: {company_name?:string;main_business?:string;company_intro?:string;concepts?:{name:string;reason?:string}[]|string;report_date?:string;report_name?:string;revenue?:number;revenue_yoy?:number;net_profit?:number;net_profit_yoy?:number;gross_margin?:number;roe?:number;total_market_cap?:number;free_market_cap?:number;performance_support?:string;updated_at?:string};
  technical?: {trade_date:string;close:number;ma5:number;ma10:number;ma20:number;atr14:number;volume_ratio_5:number;volume_ratio_20:number;drawdown_20d:number;drawdown_60d:number;pullback_days:number;industry_rank:number;industry_rotation_score:number;industry_risk_level:string;buy_score:number;sell_score:number;buy_level:string;sell_level:string;buy_model:string;buy_signals:string[];sell_signals:string[];blockers:string[];defense_price:number;stop_atr_price:number};
  tags?: {tag_name:string;direction:string;category:string}[];
};
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
}: {
  industries: Industry[];
  selected: string[];
}) {
  const dates = Array.from(new Set(industries.flatMap(item=>item.history.map(point=>point.date)))).sort().slice(-90);
  const weekEnds=dates.filter((_,index)=>index%5===4||index===dates.length-1);
  const weekly=weekEnds.map(date=>({date,leaders:industries.map(item=>{
    const point=[...item.history].reverse().find(value=>value.date<=date);
    return point?{item,point}:null;
  }).filter((value):value is {item:Industry;point:Industry["history"][number]}=>Boolean(value)).sort((a,b)=>b.point.score-a.point.score).slice(0,10)}));
  if (!weekly.length) return <div className="rotation-empty">暂无行业轮动历史数据</div>;
  return (
    <div className="rotation-map">
      <div className="rotation-map-head">
        <div>
          <b>过去90个交易日 · 每周轮动 Top 10</b>
          <span>按每周最后一个交易日的轮动强度排名，观察强势行业的进入、持续与退出</span>
        </div>
        <div className="rotation-legend">
          <span><i className="selected" />已选行业</span>
          <span><i className="danger" />高风险</span>
        </div>
      </div>
      <div className="weekly-rotation-scroll">
        <div className="weekly-rotation" style={{gridTemplateColumns:`repeat(${weekly.length}, minmax(150px, 1fr))`}}>
          {weekly.map(week=><section key={week.date} className="rotation-week">
            <time>{week.date.slice(5)}</time>
            {week.leaders.map(({item,point},index)=><div key={item.name} className={`${selected.includes(item.name)?"selected":""} ${point.risk>=65?"high-risk":""}`} title={`${item.name} ${week.date}\n轮动强度 ${point.score.toFixed(0)} · ${point.phase}\n风险 ${point.riskLevel} ${point.risk.toFixed(0)} · MA20上方 ${point.breadth.toFixed(0)}%`}>
              <b>{index+1}</b><span>{item.name}</span><em>{point.score.toFixed(0)}</em>
            </div>)}
          </section>)}
        </div>
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
  const [filterQuery,setFilterQuery]=useState("");
  const pickerRef=useRef<HTMLDetailsElement>(null);
  const available=industries.filter(item=>!selected.includes(item.name)&&item.name.includes(filterQuery.trim())).sort((a,b)=>a.name.localeCompare(b.name,"zh-CN"));
  useEffect(()=>{const close=(event:PointerEvent)=>{if(pickerRef.current?.open&&!pickerRef.current.contains(event.target as Node))pickerRef.current.removeAttribute("open")};document.addEventListener("pointerdown",close);return()=>document.removeEventListener("pointerdown",close)},[]);
  return <div className="industry-global-filter">
    <div className="global-filter-head">
      <div><b>全局行业筛选</b><span>热力图、轮动曲线、行业状态与风险明细同步筛选 · 最多6个行业</span></div>
      <div className="compare-actions industry-picker-actions">
        <details className="industry-picker" ref={pickerRef}>
          <summary>＋ 选择行业 <small>{selected.length}/6</small></summary>
          <div className="industry-picker-menu">
            <input aria-label="搜索行业" placeholder="搜索申万二级行业" value={filterQuery} onChange={event=>setFilterQuery(event.target.value)}/>
            <div>{available.map(item=><label key={item.name}><input type="checkbox" checked={false} disabled={selected.length>=6} onChange={()=>onToggle(item.name)}/><span>{item.name}</span><em>{item.rotation_score.toFixed(0)}</em></label>)}</div>
          </div>
        </details>
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
}: {
  industries: Industry[];
  selected: string[];
}) {
  const [hoverIndex,setHoverIndex]=useState<number|null>(null);
  const rows=selected.map(name=>industries.find(item=>item.name===name)).filter(Boolean) as Industry[];
  if(!rows.length) return <div className="compare-empty">请使用顶部行业选择器，选择一个或多个行业查看轮动明细</div>;
  const lines=rows.map((item,index)=>{
    const history=item.history.slice(-90),base=history[0]?.index||1;
    const normalized=history.map(point=>({...point,value:(point.index/base-1)*100}));
    const values=normalized.map(point=>point.value);
    return {item,index,normalized,min:Math.min(...values),max:Math.max(...values)};
  });
  const globalMin=Math.min(0,...lines.map(line=>line.min)),globalMax=Math.max(0,...lines.map(line=>line.max));
  const maxLength=Math.max(...lines.map(line=>line.normalized.length));
  return <div className="rotation-compare">
    <div className="compare-head"><div><b>近90个交易日相对涨跌节奏</b><span>以区间首日为0%，线上升表示行业指数相对首日走强，下降表示走弱</span></div><div className="compare-scale"><b>{globalMax>0?"+":""}{globalMax.toFixed(1)}%</b><span>区间上沿</span></div></div>
    <div className="rotation-chart-wrap" onMouseLeave={()=>setHoverIndex(null)} onMouseMove={event=>{const rect=event.currentTarget.getBoundingClientRect();setHoverIndex(Math.max(0,Math.min(maxLength-1,Math.round((event.clientX-rect.left)/rect.width*(maxLength-1)))));}}>
      <svg viewBox="0 0 900 180" preserveAspectRatio="none" aria-label="多行业90日轮动对比">
        {[0,1,2,3].map(n=><line key={n} x1="0" x2="900" y1={20+n*45} y2={20+n*45}/>)}
        <line className="zero-guide" x1="0" x2="900" y1={165-(0-globalMin)*145/Math.max(.01,globalMax-globalMin)} y2={165-(0-globalMin)*145/Math.max(.01,globalMax-globalMin)}/>
        {lines.map(line=><polyline key={line.item.name} style={{stroke:industryLineColors[line.index]}} points={line.normalized.map((point,i)=>`${i*900/Math.max(1,line.normalized.length-1)},${165-(point.value-globalMin)*145/Math.max(.01,globalMax-globalMin)}`).join(" ")}/>)}
        {hoverIndex!==null&&<line className="hover-guide" x1={hoverIndex*900/Math.max(1,maxLength-1)} x2={hoverIndex*900/Math.max(1,maxLength-1)} y1="0" y2="180"/>}
      </svg>
      {hoverIndex!==null&&<div className={`rotation-tooltip ${hoverIndex>maxLength*.7?"left":""}`} style={{left:`${hoverIndex/Math.max(1,maxLength-1)*100}%`}}>
        <b>{lines[0]?.normalized[Math.min(hoverIndex,lines[0].normalized.length-1)]?.date}</b>
        {lines.map(line=>{const point=line.normalized[Math.min(hoverIndex,line.normalized.length-1)];return point&&<span key={line.item.name}><i style={{background:industryLineColors[line.index]}}/><strong>{line.item.name}</strong><em>{point.value>0?"+":""}{point.value.toFixed(2)}%</em><small>{point.phase} · 强度{point.score.toFixed(0)} · 风险{point.riskLevel}{point.risk.toFixed(0)}</small></span>})}
      </div>}
    </div>
    <div className="compare-table">
      <div className="compare-row compare-labels"><b>行业</b><span>当前阶段</span><span>轮动强度</span><span>20日涨跌</span><span>MA20广度</span><span>风险</span></div>
      {lines.map(line=><div className="compare-row" key={line.item.name}><b><i style={{background:industryLineColors[line.index]}}/>{line.item.name}</b><span>{line.item.phase}第{line.item.phase_days}天</span><span>{line.item.rotation_score.toFixed(0)}</span><span className={line.item.return_20d<0?"trend-down":"trend-up"}>{line.item.return_20d>0?"+":""}{line.item.return_20d.toFixed(1)}%</span><span>{line.item.above_ma20_pct.toFixed(0)}%</span><span className={`risk-${line.item.risk_level}`}>{line.item.risk_level} {line.item.risk_score.toFixed(0)}</span></div>)}
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
    [activeView,setActiveView]=useState<"market"|"industry"|"radar"|"portfolio"|"backtest">("market"),
    [radarSide,setRadarSide]=useState<"all"|"buy"|"sell">("all"),
    [radarQuery,setRadarQuery]=useState(""),
    [radarLevel,setRadarLevel]=useState(""),
    [radarOnlyWatch,setRadarOnlyWatch]=useState(false),
    [radarSignals,setRadarSignals]=useState<RadarSignal[]>([]),
    [radarSummary,setRadarSummary]=useState({buy_confirmed:0,candidates:0,reduce:0,exit:0}),
    [disciplineRules,setDisciplineRules]=useState<DisciplineRule[]>([]),
    [watchCodes,setWatchCodes]=useState<string[]>([]),
    [portfolio,setPortfolio]=useState<PortfolioPosition[]>([]),
    [portfolioQuery,setPortfolioQuery]=useState(""),
    [portfolioResults,setPortfolioResults]=useState<Stock[]>([]),
    [backtestData,setBacktestData]=useState<BacktestData>({summaries:[],trades:{},strategies:[],events:[]}),
    [backtestQuery,setBacktestQuery]=useState(""),
    [backtestResults,setBacktestResults]=useState<Stock[]>([]),
    [backtestStocks,setBacktestStocks]=useState<Stock[]>([]),
    [backtestRunning,setBacktestRunning]=useState(false),
    [backtestError,setBacktestError]=useState(""),
    [positionModel,setPositionModel]=useState<PositionModel>({positions:[]}),
    [stockAnalysis,setStockAnalysis]=useState<StockAnalysis>({}),
    [analysisLoading,setAnalysisLoading]=useState(false),
    [query, setQuery] = useState(""),
    [sort,setSort]=useState<"default"|"desc"|"asc">("default"),
    [market, setMarket] = useState("全部A股"),
    [days, setDays] = useState(180),
    [loading, setLoading] = useState(true),
    [historyLoading, setHistoryLoading] = useState(true),
    [historyError, setHistoryError] = useState(""),
    [historyRetry, setHistoryRetry] = useState(0),
    [error, setError] = useState(""),
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
    } else if(industries.length) {
      setSelectedIndustry(current=>current||industries[0].name);
      setSelectedIndustries(current=>current.length?current:[industries[0].name]);
    }
  },[selected.industry_name,industries]);
  useEffect(()=>{fetch(`${API_BASE}/api/industry-leaders`).then(r=>r.json()).then(d=>setIndustryLeaders(d.leaders||[])).catch(()=>setIndustryLeaders([]))},[]);
  useEffect(()=>{fetch(`${API_BASE}/api/tags`).then(r=>r.json()).then(d=>setAvailableTags(d.tags||[])).catch(()=>setAvailableTags([]))},[]);
  useEffect(()=>{
    if(activeView!=="radar")return;
    const timer=setTimeout(()=>fetch(`${API_BASE}/api/radar?side=${radarSide}&level=${encodeURIComponent(radarLevel)}&only_watch=${radarOnlyWatch}&q=${encodeURIComponent(radarQuery.trim())}&limit=200`).then(r=>r.json()).then(d=>{setRadarSignals(d.signals||[]);setRadarSummary(d.summary||{})}).catch(()=>setRadarSignals([])),250);
    return()=>clearTimeout(timer);
  },[activeView,radarSide,radarOnlyWatch,radarQuery,radarLevel]);
  useEffect(()=>{if(activeView==="radar"||activeView==="portfolio")fetch(`${API_BASE}/api/discipline-rules`).then(r=>r.json()).then(d=>setDisciplineRules(d.rules||[])).catch(()=>setDisciplineRules([]))},[activeView]);
  const loadWatchlist=()=>fetch(`${API_BASE}/api/watchlist`).then(r=>r.json()).then(d=>setWatchCodes((d.stocks||[]).map((x:{code:string})=>x.code))).catch(()=>setWatchCodes([]));
  const loadPortfolio=()=>fetch(`${API_BASE}/api/portfolio`).then(r=>r.json()).then(d=>setPortfolio(d.positions||[])).catch(()=>setPortfolio([]));
  useEffect(()=>{loadWatchlist()},[]);
  useEffect(()=>{if(activeView==="portfolio"){loadPortfolio();fetch(`${API_BASE}/api/position-model`).then(r=>r.json()).then(setPositionModel).catch(()=>setPositionModel({positions:[]}))}},[activeView]);
  useEffect(()=>{if(activeView==="backtest")fetch(`${API_BASE}/api/backtest/latest`).then(r=>r.json()).then(d=>setBacktestData(d)).catch(()=>setBacktestData({summaries:[],trades:{},strategies:[],events:[]}))},[activeView]);
  useEffect(()=>{if(activeView!=="backtest"||!backtestQuery.trim()){setBacktestResults([]);return}const timer=setTimeout(()=>fetch(`${API_BASE}/api/search?q=${encodeURIComponent(backtestQuery.trim())}`).then(r=>r.json()).then(d=>setBacktestResults((d.stocks||[]).filter((stock:Stock)=>!backtestStocks.some(item=>item.code===stock.code)).slice(0,10))).catch(()=>setBacktestResults([])),250);return()=>clearTimeout(timer)},[activeView,backtestQuery,backtestStocks]);
  useEffect(()=>{if(!portfolioQuery.trim()){setPortfolioResults([]);return}const timer=setTimeout(()=>fetch(`${API_BASE}/api/search?q=${encodeURIComponent(portfolioQuery.trim())}`).then(r=>r.json()).then(d=>setPortfolioResults((d.stocks||[]).slice(0,8))).catch(()=>setPortfolioResults([])),250);return()=>clearTimeout(timer)},[portfolioQuery]);
  useEffect(()=>{const controller=new AbortController();setAnalysisLoading(true);fetch(`${API_BASE}/api/stock-analysis?code=${selected.code}`,{signal:controller.signal}).then(r=>r.json()).then(d=>setStockAnalysis(d||{})).catch(e=>{if(e.name!=="AbortError")setStockAnalysis({})}).finally(()=>{if(!controller.signal.aborted)setAnalysisLoading(false)});return()=>controller.abort()},[selected.code]);
  const toggleWatch=async(code:string)=>{const watched=watchCodes.includes(code);await fetch(`${API_BASE}/api/watchlist/${code}`,{method:watched?"DELETE":"POST"});await loadWatchlist()};
  const addPosition=async(stock:Stock)=>{await fetch(`${API_BASE}/api/portfolio`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:stock.code})});setPortfolioQuery("");setPortfolioResults([]);await loadPortfolio()};
  const removePosition=async(code:string)=>{await fetch(`${API_BASE}/api/portfolio/${code}`,{method:"DELETE"});await loadPortfolio()};
  const runSelectedBacktest=async()=>{
    if(!backtestStocks.length)return;
    setBacktestRunning(true);setBacktestError("");
    try{
      const response=await fetch(`${API_BASE}/api/backtest/run`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({stock_codes:backtestStocks.map(stock=>stock.code)})});
      const result=await response.json();
      if(!response.ok)throw Error(result.detail||"回测执行失败");
      const latest=await fetch(`${API_BASE}/api/backtest/latest`).then(r=>r.json());
      setBacktestData(latest);
    }catch(error){setBacktestError(error instanceof Error?error.message:"回测执行失败")}
    finally{setBacktestRunning(false)}
  };
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
          <button className={activeView==="portfolio"?"nav-active":""} onClick={()=>setActiveView("portfolio")}>持仓诊断</button>
          <button className={activeView==="backtest"?"nav-active":""} onClick={()=>setActiveView("backtest")}>策略回测</button>
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
                    {Number(s.change).toFixed(2)}%
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
              className={watchCodes.includes(selected.code) ? "watch on" : "watch"}
              onClick={() => toggleWatch(selected.code)}
            >
              {watchCodes.includes(selected.code) ? "★ 已自选" : "☆ 加自选"}
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
          <div className="stock-analysis-grid">
            <section className="fundamental-card">
              <div className="analysis-card-head"><div><h2>基本面</h2><small>公司资料与最新财报 · 本地缓存</small></div><span>{stockAnalysis.fundamental?.report_name||"待同步"}</span></div>
              {stockAnalysis.fundamental?<>
                <div className="business-summary"><b>主营业务</b><p>{stockAnalysis.fundamental.main_business||stockAnalysis.fundamental.company_intro||"暂无主营业务资料"}</p></div>
                <div className="fundamental-metrics"><div><small>业绩支撑</small><b>{stockAnalysis.fundamental.performance_support}</b></div><div><small>营业收入</small><b>{money(Number(stockAnalysis.fundamental.revenue||0))}</b><em className={Number(stockAnalysis.fundamental.revenue_yoy)<0?"trend-down":"trend-up"}>{Number(stockAnalysis.fundamental.revenue_yoy)>0?"+":""}{Number(stockAnalysis.fundamental.revenue_yoy||0).toFixed(1)}%</em></div><div><small>归母净利润</small><b>{money(Number(stockAnalysis.fundamental.net_profit||0))}</b><em className={Number(stockAnalysis.fundamental.net_profit_yoy)<0?"trend-down":"trend-up"}>{Number(stockAnalysis.fundamental.net_profit_yoy)>0?"+":""}{Number(stockAnalysis.fundamental.net_profit_yoy||0).toFixed(1)}%</em></div><div><small>毛利率 / ROE</small><b>{Number(stockAnalysis.fundamental.gross_margin||0).toFixed(1)}% / {Number(stockAnalysis.fundamental.roe||0).toFixed(1)}%</b></div><div><small>总市值</small><b>{money(Number(stockAnalysis.fundamental.total_market_cap||0))}</b></div><div><small>流通市值</small><b>{money(Number(stockAnalysis.fundamental.free_market_cap||0))}</b></div></div>
                <div className="concept-list"><b>题材概念</b><div>{(()=>{const raw=stockAnalysis.fundamental?.concepts;const concepts=typeof raw==="string"?JSON.parse(raw||"[]"):raw||[];return concepts.map((x:{name:string;reason?:string})=><span key={x.name} title={x.reason||x.name}>{x.name}</span>)})()}</div></div>
              </>:<p className="empty">{analysisLoading?"正在同步公司资料与财务数据…":"基本面数据暂时不可用"}</p>}
            </section>
            <section className="technical-card">
              <div className="analysis-card-head"><div><h2>技术面与纪律匹配</h2><small>当前买入、卖出纪律关注指标</small></div><span>{stockAnalysis.technical?.trade_date||"待计算"}</span></div>
              {stockAnalysis.technical?<><div className="discipline-status"><div><small>买入纪律</small><b className={stockAnalysis.technical.buy_level==="禁买"?"trend-down":"trend-up"}>{stockAnalysis.technical.buy_level} · {Number(stockAnalysis.technical.buy_score).toFixed(0)}分</b><em>{stockAnalysis.technical.buy_model}</em></div><div><small>卖出纪律</small><b className={stockAnalysis.technical.sell_level==="退出"?"trend-down":""}>{stockAnalysis.technical.sell_level} · {Number(stockAnalysis.technical.sell_score).toFixed(0)}分</b><em>行业风险 {stockAnalysis.technical.industry_risk_level}</em></div></div>
                <div className="technical-metrics"><span>MA5 <b>{Number(stockAnalysis.technical.ma5).toFixed(2)}</b></span><span>MA10 <b>{Number(stockAnalysis.technical.ma10).toFixed(2)}</b></span><span>MA20 <b>{Number(stockAnalysis.technical.ma20).toFixed(2)}</b></span><span>ATR14 <b>{Number(stockAnalysis.technical.atr14).toFixed(2)}</b></span><span>5日量比 <b>{Number(stockAnalysis.technical.volume_ratio_5).toFixed(2)}</b></span><span>20日量比 <b>{Number(stockAnalysis.technical.volume_ratio_20).toFixed(2)}</b></span><span>20日回撤 <b>{Number(stockAnalysis.technical.drawdown_20d).toFixed(1)}%</b></span><span>防守位 <b>{Number(stockAnalysis.technical.defense_price).toFixed(2)}</b></span></div>
                <div className="technical-tags">{(stockAnalysis.tags||[]).map(tag=><span className={tag.direction} key={`${tag.category}-${tag.tag_name}`}>{tag.tag_name}</span>)}</div>
                <div className="discipline-reasons"><p><b>买入依据</b>{(stockAnalysis.technical.buy_signals||[]).join("；")||"暂无"}</p><p><b>卖出依据</b>{(stockAnalysis.technical.sell_signals||[]).join("；")||"暂无"}</p>{!!stockAnalysis.technical.blockers?.length&&<p className="blockers"><b>风险拦截</b>{stockAnalysis.technical.blockers.join("；")}</p>}</div>
              </>:<p className="empty">{analysisLoading?"正在读取技术纪律指标…":"技术指标尚未计算"}</p>}
            </section>
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
            <IndustryRotationMap industries={industries} selected={selectedIndustries} />
            <IndustryRotationCompare industries={industries} selected={selectedIndustries} />
            <div className="industry-subtitle"><b>已选行业最新状态</b><span>{selectedIndustryRows.length} 个行业 · 选择与取消统一在顶部操作</span></div>
            <div className="industry-grid">{selectedIndustryRows.map((x)=><div className="industry-item active" key={x.name}><div><b>{x.name}</b><em className={x.avg_change_pct<0?"trend-down":"trend-up"}>{x.avg_change_pct>0?"+":""}{x.avg_change_pct.toFixed(2)}%</em></div><p><span>轮动强度 <strong>{x.rotation_score.toFixed(0)}</strong></span><span>20日 {x.return_20d>0?"+":""}{x.return_20d.toFixed(1)}%</span></p><div className="breadth"><i style={{width:`${x.above_ma20_pct}%`}}/></div><small>MA20上方 {x.above_ma20_pct.toFixed(0)}% · 涨{x.up_count} 跌{x.down_count}</small><strong className={x.phase==="下跌"||x.phase==="退潮"?"trend-down":"trend-up"}>{x.phase}第 {x.phase_days} 天</strong><span className={`risk-pill risk-${x.risk_level}`}>轮动风险 {x.risk_level} {x.risk_score.toFixed(0)}</span></div>)}</div>
            {industries.filter(x=>x.name===selectedIndustry&&selectedIndustries.includes(x.name)).map(x=><div className="industry-detail" key={x.name}>
              <div><b>{x.name} · 当前风险诊断</b><span>走势比较请查看上方“相对涨跌节奏”</span></div>
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
            <div className="radar-rules radar-rules-top"><h3>买入与卖出纪律</h3><div className="radar-rule-columns"><section><b>买入纪律</b>{disciplineRules.filter(rule=>rule.side==="buy").map(rule=><p key={rule.rule_key}><strong>{rule.rule_name}</strong><span>{rule.description}</span></p>)}</section><section><b>卖出纪律</b>{disciplineRules.filter(rule=>rule.side==="sell").map(rule=><p key={rule.rule_key}><strong>{rule.rule_name}</strong><span>{rule.description}</span></p>)}</section></div></div>
            <div className="radar-summary">
              <button className={radarLevel==="买入确认"?"active":""} onClick={()=>{setRadarLevel(v=>v==="买入确认"?"":"买入确认");setRadarSide("buy")}}><small>买入确认</small><b>{radarSummary.buy_confirmed}</b></button>
              <button className={radarLevel==="候选"?"active":""} onClick={()=>{setRadarLevel(v=>v==="候选"?"":"候选");setRadarSide("buy")}}><small>买入候选</small><b>{radarSummary.candidates}</b></button>
              <button className={radarLevel==="减仓"?"active":""} onClick={()=>{setRadarLevel(v=>v==="减仓"?"":"减仓");setRadarSide("sell")}}><small>减仓信号</small><b>{radarSummary.reduce}</b></button>
              <button className={radarLevel==="退出"?"active":""} onClick={()=>{setRadarLevel(v=>v==="退出"?"":"退出");setRadarSide("sell")}}><small>退出信号</small><b>{radarSummary.exit}</b></button>
            </div>
            <div className="radar-filters"><label className="radar-search"><span>⌕</span><input value={radarQuery} onChange={e=>setRadarQuery(e.target.value)} placeholder="搜索名称 / 代码 / 拼音 / 标签"/>{radarQuery&&<button onClick={()=>setRadarQuery("")} aria-label="清空雷达搜索">×</button>}</label>{(["all","buy","sell"] as const).map(value=><button key={value} className={radarSide===value?"active":""} onClick={()=>{setRadarSide(value);setRadarLevel("")}}>{value==="all"?"全部信号":value==="buy"?"买入纪律":"卖出纪律"}</button>)}<label className="watch-filter"><input type="checkbox" checked={radarOnlyWatch} onChange={e=>setRadarOnlyWatch(e.target.checked)}/> 仅看自选股票</label>{radarLevel&&<button className="active" onClick={()=>setRadarLevel("")}>{radarLevel} ×</button>}</div>
            <div className="radar-table">
              <div className="radar-row radar-head"><span>股票 / 行业</span><span>收盘 / 涨跌</span><span>买入纪律</span><span>卖出纪律</span><span>模型与依据</span><span>防守位</span></div>
              {radarSignals.map(item=><button className="radar-row" key={item.code} onClick={()=>{setSelected(item);setActiveView("market")}}>
                <span><b>{item.name}</b><small>{item.code} · {item.industry_name||"未分类"}</small></span>
                <span><b>{Number(item.price).toFixed(2)}</b><small className={Number(item.change)<0?"trend-down":"trend-up"}>{Number(item.change)>0?"+":""}{Number(item.change).toFixed(2)}%</small></span>
                <span><strong className={item.buy_level==="禁买"?"level-block":"level-buy"}>{item.buy_level}</strong><small>评分 {Number(item.buy_score).toFixed(0)}</small></span>
                <span><strong className={item.sell_level==="退出"?"level-exit":""}>{item.sell_level}</strong><small>评分 {Number(item.sell_score).toFixed(0)}</small></span>
                <span><b>{item.buy_model}</b><small>{[...(item.buy_signals||[]),...(item.sell_signals||[]),...(item.blockers||[])].slice(0,2).join("；")||"暂无强信号"}</small></span>
                <span><b>{Number(item.defense_price||0).toFixed(2)}</b><small>1.5ATR止损 {Number(item.stop_atr_price||0).toFixed(2)}</small></span>
              </button>)}
              {!radarSignals.length&&<p className="empty">尚未生成纪律信号，请先执行本地信号刷新。</p>}
            </div>
          </div>}
          {activeView==="portfolio"&&<div className="portfolio-panel">
            <div className="section-title"><div><h2>持仓诊断</h2><small>将持仓股票加入本地股票池，集中查看纪律信号和匹配策略</small></div><span>{portfolio.length} 只持仓</span></div>
            {positionModel.market&&<div className="position-market"><b>动态仓位环境：{positionModel.market.regime}</b><span>市场风险系数 {positionModel.market.factor.toFixed(1)} · 行业MA20广度 {positionModel.market.breadth.toFixed(0)}% · 高风险行业 {positionModel.market.high_risk_share.toFixed(0)}%</span><em>单笔风险0.8% · 单股≤15% · 单行业≤35%</em></div>}
            <div className="radar-rules radar-rules-top portfolio-rules"><h3>完整买入与卖出纪律</h3><div className="radar-rule-columns"><section><b>买入纪律</b>{disciplineRules.filter(rule=>rule.side==="buy").map(rule=><p key={rule.rule_key}><strong>{rule.rule_name}</strong><span>{rule.description}</span></p>)}</section><section><b>卖出纪律</b>{disciplineRules.filter(rule=>rule.side==="sell").map(rule=><p key={rule.rule_key}><strong>{rule.rule_name}</strong><span>{rule.description}</span></p>)}</section></div></div>
            <div className="portfolio-search"><label className="search"><span>⌕</span><input value={portfolioQuery} onChange={e=>setPortfolioQuery(e.target.value)} placeholder="搜索股票名称 / 代码 / 拼音并加入持仓"/></label>
              {!!portfolioResults.length&&<div className="portfolio-search-results">{portfolioResults.map(stock=><button key={stock.code} onClick={()=>addPosition(stock)}><span><b>{stock.name}</b><small>{stock.code} · {stock.industry_name||stock.market}</small></span><strong>＋ 加入持仓</strong></button>)}</div>}
            </div>
            <div className="portfolio-list"><div className="portfolio-row portfolio-head"><span>持仓股票</span><span>价格 / 涨跌</span><span>买入信号</span><span>卖出信号</span><span>匹配策略与依据</span><span>操作</span></div>
              {portfolio.map(item=><div className="portfolio-position" key={item.code}>
                <div className="portfolio-row"><button className="portfolio-stock" onClick={()=>{setSelected(item);setActiveView("market")}}><b>{item.name}</b><small>{item.code} · {item.industry_name||"未分类"}</small></button><span><b>{Number(item.price||0).toFixed(2)}</b><small className={Number(item.change)<0?"trend-down":"trend-up"}>{Number(item.change)>0?"+":""}{Number(item.change||0).toFixed(2)}%</small></span><span><strong className={item.buy_level==="禁买"?"level-block":"level-buy"}>{item.buy_level||"待计算"}</strong><small>评分 {Number(item.buy_score||0).toFixed(0)}</small></span><span><strong className={item.sell_level==="退出"?"level-exit":""}>{item.sell_level||"待计算"}</strong><small>评分 {Number(item.sell_score||0).toFixed(0)}</small></span><span><b>{item.buy_model||"无匹配模型"}</b><small>{[...(item.buy_signals||[]),...(item.sell_signals||[]),...(item.blockers||[])].slice(0,3).join("；")||"暂无强信号"}</small></span><button className="portfolio-remove" onClick={()=>removePosition(item.code)}>移出</button></div>
                <details className="portfolio-discipline" open>
                  <summary>详细买入与卖出纪律 <small>点击收起</small></summary>
                  <div className="portfolio-discipline-grid">
                    <section><b>买入纪律 · {item.buy_level||"待计算"}</b><small>评分 {Number(item.buy_score||0).toFixed(0)}</small><h4>已满足条件</h4>{(item.buy_signals||[]).map(signal=><p className="discipline-hit" key={signal}>✓ {signal}</p>)}{!(item.buy_signals||[]).length&&<p>暂无已满足的买入条件</p>}<h4>阻断与禁买项</h4>{(item.blockers||[]).map(signal=><p className="discipline-block" key={signal}>! {signal}</p>)}{!(item.blockers||[]).length&&<p>当前未触发阻断项</p>}</section>
                    <section><b>卖出纪律 · {item.sell_level||"待计算"}</b><small>评分 {Number(item.sell_score||0).toFixed(0)}</small><h4>已触发条件</h4>{(item.sell_signals||[]).map(signal=><p className="discipline-block" key={signal}>! {signal}</p>)}{!(item.sell_signals||[]).length&&<p>当前未触发卖出条件</p>}<h4>风险价格</h4><p>最终防守位 <strong>{Number(item.defense_price||0).toFixed(2)}</strong></p><p>1.5ATR止损 <strong>{Number(item.stop_atr_price||0).toFixed(2)}</strong></p></section>
                    <section><b>匹配模型</b><small>{item.buy_model||"无匹配模型"}</small><h4>动态仓位建议</h4>{(()=>{const advice=positionModel.positions.find(x=>x.code===item.code);return advice?<><p>目标仓位 <strong>{advice.target_weight_pct.toFixed(2)}%</strong></p><p>ATR风险仓位 {advice.risk_weight_pct.toFixed(2)}% · 防守距离 {advice.stop_distance_pct.toFixed(2)}%</p><p>行业系数 {advice.industry_factor} · 信号系数 {advice.signal_factor}</p></>:<p>暂无仓位建议</p>})()}<h4>执行提示</h4><p>先核对市场环境和行业风险，再结合量价结构确认；防守位触发时优先执行纪律。</p><h4>数据日期</h4><p>{item.trade_date||"暂无计算日期"}</p></section>
                  </div>
                </details>
              </div>)}
              {!portfolio.length&&<p className="empty">持仓股票池为空，请使用上方搜索加入股票。</p>}
            </div>
          </div>}
          {activeView==="backtest"&&<div className="backtest-panel">
            <div className="section-title"><div><h2>三年交易纪律回测</h2><small>历史当日信号 → 下一交易日开盘模拟成交 → 多周期有效率评估</small></div><span>{backtestData.run?`${backtestData.run.start_date} 至 ${backtestData.run.end_date}`:"尚未运行"}</span></div>
            <div className="backtest-selector">
              <div className="backtest-search-wrap"><label className="backtest-search"><span>⌕</span><input value={backtestQuery} onChange={event=>setBacktestQuery(event.target.value)} placeholder="搜索股票名称 / 代码 / 拼音，支持多选"/>{backtestQuery&&<button onClick={()=>setBacktestQuery("")} aria-label="清空搜索">×</button>}</label>{backtestResults.length>0&&<div className="backtest-search-results">{backtestResults.map(stock=><button key={stock.code} onClick={()=>{setBacktestStocks(current=>[...current,stock]);setBacktestQuery("");setBacktestResults([])}}><b>{stock.name}</b><span>{stock.code} · {stock.industry_name||stock.market}</span><em>＋ 加入回测</em></button>)}</div>}</div>
              <button className="backtest-run" disabled={!backtestStocks.length||backtestRunning} onClick={runSelectedBacktest}>{backtestRunning?"正在回测…":`回测选中股票（${backtestStocks.length}）`}</button>
              {!!backtestStocks.length&&<button className="backtest-clear" disabled={backtestRunning} onClick={()=>setBacktestStocks([])}>清空选择</button>}
              <div className="backtest-stock-chips">{backtestStocks.map(stock=><button key={stock.code} disabled={backtestRunning} onClick={()=>setBacktestStocks(current=>current.filter(item=>item.code!==stock.code))}><b>{stock.name}</b><small>{stock.code}</small><span>×</span></button>)}</div>
              {backtestError&&<p className="backtest-error">{backtestError}</p>}
            </div>
            {backtestData.run?<>
              <div className="backtest-notes"><b>{backtestData.run.parameters?.scope==="selected"?"选股回测":"全市场回测"} · 无未来数据泄漏</b><span>买入：仅“买入确认”且无阻断项；卖出：减仓/退出、跌破买入防守位、时间止损或移动止盈；已计佣金、印花税和滑点。</span><em>{backtestData.run.stock_count}只股票 · {backtestData.run.event_count}个信号 · {backtestData.run.trade_count}笔交易</em></div>
              <div className="backtest-cards"><div><small>已闭合交易</small><b>{Number(backtestData.trades.closed_count||0).toFixed(0)}</b></div><div><small>平均收益</small><b className={Number(backtestData.trades.avg_return_pct)<0?"trend-down":"trend-up"}>{Number(backtestData.trades.avg_return_pct||0).toFixed(2)}%</b></div><div><small>交易胜率</small><b>{Number(backtestData.trades.win_rate_pct||0).toFixed(1)}%</b></div><div><small>平均持有</small><b>{Number(backtestData.trades.avg_holding_days||0).toFixed(1)}日</b></div><div><small>最佳 / 最差</small><b>{Number(backtestData.trades.best_return_pct||0).toFixed(1)}% / {Number(backtestData.trades.worst_return_pct||0).toFixed(1)}%</b></div></div>
              <div className="backtest-section"><h3>买入与卖出信号的多周期有效率</h3><p>买入收益为信号后实际涨跌；卖出收益为“规避收益”，正数表示卖出后股价下跌、该卖出有效。</p><div className="backtest-horizon"><div className="backtest-horizon-row head"><b>周期</b><span>买入平均收益</span><span>买入胜率</span><span>买入样本</span><span>卖出规避收益</span><span>卖出有效率</span><span>卖出样本</span></div>{["1月","3月","半年","1年","2年","3年"].map(horizon=>{const buy=backtestData.summaries.find(x=>x.side==="buy"&&x.horizon===horizon),sell=backtestData.summaries.find(x=>x.side==="sell"&&x.horizon===horizon);return <div className="backtest-horizon-row" key={horizon}><b>{horizon}</b><span className={Number(buy?.avg_return_pct)<0?"trend-down":"trend-up"}>{buy?`${Number(buy.avg_return_pct).toFixed(2)}%`:"—"}</span><span>{buy?`${Number(buy.win_rate_pct).toFixed(1)}%`:"—"}</span><span>{buy?.sample_count||0}</span><span className={Number(sell?.avg_return_pct)<0?"trend-down":"trend-up"}>{sell?`${Number(sell.avg_return_pct).toFixed(2)}%`:"—"}</span><span>{sell?`${Number(sell.win_rate_pct).toFixed(1)}%`:"—"}</span><span>{sell?.sample_count||0}</span></div>})}</div></div>
              <div className="backtest-columns"><section><h3>策略分组表现</h3>{backtestData.strategies.slice(0,12).map(item=><div className="strategy-result" key={`${item.side}-${item.strategy}`}><b>{item.side==="buy"?"买":"卖"} · {item.strategy}</b><span>{item.samples}笔</span><em className={Number(item.avg_return_pct)<0?"trend-down":"trend-up"}>交易收益 {Number(item.avg_return_pct).toFixed(2)}% · 胜率 {Number(item.win_rate_pct).toFixed(1)}%</em></div>)}</section><section><h3>最近模拟节点</h3>{backtestData.events.slice(0,20).map(item=><div className="backtest-event" key={`${item.code}-${item.side}-${item.signal_date}`}><b className={item.side==="buy"?"trend-up":"trend-down"}>{item.side==="buy"?"买入":"卖出"}</b><span>{item.name} {item.code}</span><em>{item.signal_date} → {item.execution_date} · {item.strategy_name}</em><small>{(item.matched_rules||[]).join("；")}</small></div>)}</section></div>
              <div className="position-model"><h3>动态仓位管理模型 · 待纳入对照回测</h3><p><b>目标仓位 = 市场风险预算 × 行业轮动系数 × 个股信号系数 × 波动率调整</b></p><div><span>市场风险预算：正常100%、谨慎60%、系统风险30%</span><span>行业系数：前3名1.2，前10名1.0，其余0.5，高风险0</span><span>个股系数：买入确认1.0、候选0.5、观察0</span><span>ATR反算：单笔风险≤账户0.8%，单股≤15%，单行业≤35%</span></div></div>
            </>:<p className="empty">三年回测正在运行或尚未生成结果。</p>}
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
