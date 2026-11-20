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


def strategy_type(st: dict) -> str:
    """Which engine path a strategy file uses (files written before step 5.2 are bull put spreads)."""
    return st["strategy"].get("type", "bull_put_spread")


def budget_pool(st: dict) -> str:
    return st["strategy"].get("budget_pool", "premium")


def with_strategy(cfg: dict, st: dict) -> dict:
    """The settings view the rest of the code reads: config.toml + the strategy's rules."""
    out = copy.deepcopy(cfg)
    out["spread"] = dict(st["strikes"])
    out["exits"] = dict(st["exits"])
    out["strategy"] = st["_stamp"]
    return out


VARIANT_KEYS = {"name", "exits", "strikes", "direction", "require_verdict", "one_at_a_time"}
SECTION_KEYS = ("exits", "strikes", "direction")


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
        for sec in SECTION_KEYS:
            bad = set(v.get(sec, {})) - set(st.get(sec, {}))
            if bad:
                raise ValueError(f"variant {name!r}: no such {'exit rule' if sec == 'exits' else sec + ' setting'}(s) "
                                 f"{sorted(bad)}")
        s = copy.deepcopy({k: x for k, x in st.items() if k not in ("variants", "evaluation", "selection", "pass_rule")})
        for sec in SECTION_KEYS:
            if v.get(sec):
                s[sec].update(v[sec])
        if "require_verdict" in v:
            s["entry"]["require_verdict"] = v["require_verdict"] or None
        out.append({"name": name, "settings": {k: x for k, x in v.items() if k != "name"},
                    "strategy": s, "one_at_a_time": bool(v.get("one_at_a_time", True))})
    if not out:
        raise ValueError("the strategy file declares no [[variants]] to compare")
    return out


# ------------------------------------------------------------------ the decision
def decide(cfg: dict, st: dict, verdict: dict, chain: list[dict], direction: str | None = None) -> dict:
    """Return {"trade": best candidate or None, "alternates": [...], "reason": str or None}.
    `direction` (bullish / neutral / bearish) is read only by strategy types that use it (debit spreads)."""
    need = st["entry"].get("require_verdict")
    if need and verdict.get("label") != need:
        return {"trade": None, "alternates": [], "candidates": [],
                "reason": f"Volatility is {verdict.get('label')}: {verdict.get('reason', '')}. "
                          f"This strategy only trades when IV is {need.lower()}."}
    if strategy_type(st) == "debit_spread":
        return decide_debit(st, chain, direction or "neutral")
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
def skew_iv(bt: dict, atm: float, F: float, K: float, kind: str = "put") -> float:
    m = math.log(F / K) if kind == "put" else math.log(K / F)
    pts = bt["put_skew"] if kind == "put" else bt.get("call_skew", [[0.0, 1.0], [1.0, 1.0]])
    if m <= pts[0][0]:
        return atm
    for (m0, r0), (m1, r1) in zip(pts, pts[1:]):
        if m <= m1:
            return atm * (r0 + (r1 - r0) * (m - m0) / (m1 - m0))
    (m0, r0), (m1, r1) = pts[-2], pts[-1]
    return atm * (r1 + (r1 - r0) / (m1 - m0) * (m - m1))


def half_spread(bt: dict, F: float, K: float, kind: str = "put") -> float:
    a, b, floor = bt["half_spread"]
    m = math.log(F / K) if kind == "put" else math.log(K / F)
    return max(floor, a + b * max(0.0, m))


def strike_step(S: float) -> float:
    return 0.5 if S < 50 else 1.0 if S < 150 else 2.5 if S < 300 else 5.0


def model_option(bt: dict, F: float, K: float, T: float, atm: float, kind: str) -> dict:
    iv = skew_iv(bt, atm, F, K, kind)
    px = bsmodel.price(F, K, max(T, 0.0), iv, kind)
    hs = half_spread(bt, F, K, kind)
    return {"iv": iv, "mid": px, "bid": max(px * (1 - hs), 0.0), "ask": px * (1 + hs)}


def model_put(bt: dict, F: float, K: float, T: float, atm: float) -> dict:
    return model_option(bt, F, K, T, atm, "put")


