# Tastytrade QQQ / NVDA / SOXL Bot — Expert Strategy Spec v2

> Purpose: Build a systematic options trading assistant that seeks long-term positive expected value through volatility risk premium, trend participation, diversification, position sizing, and strict portfolio-level risk controls.
>
> Important: This document is not a guarantee of profit. It is a ruleset for research, backtesting, dry-run validation, and controlled live deployment.

---

## 1. Core Philosophy

The system should not wait for a perfect trade. Instead, it should trade a diversified strategy book with moderate entry requirements, high sample count, controlled risk per position, and portfolio-level drawdown limits.

The goal is not to maximize single-trade accuracy. The goal is to maximize long-term risk-adjusted return under strict drawdown constraints.

Primary objective:

```text
Maximize: Risk-adjusted CAGR / Calmar / Sortino / Profit Factor
Subject to: max drawdown, daily loss limit, weekly loss limit, position concentration limits
```

The system should be built around four engines:

1. Short premium income engine
2. Neutral range income engine
3. Trend participation engine
4. Tail-risk protection / risk-off engine

---

## 2. Strategy Allocation

Use risk budget, not cash balance, as the main allocation unit.

For a small account around $2,000:

| Engine | Risk Budget Share | Purpose |
|---|---:|---|
| Short Premium Engine | 45% | Systematic income, high probability trades |
| Neutral Range Engine | 20% | Collect premium during range-bound markets |
| Trend Participation Engine | 25% | Capture strong QQQ / NVDA / SOXL trend moves |
| Hedge / Risk-Off Engine | 10% | Reduce tail risk and protect against sharp selloffs |

Total open max loss should usually stay below 30%–40% of account equity. For a $2,000 account, this means open defined-risk loss should usually stay below $600–$800.

---

## 3. Market Regime Classifier

Every trading decision starts with a regime classification.

### 3.1 Regime Inputs

Use these inputs:

- QQQ daily close vs EMA20 / EMA50
- SPY daily close vs EMA20 / EMA50
- QQQ 5-day and 20-day return
- SMH / SOXX semiconductor trend
- VIX level and VIX change
- IV Rank / IV Percentile of target underlying
- Realized volatility vs implied volatility
- Market breadth proxy if available
- Event calendar: CPI, FOMC, Powell speech, NVDA earnings, large-cap tech earnings

### 3.2 Regime Types

| Regime | Definition | Preferred Strategy |
|---|---|---|
| Bull Trend / Low-Mid IV | QQQ above EMA20/50, IV not extreme | Put credit spread, call debit spread |
| Bull Trend / High IV | Trend up but IV elevated | Put credit spread, short premium, smaller size |
| Range / High IV | No clear trend, IV elevated | Iron condor, short premium |
| Range / Low IV | No trend, low IV | Calendar / diagonal spreads |
| Bear Trend / High IV | QQQ below EMA20/50, IV elevated | Call credit spread, put debit spread, reduce bullish trades |
| Crash / Risk-Off | gap down, volatility spike, event shock | No new short premium; hedge only |

---

## 4. Score-Based Entry System

The old strategy was too strict because it required too many conditions at the same time. The new system uses scoring.

A trade does not need every condition to be perfect. It needs enough total score.

### 4.1 Score Components

Total score = 100 points.

| Component | Max Points | Meaning |
|---|---:|---|
| Regime Fit | 30 | Strategy matches current market regime |
| Volatility Edge | 25 | IV Rank, IV Percentile, IV/HV support the trade |
| Liquidity Quality | 20 | Tight bid/ask, high volume, open interest |
| Price Action | 15 | VWAP, EMA, breakout/pullback support entry |
| Event Risk | 10 | No major event risk or event risk is intentionally priced |

### 4.2 Entry Thresholds

| Score | Action |
|---:|---|
| 80–100 | High-quality trade; normal size allowed |
| 65–79 | Good trade; reduced/normal size allowed |
| 55–64 | Small-size experimental trade only |
| <55 | No trade |

