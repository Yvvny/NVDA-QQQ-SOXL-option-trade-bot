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
.\.venv\Scripts\python.exe -m trading_bot ui --host 127.0.0.1 --port 8765
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m black --check .
```

`run-once` and `run` default to the `mock` data source. They generate dry-run candidates, pass them
through the risk engine, send them to the mock broker, and append JSONL audit records to
`docs/reports/trade_audit.jsonl`.

## Local Web UI

Start the local control UI:

```powershell
.\.venv\Scripts\python.exe -m trading_bot ui --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765`. The UI can view safety status, read recent audit records,
and trigger one dry-run scan. It does not expose live order submission.

## Tastytrade Read-Only Data

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

## Windows Task Scheduler

Point the action at:

```powershell
D:\Code\NVDA-QQQ-SOXL-option-trade-bot\.venv\Scripts\python.exe
```

with arguments:

```powershell
-m trading_bot run-once --audit-log "D:\Code\NVDA-QQQ-SOXL-option-trade-bot\docs\reports\trade_audit.jsonl"
```

and start in:

```powershell
D:\Code\NVDA-QQQ-SOXL-option-trade-bot
```

## Implemented Early-Version Capabilities

- Default config loads in `dry_run` mode.
- Core option, candidate, risk decision, and portfolio models.
- Risk engine veto checks for forbidden and oversized trades.
- EMA, RSI, MACD, VWAP, realized volatility, and regime classification.
- 100-point strategy scoring.
- Candidate generation for put credit spreads, call credit spreads, iron condors, call debit
  spreads, put debit spreads, and first-pass calendar/diagonal spreads.
- Order builder, mock broker, dry-run executor, and JSONL audit logging.
- Safe automatic dry-run runner with bounded loop support.
- Local web UI for status, safety flags, recent audit records, and one-click dry-run scans.
- Scenario-based backtest simulation with risk checks, fill assumptions, exit rules, fees,
  skipped-trade tracking, and metrics beyond win rate.
- LLM review JSON validation and research-only artifact persistence.
- Tastytrade adapter/source scaffold with env credentials, option-chain/quote mapping,
  retry/cache validation wrappers, local dry-run, and disabled live submit.

## Known Limitations

- Real tastytrade market-data access requires the optional `tastytrade` dependency and credentials;
  tests use deterministic mocks.
- Backtests now simulate candidate scenarios and exits, but they still require supplied historical
  option-position marks rather than downloading and replaying full historical option chains
  automatically.
- Tastytrade HTTP transport is intentionally injected/mocked; no production order client is wired.
- Live order submission is disabled by design.
