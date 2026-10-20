"""tickers.json の銘柄の終値と会社の数字（ROE など）を yfinance で取り、site/data/ に JSON で保存する。

GitHub Actions から毎日呼ばれる。手元でも `python fetch.py` で動く。
"""
import json
import math
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent
OUT = ROOT / "site" / "data"
JST = timezone(timedelta(hours=9))


def safe_name(code: str) -> str:
    # ^N225 のような記号入りのコードもファイル名にできるようにする
    return "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in code) + ".json"


def guess_unit(code: str) -> str:
    # tickers.json に "unit" がなければ、コードの形から決める
    if code.startswith("^"):
        return "ポイント"
    if code.upper().endswith(".T"):
        return "円"
    return "ドル"


def num(v, digits=4):
    """数字として使えるものだけを返す（NaN や文字は None）"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, digits)


def fetch_prices(code: str):
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


def row(df, names):
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def fetch_fundamentals(code: str, last_close: float):
    """ROE（年ごと）・PER・PBR・配当利回り・自己資本比率。取れないものは None。"""
    if code.startswith("^"):  # 指数には会社の数字がない
        return None
    tk = yf.Ticker(code)
    f = {}

    info = {}
    try:
        info = tk.info or {}
    except Exception as e:
        print(f"  info が取れません: {code} {e}", file=sys.stderr)

    # 年ごとの ROE ＝ 当期純利益 ÷ 自己資本（期末）
    hist = []
    eq_ratio = None
    try:
        inc, bs = tk.income_stmt, tk.balance_sheet
        ni = row(inc, ["Net Income Common Stockholders", "Net Income", "Net Income From Continuing Operation Net Minority Interest"])
        eq = row(bs, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"])
        ta = row(bs, ["Total Assets"])
        if ni is not None and eq is not None:
            for d in ni.index:
                if d in eq.index and pd.notna(ni[d]) and pd.notna(eq[d]) and eq[d] > 0:
                    hist.append({"end": pd.Timestamp(d).strftime("%Y-%m"), "roe": num(ni[d] / eq[d])})
            hist.sort(key=lambda x: x["end"])
        if eq is not None and ta is not None:
            d = max(set(eq.dropna().index) & set(ta.dropna().index), default=None)
            if d is not None and ta[d] > 0:
                eq_ratio = num(eq[d] / ta[d])
    except Exception as e:
        print(f"  決算の数字が取れません: {code} {e}", file=sys.stderr)

    f["roeHist"] = hist
    f["roe"] = num(info.get("returnOnEquity")) if num(info.get("returnOnEquity")) is not None else (hist[-1]["roe"] if hist else None)
    f["eqRatio"] = eq_ratio

    per = num(info.get("trailingPE"), 2)
    if per is None and num(info.get("trailingEps")):
        eps = num(info.get("trailingEps"))
        per = num(last_close / eps, 2) if eps else None
    f["per"] = per

    pbr = None
    bvps = num(info.get("bookValue"))
    if bvps and bvps > 0:
        pbr = num(last_close / bvps, 2)  # 株価 ÷ 1株あたり純資産（通貨のずれを避けるため自分で割る）
    f["pbr"] = pbr if pbr is not None else num(info.get("priceToBook"), 2)

    try:
        div = tk.dividends
        if div is not None and len(div):
            since = pd.Timestamp.now(tz=div.index.tz) - pd.Timedelta(days=365)
            f["divYield"] = num(float(div[div.index >= since].sum()) / last_close)
        else:
            f["divYield"] = 0.0
    except Exception:
        f["divYield"] = None

    f["sector"] = info.get("sector") or None
    f["asOf"] = datetime.now(JST).strftime("%Y-%m-%d")
    return f


def main() -> int:
    tickers = json.loads((ROOT / "tickers.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    stocks = []
    for t in tickers:
        code, name = t["code"], t["name"]
        unit = t.get("unit") or guess_unit(code)
        path = OUT / safe_name(code)
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        try:
            data = fetch_prices(code)
        except Exception as e:  # 1銘柄の失敗で全体を止めない
            print(f"失敗: {code} {e}", file=sys.stderr)
            data = None

        if data:
            fund = None
            try:
                fund = fetch_fundamentals(code, data["close"][-1])
            except Exception as e:
                print(f"  会社の数字が取れません: {code} {e}", file=sys.stderr)
            # 取れなかった項目は前回の値を使う
            if old.get("fund"):
                fund = fund or {}
                for k, v in old["fund"].items():
                    if fund.get(k) in (None, []):
                        fund[k] = v
            out = {"code": code, "name": name, "unit": unit, **data}
            if fund:
                out["fund"] = fund
            path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            f = fund or {}
            print(f"更新: {code} {len(data['close'])}日分 {data['dates'][-1]}まで ROE={f.get('roe')} PER={f.get('per')} PBR={f.get('pbr')} 配当={f.get('divYield')} 自己資本比率={f.get('eqRatio')} ROE年数={len(f.get('roeHist') or [])}")
            time.sleep(1)  # Yahoo に負担をかけすぎない
        elif path.exists():
            print(f"前回のデータを使います: {code}", file=sys.stderr)
        else:
            continue
        stocks.append({"code": code, "name": name, "unit": unit, "file": path.name})

    if not stocks:
        print("1銘柄も取れませんでした", file=sys.stderr)
        return 1

    index = {"updated": datetime.now(JST).isoformat(timespec="minutes"), "stocks": stocks}
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
