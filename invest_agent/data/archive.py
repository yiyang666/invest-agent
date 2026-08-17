"""Immutable raw payload archive for public market-data collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class RawBatchReceipt:
    payload_path: Path
    metadata_path: Path
    content_sha256: str


@dataclass(frozen=True)
class ArchivedRawBatch:
    provider_id: str
    batch_id: str
    payload: bytes
    content_type: str
    observed_at: datetime
    request_parameters: Mapping[str, str]
    content_sha256: str


class ImmutableArchiveConflict(RuntimeError):
    pass


def load_raw_payload(
    *, root: str | Path, provider_id: str, batch_id: str
) -> ArchivedRawBatch:
    """Load and verify an immutable archived payload for deterministic replay."""

    if not _SAFE_COMPONENT.fullmatch(provider_id) or not _SAFE_COMPONENT.fullmatch(batch_id):
        raise ValueError("provider_id and batch_id must be safe path components")
    base = Path(root) / provider_id / batch_id
    payload_path = base.with_suffix(".payload.gz")
    metadata_path = base.with_suffix(".metadata.json")
    if not payload_path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError(f"Raw archive is incomplete for batch {batch_id}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload = gzip.decompress(payload_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Raw archive cannot be decoded for batch {batch_id}: {exc}") from exc
    if metadata.get("provider_id") != provider_id or metadata.get("batch_id") != batch_id:
        raise ImmutableArchiveConflict(f"Raw archive identity mismatch for batch {batch_id}")
    content_hash = hashlib.sha256(payload).hexdigest()
    if metadata.get("content_sha256") != content_hash:
        raise ImmutableArchiveConflict(f"Raw archive hash mismatch for batch {batch_id}")
    observed_at = datetime.fromisoformat(str(metadata.get("observed_at", "")))
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError(f"Raw archive observed_at has no timezone for batch {batch_id}")
    request_parameters = metadata.get("request_parameters")
    if not isinstance(request_parameters, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request_parameters.items()
    ):
        raise ValueError(f"Raw archive request parameters are invalid for batch {batch_id}")
    return ArchivedRawBatch(
        provider_id=provider_id,
        batch_id=batch_id,
        payload=payload,
        content_type=str(metadata.get("content_type", "application/octet-stream")),
        observed_at=observed_at,
        request_parameters=request_parameters,
        content_sha256=content_hash,
    )


def archive_raw_payload(
    *,
    root: str | Path,
    provider_id: str,
    batch_id: str,
    payload: bytes,
    content_type: str,
    observed_at: datetime,
    request_parameters: Mapping[str, str],
) -> RawBatchReceipt:
    """Write a payload once; refuse a same batch ID with different content."""

    if not _SAFE_COMPONENT.fullmatch(provider_id) or not _SAFE_COMPONENT.fullmatch(batch_id):
        raise ValueError("provider_id and batch_id must be safe path components")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")

    content_hash = hashlib.sha256(payload).hexdigest()
    directory = Path(root) / provider_id
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    base = directory / batch_id
    payload_path = base.with_suffix(".payload.gz")
    metadata_path = base.with_suffix(".metadata.json")

    metadata = {
        "batch_id": batch_id,
        "provider_id": provider_id,
        "content_type": content_type,
        "observed_at": observed_at.isoformat(),
        "request_parameters": dict(sorted(request_parameters.items())),
        "content_sha256": content_hash,
        "payload_file": payload_path.name,
    }
    metadata_bytes = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    compressed_payload = gzip.compress(payload, mtime=0)

    if payload_path.exists() or metadata_path.exists():
        if not payload_path.exists() or not metadata_path.exists():
            raise ImmutableArchiveConflict(f"Incomplete existing archive for batch {batch_id}")
        existing_payload = gzip.decompress(payload_path.read_bytes())
        existing_metadata = metadata_path.read_bytes()
        if existing_payload != payload or existing_metadata != metadata_bytes:
            raise ImmutableArchiveConflict(f"Batch {batch_id} already exists with different content")
        return RawBatchReceipt(payload_path, metadata_path, content_hash)

    payload_tmp = payload_path.with_suffix(payload_path.suffix + ".tmp")
    metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    payload_tmp.write_bytes(compressed_payload)
    metadata_tmp.write_bytes(metadata_bytes)
    payload_tmp.replace(payload_path)
    metadata_tmp.replace(metadata_path)
    payload_path.chmod(0o600)
    metadata_path.chmod(0o600)
    return RawBatchReceipt(payload_path, metadata_path, content_hash)
