"""Backtest a strategy file on SOL's own history, using the SAME engine as the daily scan.

    python backtest.py                                   # strategies/bull_put_spread.toml
    python backtest.py strategies/bull_put_spread.toml   # any strategy file

For every past day it takes that day's real option chain if one was stored (data/chains/YYYY-MM-DD.json.gz,
kept by the daily scan), otherwise rebuilds the put chain from the stored 30-day ATM IV (engine.synthetic_chain:
same pricing model, put skew and bid-ask as set in the strategy's [backtest] section). It then asks
engine.decide() exactly what the daily scan would have suggested, opens it, and checks it each day with
engine.manage() against the strategy's exit rules (real quotes on stored days, the model otherwise).
Results go to data/backtest.json, with a SHA-256 fingerprint of every input file it read.

What is compared is set in advance in the strategy file, never in this code: the [[variants]] list (each one the
strategy's rules with a few overrides) and the [evaluation] windows (in-sample: rules were chosen looking at it;
held back: judged on it). Every variant gets the same fixed scorecard for both windows, side by side.

Only committed files are read (config.toml, the strategy file, data/iv_history.csv, data/chains/), never
config.local.toml, and the output has no timestamps: the same files always give a byte-identical result.

Caveats: model prices on days without a stored chain (every day before Nov 2026), skew held constant,
exits checked once a day, IV before Nov 2026 estimated from public trades.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

import chain_store
import engine
import main
import vol_gauge

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "backtest.json"

def load_days(cfg: dict, history_path=None) -> list[dict]:
    hist = vol_gauge.annotate_history(cfg, vol_gauge.load_history(history_path or vol_gauge.HISTORY_CSV))
    return [{"date": r["date"], "S": r["index_price"], "iv": r["atm_iv_30d"],
             "verdict": {"label": r.get("verdict"), "reason": ""}}
            for r in hist if r.get("index_price")]


def stored_days(days: list[dict], chains_dir) -> list[str]:
    """Days in this history that have a stored real chain."""
    return [d["date"] for d in days if chain_store.path_for(d["date"], chains_dir).exists()]


def run(cfg: dict, st: dict, days: list[dict], one_at_a_time: bool = True, chains_dir=None) -> list[dict]:
    chains_dir = chains_dir or chain_store.CHAINS_DIR
    have = set(stored_days(days, chains_dir))
    cache = {}

    def real(day):
        if day not in have:
            return None
        if day not in cache:
            cache[day] = engine.stored_chain(day, chains_dir)
        return cache[day]

    trades, busy_until = [], ""
    need = st["entry"].get("require_verdict")
    for i, d in enumerate(days):
        if d["iv"] is None or (one_at_a_time and d["date"] < busy_until):
            continue
        if need and d["verdict"]["label"] != need:
            continue
        chain = real(d["date"])
        source = "stored" if chain else "model"
        if chain is None:
            chain = engine.synthetic_chain(st, d["date"], d["S"], d["iv"])
        dec = engine.decide(cfg, st, d["verdict"], chain)
        if dec["trade"] is None:
            continue
        pos = engine.open_position(cfg, st, dec["trade"], d["S"])
        result, exit_day = None, None
        for later in days[i + 1:]:
            result = engine.manage(st, pos, later["date"], later["S"],
                                   later["iv"] if later["iv"] is not None else d["iv"], real(later["date"]))
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
                       "days": held, "chain": source, "verdict": d["verdict"]["label"],
                       "S_exit": later["S"]})
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
            "avg_days": round(statistics.mean(t["days"] for t in trades), 1), "exits": exits_,
            "stored_chain_entries": sum(t.get("chain") == "stored" for t in trades)}


# ------------------------------------------------------------------ the fixed scorecard (step 3)
def windows(st: dict, days: list[dict]) -> dict:
    """The in-sample and held-back date ranges from the strategy file's [evaluation], clipped to the data."""
    ev = st["evaluation"]
    first, last = days[0]["date"], days[-1]["date"]
    out = {}
    for key in ("in_sample", "held_back"):
        lo, hi = ev[key]
        out[key] = [max(lo or first, first), min(hi or last, last)]
    return out


def losing_streak(p: list[float]) -> int:
    run_ = best = 0
    for x in p:
        run_ = run_ + 1 if x < 0 else 0
        best = max(best, run_)
    return best


def hold_sol(days: list[dict], lo: str, hi: str) -> dict:
    """$1,000 of SOL bought on the window's first day, valued on its last day."""
    inside = [d for d in days if lo <= d["date"] <= hi and d["S"]]
    if len(inside) < 2:
        return {"per_1000": None}
    a, b = inside[0], inside[-1]
    return {"from": a["date"], "to": b["date"], "sol_from": a["S"], "sol_to": b["S"],
            "per_1000": round((b["S"] / a["S"] - 1) * 1000, 1)}


