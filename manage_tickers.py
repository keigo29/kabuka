"""アプリから送られた「銘柄を追加／削除」の依頼（GitHub の issue）を tickers.json に反映する。

使い方（GitHub Actions から呼ばれる）:
  ISSUE_TITLE="銘柄を追加: AAPL" ISSUE_BODY="code: AAPL\nname: アップル" python manage_tickers.py

結果は result.md（issue に返すコメント）と、変更があれば changed というファイルで伝える。
"""
import json
import os
import re
import sys
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).parent
TICKERS = ROOT / "tickers.json"
CODE_RE = re.compile(r"^[\^A-Za-z0-9.\-=]{1,20}$")
MIN_DAYS = 200


def field(body: str, key: str) -> str:
    m = re.search(rf"^\s*{key}\s*[:：]\s*(.+?)\s*$", body, re.M)
    return m.group(1).strip() if m else ""


def normalize(code: str) -> str:
    code = code.strip().upper()
    if re.fullmatch(r"\d{3}[0-9A-Z]", code):  # 7203 や 130A は東証の銘柄として .T を付ける
        code += ".T"
    return code


def days_available(code: str) -> int:
    df = yf.download(code, period="2y", interval="1d", auto_adjust=True, progress=False)
    if df is None or df.empty:
        return 0
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    return int(close.dropna().shape[0])


def finish(msg: str, changed: bool) -> int:
    (ROOT / "result.md").write_text(msg, encoding="utf-8")
    if changed:
        (ROOT / "changed").write_text("1", encoding="utf-8")
    print(msg)
    return 0


def main() -> int:
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    tickers = json.loads(TICKERS.read_text(encoding="utf-8"))

    action = "add" if title.startswith("銘柄を追加") else "remove" if title.startswith("銘柄を削除") else None
    if not action:
        return finish("依頼の形がわかりませんでした。アプリの「データ」画面から送り直してください。", False)

    code = normalize(field(body, "code") or title.split(":", 1)[-1].split("：", 1)[-1])
    if not CODE_RE.fullmatch(code):
        return finish(f"コード「{code}」は使えない形です。例：7203.T、AAPL、^GSPC", False)

    exists = next((t for t in tickers if t["code"].upper() == code), None)

    if action == "remove":
        if not exists:
            return finish(f"{code} は一覧にありません。", False)
        if len(tickers) <= 1:
            return finish("最後の1銘柄は削除できません。先に別の銘柄を追加してください。", False)
        tickers = [t for t in tickers if t is not exists]
        TICKERS.write_text(json.dumps(tickers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return finish(f"{exists['name']}（{code}）を削除しました。2〜3分でアプリに反映されます。", True)

    if exists:
        return finish(f"{exists['name']}（{code}）はすでに一覧にあります。", False)

    n = days_available(code)
    if n == 0:
        return finish(f"{code} の株価が見つかりませんでした。日本株は 7203.T、米国株は AAPL のように書いてください。", False)
    if n < MIN_DAYS:
        return finish(f"{code} は株価が {n} 日分しかなく、予測に必要な {MIN_DAYS} 日分に足りません。", False)

    name = field(body, "name")[:40] or code
    entry = {"code": code, "name": name}
    unit = field(body, "unit")
    if unit in ("円", "ドル", "ポイント"):
        entry["unit"] = unit
    tickers.append(entry)
    TICKERS.write_text(json.dumps(tickers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return finish(f"{name}（{code}）を追加しました。2〜3分でアプリに反映されます。", True)


if __name__ == "__main__":
    sys.exit(main())
