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

## Next

### Step 3: testing rules set in advance
- Variants to compare are declared in the strategy file (e.g. a `[[variants]]` list), not in code.
- Split history: rules chosen on 2024-06 to 2025-06 ("in-sample"), judged on 2025-07 onwards ("held back").
- Fixed scorecard for every report: trades, win rate, average and total per $1,000 max loss, worst trade, max
  losing streak, max drawdown, results by condition (rich/cheap x SOL up/down over the trade), versus holding SOL.
- **Done when:** the report shows in-sample and held-back columns side by side for every declared variant,
  and nothing can be added to a comparison without changing a committed file.

### Step 4: Strategy Lab page
- New dashboard page generated nightly from `data/backtest.json`: running P&L and drawdown chart, scorecard
  table (in-sample vs held back), results by market condition, trade list linked to each day's chart.
- Header: data range, strategy name/version/fingerprint, data fingerprint. Template text only.
- **Done when:** the page renders on phone, iPad and desktop and every number matches `data/backtest.json`.

### Step 5: more strategies as files
- `strategies/call_spread_cheap_vol.toml`: buy call spreads on CHEAP days, small fixed budget (the "big swing").
- `strategies/accumulation_ladder.toml`: rules for weekly SOL buys plus extra buys on crash/fear days
  (IV jump of 15+ points, SOL down 15%+ in a week).
- Each judged by the same scorecard against simply holding / dollar-cost averaging SOL.

## Decisions waiting on the owner
- Exit rules for the put spread: the current 1.5x stop lost money in the backtest; 2x / 3x / no stop did better
  on 20-25 trades. Decide after step 3's held-back test, not before.
