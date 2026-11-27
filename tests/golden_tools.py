"""Shared helpers for the golden tests: the sample-day decision and a backtest on a frozen slice of history."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main  # noqa: E402
import vol_gauge  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "sample_decision.json"
BACKTEST_GOLDEN = ROOT / "tests" / "golden" / "backtest_slice.json"
BACKTEST_HISTORY = ROOT / "tests" / "golden" / "backtest_history.csv"   # frozen copy, 2024-04-21 to 2025-12-31
NO_CHAINS = ROOT / "tests" / "golden" / "no_chains"                       # empty: the slice is model-priced


def sample_decision() -> dict:
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "data" / "sample" / "iv_history_seed.csv", tmp / "h.csv")
    old, vol_gauge.HISTORY_CSV = vol_gauge.HISTORY_CSV, tmp / "h.csv"
    try:
        snap = json.loads((ROOT / "data" / "sample" / "snapshot.json").read_text(encoding="utf-8"))
        r = main.analyse(main.load_config(), snap)["result"]
    finally:
        vol_gauge.HISTORY_CSV = old
    s = r["suggestion"]
    out = {"verdict": r["verdict"]["label"], "no_trade": r["no_trade_reason"]}
    if s:
        out.update(short=s["short_leg"]["name"], long=s["long_leg"]["name"],
                   credit=round(s["credit_conservative"], 6), max_loss=round(s["max_loss"], 6),
                   contracts=s["sizing"]["contracts"], take_profit=s["exit_plan"]["take_profit_price"],
                   price_stop=round(s["exit_plan"]["price_stop_spot"], 4), conviction=s["conviction"]["score"],
                   alternates=[a["short_leg"]["name"] + " / " + a["long_leg"]["name"] for a in r["alternates"]])
    return out


def backtest_slice() -> dict:
    """The strategy file's own rules, rich days, one trade at a time, on the frozen history slice, with its scorecard."""
    import backtest
    cfg = main.load_config(local=False)
    st = cfg["_strategy"]
    days = backtest.load_days(cfg, BACKTEST_HISTORY)
    trades = backtest.run(cfg, st, days, True, NO_CHAINS)
    win = backtest.windows(st, days)
    sc = {k: backtest.scorecard(trades, days, *win[k]) for k in ("in_sample", "held_back")}
    return {"period": [days[0]["date"], days[-1]["date"]], "strategy_version": st["_stamp"]["version"],
            "summary": backtest.summarise(trades), "windows": win, "window_labels": backtest.window_labels(st, win),
            "scorecard": sc, "evidence": backtest.evidence(sc, cfg["report"]), "trades": trades}


DEBIT_GOLDEN = ROOT / "tests" / "golden" / "debit_sample_decision.json"
DEBIT_BACKTEST_GOLDEN = ROOT / "tests" / "golden" / "debit_backtest_slice.json"
DEBIT_STRATEGY = ROOT / "strategies" / "debit_spreads.toml"


def debit_sample_decision() -> dict:
    """Debit spreads as if IV were CHEAP, for each direction reading, on (1) a fixed model chain (2025-05-01, SOL $150,
    ATM IV 60%) and (2) the stored real chain of 2026-11-19 (Deribit then listed no SOL expiry 45-75 days out)."""
    import chain_store
    import engine
    st = engine.load_strategy(DEBIT_STRATEGY)
    cfg = main.load_config(local=False)
    out = {}
    for label, chain in (("model_2025-05-01", engine.synthetic_chain(st, "2025-05-01", 150.0, 0.60)),
                         ("real_2026-11-19", vol_gauge.build_chain(chain_store.load("2026-11-19")))):
        out[label] = _debit_cases(cfg, st, chain)
    return out


def _debit_cases(cfg, st, chain) -> dict:
    import engine
    out = {}
    for reading in ("bullish", "neutral", "bearish"):
        dec = engine.decide(cfg, st, {"label": "CHEAP"}, chain, reading)
        t = dec["trade"]
        if not t:
            out[reading] = {"reason": dec["reason"]}
            continue
        sz = engine.size_debit(cfg, st, t)
        plan = engine.exit_plan_debit(st, t)
        out[reading] = {"sides": [{"kind": s["kind"], "long": s["long"]["name"],
                                   "short": s["short"]["name"] if s["short"] else None,
                                   "cost": round(s["cost"], 6), "share": s["share"], "contracts": z["contracts"],
                                   "take_profit_value": round(p["take_profit_value"], 6), "time_exit": p["time_exit_date"]}
                                  for s, z, p in zip(t["sides"], sz["sides"], plan["sides"])],
                        "cost_usd": round(sz["cost_usd"], 2)}
    return out


def debit_backtest_slice() -> dict:
    """The debit file as written on the frozen history slice (no stored chains), with its scorecard."""
    import backtest
    import engine
    cfg = main.load_config(DEBIT_STRATEGY, local=False)
    st = cfg["_strategy"]
    days = backtest.load_days(cfg, BACKTEST_HISTORY)
    trades = backtest.run(cfg, st, days, True, NO_CHAINS)
    win = backtest.windows(st, days)
    sc = {k: backtest.scorecard(trades, days, *win[k]) for k in ("in_sample", "held_back")}
    return {"period": [days[0]["date"], days[-1]["date"]], "strategy_version": st["_stamp"]["version"],
            "summary": backtest.summarise(trades), "windows": win, "scorecard": sc, "trades": trades}


def dump(x: dict) -> str:
    return json.dumps(x, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":   # python tests/golden_tools.py --update   (only after a deliberate rule change)
    if "--update" in sys.argv:
        GOLDEN.write_text(json.dumps(sample_decision(), indent=1, sort_keys=True), encoding="utf-8")
        BACKTEST_GOLDEN.write_text(dump(backtest_slice()), encoding="utf-8")
        DEBIT_GOLDEN.write_text(dump(debit_sample_decision()), encoding="utf-8")
        DEBIT_BACKTEST_GOLDEN.write_text(dump(debit_backtest_slice()), encoding="utf-8")
        print("golden files updated")
    else:
        print(json.dumps(sample_decision(), indent=1, sort_keys=True))
        print(json.dumps(backtest_slice()["summary"], indent=1, sort_keys=True))