This makes the bot trade more often without becoming random.

---

## 5. Engine A — Short Premium Income Engine

This is the main long-term high-probability engine.

### 5.1 Preferred Structures

1. Put credit spread
2. Call credit spread
3. Broken-wing butterfly only after backtesting
4. Cash-secured short put only for larger accounts, not first version

### 5.2 Underlyings

Priority:

1. SPY / QQQ
2. IWM / SMH / SOXX
3. NVDA / MSFT / AAPL / META / GOOGL
4. SOXL: avoid short premium as core strategy; use only very small defined-risk trades after separate validation

### 5.3 Put Credit Spread Rules

Use when regime is bullish or neutral-bullish.

| Parameter | Rule |
|---|---|
| DTE | IVR < 30: 45–60 DTE; IVR 30–60: 30–45 DTE; IVR > 60: 21–35 DTE |
| Short put delta | Bull trend: 0.20–0.35; weak trend: 0.10–0.20 |
| Long put | $1–$5 below short put for small account; wider only after account grows |
| Credit target | 15%–35% of spread width |
| Liquidity | Bid/ask spread ideally <= 8%–12% of mid; reject extremely illiquid chains |
| Profit target | Close at 40%–60% of max profit |
| Time exit | Close or roll at 21 DTE for 45 DTE trades; avoid holding into final week unless strategy is specifically designed for it |
| Loss management | Close if spread value reaches 2x–3x original credit, short delta > 0.55, or regime flips bearish |

### 5.4 Call Credit Spread Rules

Use when regime is bearish, overextended, or resistance rejection appears.

| Parameter | Rule |
|---|---|
| DTE | 21–45 DTE |
| Short call delta | 0.15–0.30 |
| Credit target | 15%–30% of width |
| Entry | Underlying below VWAP/EMA20 or rejecting resistance |
| Profit target | 40%–60% of max profit |
| Loss management | Close if short delta > 0.55 or underlying breaks above resistance with volume |

---

## 6. Engine B — Neutral Range Income Engine

Use this when the market is range-bound and IV is elevated.

### 6.1 Iron Condor Rules

| Parameter | Rule |
|---|---|
| Underlyings | SPY / QQQ / IWM preferred |
| IVR / IVP | Prefer IVR > 35 or IVP > 50 |
| DTE | 30–45 DTE |
| Short strike delta | 0.16–0.25 on both sides |
| Width | $1–$5 for small account |
| Credit target | 20%–35% of width if possible |
| Profit target | 25%–50% of max profit |
| Time exit | 21 DTE or earlier if profit target hits |
| Adjustment | If one side is tested, consider closing untested side or rolling only if total risk does not increase |

### 6.2 When Not to Use Iron Condors

Do not use iron condors when:

- QQQ is in strong trend expansion
- Major event is within 24 hours
- IV is too low and credit is not worth risk
- Bid/ask spread is too wide

---

## 7. Engine C — Trend Participation Engine

This engine captures upside/downside movement. It should not dominate the account, but it is necessary for long-term return maximization because pure short premium can underperform during strong trends.

### 7.1 Call Debit Spread Rules

Use when regime is bullish and trend score is strong.

| Parameter | Rule |
|---|---|
| Underlyings | QQQ, NVDA, SMH, SOXL |
| DTE | QQQ/NVDA: 14–45 DTE; SOXL: 7–21 DTE only with small size |
| Long call delta | 0.45–0.65 |
| Short call delta | 0.20–0.40 |
| Debit target | Prefer reward/risk >= 1.2; avoid paying too close to max value |
| Entry | Trend score >= 65; high-conviction if >= 80 |
| Profit target | +50% to +100%; scale out if possible |
| Stop loss | -35% to -50% of debit, or trend score falls below 50 |

### 7.2 Put Debit Spread Rules

Use when regime is bearish or risk-off.

