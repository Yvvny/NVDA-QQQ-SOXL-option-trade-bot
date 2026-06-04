from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from trading_bot.data.tastytrade_source import TastytradeFullChainSnapshot, TastytradeSdkDataSource


DEFAULT_QQQ_SPOOL_ROOT = Path("data_spool/qqq_option_chain")


@dataclass(frozen=True)
class QqqChainArchivePaths:
    raw_file: Path
    diagnostics_file: Path
    manifest_file: Path


@dataclass(frozen=True)
class QqqChainArchiveResult:
    snapshot_id: str
    symbol: str
    contract_count: int
    expirations_count: int
    paths: QqqChainArchivePaths
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "contract_count": self.contract_count,
            "expirations_count": self.expirations_count,
            "raw_file": str(self.paths.raw_file),
            "diagnostics_file": str(self.paths.diagnostics_file),
            "manifest_file": str(self.paths.manifest_file),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


class QqqFullChainCollector:
    def __init__(
        self,
        *,
        source: TastytradeSdkDataSource | None = None,
        spool_root: str | Path = DEFAULT_QQQ_SPOOL_ROOT,
    ) -> None:
        self.source = source or TastytradeSdkDataSource.from_env(max_contracts=500)
        self.spool_root = Path(spool_root)

    def collect_once(self, symbol: str = "QQQ") -> QqqChainArchiveResult:
        snapshot = self.source.fetch_full_chain_snapshot(symbol)
        return archive_full_chain_snapshot(snapshot, spool_root=self.spool_root)


def archive_full_chain_snapshot(
    snapshot: TastytradeFullChainSnapshot,
    *,
    spool_root: str | Path = DEFAULT_QQQ_SPOOL_ROOT,
) -> QqqChainArchiveResult:
    root = Path(spool_root)
    collected_at = snapshot.collected_at
    day_folder = collected_at.date().isoformat()
    timestamp_slug = collected_at.strftime("%Y-%m-%d_%H%M")
    snapshot_id = f"{snapshot.symbol.lower()}_{timestamp_slug}"

    raw_dir = root / "raw" / day_folder
    diagnostics_dir = root / "diagnostics" / day_folder
    manifests_dir = root / "manifests"
    raw_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    raw_file = raw_dir / f"{snapshot.symbol.lower()}_chain_{timestamp_slug}.jsonl"
    diagnostics_file = diagnostics_dir / f"{snapshot.symbol.lower()}_diagnostics_{day_folder}.jsonl"
    manifest_file = manifests_dir / f"{snapshot.symbol.lower()}_manifest_{day_folder}.json"

    _write_chain_file(raw_file, snapshot_id, snapshot)
    _append_diagnostics_file(diagnostics_file, snapshot_id, snapshot, raw_file)
    size_bytes = raw_file.stat().st_size
    sha256 = _sha256_file(raw_file)
    _update_manifest(
        manifest_file,
        root=root,
        snapshot=snapshot,
        raw_file=raw_file,
        size_bytes=size_bytes,
        sha256=sha256,
        diagnostics_file=diagnostics_file,
    )

    return QqqChainArchiveResult(
        snapshot_id=snapshot_id,
        symbol=snapshot.symbol,
        contract_count=len(snapshot.option_contracts),
        expirations_count=len(snapshot.expirations),
        paths=QqqChainArchivePaths(
            raw_file=raw_file,
            diagnostics_file=diagnostics_file,
            manifest_file=manifest_file,
        ),
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _write_chain_file(path: Path, snapshot_id: str, snapshot: TastytradeFullChainSnapshot) -> None:
    header = {
        "snapshot_id": snapshot_id,
        "collected_at": snapshot.collected_at.isoformat(),
        "timezone": str(snapshot.collected_at.tzinfo),
        "symbol": snapshot.symbol,
        "underlying_last": snapshot.underlying_quote.last if snapshot.underlying_quote else None,
        "underlying_bid": snapshot.underlying_quote.bid if snapshot.underlying_quote else None,
        "underlying_ask": snapshot.underlying_quote.ask if snapshot.underlying_quote else None,
        "source": "tastytrade_sdk",
        "expirations_count": len(snapshot.expirations),
        "option_contract_count": len(snapshot.option_contracts),
    }
    with path.open("w", encoding="utf-8") as handle:
        for contract in snapshot.option_contracts:
            row = {
                **header,
                "expiration": contract.expiration.isoformat(),
                "dte": max(0, (contract.expiration - snapshot.collected_at.date()).days),
                "strike": contract.strike,
                "option_type": contract.option_type.value,
                "contract_symbol": contract.symbol,
                "bid": contract.bid,
                "ask": contract.ask,
                "mid": contract.mid,
                "delta": contract.delta,
                "gamma": contract.gamma,
                "theta": contract.theta,
                "vega": contract.vega,
                "iv": contract.iv,
                "volume": contract.volume,
                "open_interest": contract.open_interest,
                "source_had_volume": contract.volume is not None,
                "source_had_open_interest": contract.open_interest is not None,
                "source_had_greeks": any(
                    value is not None for value in (contract.delta, contract.gamma, contract.theta, contract.vega, contract.iv)
                ),
            }
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _append_diagnostics_file(
    path: Path,
    snapshot_id: str,
    snapshot: TastytradeFullChainSnapshot,
    raw_file: Path,
) -> None:
    diagnostics = snapshot.market_data_diagnostics
    payload = {
        "snapshot_id": snapshot_id,
        "collected_at": snapshot.collected_at.isoformat(),
        "symbol": snapshot.symbol,
        "raw_file": raw_file.name,
        "received_contract_count": len(snapshot.option_contracts),
        "expirations_count": len(snapshot.expirations),
        "missing_bid_ask_count": sum(
            1 for contract in snapshot.option_contracts if contract.bid is None or contract.ask is None
        ),
        "missing_greeks_count": sum(
            1
            for contract in snapshot.option_contracts
            if all(value is None for value in (contract.delta, contract.gamma, contract.theta, contract.vega, contract.iv))
        ),
        "missing_volume_count": sum(1 for contract in snapshot.option_contracts if contract.volume is None),
        "missing_open_interest_count": sum(
            1 for contract in snapshot.option_contracts if contract.open_interest is None
        ),
        "market_data_incomplete": diagnostics.market_data_incomplete if diagnostics else None,
        "subscribed_option_contracts": diagnostics.subscribed_option_contracts if diagnostics else None,
        "received_option_quotes": diagnostics.received_option_quotes if diagnostics else None,
        "received_greeks": diagnostics.received_greeks if diagnostics else None,
        "required_option_quotes": diagnostics.required_option_quotes if diagnostics else None,
        "required_greeks": diagnostics.required_greeks if diagnostics else None,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _update_manifest(
    path: Path,
    *,
    root: Path,
    snapshot: TastytradeFullChainSnapshot,
    raw_file: Path,
    size_bytes: int,
    sha256: str,
    diagnostics_file: Path,
) -> None:
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        manifest = {"date": snapshot.collected_at.date().isoformat(), "symbol": snapshot.symbol, "files": []}
    relative_raw_path = raw_file.relative_to(root).as_posix()
    files = [item for item in manifest.get("files", []) if item.get("path") != relative_raw_path]
    files.append(
        {
            "path": relative_raw_path,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "diagnostics_file": diagnostics_file.relative_to(root).as_posix(),
            "collected_at": snapshot.collected_at.isoformat(),
            "contract_count": len(snapshot.option_contracts),
            "expirations_count": len(snapshot.expirations),
        }
    )
    manifest["files"] = sorted(files, key=lambda item: str(item["path"]))
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
