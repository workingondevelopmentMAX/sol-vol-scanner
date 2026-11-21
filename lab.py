"""The Strategy Lab page's data (site/data/lab.json + lab.js). DISPLAY ONLY.

Every number on lab.html is worked out here from the backtest output (data/backtest.json) and the price history
(data/iv_history.csv); every sentence is a fixed template. Nothing here changes a rule, a suggestion or a backtest
result. Variants are listed in the order written in the strategy file, never ranked by results.

    python lab.py          rebuild site/data/lab.json (the daily job runs this after the backtest)
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import backtest
import chain_store
import engine
import main
import report
import strategy_menu
import vol_gauge

ROOT = Path(__file__).resolve().parent
SITE_DATA = ROOT / "site" / "data"
WINDOWS = report.WINDOWS
FOOTER = ("Backtest results use past data and, where no real quotes were stored, model prices. Small samples. "
          "Education and decision support, not financial advice. This tool cannot place orders.")
JUDGED_ON_TEXT = {
    "standard": "Will be judged on the standard scorecard: trades, win rate, results per $1,000 risked, worst trade, "
                "losing streak, drawdown, practice vs unseen period, against holding and weekly buying SOL.",
    "hedge": "Will be judged on how much it reduced the core SOL holding's drawdowns versus what it cost.",
    "accumulation": "Will be judged on SOL acquired per dollar versus plain weekly buying.",
    "none": "Not a trade: nothing to backtest.",
}
STATUS_TEXT = {"live": "Live", "in_testing": "In testing", "not_built": "Not built"}


def mon(d: str) -> str:
    return date.fromisoformat(d).strftime("%b %Y")


# ------------------------------------------------------------------ fixed explanations
def how_to_read(bt: dict | None, st: dict | None) -> list[str]:
    w = (bt or {}).get("windows")
    if w and st:
        practice = f"{mon(w['in_sample'][0])} - {mon(w['in_sample'][1])}"
        held_end = "today" if not st["evaluation"]["held_back"][1] else mon(w["held_back"][1])
        unseen = f"{mon(w['held_back'][0])} - {held_end}"
    else:
        practice, unseen = "Jun 2024 - Jun 2025", "Jul 2025 - today"
    return [f"Practice period (in-sample, {practice}): rules were chosen while looking at these days, so good results "
            "here prove little.",
            f"Unseen period (held back, {unseen}): never used to choose rules, the fairer test.",
            "\"Per $1,000\" means: if you risked $1,000 on every trade, this is what you'd have made or lost.",
            "A version that made money in both periods is a stronger sign than one big number in one period. Switching "
            "to whichever version won the unseen period turns that period into practice too, so any switch is then "
            "judged on new days from here on."]


def rules_summary(st: dict) -> list[str]:
    """The strategy file's rules in plain words, worded for its type."""
    if engine.strategy_type(st) == "debit_spread":
        return rules_summary_debit(st)
    e, k, x = st["entry"], st["strikes"], st["exits"]
    need = e.get("require_verdict")
    out = [f"When it trades: only when IV is {need} (the rich/cheap gauge in config.toml)." if need
           else "When it trades: every day, whatever the IV verdict.",
           f"Strikes: an expiry {k['dte_min']} to {k['dte_max']} days away; sell a put near {k['short_delta_target']:.2f} "
           f"delta (allowed {k['short_delta_min']} to {k['short_delta_max']}); buy a put about "
           f"{k['long_strike_offset_pct']}% lower; the credit must be at least {k['min_credit_pct_of_width']}% of the "
           "strike gap, with tight enough bid-ask spreads.",
           "Exits: " + exits_text(x) + "."]
    return out


def rules_summary_debit(st: dict) -> list[str]:
    e, k, x, b = st["entry"], st["strikes"], st["exits"], st["budget"]
    need = e.get("require_verdict")
    return [(f"When it trades: only when IV is {need}" if need else "When it trades: any day")
            + f", at most {e['max_new_trades_per_month']} new trade per calendar month, and only when no big-move trade "
              "is still open.",
            "Direction: from the direction reading (data factors that passed testing, tilted by your view): bullish = "
            "call spread, bearish = put spread, neutral = both, each at half the budget.",
            f"Strikes: an expiry about {k['dte_target']} days away ({k['dte_min']}-{k['dte_max']}); buy the "
            f"{k['long_delta']:.2f}-delta call (or -{k['long_delta']:.2f} put), sell the strike about "
            f"{k['short_offset_pct']}% further out; skip if it costs more than {k['max_cost_pct_of_width']}% of the "
            "strike gap or the legs are too illiquid.",
            "Exits: " + exits_text(x) + ". No stop: the most a trade can lose is what it cost.",
            f"Budget: the separate big-move pool, ${b['reference_per_month_usd']:,.0f} a month (reference). A both-sides "
            "entry counts as one trade."]


