# Capital Allocation and Strategy Optimization Plan

## Objective

Maximize long-run account growth under the existing defined-risk options framework without relaxing the hard risk engine.

The economic objective is:

- allocate scarce risk budget to the highest-quality opportunities
- avoid spending too much budget on thin-credit or duplicate exposures
- keep enough unused budget to preserve option value for later opportunities

This plan separates:

- production strategy settings
- research-only experiment settings

## Production Strategy

### Hard Risk Limits

These are hard veto limits enforced by the risk engine and strict spec. They are based on `available_cash`, not net liquidation including already reserved open risk.

| Control | Rule |
|---|---|
| Normal-score per-trade max loss | `20%` of `available_cash` |
| High-score per-trade max loss | `40%` of `available_cash` |
| Total open max loss | `50%` of `available_cash` |
| High-score threshold | `entry_score >= 80` |
| SOXL special cap | keep the stricter separate cap |

### Position Sizing Targets

These are default capital-allocation targets, not hard limits.

| Score bucket | Target risk | Max contracts |
|---|---:|---:|
| `55-64` | `2%` of `available_cash` | `2` |
| `65-79` | `10%` of `available_cash` | `10` |
| `80+` | `20%` of `available_cash` | `20` |

Sizing logic:

```text
target_risk_dollars = available_cash * target_risk_pct
raw_quantity = floor(target_risk_dollars / single_contract_max_loss)
final_quantity = min(raw_quantity, max_contracts, hard_risk_cap_quantity)
```

Concentration discounts:

- if the symbol is already open, multiply target risk by `0.50`
- if the same strategy family is already open, multiply target risk by `0.75`
- if total open max loss already exceeds `25%` of `available_cash`, multiply target risk by `0.75`

### Short Premium Quality Filters

These are the production short-premium selection rules after the optimization pass.

| Parameter | Production rule |
|---|---|
| Primary underlying | `QQQ` remains the main production engine |
| Short premium delta band | `0.16 - 0.25` absolute delta |
| Credit / width minimum | `18%` |
| Credit / width maximum | `35%` |
| Candidate ranking priority | reward/risk, spread quality, diversification, entry score, risk utilization |

Why these changes:

- the tighter delta band avoids chasing low-quality far-OTM credits and avoids moving too close to the money
- the higher credit/width floor removes many low-edge trades that consume risk budget without enough compensation
- the diversification term lowers the score of repeated same-symbol or same-strategy exposure before the risk engine has to reject it

### Current Exit Rules

These remain unchanged in production until the exit-matrix research is complete.

| Strategy family | Profit target | Stop logic |
|---|---|---|
| Credit spreads | `50%` of max profit | `2.5x` original credit |
| Debit spreads | `75%` of debit | `45%` of debit loss |
| Calendar spreads | `25%` of debit | `35%` of debit loss |

## Economic Rationale

### Why the plan does not target the hard limit directly

Hard risk limits are catastrophe boundaries, not default working points.

If default sizing always targets the full hard limit:

- the portfolio spends risk budget too early in the day or week
- later higher-quality trades face a higher opportunity cost
- drawdown volatility rises faster than the marginal improvement in expected return

The production approach uses:

- hard limits as absolute veto boundaries
- sizing targets as the normal capital-allocation policy

That keeps the system inside the same risk framework while allowing account growth to scale trade size materially.

### Why QQQ remains the main production engine

`QQQ` currently has the best combination of:

- liquidity
- repeatable spread construction
- stable real-data candidate generation
- lower data-quality friction than `NVDA` and `SOXL`

This means capital allocation should first optimize the best existing engine before widening strategy breadth.

## Exit Parameter Experiment Matrix

### Research Goal

Find the best exit policy for the current production entry logic without mixing changes to entry, sizing, and exit in the same test.

The experiment must answer:

- does a tighter stop improve expectancy after costs?
- does a larger take-profit target improve profit factor enough to justify longer holding time?
- which configuration improves return per unit drawdown, not just win rate?

### Matrix Design

Run these four exit-policy variants on the same candidate set:

