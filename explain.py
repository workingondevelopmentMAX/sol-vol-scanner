"""Plain-language Greeks and 'what if' scenarios for a suggested bull put spread."""
from __future__ import annotations

import bsmodel


def _usd(x: float) -> str:
    sign = "-" if x < 0 else ""
    x = abs(x)
    return f"{sign}${x:,.0f}" if x >= 100 else f"{sign}${x:,.2f}"


def _spread_value(short, long, spot_mult=1.0, iv_shift=0.0, days=0.0) -> float:
    """Value per SOL of the position (long the lower put, short the higher put)."""
    def p(o):
        T = max(o["T"] - days / 365.0, 0.0)
        iv = max((o["iv"] or 0) + iv_shift, 0.01)
        return bsmodel.price(o["F"] * spot_mult, o["strike"], T, iv, "put")
    return p(long) - p(short)


def what_if(cand: dict, qty_sol: float) -> dict:
    s, l = cand["short"], cand["long"]
    base = _spread_value(s, l)
    price_rows = [{"move_pct": m,
                   "pnl_usd": (_spread_value(s, l, 1 + m / 100, 0, 1) - base) * qty_sol}
                  for m in (-10, -5, 0, 5, 10)]
    iv_rows = [{"iv_points": d, "pnl_usd": (_spread_value(s, l, 1, d / 100) - base) * qty_sol}
               for d in (-10, 10)]
    return {"price_tomorrow": price_rows, "iv_today": iv_rows,
            "label": "Model estimate (Black-76 repricing with Deribit mark IVs) - approximate"}


def greeks_panel(cand: dict, sizing: dict, spot: float | None = None) -> dict:
    illustrative = sizing["contracts"] == 0
    qty = cand["contract_size"] * (1 if illustrative else sizing["contracts"])
    g = {k: v * qty for k, v in cand["greeks_per_sol"].items()}
    ks, kl = cand["short"]["strike"], cand["long"]["strike"]
    spot = spot or cand["short"]["F"]
    size_note = ("Figures are for 1 contract (your limits currently allow 0)." if illustrative
                 else f"Figures are for your suggested size: {sizing['contracts']} contract(s), {qty:g} SOL.")

    items = [
        {"greek": "Delta", "value": g["delta"], "unit": "$ per $1 move in SOL",
         "now": f"If SOL moves $1, this position gains or loses about {_usd(abs(g['delta']))}. "
                f"Delta is positive, so you benefit if SOL rises.",
         "shift": f"As SOL falls toward your ${ks:g} short put, delta grows, so each further $1 drop hurts more. "
                  f"As SOL rises, delta shrinks toward zero and the position goes quiet."},
        {"greek": "Theta", "value": g["theta"], "unit": "$ per day",
         "now": f"Each day that passes with SOL roughly unchanged, this position gains about {_usd(g['theta'])}.",
         "shift": "Theta grows as expiry approaches, especially near the short strike, so the last two weeks earn "
                  f"fastest but are also the jumpiest. If SOL falls below your ${kl:g} long put, theta can turn against you."},
        {"greek": "Gamma", "value": g["gamma"], "unit": "change in delta per $1",
         "now": f"How fast delta changes: a $1 move in SOL changes delta by about {abs(g['gamma']):.3f} "
                f"(a $5 move shifts your $-per-$1 figure by about {_usd(abs(g['gamma']) * 5)}). "
                "Gamma is negative, so moves feed on themselves against you.",
         "shift": f"Gamma spikes when SOL is near ${ks:g} close to expiry. That is when small moves cause big swings "
                  "in profit and loss, and the usual moment to close or roll."},
        {"greek": "Vega", "value": g["vega"], "unit": "$ per 1 vol point",
         "now": f"If implied volatility rises 1 point, this position loses about {_usd(abs(g['vega']))}; "
                f"if it falls 1 point, it gains about {_usd(abs(g['vega']))}. You are short vega: you want fear to fade.",
         "shift": "A selloff usually lifts IV, so you can show a paper loss before SOL reaches your strikes. "
                  "Vega shrinks as expiry approaches, so later in the trade price matters more than IV."},
    ]
    summary = {
        "hurts_most": f"A fast drop toward ${ks:g} with rising IV (SOL is ${spot:,.2f} now).",
        "helps_most": "Time passing quietly with SOL flat or higher, and IV easing.",
    }
    return {"size_note": size_note, "qty_sol": qty, "items": items, "summary": summary,
            "what_if": what_if(cand, qty)}
