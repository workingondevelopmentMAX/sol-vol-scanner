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
1. ~~Direction signal module + `view.toml`, and record the view in force on every daily run.~~ **Done 2026-11-20:**
   `strategies/direction.toml` + `direction.py`; trend, momentum, drawdown and crash/fear tested with a pass rule set in
   advance: **none passed** (crash/fear came closest: big gains after fear days, but only 2 unseen-period episodes);
   skew and term structure recording only. View and reading recorded daily. Display only.
2. ~~Call and put debit spreads on cheap-IV days.~~ **Built 2026-11-20, in testing:** `strategies/debit_spreads.toml`
   on the shared engine (pre-registered). v1 passed only because its model assumed an expiry every Friday; v2
   (2026-11-21) uses Deribit's real SOL expiry calendar: "as written" kept (5-trade fallback), unseen 7 trades,
   +$1,199 per $1,000 -> **not passed** (needs 8 trades), and the owner holds it in testing anyway (`go_live = false`).
   2026-11-27 calendar fix (month-end on the day itself): unseen 8 trades, +$1,225 -> passes at the bare minimum;
   still in testing (`go_live = false`), see DECISIONS 2026-11-27.
   Paper trades recorded daily from real quotes.
3. Bear call spread and iron condor on rich-IV days.
4. Zero-cost protective hedge.
5. Accumulation helper, benchmarked against plain weekly buying.
- **Done when:** each is a strategy file on the shared engine, judged by the step 3 scorecard on held-back data,
  and shown on the dashboard only after passing.

## Decisions waiting on the owner
- **Put spread on the real expiry calendar (urgent).** Re-pricing the live put spread with only the expiries Deribit
  lists turns its results from +$316 | +$111 (practice | unseen) into -$1,003 | -$349 (14 trades each). Not changed
  yet: it needs its own pull request (new version, golden files move) and the owner's decision.
