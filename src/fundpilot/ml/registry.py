"""File-based model registry: ``<root>/<task>/<version>/`` with metadata and artefacts.

The version id is a content hash of the training configuration and data fingerprint, so the
same data + config always produce the same version.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fundpilot.core.time import utc_now


def git_revision() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git resolved from PATH on purpose
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def version_id(identity: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[
        :12
    ]


def save(root: Path, task: str, identity: dict[str, Any], metadata: dict[str, Any]) -> Path:
    """Create the version directory and write ``metadata.json``. Returns the directory."""
    version = version_id(identity)
    directory = root / task / version
    directory.mkdir(parents=True, exist_ok=True)
    meta = {
        "task": task,
        "version": version,
        "created_at": utc_now().isoformat(),
        "git_revision": git_revision(),
        "identity": identity,
        **metadata,
    }
    (directory / "metadata.json").write_text(json.dumps(meta, indent=2, default=str) + "\n")
    return directory


def load_metadata(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((directory / "metadata.json").read_text())
    return data


def training_window(meta: dict[str, Any]) -> tuple[datetime, datetime]:
    start, end = meta["training_window"]
    return datetime.fromisoformat(start), datetime.fromisoformat(end)
