"""Plain-language 'why this trade' and a conviction score.

Conviction is a transparent 0-100 score built from five parts, so you can see what drives it.
Low conviction means: skipping today is fine. Not trading is a valid outcome of the process.
"""
from __future__ import annotations

import math


def _scale(x, lo, hi):
    """0 at lo, 1 at hi, clipped."""
    if x is None:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def market_context(chain_ts: list[dict], closes: list[float], spot: float) -> dict:
    ret30 = closes[-1] / closes[-31] - 1 if len(closes) > 31 else None
    ret7 = closes[-1] / closes[-8] - 1 if len(closes) > 8 else None
    near = [r for r in chain_ts if 5 <= r["dte"] <= 16]
    far = [r for r in chain_ts if 45 <= r["dte"] <= 100]
    inverted = None
    if near and far:
        inverted = near[0]["atm_iv"] - far[-1]["atm_iv"] > 0.03
    hi30 = max(closes[-31:]) if len(closes) > 31 else None
    return {"ret30": ret30, "ret7": ret7, "term_inverted": inverted,
            "off_30d_high": spot / hi30 - 1 if hi30 else None}


def conviction(cfg: dict, verdict: dict, s: dict, spot: float) -> dict:
    """Score a suggested bull put spread. Returns score, label, action and the parts."""
    c = cfg.get("conviction", {})
    parts = []

    # 1. How expensive options are (the reason to sell premium at all)
    if verdict.get("mode") == "percentile":
        v = _scale(verdict.get("percentile"), 50, 90)
        why = f"IV at the {verdict['percentile']:.0f}th percentile (full marks at 90th)"
    else:
        v = _scale(verdict.get("iv_rv_ratio"), 1.1, 1.6)
        why = f"IV is {verdict.get('iv_rv_ratio') or 0:.2f}x realised vol (full marks at 1.6x)"
    parts.append({"name": "Options are expensive", "weight": 30, "score": v, "why": why})

    # 2. Cushion: how far SOL can fall before you lose, versus a typical move to expiry
    exp_move = spot * (verdict.get("atm_iv") or 0) * math.sqrt(max(s["dte"], 1) / 365)
    cushion = spot - s["break_even"]
    sig = cushion / exp_move if exp_move else 0
    parts.append({"name": "Cushion before a loss", "weight": 25, "score": _scale(sig, 0.4, 1.0), "ratio": sig,
                  "why": f"SOL can fall {cushion / spot:.0%} to ${s['break_even']:.2f}: {sig:.1f}x a typical move to expiry (full marks at 1.0x)"})

    # 3. Pay for the risk
    parts.append({"name": "Pay for the risk", "weight": 20, "score": _scale(s["credit_pct_of_width"], 12, 22),
                  "why": f"Credit is {s['credit_pct_of_width']:.0f}% of the strike gap (full marks at 22%)"})

    # 4. Liquidity: can you get in and out near fair value
    liq = 0.6 * _scale(-s["liquidity"]["worst_spread_pct"], -15, -4) + 0.4 * _scale(s["liquidity"]["min_oi"], 50, 1000)
    parts.append({"name": "Easy to trade", "weight": 15, "score": liq,
                  "why": f"Worst leg bid-ask {s['liquidity']['worst_spread_pct']:.0f}% of mid, open interest at least {s['liquidity']['min_oi']:,.0f} SOL"})

    # 5. Data quality: how much IV history backs the verdict
    hd = verdict.get("history_days", 0)
    parts.append({"name": "Confidence in the data", "weight": 10, "score": _scale(hd, 10, 120),
                  "why": f"{hd} days of IV history behind the rich/cheap call (full marks at 120)"})

    score = round(sum(p["weight"] * p["score"] for p in parts))
    caps = []
    if verdict.get("mode") != "percentile":
        score = min(score, 69)
        caps.append("Capped at Medium until there are 60+ days of IV history.")
    hi, mid = c.get("high", 70), c.get("medium", 45)
    if score >= hi:
        label, action = "High", "Take it at the suggested size."
    elif score >= mid:
        label, action = "Medium", "Optional. If you take it, consider half size."
    else:
        label, action = "Low", "Skip today. The best available spread is not attractive enough."
    return {"score": score, "label": label, "action": action, "parts": parts, "caps": caps}


def why_trade(cfg: dict, verdict: dict, s: dict, ctx: dict, spot: float, conv: dict) -> dict:
    risk = cfg["risk"]
    z = s["sizing"]
    market = []
    if verdict.get("mode") == "percentile":
        p = verdict["percentile"]
        how = "very well" if p >= 80 else "well" if p >= 65 else "a little better than usual"
        market.append(f"Options are pricing SOL to move {verdict['atm_iv']:.0%} a year, the {p:.0f}th percentile "
                      f"of recent history: sellers are being paid {how}.")
    else:
        market.append(f"Options are pricing SOL to move {verdict['atm_iv']:.0%} a year, but it has actually been moving "
                      f"{verdict['rv30']:.0%} over the last 30 days. Sellers are being paid for more movement than is happening.")
    if ctx.get("ret30") is not None:
        mood = ("after a sell-off" if ctx["ret30"] < -0.08 else "after a rally" if ctx["ret30"] > 0.08 else "after a fairly flat month")
        market.append(f"SOL is {ctx['ret30']:+.0%} over 30 days ({mood}); it is {abs(ctx['off_30d_high'] or 0):.0%} below its 30-day high.")
    if ctx.get("term_inverted"):
        market.append("Near-dated options cost more than later ones: the market is nervous right now. "
                      "Fear pays more premium, but moves can be sharp, so respect the size limit.")
    elif ctx.get("term_inverted") is False:
        market.append("Longer-dated options cost more than near ones, the normal calm shape.")
    market.append(f"You only lose if SOL is below ${s['break_even']:.2f} on {s['expiry']}, "
                  f"{(spot - s['break_even']) / spot:.0%} under today's ${spot:,.2f}.")

    sol_bought = z["credit_usd"] / spot if spot else 0
    goals = [
        f"Long-term accumulation: the ${z['credit_usd']:,.2f} credit can buy about {sol_bought:.2f} SOL now. "
        "The premium funds your SOL buying instead of sitting idle.",
        f"Defined risk, as you asked: the most you can lose is ${z['usd_at_risk']:,.2f}, inside your "
        f"${risk['max_loss_per_trade_usd']:,.0f} per-trade limit. No open-ended short puts.",
        f"If it goes wrong, SOL will be below ${s['break_even']:.2f}. As a long-term bull that is a price you'd want to "
        "add at. That is the moment your USDC reserve should buy spot, which is exactly when fear usually stops you.",
        "The rules picked this, not your mood on the day. That is the point: acting on a plan at the moments "
        "you would normally freeze.",
    ]
    return {"market": market, "goals": goals}


def why_no_trade(verdict: dict, reason: str) -> dict:
    goals = [
        "Not trading is a valid result. Your plan is to sell premium only when you're paid well for it.",
        "Your core SOL stays untouched either way; nothing about the long-term holding changes.",
    ]
    if verdict.get("label") == "CHEAP":
        goals.append("When options are cheap, it is the buyer's turn: this is when protection (setup 4) "
                     "or a call spread (setup 3) cost least. The scanner does not suggest those yet.")
    return {"market": [reason], "goals": goals}