def synthetic_chain(st: dict, day: str, spot: float, atm: float) -> list[dict]:
    """Puts (and calls, if the strategy's [backtest] option_types asks for them) for every weekly expiry up to
    max_expiry_days, in the same shape as vol_gauge.build_chain()."""
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
        if "call" in bt.get("option_types", ["put"]):
            for k in range(int(spot / step), int(spot * 2.0 / step) + 1):
                K = round(k * step, 4)
                q = model_option(bt, spot, K, T, atm, "call")
                if q["bid"] < 0.01:
                    continue
                chain.append({"name": f"SOL_USDC-{exp.strftime('%d%b%y').upper().lstrip('0')}-{K:g}-C",
                              "strike": K, "expiry_ms": int(exp.timestamp() * 1000), "expiry": d.isoformat(),
                              "dte": dte, "T": T, "type": "call", "bid": round(q["bid"], 4),
                              "ask": round(q["ask"], 4), "mid": (round(q["bid"], 4) + round(q["ask"], 4)) / 2,
                              "mark": q["mid"], "iv": q["iv"], "oi": 1e9, "F": spot,
                              "contract_size": float(bt["contract_size"]), "min_trade_amount": 1.0})
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


# ================================================================== debit spreads (strategy type "debit_spread")
SIDE_NAME = {"call": "call debit spread", "put": "put debit spread"}


def debit_sides(st: dict, direction: str) -> list[tuple[str, float]]:
    """Which sides to buy and each side's share of the budget, from [direction] and today's reading."""
    mode = st["direction"].get("sides", "direction")
    half = float(st["direction"].get("neutral_split", 0.5))
    if mode == "calls" or (mode == "direction" and direction == "bullish"):
        return [("call", 1.0)]
    if mode == "puts" or (mode == "direction" and direction == "bearish"):
        return [("put", 1.0)]
    return [("call", half), ("put", half)]


def debit_entry_allowed(st: dict, day: str, open_until: str | None, months_entered: set) -> tuple[bool, str]:
    """[entry]: at most N new big-move trades per calendar month, and only when none is still open."""
    e = st["entry"]
    if e.get("only_when_none_open", True) and open_until and day < open_until:
        return False, f"A big-move trade is still open (until {open_until})."
    if int(e.get("max_new_trades_per_month", 1)) >= 1 and day[:7] in months_entered:
        return False, f"Already entered a big-move trade this month ({day[:7]}): one per month."
    return True, ""


def _leg_ok(sc: dict, o: dict) -> bool:
    if not o.get("bid") or not o.get("ask") or not o.get("mid"):
        return False
    spread = o["ask"] - o["bid"]
    if 100.0 * spread / o["mid"] > sc["max_leg_spread_pct_of_mid"] and spread > sc["max_leg_spread_abs_usd"]:
        return False
    return o.get("oi", 0) >= sc.get("min_open_interest", 0)


def find_debit(st: dict, chain: list[dict], kind: str) -> tuple[dict | None, str]:
    """One side: expiry nearest dte_target within [dte_min, dte_max]; bought leg nearest the target delta; sold leg
    about short_offset_pct beyond it; liquidity filters; cost <= max_cost_pct_of_width (spreads)."""
    sc = st["strikes"]
    legs = [o for o in chain if o["type"] == kind and sc["dte_min"] <= o["dte"] <= sc["dte_max"] and o.get("iv")]
    if not legs:
        return None, f"no {kind} expiries between {sc['dte_min']} and {sc['dte_max']} days"
    exps = sorted({o["expiry_ms"]: o["dte"] for o in legs}.items(), key=lambda x: (abs(x[1] - sc["dte_target"]), x[0]))
    target = sc["long_delta"] if kind == "call" else -sc["long_delta"]
    why = "no strikes matched"
    for exp_ms, _ in exps:
        opts = sorted([o for o in legs if o["expiry_ms"] == exp_ms], key=lambda o: o["strike"])
        scored = []
        for o in opts:
            g = suggest.leg_greeks(o)
            if g is not None:
                scored.append((abs(g["delta"] - target), o["strike"], o, g["delta"]))
        if not scored:
            continue
        _, _, lo, ldelta = min(scored, key=lambda x: (x[0], x[1]))
        if not _leg_ok(sc, lo):
            why = f"the {kind} near {target:+.2f} delta fails the liquidity filters"
            continue
        side = {"kind": kind, "long": lo, "short": None, "long_delta": ldelta, "expiry": lo["expiry"],
                "expiry_ms": exp_ms, "dte": round(lo["dte"], 1), "contract_size": lo["contract_size"]}
        if sc.get("structure", "spread") == "outright":
            side.update(cost=lo["ask"], width=None, max_value=None, cost_pct_of_width=None)
            return side, ""
        off = float(sc["short_offset_pct"]) / 100
        aim = lo["strike"] * (1 + off) if kind == "call" else lo["strike"] * (1 - off)
        beyond = [o for o in opts if (o["strike"] > lo["strike"] if kind == "call" else o["strike"] < lo["strike"])]
        if not beyond:
            why = f"no {kind} strike beyond {lo['strike']:g}"
            continue
        so = min(beyond, key=lambda o: (abs(o["strike"] - aim), o["strike"]))
        if not _leg_ok(sc, so):
            why = f"the sold {kind} leg fails the liquidity filters"
            continue
        cost = lo["ask"] - so["bid"]
        width = abs(so["strike"] - lo["strike"])
        if cost <= 0 or 100 * cost / width > float(sc["max_cost_pct_of_width"]):
            why = (f"the {kind} spread costs {100 * cost / width:.0f}% of its width "
                   f"(rule: at most {sc['max_cost_pct_of_width']}%)")
            continue
        side.update(short=so, cost=cost, width=width, max_value=width, cost_pct_of_width=100 * cost / width)
        return side, ""
    return None, why


