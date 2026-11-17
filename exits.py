"""Exit plan for a suggested bull put spread: worked out on entry, meant to be placed as resting orders.

All the risk is in the short put (the long put can only gain value as SOL falls), so both stops
buy back the short put only. Once it is closed nothing can lose money; the long put is a free hedge.

Levels come from full Black-76 repricing (delta + gamma), with implied vol assumed to jump as SOL
falls (vega) and a few days assumed to pass (theta). Model estimates, not guarantees.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import bsmodel


def _buyback(short, long, spot, S, iv_pts_per_pct, days):
    """Cost per SOL to buy the spread back if SOL is at S, IV has risen with the drop, `days` have passed."""
    m = S / spot
    drop_pct = max(0.0, (1 - m) * 100)
    out = 0.0
    for o, sign in ((short, 1), (long, -1)):
        T = max(o["T"] - days / 365, 0.0)
        iv = (o["iv"] or 0) + iv_pts_per_pct * drop_pct / 100
        out += sign * bsmodel.price(o["F"] * m, o["strike"], T, iv, "put")
    return out


def _short_put_at(short, spot, S, iv_pts_per_pct, days):
    m = S / spot
    drop_pct = max(0.0, (1 - m) * 100)
    T = max(short["T"] - days / 365, 0.0)
    return bsmodel.price(short["F"] * m, short["strike"], T, (short["iv"] or 0) + iv_pts_per_pct * drop_pct / 100, "put")


def plan(cfg: dict, cand: dict, sizing: dict, spot: float) -> dict:
    e = cfg.get("exits", {})
    tp = float(e.get("take_profit_pct", 50)) / 100
    k = float(e.get("stop_multiple", 1.5))
    beta = float(e.get("iv_points_per_pct_drop", 1.0))
    days = float(e.get("stop_assumed_days", 3))
    t_dte = int(e.get("time_exit_dte", 7))
    split = bool(e.get("split_take_profit", False))
    slip = float(e.get("stop_limit_buffer_pct", 30)) / 100

    s, l = cand["short"], cand["long"]
    credit, width = cand["credit_conservative"], cand["width"]
    n = sizing["contracts"] or 1
    qty_sol = n * cand["contract_size"]
    illustrative = sizing["contracts"] == 0

    # 1. take profit: resting limit to buy the whole spread back cheaply
    tp_price = round(credit * (1 - tp), 2)

    # 2. price stop: SOL level where buying back costs credit + k x credit
    target = min(credit * (1 + k), width * 0.98)
    lo, hi = spot * 0.2, spot
    if _buyback(s, l, spot, spot, beta, days) >= target:
        stop_S = spot
    else:
        for _ in range(80):
            mid = (lo + hi) / 2
            if _buyback(s, l, spot, mid, beta, days) > target:
                lo = mid
            else:
                hi = mid
        stop_S = hi
    stop_S_naive = stop_S
    lo, hi = spot * 0.2, spot
    for _ in range(80):  # same stop with no vol jump, to show what the vega assumption changes
        mid = (lo + hi) / 2
        if _buyback(s, l, spot, mid, 0.0, days) > target:
            lo = mid
        else:
            hi = mid
    stop_S_naive = hi

    # 3. backup stop on the short put's own mark price (catches gaps and vol spikes)
    mark_trigger = _short_put_at(s, spot, stop_S, beta, days)
    limit_cap = round(min(mark_trigger * (1 + slip), s["strike"]), 2)

    # 4. time exit
    exp = datetime.fromisoformat(cand["expiry"])
    time_exit = (exp - timedelta(days=t_dte)).strftime("%Y-%m-%d")

    loss_at_stop = target - credit
    gain = credit * ((tp + 1) / 2 if split else tp)
    be_wr = loss_at_stop / (gain + loss_at_stop) if gain + loss_at_stop > 0 else None
    leg_s, leg_l = s["name"], l["name"]
    q = f"{n} contract{'s' if n != 1 else ''} ({qty_sol:g} SOL)"

    steps = [
        {"key": "take_profit", "title": "Take profit",
         "level": f"Spread costs ${tp_price:.2f} or less to buy back",
         "result": f"Keeps about ${(credit - tp_price) * qty_sol:,.0f} of the ${credit * qty_sol:,.0f} credit"
                   + (" on half; the other half rides to expiry" if split else ""),
         "why": "Most of the reward arrives early, while the last weeks carry most of the risk. "
                "A resting order takes the profit for you, so there is no decision to freeze on.",
         "ticket": f"BUY {'half of ' if split else ''}{q} of the spread: buy {leg_s}, sell {leg_l}. "
                   f"Limit ${tp_price:.2f} per SOL (net debit). Good till cancelled."},
        {"key": "price_stop", "title": "Price stop (main protection)",
         "level": f"SOL index at or below ${stop_S:,.2f} ({stop_S / spot - 1:+.1%})",
         "result": f"Loss about ${loss_at_stop * qty_sol:,.0f} instead of the ${cand['max_loss'] * qty_sol:,.0f} maximum",
         "why": f"Set where the trade has lost {k:g}x the credit, allowing for implied vol rising "
                f"{beta:g} point per 1% drop and {days:g} days passing. Without the vol allowance it would sit at "
                f"${stop_S_naive:,.2f}: the jump in fear moves the exit earlier.",
         "ticket": f"BUY {q} of {leg_s} only. Trigger: SOL index price at or below ${stop_S:,.2f}. "
                   f"Limit ${limit_cap:.2f}. Reduce-only. Good till cancelled."},
        {"key": "vol_stop", "title": "Backup stop (gaps and fear spikes)",
         "level": f"Short put's mark price at or above ${mark_trigger:.2f}",
         "result": "Fires if the put you sold gets that expensive by any route: a gap down, a fear spike, or both",
         "why": "A second trigger on the option itself, in case SOL jumps past the price stop "
                "or implied vol spikes first. Whichever fires first closes the trade; reduce-only stops the other doing anything.",
         "ticket": f"BUY {q} of {leg_s} only. Trigger: mark price at or above ${mark_trigger:.2f}. "
                   f"Limit ${limit_cap:.2f}. Reduce-only. Good till cancelled."},
        {"key": "time_exit", "title": "Time exit",
         "level": f"On {time_exit} ({t_dte} days before expiry)",
         "result": "Close whatever is still open, win or lose",
         "why": "In the last week gamma is at its highest: small moves cause big swings. "
                "This one cannot be a resting order, so the scanner will flag it.",
         "ticket": f"On {time_exit}: cancel the other orders, then buy back the spread at the market's offer."},
    ]
    after = ("Only the short put is closed. Your long put stays: it can only gain value if SOL "
             "keeps falling, so hold it as a free hedge or sell it later. A stop firing means SOL is cheaper: "
             "that is a planned moment for your USDC reserve to buy spot.")
    return {
        "settings": {"take_profit_pct": tp * 100, "stop_multiple": k, "iv_points_per_pct_drop": beta,
                     "stop_assumed_days": days, "time_exit_dte": t_dte, "split_take_profit": split},
        "illustrative_one_contract": illustrative,
        "take_profit_price": tp_price,
        "price_stop_spot": stop_S,
        "price_stop_spot_no_vol": stop_S_naive,
        "mark_stop_short_put": mark_trigger,
        "stop_limit_price": limit_cap,
        "time_exit_date": time_exit,
        "loss_at_stop_usd": loss_at_stop * qty_sol,
        "max_loss_usd": cand["max_loss"] * qty_sol,
        "breakeven_win_rate": be_wr,
        "prob_profit_approx": cand["prob_profit_approx"],
        "steps": steps,
        "after_stop": after,
        "order_note": ("On Deribit a buy that triggers when SOL falls may be listed as a 'take' order type rather than "
                       "'stop'. Check the trigger direction on the test site before using it with real money."),
    }