| Parameter | Rule |
|---|---|
| Underlyings | QQQ, SPY, SMH, SOXL |
| DTE | 14–45 DTE |
| Long put delta | 0.45–0.65 |
| Short put delta | 0.20–0.40 |
| Profit target | +50% to +100% |
| Stop loss | -35% to -50% |

### 7.3 SOXL Special Rules

SOXL has high beta and should be treated as an accelerator, not a core income product.

Rules:

- SOXL max risk per trade: 5%–10% of account for small accounts
- Prefer debit spreads, not naked long calls
- Avoid selling SOXL premium as a core strategy in V1
- Do not open SOXL trades during first 10–15 minutes unless specifically testing opening-range strategy
- If QQQ and SMH disagree, reduce or skip SOXL exposure

---

## 8. Engine D — Low-IV Calendar / Diagonal Engine

When IV is low, short premium has less edge. Instead of forcing put credit spreads, use limited-risk calendars or diagonals.

### 8.1 Calendar Spread Rules

| Parameter | Rule |
|---|---|
| Underlyings | QQQ, SPY, NVDA only if liquid |
| Market condition | Low IV, range-bound, no major near-term directional conviction |
| Structure | Buy back-month option, sell front-month option near ATM or slightly OTM |
| Front DTE | 7–21 DTE |
| Back DTE | 30–60 DTE |
| Profit target | 15%–30% of debit |
| Stop loss | 25%–40% of debit |
| Avoid | Earnings unless intentionally designed event calendar |

---

## 9. Portfolio Risk Management

Single-trade requirements can be loosened. Portfolio-level risk cannot be loosened.

### 9.1 Small Account Risk Limits

For a $2,000 account:

| Rule | Limit |
|---|---:|
| Normal max risk per position | $50–$150 |
| Aggressive max risk per position | $200 only for high-score trades |
| Total open max loss | $600–$800 |
| Daily realized loss limit | $150–$250 |
| Weekly realized loss limit | $300–$500 |
| Max correlated bullish exposure | 30%–40% account max loss |
| Max SOXL exposure | 5%–10% per trade, 15% total |

### 9.2 Kill Switch

The bot must stop opening new trades if:

- Daily loss limit is hit
- Weekly loss limit is hit
- 3 consecutive losses occur
- Market gap exceeds threshold and regime classifier enters crash mode
- API data is stale or inconsistent
- Bid/ask spreads widen beyond max threshold
- Account net liquidation drops below safety floor

### 9.3 Position Sizing Formula

```python
base_risk = account_equity * base_position_risk_pct
risk_budget = base_risk * regime_multiplier * volatility_multiplier * score_multiplier
contracts = floor(risk_budget / max_loss_per_contract)
```

Recommended defaults:

```python
base_position_risk_pct = 0.05  # 5% for small account defined-risk trades
regime_multiplier = 0.5 to 1.25
volatility_multiplier = 0.75 to 1.25
score_multiplier = 0.5 to 1.5
```

---

## 10. Trade Management

### 10.1 Winner Management

Do not hold every trade for max profit.

Default:

- Credit spreads: close at 40%–60% max profit
- Iron condors: close at 25%–50% max profit
- Debit spreads: close or scale at +50% to +100%
- Calendars/diagonals: close at 15%–30% profit

### 10.2 Loser Management

Do not average down blindly.

Allowed actions:

1. Close
2. Reduce
3. Roll for same or lower risk
4. Hedge with opposite-side spread
5. Do nothing only if thesis remains valid and risk limit remains intact

Forbidden actions:

- Increase max loss to rescue a loser
- Convert defined risk into undefined risk
- Double size because loss looks temporary
- Move stop loss farther away without predefined rule

---

## 11. Strategy Selection Matrix

