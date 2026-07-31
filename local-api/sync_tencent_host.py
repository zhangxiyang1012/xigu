"""从腾讯证券批量抓取全A股最新快照并提交到本地PostgreSQL。"""

import json
import math
import re
import subprocess
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

API = "http://127.0.0.1:8000"
BATCH_SIZE = 80
SHANGHAI = ZoneInfo("Asia/Shanghai")


def curl(url: str, body: Optional[bytes] = None) -> bytes:
    command = [
        "/usr/bin/curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "30",
        "--noproxy",
        "*",
    ]
    if body is not None:
        command += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    else:
        command += ["-H", "Referer: https://gu.qq.com/"]
    command.append(url)
    return subprocess.check_output(command, input=body)


def load_symbols() -> list[str]:
    first = json.loads(curl(f"{API}/api/stocks?page=1").decode())
    pages = math.ceil(first["total"] / first["pageSize"])
    stocks = list(first["stocks"])
    for page in range(2, pages + 1):
        payload = json.loads(curl(f"{API}/api/stocks?page={page}").decode())
        stocks.extend(payload["stocks"])
    return [
        ("sh" if item["code"].startswith("6") else
         "bj" if item["code"].startswith(("4", "8", "9")) else "sz")
        + item["code"]
        for item in stocks
    ]


def parse_quote(line: str) -> Optional[dict]:
    match = re.match(r'v_[^=]+="(.*)";', line.strip())
    if not match:
        return None
    values = match.group(1).split("~")
    if len(values) < 39 or not values[2].isdigit() or not values[30]:
        return None
    try:
        traded_at = datetime.strptime(values[30], "%Y%m%d%H%M%S").replace(
            tzinfo=SHANGHAI
        )
        amount_parts = values[35].split("/")
        return {
            "f12": values[2],
            "f14": values[1],
            "f2": float(values[3]),
            "f3": float(values[32]),
            "f5": int(float(values[36])),
            "f6": float(amount_parts[2]) if len(amount_parts) > 2 else 0,
            "f8": float(values[38]),
            "f17": float(values[5]),
            "f15": float(values[33]),
            "f16": float(values[34]),
            "f124": int(traded_at.timestamp()),
        }
    except (ValueError, IndexError):
        return None


def main():
    symbols = load_symbols()
    written = 0
    for start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[start:start + BATCH_SIZE]
        raw = curl(f"https://qt.gtimg.cn/q={','.join(batch)}").decode(
            "gb18030", errors="replace"
        )
        items = [item for line in raw.splitlines() if (item := parse_quote(line))]
        if items:
            body = json.dumps({"items": items}, ensure_ascii=False).encode()
            result = json.loads(curl(f"{API}/api/import/snapshot", body).decode())
            written += int(result["written"])
        print(
            f"\r同步 {min(start + BATCH_SIZE, len(symbols))}/{len(symbols)}，"
            f"已写入 {written}",
            end="",
            flush=True,
        )
    print(f"\n完成：{written} 只股票最新快照已写入本地PostgreSQL")


if __name__ == "__main__":
    main()
