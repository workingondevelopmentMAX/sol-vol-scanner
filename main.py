"""SOL Vol Scanner v0 - read-only. Prints today's verdict and, if IV is rich, one bull put spread idea.

  python main.py              text summary, updates history and the dashboard
  python main.py --json       machine-readable output
  python main.py --sample     run on saved sample data (offline test)
  python main.py --backfill   first, approximate past ATM IV from Deribit trade history

This tool cannot place orders. Output is a suggestion for a human decision, not financial advice.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tomllib
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import data_fetch
import explain
import suggest
import vol_gauge
import bsmodel
import rationale
import engine
import exits

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.toml"
LOCAL_CONFIG = ROOT / "config.local.toml"   # private overrides on your own computer; never committed
SUGGESTIONS_CSV = ROOT / "logs" / "suggestions.csv"
TERM_HISTORY = ROOT / "data" / "term_history.json"
SITE_DATA = ROOT / "site" / "data"

LOG_FIELDS = ["run_at", "date", "verdict", "mode", "atm_iv", "rv30", "percentile", "iv_rv_ratio", "trade",
              "conviction", "conviction_score",
              "expiry", "short_strike", "long_strike", "credit_conservative", "credit_mid", "max_loss",
              "break_even", "prob_profit_approx", "contracts", "contract_size", "usd_at_risk", "reason",
              "settle_price", "outcome", "pnl_per_sol"]


SAMPLE_DIR = ROOT / "sample_output"


def use_sample_paths() -> None:
    """Keep sample runs away from your real history and log."""
    global SUGGESTIONS_CSV, TERM_HISTORY
    SAMPLE_DIR.mkdir(exist_ok=True)
    SUGGESTIONS_CSV = SAMPLE_DIR / "suggestions.csv"
    TERM_HISTORY = SAMPLE_DIR / "term_history.json"
    vol_gauge.HISTORY_CSV = SAMPLE_DIR / "iv_history.csv"
    seed = ROOT / "data" / "sample" / "iv_history_seed.csv"
    if not vol_gauge.HISTORY_CSV.exists() and seed.exists():
        vol_gauge.HISTORY_CSV.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")


PRIVATE_DIR = ROOT / "private"


def use_private_paths() -> None:
    """With config.local.toml present, a laptop run never touches the shared (published) files.

    It starts from the latest shared history (as pulled from GitHub), adds today's reading, and writes
    everything to private/ and site/data/private/, both ignored by Git. The dashboard shows the private
    view when it exists, so you see your own limits and premium log; the cloud copy stays generic.
    """
    global SUGGESTIONS_CSV, TERM_HISTORY, SITE_DATA
    PRIVATE_DIR.mkdir(exist_ok=True)
    for name in ("iv_history.csv", "term_history.json"):
        shared = ROOT / "data" / name
        if shared.exists():
            (PRIVATE_DIR / name).write_bytes(shared.read_bytes())
    vol_gauge.HISTORY_CSV = PRIVATE_DIR / "iv_history.csv"
    TERM_HISTORY = PRIVATE_DIR / "term_history.json"
    SUGGESTIONS_CSV = PRIVATE_DIR / "suggestions.csv"
    SITE_DATA = ROOT / "site" / "data" / "private"


def merge_settings(base: dict, over: dict) -> dict:
    """Section-by-section overlay: values in config.local.toml replace the same keys in config.toml."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load_config(strategy_path=None) -> dict:
    """config.toml (market data, risk limits, vol gauge) + the strategy file's rules, as one settings view."""
    with CONFIG.open("rb") as f:
        cfg = tomllib.load(f)
    if LOCAL_CONFIG.exists():
        with LOCAL_CONFIG.open("rb") as f:
            cfg = merge_settings(cfg, tomllib.load(f))
    st = engine.load_strategy(strategy_path or engine.DEFAULT_STRATEGY)
    cfg = engine.with_strategy(cfg, st)
    cfg["_strategy"] = st
    return cfg


# ------------------------------------------------------------------ logs
def read_log() -> list[dict]:
    if not SUGGESTIONS_CSV.exists():
        return []
    with SUGGESTIONS_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_log(rows: list[dict]) -> None:
    SUGGESTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SUGGESTIONS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


