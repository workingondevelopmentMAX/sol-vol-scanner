"""The daily page's explanations: at a glance, evidence, for and against, past days like today. DISPLAY ONLY.

Everything here sits beside the engine's decision and never changes it: no suggestion, size, exit level or
conviction score is touched. All numbers are worked out here (so they can be tested) and every sentence is a
fixed template filled with numbers. Thresholds come from config.toml [report].

    python report.py      refresh the report in site/data/latest.json after the daily backtest has run
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VIEW = ROOT / "view.toml"
BACKTEST = ROOT / "data" / "backtest.json"
LIVE_VARIANT = "Strategy file as written"
WINDOWS = (("in_sample", "Practice period (in-sample)"), ("held_back", "Unseen period (held back)"))
CLOSING = ("Cautions don't cancel the trade. They're things to weigh. Taking it, taking half size, "
           "or skipping are all within the plan.")
DURING_TRADE_LABEL = "What happened when SOL fell during the trade"


# ------------------------------------------------------------------ small helpers
def ordinal(n: float) -> str:
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def money(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{'+' if x > 0 else '-' if x < 0 else ''}${abs(x):,.0f}"


def pct(x: float | None, sign: bool = True) -> str:
    return "-" if x is None else (f"{x:+.1f}%" if sign else f"{x:.0f}%")


# ------------------------------------------------------------------ inputs
def load_view(path: Path = VIEW, today: str | None = None) -> dict:
    """The owner's market view (view.toml), with its expiry applied. Display only on this page."""
    if not Path(path).exists():
        return {"available": False, "text": "No view set (view.toml missing): counts as neutral."}
    v = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    exp = v.get("expires")
    exp_s = exp.isoformat() if hasattr(exp, "isoformat") else (str(exp) if exp else None)
    expired = bool(exp_s and today and exp_s < today)
    direction, strength = v.get("direction", "neutral"), v.get("strength", 1)
    if expired:
        text = f"expired on {exp_s}: counts as neutral"
    else:
        text = f"{direction}, strength {strength} of 3" + (f", until {exp_s}" if exp_s else "")
    if v.get("note") and not expired:
        text += f' ("{v["note"]}")'
    return {"available": True, "direction": direction, "strength": strength, "expires": exp_s,
            "expired": expired, "in_force": "neutral" if expired else direction, "text": text,
            "note": v.get("note", "")}


def todays_conditions(annotated_history: list[dict], verdict_label: str | None) -> dict:
    """Today's market measured exactly like a backtest entry day (backtest.entry_conditions), so 'past days like
    today' compares like with like."""
    import backtest                     # lazy: backtest imports main, which imports this module
    days = [{"date": r["date"], "S": r["index_price"], "iv_percentile": r.get("iv_percentile"),
             "verdict": {"label": r.get("verdict")}} for r in annotated_history if r.get("index_price")]
    if not days:
        return {"sol_30d_change_pct": None, "below_30d_high_pct": None, "iv_percentile": None,
                "verdict": verdict_label}
    c = backtest.entry_conditions(days, len(days) - 1)
    c["verdict"] = verdict_label                    # today's live verdict (the one the engine used)
    return c


# ------------------------------------------------------------------ 1. banners
def banners(result: dict, bt: dict | None) -> list[dict]:
    out = []
    if not bt:
        out.append({"kind": "missing", "text": "Evidence is missing: the backtest results could not be loaded, "
                                               "so each rule's track record can't be shown today."})
        return out
    today_fp = (result.get("strategy") or {}).get("fingerprint")
    bt_fp = (bt.get("strategy") or {}).get("fingerprint")
    if today_fp and bt_fp and today_fp != bt_fp:
        out.append({"kind": "older_rules",
                    "text": f"Evidence is for an older version of the rules: the backtest used rules {bt_fp}, today's "
                            f"rules are {today_fp}. Treat the track record with care until it is re-run."})
    return out


