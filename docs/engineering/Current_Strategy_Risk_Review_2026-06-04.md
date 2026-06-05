# Current Strategy, Risk Controls, and Improvement Review

Date: 2026-06-04  
Scope: Current paper-trading behavior, active strategy logic, risk framework, observed weaknesses, and recommended next changes.

## 1. Executive Summary

The current system has moved beyond "research skeleton" status. It now:

- connects to real tastytrade market data in read-only mode,
- classifies market regimes,
- generates real option strategy candidates,
- runs candidates through strict strategy-spec checks,
- runs candidates through a risk engine,
- opens paper trades,
- records audit logs and position paths for later review.

The system is no longer failing because it cannot find contracts at all. The current bottlenecks are now higher level:

- entry timing quality,
- capital allocation across symbols and strategy families,
- spec and risk conflicts between "mainline" and "experimental" strategies,
- missing intraday confirmation filters,
- over-reliance on symbol-level concentration limits to block questionable follow-up trades.

The system is usable as a paper research engine, but it is not yet a high-confidence production strategy. The major issue is no longer "can it trade?" but rather "is it trading the right things, in the right order, at the right time, for the right size?"

## 2. Current Live Paper Architecture

Current decision pipeline:

1. Fetch underlying and option-chain data from tastytrade.
2. Apply general liquidity filters.
3. Classify regime.
4. Score strategy opportunity on a 100-point framework.
5. Generate legal defined-risk candidate structures.
6. Optionally size the candidate dynamically.
7. Validate against strict strategy-spec rules.
8. Validate against the portfolio risk engine.
9. If approved, open the paper trade.
10. Persist audit logs and mark-path history.

This is an important strength. Strategy code does not directly bypass risk checks.

## 3. Current Active Strategy Set

The repository supports these strategy families:

- `put_credit_spread`
- `call_credit_spread`
- `iron_condor`
- `call_debit_spread`
- `put_debit_spread`
- `calendar_spread`
- `diagonal_spread`

In practice, the currently active paper behavior is dominated by these two lines:

### 3.1 Mainline Strategy

- Symbol focus: `QQQ`
- Regime: `bull_trend_high_iv`
- Structure: `put_credit_spread`

This is currently the cleanest and most stable path in the system.

Why it is the mainline:

- It matches the strict allowed-regime mapping.
- It has already produced multiple real paper trades.
- Liquidity is relatively good.
- Defined-risk structure is clear.
- The strategy is naturally compatible with the current small-account framework.

### 3.2 Experimental Secondary Strategy

- Symbol focus: `NVDA`
- Regime: often `range_low_iv`
- Structure: `call_debit_spread`

This strategy is currently being allowed through paper trading via experimental override logic.

Important note:

- This is not yet a fully "trusted" mainline strategy.
- It is currently a paper-experimental branch.
- It can be approved by spec with warnings, but it is not as consistent with the strict base strategy map as the QQQ short-premium path.

### 3.3 Lower Priority / Limited / Mostly Rejected Paths

- `call_credit_spread`
- `put_debit_spread`
- `iron_condor`
- `calendar_spread`
- `diagonal_spread`
- `SOXL` short-premium variants

These are either rarely selected, still weak in generation quality, or often blocked by spec/risk constraints.

## 4. Regime-to-Strategy Mapping Currently Enforced

Current strict allowed mapping:

- `bull_trend_low_mid_iv`
  - `put_credit_spread`
  - `call_debit_spread`
  - `diagonal_spread`

- `bull_trend_high_iv`
  - `put_credit_spread`

- `range_high_iv`
  - `iron_condor`
  - `put_credit_spread`
  - `call_credit_spread`

- `range_low_iv`
  - `calendar_spread`
  - `diagonal_spread`

- `bear_trend_high_iv`
  - `call_credit_spread`
  - `put_debit_spread`

- `crash_risk_off`
  - `put_debit_spread`

Paper experimental override currently adds:

- `range_low_iv`
  - `call_debit_spread`

This means the NVDA call-debit trades that opened today were allowed because paper-experimental mode expands the otherwise strict mapping.

## 5. Current Scoring Framework

The system uses a 100-point score model:

- `regime_fit`: 30
- `volatility_edge`: 25
- `liquidity_quality`: 20
- `price_action`: 15
- `event_risk`: 10

### 5.1 Current Thresholds

- `80-100`: high-quality trade
- `65-79`: good trade
- `55-64`: small experimental trade only
- `<55`: no trade

### 5.2 Important Interpretation

The score is good at saying:

