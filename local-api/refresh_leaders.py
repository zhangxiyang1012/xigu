"""计算策略PDF明确提及的行业核心股与所属行业的联动关系。"""
import asyncio, json, math, os
from statistics import mean
import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]
LEADERS = {
    "688256": ("半导体", "科技核心/板块风向标", ["7月27日纪要", "7月30日纪要"]),
    "688981": ("半导体", "半导体核心权重", ["7月28日纪要", "7月30日纪要"]),
    "603986": ("半导体", "科技焦点股", ["7月29日纪要"]),
    "600584": ("半导体", "半导体核心股", ["7月30日纪要"]),
    "300308": ("通信设备", "光通信核心股", ["7月29日纪要"]),
    "300502": ("通信设备", "光通信核心股", ["7月29日纪要"]),
    "601869": ("通信设备", "光通信焦点股", ["7月29日纪要"]),
    "603019": ("计算机设备", "算力核心股", ["7月30日纪要"]),
    "301165": ("通信设备", "网络设备核心股", ["7月30日纪要"]),
    "600909": ("证券Ⅱ", "证券情绪观察股", ["7月27日纪要", "7月28日纪要"]),
    "300750": ("电池", "新能源权重/指数调节股", ["7月27日纪要", "7月28日纪要", "7月30日纪要"]),
    "002594": ("乘用车", "新能源车权重股", ["7月30日纪要"]),
    "600519": ("白酒Ⅱ", "基金长线核心权重", ["7月29日纪要"]),
    "600050": ("通信服务", "运营商权重股", ["7月29日纪要", "7月30日纪要"]),
    "600941": ("通信服务", "运营商权重股", ["7月29日纪要", "7月30日纪要"]),
    "601728": ("通信服务", "运营商权重股", ["7月29日纪要", "7月30日纪要"]),
}

def corr(a, b):
    if len(a) < 8 or len(a) != len(b): return 0.0
    ax, bx = mean(a), mean(b)
    den = math.sqrt(sum((x-ax)**2 for x in a)*sum((y-bx)**2 for y in b))
    return sum((x-ax)*(y-bx) for x,y in zip(a,b))/den if den else 0.0

async def refresh(db):
    await db.executemany("UPDATE stocks SET industry_name=coalesce(nullif(industry_name,''),$2) WHERE code=$1",
                         [(c,d[0]) for c,d in LEADERS.items()])
    ir = await db.fetch("SELECT industry_name,trade_date,avg_change_pct FROM industry_daily_metrics ORDER BY trade_date")
    industries = {}
    for r in ir: industries.setdefault(r["industry_name"], {})[r["trade_date"]] = float(r["avg_change_pct"])
    values=[]
    for code,(industry,role,sources) in LEADERS.items():
        qs=list(reversed(await db.fetch("""SELECT trade_date,close,change_pct,volume,
          avg(close) over(order by trade_date rows between 19 preceding and current row) ma20,
          avg(volume) over(order by trade_date rows between 19 preceding and current row) volume_ma20
          FROM daily_quotes WHERE stock_code=$1 ORDER BY trade_date DESC LIMIT 120""",code)))
        pairs=[(q,industries.get(industry,{}).get(q["trade_date"])) for q in qs]
        pairs=[p for p in pairs if p[1] is not None][-90:]
        sr=[float(q["change_pct"]) for q,_ in pairs]; br=[r for _,r in pairs]
        base=corr(sr,br); match=100*sum((a>=0)==(b>=0) for a,b in zip(sr,br))/max(1,len(sr))
        amp=(mean(abs(x) for x in sr)/mean(abs(x) for x in br)) if sr and any(br) else 0
        best_lag,best_corr=0,base
        for lag in range(-5,6):
            candidate=corr(sr[:-lag],br[lag:]) if lag>0 else corr(sr[-lag:],br[:lag]) if lag<0 else base
            if candidate>best_corr: best_lag,best_corr=lag,candidate
        signal,date,reasons="观察",None,[]
        if len(qs)>1 and qs[-1]["ma20"]:
            now,prev=qs[-1],qs[-2]
            if prev["close"]<=prev["ma20"] and now["close"]>now["ma20"]:
                signal,date="转强确认",now["trade_date"]; reasons.append("收盘价上穿20日线")
            elif prev["close"]>=prev["ma20"] and now["close"]<now["ma20"]:
                signal,date="转弱确认",now["trade_date"]; reasons.append("收盘价跌破20日线")
            else: signal="偏强延续" if now["close"]>now["ma20"] else "偏弱观察"
            vr=float(now["volume"])/float(now["volume_ma20"] or 1)
            if vr>=1.3: reasons.append(f"量能为20日均量{vr:.1f}倍")
            if best_lag>0 and best_corr>=.35: reasons.append(f"历史上约领先行业{best_lag}日")
        values.append((code,industry,role,json.dumps(sources,ensure_ascii=False),base,match,amp,best_lag,best_corr,signal,date,json.dumps(reasons or ["暂无明确拐点确认"],ensure_ascii=False)))
    await db.execute("TRUNCATE industry_leader_analysis")
    await db.executemany("""INSERT INTO industry_leader_analysis(stock_code,industry_name,strategy_role,
      source_mentions,correlation_90d,direction_match_pct,amplitude_ratio,lead_lag_days,
      lead_lag_correlation,turning_signal,turning_date,turning_reasons)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",values)
    return len(values)

async def main():
    db=await asyncpg.connect(DATABASE_URL)
    try: count=await refresh(db)
    finally: await db.close()
    print(f"行业龙头联动分析完成：{count} 只")
if __name__=="__main__": asyncio.run(main())
