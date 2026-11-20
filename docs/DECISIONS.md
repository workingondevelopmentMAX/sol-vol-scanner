# Decision log

Newest first. Each entry: what was decided, and why. Numbers are from the analysis at the time.

### 2026-11-20 · Direction signal built and tested (step 5.1)
Factors trend, momentum, drawdown and crash/fear were tested against SOL's next-30-day move with a pass rule
set in advance (bullish minus bearish at least +5 points in both periods, at least 4 non-overlapping 30-day
periods per state). Result: none passed. Trend: -21.1 points in practice (SOL above both averages was followed by
-17% on average; only 3 bearish periods), +0.6 unseen. Momentum: -11.0 | -0.6 (backwards in practice).
Drawdown (deep dip = bullish, owner's choice): +16.5 in practice but -1.9 unseen. Crash/fear (fear days vs all
other days, owner's choice): +10.9 | +18.7 points, but only 6 fear days in each period, i.e. 4 and 2
non-overlapping fear periods, so it fails the 4-period rule in the unseen period; the most promising factor, to
watch as fear days accumulate (rules unchanged: no tuning after results). Baseline (always neutral): average
next-30-day move +2.6% practice, -2.1% unseen. Skew and term structure are recorded daily
from stored chains but cannot be tested yet. The owner's view (view.toml) is now recorded every day in
data/view_history.csv, and tilts the reading by 0.15 per strength point (reading bullish at +0.25 or more, bearish
at -0.25 or less, so a strength-1 view alone stays neutral). No live trade uses direction yet.
Caveat: the unseen period's SOL path was already known, so this is a weak test; forward recording is the
real one. The rules file was committed on its own before any result was run.

### 2026-11-20 · Put spread stop moves to 2x the credit (strategy version 2)
The owner switched the stop from a loss of 1.5x the credit to 2x (buy-back at 3x the credit instead of
2.5x). Why: 2x was the only declared version that made money in both periods (practice +$316 over 11 trades,
unseen +$111 over 11, per $1,000 risked per trade); 1.5x lost -$1,268 in practice. Trade-off: fewer
stop-outs on temporary dips, but a bigger loss each time the stop fires. Caveats: small samples, all model
prices (no real stored quotes yet), and the unseen period has now been used to choose, so the fair test of
this rule is new days from 2026-11-21 onwards. The old rule stays on the Lab page as
'Stop 1.5x (rule until 2026-11-20)'.
Effect on the exit plan (same trade picked, entry and strikes unchanged): on 19 Nov's stored prices
(25 Dec 66/60 spread, credit $0.98) the SOL price stop moves from $66.18 to $63.13, the loss if the stop fires
from $279 to $372, and the win rate needed to break even from 75% to 80%. Rules fingerprint 26b253abbac9 ->
56febf4a1a57. Golden files regenerated: sample-day price stop 119.4379 -> 115.642; frozen-slice backtest
18 trades / -$1,147 -> 15 trades / +$437 (practice 14 / -$1,268 -> 11 / +$316; unseen part unchanged at
4 / +$121).

### 2026-11-19 · Strategy Lab page
A second page, lab.html, shows the full backtest for every strategy on the menu: every declared version
in the order written in its strategy file (never ranked by results), practice and unseen periods side by
side, running result and drawdown charts with benchmarks (holding SOL, buying weekly), results by
entry-day conditions, and a trade list with a price chart per trade. Not-built strategies are listed with
the scorecard they will be judged on. Why: one place to judge rules consistently without picking
winners in hindsight. Display only: suggestions, rules and backtest results are unchanged.
Today: 9 versions of the bull put spread; only "Stop 2x" made money in both periods (+$316 practice, +$111
unseen, per $1,000 risked per trade); 0 of 25 live-rule trades (0 of 499 across all versions) used real
stored quotes, the rest model prices. Each period's running result starts again from $0 (owner's choice), so each
line ends at that period's total. Not built: a link from each trade to the daily page's IV chart at that date
(the daily chart cannot open at a chosen date); the trade's own price chart covers it.

### 2026-11-19 · Daily page: at a glance, evidence, for and against, similar past days
The daily page now lists every strategy on the menu with today's status, and each suggested trade shows its
rule's practice-period and unseen-period results side by side, an evidence label, rule-based cautions, and
results on past days with similar entry conditions. Settings confirmed by the owner on 2026-11-19
(config.toml [report]): caution when SOL moved more than 15% in 30 days; a past day is similar if its 30-day
SOL move was within ±10 points of today's and it had the same IV verdict; fewer than 5 similar days =
'too few to tell'; fewer than 20 held-back trades = 'thin evidence'. Display only: suggestions, sizes,
exits and conviction are unchanged. Today's example: the 25 Dec 66/60 bull put spread (IV RICH, 63rd percentile,
conviction Medium 54/100) shows "Thin evidence, mixed results" (practice period -$1,268 over 14 trades, unseen
period +$288 over 11) with four cautions (SOL -30% in 30 days, cushion 0.7x a typical move, thin evidence, lost in
the practice period), and 6 similar past days (RICH, 30-day move between -40% and -20%): 4 won, 2 lost, -$192 in
total per $1,000 risked. "SOL's 30-day move" is measured the same way for today and for past entry days (index
price vs 30 calendar days earlier, from data/iv_history.csv). The daily job refreshes these sections after the
backtest (`python report.py`) so the evidence and the "older rules" check are current.

