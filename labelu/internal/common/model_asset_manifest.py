from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from labelu.internal.common.bm1688_compile_contract import profile_snapshot_digest


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def file_asset(path: Path | str | None, *, role: str, root: Path | None = None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
        stat = resolved.stat()
    except OSError:
        return None
    relative = None
    if root:
        try:
            relative = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            relative = None
    digest = _sha256_file(resolved)
    if not digest:
        return None
    return {
        "role": role,
        "path": str(resolved),
        "relative_path": relative,
        "bytes": int(stat.st_size),
        "sha256": digest,
    }


def _manifest_digest(payload: dict[str, Any]) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stable_artifact_identity(asset: dict[str, Any]) -> dict[str, Any]:
    """只保留跨节点复现所需字段，排除绝对路径和生成时刻。"""
    return {
        "role": asset.get("role"),
        "relative_path": asset.get("relative_path") or Path(
            str(asset.get("path") or "")
        ).name,
        "bytes": asset.get("bytes"),
        "sha256": asset.get("sha256"),
    }


def _stable_compiler_config(config: dict[str, Any]) -> dict[str, Any]:
    """工具链根目录属于节点证据，不应改变同一编译输入的执行签名。"""
    stable: dict[str, Any] = {}
    for key, value in sorted(config.items(), key=lambda item: str(item[0])):
        token = str(key).lower()
        if any(part in token for part in ("path", "root", "directory", "dir")):
            continue
        stable[str(key)] = value
    return stable


def _stable_dataset_file_sha256(path: Path, relative_path: str | None) -> str | None:
    """Hash dataset files without making the dataset root part of the identity."""
    if relative_path != "data.yaml":
        return _sha256_file(path)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return _sha256_file(path)
    if not isinstance(payload, dict):
        return _sha256_file(path)
    normalized = dict(payload)
    normalized["path"] = "."
    return _manifest_digest(normalized)


def _stable_dataset_file_bytes(path: Path, relative_path: str | None) -> int | None:
    if relative_path != "data.yaml":
        try:
            return int(path.stat().st_size)
        except OSError:
            return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        try:
            return int(path.stat().st_size)
        except OSError:
            return None
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["path"] = "."
    return len(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def build_dataset_manifest(
    dataset_root: Path,
    *,
    source_task_ids: Iterable[int],
    split_counts: dict[str, int],
    class_names: list[str],
    per_task_counts: dict[int, int] | None = None,
    sample_composition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    files = []
    for path in sorted(dataset_root.rglob("*")):
        if path.is_file():
            asset = file_asset(path, role="training_dataset_file", root=dataset_root)
            if asset:
                files.append(asset)
    payload: dict[str, Any] = {
        "manifest_version": "v1",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "dataset_root": str(dataset_root.resolve()),
        "source_task_ids": sorted({int(item) for item in source_task_ids}),
        "per_task_sample_counts": {
            str(key): int(value) for key, value in (per_task_counts or {}).items()
        },
        "split_counts": {key: int(value) for key, value in split_counts.items()},
        "class_names": [str(item) for item in class_names],
        "sample_composition": sample_composition or None,
        "file_count": len(files),
        "files": files,
    }
    stable_payload = {
        "manifest_version": "v2",
        "source_task_ids": payload["source_task_ids"],
        "split_counts": payload["split_counts"],
        "class_names": payload["class_names"],
        "files": [
            {
                "relative_path": item.get("relative_path"),
                "bytes": _stable_dataset_file_bytes(
                    dataset_root / str(item.get("relative_path") or ""),
                    item.get("relative_path"),
                ),
                "sha256": _stable_dataset_file_sha256(
                    dataset_root / str(item.get("relative_path") or ""),
                    item.get("relative_path"),
                ),
            }
            for item in files
        ],
    }
    payload["dataset_content_signature"] = _manifest_digest(stable_payload)
    payload["content_sha256"] = payload["dataset_content_signature"]
    return payload


def build_export_artifact_manifest(
    export_root: Path,
    *,
    train_job_id: int,
    export_id: int,
    source_dataset_manifest: dict[str, Any] | None,
    pt_path: Path | str | None,
    onnx_path: Path | str | None,
    bmodel_paths: Iterable[Path | str],
    calibration_dir: Path | str | None,
    compiler_config: dict[str, Any],
    compile_log: str | None,
    status: str,
    class_names: list[str] | None = None,
    label_set_signature: str | None = None,
    class_order_signature: str | None = None,
    data_yaml_sha256: str | None = None,
    dataset_content_signature: str | None = None,
    bm1688_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = []
    pt_asset = file_asset(pt_path, role="weights_pt", root=export_root)
    onnx_asset = file_asset(onnx_path, role="model_onnx", root=export_root)
    if pt_asset:
        artifacts.append(pt_asset)
    if onnx_asset:
        artifacts.append(onnx_asset)
    for path in sorted((Path(item) for item in bmodel_paths), key=lambda value: str(value)):
        asset = file_asset(path, role="model_bmodel", root=export_root)
        if asset:
            artifacts.append(asset)

    calibration_assets = []
    if calibration_dir:
        cali_path = Path(calibration_dir)
        for path in sorted(cali_path.rglob("*")) if cali_path.is_dir() else []:
            asset = file_asset(path, role="calibration_image", root=cali_path)
            if asset:
                calibration_assets.append(asset)

    profile_snapshot = dict(bm1688_profile) if isinstance(bm1688_profile, dict) else None
    profile_digest = profile_snapshot_digest(profile_snapshot) if profile_snapshot else None
    if profile_snapshot:
        # 允许调用方传入已签名快照，但以本次实际内容重算摘要，避免复制旧摘要。
        profile_snapshot["profile_digest"] = profile_digest
        profile_snapshot["snapshot_digest"] = profile_digest

    payload: dict[str, Any] = {
        "manifest_version": "v1",
        "manifest_schema_version": "v1",
        "manifest_id": f"export:{int(export_id)}",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "export_id": int(export_id),
        "train_job_id": int(train_job_id),
        "status": str(status),
        "export_root": str(export_root.resolve()),
        "source_dataset": source_dataset_manifest or {},
        "class_contract": {
            "class_names": [str(value) for value in (class_names or [])],
            "label_set_signature": label_set_signature,
            "class_order_signature": class_order_signature,
            "data_yaml_sha256": data_yaml_sha256,
            "dataset_content_signature": dataset_content_signature,
        },
        "bm1688_profile": profile_snapshot,
        "profile_digest": profile_digest,
        "artifacts": artifacts,
        "calibration": {
            "directory": str(Path(calibration_dir).resolve()) if calibration_dir else None,
            "file_count": len(calibration_assets),
            "files": calibration_assets,
        },
        "compiler": {
            "compiler_version": compiler_config.get("compiler_version")
            or compiler_config.get("tpu_mlir_python")
            or compiler_config.get("tpu_mlir_root"),
            "config": dict(compiler_config),
            "profile_digest": profile_digest,
            "compile_log_sha256": hashlib.sha256(
                str(compile_log or "").encode("utf-8")
            ).hexdigest(),
        },
    }
    stable_payload = {
        "manifest_version": "v2",
        "source_dataset_signature": (
            source_dataset_manifest or {}
        ).get("dataset_content_signature")
        or (source_dataset_manifest or {}).get("content_sha256"),
        "class_contract": {
            "class_names": payload["class_contract"]["class_names"],
            "label_set_signature": label_set_signature,
            "class_order_signature": class_order_signature,
            "dataset_content_signature": dataset_content_signature,
        },
        "artifacts": [
            _stable_artifact_identity(item) for item in artifacts
        ],
        "calibration": [
            _stable_artifact_identity(item) for item in calibration_assets
        ],
        "compiler": {
            "compiler_version": payload["compiler"]["compiler_version"],
            "config": _stable_compiler_config(compiler_config),
            "profile_digest": profile_digest,
        },
        "bm1688_profile": profile_snapshot,
    }
    payload["execution_signature"] = _manifest_digest(stable_payload)
    # content_sha256 remains the complete evidence digest for backward
    # compatibility; it includes paths, timestamps and compile-log evidence.
    payload["evidence_manifest_sha256"] = _manifest_digest(payload)
    payload["content_sha256"] = payload["evidence_manifest_sha256"]
    return payload