def rules_match(result: dict, bt: dict | None) -> bool | None:
    if not bt:
        return None
    a, b = (result.get("strategy") or {}).get("fingerprint"), (bt.get("strategy") or {}).get("fingerprint")
    return None if not (a and b) else a == b


# ------------------------------------------------------------------ 2. at a glance
MARKET_TEXT = {"rich": "RICH IV days", "cheap": "CHEAP IV days", "fair": "FAIR IV days", "any": "any market"}


def glance(result: dict, today: dict, view: dict) -> dict:
    vd = result.get("verdict") or {}
    label = vd.get("label") or "UNKNOWN"
    if vd.get("mode") == "percentile" and vd.get("percentile") is not None:
        iv = f"IV is {label} ({ordinal(vd['percentile'])} percentile of the last 12 months)"
    elif vd.get("iv_rv_ratio") is not None:
        iv = f"IV is {label} ({vd['iv_rv_ratio']:.2f}x realised vol)"
    else:
        iv = f"IV is {label}"
    move = today.get("sol_30d_change_pct")
    line = f"{iv} · SOL {pct(move)} over 30 days · Your view: {view['text']}"
    rows = []
    for s in result.get("strategies") or []:
        st, reason = s["status"], s["reason"]
        if st == "suggested":
            text = reason
        elif st == "paper_idea":
            text = reason
        elif st == "no_trade":
            text = f"Right conditions, but nothing passed the rules today: {reason}"
        elif st == "not_eligible":
            text = (f"Wrong conditions today: for {MARKET_TEXT[s['market']]}; IV is {label}" if label != "UNKNOWN"
                    else "Wrong conditions today: no IV verdict today")
        elif s.get("menu_status") == "in_testing":
            text = f"In testing (roadmap step {s.get('roadmap_step')})"
        else:
            text = f"Not built yet (roadmap step {s.get('roadmap_step')})"
        rows.append({"id": s["id"], "name": s["name"], "market": s["market"],
                     "market_text": MARKET_TEXT[s["market"]], "status": st, "text": text,
                     "card": f"idea-{s['id']}" if st == "suggested" and s.get("suggestion") else None,
                     "lab": (f"lab.html#lab-{Path(s['file']).stem}" if s.get("file") and
                             st in ("paper_idea", "suggested", "no_trade") else None)})
    any_trade = any(r["card"] for r in rows)
    d = result.get("direction")
    dline = None
    if d:
        v = d["view"]
        vt = v["text"] if v.get("available") else "none set"
        dline = (f"Direction: data {d['data_reading']} ({len(d['passed'])} of {d['tested']} factors passed testing) · "
                 f"your view {vt} · combined {d['final_reading']}")
    return {"line": line, "direction_line": dline, "direction_note": d["note"] if d else None, "rows": rows,
            "footer": None if any_trade else "No trade today. Sitting out is the plan working."}


# ------------------------------------------------------------------ 4. evidence box
def live_variant(bt: dict | None, strategy_file: str | None) -> dict | None:
    """The version in use: "Strategy file as written", or the version chosen by the file's [selection] rule."""
    if not bt or (strategy_file and (bt.get("strategy") or {}).get("file") != strategy_file):
        return None
    name = (bt.get("selection") or {}).get("chosen") or LIVE_VARIANT
    return next((v for v in bt.get("variants", []) if v["name"] == name), None)


