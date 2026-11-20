# Strategy brief

Agreed with the owner on 2026-11-19 (interview in the planning chat). This is the menu of strategies the
scanner should support, how each is chosen, and how each will be tested. Build them one at a time as strategy
files (`strategies/*.toml`) on the shared engine; none goes live until it passes the held-back test (ROADMAP step 3).

## The idea in one line
Use SOL's options price as a switch: **when options are rich, sell defined-risk premium; when they are cheap,
buy defined-risk optionality for a big move; always keep accumulating SOL.** Direction comes from a tested data
signal combined with the owner's stated view.

## Strategy menu

| Market state | Direction | Strategy | Purpose | Budget |
|---|---|---|---|---|
| IV rich | Bullish or neutral | Bull put spread (exists) | Income; paid to agree to buy lower | Per-trade max loss + total cap |
| IV rich | Bearish, or SOL stretched after a rally | Bear call spread | Income from a stalled rally | Same caps, shared with all premium trades |
| IV rich | No direction, range expected | Iron condor (put spread + call spread) | Income if SOL stays in a range | Same caps |
| IV rich, SOL near recent highs | Protective | Zero-cost hedge: buy put spread, pay with a call spread | Protect core SOL near tops without selling (setup 4) | Sized to a share of core holdings, not the risk budget |
| IV cheap | Bullish | Call debit spread | Cheap exposure to a big move up | "Big move" budget: 1% of portfolio per month (confirmed) |
| IV cheap | Bearish | Put debit spread | Cheap exposure to a big drop, or a cheap hedge | Same budget |
| IV cheap | No direction, big move expected | Long strangle or both debit spreads | Paid on a large move either way | Same budget |
| Any | — | Accumulation helper | Scheduled SOL buys + extra buys on fear days | Set by the owner; benchmarked against plain weekly buying |
| IV fair | — | No options trade | Discipline: most days the answer is no | — |

All option trades are defined-risk. No naked short options.

## Direction: data signal + the owner's view

**Data signal (tested).** A direction score from these candidate factors; only those that help on held-back data stay:
- **Trend:** SOL against its 50- and 200-day averages.
- **Momentum and drawdown:** recent % move, and how far SOL is below its recent high.
- **Options market:** put-versus-call pricing (skew) and term structure (near-term options dearer than later ones).
- **Crash / fear:** implied vol up 15+ points with a sharp drop (history: drops over 15% in 5 days lifted IV 15-17 points).

The score maps to bullish / neutral / bearish. Factor choice and weights are fixed on in-sample data
(2024-06 to 2025-06) and judged only on held-back data (2025-07 onwards).

**Result of the first test (2026-11-20, rules version 1, see DECISIONS):** no factor passed, so the data signal
stays neutral and only the owner's view tilts the reading.
- Failed: **trend** (practice period backwards, -21 points; unseen +0.6), **momentum** (-11 | -0.6, backwards in
  practice), **drawdown** (+16.5 in practice, -1.9 unseen), **crash/fear** (+10.9 | +18.7 points, but only 2
  non-overlapping fear periods in the unseen period, fewer than the 4 required).
- Recording only (not enough history to test): **skew**, **term structure**, recorded daily from the stored chains.

**The owner's view (`view.toml`).** Bullish / neutral / bearish, strength 1-3, and an expiry date so a stale view
switches itself off. The view tilts the data score; how much it can tilt is a setting in each strategy file.

**Important limit:** past views were never recorded, so the view's effect cannot be backtested. Backtests use the
data signal only. From now on, every daily run records the view in force, so its effect can be measured over time.

## Settings for the cheap-IV ("big move") trades
- **Budget (confirmed by the owner 2026-11-19):** at most 1% of portfolio per month on trades that can expire
  worthless. Real figure in the owner's private settings; the public copy uses a round reference figure.
- **Structure (decided by testing):** debit spreads by default; the backtest also tries outright options and the
  held-back results decide.
- **Horizon (decided by testing):** the backtest compares about 30, 60 and 90 days to expiry; held-back results pick.

## How each strategy is judged
Same scorecard for all (ROADMAP step 3): trades, win rate, average and total per $1,000 risked, worst trade,
longest losing streak, max drawdown, results by market condition, and comparison with simply holding or
dollar-cost averaging SOL. The protective hedge is judged on how much it reduced core-holding drawdowns versus
its cost. The accumulation helper is judged on SOL acquired per dollar versus plain weekly buying.

## Honest expectations
- History covers about 2.5 years: roughly 300 rich, 300 cheap and 300 fair days, but far fewer independent trades.
  Expect small samples; prefer simple rules that work across variants over the single best number.
- Long-option trades lose small amounts often and win occasionally; judge them over many trades, not one.
- The main source of long-term gains is the SOL holding itself; these strategies are helpers.

## Suggested build order
1. Direction signal module + `view.toml` (record the view daily from now on).
2. Call and put debit spreads on cheap-IV days (the new "big move" capability).
3. Bear call spread and iron condor on rich-IV days.
4. Zero-cost protective hedge for core holdings near highs.
5. Accumulation helper.