| Regime | IV Low | IV Medium | IV High |
|---|---|---|---|
| Bull Trend | Call debit spread / diagonal | Put credit spread + call debit spread | Put credit spread, smaller size |
| Range | Calendar / diagonal | Iron condor small | Iron condor / short premium |
| Bear Trend | Put debit spread | Call credit spread + put debit spread | Call credit spread, hedge, reduce bullish |
| Crash | Hedge only | Hedge only | No new short premium |

---

## 12. Bot Implementation Rules

The bot should not use one strategy only. It should choose from the strategy book based on regime and score.

### 12.1 Scan Cycle

Every scan cycle:

1. Update prices and option chains
2. Calculate indicators
3. Classify market regime
4. Score each eligible strategy
5. Reject trades below threshold
6. Calculate position size
7. Run order dry-run
8. Log candidate trade
9. Generate recommendation
10. Execute only if current deployment mode allows it

### 12.2 Deployment Modes

| Mode | Behavior |
|---|---|
| research | scan and log only |
| dry_run | generate simulated orders |
| assisted_live | generate order, require manual approval |
| limited_auto | small live order allowed under strict risk limits |
| full_auto | disabled until long-term validation passes |

---

## 13. Self-Improvement Layer

The LLM can review trades, but it cannot directly change live rules.

### 13.1 LLM Responsibilities

The LLM may:

- Summarize why a trade worked or failed
- Detect repeated mistake patterns
- Suggest candidate rule changes
- Generate weekly reports
- Flag strategy drift
- Explain whether losses came from signal, sizing, timing, liquidity, or regime mismatch

### 13.2 LLM Restrictions

The LLM may not:

- Increase position size in live trading
- Disable kill switch
- Remove stop-loss logic
- Convert defined-risk into undefined-risk trades
- Approve its own strategy changes
- Trade based on narrative only

### 13.3 Rule Promotion Pipeline

A candidate rule must pass:

1. Historical backtest
2. Out-of-sample test
3. Walk-forward test
4. Dry-run for at least 30 days or 50 trades
5. Small live test
6. Human approval

---

## 14. Performance Metrics

Track by strategy, by underlying, and by market regime.

Required metrics:

- Win rate
- Average winner
- Average loser
- Profit factor
- Expected value per trade
- Max drawdown
- Consecutive losses
- Return / max drawdown
- Sharpe / Sortino if enough data
- Slippage vs theoretical mid price
- Fill quality
- P&L by time of day
- P&L by IVR bucket
- P&L by DTE bucket
- P&L by delta bucket

Minimum promotion targets before live automation:

| Metric | Minimum Target |
|---|---:|
| Trades | 50+ dry-run trades |
| Profit Factor | > 1.20 |
| Max Drawdown | < 15% dry-run equity curve |
| Win Rate | Strategy-dependent, but ideally > 55% overall |
| Average Loss / Average Win | Must be controlled; avoid 5:1 loss/win profile |
| Slippage | Must not destroy theoretical edge |

---

## 15. Practical Starting Configuration

For a $2,000 account, start with:

```yaml
account_equity: 2000
max_total_open_risk: 700
max_daily_loss: 200
max_weekly_loss: 400
normal_position_risk: 100
aggressive_position_risk: 150
max_soxl_trade_risk: 150
min_trade_score: 60
high_quality_score: 80
credit_spread_profit_target: 0.50
iron_condor_profit_target: 0.35
debit_spread_profit_target: 0.75
calendar_profit_target: 0.25
credit_spread_stop_multiple: 2.5
debit_spread_stop_loss: 0.45
calendar_stop_loss: 0.35
```

---

## 16. Final Strategy Summary

The v2 system is not a single rule strategy. It is a multi-engine strategy portfolio.

Core idea:

- Use short premium when IV is attractive.
- Use calendars/diagonals when IV is low.
- Use debit spreads when trend is strong.
- Use hedges or no-trade mode when market is in crash/risk-off regime.
- Use scoring rather than perfect-condition filters.
- Let the bot trade more often, but cap total portfolio risk.
- Let the LLM improve research, not directly control live trading.