def decide_debit(st: dict, chain: list[dict], direction: str) -> dict:
    sides, skipped = [], []
    for kind, share in debit_sides(st, direction):
        side, why = find_debit(st, chain, kind)
        if side:
            side["share"] = share
            sides.append(side)
        else:
            skipped.append(f"{SIDE_NAME[kind]}: {why}")
    if not sides:
        return {"trade": None, "alternates": [], "candidates": [],
                "reason": "IV is cheap, but no debit spread passed the filters: " + "; ".join(skipped) + "."}
    return {"trade": {"type": "debit_spread", "direction": direction, "sides": sides, "skipped": skipped},
            "alternates": [], "candidates": [], "reason": None}


def size_debit(cfg: dict, st: dict, trade: dict) -> dict:
    """The big-move budget: reference per month minus what was used this month, split by each side's share."""
    r = cfg.get("risk", {})
    budget = float(r.get("big_move_budget_per_month_usd", st["budget"]["reference_per_month_usd"]))
    left = max(0.0, budget - float(r.get("budget_used_this_month_usd", 0)))
    out = []
    for sd in trade["sides"]:
        per = sd["cost"] * sd["contract_size"]
        n = math.floor(left * sd["share"] / per) if per > 0 else 0
        out.append({"kind": sd["kind"], "contracts": n, "cost_usd": n * per, "cost_per_contract_usd": per,
                    "max_value_usd": None if sd["max_value"] is None else n * sd["max_value"] * sd["contract_size"]})
    return {"budget_month_usd": budget, "budget_left_usd": left, "sides": out,
            "cost_usd": sum(x["cost_usd"] for x in out)}


def exit_plan_debit(st: dict, trade: dict) -> dict:
    e = st["exits"]
    tp, te = float(e.get("take_profit_multiple", 0)), int(e.get("time_exit_dte", 0))
    out = []
    for sd in trade["sides"]:
        # the first daily check (00:00 UTC) at most `te` days before the 08:00 UTC expiry, as manage_debit does
        exp_dt = datetime.fromtimestamp(sd["expiry_ms"] / 1000, timezone.utc) - timedelta(days=te)
        first = exp_dt.date() if exp_dt.time() == time(0, 0) else exp_dt.date() + timedelta(days=1)
        out.append({"kind": sd["kind"], "max_loss": sd["cost"],
                    "take_profit_value": sd["cost"] * tp if tp else None,
                    "time_exit_date": first.isoformat() if te else None})
    text = ["No stop: the most you can lose is what you paid for it."]
    text.append(f"Take profit: sell when the spread is worth {tp:g}x what it cost." if tp else
                "No take profit: hold to the end.")
    text.append(f"Time exit: close anything still open {te} days before expiry." if te else
                "Hold to expiry: settle at the expiry price.")
    return {"sides": out, "text": text}


def open_debit(st: dict, trade: dict, spot: float) -> list[dict]:
    """Entry as the backtest books it: pay the bought leg's ask and receive the sold leg's bid, plus fees."""
    bt = st["backtest"]
    pos = []
    for sd in trade["sides"]:
        cost = sd["cost"] + fee(bt, spot, sd["long"]["mid"]) + (fee(bt, spot, sd["short"]["mid"]) if sd["short"] else 0)
        pos.append({"kind": sd["kind"], "Kl": sd["long"]["strike"], "Ks": sd["short"]["strike"] if sd["short"] else None,
                    "cost": cost, "share": sd["share"], "expiry": sd["expiry"], "expiry_ms": sd["expiry_ms"],
                    "expiry_dt": datetime.fromtimestamp(sd["expiry_ms"] / 1000, timezone.utc)})
    return pos


def _intrinsic(kind: str, K: float, S: float) -> float:
    return max(0.0, S - K) if kind == "call" else max(0.0, K - S)


