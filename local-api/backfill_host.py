"""用Mac可访问的东方财富接口补齐腾讯未覆盖的历史行情。"""

import concurrent.futures
import http.client
import json
import os
import subprocess
import threading
import time
from datetime import date, timedelta
from typing import Optional

API = "http://127.0.0.1:8000"
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "1095"))
MIN_TRADING_DAYS = int(os.getenv("BACKFILL_MIN_DAYS", "700"))
START_DATE = date.today() - timedelta(days=BACKFILL_DAYS)
KLINE_LIMIT = min(5000, int(BACKFILL_DAYS * 0.75) + 100)
CONCURRENCY = int(os.getenv("BACKFILL_CONCURRENCY", "6"))
lock = threading.Lock()
thread_state = threading.local()
completed = 0
written = 0
failed: list[str] = []


def curl_json(url: str, body: Optional[bytes] = None) -> dict:
    command = [
        "/usr/bin/curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "35",
    ]
    if body is not None:
        command += [
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
        ]
    command.append(url)
    output = subprocess.check_output(command, input=body)
    return json.loads(output)


def post_history(code: str, rows: list[dict]) -> int:
    body = json.dumps({"code": code, "rows": rows}).encode()
    for attempt in range(4):
        try:
            connection = getattr(thread_state, "connection", None)
            if connection is None:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", 8000, timeout=90
                )
                thread_state.connection = connection
            connection.request(
                "POST",
                "/api/import/history",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Connection": "keep-alive",
                },
            )
            response = connection.getresponse()
            payload = response.read()
            if response.status != 200:
                raise RuntimeError(f"本地API {response.status}: {payload[:200]!r}")
            return int(json.loads(payload)["written"])
        except Exception:
            connection = getattr(thread_state, "connection", None)
            if connection is not None:
                connection.close()
            thread_state.connection = None
            if attempt == 3:
                raise
            time.sleep(0.5 * (attempt + 1))
    return 0


def fetch_history(code: str) -> list[dict]:
    secid = f"{1 if code.startswith('6') else 0}.{code}"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt=1&lmt={KLINE_LIMIT}&end=20500101"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    for attempt in range(3):
        try:
            data = curl_json(url).get("data") or {}
            rows = []
            for line in data.get("klines") or []:
                values = line.split(",")
                if date.fromisoformat(values[0]) < START_DATE:
                    continue
                rows.append(
                    {
                        "date": values[0],
                        "open": values[1],
                        "close": values[2],
                        "high": values[3],
                        "low": values[4],
                        "volume": values[5],
                        "amount": values[6],
                        "change": values[8],
                        "turnover": values[10],
                    }
                )
            return rows
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    return []


def process(code: str, total: int):
    global completed, written
    try:
        rows = fetch_history(code)
        if not rows:
            raise RuntimeError("无历史数据")
        count = post_history(code, rows)
        with lock:
            written += count
    except Exception as exc:
        with lock:
            failed.append(f"{code}: {exc}")
    finally:
        with lock:
            completed += 1
            if completed % 50 == 0 or completed == total:
                print(
                    f"补齐 {completed}/{total}，写入 {written} 行，"
                    f"仍无数据 {len(failed)} 只",
                    flush=True,
                )


def main():
    pending = curl_json(
        f"{API}/api/backfill/pending?min_days={MIN_TRADING_DAYS}"
        f"&range_days={BACKFILL_DAYS}"
    )["stocks"]
    codes = [item["code"].strip() for item in pending]
    print(f"需要补齐 {len(codes)} 只股票", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [executor.submit(process, code, len(codes)) for code in codes]
        for future in futures:
            future.result()
    print(
        f"补齐完成：写入 {written} 行，仍无数据 {len(failed)} 只",
        flush=True,
    )
    if failed:
        print("\n".join(failed[:100]))


if __name__ == "__main__":
    main()
