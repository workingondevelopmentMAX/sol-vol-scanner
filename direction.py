"""Direction signal (step 5.1): a bullish / neutral / bearish reading for SOL. DISPLAY ONLY for now.

Rules live in strategies/direction.toml (fixed before any result was run). This module:
  - works out each factor's state for a day from that day and earlier only (no look-ahead),
  - tests each factor against SOL's next-30-day move with the pass rule from the file,
  - combines the PASSING factors with the owner's view (view.toml) into today's reading,
  - records the view and the reading once per UTC day (data/view_history.csv, data/direction_history.csv).
No trade decision reads any of this yet.

    python direction.py        run the test and write data/direction_test.json (byte-identical on the same files)
"""
from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES = ROOT / "strategies" / "direction.toml"
TEST_OUT = ROOT / "data" / "direction_test.json"
VIEW_HISTORY = ROOT / "data" / "view_history.csv"
DIRECTION_HISTORY = ROOT / "data" / "direction_history.csv"
TESTED = ("trend", "momentum", "drawdown", "crash_fear")
STATES = ("bullish", "neutral", "bearish")
SCORE = {"bullish": 1, "neutral": 0, "bearish": -1}
VIEW_FIELDS = ["date", "direction", "strength", "expires", "in_force", "note"]
DIRECTION_FIELDS = ["date", "rules_version", "rules_fingerprint", "trend", "momentum", "drawdown", "crash_fear",
                    "skew", "skew_points", "term_structure", "term_points", "factors_passed", "data_score",
                    "view_direction", "view_strength", "view_in_force", "final_score", "final_reading"]


# ------------------------------------------------------------------ rules
def load_rules(path: Path | str = RULES) -> dict:
    raw = Path(path).read_bytes()
    r = tomllib.loads(raw.decode("utf-8"))
    r["_stamp"] = {"name": r["direction"]["name"], "title": r["direction"]["title"],
                   "version": r["direction"]["version"], "file": Path(path).name,
                   "fingerprint": hashlib.sha256(raw).hexdigest()[:12]}
    return r


def days_from_history(history: list[dict]) -> list[dict]:
    return [{"date": r["date"], "S": r["index_price"], "iv": r.get("atm_iv_30d")}
            for r in history if r.get("index_price")]


# ------------------------------------------------------------------ factor states (no look-ahead)
class Past:
    """Look-ups into the history up to and including one day. Nothing after `today` is reachable."""

    def __init__(self, days: list[dict], i: int):
        self.today = days[i]
        self.by = {d["date"]: d for d in days[:i + 1]}
        self.d0 = date.fromisoformat(self.today["date"])

    def at(self, back: int) -> dict | None:
        return self.by.get((self.d0 - timedelta(days=back)).isoformat())

    def window(self, n: int) -> list[float] | None:
        rows = [self.at(k) for k in range(n)]
        return None if any(r is None or not r["S"] for r in rows) else [r["S"] for r in rows]


