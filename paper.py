"""Paper trades for strategies that are still being tested (today: debit spreads on cheap-IV days).

Every daily run writes the debit-spread decision to logs/paper_trades.csv, whether or not the strategy has passed its
test, and manages open paper trades to their exits with that day's real option chain (engine.manage_debit). The file
is append-only: an "open" row when a paper trade starts, one "close" row per side when it ends, and a "no_entry" row
on days without a new trade. A day's rows are written once (the first run of the UTC day) and never rewritten.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import engine

ROOT = Path(__file__).resolve().parent
PAPER_LOG = ROOT / "logs" / "paper_trades.csv"
FIELDS = ["date", "event", "trade_id", "strategy", "rules_version", "rules_fingerprint", "status", "direction",
          "side", "legs", "cost_per_sol", "contracts", "cost_usd", "take_profit", "time_exit", "reason", "value_per_sol",
          "result_per_1000", "positions"]


def read(path: Path = PAPER_LOG) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows([{k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS} for r in rows])


def open_trades(rows: list[dict]) -> list[dict]:
    """Paper trades with at least one side not yet closed: [{"trade_id", "date", "positions": [...], "closed": set}]."""
    out = {}
    for r in rows:
        if r["event"] == "open":
            out[r["trade_id"]] = {"trade_id": r["trade_id"], "date": r["date"], "positions": json.loads(r["positions"]),
                                  "closed": set()}
        elif r["event"] == "close" and r["trade_id"] in out:
            out[r["trade_id"]]["closed"].add(r["side"])
    return [t for t in out.values() if len(t["closed"]) < len(t["positions"])]


def state(rows: list[dict]) -> tuple[str | None, set]:
    """What the entry rule needs: is a paper trade still open, and which months already had an entry."""
    months = {r["date"][:7] for r in rows if r["event"] == "open"}
    return ("9999-12-31" if open_trades(rows) else None), months


def record_day(today: str, debit: dict, st: dict, spot: float, atm: float, chain: list[dict] | None,
               path: Path = PAPER_LOG) -> list[dict]:
    """Close what today's real quotes say to close, then log today's decision. Once per UTC day."""
    rows = read(path)
    if any(r["date"] == today and r["event"] in ("open", "no_entry") for r in rows):
        return []                                            # today already recorded: never rewritten
    stamp = st["_stamp"]
    base = {"strategy": stamp["name"], "rules_version": stamp["version"], "rules_fingerprint": stamp["fingerprint"],
            "status": debit.get("status")}
    new = []
    for t in open_trades(rows):
        for p in t["positions"]:
            if p["kind"] in t["closed"]:
                continue
            pos = dict(p, expiry_dt=engine.datetime.fromtimestamp(p["expiry_ms"] / 1000, engine.timezone.utc))
            res = engine.manage_debit(st, pos, today, spot, atm, chain)
            if res:
                why, value = res
                new.append({**base, "date": today, "event": "close", "trade_id": t["trade_id"], "side": p["kind"],
                            "reason": why, "value_per_sol": round(value, 4),
                            "result_per_1000": round(p["share"] * (value - p["cost"]) / p["cost"] * 1000, 1)})
    v = debit.get("view")
    if debit.get("trade") and v:
        tid = f"{today}-{stamp['name']}"
        positions = [{k: x[k] for k in ("kind", "Kl", "Ks", "cost", "share", "expiry", "expiry_ms")}
                     for x in debit["positions"]]
        new.append({**base, "date": today, "event": "open", "trade_id": tid, "direction": v["direction"],
                    "side": "+".join(s["kind"] for s in v["sides"]),
                    "legs": " + ".join(f"{s['kind']} {s['long_leg']['strike']:g}" +
                                       (f"/{s['short_leg']['strike']:g}" if s["short_leg"] else "") +
                                       f" {s['expiry']}" for s in v["sides"]),
                    "cost_per_sol": " + ".join(f"{s['cost']:.4f}" for s in v["sides"]),
                    "contracts": " + ".join(str(s["contracts"]) for s in v["sides"]),
                    "cost_usd": round(v["cost_usd"], 2),
                    "take_profit": " + ".join(f"{s['take_profit_value']:.4f}" if s["take_profit_value"] else "-"
                                              for s in v["sides"]),
                    "time_exit": " + ".join(s["time_exit_date"] or "-" for s in v["sides"]),
                    "positions": json.dumps(positions, separators=(",", ":"))})
    else:
        new.append({**base, "date": today, "event": "no_entry", "reason": debit.get("reason") or ""})
    _append(path, new)
    return new
