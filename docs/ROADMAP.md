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
  drawdown, results by condition (verdict x SOL up/down over the trade), versus holding SOL. The report shows both
  windows side by side for every declared variant; the backtest code has no variants of its own.

## Next

### Step 4: Strategy Lab page
- New dashboard page generated nightly from `data/backtest.json`: running P&L and drawdown chart, scorecard
  table (in-sample vs held back), results by market condition, trade list linked to each day's chart.
- Header: data range, strategy name/version/fingerprint, data fingerprint. Template text only.
- **Done when:** the page renders on phone, iPad and desktop and every number matches `data/backtest.json`.

### Step 5: more strategies as files (see docs/STRATEGIES.md)
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
