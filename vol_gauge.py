"""Turns raw Deribit data into an option chain, ATM IV, realised vol and a RICH/FAIR/CHEAP verdict."""
from __future__ import annotations

import csv
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY_CSV = ROOT / "data" / "iv_history.csv"
HISTORY_FIELDS = ["date", "index_price", "atm_iv_30d", "rv20", "rv30", "iv_source"]

DAY_MS = 86400 * 1000
YEAR_DAYS = 365.0


# ---------------------------------------------------------------- helpers
def normalize_iv(x):
    """Deribit may report IV as a percent (65.2) or decimal (0.652). Return decimal or None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v / 100.0 if v > 3 else v


def _num(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


_NAME = re.compile(r"^(?P<u>[A-Z]+)_USDC-(?P<exp>\d{1,2}[A-Z]{3}\d{2})-(?P<k>[\dd.]+)-(?P<t>[CP])$")


def parse_name(name: str):
    m = _NAME.match(name)
    if not m:
        return None
    exp = datetime.strptime(m["exp"], "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
    return {
        "strike": float(m["k"].replace("d", ".")),
        "expiration_timestamp": int(exp.timestamp() * 1000),
        "option_type": "call" if m["t"] == "C" else "put",
    }


# ---------------------------------------------------------------- chain
def build_chain(snap: dict, now_ms: int | None = None) -> list[dict]:
    """One dict per live SOL option with quotes, IV, forward and time to expiry."""
    now_ms = now_ms or int(datetime.fromisoformat(snap["fetched_at"]).timestamp() * 1000)
    meta = {i["instrument_name"]: i for i in snap["instruments"]}
    chain = []
    for b in snap["book_summary"]:
        name = b["instrument_name"]
        m = meta.get(name) or {}
        parsed = parse_name(name) or {}
        strike = m.get("strike", parsed.get("strike"))
        exp_ms = m.get("expiration_timestamp", parsed.get("expiration_timestamp"))
        kind = m.get("option_type", parsed.get("option_type"))
        if strike is None or exp_ms is None or kind is None:
            continue
        dte = (exp_ms - now_ms) / DAY_MS
        if dte <= 0.05:
            continue
        bid, ask = _num(b.get("bid_price")), _num(b.get("ask_price"))
        mid = (bid + ask) / 2 if bid and ask else None
        chain.append({
            "name": name,
            "strike": float(strike),
            "expiry_ms": int(exp_ms),
            "expiry": datetime.fromtimestamp(exp_ms / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "dte": dte,
            "T": dte / YEAR_DAYS,
            "type": kind,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "mark": _num(b.get("mark_price")),
            "iv": normalize_iv(b.get("mark_iv")),
            "oi": float(b.get("open_interest") or 0),
            "F": _num(b.get("underlying_price")) or float(snap["index_price"]),
            "contract_size": float(m.get("contract_size") or 1.0),
            "min_trade_amount": float(m.get("min_trade_amount") or 0),
        })
    return chain


def expiries(chain):
    out = {}
    for o in chain:
        out.setdefault(o["expiry_ms"], []).append(o)
    return dict(sorted(out.items()))


def atm_iv_for_expiry(opts: list[dict]):
    """Interpolate mark IV at the forward, using OTM options (puts below F, calls above)."""
    F = statistics.median(o["F"] for o in opts)
    by_k = {}
    for o in opts:
        if o["iv"] is None:
            continue
        by_k.setdefault(o["strike"], {})[o["type"]] = o["iv"]
    pts = []
    for k, d in sorted(by_k.items()):
        pref = "put" if k < F else "call"
        iv = d.get(pref) or d.get("call") or d.get("put")
        pts.append((k, iv))
    if not pts:
        return None, F
    below = [p for p in pts if p[0] <= F]
    above = [p for p in pts if p[0] >= F]
    if below and above:
        (k1, v1), (k2, v2) = below[-1], above[0]
        if k2 == k1:
            return v1, F
        w = (F - k1) / (k2 - k1)
        return v1 + w * (v2 - v1), F
    return min(pts, key=lambda p: abs(p[0] - F))[1], F


def term_structure(chain) -> list[dict]:
    rows = []
    for exp_ms, opts in expiries(chain).items():
        iv, F = atm_iv_for_expiry(opts)
        if iv is None:
            continue
        rows.append({"expiry": opts[0]["expiry"], "dte": round(opts[0]["dte"], 2), "atm_iv": iv, "forward": F})
    return rows


def constant_maturity_iv(ts: list[dict], target_dte: float):
    """ATM IV for exactly target_dte days, interpolating total variance between neighbouring expiries."""
    pts = [r for r in ts if r["dte"] >= 1]
    if not pts:
        return None
    lo = [r for r in pts if r["dte"] <= target_dte]
    hi = [r for r in pts if r["dte"] >= target_dte]
    if lo and hi:
        a, b = lo[-1], hi[0]
        if a["dte"] == b["dte"]:
            return a["atm_iv"]
        va, vb = a["atm_iv"] ** 2 * a["dte"], b["atm_iv"] ** 2 * b["dte"]
        w = (target_dte - a["dte"]) / (b["dte"] - a["dte"])
        return math.sqrt((va + w * (vb - va)) / target_dte)
    return min(pts, key=lambda r: abs(r["dte"] - target_dte))["atm_iv"]


# ---------------------------------------------------------------- realised vol
def daily_closes(chart: dict) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, close)] from get_tradingview_chart_data output, oldest first, one per day."""
    out = {}
    for t, c in zip(chart.get("ticks", []), chart.get("close", [])):
        if c:
            out[datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")] = float(c)
    return sorted(out.items())


def realised_vol(closes: list[float], window: int):
    """Annualised (365-day) stdev of daily log returns over the last `window` returns."""
    if len(closes) < window + 1:
        return None
    seg = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(seg[:-1], seg[1:])]
    return statistics.stdev(rets) * math.sqrt(YEAR_DAYS)