PREMIUM_CSV = ROOT / "my_premium_log.csv"
LOCAL_PREMIUM_CSV = ROOT / "my_premium_log.local.csv"   # your real trades stay here, never committed


def read_premium_log() -> dict:
    """Your own record of credits taken and SOL bought with them (edit my_premium_log.csv in Excel)."""
    out = {"trades": 0, "credit_usd": 0.0, "sol_bought": 0.0, "rows": []}
    path = LOCAL_PREMIUM_CSV if LOCAL_PREMIUM_CSV.exists() else PREMIUM_CSV
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            def num(k):
                try:
                    return float(str(r.get(k, "")).replace("$", "").replace(",", ""))
                except ValueError:
                    return None
            credit, px, sol = num("credit_usd"), num("sol_price_when_bought"), num("sol_bought")
            if credit is None:
                continue
            if sol is None and px:
                sol = credit / px
            out["trades"] += 1
            out["credit_usd"] += credit
            out["sol_bought"] += sol or 0.0
            out["rows"].append({"date": r.get("date"), "spread": r.get("spread"), "credit_usd": credit, "sol_bought": sol})
    return out


def settle_outcomes(rows: list[dict], delivery: dict, now: datetime) -> list[dict]:
    """Fill in what happened to past trade suggestions once their expiry has a delivery price."""
    prices = {d.get("date"): d.get("delivery_price") for d in (delivery or {}).get("data", [])}
    for r in rows:
        if r.get("trade") != "yes" or r.get("outcome"):
            continue
        exp = r.get("expiry")
        if not exp or datetime.fromisoformat(exp + "T08:00:00+00:00") > now:
            continue
        settle = prices.get(exp)
        if settle is None:
            continue
        ks, kl, cr = float(r["short_strike"]), float(r["long_strike"]), float(r["credit_conservative"])
        pnl = suggest.payoff_at_expiry(ks, kl, cr, float(settle))
        if settle >= ks:
            label = "kept full"
        elif settle <= kl:
            label = "max loss"
        else:
            label = "partial (profit)" if pnl >= 0 else "partial (loss)"
        r.update(settle_price=f"{settle:.4f}", outcome=label, pnl_per_sol=f"{pnl:.4f}")
    return rows


# ------------------------------------------------------------------ checks
def price_unit_check(chain: list[dict]) -> dict:
    """Compare Deribit mark prices with our model, to confirm prices are quoted per 1 SOL."""
    ratios = []
    for o in chain:
        if o["iv"] and o["mark"] and o["dte"] > 3 and abs(o["strike"] / o["F"] - 1) < 0.15:
            model = bsmodel.price(o["F"], o["strike"], o["T"], o["iv"], o["type"])
            if model > 0:
                ratios.append(o["mark"] / model)
    if not ratios:
        return {"ok": None, "note": "Could not check price units"}
    r = statistics.median(ratios)
    ok = 0.85 <= r <= 1.15
    return {"ok": ok, "median_mark_to_model": r,
            "note": "Prices look quoted per 1 SOL (as assumed)" if ok else
            f"WARNING: Deribit mark prices are {r:.2f}x the model - prices may not be per 1 SOL. "
            "Check contract specs before trusting dollar figures."}


# ------------------------------------------------------------------ backfill
def run_backfill(cfg: dict, quiet: bool) -> None:
    b = cfg["backfill"]
    client = data_fetch.Client(cfg, b.get("history_base_url"))
    hist = vol_gauge.load_history()
    have = {r["date"] for r in hist if r.get("atm_iv_30d") is not None}   # never re-fetch a day we already have
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    found = {}
    started, filled = datetime.now(timezone.utc), 0
    for i in range(int(b["days"]), 0, -1):
        day = today - timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        if key in have:
            continue
        try:
            trades, scanned = data_fetch.fetch_trades_for_day(cfg, client, day)
        except Exception as e:  # one bad day must never stop the whole backfill
            if not quiet:
                print(f"  {key}: skipped ({type(e).__name__}: {str(e)[:120]})")
            continue
        iv, n = vol_gauge.iv_from_trades(trades, b["min_dte"], b["max_dte"], b["max_moneyness_pct"] / 100,
                                         int(b["min_trades_per_day"]))
        if iv is not None:
            found[key] = iv
            filled += 1
        if len(found) and len(found) % 25 == 0:   # save progress so a long run is never lost
            vol_gauge.save_history(vol_gauge.merge_history(vol_gauge.load_history(), [], iv_overrides=found))
        if not quiet:
            print(f"  {key}: scanned {scanned} USDC option trades, {len(trades)} SOL, {n} usable -> "
                  + (f"IV {iv:.1%}" if iv else "not enough"))
    hist = vol_gauge.merge_history(hist, [], iv_overrides=found)
    vol_gauge.save_history(hist)
    if not quiet:
        print(f"Backfill added approximate IV for {len(found)} of {b['days']} day(s). ({client.calls} API calls)")


