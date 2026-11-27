# SOL Vol Scanner: brief for Claude

Read this first, then `docs/ROADMAP.md` (what to build next), `docs/STRATEGIES.md` (the agreed strategy menu)
and `docs/DECISIONS.md` (why things are the way they are).

## What this is
A read-only tool that checks once a day whether Solana (SOL) options on Deribit are rich or cheap, and suggests
defined-risk option trades with a full exit plan. It runs daily in GitHub Actions (about 09:07 Singapore time) and
publishes a dashboard to GitHub Pages: https://workingondevelopmentmax.github.io/sol-vol-scanner/

## The owner and their goals
- Based in Singapore. Not a professional coder: explain changes in plain language, keep instructions click-by-click.
- Long-term SOL bull. The main goal is to **build up SOL over years**; option trades are secondary helpers.
- Wants to **act at highs and lows** instead of freezing (has historically held at tops and not bought dips).
- **Defined risk only:** spreads, never naked short options. Maximum loss is known before entry.
- Wants a small, separate budget for **big-swing** trades (e.g. cheap call spreads) to scratch the "win big" itch.
- Outputs must be **deterministic**: decisions come from code, data and versioned rule files, never from an LLM.

## Non-negotiables
1. **Read-only.** No code that authenticates to Deribit or places orders. Only `public/*` market-data methods.
   A test (`NoOrderCode`) enforces this. Order placement is a later, explicitly approved phase (testnet first).
2. **No LLM in the pipeline.** All dashboard text is fixed templates filled with numbers.
3. **One engine.** `engine.py` makes every trade decision for both the daily scan (`main.py`) and the backtest
   (`backtest.py`). Never add selection or exit logic anywhere else.
4. **Rules live in strategy files** (`strategies/*.toml`), versioned and fingerprinted. Changing behaviour =
   changing a strategy file, not hard-coding numbers. The same goes for what the backtest compares: only the
   file's `[[variants]]`, judged on its fixed `[evaluation]` windows. Never add a comparison in code.
5. **Tests must pass**, including the golden test (`tests/golden/sample_decision.json`). If a change is meant to
   alter the sample-day decision, update the golden file deliberately (`python tests/golden_tools.py --update`)
   and say so in the pull request. The same applies to the backtest golden file (`tests/golden/backtest_slice.json`).
6. **The repository is public.** No API keys, no real position sizes, no personal trade log. Real limits and the
   real premium log live only in `config.local.toml` / `my_premium_log.local.csv` on the owner's laptop
   (both git-ignored). The public copy uses reference sizing: $1,000 max loss per trade, $3,000 total.
7. **Work through pull requests.** One task per branch; explain what changed, test results, and any change to
   suggestions or backtest numbers in plain language in the PR description.

## How to report back to the owner
The owner reviews outcomes, not code. In every session and pull request:
- **Plan first.** Before editing, list in plain words what you will change and what the owner will notice
  (dashboard, suggestions, numbers). Wait for approval when in Plan mode.
- **Progress in plain words.** Short updates such as "tests written, now changing the engine", not code jargon.
- **Pull request description, always in this order:**
  1. **What changed:** 2-4 plain sentences, no code terms.
  2. **What you'll see:** dashboard or daily-output differences, or "nothing visible".
  3. **Numbers that moved:** any change to today's suggestion, golden files or backtest results, old vs new,
     and why. Say "none" if none.
  4. **How it was checked:** tests run and their result; anything that could not be checked.
  5. **What I need from you:** a decision, a setting, or "just review and merge".
- **Update the docs:** tick off the step in `docs/ROADMAP.md` and add a dated entry to `docs/DECISIONS.md`
  for anything decided or learned.

