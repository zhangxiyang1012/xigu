import { NextRequest, NextResponse } from "next/server";

export const revalidate = 900;

export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code") ?? "600519";
  if (!/^\d{6}$/.test(code)) {
    return NextResponse.json({ error: "股票代码无效" }, { status: 400 });
  }

  const secid = `${code.startsWith("6") || code.startsWith("68") ? 1 : 0}.${code}`;
  const symbol = `${code.startsWith("6") ? "sh" : code.startsWith("8") || code.startsWith("4") ? "bj" : "sz"}${code}`;

  try {
    // 腾讯历史行情目前对云端出口更稳定，优先使用；字段不足的成交额
    // 用收盘价 × 成交量 × 100 股近似，换手率留空为 0。
    const tencentUrl = `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${symbol},day,,,220,qfq`;
    const tencentRes = await fetch(tencentUrl, {
      headers: { Referer: "https://gu.qq.com/" },
      next: { revalidate: 900 },
      signal: AbortSignal.timeout(10_000),
    });
    if (tencentRes.ok) {
      const json = (await tencentRes.json()) as {
        data?: Record<string, { qfqday?: string[][]; day?: string[][] }>;
      };
      const sourceRows =
        json.data?.[symbol]?.qfqday ?? json.data?.[symbol]?.day ?? [];
      const rows = sourceRows.map((v, index) => {
        const close = Number(v[2]);
        const previousClose =
          index > 0 ? Number(sourceRows[index - 1][2]) : close;
        const volume = Number(v[5]);
        return {
          date: v[0],
          open: Number(v[1]),
          close,
          high: Number(v[3]),
          low: Number(v[4]),
          volume,
          amount: close * volume * 100,
          change: previousClose ? ((close / previousClose) - 1) * 100 : 0,
          turnover: 0,
        };
      });
      if (rows.length) {
        return NextResponse.json({
          source: "腾讯证券公开行情",
          rows,
        });
      }
    }

    // 腾讯不可用时回退至东方财富。
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