# ------------------------------------------------------------------ main run
def analyse(cfg: dict, snap: dict) -> dict:
    now = datetime.fromisoformat(snap["fetched_at"])
    today = now.strftime("%Y-%m-%d")
    chain = vol_gauge.build_chain(snap)
    ts = vol_gauge.term_structure(chain)
    atm30 = vol_gauge.constant_maturity_iv(ts, float(cfg["vol"]["target_dte"]))
    closes = vol_gauge.daily_closes(snap["chart"])
    vals = [c for _, c in closes]
    rv20, rv30 = vol_gauge.realised_vol(vals, 20), vol_gauge.realised_vol(vals, 30)

    hist = vol_gauge.merge_history(vol_gauge.load_history(), closes, today, atm30, snap["index_price"])
    dv = snap.get("dvol") or {"available": False, "rows": [], "error": "not fetched"}
    dvol_info = {"available": dv["available"], "error": dv.get("error"), "days": len(dv["rows"])}
    if dv["available"]:
        hist, info = vol_gauge.merge_dvol(hist, dv["rows"])
        dvol_info.update(info)
        hist = vol_gauge.merge_history(hist, closes)  # realised vol + price for any newly added days
    window_start = (now - timedelta(days=365)).strftime("%Y-%m-%d")   # compare with the last 12 calendar months only
    prior = [r["atm_iv_30d"] for r in hist
             if window_start <= r["date"] < today and r.get("atm_iv_30d") is not None]
    vd = vol_gauge.verdict(cfg, atm30, rv30, prior)
    vd["rv20"] = rv20

    result = {
        "generated_at": snap["fetched_at"],
        "index_price": snap["index_price"],
        "verdict": vd,
        "term_structure": ts,
        "checks": {"price_units": price_unit_check(chain), "options_in_chain": len(chain), "dvol": dvol_info},
        "suggestion": None,
        "alternates": [],
        "no_trade_reason": None,
    }

    spot = float(snap["index_price"])
    ctx = rationale.market_context(ts, vals, spot)
    result["market_context"] = ctx
    st = cfg["_strategy"]
    result["strategy"] = st["_stamp"]
    dec = engine.decide(cfg, st, vd, chain)          # the same decision function the backtest uses
    if dec["trade"] is None:
        result["no_trade_reason"] = dec["reason"]
    else:
        best = dec["trade"]
        sz = suggest.size(cfg, best)
        panel = explain.greeks_panel(best, sz, float(snap["index_price"]))
        result["suggestion"] = {**suggest.strip(best), "sizing": sz, "greeks_panel": panel}
        result["alternates"] = [{**suggest.strip(c), "sizing": suggest.size(cfg, c)} for c in dec["alternates"]]
        s = result["suggestion"]
        s["exit_plan"] = engine.exit_plan(cfg, st, best, sz, spot)
        s["conviction"] = rationale.conviction(cfg, vd, s, spot)
        s["why"] = rationale.why_trade(cfg, vd, s, ctx, spot, s["conviction"])
    if result["suggestion"] is None:
        result["why_no_trade"] = rationale.why_no_trade(vd, result["no_trade_reason"])
    return {"result": result, "history": hist, "today": today, "now": now}


