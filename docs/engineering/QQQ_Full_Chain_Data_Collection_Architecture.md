# QQQ Full-Chain Data Collection Architecture

## Objective

Run the trading bot in the cloud, capture the full `QQQ` options chain every 5 minutes, keep only a short rolling buffer on the cloud server, and persist the long-term research dataset on the local machine.

This design is optimized for:

- limited cloud disk space
- long-term historical dataset building
- future backtesting and RL research
- low operational risk

## High-Level Architecture

```text
Cloud server
  trading bot
  + QQQ chain collector
  + short-term spool storage
  + sync manifest
        |
        | scheduled pull
        v
Local machine
  long-term archive
  + compression
  + parquet conversion
  + research datasets
```

## Design Principles

- Keep trade execution and data collection close to the broker/data source in the cloud.
- Keep long-term storage local.
- Never depend on the local machine being online at all times.
- Use append-only files and daily partitions.
- Make sync idempotent so interrupted transfers can safely resume.
- Prefer a short cloud retention window and a long local retention window.

## Collection Scope

### Underlying

- `QQQ` only

### Frequency

- every `5` minutes during regular market hours

### Chain Scope

- full chain for all expirations exposed by the data source at collection time

### Recommended Session Window

- default: `09:30 - 16:00 America/New_York`
- optional future expansion:
  - premarket metadata for underlying only
  - no options-chain collection outside regular hours in the first version

## Data to Capture

The collector should write one dataset per snapshot.

### 1. Snapshot Header

One record per collection cycle:

- `snapshot_id`
- `collected_at`
- `timezone`
- `symbol`
- `underlying_last`
- `underlying_bid`
- `underlying_ask`
- `source`
- `market_session`
- `collector_version`
- `option_contract_count`
- `expirations_count`

### 2. Full Option Chain Rows

One record per option contract:

- `snapshot_id`
- `collected_at`
- `symbol`
- `expiration`
- `dte`
- `strike`
- `option_type`
- `contract_symbol`
- `bid`
- `ask`
- `mid`
- `delta`
- `gamma`
- `theta`
- `vega`
- `iv`
- `volume`
- `open_interest`
- `bid_size` if available
- `ask_size` if available
- `source_had_volume`
- `source_had_open_interest`
- `source_had_greeks`

### 3. Collection Diagnostics

One record per snapshot:

- `snapshot_id`
- `collected_at`
- `requested_symbol`
- `target_dte_hint`
- `received_contract_count`
- `missing_bid_ask_count`
- `missing_greeks_count`
- `missing_volume_count`
- `missing_open_interest_count`
- `timeout_flag`
- `error_message` if any

## File Layout

### Cloud Spool Layout

```text
/opt/trading-bot/data_spool/
  qqq_option_chain/
    raw/
      2026-06-03/
        qqq_chain_2026-06-03_0930.jsonl
        qqq_chain_2026-06-03_0935.jsonl
        ...
    diagnostics/
      2026-06-03/
        qqq_diagnostics_2026-06-03.jsonl
    manifests/
      qqq_manifest_2026-06-03.json
```

### Local Archive Layout

```text
D:\\MarketData\\QQQ\\
  raw_jsonl\\
    2026-06-03\\
      qqq_chain_2026-06-03_0930.jsonl.gz
      qqq_chain_2026-06-03_0935.jsonl.gz
  parquet\\
    year=2026\\
      month=06\\
        day=03\\
          chain.parquet
          diagnostics.parquet
  manifests\\
    qqq_manifest_2026-06-03.json
```

## Storage Format Strategy

### Cloud

Use plain `jsonl` or `jsonl.gz`.

Reason:

- simple append/write path
- easy debugging
- easy recovery after partial writes

### Local

Use two-stage storage:

1. land files as `jsonl.gz`
2. batch-convert to `parquet`

Reason:

- `jsonl.gz` is simple for transport and audit
- `parquet` is much better for analysis and later RL pipelines

## Expected Storage Size

For `QQQ` full chain every 5 minutes:

### Uncompressed JSONL estimate

- daily: `20MB - 80MB`
- monthly: `0.6GB - 2.4GB`
- yearly: `7GB - 30GB`

### Compressed local archive estimate

- daily: `8MB - 30MB`
- monthly: `250MB - 900MB`
- yearly: `3GB - 11GB`

### Cloud retention recommendation

Keep only:

- `3 - 7` days on the cloud server

That means cloud usage usually stays around:

- roughly `100MB - 600MB`

depending on compression and retention window.

## Sync Model

### Recommended Model

Cloud-first spool, local scheduled pull.

Flow:

