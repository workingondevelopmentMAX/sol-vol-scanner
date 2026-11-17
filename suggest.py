"""Bull put spread suggester and position sizing. Suggestions only - never orders."""
from __future__ import annotations

import math
from collections import Counter

import bsmodel


def leg_greeks(o: dict) -> dict | None:
    if o["iv"] is None:
        return None
    return bsmodel.greeks(o["F"], o["strike"], o["T"], o["iv"], o["type"])


def spread_math(short_strike: float, long_strike: float, credit: float) -> dict:
    """Per 1 SOL: width, max profit, max loss, break-even for a bull put spread."""
    width = short_strike - long_strike
    return {
        "width": width,
        "max_profit": credit,
        "max_loss": width - credit,
        "break_even": short_strike - credit,
    }


def payoff_at_expiry(short_strike, long_strike, credit, settle) -> float:
    """P&L per 1 SOL at expiry for the short put spread."""
    loss = max(0.0, short_strike - settle) - max(0.0, long_strike - settle)
    return credit - loss


def leg_ok(o: dict, sc: dict, reasons: Counter) -> bool:
    if not o["bid"] or not o["ask"]:
        reasons["a leg has no live bid or ask"] += 1
        return False
    spread = o["ask"] - o["bid"]
    pct = 100.0 * spread / o["mid"]
    if pct > sc["max_leg_spread_pct_of_mid"] and spread > sc["max_leg_spread_abs_usd"]:
        reasons["bid-ask spread too wide"] += 1
        return False
    if o["oi"] < sc["min_open_interest"]:
        reasons["open interest too low"] += 1
        return False
    return True


def find_candidates(cfg: dict, chain: list[dict]) -> tuple[list[dict], Counter]:
    sc = cfg["spread"]
    reasons: Counter = Counter()
    puts = [o for o in chain if o["type"] == "put" and sc["dte_min"] <= o["dte"] <= sc["dte_max"]]
    if not puts:
        reasons[f"no expiries between {sc['dte_min']} and {sc['dte_max']} days"] += 1
        return [], reasons
    by_exp: dict[int, list[dict]] = {}
    for o in puts:
        by_exp.setdefault(o["expiry_ms"], []).append(o)

    cands = []
    for exp, legs in by_exp.items():
        legs.sort(key=lambda o: o["strike"])
        for short in legs:
            g = leg_greeks(short)
            if g is None or not (sc["short_delta_min"] <= g["delta"] <= sc["short_delta_max"]):
                continue
            target = short["strike"] * (1 - sc["long_strike_offset_pct"] / 100.0)
            lower = [o for o in legs if o["strike"] < short["strike"]]
            if not lower:
                reasons["no lower strike for the long put"] += 1
                continue
            long = min(lower, key=lambda o: abs(o["strike"] - target))
            if not (leg_ok(short, sc, reasons) and leg_ok(long, sc, reasons)):
                continue
            gl = leg_greeks(long)
            if gl is None:
                reasons["missing IV on long leg"] += 1
                continue
            credit_cons = short["bid"] - long["ask"]
            credit_mid = short["mid"] - long["mid"]
            m = spread_math(short["strike"], long["strike"], credit_cons)
            if credit_cons <= 0 or 100.0 * credit_cons / m["width"] < sc["min_credit_pct_of_width"]:
                reasons[f"credit below {sc['min_credit_pct_of_width']}% of strike width"] += 1
                continue
            net = {k: gl[k] - g[k] for k in ("delta", "gamma", "theta", "vega")}  # long - short
            cands.append({
                "expiry": short["expiry"],
                "dte": round(short["dte"], 1),
                "short": short, "long": long,
                "short_delta": g["delta"],
                "credit_conservative": credit_cons,
                "credit_mid": credit_mid,
                **m,
                "credit_pct_of_width": 100.0 * credit_cons / m["width"],
                "reward_risk": credit_cons / m["max_loss"] if m["max_loss"] > 0 else math.inf,
                "prob_profit_approx": 1.0 + g["delta"],
                "greeks_per_sol": net,
                "liquidity": {
                    "min_oi": min(short["oi"], long["oi"]),
                    "worst_spread_pct": max(100 * (o["ask"] - o["bid"]) / o["mid"] for o in (short, long)),
                },
                "contract_size": short["contract_size"],
                "delta_gap": abs(g["delta"] - sc["short_delta_target"]),
            })
    cands.sort(key=lambda c: (-round(c["reward_risk"], 3), -c["liquidity"]["min_oi"], c["delta_gap"]))
    return cands, reasons


def size(cfg: dict, cand: dict) -> dict:
    r = cfg["risk"]
    remaining = max(0.0, r["max_total_spread_risk_usd"] - r["current_open_risk_usd"])
    budget = min(r["max_loss_per_trade_usd"], remaining)
    per_contract = cand["max_loss"] * cand["contract_size"]
    n = math.floor(budget / per_contract) if per_contract > 0 else 0
    sol = n * cand["contract_size"]
    return {
        "budget_usd": budget,
        "remaining_total_cap_usd": remaining,
        "max_loss_per_contract_usd": per_contract,
        "contracts": n,
        "sol_notional": sol,
        "usd_at_risk": n * per_contract,
        "credit_usd": n * cand["credit_conservative"] * cand["contract_size"],
        "note": None if n else (
            f"Your limits allow 0 contracts: one contract risks ${per_contract:,.2f} "
            f"but the budget is ${budget:,.2f}. Raise the limit or skip."),
    }


def strip(c: dict) -> dict:
    """JSON-friendly view of a candidate."""
    leg = lambda o: {k: o[k] for k in ("name", "strike", "bid", "ask", "mid", "iv", "oi", "F")}
    out = {k: v for k, v in c.items() if k not in ("short", "long", "delta_gap")}
    out["short_leg"], out["long_leg"] = leg(c["short"]), leg(c["long"])
    if out["reward_risk"] == math.inf:
        out["reward_risk"] = None
    return out