def factor_state(rules: dict, name: str, days: list[dict], i: int) -> dict:
    """{"state": bullish/neutral/bearish/unknown, "value": number shown, "text": one line}."""
    f = rules["factors"][name]
    p = Past(days, i)
    s = p.today["S"]
    if name == "trend":
        a, b = p.window(int(f["ma_short_days"])), p.window(int(f["ma_long_days"]))
        if not a or not b:
            return {"state": "unknown", "value": None, "text": "Not enough history for the 200-day average."}
        m1, m2 = statistics.fmean(a), statistics.fmean(b)
        st = "bullish" if s > m1 and s > m2 else "bearish" if s < m1 and s < m2 else "neutral"
        return {"state": st, "value": round((s / m2 - 1) * 100, 2),
                "text": f"SOL ${s:,.2f} vs 50-day average ${m1:,.2f} and 200-day average ${m2:,.2f}."}
    if name == "momentum":
        back = p.at(int(f["lookback_days"]))
        if not back or not back["S"]:
            return {"state": "unknown", "value": None, "text": "Not enough history."}
        mv = (s / back["S"] - 1) * 100
        st = "bullish" if mv > f["bullish_above_pct"] else "bearish" if mv < f["bearish_below_pct"] else "neutral"
        return {"state": st, "value": round(mv, 2), "text": f"SOL moved {mv:+.1f}% over 30 days "
                f"(bullish above {f['bullish_above_pct']:+g}%, bearish below {f['bearish_below_pct']:+g}%)."}
    if name == "drawdown":
        w = p.window(int(f["high_lookback_days"]))
        if not w:
            return {"state": "unknown", "value": None, "text": "Not enough history."}
        below = (1 - s / max(w)) * 100
        st = ("bullish" if below > f["bullish_below_high_pct"] else
              "bearish" if below <= f["bearish_within_high_pct"] else "neutral")
        return {"state": st, "value": round(below, 2), "text": f"SOL is {below:.1f}% below its 90-day high "
                f"(bullish more than {f['bullish_below_high_pct']}% below, bearish within {f['bearish_within_high_pct']}%)."}
    if name == "crash_fear":
        back = p.at(int(f["drop_days"]))
        if not back or not back["S"] or p.today["iv"] is None or back["iv"] is None:
            return {"state": "unknown", "value": None, "text": "Not enough price or IV history."}
        drop = (s / back["S"] - 1) * 100
        rise = (p.today["iv"] - back["iv"]) * 100
        fear = drop < -f["drop_pct"] and rise >= f["iv_rise_points"]
        return {"state": f["fear_implies"] if fear else "neutral", "value": round(drop, 2),
                "text": (f"Fear day: SOL {drop:+.1f}% and IV {rise:+.1f} points in 5 days." if fear else
                         f"No fear day: SOL {drop:+.1f}% and IV {rise:+.1f} points in 5 days "
                         f"(needs below -{f['drop_pct']}% and +{f['iv_rise_points']} points).")}
    raise ValueError(name)


def options_factors(rules: dict, chain: list[dict] | None, ts: list[dict] | None) -> dict:
    """Skew and term structure from today's option chain (recording only: zero weight, not testable yet)."""
    import suggest
    import vol_gauge
    out = {}
    fs, ft = rules["factors"]["skew"], rules["factors"]["term_structure"]
    skew = None
    if chain:
        exps = sorted({o["expiry_ms"]: o["dte"] for o in chain}.items(), key=lambda x: abs(x[1] - fs["target_dte"]))
        if exps:
            legs = [o for o in chain if o["expiry_ms"] == exps[0][0] and o.get("iv")]
            ivs = {}
            for kind, target in (("put", -fs["delta"]), ("call", fs["delta"])):
                best = None
                for o in legs:
                    if o["type"] != kind:
                        continue
                    g = suggest.leg_greeks(o)
                    if g and (best is None or abs(g["delta"] - target) < abs(best[0] - target)):
                        best = (g["delta"], o["iv"])
                if best:
                    ivs[kind] = best[1]
            if len(ivs) == 2:
                skew = round((ivs["put"] - ivs["call"]) * 100, 2)
    if skew is None:
        out["skew"] = {"state": "unknown", "value": None, "text": "No option chain today."}
    else:
        st = ("bearish" if skew > fs["bearish_above_points"] else "bullish" if skew < fs["bullish_below_points"]
              else "neutral")
        out["skew"] = {"state": st, "value": skew, "text": f"30-day puts are {skew:+.1f} vol points vs calls."}
    term = None
    if ts:
        far = vol_gauge.constant_maturity_iv(ts, float(ft["long_target_dte"]))
        if far is not None and ts[0].get("atm_iv") is not None:
            term = round((ts[0]["atm_iv"] - far) * 100, 2)
    if term is None:
        out["term_structure"] = {"state": "unknown", "value": None, "text": "No term structure today."}
    else:
        st = ("bearish" if term > ft["bearish_inverted_points"] else
              "bullish" if term < -ft["bullish_contango_points"] else "neutral")
        out["term_structure"] = {"state": st, "value": term,
                                 "text": f"Nearest-expiry IV is {term:+.1f} points vs 60-day IV."}
    return out


