"""Rough backtest of the bull put spread rules on SOL's own history.

    python backtest.py            prints the results and writes data/backtest.json

What it does, for every day in data/iv_history.csv that was RICH at the time (12-month window):
  * builds a ~30-day bull put spread: short put near -0.20 delta, long put ~10% lower
  * prices it with Black-76 from that day's 30-day ATM IV plus a fixed put skew (measured from a
    live Deribit chain), less bid-ask costs and Deribit-style fees
  * walks forward one day at a time on daily closes and applies the exit plan

Big caveats (it is a model, not a replay of real quotes):
  * option prices are model prices, not historical bids and offers
  * skew is held constant; real skew steepens in sell-offs (makes losses here look a bit smaller)
  * exits are checked once a day at the close, so intraday stops and gaps are approximated
  * historical IV before Nov 2026 is estimated from public trades (noisy)
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

import bsmodel
import vol_gauge

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "backtest.json"

# Put skew measured from the live chain on 2026-11-17: IV relative to ATM at log-moneyness ln(F/K).
SKEW_POINTS = [(0.0, 1.0), (0.122, 1.014), (0.220, 1.0625)]
SPREAD_COST = {"short": 0.015, "long": 0.03}   # half bid-ask as a share of price (from the same chain)
FEE_RATE, FEE_CAP = 0.0003, 0.125               # per leg per trade: 0.03% of underlying, max 12.5% of price
DTE = 30


def skew_iv(atm: float, F: float, K: float) -> float:
    m = math.log(F / K)
    pts = SKEW_POINTS
    if m <= pts[0][0]:
        return atm
    for (m0, r0), (m1, r1) in zip(pts, pts[1:]):
        if m <= m1:
            return atm * (r0 + (r1 - r0) * (m - m0) / (m1 - m0))
    (m0, r0), (m1, r1) = pts[-2], pts[-1]
    return atm * (r1 + (r1 - r0) / (m1 - m0) * (m - m1))


def step(S: float) -> float:
    return 0.5 if S < 50 else 1.0 if S < 150 else 2.5 if S < 300 else 5.0


def put(F, K, T, atm):
    return bsmodel.price(F, K, max(T, 0.0), skew_iv(atm, F, K), "put")


def fee(S, px):
    return min(FEE_RATE * S, FEE_CAP * px) if px > 0 else 0.0


def open_spread(S, atm, delta_target=-0.20, offset=0.10):
    T = DTE / 365
    g = step(S)
    best = None
    for k in range(int(S * 0.5 / g), int(S / g) + 1):
        K = k * g
        d = bsmodel.greeks(S, K, T, skew_iv(atm, S, K), "put")["delta"]
        if -0.25 <= d <= -0.15 and (best is None or abs(d + 0.20) < abs(best[1] - delta_target)):
            best = (K, d)
    if not best:
        return None
    Ks = best[0]
    Kl = round(Ks * (1 - offset) / g) * g
    if Kl >= Ks:
        Kl = Ks - g
    ps, pl = put(S, Ks, T, atm), put(S, Kl, T, atm)
    credit = ps * (1 - SPREAD_COST["short"]) - pl * (1 + SPREAD_COST["long"]) - fee(S, ps) - fee(S, pl)
    return {"Ks": Ks, "Kl": Kl, "credit": credit, "width": Ks - Kl, "delta": best[1], "S0": S, "atm0": atm,
            "short": {"F": S, "strike": Ks, "T": T, "iv": skew_iv(atm, S, Ks)},
            "long": {"F": S, "strike": Kl, "T": T, "iv": skew_iv(atm, S, Kl)}}


def buyback(sp, S, atm, days_left):
    T = days_left / 365
    ps, pl = put(S, sp["Ks"], T, atm), put(S, sp["Kl"], T, atm)
    return ps * (1 + SPREAD_COST["short"]) - pl * (1 - SPREAD_COST["long"]) + fee(S, ps) + fee(S, pl), ps


def stop_levels(sp, k, beta, assumed_days=3):
    """Same logic as exits.py: SOL level where buying back costs credit * (1 + k), with IV rising beta pts per 1% drop."""
    target = min(sp["credit"] * (1 + k), sp["width"] * 0.98)
    lo, hi, S0 = sp["S0"] * 0.2, sp["S0"], sp["S0"]
    T = (DTE - assumed_days) / 365

    def cost(S):
        atm = sp["atm0"] + beta * max(0.0, (1 - S / S0) * 100) / 100
        return put(S, sp["Ks"], T, atm) - put(S, sp["Kl"], T, atm)
    for _ in range(70):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cost(mid) > target else (lo, mid)
    atm = sp["atm0"] + beta * max(0.0, (1 - hi / S0) * 100) / 100
    return hi, put(hi, sp["Ks"], T, atm)   # price-stop SOL level, short-put mark trigger


def simulate(rows, i0, sp, rule):
    """Walk forward from entry row i0. Returns (pnl per SOL, exit reason, days held)."""
    tp, k, t_exit, beta = rule["tp"], rule["stop"], rule["time_exit"], rule["beta"]
    stop_S, mark_trig = stop_levels(sp, k, beta) if k else (None, None)
    d0 = date.fromisoformat(rows[i0]["date"])
    for i in range(i0 + 1, len(rows)):
        r = rows[i]
        held = (date.fromisoformat(r["date"]) - d0).days
        left = DTE - held
        S, atm = r["S"], r["iv"] if r["iv"] is not None else sp["atm0"]
        if left <= 0:
            loss = max(0.0, sp["Ks"] - S) - max(0.0, sp["Kl"] - S)
            return sp["credit"] - loss, "expiry", held
        v, ps = buyback(sp, S, atm, left)
        if k and (S <= stop_S or ps >= mark_trig):
            return sp["credit"] - v, "stop", held
        if tp and v <= sp["credit"] * (1 - tp):
            return sp["credit"] * tp, "take profit", held          # resting limit fills at its price
        if t_exit and left <= t_exit:
            return sp["credit"] - v, "time exit", held
    return None, "open", None


def load():
    hist = vol_gauge.annotate_history(CFG, vol_gauge.load_history())
    rows = [{"date": r["date"], "S": r["index_price"], "iv": r["atm_iv_30d"], "verdict": r.get("verdict")}
            for r in hist if r.get("index_price")]
    return rows


def run(rows, rule, only_rich=True, one_at_a_time=False):
    trades, busy_until = [], None
    for i, r in enumerate(rows):
        if r["iv"] is None or (only_rich and r["verdict"] != "RICH"):
            continue
        if one_at_a_time and busy_until and r["date"] < busy_until:
            continue
        sp = open_spread(r["S"], r["iv"])
        if not sp or sp["credit"] <= 0 or sp["credit"] / sp["width"] < 0.12:
            continue
        pnl, why, held = simulate(rows, i, sp, rule)
        if pnl is None:
            continue
        max_loss = sp["width"] - sp["credit"]
        trades.append({"date": r["date"], "S": r["S"], "Ks": sp["Ks"], "Kl": sp["Kl"], "credit": sp["credit"],
                       "max_loss": max_loss, "pnl": pnl, "pnl_per_1000": pnl / max_loss * 1000,
                       "exit": why, "days": held})
        busy_until = (date.fromisoformat(r["date"]) + timedelta(days=held)).isoformat()
    return trades


def summarise(trades):
    if not trades:
        return {"trades": 0}
    p = [t["pnl_per_1000"] for t in trades]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in p:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    exits = {}
    for t in trades:
        exits[t["exit"]] = exits.get(t["exit"], 0) + 1
    return {"trades": len(trades), "win_rate": sum(x > 0 for x in p) / len(p),
            "avg_per_1000": statistics.mean(p), "median_per_1000": statistics.median(p),
            "worst_per_1000": min(p), "best_per_1000": max(p), "total_per_1000": sum(p),
            "max_drawdown_per_1000": dd, "avg_days": statistics.mean(t["days"] for t in trades),
            "avg_credit_pct_width": statistics.mean(t["credit"] / (t["credit"] + t["max_loss"]) for t in trades),
            "exits": exits}


CFG = {"vol": {"iv_percentile_min": 50, "iv_percentile_cheap": 20, "iv_rv_ratio_min": 1.2,
               "iv_rv_ratio_cheap": 0.9, "min_history_days": 60, "target_dte": 30}}

RULES = {
    "Your plan (TP 50%, stop 1.5x, close 7 days out)": {"tp": 0.5, "stop": 1.5, "time_exit": 7, "beta": 1.0},
    "Same, stop 1x": {"tp": 0.5, "stop": 1.0, "time_exit": 7, "beta": 1.0},
    "Same, stop 2x": {"tp": 0.5, "stop": 2.0, "time_exit": 7, "beta": 1.0},
    "Your plan, vol jump 0.3 (measured)": {"tp": 0.5, "stop": 1.5, "time_exit": 7, "beta": 0.3},
    "No stop (TP 50%, close 7 days out)": {"tp": 0.5, "stop": 0, "time_exit": 7, "beta": 1.0},
    "Hold to expiry, no exits": {"tp": 0, "stop": 0, "time_exit": 0, "beta": 1.0},
    "TP 50%, stop 2x, no time exit": {"tp": 0.5, "stop": 2.0, "time_exit": 0, "beta": 1.0},
    "TP 50%, stop 3x, no time exit": {"tp": 0.5, "stop": 3.0, "time_exit": 0, "beta": 1.0},
    "TP 50%, no stop, no time exit": {"tp": 0.5, "stop": 0, "time_exit": 0, "beta": 1.0},
}


def main():
    rows = load()
    out = {"period": [rows[0]["date"], rows[-1]["date"]], "results": {}}
    print(f"History {rows[0]['date']} to {rows[-1]['date']}. P&L shown per $1,000 of max loss per trade.\n")
    hdr = f"{'rule':52s} {'trades':>6s} {'win%':>5s} {'avg':>6s} {'worst':>6s} {'total':>7s} {'maxDD':>7s}  exits"
    for label, filt in (("RICH days only, one trade at a time", dict(only_rich=True, one_at_a_time=True)),
                        ("EVERY day, one trade at a time (no rich filter)", dict(only_rich=False, one_at_a_time=True)),
                        ("RICH days, a new trade every rich day (overlapping)", dict(only_rich=True, one_at_a_time=False))):
        print(label); print(hdr)
        out["results"][label] = {}
        for name, rule in RULES.items():
            s = summarise(run(rows, rule, **filt))
            out["results"][label][name] = s
            if s["trades"]:
                print(f"{name:52s} {s['trades']:6d} {s['win_rate']*100:5.0f} {s['avg_per_1000']:6.0f} "
                      f"{s['worst_per_1000']:6.0f} {s['total_per_1000']:7.0f} {s['max_drawdown_per_1000']:7.0f}  {s['exits']}")
        print()
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