| Experiment ID | Profit target | Stop rule | Hypothesis |
|---|---:|---:|---|
| `E1` | `50%` | `2.5x credit` | current baseline |
| `E2` | `50%` | `2.0x credit` | same take-profit, less tail loss |
| `E3` | `60%` | `2.0x credit` | keep more upside while still tightening stop |
| `E4` | `40%` | `1.8x credit` | faster win capture and lower drawdown |

### What Must Stay Fixed

For a valid experiment, hold these constant across all matrix runs:

- same underlying set
- same regime classifier
- same candidate-generation rules
- same sizing policy
- same commission and slippage assumptions
- same event calendar blocking rules
- same market-data sample window

Only the exit parameters should change.

### Execution Procedure

1. Build one canonical scenario set.
   Use the same generated candidates and market path for all four exit variants.

2. Replay each variant independently.
   Each variant must run on the exact same scenario list.

3. Record metrics by variant.
   Required outputs:
   - total return
   - max drawdown
   - profit factor
   - expectancy per trade
   - average win
   - average loss
   - average win / average loss
   - number of trades
   - worst day
   - worst week
   - exposure time

4. Rank variants by risk-adjusted performance.
   Do not promote a variant based only on win rate or total return.

5. Paper-validate the winning variant.
   Backtest winner must survive paper trading before becoming the new production exit.

### Executable Research Command

The matrix is now executable from the CLI.

Command:

```bash
python -m trading_bot.cli exit-matrix ^
  --scenario-file docs/reports/backtests/exit_matrix_scenarios.json ^
  --output-dir docs/reports/backtests/exit_matrix
```

Inputs:

- `scenario-file` accepts either:
  - a JSON array of scenarios
  - or an object with a top-level `"scenarios"` list

Each scenario must include:

- `trade_id`
- `entry_date`
- `candidate`
- `exit_snapshots`

The candidate payload must include:

- strategy name
- underlying
- legs with contract details
- `dte`
- `entry_score`
- `max_profit`
- `max_loss`
- `expected_credit_or_debit`
- `exit_plan`
- optional `quantity`

Output files:

- `exit_matrix_summary.json`
- `e1_backtest.json`
- `e2_backtest.json`
- `e3_backtest.json`
- `e4_backtest.json`

The summary file contains the cross-variant comparison table. Each per-variant report contains full backtest trades, skipped trades, and metrics for auditability.

### Promotion Rules

Promote a new exit policy only if all of the following are true versus the baseline:

- expectancy per trade is higher
- max drawdown is not materially worse
- profit factor is equal or higher
- average loss does not widen enough to destroy capital efficiency
- paper-trading behavior remains operationally stable

### Failure Modes to Watch

#### `E2` and `E4` failure mode

The stop may become so tight that the strategy gets shaken out before short-premium theta can work.

Signal:

- lower win rate without enough reduction in average loss

#### `E3` failure mode

The system may hold too long for the extra `10%` target, giving back open profit and increasing exposure time.

Signal:

- higher average win, but lower expectancy or worse drawdown

## Implementation Order

1. Ship the new production sizing policy.
2. Ship the tighter short-premium delta and credit/width filters.
3. Keep production exits unchanged.
4. Run the exit matrix in research and compare variants.
5. Promote only the best risk-adjusted exit policy.

## Code Mapping

Production behavior is implemented in:

- `src/trading_bot/config/risk_limits.yaml`
- `src/trading_bot/config/settings.py`
- `src/trading_bot/risk/sizing.py`
- `src/trading_bot/risk/engine.py`
- `src/trading_bot/strategies/short_premium.py`
- `src/trading_bot/strategies/spec_compliance.py`
- `src/trading_bot/strategies/selector.py`

## Current Recommendation

Use the new production strategy as the default paper strategy:

- hard limits: `20% / 40% / 50%`
- sizing targets: `10% / 20%`
- short premium delta band: `0.16 - 0.25`
- credit/width band: `18% - 35%`
- production exits unchanged until matrix testing is complete

This keeps the strategy economically coherent:

- capital can scale as the account grows
- poor-quality thin-credit trades are reduced
- the system preserves budget for later opportunities
- exit optimization stays in research until proven