### 2026-11-19 · "Thin evidence" threshold confirmed at 20 held-back trades
The owner confirmed `held_back_thin_below = 20` (`config.toml` `[report]`): a rule with fewer than 20 trades in the
unseen period (held back) is labelled "thin evidence". Today every bull put spread variant is thin (10-13
held-back trades). Display only: no trading rule changes.

### 2026-11-19 · Dashboard shows the evidence behind each idea, for every strategy on the menu
The site will list every strategy in docs/STRATEGIES.md with today's status (suggested, no trade, wrong
market conditions, or not built yet), and show each suggested rule's results in the practice period
(in-sample) and the unseen period (held back), a fixed evidence label, and results on past days with similar
entry conditions. 'Similar' means conditions on the entry day, never what happened afterwards. Why: the page
showed a conviction score but not the track record. The live rule lost $1,268 per $1,000 in-sample (14 trades)
and made +$288 held back (11 trades), and the page never said so. Display only: no trading rules change.
The 'thin evidence' threshold (20 held-back trades) was a starting value; confirmed the same day (entry above).
"IV fair: no options trade" counts as live (the scanner already suggests nothing on FAIR days). If the daily
backtest fails, the scan and dashboard still publish and the last evidence stays up.

### 2026-11-19 · Testing rules set in advance (step 3)
What the backtest compares now lives in the strategy file: `[[variants]]` (9 entries: the 7 exit-rule variants,
"no rich filter" and "overlapping") and `[evaluation]` windows, in-sample 2024-06-01 to 2025-06-30 (the period the
rules were chosen on) and held back 2025-07-01 onwards. Each variant runs once over all of history; a trade counts
in the window of its entry day (one no-filter trade on 2024-05-31 is before both windows). The 12 other
combinations the old code printed (every exit variant without the rich filter, and overlapping) were dropped;
any can come back as one `[[variants]]` entry. Trades are identical to before: only the reporting changed.
First results, per $1,000 max loss per trade (in-sample | held back):
as written -$1,268 over 14 trades | +$288 over 11; stop 2x +$316/11 | +$111/11; stop 3x or no stop with no time
exit -$675/10 | +$1,278/10 (all 10 won); no stop, close 7 days out -$200/11 | +$738/10; no rich filter
-$1,849/23 | -$2,980/32. Holding $1,000 of SOL: -$90 | -$495; buying it weekly instead: -$66 | -$300. The held-back period was good for selling puts on
rich days but small (10-13 trades). Only stop 2x made money in both windows, modestly; the no-stop variants did
best held back but lost in-sample. Strategy version stays 1:
the trading rules did not change (the fingerprint did, because the file did).

### 2026-11-19 · Strategy menu agreed (see docs/STRATEGIES.md)
Rich IV: bull put spreads, bear call spreads, iron condors, and a zero-cost hedge for core SOL near highs. Cheap IV:
buy call or put debit spreads (or a no-direction big-move trade). Plus an accumulation helper. Direction combines a
data signal (factors: trend, momentum/drawdown, options-market signals, crash/fear; kept only if they help on
held-back data) with the owner's view in `view.toml` (direction, strength 1-3, expiry). Past views cannot be
backtested, so the view is recorded daily from now on. Big-move budget confirmed by the owner: at most 1% of
portfolio per month. Left to testing: debit spreads vs outright options, and 30/60/90-day horizons.

### 2026-11-19 · Fixed, recorded data (step 2)
Each daily scan stores the SOL option chain in `data/chains/YYYY-MM-DD.json.gz`: SOL options only, only the fields
the engine reads, about 30 KB a day. The first snapshot of each UTC day is kept and never overwritten, so a stored
day (and its fingerprint) never changes; later runs the same day (e.g. after a push) leave it alone. Files are
written reproducibly (sorted keys, gzip without a timestamp). On days with a stored chain the backtest picks the
trade from real quotes and values open trades from real quotes (short put's offer, long put's bid, short put's
mark for the stop); if a leg is missing that day it falls back to the model. The backtest ignores
`config.local.toml` so a laptop and the cloud always get the same result, and lists the SHA-256 of every input
file. At the time of this change no chain was stored yet, so every backtest number stayed exactly the same.

