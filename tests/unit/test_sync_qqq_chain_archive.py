import gzip
import hashlib
import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tools" / "sync_qqq_chain_archive.py"
_SPEC = importlib.util.spec_from_file_location("sync_qqq_chain_archive", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_gzip_copy = _MODULE._gzip_copy
_has_matching_file = _MODULE._has_matching_file
build_parser = _MODULE.build_parser


def test_has_matching_file_validates_size_and_sha256(tmp_path):
    file_path = tmp_path / "sample.jsonl"
    file_path.write_text('{"x":1}\n', encoding="utf-8")
    raw = file_path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()

    assert _has_matching_file(file_path, len(raw), sha256) is True
    assert _has_matching_file(file_path, len(raw) + 1, sha256) is False


def test_gzip_copy_writes_readable_compressed_copy(tmp_path):
    source = tmp_path / "sample.jsonl"
    destination = tmp_path / "sample.jsonl.gz"
    source.write_text('{"x":1}\n{"x":2}\n', encoding="utf-8")

    _gzip_copy(source, destination)

    assert destination.exists()
    with gzip.open(destination, "rt", encoding="utf-8") as handle:
        assert handle.read() == '{"x":1}\n{"x":2}\n'


def test_sync_parser_supports_delete_remote_flag():
    args = build_parser().parse_args(
        [
            "--ssh-key",
            "key",
            "--remote",
            "ubuntu@example",
            "--remote-root",
            "/opt/trading-bot",
            "--local-root",
            "D:\\MarketData\\QQQ",
            "--delete-remote-after-verify",
        ]
    )

    assert args.delete_remote_after_verify is True
