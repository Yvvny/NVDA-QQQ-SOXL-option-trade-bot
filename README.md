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
.\.venv\Scripts\python.exe -m trading_bot research-export --date 2026-05-20
.\.venv\Scripts\python.exe -m trading_bot research-review --date 2026-05-20
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
trigger one dry-run scan, and show read-only tastytrade account balances/positions when OAuth
credentials are configured. It does not expose live order submission.

For the account panel, set tastytrade SDK v12+ OAuth values in `.env` or your shell:

```powershell
$env:TASTYTRADE_PROVIDER_SECRET="your_provider_secret"
$env:TASTYTRADE_REFRESH_TOKEN="your_refresh_token"
$env:TASTYTRADE_ACCOUNT_NUMBER="your_account_number"
$env:TASTYTRADE_IS_TEST="true"
```

The account panel calls the read-only SDK account endpoints for balances, positions with marks,
and trading status. Account numbers are masked in the UI.

## Windows Desktop Control App

For a no-terminal control window during development, double-click:

```text
TradingBotControl.pyw
```

The desktop window can start/stop the local web UI, open it in your browser, run one dry-run scan,
start/stop a repeated dry-run loop, and refresh the read-only tastytrade account status.

To build a standalone Windows executable:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,tastytrade]"
.\.venv\Scripts\python.exe -m PyInstaller --noconsole --onefile --name TradingBotControl --paths src TradingBotControl.pyw
```

The generated executable is:

```text
dist\TradingBotControl.exe
```

Put a `.env` file next to the executable when running outside the repo.

## Strict One-Month Paper Simulation

Run a 30-day virtual account test with a $2,000 starting equity and the strategy-spec compliance
gate enabled:

```powershell
.\.venv\Scripts\python.exe -m trading_bot paper-run --source tastytrade --symbols QQQ,NVDA,SOXL --starting-equity 2000 --cycles 0 --days 30 --interval-seconds 300 --strict-spec
```

`--strict-spec` applies the strategy-spec gate before paper entries. Hard rule failures reject the
candidate; unavailable IV-rank/price-action context is recorded as a strict warning in
`docs/reports/paper_audit.jsonl` so the 30-day result can be reviewed honestly.

## Read-Only Research Review Bot

The research tools read paper audit logs and write reports under `docs/reports/research/`.
They are research-only: reports may propose hypotheses and backtest tasks, but they cannot modify
strategy config, risk limits, position size, or live trading settings.

For ChatGPT Plus manual analysis without API billing, export a Markdown packet and paste it into
ChatGPT:

```powershell
.\.venv\Scripts\python.exe -m trading_bot research-export --date 2026-05-20
```

To download today's online ChatGPT export from the server to this local repo, run this from local
PowerShell, not from inside the SSH session:

```powershell
.\tools\download_today_research_export.ps1
```

The script uses the New York date by default and saves to `docs\reports\research\`. To download a
specific trading day:

```powershell
.\tools\download_today_research_export.ps1 -Date 2026-05-19
```

For automated JSON reports with OpenAI API billing, set an OpenAI API key in `.env` or your shell:


```powershell
$env:OPENAI_API_KEY="your_api_key"
$env:OPENAI_RESEARCH_MODEL="gpt-5.5"
.\.venv\Scripts\python.exe -m trading_bot research-review --date 2026-05-20
```

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