# ------------------------------------------------------------------ the test
def windows(rules: dict, days: list[dict]) -> dict:
    last = days[-1]["date"]
    ev = rules["evaluation"]
    return {k: [ev[k][0], ev[k][1] or last] for k in ("in_sample", "held_back")}


def non_overlapping(dates: list[str], horizon: int) -> int:
    """Count periods: take the first day, skip the next `horizon` days, take the next day, and so on."""
    n, next_free = 0, None
    for d in sorted(dates):
        if next_free is None or d >= next_free:
            n += 1
            next_free = (date.fromisoformat(d) + timedelta(days=horizon)).isoformat()
    return n


def outcome(days: list[dict], i: int, horizon: int) -> tuple[str, float] | None:
    by = {d["date"]: d for d in days}
    end = (date.fromisoformat(days[i]["date"]) + timedelta(days=horizon)).isoformat()
    later = by.get(end)
    return None if not later or not later["S"] else (end, (later["S"] / days[i]["S"] - 1) * 100)


def group_stats(rows: list[tuple[str, float]], horizon: int) -> dict:
    if not rows:
        return {"days": 0, "avg_move_pct": None, "pct_up": None, "periods": 0}
    moves = [m for _, m in rows]
    return {"days": len(rows), "avg_move_pct": round(statistics.fmean(moves), 2),
            "pct_up": round(100 * sum(m > 0 for m in moves) / len(moves), 1),
            "periods": non_overlapping([d for d, _ in rows], horizon)}


def judge(rules: dict, one_sided: bool, by_window: dict) -> dict:
    """The pass rule from [evaluation], applied exactly at its thresholds."""
    ev = rules["evaluation"]
    spread_min, periods_min = float(ev["min_spread_points"]), int(ev["min_periods"])
    detail, ok_all = {}, True
    for k, g in by_window.items():
        if one_sided:
            a, b, names = g["fear"], g["other"], ("fear", "other")
            enough = a["periods"] >= periods_min
        else:
            a, b, names = g["bullish"], g["bearish"], ("bullish", "bearish")
            enough = a["periods"] >= periods_min and b["periods"] >= periods_min
        spread = None if a["avg_move_pct"] is None or b["avg_move_pct"] is None else \
            round(a["avg_move_pct"] - b["avg_move_pct"], 2)
        ok = spread is not None and spread >= spread_min and enough
        detail[k] = {"spread_points": spread, "enough_periods": enough, "pass": ok, "compared": list(names)}
        ok_all = ok_all and ok
    return {"pass": ok_all, "windows": detail}