def window_rows(sc: dict) -> list[dict]:
    n = sc.get("trades", 0)
    has = n > 0
    hold, dca = (sc.get("hold_sol") or {}).get("per_1000"), (sc.get("dca_sol") or {}).get("per_1000")
    rows = [("trades", "Trades", n, str(n)),
            ("win_rate", "Win rate", sc.get("win_rate") if has else None,
             f"{sc['win_rate'] * 100:.0f}%" if has else "-"),
            ("total_per_1000", "Total per $1,000 risked", sc.get("total_per_1000") if has else None,
             money(sc.get("total_per_1000")) if has else "-"),
            ("avg_per_1000", "Average per trade", sc.get("avg_per_1000") if has else None,
             money(sc.get("avg_per_1000")) if has else "-"),
            ("worst_per_1000", "Worst trade", sc.get("worst_per_1000") if has else None,
             money(sc.get("worst_per_1000")) if has else "-"),
            ("max_losing_streak", "Longest losing streak", sc.get("max_losing_streak") if has else None,
             f"{sc['max_losing_streak']} in a row" if has else "-"),
            ("max_drawdown_per_1000", "Max drawdown", sc.get("max_drawdown_per_1000") if has else None,
             money(sc.get("max_drawdown_per_1000")) if has else "-"),
            ("hold_sol", "vs holding SOL ($1,000 bought at the start)", hold, money(hold)),
            ("dca_sol", "vs buying SOL weekly ($1,000 spread out)", dca, money(dca))]
    return [{"key": k, "name": name, "value": v, "text": t} for k, name, v, t in rows]


def evidence_box(bt: dict, v: dict, strategy_id: str | None = None) -> dict:
    e = v["evidence"]
    title = ("Thin evidence, " if e["thin"] else "Enough trades to judge, ") + {
        "consistent": "consistent results", "mixed": "mixed results", "losing": "losing results"}[e["label"]]
    labels = bt.get("window_labels") or {}
    return {"variant": v["name"], "label": e["label"], "thin": e["thin"], "title": title, "meaning": e["text"],
            "windows": [{"key": k, "title": t, "label": labels.get(k, ""), "rows": window_rows(v["scorecard"][k])}
                        for k, t in WINDOWS],
            "unseen_note": "The unseen period counts for more: it was never used to choose the rules.",
            "per_1000_note": "If you risked $1,000 on every trade, this is what you'd have made or lost.",
            "versions_note": f"{len(bt.get('variants', []))} versions of these rules were tested.",
            "lab_link": f"lab.html#lab-{strategy_id}" if strategy_id else "lab.html",
            "lab_link_text": "See the full comparison on the Strategy Lab page"}


# ------------------------------------------------------------------ 6. past days like today
def similar_trades(trades: list[dict], today: dict, band: float) -> list[dict]:
    """Past trades whose ENTRY-day conditions match today: same IV verdict and a prior 30-day SOL move within
    +/- band points of today's. Uses only each trade's entry_conditions, never anything after entry."""
    m0 = today.get("sol_30d_change_pct")
    if m0 is None or not today.get("verdict"):
        return []
    out = []
    for t in trades:
        c = t.get("entry_conditions") or {}
        m = c.get("sol_30d_change_pct")
        if c.get("verdict") == today["verdict"] and m is not None and round(abs(m - m0), 6) <= band:
            out.append(t)
    return out


def similar_days(bt: dict, v: dict, today: dict, rep: dict) -> dict:
    band, need = float(rep["similar_move_band_pts"]), int(rep["similar_min_trades"])
    m0 = today.get("sol_30d_change_pct")
    picked = similar_trades(v["trades"], today, band)
    n = len(picked)
    wins = sum(t["pnl_per_1000"] > 0 for t in picked)
    total = round(sum(t["pnl_per_1000"] for t in picked), 1)
    out = {"band_pts": band, "min_trades": need, "today_move_pct": m0, "today_verdict": today.get("verdict"),
           "range_pct": None if m0 is None else [round(m0 - band, 1), round(m0 + band, 1)],
           "count": n, "wins": wins, "total_per_1000": total if n else None,
           "avg_per_1000": round(total / n, 1) if n else None, "enough": n >= need}
    if m0 is None:
        out["rule"] = "Today's 30-day SOL move is not available, so similar past days can't be picked."
    else:
        out["rule"] = (f"Past entry days with the same IV verdict ({today.get('verdict')}) and a prior 30-day SOL "
                       f"move between {out['range_pct'][0]:+.0f}% and {out['range_pct'][1]:+.0f}% (today "
                       f"{m0:+.1f}%, ±{band:.0f} points). Chosen only from conditions on the entry day.")
    if not out["enough"]:
        out["text"] = f"Too few similar past days to tell ({n} found)."
    else:
        out["text"] = (f"{n} similar past days: {wins} won, {n - wins} lost, {money(total)} in total "
                       f"({money(out['avg_per_1000'])} per trade, per $1,000 risked).")
    bounds = bt.get("windows") or {}
    groups = []
    for k, title in WINDOWS:
        lo, hi = (bounds.get(k) or [None, None])
        rows = [{"date": t["date"], "sol": t["S"], "move_30d_pct": t["entry_conditions"]["sol_30d_change_pct"],
                 "pnl_per_1000": t["pnl_per_1000"], "result": money(t["pnl_per_1000"]), "exit": t["exit"]}
                for t in picked if lo and lo <= t["date"] <= hi]
        groups.append({"key": k, "title": title, "rows": rows})
    out["windows"] = groups
    return out