def exits_text(x: dict) -> str:
    parts = []
    if "take_profit_multiple" in x:                           # debit spreads
        tp = float(x.get("take_profit_multiple", 0))
        te = int(x.get("time_exit_dte", 0))
        return ", ".join([f"take profit when worth {tp:g}x what it cost" if tp else "no take profit",
                          f"close {te} days before expiry" if te else "hold to expiry"])
    tp = float(x.get("take_profit_pct", 0))
    parts.append(f"take profit when {tp:.0f}% of the credit is kept" if tp else "no take profit")
    sm = float(x.get("stop_multiple", 0))
    parts.append(f"stop when the loss reaches {sm:g}x the credit" if sm else "no stop")
    te = int(x.get("time_exit_dte", 0))
    parts.append(f"close {te} days before expiry" if te else "hold to expiry")
    return ", ".join(parts)


def variant_text(name: str, settings: dict, st: dict) -> str:
    """One plain sentence for a [[variants]] entry: what it changes from the rules above."""
    if not settings:
        return "The rules above, exactly as written."
    bits = []
    if settings.get("exits"):
        x = dict(st["exits"], **settings["exits"])
        bits.append("exits: " + exits_text(x))
    if "require_verdict" in settings:
        bits.append("trades any day (no IV filter)" if not settings["require_verdict"]
                    else f"trades only when IV is {settings['require_verdict']}")
    if settings.get("strikes"):
        k = settings["strikes"]
        if "dte_target" in k:
            bits.append(f"expiry about {k['dte_target']} days ({k.get('dte_min')}-{k.get('dte_max')})")
        if k.get("structure") == "outright":
            bits.append("buys the near option alone instead of a spread")
    if settings.get("direction"):
        bits.append({"calls": "always buys the call side, whatever the direction",
                     "puts": "always buys the put side, whatever the direction",
                     "both": "always buys both sides, ignoring the direction"}.get(settings["direction"].get("sides"),
                                                                                   "different direction rule"))
    if settings.get("one_at_a_time") is False:
        bits.append("opens a new trade every eligible day, even with others still open")
    return "Same rules, but " + "; ".join(bits) + "."


# ------------------------------------------------------------------ series (cumulative, drawdown, benchmarks)
def window_of(d: str, win: dict) -> str | None:
    for k, _ in WINDOWS:
        lo, hi = win[k]
        if lo <= d <= hi:
            return k
    return None


def running(trades: list[dict], lo: str, hi: str) -> dict:
    """Cumulative result per $1,000 for one period, starting at $0 on its first day, one point per trade at its
    entry date (in the backtest's own order, as the scorecard counts), and the drawdown below the running best."""
    eq = peak = 0.0
    pts, dd = [{"date": lo, "value": 0.0}], [{"date": lo, "value": 0.0}]
    for t in trades:
        if not lo <= t["date"] <= hi:
            continue
        eq += t["pnl_per_1000"]
        peak = max(peak, eq)
        pts.append({"date": t["date"], "value": round(eq, 1)})
        dd.append({"date": t["date"], "value": round(eq - peak, 1)})
    return {"points": pts, "drawdown": dd}


def benchmarks(days: list[dict], lo: str, hi: str, every_days: int = 7) -> dict:
    """$1,000 of SOL bought on the period's first day, and $1,000 spread over weekly buys, valued every day."""
    inside = [d for d in days if lo <= d["date"] <= hi and d["S"]]
    if len(inside) < 2:
        return {"hold": [], "dca": []}
    s0, start = inside[0]["S"], date.fromisoformat(inside[0]["date"])
    buys = [d for d in inside if (date.fromisoformat(d["date"]) - start).days % every_days == 0]
    per = 1000 / len(buys)
    hold, dca, sol, spent, bi = [], [], 0.0, 0.0, 0
    for d in inside:
        while bi < len(buys) and buys[bi]["date"] <= d["date"]:
            sol += per / buys[bi]["S"]
            spent += per
            bi += 1
        hold.append({"date": d["date"], "value": round((d["S"] / s0 - 1) * 1000, 1)})
        dca.append({"date": d["date"], "value": round(sol * d["S"] + (1000 - spent) - 1000, 1)})
    return {"hold": hold, "dca": dca}