def debit_value(st: dict, pos: dict, S: float, atm: float, days_left: float, chain: list[dict] | None) -> float:
    """What the position would sell for (bought leg's bid minus sold leg's ask, less fees): real quotes from a stored
    chain when both legs are quoted, otherwise the model."""
    bt = st["backtest"]
    legs = {}
    for o in chain or []:
        if o["type"] == pos["kind"] and o["expiry_ms"] == pos["expiry_ms"] and o["strike"] in (pos["Kl"], pos["Ks"]):
            legs[o["strike"]] = o
    lo, so = legs.get(pos["Kl"]), legs.get(pos["Ks"]) if pos["Ks"] is not None else None
    if lo and lo.get("bid") is not None and (pos["Ks"] is None or (so and so.get("ask") is not None)):
        val = lo["bid"] - fee(bt, S, lo["mid"] or lo["bid"])
        if so:
            val -= so["ask"] + fee(bt, S, so["mid"] or so["ask"])
        return val
    T = days_left / 365
    l = model_option(bt, S, pos["Kl"], T, atm, pos["kind"])
    val = l["bid"] - fee(bt, S, l["mid"])
    if pos["Ks"] is not None:
        s_ = model_option(bt, S, pos["Ks"], T, atm, pos["kind"])
        val -= s_["ask"] + fee(bt, S, s_["mid"])
    return val


def manage_debit(st: dict, pos: dict, day: str, S: float, atm: float,
                 chain: list[dict] | None = None) -> tuple[str, float] | None:
    """One day's check of one bought side. Returns (reason, value per SOL received) or None to hold. No stop."""
    e = st["exits"]
    left = (pos["expiry_dt"] - datetime.combine(date.fromisoformat(day), time(0, 0), timezone.utc)).total_seconds() / 86400
    if left <= 0:
        v = _intrinsic(pos["kind"], pos["Kl"], S) - (_intrinsic(pos["kind"], pos["Ks"], S) if pos["Ks"] is not None else 0)
        return "expiry", v
    value = debit_value(st, pos, S, atm, left, chain)
    tp = float(e.get("take_profit_multiple", 0))
    if tp > 0 and value >= pos["cost"] * tp:
        return "take profit", pos["cost"] * tp             # a resting limit fills at its own price
    te = int(e.get("time_exit_dte", 0))
    if te and left <= te:
        return "time exit", max(value, 0.0)
    return None


def debit_view(st: dict, trade: dict, sizing: dict, plan: dict, spot: float) -> dict:
    """A JSON-friendly summary of a debit idea for the page and logs: legs, cost, sizes, exit plan and a payoff curve
    at expiry (in USD for the suggested sizes), all worked out here so the page only draws."""
    leg = lambda o: None if o is None else {k: o.get(k) for k in ("name", "strike", "bid", "ask", "mid", "iv", "oi")}
    sides = []
    for sd, sz, ep in zip(trade["sides"], sizing["sides"], plan["sides"]):
        sides.append({"kind": sd["kind"], "name": SIDE_NAME[sd["kind"]] if sd["short"] else f"{sd['kind']} option",
                      "long_leg": leg(sd["long"]), "short_leg": leg(sd["short"]), "long_delta": sd["long_delta"],
                      "expiry": sd["expiry"], "dte": sd["dte"], "cost": sd["cost"], "width": sd["width"],
                      "cost_pct_of_width": sd["cost_pct_of_width"], "share": sd["share"], "contracts": sz["contracts"],
                      "contract_size": sd["contract_size"], "cost_usd": sz["cost_usd"], "max_value_usd": sz["max_value_usd"],
                      "take_profit_value": ep["take_profit_value"], "time_exit_date": ep["time_exit_date"]})
    lo, hi = spot * 0.5, spot * 1.6
    curve = []
    for i in range(61):
        S = lo + (hi - lo) * i / 60
        pnl = 0.0
        for sd, sz in zip(trade["sides"], sizing["sides"]):
            v = _intrinsic(sd["kind"], sd["long"]["strike"], S)
            if sd["short"]:
                v -= _intrinsic(sd["kind"], sd["short"]["strike"], S)
            pnl += sz["contracts"] * sd["contract_size"] * (v - sd["cost"])
        curve.append([round(S, 4), round(pnl, 2)])
    return {"type": "debit_spread", "direction": trade["direction"], "sides": sides, "skipped": trade["skipped"],
            "cost_usd": sizing["cost_usd"], "budget_month_usd": sizing["budget_month_usd"],
            "budget_left_usd": sizing["budget_left_usd"], "budget_left_after_usd": sizing["budget_left_usd"] - sizing["cost_usd"],
            "exit_text": plan["text"], "payoff_curve": curve, "spot": spot}
