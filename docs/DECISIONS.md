# Decision log

Newest first. Each entry: what was decided, and why. Numbers are from the analysis at the time.

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
