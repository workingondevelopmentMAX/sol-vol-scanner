"""Change your risk limits without editing config.toml by hand.

  python set_limits.py            asks for each value (press Enter to keep the current one)

Limits can be dollars (250) or a percentage of your portfolio (2%) once you have entered
the portfolio value. Changes apply on the next scan.
"""
import re
import sys
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config.toml"


def set_value(text: str, key: str, value: float) -> str:
    pat = re.compile(rf"^({re.escape(key)}\s*=\s*)([^#\n]*?)(\s*(#.*)?)$", re.M)
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}{value:g}{m.group(3)}", text, count=1)
    return text.replace("[risk]\n", f"[risk]\n{key} = {value:g}\n", 1)


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
    text = CONFIG.read_text(encoding="utf-8")
    r = tomllib.loads(text)["risk"]
    print("Risk limits for SOL put spreads. Press Enter to keep a value.\n")
    pv = ask("Portfolio value in USD, used only for % limits (0 = skip)", float(r.get("portfolio_value_usd", 0)), None)
    per = ask("Max loss per trade (USD or %)", float(r["max_loss_per_trade_usd"]), pv)
    tot = ask("Max loss across ALL open spreads (USD or %)", float(r["max_total_spread_risk_usd"]), pv)
    if tot < per:
        print(f"  Note: total cap is below the per-trade limit, so trades will be sized to ${tot:g}.")
    opn = ask("Max loss of spreads you already hold (USD)", float(r["current_open_risk_usd"]), pv)
    for k, v in (("portfolio_value_usd", pv), ("max_loss_per_trade_usd", per),
                 ("max_total_spread_risk_usd", tot), ("current_open_risk_usd", opn)):
        text = set_value(text, k, v)
    tomllib.loads(text)  # refuse to save a broken file
    CONFIG.write_text(text, encoding="utf-8")
    print(f"\nSaved: ${per:,.2f} per trade, ${tot:,.2f} total, ${opn:,.2f} already open"
          + (f" (portfolio ${pv:,.0f}: {100 * per / pv:.1f}% per trade, {100 * tot / pv:.1f}% total)" if pv else "") + ".")
    print("Run run_scan.bat to resize today's idea.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
