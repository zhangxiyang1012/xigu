import asyncio, os
from contextlib import asynccontextmanager
from datetime import date
import asyncpg, httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DB=os.environ["DATABASE_URL"]
FS="m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
pool:asyncpg.Pool
def market(code): return "科创板" if code.startswith("68") else "创业板" if code.startswith("30") else "沪市" if code.startswith("6") else "北交所" if code.startswith(("8","4")) else "深市"
async def init_db():
  global pool; pool=await asyncpg.create_pool(DB,min_size=2,max_size=12)
  async with pool.acquire() as c:
    sql=open("schema.sql").read()
    for statement in [s.strip() for s in sql.split(";") if s.strip()]: await c.execute(statement)
async def fetch_page(page:int):
  url=f"https://push2.eastmoney.com/api/qt/clist/get?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs={FS}&fields=f2,f3,f5,f6,f7,f8,f12,f14,f15,f16,f17,f18"
  async with httpx.AsyncClient(timeout=20,headers={"Referer":"https://quote.eastmoney.com/"}) as h: j=(await h.get(url)).json()
  return j.get("data") or {"total":0,"diff":[]}
async def persist_snapshot(items):
  today=date.today()
  async with pool.acquire() as c, c.transaction():
    await c.executemany("INSERT INTO stocks(code,name,market,updated_at) VALUES($1,$2,$3,now()) ON CONFLICT(code) DO UPDATE SET name=excluded.name,market=excluded.market,updated_at=now()",[(str(x["f12"]),str(x["f14"]),market(str(x["f12"]))) for x in items])
    rows=[(str(x["f12"]),today,float(x.get("f17") or 0),float(x.get("f15") or 0),float(x.get("f16") or 0),float(x.get("f2") or 0),float(x.get("f3") or 0),int(x.get("f5") or 0),float(x.get("f6") or 0),float(x.get("f8") or 0)) for x in items if float(x.get("f2") or 0)>0]
    await c.executemany("INSERT INTO daily_quotes(stock_code,trade_date,open,high,low,close,change_pct,volume,amount,turnover) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT(stock_code,trade_date) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,change_pct=excluded.change_pct,volume=excluded.volume,amount=excluded.amount,turnover=excluded.turnover,updated_at=now()",rows)
async def sync_all():
  async with pool.acquire() as c: run=await c.fetchval("INSERT INTO sync_runs(sync_type,trade_date,status) VALUES('daily',current_date,'running') RETURNING id")
  count=0
  try:
    first=await fetch_page(1); pages=(first["total"]+99)//100
    for p in range(1,pages+1):
      data=first if p==1 else await fetch_page(p); await persist_snapshot(data["diff"]);count+=len(data["diff"]);await asyncio.sleep(.08)
    async with pool.acquire() as c: await c.execute("UPDATE sync_runs SET status='success',rows_written=$1,finished_at=now() WHERE id=$2",count,run)
  except Exception as e:
    async with pool.acquire() as c: await c.execute("UPDATE sync_runs SET status='failed',error=$1,finished_at=now() WHERE id=$2",str(e),run)
@asynccontextmanager
async def lifespan(app):
  await init_db(); scheduler=AsyncIOScheduler(timezone="Asia/Shanghai");scheduler.add_job(sync_all,"cron",hour=18,minute=10);scheduler.start();yield;scheduler.shutdown();await pool.close()
app=FastAPI(title="析股本地行情API",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv("CORS_ORIGINS","http://localhost:3000").split(","),allow_methods=["GET","POST"],allow_headers=["*"])
@app.get("/health")
async def health(): return {"ok":True}
@app.get("/api/stocks")
async def stocks(page:int=1):
  data=await fetch_page(max(1,page));await persist_snapshot(data["diff"])
  return {"page":page,"pageSize":100,"total":data["total"],"stocks":[{"code":str(x["f12"]),"name":str(x["f14"]),"market":market(str(x["f12"])),"price":float(x.get("f2") or 0),"change":float(x.get("f3") or 0),"volume":int(x.get("f5") or 0),"amount":float(x.get("f6") or 0)} for x in data["diff"]]}
@app.get("/api/search")
async def search(q:str):
  async with pool.acquire() as c: rows=await c.fetch("SELECT code,name,market FROM stocks WHERE code LIKE $1 OR name LIKE $1 ORDER BY code LIMIT 30",f"%{q}%")
  return {"stocks":[{"code":r["code"],"name":r["name"],"market":r["market"],"price":0,"change":0,"volume":0,"amount":0} for r in rows]}
@app.get("/api/history")
async def history(code:str):
  if len(code)!=6 or not code.isdigit(): raise HTTPException(400,"股票代码无效")
  secid=f"{1 if code.startswith(('6','68')) else 0}.{code}";url=f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=1&lmt=5000&end=20500101&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
  async with httpx.AsyncClient(timeout=30,headers={"Referer":"https://quote.eastmoney.com/"}) as h: data=(await h.get(url)).json().get("data") or {}
  parsed=[]
  for line in data.get("klines",[]):
    v=line.split(",");parsed.append({"date":v[0],"open":float(v[1]),"close":float(v[2]),"high":float(v[3]),"low":float(v[4]),"volume":int(float(v[5])),"amount":float(v[6]),"change":float(v[8]),"turnover":float(v[10])})
  async with pool.acquire() as c, c.transaction():
    await c.execute("INSERT INTO stocks(code,name,market,updated_at) VALUES($1,$2,$3,now()) ON CONFLICT(code) DO UPDATE SET name=excluded.name,updated_at=now()",code,data.get("name",code),market(code))
    await c.executemany("INSERT INTO daily_quotes(stock_code,trade_date,open,high,low,close,change_pct,volume,amount,turnover) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT(stock_code,trade_date) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,change_pct=excluded.change_pct,volume=excluded.volume,amount=excluded.amount,turnover=excluded.turnover,updated_at=now()",[(code,r["date"],r["open"],r["high"],r["low"],r["close"],r["change"],r["volume"],r["amount"],r["turnover"]) for r in parsed])
  return {"source":"本地PostgreSQL + 东方财富增量","name":data.get("name"),"rows":parsed}
@app.get("/api/industries")
async def industries():
  url="https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fid=f7&fs=m:90+t:2+f:!50&fields=f2,f3,f6,f7,f8,f12,f14,f104,f105,f106"
  async with httpx.AsyncClient(timeout=20,headers={"Referer":"https://quote.eastmoney.com/"}) as h: items=((await h.get(url)).json().get("data") or {}).get("diff",[])
  return {"industries":[{"code":str(x["f12"]),"name":str(x["f14"]),"index":float(x.get("f2") or 0),"change":float(x.get("f3") or 0),"amount":float(x.get("f6") or 0),"amplitude":float(x.get("f7") or 0),"turnover":float(x.get("f8") or 0),"up":int(x.get("f104") or 0),"down":int(x.get("f105") or 0),"flat":int(x.get("f106") or 0),"direction":"涨" if float(x.get("f3") or 0)>=0 else "跌","streakDays":1} for x in items]}
@app.post("/api/sync/daily")
async def manual_sync(): asyncio.create_task(sync_all());return {"started":True}
