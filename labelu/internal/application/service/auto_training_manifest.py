from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from labelu.internal.common.config import settings
from labelu.internal.domain.models.auto_training import AutoTrainingRound


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def persist_round_manifest(
    round_row: AutoTrainingRound, *, payload: dict[str, Any]
) -> tuple[str, str]:
    content = canonical_manifest_bytes(payload)
    digest = hashlib.sha256(content).hexdigest()
    relative = Path("auto_training") / f"plan-{round_row.plan_id}" / f"round-{round_row.id}" / "manifest.json"
    target = Path(settings.MEDIA_ROOT) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = target.read_bytes()
        if existing != content:
            raise RuntimeError(f"round manifest already exists with different content: {target}")
    else:
        temporary = target.with_suffix(".json.tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, target)
        except Exception:
            # A failed disk write must not leave a stale temporary artifact
            # that can be mistaken for a valid frozen-round manifest.
            temporary.unlink(missing_ok=True)
            raise
    return relative.as_posix(), digest


def verify_round_manifest(round_row: AutoTrainingRound) -> Path:
    if not round_row.manifest_rel_path or not round_row.manifest_sha256:
        raise RuntimeError("round manifest metadata is incomplete")
    target = Path(settings.MEDIA_ROOT) / round_row.manifest_rel_path
    if not target.is_file():
        raise RuntimeError(f"round manifest is missing: {target}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != round_row.manifest_sha256:
        raise RuntimeError(
            f"round manifest sha256 mismatch: expected={round_row.manifest_sha256} actual={actual}"
        )
    return target