def rolling_rv(closes_by_day: list[tuple[str, float]]) -> dict:
    """{date: (rv20, rv30)} for every date with enough history."""
    vals = [c for _, c in closes_by_day]
    out = {}
    for i, (d, _) in enumerate(closes_by_day):
        upto = vals[: i + 1]
        out[d] = (realised_vol(upto, 20), realised_vol(upto, 30))
    return out


# ---------------------------------------------------------------- history file
def load_history() -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("index_price", "atm_iv_30d", "rv20", "rv30"):
            r[k] = float(r[k]) if r.get(k) not in (None, "") else None
    return rows


def save_history(rows: list[dict]) -> None:
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["date"])
    with HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else (round(r[k], 6) if isinstance(r[k], float) else r[k]))
                        for k in HISTORY_FIELDS})


def merge_history(rows: list[dict], closes_by_day, today: str | None = None, today_iv=None,
                  today_price=None, iv_overrides: dict | None = None) -> list[dict]:
    """Fill realised vol for every candle day; set today's IV (live snapshot beats backfill)."""
    by = {r["date"]: dict(r) for r in rows}
    rv = rolling_rv(closes_by_day)
    for d, close in closes_by_day:
        r = by.setdefault(d, {"date": d, "atm_iv_30d": None, "iv_source": "price_only"})
        r["index_price"] = r.get("index_price") or close
        r["rv20"], r["rv30"] = rv[d]
    for d, iv in (iv_overrides or {}).items():
        r = by.setdefault(d, {"date": d, "iv_source": "price_only"})
        if r.get("iv_source") not in ("snapshot", "imported"):
            r["atm_iv_30d"], r["iv_source"] = iv, "trades_backfill"
    if today:
        r = by.setdefault(today, {"date": today})
        if today_iv is not None:
            r["atm_iv_30d"], r["iv_source"] = today_iv, "snapshot"
        if today_price is not None:
            r["index_price"] = today_price
        if today in rv:
            r["rv20"], r["rv30"] = rv[today]
    return sorted(by.values(), key=lambda r: r["date"])


