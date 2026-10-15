"""Small file safety helpers shared by core services."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse


def fsync_file(path: str | os.PathLike[str]) -> None:
    """Flush file contents to disk when the platform supports it."""
    with open(path, "rb+") as fh:
        fh.flush()
        os.fsync(fh.fileno())


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Write bytes via a temp file in the same directory, then replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Write text atomically."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    indent: int = 2,
) -> None:
    """Serialize JSON and write it atomically."""
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text, encoding="utf-8")


def sha256_file(path: str | os.PathLike[str], chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 hex digest for a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sanitize_url_for_log(url: str) -> str:
    """Return a URL with query and fragment removed for logs."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def is_https_url(url: str) -> bool:
    """Return True if the URL uses HTTPS."""
    return urlparse(url).scheme.lower() == "https"
