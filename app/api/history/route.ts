import { NextRequest, NextResponse } from "next/server";

export const revalidate = 900;

export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code") ?? "600519";
  if (!/^\d{6}$/.test(code)) {
    return NextResponse.json({ error: "股票代码无效" }, { status: 400 });
  }

  const secid = `${code.startsWith("6") || code.startsWith("68") ? 1 : 0}.${code}`;

  try {
    // 页面最多展示180日；额外保留40日用于均线与指标预热。
    const url = `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${secid}&klt=101&fqt=1&lmt=220&end=20500101&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61`;
    const res = await fetch(url, {
      headers: { Referer: "https://quote.eastmoney.com/" },
      next: { revalidate: 900 },
      signal: AbortSignal.timeout(12_000),
    });
    if (!res.ok) throw new Error(`upstream ${res.status}`);

    const json = (await res.json()) as {
      data?: { name: string; klines: string[] };
    };
    const rows = (json.data?.klines ?? []).map((line) => {
      const v = line.split(",");
      return {
        date: v[0],
        open: +v[1],
        close: +v[2],
        high: +v[3],
        low: +v[4],
        volume: +v[5],
        amount: +v[6],
        change: +v[8],
        turnover: +v[10],
      };
    });
    if (!rows.length) throw new Error("empty history");

    return NextResponse.json({
      source: "东方财富公开行情",
      name: json.data?.name,
      rows,
    });
  } catch {
    return NextResponse.json(
      { error: "历史行情暂时不可用，请稍后重试" },
      { status: 502 },
    );
  }
}
