"""Generate synthetic (fake) Deribit-shaped sample data for offline testing.

Not real market data. Run: python tests/make_sample.py
"""
import csv
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bsmodel  # noqa: E402

random.seed(7)
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)
SPOT = 132.40
CONTRACT_SIZE = 1.0  # illustrative only; real runs read contract_size from Deribit


def smile(k, F, dte):
    base = 0.62 + 0.06 * math.exp(-dte / 20)        # short end a bit higher
    m = math.log(k / F)
    return base - 0.35 * m + 0.9 * m * m             # put skew + curvature


def expiry_list():
    out = set()
    for d in range(1, 4):                           # dailies
        out.add((NOW + timedelta(days=d)).date())
    d = NOW.date()
    while len([x for x in out if x > NOW.date() + timedelta(days=3)]) < 6:  # Fridays
        d += timedelta(days=1)
        if d.weekday() == 4:
            out.add(d)
    for months in (2, 3):                            # last Fridays of later months
        y, m = NOW.year, NOW.month + months
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        last = datetime(y + (m == 12), m % 12 + 1, 1).date() - timedelta(days=1)
        while last.weekday() != 4:
            last -= timedelta(days=1)
        out.add(last)
    return sorted(out)


def main():
    instruments, book = [], []
    for exp in expiry_list():
        exp_dt = datetime(exp.year, exp.month, exp.day, 8, tzinfo=timezone.utc)
        dte = (exp_dt - NOW).total_seconds() / 86400
        T = dte / 365
        F = SPOT * (1 + 0.04 * T)
        step = 1 if dte < 4 else (2 if dte < 20 else 5)
        lo, hi = int(F * 0.6 // step * step), int(F * 1.5 // step * step)
        for k in range(lo, hi + step, step):
            for t in ("call", "put"):
                tag = exp_dt.strftime("%d%b%y").upper().lstrip("0")
                name = f"SOL_USDC-{tag}-{k}-{'C' if t == 'call' else 'P'}"
                iv = smile(k, F, dte)
                px = bsmodel.price(F, k, T, iv, t)
                instruments.append({"instrument_name": name, "strike": float(k), "option_type": t,
                                    "expiration_timestamp": int(exp_dt.timestamp() * 1000),
                                    "contract_size": CONTRACT_SIZE, "min_trade_amount": 1.0,
                                    "base_currency": "SOL", "quote_currency": "USDC", "kind": "option"})
                if px < 0.02:
                    bid, ask = None, round(0.03, 2)
                else:
                    half = max(0.03, px * random.uniform(0.03, 0.07))
                    bid, ask = round(max(px - half, 0.01), 2), round(px + half, 2)
                moneyness = abs(math.log(k / F))
                oi = round(max(0.0, random.gauss(4000 * math.exp(-moneyness * 8), 300)), 0)
                book.append({"instrument_name": name, "bid_price": bid, "ask_price": ask,
                             "mid_price": round((bid + ask) / 2, 4) if bid else None,
                             "mark_price": round(px, 4), "mark_iv": round(iv * 100, 2),
                             "open_interest": oi, "underlying_price": round(F, 4),
                             "underlying_index": f"SOL_USDC-{exp_dt.strftime('%d%b%y').upper()}",
                             "base_currency": "SOL", "quote_currency": "USDC", "volume": 0})

    # 400 days of daily candles (random walk ending at SPOT)
    closes = [SPOT]
    for _ in range(399):
        closes.append(closes[-1] / math.exp(random.gauss(0, 0.62 / math.sqrt(365))))
    closes.reverse()
    ticks = [int((NOW - timedelta(days=399 - i)).replace(hour=0).timestamp() * 1000) for i in range(400)]
    chart = {"status": "ok", "ticks": ticks, "close": [round(c, 3) for c in closes],
             "open": [round(c, 3) for c in closes], "high": [round(c * 1.02, 3) for c in closes],
             "low": [round(c * 0.98, 3) for c in closes], "volume": [0] * 400, "cost": [0] * 400}
    delivery = {"data": [{"date": (NOW - timedelta(days=i)).strftime("%Y-%m-%d"),
                          "delivery_price": round(closes[-1 - i], 4)} for i in range(1, 60)],
                "records_total": 59}
    snap = {"fetched_at": NOW.isoformat(), "index_price": SPOT, "instruments": instruments,
            "book_summary": book, "chart": chart, "delivery_prices": delivery, "_note": "SYNTHETIC SAMPLE DATA"}
    out = ROOT / "data" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    (out / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")

    # 200 days of fake IV history so percentile mode and chart shading can be seen
    with (out / "iv_history_seed.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "index_price", "atm_iv_30d", "rv20", "rv30", "iv_source"])
        path, iv = [], 0.62                     # walk backwards from today's level
        for i in range(1, 201):
            iv = min(1.1, max(0.40, iv + random.gauss(0, 0.02) + 0.03 * (0.60 - iv)))
            path.append(((NOW - timedelta(days=i)).strftime("%Y-%m-%d"), iv))
        for d, v in reversed(path):
            w.writerow([d, "", round(v, 4), "", "", "sample"])
    print(f"Wrote {len(book)} options to {out}")


if __name__ == "__main__":
    main()
