# Tastytrade QQQ / NVDA / SOXL Bot Implementation Plan

This plan implements the early, local-first version described by `AGENTS.md`.

## Scope

The early version must run without broker credentials, default to `dry_run`, generate scored
defined-risk option candidates, pass every candidate through the risk engine, persist audit records,
and produce basic backtest metrics.

Live trading is out of scope. Broker submit support remains a disabled stub until a later human
approval milestone.

## Milestones

### 1. Project Skeleton

Status: complete.

- Python package under `src/trading_bot`
- Project-local `.venv`
- `pyproject.toml`
- CLI
- Config loader
- Default `dry_run` mode tests

### 2. Core Models and Risk Engine

Status: complete.

- Core option and candidate models
- Portfolio state
- Kill switch state
- Risk engine veto checks
- Unit tests for major rejection rules

### 3. Indicators and Regime Classifier

Status: complete.

- EMA, RSI, MACD, VWAP, realized volatility
- Market input model
- Regime decision with confidence and reason codes
- Degraded confidence when optional inputs are missing

### 4. Strategy Scoring and Candidate Generation

Status: complete.

- 100-point score breakdown
- Liquidity and event-risk scoring
- Put credit spread and call credit spread candidates
- Call debit spread and put debit spread candidates
- Candidate max-profit, max-loss, DTE, legs, and exit plans

### 5. Dry-Run Pipeline

Status: complete.

- Order builder
- Mock broker
- Dry-run executor
- JSONL audit logger for approved and rejected candidates
- Live submit stub that raises a clear exception

### 6. Basic Backtest Engine

Status: complete.

- Slippage and bid/ask assumptions
- Trade simulation from supplied candidate outcomes
- Metrics beyond win rate: total return, drawdown, profit factor, expectancy, average win/loss,
  worst trade, worst day/week, and consecutive losses

### 7. Tastytrade Adapter

Status: scaffold complete.

- Env-only credential loading
- Mockable HTTP client boundary
- Balance, position, option-chain, and quote fetch wrappers
- Local dry-run
- Live submit disabled

### 8. LLM Review

Status: complete.

- Structured JSON schema validation
- Mockable LLM client boundary
- Research-only prompt language
- No automatic config or strategy mutation

### 9. Documentation and Acceptance

Status: complete.

- README usage examples
- Known limitations
- Full test, lint, and format checks inside `.venv`

## Safety Invariants

- Default execution mode is always `dry_run`.
- `live` is not an accepted early-version mode.
- Strategies never call broker adapters directly.
- Every dry-run order must include a risk decision.
- Every broker submit method raises unless live trading is explicitly implemented in a later
  milestone.
- `.env` is ignored and `.env.example` contains placeholders only.
