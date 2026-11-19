"""The one decision engine, shared by the daily scan and the backtest.

    strategy file  +  a day's market (option chain, verdict)  ->  trade or no trade, then the exit plan
    (live: the real Deribit chain; backtest: that day's stored real chain from data/chains/ when there is one,
     otherwise a chain rebuilt from that day's ATM IV, priced the same way)

Nothing here fetches data or calls an LLM: same inputs, same outputs, every time.
"""
from __future__ import annotations

import copy
import hashlib
import math
import tomllib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import bsmodel
import chain_store
import exits
import suggest
import vol_gauge

ROOT = Path(__file__).resolve().parent
DEFAULT_STRATEGY = ROOT / "strategies" / "bull_put_spread.toml"


# ------------------------------------------------------------------ strategy files
def load_strategy(path: Path | str = DEFAULT_STRATEGY) -> dict:
    raw = Path(path).read_bytes()
    st = tomllib.loads(raw.decode("utf-8"))
    st["_stamp"] = {"name": st["strategy"]["name"], "title": st["strategy"].get("title", st["strategy"]["name"]),
                    "version": st["strategy"]["version"], "file": Path(path).name,
                    "fingerprint": hashlib.sha256(raw).hexdigest()[:12]}
    return st


def with_strategy(cfg: dict, st: dict) -> dict:
    """The settings view the rest of the code reads: config.toml + the strategy's rules."""
    out = copy.deepcopy(cfg)
    out["spread"] = dict(st["strikes"])
    out["exits"] = dict(st["exits"])
    out["strategy"] = st["_stamp"]
    return out


VARIANT_KEYS = {"name", "exits", "require_verdict", "one_at_a_time"}


def variants(st: dict) -> list[dict]:
    """The comparisons declared in the strategy file's [[variants]] list, each as a full strategy.

    Returns [{"name", "settings" (the overrides as written), "strategy", "one_at_a_time"}]. Unknown keys or
    exit rules that the strategy file does not have are refused, so a typo can never silently test nothing."""
    out, seen = [], set()
    for v in st.get("variants", []):
        name = v.get("name")
        if not name or name in seen:
            raise ValueError(f"[[variants]] entries need a unique name (got {name!r})")
        seen.add(name)
        bad = set(v) - VARIANT_KEYS
        if bad:
            raise ValueError(f"variant {name!r}: unknown setting(s) {sorted(bad)}")
        ex = v.get("exits", {})
        bad = set(ex) - set(st["exits"])
        if bad:
            raise ValueError(f"variant {name!r}: no such exit rule(s) {sorted(bad)}")
        s = copy.deepcopy({k: x for k, x in st.items() if k not in ("variants", "evaluation")})
        s["exits"].update(ex)
        if "require_verdict" in v:
            s["entry"]["require_verdict"] = v["require_verdict"] or None
        out.append({"name": name, "settings": {k: x for k, x in v.items() if k != "name"},
                    "strategy": s, "one_at_a_time": bool(v.get("one_at_a_time", True))})
    if not out:
        raise ValueError("the strategy file declares no [[variants]] to compare")
    return out


# ------------------------------------------------------------------ the decision
def decide(cfg: dict, st: dict, verdict: dict, chain: list[dict]) -> dict:
    """Return {"trade": best candidate or None, "alternates": [...], "reason": str or None}."""
    need = st["entry"].get("require_verdict")
    if need and verdict.get("label") != need:
        return {"trade": None, "alternates": [], "candidates": [],
                "reason": f"Volatility is {verdict.get('label')}: {verdict.get('reason', '')}. "
                          f"This strategy only trades when IV is {need.lower()}."}
    view = with_strategy(cfg, st)
    cands, reasons = suggest.find_candidates(view, chain)
    if not cands:
        why = "; ".join(f"{k} ({n})" for k, n in reasons.most_common(4)) or "no strikes matched the rules"
        return {"trade": None, "alternates": [], "candidates": [],
                "reason": f"IV is {verdict.get('label', '').lower()}, but no put spread passed the filters: {why}."}
    k = int(st["strikes"].get("max_alternates", 2))
    return {"trade": cands[0], "alternates": cands[1:1 + k], "candidates": cands, "reason": None}