- "this is broadly aligned enough to consider"

It is not yet strong enough at saying:

- "this is a high-quality entry location right now"

That distinction matters a lot. A candidate can pass the score threshold and still be opened at poor intraday timing.

## 6. Current DTE, Delta, and Structure Rules

### 6.1 DTE Rules

Current core settings:

- Short premium: `21-60 DTE`
- Neutral range: `30-45 DTE`
- Trend QQQ/NVDA: `14-45 DTE`
- Trend SOXL: `7-21 DTE`
- Calendar front: `7-21 DTE`
- Calendar back: `30-60 DTE`
- Forbidden minimum: `1 DTE`

### 6.2 Delta Rules

Current active defaults:

- Short premium short leg abs delta: `0.16-0.25`
- Iron condor short legs abs delta: `0.16-0.25`
- Trend long leg abs delta: `0.45-0.65`
- Trend short leg abs delta: `0.20-0.40`

### 6.3 Credit-Spread Width / Credit Rules

Current enforced rule:

- `credit / width` must be between `18%` and `35%`

This is tighter than earlier looser versions and is intended to avoid low-credit, poor-payoff short-premium entries.

## 7. Current Risk Framework

The system currently uses `available_cash` as the risk budget base, not raw account net liquidation.

This is correct and important.

Current interpretation:

- `available_cash = equity - total_open_max_loss`

This prevents already-open risk from being double-counted as if it were still deployable capital.

### 7.1 Hard Risk Limits

Current risk limits:

- Default mode: `dry_run`
- Normal-score per-trade max loss: `20% of available_cash`
- High-score per-trade max loss: `40% of available_cash`
- SOXL per-trade max loss: `$150`
- Total open max loss: `50% of available_cash`
- Daily loss limit: `$200`
- Weekly loss limit: `$400`
- Max consecutive losses: `3`
- Max new trades per day: `2`
- Max new trades per week: `5`
- Max same-symbol open positions: `2`
- Max same-strategy open positions: `3`
- Minimum cash buffer: `25%`

### 7.2 Forbidden Defaults

- 0DTE forbidden
- Naked options forbidden
- Market orders for options forbidden
- Live trading disabled by default

### 7.3 Important Observation

The current risk engine is working. It is actively blocking:

- same-symbol concentration,
- total open max loss overflow,
- daily new-trade exhaustion,
- oversized candidates.

This is one of the strongest parts of the system.

## 8. Current Position Sizing Logic

The strategy does not stay at fixed `1x` forever anymore. It now has dynamic sizing logic.

### 8.1 Current Sizing Targets

- Score `<65`
  - target risk: `2% of available_cash`
  - max contracts: `2`

- Score `65-79`
  - target risk: `10% of available_cash`
  - max contracts: `10`

- Score `80+`
  - target risk: `20% of available_cash`
  - max contracts: `20`

### 8.2 Current Sizing Penalties

- Same symbol already open:
  - multiply target risk by `0.50`

- Same strategy already open:
  - multiply target risk by `0.75`

- Crowded portfolio:
  - threshold: `25% of available_cash` already in open max loss
  - then multiply target risk by `0.75`

### 8.3 Practical Effect

This is now a real capital-allocation system, not just a candidate generator.

But it still has weaknesses:

- it is mostly static by score bucket,
- it is not yet regime-aware at the sizing layer,
- it does not yet explicitly prioritize one strategy family over another,
- it still depends on later concentration caps to stop questionable follow-up trades.

## 9. What Actually Happened Today

This section is included so an external reviewer can see real behavior, not just theoretical config.

### 9.1 Today’s New Trades

On 2026-06-04 America/New_York, the paper system opened two new trades:

1. `NVDA call_debit_spread`
   - opened at `09:38:38 EDT`
   - structure: `2026-07-02 +C220 / -C230`
   - entry debit: `327.5`
   - max loss: `327.5`
   - max profit: `672.5`

2. `NVDA call_debit_spread`
   - opened at `11:11:08 EDT`
   - structure: `2026-07-02 +C220 / -C225`
   - entry debit: `180.0`
   - max loss: `180.0`
   - max profit: `320.0`

### 9.2 Current Interpretation

The first trade appears to have been opened at a poor intraday location, likely near a local high.

That is a critical observation because it means:

- the score threshold was met,
- the regime filter did not fail,
- the candidate structure was legal,
- risk was available,
- but the actual entry timing quality was poor.

This is not mainly a risk-engine failure. It is an entry-quality failure.

## 10. Why Today’s NVDA Trades Passed

