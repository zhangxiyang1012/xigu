import { NextResponse } from "next/server";

export const revalidate = 300;

const market = (code: string) => code.startsWith("68") ? "科创板" : code.startsWith("30") ? "创业板" : code.startsWith("6") ? "沪市" : code.startsWith("8") || code.startsWith("4") ? "北交所" : "深市";

export async function GET(req: Request) {
  try {
    const page = Math.max(1, Number(new URL(req.url).searchParams.get("page")) || 1);
    const url = `https://push2.eastmoney.com/api/qt/clist/get?pn=${page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f2,f3,f5,f6,f12,f14`;
    const res = await fetch(url, { headers: { Referer: "https://quote.eastmoney.com/" }, next: { revalidate: 300 } });
    if (!res.ok) throw new Error(`upstream ${res.status}`);
    const json = await res.json() as { data?: { total: number; diff: Record<string, string | number>[] } };
    const stocks = (json.data?.diff ?? []).map((d) => ({
      code: String(d.f12), name: String(d.f14), market: market(String(d.f12)),
      price: Number(d.f2) || 0, change: Number(d.f3) || 0, volume: Number(d.f5) || 0, amount: Number(d.f6) || 0,
    }));
    return NextResponse.json({ source: "东方财富公开行情", updatedAt: new Date().toISOString(), page, pageSize: 100, total: json.data?.total ?? stocks.length, stocks });
  } catch {
    return NextResponse.json({ error: "免费行情源暂时不可用" }, { status: 502 });
  }
}
