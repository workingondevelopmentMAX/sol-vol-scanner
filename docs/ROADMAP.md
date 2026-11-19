# Roadmap

Goal: a deterministic strategy engine with honest, pre-registered testing and clear outputs.
Full rationale: the "Deterministic Strategy Engine Plan" doc. Work through one step per pull request.

## Done
- **Step 1: one engine + strategy file** (2026-11-19). `engine.py` shared by `main.py` and `backtest.py`;
  rules in `strategies/bull_put_spread.toml`; results stamped with name, version, fingerprint; golden test.
- **Private local settings** (2026-11-19). `config.local.toml` and `my_premium_log.local.csv` on the laptop
  only; laptop runs write to `private/` and `site/data/private/`, never to shared files.
- **Step 2: fixed, recorded data** (2026-11-19). Daily scan keeps the day's SOL chain in
  `data/chains/YYYY-MM-DD.json.gz` (first snapshot of the UTC day, never overwritten, committed by the workflow).
  The backtest uses a stored chain instead of `synthetic_chain()` where one exists (entries and exits), reads only
  committed files, lists the SHA-256 of each in `data/backtest.json` plus a `data_fingerprint`, and re-runs
  byte-identically. Golden test on a frozen 2024-04 to 2025-12 history slice.
- **Step 3: testing rules set in advance** (2026-11-19). Variants live in the strategy file's `[[variants]]` list
  and windows in `[evaluation]` (in-sample 2024-06-01 to 2025-06-30, held back 2025-07-01 onwards). Fixed
  scorecard per window: trades, win rate, average/total/worst per $1,000 max loss, longest losing streak, max
  drawdown, results by condition (verdict x SOL up/down over the trade), versus holding SOL and versus buying
  SOL weekly (dollar-cost averaging). The report shows both
  windows side by side for every declared variant; the backtest code has no variants of its own.
- **Step 4a: publish evidence and strategy menu** (2026-11-19). `strategies/menu.toml` lists every strategy in
  `docs/STRATEGIES.md` (display only); the daily output has a `strategies` list with today's status for each
  (suggested / no trade / not eligible / not built). The backtest runs daily after the scan and is published to
  `site/data/backtest.json` (+ `backtest.js`), with a check that its rules fingerprint matches today's, the two
  period labels, a fixed evidence label per variant (`config.toml` `[report]`, thin below 20 held-back trades),
  and each trade's entry-day conditions (30-day move, % below 30-day high, IV percentile, verdict).
- **Step 4b: daily page shows the evidence** (2026-11-19). Today at a glance (every menu strategy and its status),
  idea cards with an evidence box (practice vs unseen period), for and against today (rule-based cautions), and
  results on past days with similar entry-day conditions; all worked out in `report.py`, display only.
- **Step 4c: Strategy Lab page** (2026-11-19). `site/lab.html` (data from `lab.py` in `site/data/lab.json`): every
  version of every built strategy in strategy-file order, practice vs unseen side by side, running result and
  drawdown charts with holding/weekly-buying benchmarks, results by entry-day conditions, and a trade list with a
  price chart per trade; not-built strategies listed with the scorecard they will be judged on.
- **Step 4 done** (4a + 4b + 4c): the dashboard shows the evidence behind every idea and every strategy.

## Next

### Step 5 (next): more strategies as files (see docs/STRATEGIES.md)
Agreed menu (2026-11-19): rich IV = bull put spread (exists), bear call spread, iron condor, zero-cost protective
hedge for core SOL near highs; cheap IV = call or put debit spreads (and a no-direction big-move option); always =
accumulation helper. Direction = tested data signal (trend, momentum/drawdown, options-market signals, crash/fear)
tilted by `view.toml`. Build order:
1. Direction signal module + `view.toml`, and record the view in force on every daily run.
2. Call and put debit spreads on cheap-IV days.
3. Bear call spread and iron condor on rich-IV days.
4. Zero-cost protective hedge.
5. Accumulation helper, benchmarked against plain weekly buying.
- **Done when:** each is a strategy file on the shared engine, judged by the step 3 scorecard on held-back data,
  and shown on the dashboard only after passing.

## Decisions waiting on the owner
- Exit rules for the put spread: the current 1.5x stop lost money in the backtest; 2x / 3x / no stop did better
  on 20-25 trades. Step 3's held-back test is now available (see DECISIONS 2026-11-19, "Testing rules set in
  advance"): only 10-13 held-back trades per variant, so treat it as a hint, not proof. The owner decides.