def during_trade(v: dict) -> dict:
    """The hindsight split (what SOL did AFTER entry), kept apart under its own label."""
    rows = []
    for k, title in WINDOWS:
        for key, c in (v["scorecard"][k].get("by_condition") or {}).items():
            rows.append({"window": title, "group": key, "trades": c["trades"], "wins": c["wins"],
                         "total_per_1000": c["total_per_1000"], "text": money(c["total_per_1000"])})
    return {"label": DURING_TRADE_LABEL,
            "note": "Known only after each trade ended, so it never picks 'days like today'.", "rows": rows}


# ------------------------------------------------------------------ 5. for and against
def for_list(cfg: dict, result: dict, s: dict) -> list[str]:
    sp, risk = cfg["spread"], cfg["risk"]
    vd = result["verdict"]
    out = []
    if vd.get("mode") == "percentile" and vd.get("percentile") is not None:
        out.append(f"IV is {vd['label']} at the {ordinal(vd['percentile'])} percentile "
                   f"(rule: this strategy trades only {vd['label']} days).")
    else:
        out.append(f"IV is {vd['label']} ({vd.get('iv_rv_ratio') or 0:.2f}x realised vol).")
    out.append(f"Credit is {s['credit_pct_of_width']:.0f}% of the strike width "
               f"(rule: at least {sp['min_credit_pct_of_width']}%).")
    out.append(f"Short put delta {s['short_delta']:.2f} (rule: between {sp['short_delta_min']} and "
               f"{sp['short_delta_max']}).")
    out.append(f"{s['dte']:.0f} days to expiry (rule: {sp['dte_min']} to {sp['dte_max']}).")
    liq = s["liquidity"]
    out.append(f"Liquidity OK: worst leg bid-ask {liq['worst_spread_pct']:.0f}% of mid (rule: at most "
               f"{sp['max_leg_spread_pct_of_mid']}%, or ${sp['max_leg_spread_abs_usd']:.2f} per SOL), open interest "
               f"at least {liq['min_oi']:,.0f} SOL.")
    sz = s.get("sizing") or {}
    if sz.get("contracts"):
        out.append(f"Size within limits: {sz['contracts']} contracts risk ${sz['usd_at_risk']:,.0f} "
                   f"(limit ${risk['max_loss_per_trade_usd']:,.0f} per trade, ${sz['remaining_total_cap_usd']:,.0f} "
                   f"left under the total cap).")
    return out


def cushion_ratio(s: dict) -> float | None:
    part = next((p for p in (s.get("conviction") or {}).get("parts", []) if p["name"] == "Cushion before a loss"), None)
    return None if part is None else part.get("ratio")