def exit_plan(cfg: dict, st: dict, cand: dict, sizing: dict, spot: float) -> dict:
    return exits.plan(with_strategy(cfg, st), cand, sizing, spot)


# ------------------------------------------------------------------ rebuilding a past day's chain
def skew_iv(bt: dict, atm: float, F: float, K: float) -> float:
    m = math.log(F / K)
    pts = bt["put_skew"]
    if m <= pts[0][0]:
        return atm
    for (m0, r0), (m1, r1) in zip(pts, pts[1:]):
        if m <= m1:
            return atm * (r0 + (r1 - r0) * (m - m0) / (m1 - m0))
    (m0, r0), (m1, r1) = pts[-2], pts[-1]
    return atm * (r1 + (r1 - r0) / (m1 - m0) * (m - m1))


def half_spread(bt: dict, F: float, K: float) -> float:
    a, b, floor = bt["half_spread"]
    return max(floor, a + b * max(0.0, math.log(F / K)))


def strike_step(S: float) -> float:
    return 0.5 if S < 50 else 1.0 if S < 150 else 2.5 if S < 300 else 5.0


def model_put(bt: dict, F: float, K: float, T: float, atm: float) -> dict:
    iv = skew_iv(bt, atm, F, K)
    px = bsmodel.price(F, K, max(T, 0.0), iv, "put")
    hs = half_spread(bt, F, K)
    return {"iv": iv, "mid": px, "bid": max(px * (1 - hs), 0.0), "ask": px * (1 + hs)}


def synthetic_chain(st: dict, day: str, spot: float, atm: float) -> list[dict]:
    """Puts for every weekly expiry up to max_expiry_days, in the same shape as vol_gauge.build_chain()."""
    bt = st["backtest"]
    now = datetime.combine(date.fromisoformat(day), time(0, 0), timezone.utc)
    step = strike_step(spot)
    chain = []
    d = now.date()
    for _ in range(int(bt["max_expiry_days"])):
        d += timedelta(days=1)
        if d.weekday() != int(bt["expiry_weekday"]):
            continue
        exp = datetime.combine(d, time(8, 0), timezone.utc)
        dte = (exp - now).total_seconds() / 86400
        T = dte / 365
        for k in range(int(spot * 0.5 / step), int(spot / step) + 1):
            K = round(k * step, 4)
            q = model_put(bt, spot, K, T, atm)
            if q["bid"] < 0.01:
                continue
            chain.append({"name": f"SOL_USDC-{exp.strftime('%d%b%y').upper().lstrip('0')}-{K:g}-P",
                          "strike": K, "expiry_ms": int(exp.timestamp() * 1000), "expiry": d.isoformat(),
                          "dte": dte, "T": T, "type": "put", "bid": round(q["bid"], 4), "ask": round(q["ask"], 4),
                          "mid": (round(q["bid"], 4) + round(q["ask"], 4)) / 2, "mark": q["mid"], "iv": q["iv"],
                          "oi": 1e9, "F": spot, "contract_size": float(bt["contract_size"]), "min_trade_amount": 1.0})
    return chain


def stored_chain(day: str, chains_dir=chain_store.CHAINS_DIR) -> list[dict] | None:
    """The real chain recorded on that day (data/chains/), in the same shape as a live run's chain, or None."""
    snap = chain_store.load(day, chains_dir)
    return vol_gauge.build_chain(snap) if snap else None


# ------------------------------------------------------------------ managing an open trade
def fee(bt: dict, S: float, px: float) -> float:
    return min(bt["fee_rate"] * S, bt["fee_cap"] * px) if px > 0 else 0.0


