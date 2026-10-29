"""AICM 模型发布后台任务状态（每任务独立目录，供前端轮询进度）。"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from labelu.internal.common.config import settings

PUBLISH_STEP_SPECS: List[tuple[str, str]] = [
    ("validate", "1. 校验导出记录与训练作业"),
    ("config", "2. 加载已保存的 AICM 配置"),
    ("login", "3. 登录 AICM 后台"),
    ("search", "4. 准备发布包来源"),
    ("template", "5. 确认发布包结构"),
    ("package", "6. 生成 YOLOv11 标准模型包"),
    ("upload", "7. 上传模型包到 AICM（不传 name）"),
    ("rename", "8. 查询最新模型集并修改名称（edit_name）"),
    ("finish", "9. 完成"),
]

JOB_JSON_NAME = "job.json"


def jobs_dir() -> Path:
    return Path(settings.BASE_DATA_DIR) / "aicm_publish_jobs"


def _safe_job_id(job_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (job_id or ""))


def job_work_dir(job_id: str) -> Path:
    """发布任务工作目录：{BASE_DATA_DIR}/aicm_publish_jobs/{job_id}/"""
    return jobs_dir() / _safe_job_id(job_id)


def _job_dir(job_id: str) -> Path:
    return job_work_dir(job_id)


def _job_json_path(job_id: str) -> Path:
    return _job_dir(job_id) / JOB_JSON_NAME


def _legacy_job_json_path(job_id: str) -> Path:
    return jobs_dir() / f"{_safe_job_id(job_id)}.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_steps() -> List[dict[str, Any]]:
    return [
        {"key": k, "label": label, "status": "pending", "detail": None}
        for k, label in PUBLISH_STEP_SPECS
    ]


def _read_job_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _migrate_legacy_job_file(job_id: str) -> None:
    """将旧版平铺 {job_id}.json 迁入 {job_id}/job.json。"""
    legacy = _legacy_job_json_path(job_id)
    if not legacy.is_file():
        return
    target = _job_json_path(job_id)
    if target.is_file():
        return
    raw = _read_job_json(legacy)
    if raw is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        legacy.unlink()
    except OSError:
        pass


def create_job(
    *,
    export_id: int,
    user_id: int,
    job_id: Optional[str] = None,
    preset_name: str = "",
    publisher_username: str = "",
    task_id: Optional[int] = None,
    project_id: Optional[int] = None,
    task_name: str = "",
    dataset_category_id: Optional[int] = None,
    dataset_category_key: str = "",
    dataset_category_name: str = "",
    class_names: Optional[List[str]] = None,
    label_set_signature: str = "",
    class_order_signature: str = "",
    data_yaml_sha256: str = "",
    dataset_content_signature: str = "",
    training_recipe_key: str = "",
    training_recipe_version: Optional[int] = None,
    runtime_profile: str = "",
    runtime_interface_version: Optional[int] = None,
    publish_template_key: str = "",
    publish_template_name: str = "",
    publish_template_source: str = "",
    publish_template_id: Optional[int] = None,
    publish_template_version: Optional[int] = None,
    publish_template_sha256: str = "",
    publish_template_model_set_id: Optional[int] = None,
    environment_id: str = "",
    environment_name: str = "",
    publish_mode: str = "automatic",
    keep_template_cache: bool = False,
    bmodel_precision: Optional[str] = None,
    bmodel_rel_path: Optional[str] = None,
    publish_lease_owner: str = "",
    publish_fencing_token: Optional[int] = None,
    quality_gate_status: str = "unknown",
    quality_risk_snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    job_id = str(job_id or uuid.uuid4().hex[:16])[:64]
    payload = {
        "job_id": job_id,
        "export_id": int(export_id),
        "user_id": int(user_id),
        "publisher_username": (publisher_username or "").strip()[:128],
        "task_id": int(task_id) if task_id is not None else None,
        "project_id": int(project_id) if project_id is not None else None,
        "task_name": (task_name or "").strip()[:256],
        "dataset_category_id": (
            int(dataset_category_id) if dataset_category_id is not None else None
        ),
        "dataset_category_key": (dataset_category_key or "").strip()[:128],
        "dataset_category_name": (dataset_category_name or "").strip()[:128],
        "class_names": [str(value) for value in (class_names or [])],
        "label_set_signature": (label_set_signature or "").strip()[:64],
        "class_order_signature": (class_order_signature or "").strip()[:64],
        "data_yaml_sha256": (data_yaml_sha256 or "").strip()[:64],
        "dataset_content_signature": (dataset_content_signature or "").strip()[:64],
        "training_recipe_key": (training_recipe_key or "").strip()[:128],
        "training_recipe_version": training_recipe_version,
        "runtime_profile": (runtime_profile or "").strip()[:128],
        "runtime_interface_version": runtime_interface_version,
        "publish_template_key": (publish_template_key or "").strip()[:128],
        "publish_template_name": (publish_template_name or "").strip()[:128],
        "publish_template_source": (publish_template_source or "").strip()[:32],
        "publish_template_id": publish_template_id,
        "publish_template_version": publish_template_version,
        "publish_template_sha256": (publish_template_sha256 or "").strip()[:64],
        "publish_template_model_set_id": publish_template_model_set_id,
        "preset_name": (preset_name or "").strip(),
        "environment_id": (environment_id or "").strip()[:64],
        "environment_name": (environment_name or "").strip()[:128],
        "publish_mode": (
            "manual" if str(publish_mode or "").strip().lower() == "manual" else "automatic"
        ),
        "keep_template_cache": bool(keep_template_cache),
        "bmodel_precision": (str(bmodel_precision or "").strip().lower() or None),
        "bmodel_rel_path": (str(bmodel_rel_path or "").strip() or None),
        "publish_lease_owner": (str(publish_lease_owner or "").strip()[:128] or None),
        "publish_fencing_token": (
            int(publish_fencing_token) if publish_fencing_token is not None else None
        ),
        "quality_gate_status": (quality_gate_status or "unknown").strip()[:32],
        "quality_risk_snapshot": dict(quality_risk_snapshot or {}),
        "status": "PROCESSING",
        "current_step": "validate",
        "steps": _default_steps(),
        "result": None,
        "error_message": None,
        "log_lines": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    save_job(job_id, payload)
    return job_id


def save_job(job_id: str, payload: dict[str, Any]) -> None:
    payload["updated_at"] = _now_iso()
    p = _job_json_path(job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_jobs(
    *,
    user_id: Optional[int] = None,
    page: int = 0,
    size: int = 20,
) -> tuple[List[dict[str, Any]], int]:
    """按更新时间倒序列出发布任务（可选仅某用户）。"""
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    d = jobs_dir()
    if not d.is_dir():
        return [], 0
    rows: List[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(raw: Optional[dict[str, Any]]) -> None:
        if not raw:
            return
        jid = str(raw.get("job_id") or "")
        if not jid or jid in seen:
            return
        if user_id is not None and int(raw.get("user_id") or 0) != int(user_id):
            return
        seen.add(jid)
        rows.append(raw)

    for sub in sorted(d.iterdir()):
        if not sub.is_dir():
            continue
        _append(_read_job_json(sub / JOB_JSON_NAME))

    for p in sorted(d.glob("*.json")):
        _append(_read_job_json(p))

    rows.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
    total = len(rows)
    start = page * size
    return rows[start : start + size], total


def load_job(job_id: str) -> Optional[dict[str, Any]]:
    _migrate_legacy_job_file(job_id)
    raw = _read_job_json(_job_json_path(job_id))
    if raw is not None:
        return raw
    return _read_job_json(_legacy_job_json_path(job_id))


def delete_job_dir(job_id: str) -> None:
    """删除发布任务目录（含 process.log）；忽略不存在。"""
    root = _job_dir(job_id)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    legacy = _legacy_job_json_path(job_id)
    if legacy.is_file():
        try:
            legacy.unlink()
        except OSError:
            pass


class PublishJobTracker:
    """在发布流水线各阶段更新任务 JSON。"""

    def __init__(self, job_id: str) -> None:
        from labelu.internal.common.pipeline_process_log import (
            ProcessLogWriter,
            aicm_publish_log_path,
        )

        self.job_id = job_id
        data = load_job(job_id)
        if not data:
            raise ValueError(f"publish job not found: {job_id}")
        self._data = data
        self._file_log = ProcessLogWriter(
            aicm_publish_log_path(job_id),
            logger_prefix=f"AICM publish job_id={job_id}",
            header=(
                f"===== AICM model publish job_id={job_id} "
                f"export_id={data.get('export_id')} preset={data.get('preset_name')} ====="
            ),
        )
        self._data["process_log_path"] = str(self._file_log.path)
        self._persist()

    def _persist(self) -> None:
        save_job(self.job_id, self._data)

    def _find_step(self, key: str) -> Optional[dict[str, Any]]:
        for s in self._data.get("steps") or []:
            if isinstance(s, dict) and s.get("key") == key:
                return s
        return None

    def start(self, key: str, detail: Optional[str] = None) -> None:
        self._data["current_step"] = key
        self._data["status"] = "PROCESSING"
        row = self._find_step(key)
        if row is not None:
            row["status"] = "running"
            if detail:
                row["detail"] = str(detail)[:500]
        self.log(f"STEP start {key}" + (f": {detail}" if detail else ""))
        self._persist()

    def ok(self, key: str, detail: Optional[str] = None) -> None:
        row = self._find_step(key)
        if row is not None:
            row["status"] = "ok"
            if detail:
                row["detail"] = str(detail)[:500]
        if detail:
            self.log(detail)
        self._persist()

    def skip(self, key: str, detail: Optional[str] = None) -> None:
        row = self._find_step(key)
        if row is not None:
            row["status"] = "skipped"
            if detail:
                row["detail"] = str(detail)[:500]
        self._persist()

    def log(self, line: str) -> None:
        if not line:
            return
        self._file_log.write(str(line))
        logs: List[str] = list(self._data.get("log_lines") or [])
        logs.append(str(line)[:800])
        self._data["log_lines"] = logs[-150:]
        self._persist()

    def update_metadata(self, **values: Any) -> None:
        for key, value in values.items():
            self._data[key] = value
        self._persist()

    def complete(self, result: dict[str, Any]) -> None:
        self.ok("finish", "发布流程结束")
        self._data["status"] = "COMPLETED" if result.get("ok") else "FAILED"
        self._data["current_step"] = "finish"
        self._data["result"] = result
        upload_name = result.get("upload_name")
        if upload_name:
            self.log(f"上传模型名 name={upload_name}")
        if result.get("ok"):
            self.log("AICM 发布成功（error_code=0）")
        else:
            self.log(result.get("error_message") or "发布未成功")
        if not result.get("ok"):
            self._data["error_message"] = result.get("error_message") or "发布未成功"
        self._persist()
        self._close_file_log()

    def fail(self, message: str, *, failed_step: Optional[str] = None) -> None:
        key = failed_step or self._data.get("current_step") or "finish"
        row = self._find_step(str(key))
        if row is not None and row.get("status") == "running":
            row["status"] = "error"
            row["detail"] = str(message)[:500]
        self._data["status"] = "FAILED"
        self._data["error_message"] = str(message)[:1000]
        self.log(message)
        self._persist()
        self._file_log.close()

    def _close_file_log(self) -> None:
        if getattr(self, "_file_log", None) is not None:
            self._file_log.close()
            self._file_log = None  # type: ignore[assignment]