def run_test(rules: dict, days: list[dict]) -> dict:
    ev = rules["evaluation"]
    h = int(ev["horizon_days"])
    win = windows(rules, days)
    outs = {i: outcome(days, i, h) for i in range(len(days))}

    def in_window(i, k):
        lo, hi = win[k]
        o = outs[i]
        return o is not None and lo <= days[i]["date"] <= hi and (not ev["outcome_inside_window"] or o[0] <= hi)

    baseline = {k: group_stats([(days[i]["date"], outs[i][1]) for i in outs if in_window(i, k)], h) for k in win}
    factors = []
    for name, f in rules["factors"].items():
        if f.get("recording_only"):
            factors.append({"name": name, "title": f["title"], "result": "RECORDING ONLY",
                            "note": "Recorded every day from the stored option chains; not enough history to test."})
            continue
        states = {i: factor_state(rules, name, days, i)["state"] for i in outs if outs[i] is not None}
        one_sided = bool(f.get("one_sided"))
        by_window = {}
        for k in win:
            rows = {s: [] for s in STATES}
            for i, st in states.items():
                if st != "unknown" and in_window(i, k):
                    rows[st].append((days[i]["date"], outs[i][1]))
            g = {s: group_stats(rows[s], h) for s in STATES}
            if one_sided:
                fear = f["fear_implies"]
                other = [r for s in STATES if s != fear for r in rows[s]]
                g = {"fear": group_stats(rows[fear], h), "other": group_stats(other, h)}
            g["unknown_days"] = sum(1 for i, st in states.items() if st == "unknown" and in_window(i, k))
            by_window[k] = g
        verdict = judge(rules, one_sided, by_window)
        factors.append({"name": name, "title": f["title"], "one_sided": one_sided,
                        "result": "PASS" if verdict["pass"] else "FAIL", "judge": verdict, "windows": by_window})
    return {"rules": rules["_stamp"], "windows": win, "horizon_days": h,
            "pass_rule": {"min_spread_points": ev["min_spread_points"], "min_periods": ev["min_periods"]},
            "baseline": baseline, "factors": factors,
            "passed": [f["name"] for f in factors if f["result"] == "PASS"]}


def dumps(x: dict) -> str:
    return json.dumps(x, indent=1, sort_keys=False, allow_nan=False) + "\n"


def load_test(path: Path = TEST_OUT) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ today's reading
def label(score: float, c: dict) -> str:
    return "bullish" if score >= c["bullish_at"] else "bearish" if score <= c["bearish_at"] else "neutral"


def combine(rules: dict, states: dict, passed: list[str], view: dict) -> dict:
    c = rules["combine"]
    used = [n for n in passed if states.get(n, {}).get("state") in SCORE]
    data_score = round(statistics.fmean(SCORE[states[n]["state"]] for n in used), 4) if used else 0.0
    vdir = view.get("in_force") or "neutral"
    tilt = SCORE.get(vdir, 0) * int(view.get("strength") or 0) * float(c["view_tilt_per_strength"]) \
        if vdir != "neutral" else 0.0
    final = round(max(-1.0, min(1.0, data_score + tilt)), 4)
    return {"data_score": data_score, "data_reading": label(data_score, c), "factors_used": used,
            "view_tilt": round(tilt, 4), "final_score": final, "final_reading": label(final, c)}


def today(rules: dict, history: list[dict], chain: list[dict] | None, ts: list[dict] | None, view: dict,
          test: dict | None) -> dict:
    days = days_from_history(history)
    i = len(days) - 1
    states = {n: factor_state(rules, n, days, i) for n in TESTED} if days else {}
    states.update(options_factors(rules, chain, ts))
    tested = [f["name"] for f in (test or {}).get("factors", []) if f["result"] in ("PASS", "FAIL")]
    passed = (test or {}).get("passed", [])
    result_of = {f["name"]: f["result"] for f in (test or {}).get("factors", [])}
    factors = []
    for n, f in rules["factors"].items():
        s = states.get(n, {"state": "unknown", "value": None, "text": ""})
        res = "RECORDING ONLY" if f.get("recording_only") else result_of.get(n, "NOT TESTED")
        factors.append({"name": n, "title": f["title"], "state": s["state"], "value": s["value"], "text": s["text"],
                        "tested": res, "counts": n in passed})
    comb = combine(rules, states, passed, view)
    m = len(tested) or len(TESTED)
    data_line = (f"Data reading {comb['data_reading']}: {len(passed)} of {m} factors passed testing"
                 + (f" ({', '.join(passed)})." if passed else ", so the data signal stays neutral."))
    view_line = ("Your view: " + view.get("text", "none") + "." if view.get("available")
                 else "Your view: none set (counts as neutral).")
    final_line = (f"Combined reading {comb['final_reading']} (score {comb['final_score']:+.2f}; bullish at "
                  f"{rules['combine']['bullish_at']:+.2f} or more, bearish at {rules['combine']['bearish_at']:+.2f} "
                  "or less).")
    return {"rules": rules["_stamp"], "date": days[-1]["date"] if days else None, "factors": factors,
            "passed": passed, "tested": m, **comb, "view": view,
            "lines": {"data": data_line, "view": view_line, "final": final_line},
            "note": "Direction is not used by any live trade yet."}