def buyback_cost(st: dict, pos: dict, S: float, atm: float, days_left: float) -> tuple[float, float]:
    """Cost per SOL to close the spread (pay the short put's offer, hit the long put's bid) + fees; short put mark."""
    bt = st["backtest"]
    T = days_left / 365
    s = model_put(bt, S, pos["Ks"], T, atm)
    l = model_put(bt, S, pos["Kl"], T, atm)
    return s["ask"] - l["bid"] + fee(bt, S, s["mid"]) + fee(bt, S, l["mid"]), s["mid"]


def real_buyback_cost(st: dict, pos: dict, S: float, chain: list[dict] | None) -> tuple[float, float] | None:
    """Same as buyback_cost, but from a stored real chain: short put's ask, long put's bid, short put's mark.
    None when either leg is missing or lacks those quotes (the model is used instead)."""
    if not chain:
        return None
    legs = {}
    for o in chain:
        if o["type"] == "put" and o["expiry_ms"] == pos["expiry_ms"] and o["strike"] in (pos["Ks"], pos["Kl"]):
            legs[o["strike"]] = o
    s, l = legs.get(pos["Ks"]), legs.get(pos["Kl"])
    if not s or not l or s["ask"] is None or s["mark"] is None or l["bid"] is None:
        return None
    bt = st["backtest"]
    s_px, l_px = s["mid"] or s["mark"], l["mid"] or l["mark"] or l["bid"]
    return s["ask"] - l["bid"] + fee(bt, S, s_px) + fee(bt, S, l_px), s["mark"]


def manage(st: dict, pos: dict, day: str, S: float, atm: float,
           chain: list[dict] | None = None) -> tuple[str, float] | None:
    """One day's check of an open spread against its exit plan. Returns (reason, pnl per SOL) or None to hold.
    With that day's stored real chain, the buy-back cost comes from real quotes; otherwise from the model."""
    e = st["exits"]
    left = (pos["expiry_dt"] - datetime.combine(date.fromisoformat(day), time(0, 0), timezone.utc)).total_seconds() / 86400
    if left <= 0:
        loss = max(0.0, pos["Ks"] - S) - max(0.0, pos["Kl"] - S)
        return "expiry", pos["credit"] - loss
    cost, short_mark = real_buyback_cost(st, pos, S, chain) or buyback_cost(st, pos, S, atm, left)
    if float(e.get("stop_multiple", 0)) > 0 and (S <= pos["stop_spot"] or short_mark >= pos["stop_mark"]):
        return "stop", pos["credit"] - cost
    tp = float(e.get("take_profit_pct", 0)) / 100
    if tp > 0 and cost <= pos["credit"] * (1 - tp):
        return "take profit", pos["credit"] * tp           # a resting limit fills at its own price
    t_exit = int(e.get("time_exit_dte", 0))
    if t_exit and left <= t_exit:
        return "time exit", pos["credit"] - cost
    return None


def open_position(cfg: dict, st: dict, cand: dict, spot: float) -> dict:
    """Entry as the backtest books it: the candidate's conservative credit less entry fees, plus its exit levels."""
    bt = st["backtest"]
    plan = exit_plan(cfg, st, cand, {"contracts": 1}, spot)
    credit = cand["credit_conservative"] - fee(bt, spot, cand["short"]["mid"]) - fee(bt, spot, cand["long"]["mid"])
    return {"Ks": cand["short"]["strike"], "Kl": cand["long"]["strike"], "credit": credit,
            "width": cand["width"], "max_loss": cand["width"] - credit, "expiry": cand["expiry"],
            "expiry_ms": cand["short"]["expiry_ms"],
            "expiry_dt": datetime.fromtimestamp(cand["short"]["expiry_ms"] / 1000, timezone.utc),
            "stop_spot": plan["price_stop_spot"], "stop_mark": plan["mark_stop_short_put"],
            "delta": cand["short_delta"]}
