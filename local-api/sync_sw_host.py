"""在Mac宿主机从申万宏源官方接口同步一、二级行业成分。"""
import json
import subprocess
import time
import urllib.parse

BASE = "https://www.swsresearch.com/institute-sw/api/index_publish"
TARGET = "http://127.0.0.1:8000/api/import/sw-industries"


def get_json(url: str) -> dict:
    error = None
    for attempt in range(4):
        try:
            body = subprocess.check_output([
                "/usr/bin/curl", "--fail", "--silent", "--show-error",
                "--max-time", "30", "-H", "User-Agent: Mozilla/5.0", url,
            ])
            return json.loads(body)
        except Exception as exc:
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"申万接口连续失败：{error}")


def indexes(level: str) -> list[dict]:
    query = urllib.parse.urlencode({"page": 1, "page_size": 500, "indextype": level})
    return get_json(f"{BASE}/current/?{query}")["data"]["results"]


def members(index_code: str) -> list[dict]:
    query = urllib.parse.urlencode({
        "swindexcode": index_code, "page": 1, "page_size": 10000,
    })
    return get_json(f"{BASE}/details/component_stocks/?{query}")["data"]["results"]


def post(items: list[dict]) -> dict:
    body = json.dumps({"items": items}, ensure_ascii=False).encode()
    response = subprocess.check_output([
        "/usr/bin/curl", "--fail", "--silent", "--show-error", "--max-time", "120",
        "-H", "Content-Type: application/json", "--data-binary", "@-", TARGET,
    ], input=body)
    return json.loads(response)


def main():
    assignments: dict[str, dict] = {}
    levels = [("一级行业", "l1"), ("二级行业", "l2")]
    total_indexes = sum(len(indexes(level)) for level, _ in levels)
    done = 0
    for level, prefix in levels:
        for industry in indexes(level):
            code, name = industry["swindexcode"], industry["swindexname"]
            for stock in members(code):
                stock_code = str(stock["stockcode"]).zfill(6)
                item = assignments.setdefault(stock_code, {"code": stock_code})
                item[f"{prefix}_code"] = code
                item[f"{prefix}_name"] = name
            done += 1
            print(f"\r申万行业 {done}/{total_indexes}，已识别 {len(assignments)} 只", end="", flush=True)
            time.sleep(0.05)
    result = post(list(assignments.values()))
    print(f"\n同步完成：申万二级覆盖 {result['l2_covered']}/{result['total']} 只，行业 {result['industries']} 个")


if __name__ == "__main__":
    main()
