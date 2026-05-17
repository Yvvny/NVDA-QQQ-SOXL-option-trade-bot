# Tastytrade QQQ / NVDA / SOXL Trading Bot

This repository is for a research-first, dry-run-first systematic options trading assistant.

The early version intentionally does not implement live order submission. The default execution
mode is `dry_run`, and future order routing must pass through portfolio risk controls before any
broker adapter is allowed to act.

## Local Development

Use the project-local virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## Commands

```powershell
.\.venv\Scripts\python.exe -m trading_bot status
.\.venv\Scripts\python.exe -m trading_bot config
.\.venv\Scripts\python.exe -m trading_bot run-once
.\.venv\Scripts\python.exe -m trading_bot run --cycles 5 --interval-seconds 60
.\.venv\Scripts\python.exe -m trading_bot run-once --source tastytrade --symbol QQQ --target-dte 30
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m black --check .
```

`run-once` and `run` default to the `mock` data source. They generate dry-run candidates, pass them
through the risk engine, send them to the mock broker, and append JSONL audit records to
`docs/reports/trade_audit.jsonl`.

For real read-only tastytrade data, install the optional SDK package and set credentials in `.env`
or your shell environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[tastytrade]"
$env:TASTYTRADE_USERNAME="your_username"
$env:TASTYTRADE_PASSWORD="your_password"
$env:TASTYTRADE_IS_TEST="true"
.\.venv\Scripts\python.exe -m trading_bot run-once --source tastytrade --symbol QQQ --target-dte 30
```

The tastytrade source reads the option chain and subscribes to DXLink quote/Greeks events. It still
uses the local dry-run executor and disabled live-submit path.

For Windows Task Scheduler, point the action at:

```powershell
D:\Code\Bot Tastytrade\.venv\Scripts\python.exe
```

with arguments:

```powershell
-m trading_bot run-once --audit-log "D:\Code\Bot Tastytrade\docs\reports\trade_audit.jsonl"
```

and start in:

```powershell
D:\Code\Bot Tastytrade
```

## Implemented Early-Version Capabilities

- Default config loads in `dry_run` mode.
- Core option, candidate, risk decision, and portfolio models.
- Risk engine veto checks for forbidden and oversized trades.
- EMA, RSI, MACD, VWAP, realized volatility, and regime classification.
- 100-point strategy scoring.
- Candidate generation for put credit spreads, call credit spreads, call debit spreads, and put
  debit spreads.
- Order builder, mock broker, dry-run executor, and JSONL audit logging.
- Safe automatic dry-run runner with bounded loop support.
- Basic backtest metrics beyond win rate.
- LLM review JSON validation for research artifacts only.
- Tastytrade adapter scaffold with env credentials, mocked fetch methods, local dry-run, and disabled
  live submit.

## Known Limitations

- No real market-data download is implemented yet.
- Backtests consume supplied trade outcomes; they do not yet replay historical option chains.
- Tastytrade HTTP transport is intentionally injected/mocked; no production API client is wired.
- Live order submission is disabled by design.