## Layout
| Path | Role |
|---|---|
| `main.py` | Daily entry point: fetch, analyse, write history/log/dashboard data. `--sample` runs offline, `--backfill` fills past IV |
| `engine.py` | The decision engine for every strategy type (`type` in the strategy file): `decide()`, `exit_plan()`, `synthetic_chain()`, `manage()`/`manage_debit()`, `open_position()`/`open_debit()` |
| `strategies/bull_put_spread.toml` | Put spread rules: entry, strikes, filters, exits, backtest pricing; `[evaluation]` windows and `[[variants]]` to compare |
| `strategies/direction.toml` | Direction rules (factors, view tilt, pass test set in advance); versioned and fingerprinted; display only for now |
| `direction.py` | Direction factors (no look-ahead), the pass test (`python direction.py` -> `data/direction_test.json`), today's reading, daily recording |
| `data/view_history.csv`, `data/direction_history.csv` | The owner's view and the direction reading, one row per UTC day (first run wins; public, so no private notes) |
| `strategies/debit_spreads.toml` | Debit spreads on cheap-IV days (call / put / both by direction), "big move" budget pool; pre-registered selection and pass rules |
| `paper.py`, `logs/paper_trades.csv` | Paper trades for strategies in testing: one open row, one close row per side, one no-entry row per day; append-only |
| `strategies/menu.toml` | The agreed strategy menu (docs/STRATEGIES.md) with status; display only, never changes a decision |
| `report.py` | The daily page's explanations (at a glance, evidence box, cautions, similar past days): numbers + templates, display only; `python report.py` refreshes them after the backtest |
| `strategy_menu.py` | Loads the menu and turns today's verdict and the scan's result into one status per strategy (templates) |
| `config.toml` | Shared settings: Deribit, rich/cheap gauge, reference risk limits, backfill, DVOL check, `[report]` display thresholds (evidence labels, cautions, similar days) |
| `data_fetch.py` | Read-only Deribit public API client (retries, history host for backfill) |
| `vol_gauge.py` | Option chain, ATM IV, realised vol, 12-month rich/cheap verdict, history file |
| `suggest.py` | Candidate search, filters and sizing (called through `engine.decide`) |
| `exits.py` | Exit plan: take profit, Greek-informed price stop, mark-price stop, time exit |
| `rationale.py`, `explain.py` | Conviction score, plain-language "why", Greeks panel (templates only) |
| `backtest.py` | Replays the strategy file's declared variants over stored files with the same engine; fixed scorecard, in-sample vs held back; writes `data/backtest.json` (with input fingerprints) |
| `chain_store.py` | Saves/loads one compressed SOL option chain per day; SHA-256 fingerprints of input files |
| `data/iv_history.csv` | One row per day since 2024-04: price, 30-day ATM IV, RV20, RV30, IV source |
| `data/chains/` | `YYYY-MM-DD.json.gz`: the first real SOL chain of each UTC day, never overwritten (from 2026-11) |
| `tests/golden/` | Golden files: sample-day decision, backtest on a frozen history slice (`backtest_history.csv`) |
| `lab.py` | Strategy Lab data: every version's scorecard, running result/drawdown series, entry-day groups, trades, benchmarks; writes `site/data/lab.json` + `lab.js` (display only) |
| `site/lab.html` | Strategy Lab page (full evidence for every strategy on the menu); shares `site/style.css` and `site/charts.js` with `index.html` |
| `site/` | Static dashboard (plain HTML/JS, no build step); data in `site/data/` (`latest.json`/`data.js` daily, `backtest.json`/`backtest.js` evidence, `lab.json`/`lab.js` Strategy Lab) |
| `.github/workflows/daily.yml` | Daily scan, commit of data, Pages deploy; "Run workflow" with optional backfill |

## How to run
```
python -m unittest discover tests      # all tests (must pass)
python main.py --sample                # offline run on stored sample data
python main.py                         # live run (needs internet access to deribit.com)
python backtest.py                     # backtest the default strategy file
python backtest.py --publish           # ...and publish it to site/data/ (the daily job does this)
python backtest.py strategies/debit_spreads.toml   # another strategy file -> data/backtest_debit_spreads.json
```
Python 3.11+ standard library only. No pip installs.

## Data notes
- Deribit lists SOL expiries only about 15 days out weekly, plus this and next month-end and the next two quarter-ends.
  Strategy files with `[backtest.calendar]` (debit spreads v2) model only those; files without it (the put spread,
  for now) still assume an expiry every Friday, which flatters their backtest (see DECISIONS 2026-11-21).
- Deribit publishes no SOL volatility index (the `get_volatility_index_data` currencies are BTC, ETH, USDC, USDT, EURR).
- The backtest uses a stored real chain (`data/chains/`) on days that have one, the model chain otherwise. It
  reads only committed files (never `config.local.toml`) and has no timestamps: same files, byte-identical output.
- Past IV (2024-05-31 to 2026-11) is estimated from public trades on `history.deribit.com`; daily readings since
  2026-11-15 come from the live chain (`iv_source = snapshot`).
- SOL options are USDC-settled (linear), European, contract size 10 SOL; prices are quoted per 1 SOL.
