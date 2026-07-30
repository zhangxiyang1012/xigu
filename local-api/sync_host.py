"""从Mac宿主机抓取东方财富全A股快照，分批提交给本地API入库。"""

import json
import subprocess
import sys
import time
import urllib.parse

SOURCE = "https://push2.eastmoney.com/api/qt/clist/get"
TARGET = "http://127.0.0.1:8000/api/import/snapshot"
MARKETS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
FIELDS = "f2,f3,f5,f6,f8,f12,f14,f15,f16,f17,f100"


def get_json(url: str) -> dict:
    # macOS curl 会优先使用当前网络可达的 IPv6 节点；系统 Python
    # 在部分网络环境下会选择不可用的 IPv4 节点。
    body = subprocess.check_output(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "-H",
            "User-Agent: Mozilla/5.0",
            "-H",
            "Referer: https://quote.eastmoney.com/",
            url,
        ]
    )
    return json.loads(body)


def post_items(items: list[dict]) -> int:
    body = json.dumps({"items": items}).encode()
    response = subprocess.check_output(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "60",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            TARGET,
        ],
        input=body,
    )
    return int(json.loads(response)["written"])


def fetch_page(page: int) -> dict:
    query = urllib.parse.urlencode(
        {
            "pn": page,
            "pz": 100,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": MARKETS,
            "fields": FIELDS,
        }
    )
    return get_json(f"{SOURCE}?{query}").get("data") or {}


def main():
    first = fetch_page(1)
    total = int(first.get("total") or 0)
    pages = (total + 99) // 100
    written = 0
    for page in range(1, pages + 1):
        data = first if page == 1 else fetch_page(page)
        items = data.get("diff") or []
        written += post_items(items)
        print(f"\r同步 {page}/{pages} 页，累计 {written}/{total} 只", end="", flush=True)
        time.sleep(0.08)
    print(f"\n完成：已提交 {written} 只A股到本地PostgreSQL")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n同步失败：{exc}", file=sys.stderr)
        raise