# ------------------------------------------------------------------ results by entry-day conditions
def move_bucket(move: float | None, lim: float) -> str:
    if move is None:
        return "30-day move unknown"
    if move < -lim:
        return f"SOL down more than {lim:.0f}%"
    if move > lim:
        return f"SOL up more than {lim:.0f}%"
    return f"SOL within ±{lim:.0f}%"


def entry_groups(trades: list[dict], lo: str, hi: str, rep: dict) -> list[dict]:
    """Groups by the ENTRY-day 30-day move and IV verdict only (entry_conditions), never by what came after."""
    lim, need = float(rep["caution_move_30d_pct"]), int(rep["similar_min_trades"])
    order_m = [f"SOL down more than {lim:.0f}%", f"SOL within ±{lim:.0f}%", f"SOL up more than {lim:.0f}%",
               "30-day move unknown"]
    order_v = ["RICH", "FAIR", "CHEAP", "NO VERDICT"]
    groups: dict = {}
    for t in trades:
        if not lo <= t["date"] <= hi:
            continue
        c = t["entry_conditions"]
        key = (move_bucket(c.get("sol_30d_change_pct"), lim), c.get("verdict") or "NO VERDICT")
        g = groups.setdefault(key, {"move": key[0], "verdict": key[1], "trades": 0, "wins": 0, "total": 0.0})
        g["trades"] += 1
        g["wins"] += t["pnl_per_1000"] > 0
        g["total"] += t["pnl_per_1000"]
    out = []
    for key in sorted(groups, key=lambda k: (order_m.index(k[0]), order_v.index(k[1]) if k[1] in order_v else 9)):
        g = groups[key]
        total = round(g["total"], 1)
        out.append({"move": g["move"], "verdict": g["verdict"], "trades": g["trades"], "wins": g["wins"],
                    "total_per_1000": total, "avg_per_1000": round(total / g["trades"], 1),
                    "too_few": g["trades"] < need,
                    "note": "too few to tell" if g["trades"] < need else ""})
    return out


# ------------------------------------------------------------------ one strategy
def chart_lines(t: dict) -> list[list]:
    """Horizontal lines for a trade's price chart: [price, label, colour token]."""
    if t.get("sides"):                                          # debit spreads: bought and sold strikes per side
        out = []
        for x in t["sides"]:
            out.append([x["Kl"], f"Bought {x['kind']} {x['Kl']:g}", "--good"])
            if x["Ks"] is not None:
                out.append([x["Ks"], f"Sold {x['kind']} {x['Ks']:g}", "--text-muted"])
        return out
    return [[t["Ks"], f"Short {t['Ks']:g}", "--rich"], [t["Kl"], f"Long {t['Kl']:g}", "--text-muted"],
            [round(t["Ks"] - t["credit"], 4), f"Break-even {t['Ks'] - t['credit']:.2f}", "--series-rv"]]


def trade_rows(trades: list[dict], win: dict, index_of: dict) -> list[dict]:
    rows = []
    for t in trades:
        c = t["entry_conditions"]
        k = window_of(t["date"], win)
        rows.append({"date": t["date"], "window": k,
                     "window_title": dict(WINDOWS).get(k, "Before the test periods"),
                     "sol": t["S"], "move_30d_pct": c.get("sol_30d_change_pct"), "iv_percentile": c.get("iv_percentile"),
                     "verdict": c.get("verdict"), "short_strike": t["Ks"], "long_strike": t["Kl"],
                     "strikes_text": t.get("strikes_text") or f"{t['Ks']:g}/{t['Kl']:g}",
                     "break_even": round(t["Ks"] - t["credit"], 4) if t.get("credit") is not None else None,
                     "expiry": t["expiry"],
                     "pnl_per_1000": t["pnl_per_1000"], "result": report.money(t["pnl_per_1000"]),
                     "loss": t["pnl_per_1000"] < 0, "exit": t["exit"], "exit_date": t["exit_date"],
                     "quotes": "real quotes" if t.get("chain") == "stored" else "model",
                     "price_from": index_of.get(t["date"]), "price_to": index_of.get(t["exit_date"]),
                     "lines": chart_lines(t)})
    rows.sort(key=lambda r: r["date"], reverse=True)              # newest first
    return rows


def paper_rows(st: dict) -> list[dict]:
    import paper
    import main
    rows = [r for r in paper.read(main.PAPER_LOG or paper.PAPER_LOG) if r.get("strategy") == st["strategy"]["name"]]
    return [{k: r.get(k) for k in ("date", "event", "status", "direction", "legs", "cost_usd", "take_profit",
                                   "time_exit", "side", "reason", "result_per_1000")} for r in reversed(rows)]