def cautions(rep: dict, s: dict, spot: float, today: dict, v: dict | None, match: bool | None,
             similar: dict | None) -> list[dict]:
    out = []
    move = today.get("sol_30d_change_pct")
    lim = float(rep["caution_move_30d_pct"])
    if move is not None and abs(move) > lim:
        out.append({"key": "big_move", "text": f"SOL moved {move:+.1f}% in the last 30 days, more than the "
                                                f"±{lim:.0f}% caution level. Big recent moves make the next weeks "
                                                "harder to call."})
    r = cushion_ratio(s)
    if r is not None and r < 1.0:
        cushion = (spot - s["break_even"]) / spot * 100
        out.append({"key": "cushion", "text": f"The cushion to break-even ({cushion:.0f}%, to ${s['break_even']:.2f}) "
                                               f"is {r:.1f}x a typical move to expiry, less than 1.0x."})
    if v is not None:
        n, thin_below = v["scorecard"]["held_back"]["trades"], int(rep["held_back_thin_below"])
        if n < thin_below:
            out.append({"key": "thin", "text": f"Thin evidence: only {n} trades in the unseen period "
                                               f"(fewer than {thin_below})."})
        lost = [(t, v["scorecard"][k]) for k, t in (("in_sample", "practice period"), ("held_back", "unseen period"))
                if v["scorecard"][k]["trades"] and v["scorecard"][k]["total_per_1000"] < 0]
        for title, sc in lost:
            out.append({"key": "lost", "text": f"The live rule lost {money(sc['total_per_1000'])[1:]} per $1,000 "
                                               f"risked in the {title} ({sc['trades']} trades)."})
    if match is False:
        out.append({"key": "older_rules", "text": "The evidence is for an older version of the rules."})
    if similar is not None and not similar["enough"]:
        out.append({"key": "few_similar", "text": f"Only {similar['count']} similar past days (fewer than "
                                                   f"{similar['min_trades']}): too few to tell."})
    return out


# ------------------------------------------------------------------ the whole report
def for_list_debit(cfg: dict, result: dict, s: dict, sc: dict) -> list[str]:
    """Rules a debit idea passed (strategies/debit_spreads.toml), restated from the engine's checks."""
    vd = result["verdict"]
    out = [f"IV is {vd['label']}" + (f" at the {ordinal(vd['percentile'])} percentile" if vd.get("percentile") is not None
                                     else "") + " (rule: this strategy buys only on CHEAP days)."]
    out.append(f"Direction reading {s['direction']}: " + {"bullish": "buy the call spread.", "bearish":
               "buy the put spread.", "neutral": "buy both sides at half size each."}.get(s["direction"], ""))
    for x in s["sides"]:
        bits = f"{x['name'].capitalize()}: {x['dte']:.0f} days to expiry (rule: {sc['dte_min']} to {sc['dte_max']})"
        if x["cost_pct_of_width"] is not None:
            bits += f", costs {x['cost_pct_of_width']:.0f}% of its width (rule: at most {sc['max_cost_pct_of_width']}%)"
        out.append(bits + ".")
    out.append(f"Within the big-move budget: ${s['cost_usd']:,.0f} of ${s['budget_left_usd']:,.0f} left this month; "
               "the most you can lose is what you pay.")
    return out


def cautions_debit(rep: dict, today: dict, v: dict | None, match: bool | None, similar: dict | None) -> list[dict]:
    """The same caution rules as the put spread, minus the cushion one (a debit spread has no break-even cushion)."""
    out = []
    move, lim = today.get("sol_30d_change_pct"), float(rep["caution_move_30d_pct"])
    if move is not None and abs(move) > lim:
        out.append({"key": "big_move", "text": f"SOL moved {move:+.1f}% in the last 30 days, more than the "
                                                f"±{lim:.0f}% caution level."})
    if v is not None:
        n, thin_below = v["scorecard"]["held_back"]["trades"], int(rep["held_back_thin_below"])
        if n < thin_below:
            out.append({"key": "thin", "text": f"Thin evidence: only {n} trades in the unseen period "
                                               f"(fewer than {thin_below})."})
        for k, title in (("in_sample", "practice period"), ("held_back", "unseen period")):
            sc = v["scorecard"][k]
            if sc["trades"] and sc["total_per_1000"] < 0:
                out.append({"key": "lost", "text": f"This rule lost {money(sc['total_per_1000'])[1:]} per $1,000 in the "
                                                   f"{title} ({sc['trades']} trades)."})
    if match is False:
        out.append({"key": "older_rules", "text": "The evidence is for an older version of the rules."})
    if similar is not None and not similar["enough"]:
        out.append({"key": "few_similar", "text": f"Only {similar['count']} similar past days (fewer than "
                                                   f"{similar['min_trades']}): too few to tell."})
    return out


