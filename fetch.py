"""tickers.json の銘柄の終値を yfinance で取り、site/data/ に JSON で保存する。

GitHub Actions から毎日呼ばれる。手元でも `python fetch.py` で動く。
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).parent
OUT = ROOT / "site" / "data"
JST = timezone(timedelta(hours=9))


def safe_name(code: str) -> str:
    # ^N225 のような記号入りのコードもファイル名にできるようにする
    return "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in code) + ".json"


def fetch_one(code: str):
    df = yf.download(code, period="5y", interval="1d", auto_adjust=True, progress=False)
    if df is None or df.empty:
        return None
    close = df["Close"]
    if hasattr(close, "columns"):  # 新しい yfinance は列が2段になることがある
        close = close.iloc[:, 0]
    close = close.dropna()
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in close.index],
        "close": [round(float(v), 2) for v in close.values],
    }


def main() -> int:
    tickers = json.loads((ROOT / "tickers.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    stocks = []
    for t in tickers:
        code, name = t["code"], t["name"]
        path = OUT / safe_name(code)
        try:
            data = fetch_one(code)
        except Exception as e:  # 1銘柄の失敗で全体を止めない
            print(f"失敗: {code} {e}", file=sys.stderr)
            data = None
        if data:
            path.write_text(
                json.dumps({"code": code, "name": name, **data}, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            print(f"更新: {code} {len(data['close'])}日分 {data['dates'][-1]}まで")
        elif path.exists():
            print(f"前回のデータを使います: {code}", file=sys.stderr)
        else:
            continue
        stocks.append({"code": code, "name": name, "file": path.name})

    if not stocks:
        print("1銘柄も取れませんでした", file=sys.stderr)
        return 1

    index = {"updated": datetime.now(JST).isoformat(timespec="minutes"), "stocks": stocks}
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