def save_outputs(cfg: dict, snap: dict, out: dict, sample: bool = False) -> dict:
    result, hist, today, now = out["result"], out["history"], out["today"], out["now"]
    vol_gauge.save_history(hist)

    # term structure memory, for "last week" comparison on the dashboard
    th = json.loads(TERM_HISTORY.read_text(encoding="utf-8")) if TERM_HISTORY.exists() else {}
    th[today] = result["term_structure"]
    th = dict(sorted(th.items())[-90:])
    TERM_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    TERM_HISTORY.write_text(json.dumps(th), encoding="utf-8")
    wk = None
    for d in sorted(th):
        age = (datetime.fromisoformat(today) - datetime.fromisoformat(d)).days
        if 5 <= age <= 9:
            wk = {"date": d, "rows": th[d]}
    result["term_structure_last_week"] = wk

    # suggestion log (one row per run) + outcomes
    rows = settle_outcomes(read_log(), snap.get("delivery_prices"), now)
    vd, s = result["verdict"], result["suggestion"]
    row = {"run_at": result["generated_at"], "date": today, "verdict": vd["label"], "mode": vd.get("mode"),
           "atm_iv": vd.get("atm_iv"), "rv30": vd.get("rv30"), "percentile": vd.get("percentile"),
           "iv_rv_ratio": vd.get("iv_rv_ratio"), "trade": "yes" if s else "no",
           "reason": result["no_trade_reason"] or ""}
    if s:
        row.update(expiry=s["expiry"], short_strike=s["short_leg"]["strike"], long_strike=s["long_leg"]["strike"],
                   credit_conservative=round(s["credit_conservative"], 4), credit_mid=round(s["credit_mid"], 4),
                   max_loss=round(s["max_loss"], 4), break_even=round(s["break_even"], 4),
                   prob_profit_approx=round(s["prob_profit_approx"], 3), contracts=s["sizing"]["contracts"],
                   contract_size=s["contract_size"], usd_at_risk=round(s["sizing"]["usd_at_risk"], 2),
                   conviction=s["conviction"]["label"], conviction_score=s["conviction"]["score"])
    rows = [r for r in rows if not (r.get("date") == today and r.get("trade") == "no" and not s)]
    rows.append(row)
    write_log(rows)

    annotated = vol_gauge.annotate_history(cfg, hist)
    premium = read_premium_log()
    dash = {
        "status": "ok",
        "last_good_at": result["generated_at"],
        "latest": result,
        "history": annotated[-1000:],
        "log": list(reversed(rows))[:60],
        "premium": premium,
        "config": {"risk": cfg["risk"], "vol": cfg["vol"], "spread": cfg["spread"], "exits": cfg["exits"],
                   "conviction": cfg.get("conviction", {}), "strategy": cfg["strategy"]},
    }
    write_dashboard(dash, sample)
    return dash


def write_dashboard(dash: dict, sample: bool = False) -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    if sample:
        existing = SITE_DATA / "latest.json"
        if existing.exists() and not json.loads(existing.read_text(encoding="utf-8")).get("sample"):
            return  # never overwrite a real dashboard with sample data
        dash["sample"] = True
    text = json.dumps(dash, default=str, allow_nan=False)
    (SITE_DATA / "latest.json").write_text(text, encoding="utf-8")
    # data.js lets index.html work when opened straight from disk (file://) as well as over Wi-Fi
    var = "SCANNER_PRIVATE_DATA" if SITE_DATA.name == "private" else "SCANNER_DATA"
    (SITE_DATA / "data.js").write_text(f"window.{var} = " + text + ";\n", encoding="utf-8")


def mark_stale(error: str) -> None:
    p = SITE_DATA / "latest.json"
    if p.exists():
        dash = json.loads(p.read_text(encoding="utf-8"))
    else:
        dash = {"latest": None, "history": [], "log": [], "last_good_at": None}
    dash.update(status="error", error=error, error_at=datetime.now(timezone.utc).isoformat())
    write_dashboard(dash)


# ------------------------------------------------------------------ printing
def pct(x):
    return "n/a" if x is None else f"{x:.1%}"