def strategy_section(entry: dict, bt: dict, st: dict, rep: dict, days: list[dict], index_of: dict) -> dict:
    win = bt["windows"]
    live = (bt.get("selection") or {}).get("chosen") or report.LIVE_VARIANT
    variants = []
    for v in bt["variants"]:                                   # the strategy file's order, as backtest.py wrote it
        variants.append({
            "name": v["name"], "live": v["name"] == live, "text": variant_text(v["name"], v["settings"], st),
            "evidence": v["evidence"],
            "evidence_title": report.evidence_box(bt, v)["title"],
            "windows": {k: {"rows": report.window_rows(v["scorecard"][k]),
                            "series": running(v["trades"], *win[k]),
                            "groups": entry_groups(v["trades"], *win[k], rep)} for k, _ in WINDOWS},
            "during_trade": report.during_trade(v),
            "trades": trade_rows(v["trades"], win, index_of),
        })
    first = bt["variants"][0]["scorecard"]
    live_v = next((v for v in bt["variants"] if v["name"] == live), bt["variants"][0])
    n = len(live_v["trades"])
    real = sum(t.get("chain") == "stored" for t in live_v["trades"])
    return {
        "id": entry["id"], "anchor": f"lab-{Path(entry['file']).stem}",
        "name": st["strategy"].get("title", entry["name"]) if engine.strategy_type(st) != "bull_put_spread" else entry["name"],
        "live_label": "Chosen version" if bt.get("selection") else "Live rule",
        "selection": bt.get("selection"), "pass": bt.get("pass"),
        "paper": paper_rows(st) if engine.strategy_type(st) == "debit_spread" else None,
        "stamp": bt["strategy"], "rules": rules_summary(st),
        "quotes_line": f"{real} of {n} trades used real quotes; the rest use model prices built from that day's IV.",
        "windows": [{"key": k, "title": t, "label": (bt.get("window_labels") or {}).get(k, ""),
                     "range": win[k]} for k, t in WINDOWS],
        "benchmarks": {k: {"hold": first[k]["hold_sol"], "dca": first[k]["dca_sol"],
                           "series": benchmarks(days, *win[k])} for k, _ in WINDOWS},
        "variants": variants, "default_variant": live,
    }


# ------------------------------------------------------------------ direction signal section
DIRECTION_EXPLAIN = [
    "\"Next 30-day move\" is SOL's % change from a day's close to the close 30 days later; each factor is judged on "
    "whether its bullish days were followed by bigger moves than its bearish days.",
    "Days only 1 day apart share almost the same next 30 days, so they are not independent: \"non-overlapping "
    "periods\" counts how many separate 30-day stretches each state really covers (take a day, skip 30 days, take "
    "the next), and a factor needs at least 4 in each state to count.",
]


def direction_section(test: dict | None) -> dict | None:
    if not test:
        return None
    pr = test["pass_rule"]
    rows = []
    for f in test["factors"]:                         # the order written in strategies/direction.toml
        r = {"name": f["name"], "title": f["title"], "result": f["result"], "note": f.get("note"),
             "one_sided": f.get("one_sided", False), "windows": []}
        if f["result"] != "RECORDING ONLY":
            for k, title in WINDOWS:
                g, j = f["windows"][k], f["judge"]["windows"][k]
                groups = (("fear", "Fear days"), ("other", "Other days")) if f.get("one_sided") else                     (("bullish", "Bullish"), ("neutral", "Neutral"), ("bearish", "Bearish"))
                r["windows"].append({"key": k, "title": title, "spread_points": j["spread_points"],
                                     "enough_periods": j["enough_periods"], "pass": j["pass"],
                                     "unknown_days": g["unknown_days"],
                                     "groups": [{"state": lbl, **g[s]} for s, lbl in groups]})
        rows.append(r)
    passed = test["passed"]
    return {"rules": test["rules"], "windows": test["windows"], "explain": DIRECTION_EXPLAIN,
            "pass_rule": (f"PASS needs, in both periods: average next-30-day move when bullish minus when bearish of at "
                          f"least +{pr['min_spread_points']:g} points, and at least {pr['min_periods']} non-overlapping "
                          "30-day periods in each state (one-sided crash/fear: fear days vs all other days, at least "
                          f"{pr['min_periods']} fear periods). Set in advance in strategies/direction.toml."),
            "summary": (f"{len(passed)} of {sum(1 for f in test['factors'] if f['result'] != 'RECORDING ONLY')} tested "
                        "factors passed" + (f": {', '.join(passed)}." if passed else
                                            ", so the data signal stays neutral and only your view tilts it.")),
            "baseline": [{"key": k, "title": t, **test["baseline"][k]} for k, t in WINDOWS],
            "factors": rows}


