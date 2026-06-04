from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_qqq_chain_archive",
        description="Pull QQQ chain spool files from the cloud server into a local archive.",
    )
    parser.add_argument("--ssh-key", required=True, help="Path to the SSH private key.")
    parser.add_argument("--remote", required=True, help="Remote in user@host form.")
    parser.add_argument("--remote-root", required=True, help="Remote project root, for example /opt/trading-bot.")
    parser.add_argument("--local-root", required=True, help="Local archive root, for example D:\\MarketData\\QQQ.")
    parser.add_argument(
        "--remote-spool-root",
        default="data_spool/qqq_option_chain",
        help="Remote spool root relative to the project root.",
    )
    parser.add_argument(
        "--delete-remote-after-verify",
        action="store_true",
        help="Delete verified remote raw snapshot files after local download and checksum validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    local_root = Path(args.local_root)
    local_root.mkdir(parents=True, exist_ok=True)
    remote_spool_root = f"{args.remote_root.rstrip('/')}/{args.remote_spool_root.strip('/')}"
    manifest_paths = _list_remote_manifests(args.ssh_key, args.remote, remote_spool_root)
    synced = 0
    for manifest_path in manifest_paths:
        manifest = _read_remote_json(args.ssh_key, args.remote, f"{remote_spool_root}/{manifest_path}")
        _write_local_manifest(local_root, manifest_path, manifest)
        for entry in manifest.get("files", []):
            relative_path = str(entry["path"])
            size_bytes = int(entry["size_bytes"])
            sha256 = str(entry["sha256"])
            local_raw = local_root / "raw_jsonl" / relative_path
            if not _has_matching_file(local_raw, size_bytes, sha256):
                local_raw.parent.mkdir(parents=True, exist_ok=True)
                remote_file = f"{remote_spool_root}/{relative_path}"
                _scp_download(args.ssh_key, args.remote, remote_file, local_raw)
            if not _has_matching_file(local_raw, size_bytes, sha256):
                raise RuntimeError(f"Downloaded file failed verification: {local_raw}")
            _gzip_copy(local_raw, local_raw.with_suffix(local_raw.suffix + ".gz"))
            if args.delete_remote_after_verify:
                remote_file = f"{remote_spool_root}/{relative_path}"
                _delete_remote_file(args.ssh_key, args.remote, remote_file)
            synced += 1
    print(
        json.dumps(
            {
                "manifest_count": len(manifest_paths),
                "files_synced_or_verified": synced,
                "local_root": str(local_root),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _list_remote_manifests(ssh_key: str, remote: str, remote_spool_root: str) -> list[str]:
    command = (
        f"if [ -d {remote_spool_root}/manifests ]; then "
        f"cd {remote_spool_root} && find manifests -type f -name '*.json' | sort; "
        "fi"
    )
    result = _run(["ssh", "-i", ssh_key, remote, command], check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _read_remote_json(ssh_key: str, remote: str, remote_path: str) -> dict:
    result = _run(["ssh", "-i", ssh_key, remote, f"cat {remote_path}"])
    return json.loads(result.stdout)


def _write_local_manifest(local_root: Path, manifest_path: str, manifest: dict) -> None:
    path = local_root / "manifests" / manifest_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _has_matching_file(path: Path, size_bytes: int, sha256: str) -> bool:
    return path.exists() and path.stat().st_size == size_bytes and _sha256(path) == sha256


def _scp_download(ssh_key: str, remote: str, remote_path: str, local_path: Path) -> None:
    _run(["scp", "-i", ssh_key, f"{remote}:{remote_path}", str(local_path)])


def _delete_remote_file(ssh_key: str, remote: str, remote_path: str) -> None:
    _run(["ssh", "-i", ssh_key, remote, f"rm -f {remote_path}"])


def _gzip_copy(source: Path, destination: Path) -> None:
    with source.open("rb") as source_handle, gzip.open(destination, "wb") as gzip_handle:
        shutil.copyfileobj(source_handle, gzip_handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
