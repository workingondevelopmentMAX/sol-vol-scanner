"""Backtest a strategy file on SOL's own history, using the SAME engine as the daily scan.

    python backtest.py                                   # strategies/bull_put_spread.toml
    python backtest.py strategies/bull_put_spread.toml   # any strategy file

For every past day it rebuilds that day's put chain from the stored 30-day ATM IV (engine.synthetic_chain:
same pricing model, put skew and bid-ask as set in the strategy's [backtest] section), then asks
engine.decide() exactly what the daily scan would have suggested, opens it, and checks it each day with
engine.manage() against the strategy's exit rules. Results go to data/backtest.json.

Caveats: model prices (not stored quotes), skew held constant, exits checked once a day at the close,
IV before Nov 2026 estimated from public trades.
"""
from __future__ import annotations

import copy
import json
import statistics
import sys
from datetime import date
from pathlib import Path

import engine
import main
import vol_gauge

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "backtest.json"

# Exit-rule variants compared against the strategy file's own rules (overrides of its [exits] section).
VARIANTS = {
    "Strategy file as written": {},
    "Stop 1x": {"stop_multiple": 1.0},
    "Stop 2x": {"stop_multiple": 2.0},
    "Stop 3x, no time exit": {"stop_multiple": 3.0, "time_exit_dte": 0},
    "No stop, close 7 days out": {"stop_multiple": 0},
    "No stop, no time exit": {"stop_multiple": 0, "time_exit_dte": 0},
    "Hold to expiry, no exits": {"stop_multiple": 0, "time_exit_dte": 0, "take_profit_pct": 0},
}


def load_days(cfg: dict) -> list[dict]:
    hist = vol_gauge.annotate_history(cfg, vol_gauge.load_history())
    return [{"date": r["date"], "S": r["index_price"], "iv": r["atm_iv_30d"],
             "verdict": {"label": r.get("verdict"), "reason": ""}}
            for r in hist if r.get("index_price")]


def run(cfg: dict, st: dict, days: list[dict], one_at_a_time: bool = True) -> list[dict]:
    trades, busy_until = [], ""
    need = st["entry"].get("require_verdict")
    for i, d in enumerate(days):
        if d["iv"] is None or (one_at_a_time and d["date"] < busy_until):
            continue
        if need and d["verdict"]["label"] != need:
            continue
        chain = engine.synthetic_chain(st, d["date"], d["S"], d["iv"])
        dec = engine.decide(cfg, st, d["verdict"], chain)
        if dec["trade"] is None:
            continue
        pos = engine.open_position(cfg, st, dec["trade"], d["S"])
        result, exit_day = None, None
        for later in days[i + 1:]:
            result = engine.manage(st, pos, later["date"], later["S"],
                                   later["iv"] if later["iv"] is not None else d["iv"])
            if result:
                exit_day = later["date"]
                break
        if not result:
            continue                                  # still open at the end of the data
        why, pnl = result
        held = (date.fromisoformat(exit_day) - date.fromisoformat(d["date"])).days
        trades.append({"date": d["date"], "S": d["S"], "expiry": pos["expiry"], "Ks": pos["Ks"], "Kl": pos["Kl"],
                       "delta": round(pos["delta"], 3), "credit": round(pos["credit"], 4),
                       "max_loss": round(pos["max_loss"], 4), "pnl": round(pnl, 4),
                       "pnl_per_1000": round(pnl / pos["max_loss"] * 1000, 1), "exit": why, "exit_date": exit_day,
                       "days": held})
        busy_until = exit_day
    return trades


def summarise(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    p = [t["pnl_per_1000"] for t in trades]
    eq = peak = dd = 0.0
    for x in p:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    exits_ = {}
    for t in trades:
        exits_[t["exit"]] = exits_.get(t["exit"], 0) + 1
    return {"trades": len(trades), "win_rate": round(sum(x > 0 for x in p) / len(p), 4),
            "avg_per_1000": round(statistics.mean(p), 1), "worst_per_1000": min(p), "best_per_1000": max(p),
            "total_per_1000": round(sum(p), 1), "max_drawdown_per_1000": round(dd, 1),
            "avg_days": round(statistics.mean(t["days"] for t in trades), 1), "exits": exits_}


def variant(st: dict, overrides: dict, require_verdict: bool = True) -> dict:
    v = copy.deepcopy(st)
    v["exits"].update(overrides)
    if not require_verdict:
        v["entry"]["require_verdict"] = None
    return v


def run_all(strategy_path=None, quiet=False) -> dict:
    cfg = main.load_config(strategy_path)
    st = cfg["_strategy"]
    days = load_days(cfg)
    out = {"strategy": st["_stamp"], "period": [days[0]["date"], days[-1]["date"]], "sections": {}}
    say = (lambda *a: None) if quiet else print
    say(f"{st['_stamp']['title']} (v{st['_stamp']['version']}, fingerprint {st['_stamp']['fingerprint']})")
    say(f"History {days[0]['date']} to {days[-1]['date']}. P&L per $1,000 of max loss per trade.\n")
    hdr = f"{'exit rules':30s} {'trades':>6s} {'win%':>5s} {'avg':>6s} {'worst':>6s} {'total':>7s} {'maxDD':>7s}  exits"
    for label, rich, single in (("Rich days only, one trade at a time", True, True),
                                ("Every day, one trade at a time (no rich filter)", False, True),
                                ("Rich days, a new trade every rich day (overlapping)", True, False)):
        say(label); say(hdr)
        sec = out["sections"][label] = {}
        for name, ov in VARIANTS.items():
            trades = run(cfg, variant(st, ov, rich), days, single)
            s = summarise(trades)
            sec[name] = {"summary": s, "trades": trades if (rich and single) else None}
            if s["trades"]:
                say(f"{name:30s} {s['trades']:6d} {s['win_rate']*100:5.0f} {s['avg_per_1000']:6.0f} "
                    f"{s['worst_per_1000']:6.0f} {s['total_per_1000']:7.0f} {s['max_drawdown_per_1000']:7.0f}  {s['exits']}")
        say()
    return out


if __name__ == "__main__":
    res = run_all(sys.argv[1] if len(sys.argv) > 1 else None)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