1. cloud collector writes snapshot files locally on the server
2. a manifest file marks finished files for the day
3. local machine runs a scheduled sync job
4. local machine downloads only missing files
5. local machine verifies file size and checksum
6. local machine compresses or converts to parquet
7. cloud cleanup job deletes files older than retention threshold after successful sync

## Manifest Design

Each day has one manifest file, for example:

```json
{
  "date": "2026-06-03",
  "symbol": "QQQ",
  "files": [
    {
      "path": "raw/2026-06-03/qqq_chain_2026-06-03_0930.jsonl",
      "size_bytes": 182345,
      "sha256": "..."
    }
  ]
}
```

This allows:

- resumable sync
- integrity checks
- safe cleanup

## Scheduling

### Cloud Collection Schedule

- every 5 minutes during market hours

Recommended implementation:

- long-running collector process with clock-based gating
- or cron/systemd timer every 5 minutes

### Local Sync Schedule

Recommended:

- every 30 minutes during market hours
- one final sync after market close
- one overnight compression/parquet conversion job

This keeps local storage close to real time without requiring permanent connectivity.

## Failure Handling

### If the local machine is offline

- cloud continues collecting
- spool accumulates up to retention threshold
- local sync catches up later

### If a cloud write fails

- write a diagnostics error record
- do not write an incomplete manifest entry

### If sync is interrupted

- local job reads manifest again
- re-downloads only files not verified

### If cloud disk nears capacity

- alert condition should trigger before disk is full
- pause collection only after logging the failure

## Cleanup Policy

### Cloud

Delete files only when both are true:

- file age exceeds retention window
- local sync verification succeeded

Recommended retention:

- default `5 days`

### Local

- keep raw compressed files indefinitely if disk allows
- parquet is the preferred analysis layer
- optional yearly cold archive to external drive

## Minimal Implementation Plan

### Phase 1

- add QQQ full-chain collector
- write daily raw snapshot files on cloud
- write diagnostics files

### Phase 2

- add manifest generation
- add local sync script
- add checksum verification

### Phase 3

- add local compression
- add local parquet conversion
- add retention cleanup on cloud

### Phase 4

- add research dataset builders
- generate RL-ready or backtest-ready tables from parquet

## Recommended Initial Defaults

- symbol: `QQQ`
- frequency: `5 minutes`
- format on cloud: `jsonl`
- format on local: `jsonl.gz` then `parquet`
- cloud retention: `5 days`
- local sync cadence: `30 minutes`
- overnight final sync: enabled

## First-Version Commands

### Cloud: collect one snapshot

```bash
python -m trading_bot.cli collect-qqq-chain \
  --spool-root data_spool/qqq_option_chain \
  --max-contracts-per-batch 500
```

This writes:

- raw chain rows
- daily diagnostics
- a daily manifest with file size and sha256

Note:

- this command now safely skips outside regular market hours
- it is suitable for a simple every-5-minute systemd timer

### Local: pull archive from cloud

```bash
python tools/sync_qqq_chain_archive.py \
  --ssh-key C:\\Users\\YOUR_USER\\.ssh\\your_key \
  --remote ubuntu@YOUR_SERVER_IP \
  --remote-root /opt/trading-bot \
  --local-root D:\\MarketData\\QQQ \
  --delete-remote-after-verify
```

This does:

- fetch remote manifest files
- download missing raw snapshot files
- verify size and sha256
- write local manifest copies
- create local `.jsonl.gz` copies for long-term archive
- optionally delete remote raw files after successful verification

### Windows Local Automation

Two local helper scripts are provided:

- `tools/run_qqq_chain_sync.ps1`
- `tools/install_qqq_chain_sync_task.ps1`

Install the scheduled task once:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_qqq_chain_sync_task.ps1
```

Default task behavior:

- run every 30 minutes starting at `09:35`
- continue through the trading day
- run one final catch-up sync at `16:20`

Direct manual run:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_qqq_chain_sync.ps1
```

### Recommended Automation Direction

Preferred:

- local scheduled pull
- local verification
- local-triggered remote cleanup

Not preferred:

- cloud trying to push directly into the local machine

Reason:

- local machines are often offline
- inbound access to the local machine is fragile and less secure
- pull-based sync is easier to recover and audit

## Why This Design Fits the Current Bot

- The trading bot stays on the cloud where the market data connection already exists.
- Long-term storage pressure moves to the local machine.
- Data collection remains independent from the trade-decision loop.
- The dataset can later support:
  - exit matrix research
  - candidate-ranking research
  - sizing analysis
  - future bandit or constrained RL experiments
