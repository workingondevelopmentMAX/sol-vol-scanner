"""Change your risk limits privately, without touching the shared config.toml.

  python set_limits.py            asks for each value (press Enter to keep the current one)

Your answers are saved to config.local.toml, which stays on this computer: Git ignores it, so it is never
published to GitHub. The cloud copy keeps the public reference limits ($1,000 per trade, $3,000 total).
Limits can be dollars (250) or a percentage of your portfolio (2%) once you have entered the portfolio value.
"""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.toml"
LOCAL = ROOT / "config.local.toml"
KEYS = ("portfolio_value_usd", "max_loss_per_trade_usd", "max_total_spread_risk_usd", "current_open_risk_usd")


def ask(prompt: str, current: float, portfolio: float | None) -> float:
    while True:
        raw = input(f"{prompt} [now {current:g}]: ").strip().replace("$", "").replace(",", "")
        if not raw:
            return current
        try:
            if raw.endswith("%"):
                if not portfolio:
                    print("  Enter a portfolio value first to use percentages.")
                    continue
                return round(portfolio * float(raw[:-1]) / 100, 2)
            v = float(raw)
            if v < 0:
                raise ValueError
            return v
        except ValueError:
            print("  Please enter a number like 250 or 2%.")


def main() -> int:
    shared = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["risk"]
    local = tomllib.loads(LOCAL.read_text(encoding="utf-8")) if LOCAL.exists() else {}
    r = {**shared, **local.get("risk", {})}
    print("Your private risk limits (saved to config.local.toml, never published). Press Enter to keep a value.\n")
    pv = ask("Portfolio value in USD, used only for % limits (0 = skip)", float(r.get("portfolio_value_usd", 0)), None)
    per = ask("Max loss per trade (USD or %)", float(r["max_loss_per_trade_usd"]), pv)
    tot = ask("Max loss across ALL open spreads (USD or %)", float(r["max_total_spread_risk_usd"]), pv)
    if tot < per:
        print(f"  Note: total cap is below the per-trade limit, so trades will be sized to ${tot:g}.")
    opn = ask("Max loss of spreads you already hold (USD)", float(r["current_open_risk_usd"]), pv)
    lines = ["# Private settings for THIS computer only. Git ignores this file; it is never published.",
             "# Any [section] key here replaces the same key in config.toml.", "", "[risk]",
             "reference_sizing = false   # show your real sizes, not the public $1,000 reference",
             f"portfolio_value_usd = {pv:g}", f"max_loss_per_trade_usd = {per:g}",
             f"max_total_spread_risk_usd = {tot:g}", f"current_open_risk_usd = {opn:g}", ""]
    # keep any other sections the user added by hand
    for sec, vals in local.items():
        if sec == "risk":
            continue
        lines.append(f"[{sec}]")
        lines += [f"{k} = {v!r}".replace("'", '"') if isinstance(v, str) else f"{k} = {str(v).lower() if isinstance(v, bool) else v}"
                  for k, v in vals.items()]
        lines.append("")
    text = "\n".join(lines)
    tomllib.loads(text)  # refuse to save a broken file
    LOCAL.write_text(text, encoding="utf-8")
    print(f"\nSaved privately: ${per:,.2f} per trade, ${tot:,.2f} total, ${opn:,.2f} already open"
          + (f" (portfolio ${pv:,.0f}: {100 * per / pv:.1f}% per trade, {100 * tot / pv:.1f}% total)" if pv else "") + ".")
    print("Run run_scan.bat to size today's idea with your own limits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
