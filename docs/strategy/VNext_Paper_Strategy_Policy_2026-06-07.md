# vNext Paper Strategy Policy - 2026-06-07

This policy is paper-only. It does not enable live trading and does not allow
0DTE, naked/undefined-risk positions, market orders, missing max loss, missing
exit plans, or risk-engine bypasses.

## Current Mode

The current account is treated as preservation mode because equity is below
starting equity and open max loss is elevated.

Current snapshot used for this policy:

- Starting equity: 2000.00
- Current equity: 1705.50
- Available cash: 1198.00
- Open max loss: 507.50
- Open positions: 2 NVDA call debit spreads
- Closed trades: 2 QQQ put credit spreads, both stopped out

## Hard Approval Stack

A candidate must pass every layer:

1. Strategy generation with defined-risk legs only.
2. Exit-plan quality gate.
3. Tastytrade paper liquidity gate.
4. Paper capital preservation gate.
5. Symbol and TECH_BETA allocation gate.
6. Duplicate/correlation and stopout cooldown gate.
7. Risk engine final veto.
8. Limit-order paper execution only.

Any rejection means no trade. No layer can override a later veto.

## Risk Budget

Risk budget is based on available cash and already-open max loss, not headline
net liquidation value.

Current defaults:

- Normal total open max loss cap: 20% of available cash through the core risk
  engine.
- Paper preservation total open max loss cap: 12% of available cash.
- Sizing preservation total open max loss cap: 12% of available cash.
- Paper preservation per-trade cap: min(100 USD, 5% of available cash).
- Max new trades per day: 1.
- SOXL remains capped and experimental-only.

## Candidate Selection

The candidate ranker returns exactly one selected candidate or no trade.

Defaults:

- Normal minimum opportunity score: 70.
- Preservation minimum opportunity score: 78.
- Top candidate must beat runner-up by 8 points unless it has materially lower
  max loss.
- No trade is preferred over spending risk on a marginal candidate.

## Correlation Policy

QQQ, NVDA, SOXL, SMH, and SOXX are treated as one TECH_BETA cluster.

During preservation mode:

- Max one active position per symbol/direction.
- Max one active position per thesis bucket.
- Max one bullish TECH_BETA position.
- Max one bearish TECH_BETA position.
- Block additional correlated TECH_BETA exposure when open max loss is already
  elevated.

## Liquidity Policy

Tastytrade paper candidates fail closed on liquidity:

- Missing bid/ask/mid rejects.
- Invalid bid/ask rejects.
- Missing volume or open interest rejects by default.
- Low volume or low open interest rejects.
- Wide leg market rejects.
- Wide package market rejects.

Observation mode exists only for explicit paper-only data collection and cannot
override risk-engine, max-loss, or exit-plan requirements.

## Timing And Regime

Unknown or unstable/choppy regimes receive hard-block score reasons.

Debit spreads still require:

- Opening cooldown.
- Price-action confirmation.
- Anti-chase checks against VWAP/ATR when data exists.

## Exit Plan

Every candidate must have a defined exit plan before order creation.

Current exit plan enforcement includes:

- Planned profit target.
- Planned stop loss.
- Hard stop loss.
- EOD tightened stop for credit spreads.
- Debit spread invalidation stop when available.
- Time/expiration management through existing exit logic.

## Data Collection

Every paper decision should be auditable.

Added measurement layers:

- Strategy attribution rollups by strategy, symbol, and exit reason.
- RL shadow JSONL events with `paper_only=true`, `shadow_mode=true`, and
  `rl_shadow_score=null`.

RL may be researched later only as an advisory filter. It cannot approve trades,
increase size, bypass vetoes, or enable live trading.
