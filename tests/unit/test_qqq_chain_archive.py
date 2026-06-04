from datetime import UTC, date, datetime
from pathlib import Path

from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract, UnderlyingQuote
from trading_bot.data.qqq_chain_archive import archive_full_chain_snapshot
from trading_bot.data.tastytrade_source import (
    TastytradeFullChainSnapshot,
    TastytradeMarketDataDiagnostics,
    _chunk_symbols,
)


def test_archive_full_chain_snapshot_writes_raw_diagnostics_and_manifest(tmp_path):
    snapshot = TastytradeFullChainSnapshot(
        symbol="QQQ",
        collected_at=datetime(2026, 6, 3, 9, 35, tzinfo=UTC),
        expirations=(date(2026, 6, 19), date(2026, 6, 26)),
        underlying_quote=UnderlyingQuote(
            symbol="QQQ",
            timestamp=datetime(2026, 6, 3, 9, 35, tzinfo=UTC),
            bid=510.0,
            ask=510.2,
            last=510.1,
        ),
        option_contracts=(
            OptionContract(
                symbol="QQQ 2026-06-19 450 put",
                underlying="QQQ",
                expiration=date(2026, 6, 19),
                strike=450,
                option_type=OptionType.PUT,
                bid=0.45,
                ask=0.55,
                mid=0.50,
                delta=-0.25,
                gamma=0.01,
                theta=-0.02,
                vega=0.03,
                iv=0.40,
                volume=100,
                open_interest=1000,
            ),
        ),
        market_data_diagnostics=TastytradeMarketDataDiagnostics(
            subscribed_option_contracts=1,
            received_option_quotes=1,
            received_greeks=1,
            required_option_quotes=1,
            required_greeks=1,
            market_data_incomplete=False,
        ),
    )

    result = archive_full_chain_snapshot(snapshot, spool_root=tmp_path)

    assert result.contract_count == 1
    assert result.expirations_count == 2
    assert result.paths.raw_file.exists()
    assert result.paths.diagnostics_file.exists()
    assert result.paths.manifest_file.exists()
    assert "qqq_chain_2026-06-03_0935.jsonl" in result.paths.raw_file.name
    manifest_text = result.paths.manifest_file.read_text(encoding="utf-8")
    assert "raw/2026-06-03/qqq_chain_2026-06-03_0935.jsonl" in manifest_text
    assert result.paths.manifest_file.name == "qqq_manifest_2026-06-03.json"


def test_chunk_symbols_splits_streamer_symbols_into_batches():
    symbols = tuple(f".QQQ{i}" for i in range(7))

    chunks = _chunk_symbols(symbols, 3)

    assert chunks == [
        (".QQQ0", ".QQQ1", ".QQQ2"),
        (".QQQ3", ".QQQ4", ".QQQ5"),
        (".QQQ6",),
    ]