The opened NVDA trades were not high-score "premium" trades. They were medium-score experimental debit trades.

Their score breakdown was:

- `regime_fit = 18`
- `volatility_edge = 15`
- `liquidity_quality = 16`
- `price_action = 7`
- `event_risk = 10`

Total:

- `66`

Meaning:

- good enough to trade,
- not a top-tier signal,
- price action only neutral,
- not a strong confirmation setup.

This is exactly why the first trade can pass the framework and still be a weak entry.

## 11. Top Current Risk / Weakness Areas

### 11.1 Entry Timing Is Still Too Weak

This is the biggest strategy weakness right now.

Evidence:

- today’s first NVDA trade appears to have been opened near a short-term high,
- `price_action_neutral` can still pass into an approved debit spread,
- the system scans every 5 minutes and can act too early in unstable intraday conditions.

What this means:

- the strategy is direction-aware,
- but it is not yet sufficiently timing-aware.

### 11.2 Opening-Range / Early-Session Noise

The first NVDA trade opened at `09:38 EDT`, very near the opening session.

That is dangerous because:

- opening minutes often have false directional bursts,
- spreads and option repricing can still be settling,
- fast trend-following entries are more likely to chase than confirm.

### 11.3 Experimental and Mainline Strategies Are Mixed Together

Currently the system is effectively running:

- a mainline QQQ short-premium strategy,
- plus an experimental NVDA debit-spread strategy.

This is useful for paper research, but it creates ambiguity:

- total P&L rises, but which strategy truly has edge?
- risk budget gets consumed by different strategy types,
- later tuning becomes harder because attribution is blurred.

### 11.4 Capital Allocation Is Reactive, Not Yet Strategic

The current sizing system allocates by score bucket and penalties, but does not yet explicitly say:

- QQQ mainline takes priority over NVDA experiment,
- experimental trades get a separate smaller budget,
- range-low-IV debit spreads should never consume the same strategic budget as core trend or premium trades.

This causes the system to rely on downstream hard rejections instead of smarter pre-allocation.

### 11.5 Spec and Strategy Intent Are Not Fully Aligned

The system currently has paper-experimental overrides to allow trades that the strict map would otherwise reject.

This is fine for research, but it creates a state where:

- the codebase has one declared strategy philosophy,
- but paper behavior is broader than that philosophy.

That is acceptable only if it is clearly documented as experimental, not treated as part of the core production strategy.

### 11.6 Missing Activity Metadata Still Reduces Confidence

The system now degrades gracefully when tastytrade is missing `volume` / `open_interest`.

That was the right fix.

But the consequence remains:

- some trades are being made with incomplete activity metadata,
- the system relies more heavily on quote spread and Greeks than on proven market participation statistics.

This is workable, but not ideal.

### 11.7 Current Debit-Spread Risk Can Still Be Large Relative to Signal Quality

Today’s opened debit spreads were score `66`, not `80+`.

That means:

- they are not low-confidence,
- but they are also not strong enough to justify sloppy entries.

If medium-score debit spreads are allowed to open at weak timing points, realized performance will become unstable.

## 12. What the System Is Good At Right Now

To avoid a one-sided review, these are the current strengths.

### 12.1 Risk Engine Veto Power Works

This is real and operational, not theoretical.

The system successfully blocks candidates for:

- `same_symbol_concentration_exceeded`
- `total_open_max_loss_exceeded`
- `max_new_trades_per_day_exceeded`
- `spec_normal_trade_risk_above_20pct_equity`

### 12.2 Defined-Risk Structures Only

The current system remains aligned with safety requirements:

- defined-risk spreads only,
- no naked options,
- no 0DTE,
- no market orders.

### 12.3 Logging and Replay Infrastructure Are Becoming Strong

The paper engine now stores:

- detailed audit records,
- candidate approvals and rejections,
- path snapshots for positions,
- future exit-matrix scenarios.

This is very valuable for iterative strategy improvement.

### 12.4 QQQ Core Path Is Reasonably Coherent

The QQQ short-premium mainline is much cleaner than many of the side paths.

If one strategy family is to be promoted first, it should almost certainly be that one.

## 13. What Most Needs Improvement

Below is the prioritized improvement list.

### Priority 1: Improve Entry Timing Filters

This is the most urgent strategy improvement.

Recommended changes:

- Add an opening cooldown for trend/debit strategies.
  - Example: do not open trend participation trades in the first `15-30` minutes after the open.

- Require stronger price-action confirmation for trend debit spreads.
  - Today `price_action_neutral` is too permissive.
  - Debit spreads should likely require either:
    - higher price-action score,
    - or explicit confirmation reason code.

