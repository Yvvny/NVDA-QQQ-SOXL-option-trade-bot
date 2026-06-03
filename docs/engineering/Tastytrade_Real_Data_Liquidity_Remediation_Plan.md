# Tastytrade Real-Data Liquidity Remediation Plan

## Context

The live cloud paper runner is active, but it is producing `0` strategy candidates for `QQQ`, `NVDA`, and `SOXL` under the real `tastytrade` data path.

Recent diagnostics show repeated:

- `no_eligible_contracts_after_liquidity_filters`
- `low_or_missing_volume`
- `low_or_missing_open_interest`
- `wide_bid_ask_spread`
- `missing_delta`

Direct inspection of the real `tastytrade` option objects on the cloud server showed that the current SDK option-chain objects do not provide usable `volume` or `open_interest` values for the scanned contracts. In the current code path, those fields map to `None`, and the liquidity filter treats `None` as a hard failure.

This means the current system is rejecting contracts due to missing data, not because it has proven that market liquidity is actually unacceptable.

## Root Cause

Current behavior:

1. `tastytrade` option-chain objects are mapped into `OptionContract`.
2. `volume` and `open_interest` are read from the SDK option objects.
3. In the real data path, those values are frequently `None`.
4. The liquidity filter treats missing `volume` and missing `open_interest` as blocking conditions.
5. All contracts become ineligible before strategy construction begins.

This is a data-contract mismatch between the real `tastytrade` feed and the current liquidity rules.

## Short-Term Plan

Goal: restore real-data candidate generation without removing core execution safety checks.

### Actions

1. Separate "missing liquidity metadata" from "proven low liquidity".
2. For the real `tastytrade` path, do not hard-reject contracts solely because `volume` or `open_interest` is missing.
3. Continue to require these hard checks:
   - bid and ask present
   - mid price computable
   - bid/ask spread percentage within configured bounds
   - required delta/Greeks present when needed
4. Record missing `volume` and `open_interest` as structured warnings in diagnostics and audit logs.
5. Add tests covering degraded real-data behavior where:
   - `volume=None`
   - `open_interest=None`
   - quotes and Greeks are otherwise valid
6. Re-run the cloud paper service and verify whether candidates begin to appear.

### Short-Term Acceptance Criteria

- Real `tastytrade` scans no longer fail solely because `volume` and `open_interest` are missing.
- Audit logs clearly distinguish:
  - missing metadata
  - true low-liquidity rejection
  - spread-based rejection
  - Greek/delta-based rejection
- The cloud paper runner begins producing either:
  - real candidates, or
  - narrower and more informative rejection reasons than the current blanket `0 eligible contracts`.

## Mid-Term Plan

Goal: restore stronger liquidity validation with better data quality.

### Actions

1. Add a secondary market-data source for option liquidity metadata:
   - `open_interest`
   - `volume`
2. Build a symbol-matching layer so external contract identifiers map reliably onto the internal `OptionContract` representation.
3. Merge the secondary metadata into the real `tastytrade` scan path before final liquidity filtering.
4. Introduce two explicit liquidity modes:
   - `strict_metadata`
   - `degraded_real_data`
5. Use `strict_metadata` when high-confidence `OI/volume` data is available.
6. Use `degraded_real_data` only when metadata is unavailable but quote-quality data is present.
7. Track diagnostics for metadata coverage by symbol and expiration.
8. Re-evaluate default thresholds after at least several trading sessions of new audit data.

### Mid-Term Acceptance Criteria

- The system can ingest `open_interest` and `volume` from a reliable source.
- Contract matching is stable across `QQQ`, `NVDA`, and `SOXL`.
- Real-data scans use metadata when available and degrade gracefully when unavailable.
- Liquidity filtering is no longer dependent on one provider exposing every required field in one object model.

## Guardrails

The following must remain unchanged during remediation:

- No live order submission by default
- Risk engine veto power
- Defined-risk structures only
- No 0DTE
- No market orders for options
- Full audit logging for both accepted and rejected candidates

## Recommended Execution Order

1. Implement the short-term degraded real-data handling.
2. Add tests for missing `OI/volume` with valid quotes and Greeks.
3. Deploy to the cloud paper runner.
4. Observe at least several sessions of new diagnostics.
5. Only then add the secondary metadata source and strict metadata mode.