def by_condition(trades: list[dict]) -> dict:
    """Results split by the vol verdict on entry day and whether SOL ended the trade higher or lower."""
    out = {}
    for t in trades:
        key = f"{t['verdict'] or 'NO VERDICT'}, SOL {'up' if t['S_exit'] >= t['S'] else 'down'}"
        c = out.setdefault(key, {"trades": 0, "wins": 0, "total_per_1000": 0.0})
        c["trades"] += 1
        c["wins"] += t["pnl_per_1000"] > 0
        c["total_per_1000"] += t["pnl_per_1000"]
    for c in out.values():
        c["total_per_1000"] = round(c["total_per_1000"], 1)
        c["avg_per_1000"] = round(c["total_per_1000"] / c["trades"], 1)
    return dict(sorted(out.items()))


def scorecard(trades: list[dict], days: list[dict], lo: str, hi: str) -> dict:
    """The same fixed set of numbers for every variant and window. Trades belong to the window of their entry day."""
    ts = [t for t in trades if lo <= t["date"] <= hi]
    s = summarise(ts)
    s["max_losing_streak"] = losing_streak([t["pnl_per_1000"] for t in ts])
    s["by_condition"] = by_condition(ts)
    s["hold_sol"] = hold_sol(days, lo, hi)
    return s


def input_files(strategy_path, history_path, chains_dir, days) -> tuple[dict, str]:
    """SHA-256 of every file the backtest reads, and one short fingerprint of the data files."""
    chains = [chain_store.path_for(d, chains_dir) for d in stored_days(days, chains_dir)]
    fps = chain_store.fingerprints([main.CONFIG, strategy_path, history_path, *chains])
    data = {k: v for k, v in fps.items() if k not in chain_store.fingerprints([main.CONFIG, strategy_path])}
    return fps, chain_store.combined(data)


def run_all(strategy_path=None, quiet=False, history_path=None, chains_dir=None) -> dict:
    strategy_path = Path(strategy_path or engine.DEFAULT_STRATEGY)
    history_path = Path(history_path or vol_gauge.HISTORY_CSV)
    chains_dir = Path(chains_dir or chain_store.CHAINS_DIR)
    cfg = main.load_config(strategy_path, local=False)
    st = cfg["_strategy"]
    days = load_days(cfg, history_path)
    inputs, data_fp = input_files(strategy_path, history_path, chains_dir, days)
    out = {"strategy": st["_stamp"], "period": [days[0]["date"], days[-1]["date"]],
           "inputs": inputs, "data_fingerprint": data_fp,
           "stored_chain_days": len(stored_days(days, chains_dir))}
    win = out["windows"] = windows(st, days)
    out["variants"] = []
    say = (lambda *a: None) if quiet else print
    say(f"{st['_stamp']['title']} (v{st['_stamp']['version']}, fingerprint {st['_stamp']['fingerprint']})")
    say(f"History {days[0]['date']} to {days[-1]['date']}, {out['stored_chain_days']} day(s) with a stored real chain, "
        f"data fingerprint {data_fp}. P&L per $1,000 of max loss per trade.\n")
    for v in engine.variants(st):                       # only what the strategy file declares
        trades = run(cfg, v["strategy"], days, v["one_at_a_time"], chains_dir)
        out["variants"].append({"name": v["name"], "settings": v["settings"],
                                "scorecard": {k: scorecard(trades, days, *win[k]) for k in ("in_sample", "held_back")},
                                "trades": trades})
    say(report(out))
    return out


def report(out: dict) -> str:
    """Plain-text table: every declared variant, in-sample and held-back side by side."""
    w = out["windows"]
    cols = f"{'trades':>6s} {'win%':>5s} {'avg':>5s} {'total':>6s} {'worst':>6s} {'strk':>4s} {'maxDD':>6s}"
    lines = [f"{'':38s} | {'IN-SAMPLE ' + w['in_sample'][0] + ' to ' + w['in_sample'][1]:46s}"
             f" | HELD BACK {w['held_back'][0]} to {w['held_back'][1]}",
             f"{'variant':38s} | {cols} | {cols}"]

    def cells(s):
        if not s["trades"]:
            return f"{0:6d} {'-':>5s} {'-':>5s} {'-':>6s} {'-':>6s} {'-':>4s} {'-':>6s}"
        return (f"{s['trades']:6d} {s['win_rate']*100:5.0f} {s['avg_per_1000']:5.0f} {s['total_per_1000']:6.0f} "
                f"{s['worst_per_1000']:6.0f} {s['max_losing_streak']:4d} {s['max_drawdown_per_1000']:6.0f}")
    for v in out["variants"]:
        sc = v["scorecard"]
        lines.append(f"{v['name'][:38]:38s} | {cells(sc['in_sample'])} | {cells(sc['held_back'])}")
    first = out["variants"][0]["scorecard"]
    hold = [first[k]["hold_sol"]["per_1000"] for k in ("in_sample", "held_back")]
    lines.append(f"Holding SOL instead ($1,000 bought on the window's first day): "
                 f"in-sample {hold[0]:+.0f}, held back {hold[1]:+.0f}")
    lines.append("P&L per $1,000 of max loss per trade (holding SOL: per $1,000 invested). "
                 "strk = longest losing streak.")
    return "\n".join(lines)


def dumps(res: dict) -> str:
    return json.dumps(res, indent=1, default=str, allow_nan=False) + "\n"


if __name__ == "__main__":
    res = run_all(sys.argv[1] if len(sys.argv) > 1 else None)
    OUT.write_bytes(dumps(res).encode("utf-8"))