def load_other_backtests() -> dict:
    """Backtest output of the other strategy files (data/backtest_<name>.json), by strategy file name."""
    out = {}
    for p in sorted((ROOT / "data").glob("backtest_*.json")):
        b = load_backtest(p)
        if b and (b.get("strategy") or {}).get("file"):
            out[b["strategy"]["file"]] = b
    return out


def build(cfg: dict, result: dict, bt: dict | None, annotated_history: list[dict], view: dict,
          others: dict | None = None) -> dict:
    rep = cfg["report"]
    verdict_label = (result.get("verdict") or {}).get("label")
    today = todays_conditions(annotated_history, verdict_label)
    match = rules_match(result, bt)
    out = {"today": today, "view": view, "banners": banners(result, bt), "rules_match": match,
           "glance": glance(result, today, view), "cards": [], "settings": dict(rep)}
    spot = float(result.get("index_price") or 0)
    bts = dict(others or {})
    if bt and (bt.get("strategy") or {}).get("file"):
        bts[bt["strategy"]["file"]] = bt
    for s in result.get("strategies") or []:
        if s["status"] != "suggested" or not s.get("suggestion"):
            continue
        sug = s["suggestion"]
        b = bts.get(s.get("file"))
        v = live_variant(b, s.get("file"))
        sim = similar_days(b, v, today, rep) if v else None
        debit = sug.get("type") == "debit_spread"
        if debit:
            import engine
            dst = engine.load_strategy(ROOT / "strategies" / s["file"])
            m = None if not b else b["strategy"]["fingerprint"] == dst["_stamp"]["fingerprint"]
        out["cards"].append({
            "id": s["id"], "anchor": f"idea-{s['id']}", "name": s["name"], "type": sug.get("type", "bull_put_spread"),
            "evidence": evidence_box(b, v, s["id"]) if v else None,
            "evidence_missing": None if v else "No backtest results for this rule yet, so its track record can't be shown.",
            "for": for_list_debit(cfg, result, sug, dst["strikes"]) if debit else for_list(cfg, result, sug),
            "cautions": cautions_debit(rep, today, v, m, sim) if debit else cautions(rep, sug, spot, today, v, match, sim),
            "closing": CLOSING,
            "similar": sim,
            "during_trade": during_trade(v) if v else None,
        })
    return out


def load_backtest(path: Path = BACKTEST) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh(site_data: Path | None = None) -> dict:
    """Rebuild the report inside the already-written latest.json (after the daily backtest has run)."""
    import main
    import vol_gauge
    site_data = Path(site_data or main.SITE_DATA)
    p = site_data / "latest.json"
    dash = json.loads(p.read_text(encoding="utf-8"))
    res = dash.get("latest")
    if not res:
        raise SystemExit("latest.json has no result to report on")
    cfg = main.load_config()
    hist = vol_gauge.annotate_history(cfg, vol_gauge.load_history())
    today = res["generated_at"][:10]
    if res.get("direction"):
        import direction
        direction.refresh_block(direction.load_rules(), res["direction"], direction.load_test())
    res["report"] = build(cfg, res, load_backtest(), hist, load_view(today=today), load_other_backtests())
    old, main.SITE_DATA = main.SITE_DATA, site_data
    try:
        main.write_dashboard(dash, bool(dash.get("sample")))
    finally:
        main.SITE_DATA = old
    return res["report"]


if __name__ == "__main__":
    r = refresh(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"Report refreshed: {len(r['cards'])} idea card(s), {len(r['banners'])} banner(s).")
