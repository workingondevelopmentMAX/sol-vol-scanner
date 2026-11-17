# SOL Vol Scanner (v0, read-only)

> **Dashboard:** https://workingondevelopmentmax.github.io/sol-vol-scanner/ (updated daily by GitHub Actions at about 9:07am Singapore time)
>
> **Running it in the cloud:**
> - The scan runs every day in GitHub's cloud, so your laptop can be off.
> - To run it now, go to **Actions → Daily SOL vol scan → Run workflow**. Tick "backfill" to fill in about 90 days of past implied vol.
> - Settings (`config.toml`) and your premium log (`my_premium_log.csv`) can be edited on github.com: open the file and click the pencil icon. Saving re-runs the scan.
> - This repository is **public**: anyone with the link can see the code, the settings (including your loss limits) and the dashboard.

Checks once a day whether Solana options on Deribit look **rich or cheap**, and when they are rich,
suggests one defined-risk **bull put spread**. It only reads public market data:
**it has no API keys and no code that can place orders.** Suggestions are for your own decision,
not financial advice.

## One-time setup on Windows

1. **Install Python 3.11 or newer** from <https://www.python.org/downloads/>.
   On the first install screen, tick **"Add python.exe to PATH"**. Nothing else needs installing,
   because the scanner only uses Python's built-in libraries.
2. **Set your limits** in `config.toml` (open it with Notepad):
   `max_loss_per_trade_usd`, `max_total_spread_risk_usd`, and `current_open_risk_usd`.
3. **Test offline:** double-click `run_tests.bat`. It runs the maths tests and a run on
   *synthetic* sample data. Sample runs never touch your real history.
4. **First real run:** double-click `run_scan.bat`. It prints the summary and opens the dashboard.
   - Optional, once: open a Command Prompt in this folder and run `run_scan.bat --backfill`.
     This approximates the last 90 days of implied vol from Deribit's public trade history, so the
     chart has a history from day one. It takes a few minutes. The values are approximate, and
     the chart's hover text marks them "backfill".
   - If the backfill finds little, you can bring in a daily SOL implied-vol series from elsewhere:
     drag the CSV file onto `import_iv.bat`. The CSV needs a `date` column and an `atm_iv` column
     (30-day at-the-money IV, as 65.2 or 0.652).
5. **Make it daily:** double-click `install_daily_task.bat`. It runs at 9:00 am every day, or as
   soon as the laptop is next on if it was off or asleep. To remove it, run `remove_daily_task.bat`.

## Changing your risk limits

Double-click `set_limits.bat`. It asks for:
- the most you'll lose on one trade
- the most across all open spreads
- the max loss of spreads you already hold

Press Enter to keep any value. Once you've entered a portfolio value, you can type limits as
percentages, e.g. `2%`. To preview other limits without saving them, use "Try different loss limits"
under today's idea on the dashboard.

## Exit plan

Every idea comes with a plan to set the moment you enter:
- **Take profit:** a resting limit order to buy the spread back once you've kept 50% of the credit.
- **Price stop:** triggers on the SOL index at the level where the trade has lost 1.5x the credit.
  The level allows for implied vol rising 1 point per 1% drop.
- **Backup stop:** triggers on the short put's own mark price.
- **Time exit:** close 7 days before expiry.

Both stops buy back only the short put. Change the settings in `config.toml` under `[exits]`.

## Tracking SOL bought with premium

Add each trade you actually take to `my_premium_log.csv` (it opens in Excel) with these columns:
`date, spread, credit_usd, sol_price_when_bought`. The dashboard's track record shows the total
premium collected and the SOL it bought.

## Looking at it

- **On the laptop:** open `site\index.html`. Bookmark it.
- **On your phone, at home:** double-click `serve_for_phone.bat` and keep its window open. It
  shows an address like `http://192.168.1.23:8765/`. Open that on your phone (same Wi-Fi), then use
  Share > **Add to Home Screen**. If Windows asks about the firewall, allow **Private networks** only.
  The phone can only reach the dashboard while the laptop is on and that window is open.

## What it does each day

1. Pulls SOL option prices, IVs and open interest, the SOL index, and about 400 days of daily prices
   (Deribit public API).
2. Works out the 30-day at-the-money IV, plus 20- and 30-day realised vol.
3. Verdict: with 60+ days of IV history, RICH when IV is at or above the 50th percentile and CHEAP
   at or below the 20th. Before that, it compares IV with realised vol (RICH at 1.2x or more).
4. If RICH, it looks for a put spread that meets these rules:
   - 14 to 42 days to expiry
   - a short put with delta near -0.20
   - a long put about 10% lower
   - a conservative credit (short bid minus long ask) of at least 12% of the gap between strikes
   - tight bid-ask spreads on both legs
5. It sizes the trade so the maximum loss stays within your limits, explains the Greeks in dollars,
   and logs everything.

## Files

| File | What it is |
|---|---|
| `config.toml` | All thresholds and your risk limits |
| `main.py` | Entry point (`--sample`, `--json`, `--backfill`) |
| `data_fetch.py` | Read-only Deribit calls (public methods only) |
| `vol_gauge.py` | Chain building, ATM IV, realised vol, verdict, history |
| `suggest.py` | Put spread search, filters, sizing |
| `explain.py` | Plain-language Greeks and "what if" table |
| `data\iv_history.csv` | One row per day: price, ATM IV, RV20, RV30 |
| `logs\suggestions.csv` | Every run, including outcomes once trades expire |
| `logs\last_run.txt` | Output of the last scheduled run (check this if something looks wrong) |
| `site\` | The dashboard |

## Things to check on the first real run

- **Price units.** The scanner compares Deribit's mark prices with its own model. If the summary
  shows a "price units" WARNING, the dollar figures may be off by the contract multiplier.
  Tell Claude what it says.
- **Contract size** is read from Deribit for every run. Check that the "Size" line on the dashboard
  makes sense against Deribit's SOL option contract specs.
- **Open interest filter.** `min_open_interest` starts at 0. After a week of looking at real data,
  set a sensible floor.
- **The 12% credit rule** is deliberately modest. A put spread with a short put near -0.20 delta
  usually pays about 10-17% of its width. Raise the rule if you only want the richest setups.