### 2026-11-19 · Private settings stay on the laptop
`config.local.toml` and `my_premium_log.local.csv` are git-ignored. When `config.local.toml` exists, a laptop run
starts from the latest shared history, writes to `private/` and `site/data/private/`, and the dashboard shows a
"Private view". Why: the repository is public, and real sizes and trades should never be published.

### 2026-11-19 · One engine, rules in strategy files (step 1)
Live scan and backtest both call `engine.decide()` / `engine.manage()`. Verified: on a stored live snapshot and
the sample day, suggestions, sizes, exit levels and conviction were identical before and after. The backtest's
numbers changed because it now picks exactly the trade the live scanner would (before, it used its own simpler
choice), which showed how fragile 20-25 trade samples are.

### 2026-11-18 · Backtest of the put spread exit rules
Rich days only, one trade at a time, June 2024 to Nov 2026, per $1,000 max loss (engine-based, 2026-11-19 run):
current rules (take profit 50%, 1.5x stop, close 7 days out) 25 trades, 72% wins, average -$39, total -$980;
2x stop 82%, +$19, +$426; 3x stop or no stop with no time exit 90%, +$30, +$604. Every variant lost money without
the rich filter. Conclusions: keep the rich filter; the 1.5x stop turns temporary dips into losses (SOL often
recovered); results are small-sample and model-priced, so exit rules are not changed until held-back testing.
SOL fell from about $154 to $72 over the period, a hard test for bull put spreads.

### 2026-11-18 · Realistic expectations
The best variant made about $49 per $1,000 risked per trade (roughly 10 trades a year): an optimistic ceiling
chosen with hindsight. Put spreads are a small, optional helper; the tool's main value is discipline (saying
no on most days). The owner's main goal (SOL accumulation) is better served by automatic buying rules.

### 2026-11-18 · Vol jump assumption stays at 1 point per 1% drop
Measured from history: SOL drops of 0-15% over 5 days moved 30-day IV by roughly -3 to +4 points (flat);
drops of more than 15% lifted IV by +15 to +17 points. So 1 point per 1% is right for crashes and cautious for
ordinary dips. Using 0.3 instead barely changed backtest results.

### 2026-11-17 · Rich/cheap uses the last 12 calendar months
Earlier code used the last 365 readings, which, with a gap in the data, compared today with expensive 2024
prices and wrongly showed FAIR (39th percentile). Now the window is calendar-based, for today and for every day's
chart shading. Minimum 60 days of IV in the window before percentiles are used (otherwise IV / realised vol).

### 2026-11-17 · History backfilled from public trades
`history.deribit.com` keeps every trade; each option trade carries its IV. Daily 30-day ATM IV is estimated as the
weighted median of near-the-money SOL trades with 7-60 days to expiry. Result: every day from 2024-05-31 filled
(901 days). The main API only keeps recent trades, which is why the first attempt found almost nothing.

### 2026-11-17 · No SOL DVOL
Deribit's volatility index endpoint accepts only BTC, ETH, USDC, USDT, EURR. The scan still asks once a day
(harmless) and would merge SOL DVOL automatically, scaled to ATM IV, if it ever appears.

### 2026-11-17 · Public repository, reference sizing
GitHub Pages is free only for public repositories (private Pages needs an Enterprise organisation even on paid
plans). The public copy sizes every trade at $1,000 max loss ($3,000 total) so figures scale by mental maths.

### 2026-11-16 · Exit plan design
Both stops buy back only the short put (the long put can only gain as SOL falls). Price stop on the SOL index,
backup stop on the short put's mark price, take profit as a resting limit, time exit 7 days before expiry.
Stop-limit, reduce-only, good-till-cancelled. On Deribit a buy triggered by SOL falling may be a "take" order
type: to be verified on the test site before any automation.

### 2026-11-16 · Conviction score
0-100 from: options expensive (30), cushion before a loss vs a typical move (25), credit vs width (20),
liquidity (15), depth of IV history (10). High 70+, Medium 45-69, Low below 45 (= skip). Capped at Medium while
fewer than 60 days of IV history exist.

### 2026-11-15 · Credit rule 12% of width, not 25%
A put spread with the short put near -0.20 delta typically pays 10-17% of its width; 25% would need a much
closer short strike or extreme IV, so the scanner would almost never suggest anything.

### 2026-11-15 · Standard library only
No pandas/numpy/YAML so the laptop needs only Python, nothing to install. Settings in TOML (`tomllib`).

### 2026-11-14 · Strategy direction
Defined-risk spreads only. Four setups explored (bull put spread for income, bear call spread near highs,
zero-cost put-spread-financed call spread at lows, zero-cost call-spread-financed put spread at highs).
Version 0 implements only the bull put spread, read-only.