# ------------------------------------------------------------------ the whole page
def load_backtests(entries: list[dict]) -> dict:
    """Backtest output per strategy file. Today data/backtest.json covers the default strategy; another strategy
    file's results are read from data/backtest_<name>.json once its backtest exists."""
    out = {}
    default = report.load_backtest()
    for e in entries:
        f = e.get("file")
        if not f:
            continue
        if default and (default.get("strategy") or {}).get("file") == f:
            out[f] = default
        else:
            other = report.load_backtest(ROOT / "data" / f"backtest_{Path(f).stem}.json")
            if other:
                out[f] = other
    return out


def build(cfg: dict, entries: list[dict], bts: dict, history: list[dict], latest: dict | None,
          now: str | None = None, direction_test: dict | None = None) -> dict:
    rep = cfg["report"]
    days = [{"date": r["date"], "S": r["index_price"]} for r in history if r.get("index_price")]
    prices = [[d["date"], d["S"]] for d in days]
    index_of = {d["date"]: i for i, d in enumerate(days)}
    bt0 = next(iter(bts.values()), None)
    st0 = engine.load_strategy(ROOT / "strategies" / bt0["strategy"]["file"]) if bt0 else None
    banners = report.banners(latest or {}, bt0)
    out = {"built_at": now or datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "how_to_read": how_to_read(bt0, st0), "footer": FOOTER, "banners": banners,
           "period": bt0["period"] if bt0 else None,
           "data_fingerprint": bt0.get("data_fingerprint") if bt0 else None,
           "menu": [], "strategies": [], "prices": prices, "settings": dict(rep),
           "direction": direction_section(direction_test)}
    for e in entries:
        bt = bts.get(e.get("file")) if e.get("file") else None
        row = {"id": e["id"], "name": e["name"], "market_text": report.MARKET_TEXT[e["market"]],
               "status": e["status"], "status_text": STATUS_TEXT[e["status"]], "anchor": None,
               "evidence_title": None, "note": None}
        if bt:
            sec = next((x for x in out["strategies"] if x["anchor"] == f"lab-{Path(e['file']).stem}"), None)
            if sec is None:                                    # one section per strategy file
                st = engine.load_strategy(ROOT / "strategies" / e["file"])
                sec = strategy_section(e, bt, st, rep, days, index_of)
                out["strategies"].append(sec)
            live = next(v for v in sec["variants"] if v["live"]) if any(v["live"] for v in sec["variants"]) \
                else sec["variants"][0]
            row.update(anchor=sec["anchor"], evidence_title=live["evidence_title"])
            if (sec.get("pass") or {}).get("status") == "live":
                row["status_text"] = "Passed its test"
            elif (sec.get("pass") or {}).get("passed"):
                row["status_text"] = "Passed, held in testing"
        else:
            row["note"] = ("No backtest yet. " if e.get("judged_on", "standard") != "none" else "") + \
                JUDGED_ON_TEXT[e.get("judged_on", "standard")]
        out["menu"].append(row)
    if out["strategies"]:
        out["quotes_line"] = out["strategies"][0]["quotes_line"]
    return out


def publish(site_data: Path = SITE_DATA, now: str | None = None) -> dict:
    cfg = main.load_config(local=False)
    entries = strategy_menu.load()
    latest_path = site_data / "latest.json"
    latest = None
    if latest_path.exists():
        latest = (json.loads(latest_path.read_text(encoding="utf-8")).get("latest")) or None
    hist = vol_gauge.load_history()
    import direction
    lab = build(cfg, entries, load_backtests(entries), hist, latest, now, direction.load_test())
    text = json.dumps(lab, default=str, allow_nan=False)
    site_data.mkdir(parents=True, exist_ok=True)
    (site_data / "lab.json").write_text(text, encoding="utf-8")
    (site_data / "lab.js").write_text("window.SCANNER_LAB = " + text + ";\n", encoding="utf-8")
    return lab


if __name__ == "__main__":
    lab = publish(Path(sys.argv[1]) if len(sys.argv) > 1 else SITE_DATA)
    print(f"Strategy Lab data: {len(lab['strategies'])} built strateg(ies), {len(lab['menu'])} menu rows, "
          f"{len(lab['banners'])} banner(s).")
