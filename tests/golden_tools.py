"""Shared helper: run the full daily analysis on the stored sample snapshot with a frozen history copy."""
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


if __name__ == "__main__":   # python tests/golden_tools.py --update   (only after a deliberate rule change)
    if "--update" in sys.argv:
        GOLDEN.write_text(json.dumps(sample_decision(), indent=1, sort_keys=True), encoding="utf-8")
        print("golden file updated")
    else:
        print(json.dumps(sample_decision(), indent=1, sort_keys=True))