# ------------------------------------------------------------------ daily recording (first run of the day wins)
def append_once(path: Path, fields: list[str], row: dict) -> bool:
    """Append `row` unless the file already has a row for that date. Existing rows are never rewritten."""
    path = Path(path)
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            if any(r.get("date") == row["date"] for r in csv.DictReader(f)):
                return False
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fields})
    return True


def view_row(day: str, view: dict) -> dict:
    return {"date": day, "direction": view.get("direction", "neutral"), "strength": view.get("strength", ""),
            "expires": view.get("expires") or "", "in_force": "true" if view.get("available") and not view.get("expired")
            else "false", "note": view.get("note", "")}


def direction_row(day: str, block: dict) -> dict:
    st = {f["name"]: f for f in block["factors"]}
    v = block["view"]
    return {"date": day, "rules_version": block["rules"]["version"], "rules_fingerprint": block["rules"]["fingerprint"],
            **{n: st[n]["state"] for n in TESTED if n in st},
            "skew": st.get("skew", {}).get("state"), "skew_points": st.get("skew", {}).get("value"),
            "term_structure": st.get("term_structure", {}).get("state"),
            "term_points": st.get("term_structure", {}).get("value"),
            "factors_passed": " ".join(block["passed"]), "data_score": block["data_score"],
            "view_direction": v.get("direction"), "view_strength": v.get("strength"),
            "view_in_force": "true" if v.get("available") and not v.get("expired") else "false",
            "final_score": block["final_score"], "final_reading": block["final_reading"]}


def record(day: str, block: dict, view_path: Path = VIEW_HISTORY, dir_path: Path = DIRECTION_HISTORY) -> None:
    append_once(view_path, VIEW_FIELDS, view_row(day, block["view"]))
    append_once(dir_path, DIRECTION_FIELDS, direction_row(day, block))


if __name__ == "__main__":
    import vol_gauge
    res = run_test(load_rules(), days_from_history(vol_gauge.load_history()))
    TEST_OUT.write_bytes(dumps(res).encode("utf-8"))
    for f in res["factors"]:
        print(f"{f['name']:15s} {f['result']}")
    print("passed:", ", ".join(res["passed"]) or "none")


def refresh_block(rules: dict, block: dict, test: dict | None) -> dict:
    """Re-apply a newer test result (which factors passed) to an already-computed day, keeping its factor states."""
    states = {f["name"]: {"state": f["state"], "value": f["value"], "text": f["text"]} for f in block["factors"]}
    passed = (test or {}).get("passed", [])
    result_of = {f["name"]: f["result"] for f in (test or {}).get("factors", [])}
    for f in block["factors"]:
        f["tested"] = "RECORDING ONLY" if rules["factors"][f["name"]].get("recording_only") else \
            result_of.get(f["name"], "NOT TESTED")
        f["counts"] = f["name"] in passed
    comb = combine(rules, states, passed, block["view"])
    m = len([1 for r in result_of.values() if r in ("PASS", "FAIL")]) or len(TESTED)
    block.update(comb, passed=passed, tested=m)
    block["lines"]["data"] = (f"Data reading {comb['data_reading']}: {len(passed)} of {m} factors passed testing"
                              + (f" ({', '.join(passed)})." if passed else ", so the data signal stays neutral."))
    block["lines"]["final"] = (f"Combined reading {comb['final_reading']} (score {comb['final_score']:+.2f}; bullish "
                               f"at {rules['combine']['bullish_at']:+.2f} or more, bearish at "
                               f"{rules['combine']['bearish_at']:+.2f} or less).")
    return block