def print_summary(r: dict) -> None:
    vd = r["verdict"]
    print("=" * 62)
    print(f" SOL VOL SCANNER   {r['generated_at'][:16].replace('T', ' ')} UTC")
    print("=" * 62)
    print(f" SOL index      ${r['index_price']:,.2f}")
    print(f" ATM IV (30d)   {pct(vd.get('atm_iv'))}")
    print(f" Realised vol   RV20 {pct(vd.get('rv20'))}   RV30 {pct(vd.get('rv30'))}")
    if vd.get("mode") == "percentile":
        print(f" IV percentile  {vd['percentile']:.0f}  ({vd['history_days']} days of history)")
    else:
        ratio = vd.get("iv_rv_ratio")
        print(f" IV / RV30      {ratio:.2f}  (bootstrap mode: {vd.get('history_days', 0)} days of IV history)" if ratio else " IV / RV30      n/a")
    print(f" VERDICT        {vd['label']}  - {vd.get('reason', '')}")
    print("-" * 62)
    s = r["suggestion"]
    if not s:
        print(" NO TRADE TODAY")
        print(" " + (r["no_trade_reason"] or ""))
    else:
        sz = s["sizing"]
        print(f" BULL PUT SPREAD  expiry {s['expiry']}  ({s['dte']} days)")
        print(f"   Sell {s['short_leg']['name']}  (delta {s['short_delta']:.2f})")
        print(f"   Buy  {s['long_leg']['name']}")
        print(f"   Credit (conservative)  ${s['credit_conservative']:.2f}/SOL   (mid ${s['credit_mid']:.2f})")
        print(f"   Max loss  ${s['max_loss']:.2f}/SOL   Break-even ${s['break_even']:.2f}")
        print(f"   Approx. chance of profit {s['prob_profit_approx']:.0%}   credit = {s['credit_pct_of_width']:.0f}% of width")
        g = s["greeks_per_sol"]
        print(f"   Greeks per SOL: delta {g['delta']:+.3f}  gamma {g['gamma']:+.4f}  "
              f"theta {g['theta']:+.4f}/day  vega {g['vega']:+.4f}/pt")
        print(f"   Size: {sz['contracts']} contract(s) x {s['contract_size']:g} SOL  -> "
              f"${sz['usd_at_risk']:,.2f} at risk, ${sz['credit_usd']:,.2f} credit")
        if sz["note"]:
            print("   " + sz["note"])
        print(f"   Liquidity: min open interest {s['liquidity']['min_oi']:g} SOL, "
              f"worst leg spread {s['liquidity']['worst_spread_pct']:.0f}% of mid")
        for a in r["alternates"]:
            print(f"   Alt: {a['expiry']}  {a['short_leg']['strike']:g}/{a['long_leg']['strike']:g}  "
                  f"credit ${a['credit_conservative']:.2f}  max loss ${a['max_loss']:.2f}")
        ep = s["exit_plan"]
        print(f"   EXIT PLAN: take profit when spread costs ${ep['take_profit_price']:.2f}; "
              f"stop if SOL <= ${ep['price_stop_spot']:,.2f} or short put mark >= ${ep['mark_stop_short_put']:.2f}; "
              f"close by {ep['time_exit_date']}")
        cv = s["conviction"]
        print(f"   CONVICTION: {cv['label']} ({cv['score']}/100) - {cv['action']}")
        for c in cv["caps"]:
            print("   " + c)
        print("   Settles in USDC: if you want to own more SOL, buy spot separately.")
    chk = r["checks"]["price_units"]
    if chk.get("ok") is False:
        print("-" * 62)
        print(" " + chk["note"])
    print("-" * 62)
    print(" Suggestion only - not financial advice. Check live quotes on Deribit before trading.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print machine-readable output")
    ap.add_argument("--sample", action="store_true", help="use saved sample data (offline)")
    ap.add_argument("--backfill", action="store_true", help="approximate past IV from trade history first")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = load_config()
    if args.sample:
        use_sample_paths()
    elif LOCAL_CONFIG.exists():
        use_private_paths()
    if args.backfill and not args.sample:
        print("Backfilling approximate IV history from Deribit trades (takes a few minutes)...")
        try:
            run_backfill(cfg, args.json)
        except Exception as e:
            print(f"Backfill stopped: {e}")
    try:
        snap = data_fetch.load_sample() if args.sample else data_fetch.fetch_snapshot(cfg)
    except Exception as e:
        msg = f"Could not fetch Deribit data: {e}"
        if not args.sample:
            mark_stale(msg)
        print(msg + "\nThe dashboard keeps the last good data and shows a stale-data warning.", file=sys.stderr)
        return 1
    try:
        out = analyse(cfg, snap)
        dash = save_outputs(cfg, snap, out, args.sample)
    except Exception as e:
        if not args.sample:
            mark_stale(f"Analysis failed: {e}")
        traceback.print_exc()
        return 2
    if args.json:
        print(json.dumps(dash["latest"], default=str, indent=2))
    else:
        print_summary(dash["latest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
