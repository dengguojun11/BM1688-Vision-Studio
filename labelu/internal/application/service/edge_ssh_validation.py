"""从 LabelU 部署机 SSH 到算能盒执行板端验证 + bmodel SOP 评估。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.application.service.yolo_bm1688_export import (
    _export_dir_for_row,
    _job_work_root,
)
from labelu.internal.application.service.yolo_sop_model_evaluation import (
    _compare_pt_bmodel,
    enrich_sop_eval_summary,
    load_server_sop_eval_summary,
)
from labelu.internal.common import edge_ssh_config as edge_cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.edge_remote_paths import (
    edge_remote_paths,
    remote_edge_path_guard_command,
    require_edge_path,
)
from labelu.internal.common.edge_sophon_demo import (
    DEFAULT_SOPHON_DEMO_ROOT,
    probe_sophon_demo_root_via_ssh,
)
from labelu.internal.common.archive import safe_extract_zip
from labelu.internal.common.edge_ssh_client import (
    EdgeSshError,
    EdgeSshTransferInterrupted,
    sftp_download,
    sftp_download_tree,
    sftp_stat_size,
    sftp_upload_resumable,
    ssh_connect,
    ssh_mkdir_p,
    ssh_run,
)
from labelu.internal.common.edge_ssh_probe import probe_and_persist_edge_ssh_target
from labelu.internal.common.bm1688_compile_contract import profile_snapshot_digest
from labelu.internal.common.model_manifest_signature import sign_model_manifest_ed25519
from labelu.internal.common.fixed_eval_signature import fixed_eval_content_signature

_export_edge_ssh_locks_guard = threading.Lock()
_export_edge_ssh_locks: Dict[int, threading.Lock] = {}
_EDGE_REQUIRED_PIP_REQUIREMENTS = ("tqdm==4.64.1",)
EDGE_BUNDLE_UPLOAD_ATTEMPTS = 8
EDGE_BUNDLE_UPLOAD_RETRY_DELAY_SECONDS = 10.0


def _export_edge_ssh_lock(export_id: int) -> threading.Lock:
    with _export_edge_ssh_locks_guard:
        lock = _export_edge_ssh_locks.get(export_id)
        if lock is None:
            lock = threading.Lock()
            _export_edge_ssh_locks[export_id] = lock
        return lock


def _artifact_profile_expectation(export_row: Any) -> Dict[str, Any]:
    """从服务器导出清单生成 SSH 验证的档案摘要期望值。"""
    raw = getattr(export_row, "artifact_manifest_json", None)
    try:
        manifest = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        return {}
    profile = manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = manifest.get("profile_snapshot")
    expected = {}
    if isinstance(profile, dict):
        digest = profile_snapshot_digest(profile)
        if digest:
            expected["profile_digest"] = digest
    execution_signature = str(manifest.get("execution_signature") or "").strip()
    if execution_signature:
        expected["execution_generation"] = execution_signature
        expected["require_execution_generation_state"] = True
    compiler_block = manifest.get("compiler")
    compiler_config = (
        compiler_block.get("config")
        if isinstance(compiler_block, dict)
        else manifest.get("compiler_config")
    )
    if isinstance(compiler_config, dict) and isinstance(compiler_config.get("quality_rule"), dict):
        expected["quality_rule"] = compiler_config["quality_rule"]
    return expected


def _edge_ssh_configured() -> bool:
    return edge_cfg_io.is_edge_ssh_configured()


def _mark_active_device_used() -> None:
    try:
        from labelu.internal.common import edge_device_registry

        edge_device_registry.mark_active_device_used()
    except Exception:
        return


def _record_active_device_validation_status(status_value: str) -> None:
    try:
        from labelu.internal.common import edge_device_registry

        edge_device_registry.mark_active_device_validation(
            str(status_value or "").strip() or "unknown"
        )
    except Exception:
        return


def _export_needs_edge_ssh_validation(export_id: int) -> bool:
    """尚无 bmodel SOP 报告时需在边端补跑。"""
    root = _export_dir_for_row(export_id)
    report = root / "server_eval" / "results_bmodel" / "evaluation_report.md"
    variant_root = root / "server_eval" / "results_bmodel"
    all_variant_reports = all(
        (variant_root / "evaluation_report.md").is_file()
        and (variant_root / "variants" / tag / "evaluation_report.md").is_file()
        for tag in ("fp32", "fp16")
    )
    if report.is_file() and report.stat().st_size > 32 and all_variant_reports:
        return False
    summary = root / "server_eval" / "sop_eval_summary.json"
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            cmp_bm = (data or {}).get("comparison_pt_bmodel") or {}
            has_comparison = cmp_bm.get("status") in (
                "ok",
                "warn",
                "fail",
            ) or cmp_bm.get("verdict") in ("pass", "review", "fail")
            # A legacy summary without a local report is still considered
            # complete; a real INT8 report, however, must be supplemented by
            # the new FP32/FP16 variant reports.
            if has_comparison and not (
                report.is_file()
                and report.stat().st_size > 32
                and not all_variant_reports
            ):
                return False
        except (json.JSONDecodeError, OSError):
            pass
    return True


def run_edge_ssh_catchup_sync(*, limit: int = 30) -> Dict[str, Any]:
    """配置保存后或运维触发：为尚未有 bmodel 报告的 COMPLETED 导出自动 SSH 验证。"""
    from sqlalchemy import desc

    from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
    from labelu.internal.common.db import session_scope
    from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export

    if not edge_cfg_io.should_auto_run_edge_ssh():
        return {
            "status": "skipped",
            "reason": "auto_after_export 未启用或边端 SSH 未配置",
        }

    probe = probe_and_persist_edge_ssh_target()
    if probe.get("status") != "ok":
        return {
            "status": "skipped",
            "reason": f"edge_ssh_probe_failed:{probe.get('code')}:{probe.get('stage')}",
            "probe": probe,
        }

    ran: List[int] = []
    skipped: List[int] = []
    errors: Dict[int, str] = {}

    with session_scope() as db:
        rows = (
            db.query(YoloBm1688Export)
            .filter(YoloBm1688Export.status == "COMPLETED")
            .order_by(desc(YoloBm1688Export.id))
            .limit(max(1, int(limit)))
            .all()
        )
        pending = [r for r in rows if _export_needs_edge_ssh_validation(int(r.id))]

    for row in pending:
        eid = int(row.id)
        try:
            with session_scope() as db:
                summary = run_edge_ssh_validation_sync(
                    db,
                    export_id=eid,
                    yolo_train_job_id=int(row.yolo_train_job_id),
                    imgsz=int(row.imgsz or 640),
                    skip_if_busy=True,
                )
            if summary.get("status") in ("ok", "fail"):
                ran.append(eid)
            else:
                skipped.append(eid)
        except Exception as exc:
            logger.exception("edge ssh catchup export_id={}", eid)
            errors[eid] = str(exc)[:500]

    return {
        "status": "ok",
        "ran_export_ids": ran,
        "skipped_export_ids": skipped,
        "errors": errors,
        "pending_count": len(pending),
    }


def schedule_edge_ssh_catchup_background(*, limit: int = 30) -> None:
    """后台线程执行 catchup，避免阻塞保存配置 API。"""

    def _worker() -> None:
        try:
            run_edge_ssh_catchup_sync(limit=limit)
        except Exception:
            logger.exception("edge ssh catchup background failed")

    threading.Thread(target=_worker, name="edge-ssh-catchup", daemon=True).start()


def _staging_bundle_dir(export_id: int) -> Path:
    return _export_dir_for_row(export_id) / "edge_ssh_staging"


def _safe_eval_filename(index: int, src: Path) -> str:
    suffix = src.suffix.lower() or ".jpg"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem or "")
    stem = stem.strip("._-") or "image"
    return f"{index:04d}_{stem}{suffix}"


def _prepare_edge_runtime_dependency_bundle(staging: Path) -> Dict[str, Any]:
    """为 Python 3.6/aarch64 边端准备可离线安装的纯 Python wheel。"""
    requirements_path = staging / "edge_requirements.txt"
    wheel_dir = staging / "edge_wheels"
    requirements_path.write_text(
        "\n".join(_EDGE_REQUIRED_PIP_REQUIREMENTS) + "\n", encoding="utf-8"
    )
    wheel_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--no-deps",
        "--only-binary=:all:",
        "--dest",
        str(wheel_dir),
        *_EDGE_REQUIRED_PIP_REQUIREMENTS,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=180,
            check=False,
        )
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        code = int(proc.returncode)
    except Exception as exc:
        output = str(exc)
        code = 1

    wheels = sorted(p.name for p in wheel_dir.glob("*.whl"))
    meta = {
        "status": "ready" if code == 0 and wheels else "unavailable",
        "requirements": list(_EDGE_REQUIRED_PIP_REQUIREMENTS),
        "wheels": wheels,
        "download_exit_code": code,
        "download_output_tail": output[-2000:],
    }
    (staging / "edge_dependency_prepare.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if meta["status"] != "ready":
        logger.warning(
            "边端离线依赖包准备失败，在线安装仍会优先尝试: {}",
            meta["download_output_tail"],
        )
    return meta


def _copy_eval_dataset(job_id: int, dest: Path) -> int:
    """复制 test/val 图与 labelsxml 到 eval_data/。"""
    root = _job_work_root(job_id) / "dataset"
    n = 0
    for split in ("test", "val"):
        img_src = root / "images" / split
        if not img_src.is_dir() or not any(img_src.iterdir()):
            continue
        img_dst = dest / "images" / split
        xml_src = root / "labelsxml" / split
        if not xml_src.is_dir():
            xml_src = root / "labelsxml" / "test"
        img_dst.mkdir(parents=True, exist_ok=True)
        image_name_map: Dict[str, str] = {}
        for fp in sorted(img_src.iterdir()):
            if fp.is_file():
                dst_name = _safe_eval_filename(n, fp)
                shutil.copy2(fp, img_dst / dst_name)
                image_name_map[fp.stem] = Path(dst_name).stem
                n += 1
        if xml_src.is_dir():
            xml_dst = dest / "labelsxml" / split
            xml_dst.mkdir(parents=True, exist_ok=True)
            for fp in sorted(xml_src.iterdir()):
                if not fp.is_file():
                    continue
                dst_stem = image_name_map.get(fp.stem)
                if dst_stem:
                    shutil.copy2(
                        fp, xml_dst / f"{dst_stem}{fp.suffix.lower() or '.xml'}"
                    )
        break
    return n


def _read_class_names(job_id: int) -> list:
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        _read_class_names_from_data_yaml,
    )

    root = _job_work_root(job_id) / "dataset"
    return _read_class_names_from_data_yaml(root) if root.is_dir() else []


def _copy_board_eval_dataset(board_eval: Path, dest: Path) -> dict[str, Any] | None:
    """Reuse the Board Agent fixed pack for the SSH compatibility channel.

    Both channels may use different transport layouts, but they must consume
    the same images and labels.  The returned manifest is only used as source
    metadata; the SSH bundle still writes its own root manifest for the legacy
    runner layout.
    """
    manifest_path = board_eval / "manifest.json"
    if not manifest_path.is_file() or not (board_eval / "images").is_dir():
        return None
    try:
        source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(source_manifest, dict):
        return None
    if str(source_manifest.get("schema_version") or "") != "fixed_eval.v1":
        return None
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    for source in sorted(path for path in board_eval.rglob("*") if path.is_file()):
        relative = source.relative_to(board_eval)
        parts = relative.parts
        if not parts or parts[0] == "manifest.json":
            continue
        if parts[0] == "images":
            target = dest / "images" / "test" / relative.name
        elif parts[0] in {"labels", "labelsxml"}:
            target = dest / parts[0] / "test" / relative.name
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return source_manifest


def build_edge_validation_bundle(
    db: Session,
    *,
    export_id: int,
    yolo_train_job_id: int,
    imgsz: int,
) -> Path:
    """打包 board 模型 + 验证图 + 脚本，返回 bundle.tar.gz 路径。"""
    from labelu.internal.application.service import board_agent

    staging = _staging_bundle_dir(export_id)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    zip_path = board_agent.build_models_zip_path(export_id, db)
    safe_extract_zip(zip_path, staging)

    eval_dest = staging / "eval_data"
    board_eval_manifest = _copy_board_eval_dataset(staging / "board_eval", eval_dest)
    if board_eval_manifest is not None:
        image_dir = eval_dest / "images"
        img_n = sum(1 for path in image_dir.rglob("*") if path.is_file())
    else:
        img_n = _copy_eval_dataset(yolo_train_job_id, eval_dest)
    # Legacy SSH bundles did not have the newer pack manifest. Create the same
    # immutable fixed-evaluation identity from the copied image/label bytes so
    # PT and all BModel precisions cannot silently evaluate different files.
    eval_files = []
    for path in sorted(p for p in eval_dest.rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        eval_files.append(
            {
                "path": path.relative_to(eval_dest).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest,
            }
        )
    image_paths = sorted(
        path
        for path in (eval_dest / "images").rglob("*")
        if path.is_file()
    )
    label_paths = {}
    for label_root in (eval_dest / "labelsxml", eval_dest / "labels"):
        for path in label_root.rglob("*") if label_root.is_dir() else []:
            if path.is_file():
                label_paths.setdefault(path.stem, path)
    label_mappings = [
        {
            "image": path.relative_to(eval_dest).as_posix(),
            "path": label_paths[path.stem].relative_to(eval_dest).as_posix(),
            "has_annotation": True,
            "has_annotation_file": True,
        }
        for path in image_paths
        if path.stem in label_paths
    ]
    eval_manifest = {
        "schema_version": "fixed_eval.v1",
        "image_count": len(image_paths),
        "labeled_image_count": len(label_mappings),
        "unlabeled_image_count": max(0, len(image_paths) - len(label_mappings)),
        "label_mappings": label_mappings,
        "files": eval_files,
        "class_names": (
            board_eval_manifest.get("class_names") or []
            if isinstance(board_eval_manifest, dict)
            else _read_class_names(yolo_train_job_id)
        ),
    }
    eval_manifest["fixed_eval_signature"] = fixed_eval_content_signature(eval_manifest)
    if isinstance(board_eval_manifest, dict):
        expected_fixed_signature = str(
            board_eval_manifest.get("fixed_eval_signature") or ""
        ).strip()
        if expected_fixed_signature and expected_fixed_signature != eval_manifest["fixed_eval_signature"]:
            raise RuntimeError("Board Agent 与 SSH 固定评估集内容不一致")
    eval_manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            eval_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    (staging / "manifest.json").write_text(
        json.dumps(eval_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from labelu.internal.common.edge_script_paths import (
        resolve_board_agent_script,
        resolve_edge_board_validate_runner,
        resolve_python_test_evaluation,
        resolve_sop_eval_dir,
    )

    ops_src = resolve_board_agent_script()
    runner_src = resolve_edge_board_validate_runner()
    sop_src = resolve_sop_eval_dir()
    docs_pt = resolve_python_test_evaluation()

    dest_scripts = staging / "scripts"
    (dest_scripts / "ops").mkdir(parents=True, exist_ok=True)
    if ops_src.is_file():
        shutil.copy2(ops_src, dest_scripts / "ops" / "labelu_board_agent.py")
    if runner_src.is_file():
        shutil.copy2(runner_src, dest_scripts / "edge_board_validate_runner.py")
    sop_dest = dest_scripts / "sop_eval"
    sop_dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "evaluation_utils.py",
        "yolo_eval_postprocess.py",
        "cloud_based_evaluation.py",
        "onnx_based_evaluation.py",
        "sophon_bmodel_evaluation.py",
        "edge_performance_probe.py",
    ):
        p = sop_src / name
        if p.is_file():
            shutil.copy2(p, sop_dest / name)
    if docs_pt.is_file():
        shutil.copy2(docs_pt, sop_dest / "pythonTest_evaluation.py")
    elif (sop_dest / "pythonTest_evaluation.py").exists() is False:
        logger.warning("pythonTest_evaluation.py 未找到，边端 mAP 评估将跳过")

    _prepare_edge_runtime_dependency_bundle(staging)

    cfg = edge_cfg_io.load_edge_ssh_config()
    from labelu.internal.adapter.persistence import crud_yolo_bm1688_export

    export_row = crud_yolo_bm1688_export.get(db, export_id)
    threshold_contract = board_agent.build_board_threshold_contract(export_row)
    artifact_manifest = {}
    try:
        artifact_manifest = json.loads(
            getattr(export_row, "artifact_manifest_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        artifact_manifest = {}
    artifact_signature = str(
        artifact_manifest.get("execution_signature")
        or getattr(export_row, "execution_signature", None)
        or ""
    ).strip() or None
    profile = artifact_manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = artifact_manifest.get("profile_snapshot")
    profile_digest = (
        profile_snapshot_digest(profile) if isinstance(profile, dict) else None
    )
    run_cfg = {
        "schema_version": threshold_contract.get("schema_version"),
        "export_id": export_id,
        "model_identity": {
            "model_id": str(
                getattr(export_row, "published_model_id", None) or export_id
            ),
            "export_id": int(export_id),
            "aicm_model_id": getattr(export_row, "published_model_id", None),
            "model_id_source": (
                "aicm_model_id"
                if getattr(export_row, "published_model_id", None)
                else "export_id_pre_publish"
            ),
        },
        "artifact_execution_signature": artifact_signature,
        "artifact_manifest": artifact_manifest or None,
        "bm1688_profile": profile,
        "profile_digest": profile_digest,
        "edge_execution_contract": {
            "compile_location": "server",
            "device_action": "load_and_infer",
            "device_must_not_compile": True,
        },
        "required_bmodel_precisions": ["fp32", "fp16", "int8"],
        "execution_generation": str(
            artifact_manifest.get("execution_signature")
            or artifact_manifest.get("manifest_id")
            or "export:{}".format(int(export_id))
        ),
        "execution_generation_scope": (
            "aicm:{}".format(getattr(export_row, "published_model_id", None))
            if getattr(export_row, "published_model_id", None)
            else "export:{}".format(int(export_id))
        ),
        "execution_generation_issued_at": artifact_manifest.get("created_at"),
        "edge_manifest_key_id": str(
            getattr(settings, "EDGE_MANIFEST_ED25519_KEY_ID", "edge-v1") or "edge-v1"
        ),
        "edge_signature_required": bool(
            getattr(settings, "EDGE_MANIFEST_ED25519_REQUIRE", False)
            or str(getattr(settings, "APP_ENV", "") or "").lower() == "production"
        ),
        "yolo_train_job_id": yolo_train_job_id,
        "imgsz": int(imgsz or 640),
        "threshold_contract": threshold_contract,
        "threshold_unit": threshold_contract["threshold_unit"],
        "threshold_source": threshold_contract["source"],
        "defaulted_fields": threshold_contract["defaulted_fields"],
        "conf_thres": threshold_contract["conf_thres"],
        "nms_thres": threshold_contract["nms_thres"],
        "match_iou_thres": threshold_contract["match_iou_thres"],
        "preprocessing_contract": {
            "resize": "letterbox",
            "pad_value": 114,
            "color_order": "RGB",
            "scale": 1.0 / 255.0,
        },
        # Keep the legacy key for older runner bundles while making its
        # meaning explicit: it is the GT matching IoU, not NMS IoU.
        "iou_thres": threshold_contract["match_iou_thres"],
        "class_names": _read_class_names(yolo_train_job_id),
        "eval_image_count": img_n,
        "labeled_image_count": len(label_mappings),
        "fixed_eval_signature": eval_manifest["fixed_eval_signature"],
        "require_fixed_eval_binding": True,
        "run_coco_eval": bool(cfg.get("run_coco_eval")),
        "timeout_sec": int(cfg.get("validation_timeout_sec") or 7200),
        "performance_enabled": True,
        "performance_warmup_count": int(settings.EDGE_PERFORMANCE_WARMUP_COUNT),
        "performance_repeat_count": int(settings.EDGE_PERFORMANCE_REPEAT_COUNT),
        "performance_resource_interval_seconds": float(
            settings.EDGE_PERFORMANCE_RESOURCE_INTERVAL_SECONDS
        ),
        "performance_stability_seconds": int(
            settings.EDGE_PERFORMANCE_STABILITY_SECONDS
        ),
    }
    edge_private_key = str(
        getattr(settings, "EDGE_MANIFEST_ED25519_PRIVATE_KEY", "") or ""
    ).strip()
    if edge_private_key:
        run_cfg = sign_model_manifest_ed25519(
            run_cfg,
            private_key=edge_private_key,
            key_id=run_cfg["edge_manifest_key_id"],
        )
    elif run_cfg["edge_signature_required"]:
        run_cfg["edge_manifest_signature_status"] = "MISSING_KEY"
    (staging / "run_config.json").write_text(
        json.dumps(run_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    bundle = _export_dir_for_row(export_id) / "edge_ssh_bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        for fp in staging.rglob("*"):
            if fp.is_file():
                tar.add(fp, arcname=fp.relative_to(staging).as_posix())
    return bundle


def _edge_run_log_failure_hint(export_root: Path) -> str:
    """从边端 run.log 提取可操作的失败说明（供页面 error_message）。"""
    log_path = export_root / "edge_ssh_remote_run.log"
    if not log_path.is_file():
        return ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:].strip()
    except OSError:
        return ""
    if not tail:
        return ""
    if "future feature annotations is not defined" in tail or (
        "SyntaxError" in tail and "annotations" in tail
    ):
        return (
            "边端 run.log：验证脚本与容器内 Python 版本不兼容（容器 python3 需 ≥3.7，"
            "或请拉取最新 LabelU 后重试边端 SSH）。"
            f" 日志片段：{tail[-400:]}"
        )
    if "SyntaxError" in tail:
        return f"边端 run.log 语法错误：{tail[-500:]}"
    if "UnicodeEncodeError" in tail and "fsencode" in tail:
        return (
            "边端 Python 3.6 无法向子进程传递含中文的环境变量（EVAL_CLASS_NAMES）。"
            "请拉取最新 LabelU（已改为 ASCII JSON 转义类名）后重新触发边端验证。"
            f" 日志片段：{tail[-400:]}"
        )
    if "Unexpected EOF in archive" in tail or "unexpected end of file" in tail.lower():
        return (
            "边端 bundle.tar.gz 不完整（解压失败）。常见原因：重复触发边端验证导致并发上传，"
            "或 SFTP 传输中断。请只点一次「边端 SSH 验证」，待上传完成后再观察 run.log。"
            f" 日志片段：{tail[-400:]}"
        )
    return f"边端 run.log 末尾：{tail[-500:]}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fp:
        while True:
            chunk = fp.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _remote_digest_text(client, remote_digest_path: str) -> str:
    code, out, err = ssh_run(
        client,
        f"test ! -f {_shell_quote(remote_digest_path)} || "
        f"cat {_shell_quote(remote_digest_path)}",
        timeout=60,
    )
    if code != 0:
        raise EdgeSshError(
            f"读取边端 bundle 指纹失败 (exit={code}): {(err or out)[:500]}"
        )
    return str(out or "").strip().splitlines()[0] if str(out or "").strip() else ""


def _prepare_remote_part_identity(
    client,
    *,
    remote_part: str,
    local_digest: str,
) -> str:
    remote_digest_path = f"{remote_part}.sha256"
    if _remote_digest_text(client, remote_digest_path) == local_digest:
        return remote_digest_path
    code, out, err = ssh_run(
        client,
        f"rm -f {_shell_quote(remote_part)} {_shell_quote(remote_digest_path)} && "
        f"printf '%s\\n' {_shell_quote(local_digest)} > {_shell_quote(remote_digest_path)}",
        timeout=120,
    )
    if code != 0:
        raise EdgeSshError(
            f"初始化边端 bundle 续传指纹失败 (exit={code}): {(err or out)[:500]}"
        )
    return remote_digest_path


def _upload_edge_bundle_verified(
    client,
    local_bundle: Path,
    remote_bundle: str,
    *,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> None:
    """先上传到 .part，校验大小后再原子 mv，避免并发写同一 bundle.tar.gz。"""
    local_size = int(local_bundle.stat().st_size)
    local_digest = _file_sha256(local_bundle)
    remote_digest = f"{remote_bundle}.sha256"
    try:
        if (
            _remote_digest_text(client, remote_digest) == local_digest
            and sftp_stat_size(client, remote_bundle) == local_size
        ):
            if progress_callback is not None:
                progress_callback(local_size, local_size)
            return
    except EdgeSshError:
        pass
    remote_part = f"{remote_bundle}.part"
    remote_part_digest = _prepare_remote_part_identity(
        client,
        remote_part=remote_part,
        local_digest=local_digest,
    )
    sftp_upload_resumable(
        client,
        local_bundle,
        remote_part,
        progress_callback=progress_callback,
    )
    remote_size = sftp_stat_size(client, remote_part)
    if remote_size != local_size:
        raise EdgeSshTransferInterrupted(
            f"边端 bundle 大小校验失败：本地 {local_size} 字节，远端 {remote_size} 字节"
        )
    code, out, err = ssh_run(
        client,
        f"mv -f {_shell_quote(remote_part)} {_shell_quote(remote_bundle)} && "
        f"mv -f {_shell_quote(remote_part_digest)} {_shell_quote(remote_digest)}",
        timeout=120,
    )
    if code != 0:
        raise EdgeSshError(
            f"边端 bundle 重命名失败 (exit={code}): {(err or out)[:500]}"
        )


def _is_retryable_edge_transfer_error(exc: Exception) -> bool:
    if isinstance(exc, EdgeSshTransferInterrupted):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "ssh 连接失败",
            "ssh 命令执行失败",
            "connection reset",
            "connection dropped",
            "connection refused",
            "socket is closed",
            "server connection dropped",
            "timed out",
            "no route to host",
        )
    )


def _upload_edge_bundle_with_reconnect(
    client,
    local_bundle: Path,
    remote_bundle: str,
    *,
    reconnect: Callable[[], Any],
    progress_callback: Optional[Callable[[int, int], None]] = None,
    retry_callback: Optional[Callable[[int, int, Exception], None]] = None,
    attempts: int = EDGE_BUNDLE_UPLOAD_ATTEMPTS,
    retry_delay_seconds: float = EDGE_BUNDLE_UPLOAD_RETRY_DELAY_SECONDS,
):
    """Upload with resume, reconnecting only for transport-level failures."""
    attempt_cap = max(1, int(attempts))
    current_client = client
    for attempt in range(1, attempt_cap + 1):
        try:
            if current_client is None:
                current_client = reconnect()
            remote_dir = str(remote_bundle).replace("\\", "/").rsplit("/", 1)[0]
            if remote_dir:
                remote_dir = require_edge_path(remote_dir)
                code, out, err = ssh_run(
                    current_client,
                    remote_edge_path_guard_command(remote_dir),
                    timeout=60,
                )
                if code != 0:
                    raise EdgeSshError(
                        ((err or out) or "remote path escapes edge root")[-1000:]
                    )
                ssh_mkdir_p(current_client, remote_dir)
            _upload_edge_bundle_verified(
                current_client,
                local_bundle,
                remote_bundle,
                progress_callback=progress_callback,
            )
            return current_client
        except Exception as exc:
            if not _is_retryable_edge_transfer_error(exc) or attempt >= attempt_cap:
                raise
            try:
                if current_client is not None:
                    current_client.close()
            except Exception:
                pass
            current_client = None
            if retry_callback is not None:
                retry_callback(attempt + 1, attempt_cap, exc)
            if retry_delay_seconds > 0:
                time.sleep(float(retry_delay_seconds))
    raise EdgeSshError("边端 bundle 上传重试次数已耗尽")


def _pull_remote_run_log_if_present(
    client, *, remote_job: str, export_root: Path
) -> bool:
    """尽量在清理前拉回 run.log，失败时不中断主流程。"""
    remote_log = f"{str(remote_job).rstrip('/')}/run.log"
    local_log = export_root / "edge_ssh_remote_run.log"
    try:
        return bool(sftp_download(client, remote_log, local_log))
    except EdgeSshError:
        return False


def _cleanup_remote_job_dir(
    client, *, remote_job: str, edge_root: str | None = None
) -> tuple[bool, str]:
    """删除当前 export 的边端临时目录，避免磁盘持续堆积。"""
    remote_job = require_edge_path(str(remote_job).rstrip("/"), root=edge_root)
    parent_dir = remote_job.rsplit("/", 1)[0] if "/" in remote_job else ""
    job_name = remote_job.rsplit("/", 1)[-1]
    if not parent_dir or not job_name or job_name in (".", ".."):
        return False, f"skip cleanup for unsafe remote_job={remote_job}"
    command = (
        f"set -e; {remote_edge_path_guard_command(remote_job, root=edge_root)}; "
        f"cd {_shell_quote(parent_dir)}; rm -rf -- {_shell_quote(job_name)}"
    )
    code, out, err = ssh_run(client, command, timeout=300)
    if code != 0:
        return False, ((err or out) or "").strip()[:1000] or f"exit={code}"
    return True, ""


def _retain_failed_remote_job_dir(
    client,
    *,
    remote_job: str,
    retention_hours: int,
    edge_root: str | None = None,
) -> tuple[bool, str]:
    """Mark a failed job for diagnosis; the maintenance watchdog removes it later."""
    remote_job = require_edge_path(str(remote_job).rstrip("/"), root=edge_root)
    parent_dir = remote_job.rsplit("/", 1)[0] if "/" in remote_job else ""
    job_name = remote_job.rsplit("/", 1)[-1]
    if not parent_dir or not job_name or job_name in (".", ".."):
        return False, f"skip retention for unsafe remote_job={remote_job}"
    marker = f"{remote_job}/.labelu_failed_retained"
    command = (
        f"set -e; {remote_edge_path_guard_command(remote_job, root=edge_root)}; "
        f"test -d {_shell_quote(remote_job)}; "
        f"printf '%s\n' {_shell_quote(str(max(1, int(retention_hours))))} > {_shell_quote(marker)}; "
        f"touch {_shell_quote(remote_job)} {_shell_quote(marker)}"
    )
    code, out, err = ssh_run(client, command, timeout=60)
    if code != 0:
        return False, ((err or out) or "").strip()[:1000] or f"exit={code}"
    return True, ""


def cleanup_stale_remote_job_dirs(
    client, *, remote_base: str, retention_hours: int
) -> dict[str, Any]:
    """Remove only stale LabelU validation job directories under one configured base."""
    remote_base = require_edge_path(str(remote_base or "").rstrip("/"))
    if not remote_base.startswith("/") or remote_base in ("", "/", "/data"):
        return {
            "status": "skipped",
            "removed": [],
            "message": f"unsafe remote_base={remote_base}",
        }
    minutes = max(60, int(retention_hours) * 60)
    selector = "\\( -name 'manual-job-[0-9]*' -o -name 'export-[0-9]*' \\)"
    list_command = (
        f"set -e; {remote_edge_path_guard_command(remote_base)}; "
        f"test -d {_shell_quote(remote_base)} || exit 0; "
        f"cd {_shell_quote(remote_base)}; "
        f"find . -mindepth 1 -maxdepth 1 -type d {selector} -mmin +{minutes} -print"
    )
    code, out, err = ssh_run(client, list_command, timeout=120)
    if code != 0:
        return {
            "status": "failed",
            "removed": [],
            "message": ((err or out) or f"exit={code}")[-1000:],
        }
    candidates = [
        line.strip().removeprefix("./")
        for line in str(out or "").splitlines()
        if line.strip().startswith("./")
    ]
    if not candidates:
        return {"status": "completed", "removed": [], "message": ""}
    quoted_names = " ".join(_shell_quote(name) for name in candidates)
    delete_command = (
        f"set -e; {remote_edge_path_guard_command(remote_base)}; "
        f"cd {_shell_quote(remote_base)}; "
        f"for name in {quoted_names}; do "
        'case "$name" in manual-job-[0-9]*|export-[0-9]*) rm -rf -- "$name";; esac; '
        "done"
    )
    code, out, err = ssh_run(client, delete_command, timeout=300)
    if code != 0:
        return {
            "status": "failed",
            "removed": [],
            "message": ((err or out) or f"exit={code}")[-1000:],
        }
    return {"status": "completed", "removed": candidates, "message": ""}


def _read_json_object_file(
    path: Path, *, label: str
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读取边端回传的 JSON 对象；空文件或非法 JSON 返回 (None, 说明) 而不抛异常。"""
    if not path.is_file():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return None, f"{label} 读取失败: {exc}"
    if not raw:
        return None, (
            f"{label} 为空（边端未生成有效报告，常见原因：验证脚本失败、"
            f"SOPHON_DEMO_ROOT 错误或 output 未写入）"
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{label} JSON 无效: {exc}"
    if not isinstance(data, dict):
        return None, f"{label} 须为 JSON 对象"
    return data, None


def _ingest_remote_results(export_id: int, local_output: Path) -> Dict[str, Any]:
    """将边端 output 写回 server_eval / board 报告。"""
    export_root = _export_dir_for_row(export_id)
    server_eval = export_root / "server_eval"
    server_eval.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {"ingested": [], "warnings": []}

    bmodel_src = local_output / "results_bmodel"
    if bmodel_src.is_dir():
        bmodel_dst = server_eval / "results_bmodel"
        if bmodel_dst.exists():
            shutil.rmtree(bmodel_dst)
        shutil.copytree(bmodel_src, bmodel_dst)
        result["ingested"].append("results_bmodel")

    board_report_path = local_output / "board_report.json"
    board_data, board_warn = _read_json_object_file(
        board_report_path, label="board_report.json"
    )
    if board_warn:
        result["warnings"].append(board_warn)
    elif board_data is not None:
        result["board_report"] = board_data

    bmcv_summary_path = local_output / "bmcv_infer_summary.json"
    bmcv_summary, bmcv_warn = _read_json_object_file(
        bmcv_summary_path, label="bmcv_infer_summary.json"
    )
    if bmcv_warn and bmcv_summary_path.is_file():
        result["warnings"].append(bmcv_warn)
    if bmcv_summary is not None and board_data is not None:
        board_data["bmcv_infer"] = bmcv_summary
        ds = bmcv_summary.get("detection_summary") or {}
        prev = bmcv_summary.get("preview") or {}
        metrics = board_data.setdefault("metrics", {})
        if isinstance(metrics, dict):
            if isinstance(ds, dict) and ds:
                metrics["bmcv_detection"] = {
                    "image_count": ds.get("image_count"),
                    "with_boxes": ds.get("with_boxes"),
                    "total_boxes": ds.get("total_boxes"),
                    "sample": ds.get("sample"),
                    "result_json": ds.get("result_json"),
                }
            if isinstance(prev, dict) and prev:
                metrics["bmcv_preview"] = {
                    "viz_image_count": prev.get("viz_image_count"),
                    "preview_files": prev.get("preview_files") or [],
                }
        result["board_report"] = board_data

    bmcv_preview_src = local_output / "bmcv_preview"
    if bmcv_preview_src.is_dir():
        bmcv_preview_dst = server_eval / "bmcv_preview"
        if bmcv_preview_dst.exists():
            shutil.rmtree(bmcv_preview_dst)
        shutil.copytree(bmcv_preview_src, bmcv_preview_dst)
        result["ingested"].append("bmcv_preview")
        n_prev = sum(
            1
            for p in bmcv_preview_dst.iterdir()
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")
        )
        if board_data is not None:
            metrics = board_data.setdefault("metrics", {})
            if isinstance(metrics, dict):
                prev_m = metrics.setdefault("bmcv_preview", {})
                if isinstance(prev_m, dict):
                    prev_m["platform_dir"] = str(bmcv_preview_dst)
                    prev_m["preview_count"] = n_prev
                    if not prev_m.get("preview_files"):
                        prev_m["preview_files"] = sorted(
                            p.name
                            for p in bmcv_preview_dst.iterdir()
                            if p.is_file()
                            and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")
                        )
            result["board_report"] = board_data

    bmodel_json = local_output / "bmodel_sop_eval.json"
    bmodel_data, bmodel_warn = _read_json_object_file(
        bmodel_json, label="bmodel_sop_eval.json"
    )
    if bmodel_warn:
        result["warnings"].append(bmodel_warn)
    if bmodel_data is not None:
        result["bmodel_sop"] = bmodel_data
    elif (server_eval / "results_bmodel" / "eval_summary.json").is_file():
        try:
            metrics = json.loads(
                (server_eval / "results_bmodel" / "eval_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            if isinstance(metrics, dict) and metrics.get("map50") is not None:
                bmodel_data = {
                    "status": "ok",
                    "metrics": metrics,
                    "results_dir": str(server_eval / "results_bmodel"),
                    "report_md": str(
                        server_eval / "results_bmodel" / "evaluation_report.md"
                    ),
                }
                result["bmodel_sop"] = bmodel_data
                result["warnings"].append(
                    "bmodel_sop_eval.json 缺失，已从 results_bmodel/eval_summary.json 回填"
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    # The runner keeps INT8 at results_bmodel for backward compatibility and
    # stores FP32/FP16 under results_bmodel/variants/<tag>. Normalize all
    # variant paths to the local pulled report tree before exposing them.
    if isinstance(result.get("bmodel_sop"), dict):
        bmodel_data = result["bmodel_sop"]
        models = bmodel_data.get("models")
        if isinstance(models, dict):
            normalized = {}
            for tag in ("fp32", "fp16", "int8"):
                item = models.get(tag)
                if not isinstance(item, dict):
                    continue
                block = dict(item)
                variant_dir = (
                    server_eval / "results_bmodel" / "variants" / tag
                    if tag != "int8"
                    else server_eval / "results_bmodel"
                )
                block["model_tag"] = tag
                block["results_dir"] = str(variant_dir)
                block["report_md"] = str(variant_dir / "evaluation_report.md")
                block["report_md_path"] = str(variant_dir / "evaluation_report.md")
                block["eval_summary_path"] = str(variant_dir / "eval_summary.json")
                block["report_md_exists"] = (
                    variant_dir / "evaluation_report.md"
                ).is_file()
                block["eval_summary_exists"] = (
                    variant_dir / "eval_summary.json"
                ).is_file()
                normalized[tag] = block
            if normalized:
                bmodel_data["models"] = normalized
                bmodel_data["completed_models"] = [
                    tag
                    for tag, block in normalized.items()
                    if block.get("status") == "ok"
                ]
                bmodel_data["missing_or_failed_models"] = [
                    tag
                    for tag in ("fp32", "fp16", "int8")
                    if tag not in normalized or normalized[tag].get("status") != "ok"
                ]
                result["bmodel_sop"] = bmodel_data

    summary_path = server_eval / "sop_eval_summary.json"
    sop = load_server_sop_eval_summary(export_root) or {
        "status": "pending",
        "export_id": export_id,
    }
    if bmodel_data is not None:
        bm = result.get("bmodel_sop") or {}
        sop["bmodel"] = {
            "status": bm.get("status", "skip"),
            "results_dir": str(server_eval / "results_bmodel"),
            "metrics": bm.get("metrics"),
            "report_md_exists": Path(bm.get("report_md", "")).name
            == "evaluation_report.md"
            or (server_eval / "results_bmodel" / "evaluation_report.md").is_file(),
        }
        pt_m = (sop.get("pt") or {}).get("metrics") or {}
        bm_m = bm.get("metrics") or {}
        if pt_m.get("map50") is not None and bm_m.get("map50") is not None:
            sop["comparison_pt_bmodel"] = _compare_pt_bmodel(pt_m, bm_m)
        models = bm.get("models")
        if isinstance(models, dict):
            sop_models = {}
            for tag in ("fp32", "fp16", "int8"):
                model = models.get(tag)
                if not isinstance(model, dict):
                    continue
                model_block = dict(model)
                model_block.setdefault("model_tag", tag)
                model_block.setdefault(
                    "results_dir",
                    str(
                        server_eval
                        / "results_bmodel"
                        / (Path("variants") / tag if tag != "int8" else Path(""))
                    ),
                )
                sop_models[tag] = model_block
            if sop_models:
                sop["bmodels"] = sop_models
    sop = enrich_sop_eval_summary(sop, export_root)
    summary_path.write_text(
        json.dumps(sop, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result["sop_eval_summary"] = sop
    return result


def run_edge_ssh_validation_sync(
    db: Session,
    *,
    export_id: int,
    yolo_train_job_id: int,
    imgsz: int,
    skip_if_busy: bool = False,
) -> Dict[str, Any]:
    """SSH 到边端执行验证并拉回报告。"""
    lock = _export_edge_ssh_lock(export_id)
    if not lock.acquire(blocking=False):
        reason = f"export-{export_id} 边端 SSH 验证正在进行中，请勿重复触发"
        if skip_if_busy:
            return {"status": "skipped", "export_id": export_id, "reason": reason}
        return {"status": "busy", "export_id": export_id, "reason": reason}

    cfg = edge_cfg_io.load_edge_ssh_config()
    if not edge_cfg_io.is_edge_ssh_configured(cfg):
        lock.release()
        return {
            "status": "skipped",
            "reason": "边端 SSH 未启用或缺少主机/账号（请在页面「边端 SSH 配置」保存）",
        }

    probe = probe_and_persist_edge_ssh_target(cfg)
    if probe.get("status") != "ok":
        lock.release()
        _record_active_device_validation_status("probe_failed")
        return {
            "status": "skipped",
            "export_id": export_id,
            "reason": f"edge_ssh_probe_failed:{probe.get('code')}:{probe.get('stage')}",
            "probe": probe,
        }

    host = str(cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 22)
    user = str(cfg.get("user") or "").strip()
    password = str(cfg.get("password") or "")
    private_key_path = str(cfg.get("private_key_path") or "")
    paths = edge_remote_paths(cfg.get("edge_root"))
    remote_base = paths.jobs
    remote_job = paths.job(f"export-{export_id}")
    sophon_root = paths.sophon_demo_root
    docker_container = str(cfg.get("docker_container") or "").strip()
    docker_use_sudo = bool(cfg.get("docker_use_sudo", True))

    log_lines: list[str] = []
    export_root = _export_dir_for_row(export_id)
    log_path = export_root / "edge_ssh_validation.log"

    def log(msg: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
        log_lines.append(line)
        logger.info("edge_ssh export_id={} {}", export_id, msg)

    summary: Dict[str, Any] = {
        "status": "pending",
        "export_id": export_id,
        "remote_host": host,
        "remote_job_dir": remote_job,
        "docker_container": docker_container,
        "docker_use_sudo": docker_use_sudo,
    }

    client = None
    try:
        try:
            _mark_active_device_used()
            result = _run_edge_ssh_validation_locked(
                db,
                export_id=export_id,
                yolo_train_job_id=yolo_train_job_id,
                imgsz=imgsz,
                cfg=cfg,
                host=host,
                port=port,
                user=user,
                password=password,
                private_key_path=private_key_path,
                remote_base=remote_base,
                remote_job=remote_job,
                sophon_root=sophon_root,
                docker_container=docker_container,
                docker_use_sudo=docker_use_sudo,
                export_root=export_root,
                log=log,
                log_lines=log_lines,
                summary=summary,
            )
            _record_active_device_validation_status(result.get("status") or "unknown")
            return result
        finally:
            lock.release()
    except Exception:
        if lock.locked():
            lock.release()
        raise


def _run_edge_ssh_validation_locked(
    db: Session,
    *,
    export_id: int,
    yolo_train_job_id: int,
    imgsz: int,
    cfg: Dict[str, Any],
    host: str,
    port: int,
    user: str,
    password: str,
    private_key_path: str,
    remote_base: str,
    remote_job: str,
    sophon_root: str,
    docker_container: str,
    docker_use_sudo: bool,
    export_root: Path,
    log,
    log_lines: list,
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    client = None
    configured_edge_root = str(cfg.get("edge_root") or "").strip() or None
    log_path = export_root / "edge_ssh_validation.log"
    remote_cleanup_needed = False
    try:
        log("打包边端验证 bundle …")
        bundle = build_edge_validation_bundle(
            db,
            export_id=export_id,
            yolo_train_job_id=yolo_train_job_id,
            imgsz=imgsz,
        )
        log(f"连接 SSH {user}@{host}:{port} …")
        client = ssh_connect(
            host=host,
            port=port,
            username=user,
            password=password,
            private_key_path=private_key_path or None,
            timeout=int(cfg.get("connect_timeout_sec") or 30),
        )
        code_guard, out_guard, err_guard = ssh_run(
            client,
            remote_edge_path_guard_command(remote_job, root=configured_edge_root),
            timeout=60,
        )
        if code_guard != 0:
            raise EdgeSshError(
                ((err_guard or out_guard) or "remote path escapes edge root")[-1000:]
            )
        ssh_mkdir_p(client, remote_job)
        remote_cleanup_needed = True
        code_df, out_df, err_df = ssh_run(
            client,
            f"df -h {_shell_quote(remote_base)} 2>/dev/null || df -h / 2>/dev/null; "
            f"test -w {_shell_quote(remote_job)} && echo WRITABLE_OK || echo WRITABLE_FAIL",
            timeout=60,
        )
        df_tail = (out_df or err_df or "").strip()[-1200:]
        if df_tail:
            log(f"边端磁盘/可写性: {df_tail.replace(chr(10), ' | ')}")
            summary["remote_disk_check"] = df_tail
        if "WRITABLE_FAIL" in (out_df or ""):
            raise EdgeSshError(
                f"边端目录不可写: {remote_job}；请检查 SFTP 用户权限与磁盘空间（df -h）"
            )
        remote_bundle = f"{remote_job}/bundle.tar.gz"
        bundle_bytes = int(bundle.stat().st_size)
        log(f"上传 bundle（{bundle_bytes} 字节）…")
        _upload_edge_bundle_verified(client, bundle, remote_bundle)
        log(f"bundle 上传完成，远端大小校验通过")

        resolved_demo, probe_note = probe_sophon_demo_root_via_ssh(
            client,
            configured=sophon_root,
            docker_container=docker_container,
            docker_use_sudo=docker_use_sudo,
            edge_root=configured_edge_root,
            restrict_to_configured=True,
        )
        if resolved_demo:
            demo_env = resolved_demo
            log(f"SOPHON_DEMO_ROOT={demo_env} ({probe_note})")
        else:
            demo_env = sophon_root or DEFAULT_SOPHON_DEMO_ROOT
            log(
                f"WARN: {probe_note}；将跳过 sophon-demo 例程，仅跑 pythonTest bmodel 评估"
            )
        summary["sophon_demo_root"] = demo_env
        summary["sophon_demo_probe"] = probe_note
        cmd = build_edge_remote_validation_command(
            remote_job=remote_job,
            demo_env=demo_env,
            docker_container=docker_container,
            docker_use_sudo=docker_use_sudo,
        )
        if docker_container:
            log(
                f"边端在 Docker 容器 {docker_container} 内执行验证 "
                f"({'sudo ' if docker_use_sudo else ''}docker exec) …"
            )
        else:
            log("边端在 SSH 宿主机执行验证脚本 …")
        code, out, err = ssh_run(
            client,
            cmd,
            timeout=int(cfg.get("validation_timeout_sec") or 7200),
        )
        summary["remote_exit_code"] = code
        summary["remote_stdout_tail"] = out[-4000:]
        summary["remote_stderr_tail"] = err[-2000:]

        local_out = export_root / "edge_ssh_pull" / "output"
        if local_out.exists():
            shutil.rmtree(local_out)
        local_out.mkdir(parents=True, exist_ok=True)
        log("拉回 output …")
        n = sftp_download_tree(client, f"{remote_job}/output", local_out)
        if n == 0:
            log("output/ 为空，改从 work/output 拉回 …")
            n = sftp_download_tree(client, f"{remote_job}/work/output", local_out)
        summary["files_pulled"] = n

        if not (local_out / "board_report.json").is_file():
            sftp_download(
                client,
                f"{remote_job}/work/output/board_report.json",
                local_out / "board_report.json",
            )
        if not (local_out / "bmodel_sop_eval.json").is_file():
            try:
                sftp_download(
                    client,
                    f"{remote_job}/work/output/bmodel_sop_eval.json",
                    local_out / "bmodel_sop_eval.json",
                )
            except EdgeSshError:
                pass
        _pull_remote_run_log_if_present(
            client, remote_job=remote_job, export_root=export_root
        )

        ingest = _ingest_remote_results(export_id, local_out)
        summary["ingest"] = ingest
        ingest_warnings = list(ingest.get("warnings") or [])
        if ingest_warnings:
            summary["ingest_warnings"] = ingest_warnings
            for w in ingest_warnings:
                log(f"ingest: {w}")

        bmodel_sop = ingest.get("bmodel_sop") or {}
        bmodel_ok = bmodel_sop.get("status") == "ok"
        board_report = ingest.get("board_report") or {}
        board_skipped = bool(board_report.get("skipped"))
        board_ok = bool(board_report) and (board_report.get("passed") or board_skipped)

        if code != 0:
            summary["status"] = "fail"
            summary["error"] = summary.get("error") or f"边端脚本 exit={code}"
        elif bmodel_ok:
            summary["status"] = "ok"
            if board_skipped:
                summary["note"] = "sophon-demo 例程已跳过；pythonTest bmodel 评估成功"
        elif board_ok and not board_skipped and board_report.get("passed"):
            summary["status"] = "ok"
        elif board_report and board_skipped and bmodel_sop.get("status") == "fail":
            summary["status"] = "fail"
            err_parts = [
                "pythonTest bmodel 评估失败",
                bmodel_sop.get("stderr_tail") or bmodel_sop.get("detail") or "",
            ]
            summary["error"] = "; ".join(p for p in err_parts if p)[:2000]
        elif not ingest.get("board_report"):
            summary["status"] = "fail"
            err_parts = (
                list(ingest_warnings)
                if ingest_warnings
                else ["边端未返回有效的 board_report.json"]
            )
            run_hint = _edge_run_log_failure_hint(export_root)
            if run_hint and run_hint not in err_parts:
                err_parts.append(run_hint)
            summary["error"] = "; ".join(err_parts)
        else:
            summary["status"] = "fail"
            summary["error"] = "; ".join(ingest_warnings) or "边端验证未通过"

        persist_report = ingest.get("board_report")
        if isinstance(persist_report, dict):
            # board_report carries device/inference steps; the same SSH run's
            # BModel SOP report carries accuracy, P95 and short-stability
            # evidence.  Keep them under one immutable validation report so
            # the shared evidence gate can evaluate the whole run.
            persist_report = dict(persist_report)
            if isinstance(ingest.get("bmodel_sop"), dict):
                persist_report["bmodel_sop"] = ingest["bmodel_sop"]
            if isinstance(ingest.get("sop_eval_summary"), dict):
                persist_report["sop_eval_summary"] = ingest["sop_eval_summary"]
        _persist_board_validation_from_ssh(db, export_id, persist_report, summary)

    except EdgeSshError as exc:
        summary["status"] = "fail"
        summary["error"] = str(exc)
        log(f"失败: {exc}")
        if client is not None:
            _pull_remote_run_log_if_present(
                client, remote_job=remote_job, export_root=export_root
            )
    except Exception as exc:
        summary["status"] = "fail"
        summary["error"] = str(exc)[:8000]
        logger.exception("edge ssh validation export_id={}", export_id)
        if client is not None:
            _pull_remote_run_log_if_present(
                client, remote_job=remote_job, export_root=export_root
            )
    finally:
        if client and remote_cleanup_needed:
            try:
                if summary.get("status") == "ok":
                    cleaned, cleanup_msg = _cleanup_remote_job_dir(
                        client,
                        remote_job=remote_job,
                        edge_root=configured_edge_root,
                    )
                    summary["remote_cleanup"] = "done" if cleaned else "failed"
                    summary["remote_retention_hours"] = 0
                else:
                    retention_hours = int(
                        getattr(settings, "EDGE_REMOTE_FAILED_JOB_RETENTION_HOURS", 24)
                        or 24
                    )
                    cleaned, cleanup_msg = _retain_failed_remote_job_dir(
                        client,
                        remote_job=remote_job,
                        retention_hours=retention_hours,
                        edge_root=configured_edge_root,
                    )
                    summary["remote_cleanup"] = (
                        "retained_for_diagnosis" if cleaned else "retention_mark_failed"
                    )
                    summary["remote_retention_hours"] = retention_hours
                if cleanup_msg:
                    summary["remote_cleanup_message"] = cleanup_msg
                log(
                    (
                        "远端临时目录清理完成"
                        if cleaned and summary.get("status") == "ok"
                        else (
                            f"失败目录保留 {summary.get('remote_retention_hours')} 小时"
                            if cleaned
                            else f"远端目录留存处理失败: {cleanup_msg}"
                        )
                    )
                )
            except Exception as exc:
                summary["remote_cleanup"] = "failed"
                summary["remote_cleanup_message"] = str(exc)[:1000]
                log(f"远端临时目录清理失败: {exc}")
        if client:
            try:
                client.close()
            except Exception:
                pass
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        summary["log_path"] = str(log_path)

    return summary


def _shell_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def _edge_model_cache_dir(cache_key: str, *, edge_root: str | None = None) -> str:
    normalized = str(cache_key or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("edge model cache key must be a SHA-256 digest")
    paths = edge_remote_paths(edge_root)
    # `jobs` is the device path guaranteed to be shared with the runtime
    # container. Keep the cache there so host probes and container runs see
    # the same persistent files.
    return require_edge_path(
        f"{paths.jobs}/.cache/models/{normalized}", root=paths.root
    )


def edge_model_cache_exists(
    client,
    cache_key: str,
    *,
    edge_root: str | None = None,
    docker_container: str = "",
    docker_use_sudo: bool = True,
) -> bool:
    paths = edge_remote_paths(edge_root)
    cache_dir = _edge_model_cache_dir(cache_key, edge_root=paths.root)
    check_command = (
        f"set -e; {remote_edge_path_guard_command(cache_dir, root=paths.root)}; "
        f"test -f {_shell_quote(cache_dir + '/.complete')} && "
        f"test -d {_shell_quote(cache_dir + '/models')} && printf 'hit'"
    )
    container = str(docker_container or "").strip()
    if container:
        docker_bin = "sudo docker" if docker_use_sudo else "docker"
        command = (
            f"{docker_bin} exec {_shell_quote(container)} "
            f"bash -lc {_shell_quote(check_command)}"
        )
    else:
        command = check_command
    code, out, _ = ssh_run(client, command, timeout=60)
    return code == 0 and str(out or "").strip() == "hit"


def build_edge_remote_validation_command(
    *,
    remote_job: str,
    demo_env: str,
    docker_container: str = "",
    docker_use_sudo: bool = True,
    model_cache_key: str = "",
    edge_root: str | None = None,
) -> str:
    """生成边端 SSH 执行的 shell；默认 docker exec bm1688（SOP 3.6.2 容器内 pythonTest）。"""
    paths = edge_remote_paths(edge_root)
    remote_job = require_edge_path(str(remote_job).rstrip("/"), root=paths.root)
    runner = f"{remote_job}/work/scripts/edge_board_validate_runner.py"
    work_dir = f"{remote_job}/work"
    run_log = f"{remote_job}/run.log"
    cache_prepare = ""
    if model_cache_key:
        cache_dir = _edge_model_cache_dir(model_cache_key, edge_root=paths.root)
        cache_parent = cache_dir.rsplit("/", 1)[0]
        cache_prepare = (
            f"model_cache={_shell_quote(cache_dir)}; "
            f'model_cache_tmp="${{model_cache}}.tmp.$$"; '
            f"mkdir -p {_shell_quote(cache_parent)}; "
            f'if [ -f "$model_cache/.complete" ] && [ -d "$model_cache/models" ]; then '
            f'rm -rf work/models; cp -a "$model_cache/models" work/models; '
            f"elif [ -d work/models ]; then "
            f'rm -rf "$model_cache_tmp"; mkdir -p "$model_cache_tmp"; '
            f'cp -a work/models "$model_cache_tmp/models"; '
            f"printf '%s\\n' {_shell_quote(model_cache_key)} > \"$model_cache_tmp/.complete\"; "
            f'if mkdir "$model_cache.lock" 2>/dev/null; then '
            f'if [ ! -f "$model_cache/.complete" ]; then '
            f'rm -rf "$model_cache"; mv "$model_cache_tmp" "$model_cache"; '
            f'else rm -rf "$model_cache_tmp"; fi; '
            f'rmdir "$model_cache.lock" 2>/dev/null || true; '
            f'else rm -rf "$model_cache_tmp"; fi; '
            f"else echo 'edge model cache miss and bundle has no models' >&2; exit 74; fi; "
        )
    inner = (
        f"set -e; cd {_shell_quote(remote_job)}; "
        f"rm -rf work output && mkdir -p work output; "
        f"tar -xzf bundle.tar.gz -C work; "
        f"{cache_prepare}"
        f"export SOPHON_DEMO_ROOT={_shell_quote(demo_env)}; "
        f"export LABELU_EDGE_ROOT={_shell_quote(paths.root)}; "
        f"export LABELU_PYTHON_PACKAGES={_shell_quote(paths.python_packages)}; "
        f"export PYTHONPATH={_shell_quote(paths.python_packages)}${{PYTHONPATH:+:$PYTHONPATH}}; "
        f"export TMPDIR={_shell_quote(paths.tmp)}; "
        f"export PIP_CACHE_DIR={_shell_quote(paths.cache + '/pip')}; "
        f"export PATH=$HOME/.local/bin:/opt/sophon/libsophon-current/bin:$PATH; "
        f"source /etc/profile 2>/dev/null || true; "
        f"PY=$(for p in python3.10 python3.8 python3; do "
        f"command -v $p >/dev/null 2>&1 || continue; "
        f"$p -c 'import cv2' >/dev/null 2>&1 && echo $p && break; done); "
        f"PY=${{PY:-$(command -v python3.10 || command -v python3.8 || command -v python3)}}; "
        f'echo "edge_python=$PY $($PY --version 2>&1)"; '
        f"$PY {_shell_quote(runner)} --work-dir {_shell_quote(work_dir)}; "
        # docker exec 内以 root 写 work/output，需在容器内复制到 output 供宿主机 SFTP 拉回
        f"test -d work/output && cp -a work/output/. output/ || true"
    )
    dc = (docker_container or "").strip()
    if dc:
        docker_bin = "sudo docker" if docker_use_sudo else "docker"
        body = f"{docker_bin} exec {dc} bash -lc {_shell_quote(inner)}"
    else:
        body = inner
    return (
        f"set -e; {remote_edge_path_guard_command(remote_job, root=paths.root)}; "
        f"cd {_shell_quote(remote_job)}; "
        f"set +e; set -o pipefail; {body} 2>&1 | tee {_shell_quote(run_log)}; "
        f"runner_code=${{PIPESTATUS[0]}}; set -e; "
        # 容器内 root 创建的目录先交还 SSH 用户，再生成轻量归档。
        f"sudo chown -R $(id -un):$(id -gn) work output {_shell_quote(run_log)} 2>/dev/null || true; "
        f"rm -f output-lite.tar.gz; "
        f"if [ -d output ]; then if ! tar -czf output-lite.tar.gz "
        f"--exclude='output/results_bmodel/images' "
        f"--exclude='output/results_bmodel/images/*' "
        f"--exclude='output/results_bmodel/variants/*/images' "
        f"--exclude='output/results_bmodel/variants/*/images/*' output; then "
        f"echo 'WARN: lightweight output archive failed; SFTP tree fallback required' >&2; "
        f"rm -f output-lite.tar.gz; fi; fi; "
        f"sudo chown $(id -un):$(id -gn) output-lite.tar.gz 2>/dev/null || true; "
        f"exit $runner_code"
    )


def _persist_board_validation_from_ssh(
    db: Session,
    export_id: int,
    board_report: Optional[Dict[str, Any]],
    ssh_summary: Dict[str, Any],
) -> None:
    from labelu.internal.adapter.persistence import (
        crud_yolo_bm1688_export,
        crud_yolo_board_validation,
    )
    from labelu.internal.common.db import begin_transaction

    row = crud_yolo_bm1688_export.get(db, export_id)
    if not row:
        return
    st = "COMPLETED" if ssh_summary.get("status") == "ok" else "FAILED"
    report_json = None
    from labelu.internal.common.edge_validation_evidence import (
        evaluate_edge_validation_evidence,
    )

    # SSH 与 Board Agent 共用同一发布证据门禁。把服务端已知的导出
    # 身份/签名和实际 SSH 主机写入证据上下文，但不伪造 AICM ID 或
    # fencing token；缺失时明确保存为 PARTIAL_COMPLETED。
    board_report = dict(board_report) if isinstance(board_report, dict) else {}
    board_report.setdefault(
        "model_identity",
        {
            "model_id": str(int(row.id)),
            "export_id": int(row.id),
            "aicm_model_id": getattr(row, "published_model_id", None),
            "model_id_source": (
                "aicm_model_id"
                if getattr(row, "published_model_id", None)
                else "export_id_pre_publish"
            ),
        },
    )
    board_report.setdefault(
        "artifact_execution_signature",
        getattr(row, "execution_signature", None),
    )
    board_report.setdefault(
        "execution_device_host", ssh_summary.get("remote_host")
    )
    evidence_gate = evaluate_edge_validation_evidence(
        board_report,
        expected=_artifact_profile_expectation(row),
    )
    board_report["evidence_gate"] = evidence_gate
    if st == "COMPLETED" and not evidence_gate["publishable"]:
        st = "PARTIAL_COMPLETED"
        if not ssh_summary.get("error"):
            ssh_summary["error"] = "边端证据未齐全：" + ",".join(
                evidence_gate.get("reason_codes") or ["evidence_pending"]
            )
    board_report["validation_mode"] = "edge_ssh"
    board_report["ssh_summary"] = {
        k: ssh_summary.get(k)
        for k in ("remote_host", "remote_job_dir", "remote_exit_code", "status")
    }
    report_json = json.dumps(board_report, ensure_ascii=False)[:500_000]
    with begin_transaction(db):
        crud_yolo_board_validation.delete_all_for_export(db, export_id=export_id)
        bv = crud_yolo_board_validation.create_for_export(
            db,
            export_id=export_id,
            task_id=row.task_id,
            yolo_train_job_id=row.yolo_train_job_id,
        )
        crud_yolo_board_validation.update_row(
            db,
            bv,
            status=st,
            report_json=report_json,
            error_message=ssh_summary.get("error"),
            completed_at=datetime.now(),
        )


async def start_edge_ssh_validation_for_export(
    db: Session,
    *,
    export_id: int,
    current_user,
) -> Dict[str, Any]:
    import asyncio

    from labelu.internal.application.service.yolo_bm1688_export import (
        get_export_for_download,
    )

    row = get_export_for_download(db, export_id=export_id, current_user=current_user)
    from fastapi import status as http_status

    from labelu.internal.common.error_code import ErrorCode, LabelUException

    if row.status != "COMPLETED":
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=http_status.HTTP_400_BAD_REQUEST,
            message="导出未完成，无法边端 SSH 验证",
        )
    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(
        None,
        lambda: run_edge_ssh_validation_sync(
            db,
            export_id=export_id,
            yolo_train_job_id=int(row.yolo_train_job_id),
            imgsz=int(row.imgsz or 640),
        ),
    )
    if summary.get("status") == "busy":
        raise LabelUException(
            code=ErrorCode.CODE_57032_EDGE_SSH_VALIDATION_BUSY,
            status_code=http_status.HTTP_409_CONFLICT,
            message=str(summary.get("reason") or "边端 SSH 验证正在进行中"),
        )
    return summary