- Add anti-chase logic.
  - Avoid entries after short-term vertical moves.
  - Avoid the first breakout candle.
  - Avoid entries when price is extended too far from short-term mean.

This single area is likely the highest-value improvement.

### Priority 2: Separate Mainline and Experimental Budget

Recommended structure:

- Mainline strategy bucket
  - QQQ `put_credit_spread`

- Secondary production bucket
  - only after proof

- Experimental paper bucket
  - NVDA `range_low_iv` debit overrides

This would solve multiple problems:

- cleaner P&L attribution,
- better capital discipline,
- less confusion about what is "real strategy" vs "research strategy."

### Priority 3: Add Strategy-Level P&L Attribution

Track, at minimum:

- cumulative P&L by strategy family,
- cumulative P&L by symbol,
- max drawdown by strategy family,
- expectancy by strategy family,
- average hold time by strategy family.

Without this, overall account growth can hide weak sub-strategies.

### Priority 4: Add Better Pre-Risk Candidate Ranking

Right now the system often relies on hard caps later in the pipeline.

Instead, it should rank candidates before that point using:

- regime quality,
- entry timing quality,
- reward/risk,
- spread quality,
- same-symbol existing exposure,
- strategy-priority budget.

This would reduce the number of low-value candidates that only get rejected at the end.

### Priority 5: Tighten Debit-Spread Qualification in Experimental Paths

For `NVDA call_debit_spread` experimental paper trades, consider:

- requiring higher score than generic `65`,
- requiring non-neutral price action,
- requiring no early-session entries,
- requiring stronger trend continuation confirmation.

This is especially important because debit spreads are timing-sensitive.

### Priority 6: Verify Path-Tracking Data Quality

There have been signs that some underlying-price path values may not always line up cleanly with later observed values.

This should be reviewed so that future replay and RL/backtest research are not polluted by path-capture inconsistencies.

## 14. Suggested Concrete Next Changes

These are the most practical next changes to implement.

### 14.1 Add Trend Entry Cooldown

Suggested rule:

- no new `call_debit_spread` / `put_debit_spread` entries in the first `20` minutes after the open.

Reason:

- removes some of the noisiest and most easily chased entries.

### 14.2 Raise Confirmation Requirements for Trend Debit Spreads

Suggested rule:

- trend debit spreads require stronger price action than neutral.

Possible implementation:

- require price-action component score above a threshold,
- or disallow `price_action_neutral` for trend debit entries.

### 14.3 Create Explicit Strategy Tiers

Suggested categorization:

- Tier 1:
  - `QQQ bull_trend_high_iv put_credit_spread`

- Tier 2:
  - validated NVDA debit spreads after enough paper evidence

- Tier 3:
  - experimental overrides only

### 14.4 Add Per-Strategy Risk Budgets

Example concept:

- QQQ mainline can consume the majority of risk budget,
- NVDA experimental can only consume a capped sub-budget,
- SOXL remains tightly limited.

### 14.5 Add Intraday Entry Quality Metrics

Track:

- distance from short-term VWAP,
- 5-minute move into entry,
- bar extension versus recent average true range,
- whether entry occurs after first pullback or at immediate impulse high.

These features matter more for debit-spread timing than broad regime alone.

## 15. Questions to Ask a Reviewer or Another LLM

If you want to take this document to another model, these are the best questions to ask:

1. Is `price_action_neutral` too permissive for trend debit-spread entries?
2. Should opening-range entries be blocked for `NVDA call_debit_spread`?
3. How should mainline vs experimental strategy risk budgets be separated?
4. Is a `66` score sufficient for debit spreads, or should debit spreads require higher entry quality than credit spreads?
5. What is the best way to encode anti-chase filters for 5-minute scanning logic?
6. Should regime classification and intraday confirmation be separated into two gates instead of one blended score?
7. How should P&L attribution by strategy family be structured?

## 16. Bottom-Line Assessment

The current strategy is not broken. It is functioning.

But it is functioning in a way that reveals the next real problem:

- the system can now trade,
- but it still does not always know when *not* to trade.

That is the key maturity gap.

The biggest weakness is not structural risk control. The risk engine is doing its job.

The biggest weakness is:

- entry timing quality,
- especially for debit spreads,
- especially early in the session,
- especially in experimental strategy branches.

The current best path forward is not to add more strategies yet. It is to improve:

- entry timing,
- strategy-tier separation,
- capital allocation priority,
- attribution,
- and experimental discipline.