# ---------------------------------------------------------------- verdict
def percentile_rank(value: float, history: list[float]) -> float:
    """% of history values at or below `value` (0-100)."""
    if not history:
        return float("nan")
    return 100.0 * sum(1 for h in history if h <= value) / len(history)


def verdict(cfg: dict, iv, rv30, prior_ivs: list[float]) -> dict:
    v = cfg["vol"]
    out = {"atm_iv": iv, "rv30": rv30, "history_days": len(prior_ivs)}
    if iv is None:
        out.update(label="UNKNOWN", mode="none", reason="No ATM IV available")
        return out
    ratio = iv / rv30 if rv30 else None
    out["iv_rv_ratio"] = ratio
    if len(prior_ivs) >= int(v["min_history_days"]):
        pct = percentile_rank(iv, prior_ivs[-365:] + [iv])
        out.update(mode="percentile", percentile=pct)
        if pct >= v["iv_percentile_min"]:
            out.update(label="RICH", reason=f"IV is at the {pct:.0f}th percentile of the last {min(len(prior_ivs), 365)} days (threshold {v['iv_percentile_min']})")
        elif pct <= v["iv_percentile_cheap"]:
            out.update(label="CHEAP", reason=f"IV is at the {pct:.0f}th percentile, low in its range")
        else:
            out.update(label="FAIR", reason=f"IV is at the {pct:.0f}th percentile, below the {v['iv_percentile_min']} threshold")
        return out
    out.update(mode="ratio", percentile=None)
    if ratio is None:
        out.update(label="UNKNOWN", reason="Not enough price history for realised vol")
    elif ratio >= v["iv_rv_ratio_min"]:
        out.update(label="RICH", reason=f"IV is {ratio:.2f}x realised vol (threshold {v['iv_rv_ratio_min']}); percentile not yet available ({len(prior_ivs)} of {v['min_history_days']} days of IV history)")
    elif ratio <= v["iv_rv_ratio_cheap"]:
        out.update(label="CHEAP", reason=f"IV is only {ratio:.2f}x realised vol")
    else:
        out.update(label="FAIR", reason=f"IV is {ratio:.2f}x realised vol, below the {v['iv_rv_ratio_min']} threshold")
    return out


def annotate_history(cfg: dict, rows: list[dict]) -> list[dict]:
    """Add a point-in-time verdict to each row with IV (using only data before that day) for chart shading."""
    out, prior = [], []
    for r in rows:
        r = dict(r)
        if r.get("atm_iv_30d") is not None:
            vd = verdict(cfg, r["atm_iv_30d"], r.get("rv30"), prior)
            r["verdict"] = vd["label"]
            prior.append(r["atm_iv_30d"])
        else:
            r["verdict"] = None
        out.append(r)
    return out


# ---------------------------------------------------------------- backfill from trades
def iv_from_trades(trades: list[dict], min_dte=7, max_dte=60, moneyness=0.10, min_trades=2):
    """Approximate 30-day ATM IV from one day's SOL option trades.

    Uses trades near the money (strike within +/-moneyness of the index) with min_dte..max_dte left,
    weighting each trade by closeness to 30 days and to the money. Returns (iv or None, trades used).
    """
    pts = []
    for t in trades:
        p = parse_name(t.get("instrument_name", ""))
        iv = normalize_iv(t.get("iv"))
        idx = t.get("index_price")
        if not p or iv is None or not idx or not t.get("timestamp"):
            continue
        dte = (p["expiration_timestamp"] - t["timestamp"]) / DAY_MS
        m = abs(math.log(p["strike"] / idx))
        if not (min_dte <= dte <= max_dte) or m > moneyness:
            continue
        w = 1.0 / (1.0 + abs(dte - 30) / 15.0) / (1.0 + m / 0.03)
        pts.append((iv, w))
    if len(pts) < min_trades:
        return None, len(pts)
    pts.sort()
    half, acc = sum(w for _, w in pts) / 2, 0.0
    for iv, w in pts:             # weighted median: robust to the odd mispriced trade
        acc += w
        if acc >= half:
            return iv, len(pts)
    return pts[-1][0], len(pts)
