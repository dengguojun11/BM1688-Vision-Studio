#!/usr/bin/env python3
"""
在算能盒上执行 BM1688 板端验证 + 可选 pythonTest bmodel 评估（由 LabelU 服务器 SSH 下发）。

用法（边端）:
  export SOPHON_DEMO_ROOT=/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det
  python3 edge_board_validate_runner.py --work-dir /data/labelu_edge/jobs/export-25/work

注意：边端容器内 python3 可能为 3.6（算能盒 Docker 常见），本脚本保持 3.6+ 语法兼容。
SOPHON_DEMO_ROOT 无效时跳过 sophon-demo 例程，仍执行 pythonTest bmodel mAP 评估。
"""

import argparse
import hashlib
import importlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _sophon_demo_candidates() -> list:
    env_val = (os.environ.get("SOPHON_DEMO_ROOT") or "").strip()
    edge_root = (os.environ.get("LABELU_EDGE_ROOT") or "/data/labelu_edge").rstrip("/")
    paths = (
        [env_val]
        if env_val and (env_val == edge_root or env_val.startswith(edge_root + "/"))
        else []
    )
    seen = set()
    out = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(Path(p).expanduser())
    return out


def _resolve_sophon_demo_root():
    """返回 (Path|None, tried_paths_str)。"""
    tried = []
    for p in _sophon_demo_candidates():
        tried.append(str(p))
        if p.is_dir() and (p / "python" / "yolov8_bmcv.py").is_file():
            return p.resolve(), ", ".join(tried)
    return None, ", ".join(tried)


def _repo_ops_dir(work):
    ops = work / "scripts" / "ops"
    if (ops / "labelu_board_agent.py").is_file():
        return ops
    here = Path(__file__).resolve().parent
    for cand in (here / "ops", here.parent.parent / "scripts" / "ops"):
        if (cand / "labelu_board_agent.py").is_file():
            return cand
    return ops


def _load_run_config(work):
    p = work / "run_config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _quality_rule_reasons(rule):
    """Validate the immutable edge threshold snapshot on the board.

    The board must consume the server snapshot verbatim.  Missing or malformed
    limits are a hard failure; silently using a historical default would make
    a result impossible to audit.
    """
    reasons = []
    if not isinstance(rule, dict):
        return ["quality_rule_missing"]
    if not str(rule.get("version") or "").strip():
        reasons.append("quality_rule_version_missing")
    if str(rule.get("metric") or "mAP50").strip() != "mAP50":
        reasons.append("quality_rule_metric_invalid")
    if str(rule.get("threshold_unit") or "map50_absolute_drop").strip() != "map50_absolute_drop":
        reasons.append("quality_rule_threshold_unit_invalid")
    edge = rule.get("edge") if isinstance(rule.get("edge"), dict) else {}
    if str(edge.get("baseline_variant") or "").strip().lower() != "fp32":
        reasons.append("quality_rule_baseline_invalid")
    limits = edge.get("variant_max_drop")
    if not isinstance(limits, dict):
        reasons.append("quality_rule_variant_limits_missing")
    else:
        for tag in ("fp16", "int8"):
            try:
                value = float(limits.get(tag))
            except (TypeError, ValueError):
                value = float("nan")
            if not math.isfinite(value) or value < 0 or value > 1:
                reasons.append("quality_rule_{}_limit_invalid".format(tag))
    return reasons


def _manifest_signature(work):
    """Return the committed fixed-evaluation manifest digest, if present."""
    manifest_path = Path(work) / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    declared = str(payload.get("manifest_sha256") or "").strip()
    if not declared:
        return None
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256", None)
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != declared:
        return "__manifest_tampered__"
    # The transport package may place the same images under different
    # directories (Board Agent vs SSH compatibility bundle).  Prefer the
    # sealed content identity when present; old bundles fall back to the
    # package manifest digest.
    content_signature = str(payload.get("fixed_eval_signature") or "").strip()
    return content_signature or declared


def _fixed_eval_binding(work, cfg):
    """Check that this run consumed the server-committed fixed eval pack."""
    expected = str((cfg or {}).get("fixed_eval_signature") or "").strip()
    if not expected and not (cfg or {}).get("require_fixed_eval_binding"):
        return {"required": False, "passed": True, "reasons": []}
    actual = _manifest_signature(work)
    reasons = []
    if not expected:
        reasons.append("fixed_eval_signature_missing")
    if actual is None:
        reasons.append("fixed_eval_manifest_missing")
    elif actual == "__manifest_tampered__":
        reasons.append("fixed_eval_manifest_tampered")
    elif expected and actual != expected:
        reasons.append("fixed_eval_signature_mismatch")
    try:
        count = int((cfg or {}).get("eval_image_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        reasons.append("fixed_eval_image_count_missing")
    try:
        labeled_count = int((cfg or {}).get("labeled_image_count") or 0)
    except (TypeError, ValueError):
        labeled_count = 0
    if labeled_count <= 0:
        reasons.append("fixed_eval_labeled_image_count_missing")
    manifest_path = Path(work) / "manifest.json"
    manifest_payload = {}
    if manifest_path.is_file():
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                manifest_payload = parsed
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            manifest_payload = {}
    try:
        # Packs created before the fixed-evaluation field names were added
        # stored the same value as ``labeled_count``.  Accept that legacy
        # spelling so a valid local pack is not rejected as unlabeled.
        manifest_labeled_count = int(
            manifest_payload.get("labeled_image_count")
            or manifest_payload.get("labeled_count")
            or 0
        )
    except (TypeError, ValueError):
        manifest_labeled_count = 0
    if manifest_labeled_count <= 0:
        reasons.append("fixed_eval_labels_missing")
    elif labeled_count and manifest_labeled_count != labeled_count:
        reasons.append("fixed_eval_labeled_image_count_mismatch")
    label_mappings = manifest_payload.get("label_mappings")
    if not isinstance(label_mappings, list):
        # ``file_mappings`` is the pre-contract name used by selected-sample
        # packs.  It contains the same annotation evidence per image.
        label_mappings = manifest_payload.get("file_mappings")
    # New packs mark annotation evidence explicitly.  Older fixed-evaluation
    # packs used a canonical image/path mapping without those flags; accept
    # that representation when both referenced files are present so a valid
    # immutable pack is not rejected solely because of a schema evolution.
    annotated_mappings = []
    if isinstance(label_mappings, list):
        for item in label_mappings:
            if not isinstance(item, dict):
                continue
            if item.get("has_annotation") is True or item.get("has_annotation_file") is True:
                annotated_mappings.append(item)
                continue
            image_rel = str(item.get("image") or "").strip()
            label_rel = str(item.get("path") or "").strip()
            if not image_rel or not label_rel:
                continue
            image_path = Path(work) / image_rel
            label_path = Path(work) / label_rel
            if image_path.is_file() and label_path.is_file():
                annotated_mappings.append(item)
    if len(annotated_mappings) < manifest_labeled_count:
        reasons.append("fixed_eval_label_manifest_incomplete")
    return {
        "required": True,
        "passed": not reasons,
        "expected_signature": expected or None,
        "actual_signature": actual,
        "image_count": count,
        "labeled_image_count": labeled_count,
        "reasons": reasons,
    }
def _verify_edge_run_config(work, cfg):
    """Reuse the Board Agent verifier for SSH bundles when available."""
    required = bool((cfg or {}).get("edge_signature_required"))
    if not (cfg or {}).get("manifest_ed25519_signature"):
        if required:
            raise RuntimeError("边端清单签名缺失")
        # 本地/兼容模式没有清单签名，但仍然要在设备侧记录执行世代。
        # 这样同一份 BModel 重新验证可以幂等通过，旧世代不能被误用，且
        # 报告能够明确证明本次运行消费了当前编译档案。
        generation = str((cfg or {}).get("execution_generation") or "").strip()
        ops = _repo_ops_dir(work)
        helper = ops / "labelu_board_agent.py"
        if generation and helper.is_file():
            sys.path.insert(0, str(ops))
            try:
                import labelu_board_agent  # type: ignore

                consume = getattr(
                    labelu_board_agent, "consume_edge_execution_generation", None
                )
                if callable(consume):
                    state = consume(
                        cfg,
                        expected_export_id=cfg.get("export_id"),
                    )
                    return {
                        "status": "unsigned_compat",
                        "required": False,
                        "execution_generation_state": state,
                    }
            finally:
                try:
                    sys.path.remove(str(ops))
                except ValueError:
                    pass
        return {"status": "unsigned_compat", "required": False}
    ops = _repo_ops_dir(work)
    helper = ops / "labelu_board_agent.py"
    if not helper.is_file():
        raise RuntimeError("缺少边端清单验签脚本")
    sys.path.insert(0, str(ops))
    try:
        import labelu_board_agent  # type: ignore
        if not callable(getattr(labelu_board_agent, "consume_edge_execution_generation", None)):
            raise RuntimeError("边端清单验签脚本缺少执行世代持久化门禁")
        return labelu_board_agent.verify_edge_run_config(work, cfg)
    finally:
        try:
            sys.path.remove(str(ops))
        except ValueError:
            pass
def _cfg_threshold_values(cfg):
    """Return threshold values with the nested contract taking precedence.

    New bundles carry the immutable contract under ``threshold_contract``;
    older bundles keep the fields at the run-config root.  Merging here keeps
    every evaluator entry point on the same compatibility path.
    """
    base = dict(cfg or {})
    nested = base.get("threshold_contract")
    if isinstance(nested, dict):
        base.update(nested)
    return base


def _cfg_conf_thres(cfg):
    return float(_cfg_threshold_values(cfg).get("conf_thres", 0.35))


def _cfg_nms_thres(cfg):
    values = _cfg_threshold_values(cfg)
    return float(values.get("nms_thres", values.get("iou_thres", 0.7)))


def _cfg_match_iou_thres(cfg):
    values = _cfg_threshold_values(cfg)
    return float(values.get("match_iou_thres", values.get("iou_thres", 0.5)))


def _cfg_threshold_unit(cfg):
    values = _cfg_threshold_values(cfg)
    return str(values.get("threshold_unit") or "ratio_0_1")


def _cfg_threshold_source(cfg):
    values = _cfg_threshold_values(cfg)
    return str(
        values.get("source") or values.get("threshold_source") or "system_default"
    )


def _json_env_class_names(class_names):
    """Python 3.6 subprocess 环境变量须 ASCII；ensure_ascii 后 json.loads 仍得中文类名。"""
    return json.dumps(class_names, ensure_ascii=True)


def _subprocess_env_py36_compatible(env):
    """边端 Python 3.6 的 Popen 对 env 值做 fsencode，非 ASCII 会 UnicodeEncodeError。"""
    if sys.version_info >= (3, 7):
        return env
    safe = {}
    for key, val in env.items():
        if val is None:
            continue
        text = str(val)
        try:
            os.fsencode(text)
        except UnicodeEncodeError:
            continue
        safe[key] = text
    return safe


def _subprocess_run_compat(cmd, *, cwd, env, timeout_sec):
    """边端 Python 3.6 无 text=；避免 universal_newlines 按 ASCII 解码中文日志。"""
    env = _subprocess_env_py36_compatible(env)
    if sys.version_info >= (3, 7):
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _load_edge_requirements(work):
    path = work / "edge_requirements.txt"
    if not path.is_file():
        return [("tqdm", "tqdm==4.64.1")]
    requirements = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        requirement = raw.strip()
        if not requirement or requirement.startswith("#"):
            continue
        package = requirement.split("[", 1)[0]
        for separator in ("==", ">=", "<=", "~=", ">", "<"):
            package = package.split(separator, 1)[0]
        package = package.strip()
        if package:
            requirements.append((package, requirement))
    return requirements or [("tqdm", "tqdm==4.64.1")]


def _module_available(module_name):
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _run_pip_install(args, timeout_sec=300):
    target = (
        os.environ.get("LABELU_PYTHON_PACKAGES")
        or "/data/labelu_edge/runtime/python-packages"
    )
    cmd = [sys.executable, "-m", "pip", "--target", target] + list(args)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return int(proc.returncode), (stdout or "") + "\n" + (stderr or "")
    except Exception as exc:
        return 1, str(exc)


def _ensure_edge_runtime_dependencies(work):
    """先在线安装缺失必需依赖，失败后使用 bundle 内 wheel 离线安装。"""
    requirements = _load_edge_requirements(work)
    wheel_dir = work / "edge_wheels"
    result = {
        "status": "ok",
        "required": [],
        "offline_wheels": sorted(p.name for p in wheel_dir.glob("*.whl")),
        "attempts": [],
    }
    for module_name, requirement in requirements:
        item = {"module": module_name, "requirement": requirement}
        if _module_available(module_name):
            item["status"] = "already_available"
            result["required"].append(item)
            continue

        code, output = _run_pip_install(
            [
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--no-cache-dir",
                requirement,
            ]
        )
        item["online_exit_code"] = code
        item["online_output_tail"] = output[-1200:]
        if code == 0 and _module_available(module_name):
            item["status"] = "installed_online"
            result["required"].append(item)
            continue

        if wheel_dir.is_dir() and any(wheel_dir.glob("*.whl")):
            code, output = _run_pip_install(
                [
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--no-index",
                    "--find-links",
                    str(wheel_dir),
                    requirement,
                ]
            )
            item["offline_exit_code"] = code
            item["offline_output_tail"] = output[-1200:]
            if code == 0 and _module_available(module_name):
                item["status"] = "installed_offline"
                result["required"].append(item)
                continue

        item["status"] = "failed"
        result["required"].append(item)
        result["status"] = "failed"
        result["error"] = (
            "边端必需 Python 依赖安装失败: {}。已尝试在线 pip 和 bundle 离线 wheel。".format(
                requirement
            )
        )
        break

    (work / "output").mkdir(parents=True, exist_ok=True)
    (work / "output" / "dependency_setup.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _skipped_board_report(reason, tried):
    return {
        "passed": False,
        "skipped": True,
        "status": "skipped",
        "reason": reason,
        "tried_paths": tried,
        "steps": [],
        "summary": (
            "sophon-demo 板端例程已跳过（未找到有效 SOPHON_DEMO_ROOT）；"
            "将仅执行 pythonTest bmodel 评估"
        ),
    }


def _run_sophon_bmodel_eval(
    work,
    cfg,
    demo_root=None,
    *,
    model_file=None,
    model_tag="int8",
    out_dir=None,
    performance_enabled=None,
):
    """文档 3.6.2 边端 bmodel：sophon.sail 评估（BM1688）。"""
    scripts = work / "scripts" / "sop_eval"
    main_py = scripts / "sophon_bmodel_evaluation.py"
    if not main_py.is_file():
        return {"status": "skip", "detail": "未下发 sophon_bmodel_evaluation.py"}

    img_dir = work / "eval_data" / "images" / "test"
    if not img_dir.is_dir():
        img_dir = work / "eval_data" / "images" / "val"
    xml_dir = work / "eval_data" / "labelsxml" / "test"
    if not xml_dir.is_dir():
        xml_dir = work / "eval_data" / "labelsxml" / "val"
    model_dir = work / "models" / "BM1688"
    if not model_dir.is_dir():
        return {"status": "skip", "detail": "无 models/BM1688"}

    out_dir = out_dir or work / "output" / "results_bmodel"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["EVAL_DATA_PATH"] = str(img_dir.resolve())
    env["EVAL_XML_DIR"] = str(xml_dir.resolve()) if xml_dir.is_dir() else ""
    env["EVAL_MODEL_PATH"] = str(model_dir.resolve())
    if model_file:
        env["EVAL_MODEL_FILE"] = str(model_file)
    env["EVAL_MODEL_TAG"] = str(model_tag or "int8")
    env["EVAL_SAVE_DIR"] = str(out_dir.resolve())
    env["EVAL_CONF_THRES"] = str(_cfg_conf_thres(cfg))
    env["EVAL_NMS_THRES"] = str(_cfg_nms_thres(cfg))
    env["EVAL_IOU_THRES"] = str(_cfg_match_iou_thres(cfg))
    env["EVAL_INPUT_H"] = str(cfg.get("imgsz", 640))
    env["EVAL_INPUT_W"] = str(cfg.get("imgsz", 640))
    if performance_enabled is None:
        performance_enabled = bool(cfg.get("performance_enabled", True))
    env["EVAL_PERFORMANCE_ENABLED"] = "1" if performance_enabled else "0"
    env["EVAL_PERFORMANCE_WARMUP_COUNT"] = str(
        int(cfg.get("performance_warmup_count", 50))
    )
    env["EVAL_PERFORMANCE_REPEAT_COUNT"] = str(
        int(cfg.get("performance_repeat_count", 1000))
    )
    env["EVAL_RESOURCE_INTERVAL_SECONDS"] = str(
        float(cfg.get("performance_resource_interval_seconds", 1.0))
    )
    env["EVAL_STABILITY_SECONDS"] = str(
        float(cfg.get("performance_stability_seconds", 0))
        if str(model_tag or "int8").lower() == "int8"
        else 0
    )
    if cfg.get("class_names"):
        env["EVAL_CLASS_NAMES"] = _json_env_class_names(cfg["class_names"])
    if demo_root is not None and Path(demo_root).is_dir():
        env["SOPHON_DEMO_ROOT"] = str(Path(demo_root).resolve())

    cmd = [sys.executable, str(main_py)]
    timeout_sec = int(cfg.get("timeout_sec", 7200))
    proc = _subprocess_run_compat(
        cmd, cwd=str(scripts), env=env, timeout_sec=timeout_sec
    )
    summary_path = out_dir / "eval_summary.json"
    metrics = None
    if summary_path.is_file():
        try:
            metrics = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    stderr_tail = (proc.stderr or "")[-2000:]
    warnings = []
    if isinstance(metrics, dict) and isinstance(metrics.get("warnings"), list):
        warnings.extend(
            item for item in metrics["warnings"] if isinstance(item, dict)
        )
    if stderr_tail and "曲线图未生成" in stderr_tail and not warnings:
        warnings.append(
            {
                "code": "PLOT_DEPENDENCY_MISSING",
                "message": "曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）",
            }
        )
    return {
        "model_tag": str(model_tag or "int8"),
        "model_filename": Path(model_file).name if model_file else None,
        "status": "ok" if proc.returncode == 0 else "fail",
        "backend": (metrics or {}).get("backend") or "sophon.sail",
        "returncode": proc.returncode,
        "results_dir": str(out_dir),
        "metrics": metrics,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": stderr_tail,
        "warnings": warnings,
        "report_md": str(out_dir / "evaluation_report.md"),
    }


def _run_python_test(work, cfg):
    """文档 3.6.2 边端 bmodel（RKNN 回退，BM1688 通常不可用）。"""
    scripts = work / "scripts" / "sop_eval"
    main_py = scripts / "pythonTest_evaluation.py"
    if not main_py.is_file():
        return {"status": "skip", "detail": "未下发 pythonTest_evaluation.py"}

    img_dir = work / "eval_data" / "images" / "test"
    if not img_dir.is_dir():
        img_dir = work / "eval_data" / "images" / "val"
    xml_dir = work / "eval_data" / "labelsxml" / "test"
    if not xml_dir.is_dir():
        xml_dir = work / "eval_data" / "labelsxml" / "val"
    model_dir = work / "models" / "BM1688"
    if not model_dir.is_dir():
        return {"status": "skip", "detail": "无 models/BM1688"}

    out_dir = work / "output" / "results_bmodel"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["EVAL_DATA_PATH"] = str(img_dir.resolve())
    env["EVAL_XML_DIR"] = str(xml_dir.resolve()) if xml_dir.is_dir() else ""
    env["EVAL_MODEL_PATH"] = str(model_dir.resolve())
    env["EVAL_SAVE_DIR"] = str(out_dir.resolve())
    env["EVAL_CONF_THRES"] = str(_cfg_conf_thres(cfg))
    env["EVAL_NMS_THRES"] = str(_cfg_nms_thres(cfg))
    env["EVAL_IOU_THRES"] = str(_cfg_match_iou_thres(cfg))
    if cfg.get("class_names"):
        env["EVAL_CLASS_NAMES"] = _json_env_class_names(cfg["class_names"])

    cmd = [sys.executable, str(main_py)]
    timeout_sec = int(cfg.get("timeout_sec", 7200))
    proc = _subprocess_run_compat(
        cmd, cwd=str(scripts), env=env, timeout_sec=timeout_sec
    )
    summary_path = out_dir / "eval_summary.json"
    metrics = None
    if summary_path.is_file():
        try:
            metrics = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "status": "ok" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "results_dir": str(out_dir),
        "metrics": metrics,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "report_md": str(out_dir / "evaluation_report.md"),
    }


def _discover_bmodel_variants(work):
    model_dir = work / "models" / "BM1688"
    variants = {}
    if not model_dir.is_dir():
        return variants
    for tag in ("fp32", "fp16", "int8"):
        candidates = sorted(model_dir.glob("*_{}_1b.bmodel".format(tag)))
        if candidates:
            variants[tag] = candidates[0]
    return variants


def _plate_detection_label_paths(work, image_stem):
    txt_candidates = (
        work / "eval_data" / "labels" / "test" / (image_stem + ".txt"),
        work / "eval_data" / "labels" / "val" / (image_stem + ".txt"),
        work / "eval_data" / "labelsxml" / "test" / (image_stem + ".txt"),
        work / "eval_data" / "labelsxml" / "val" / (image_stem + ".txt"),
    )
    xml_candidates = (
        work / "eval_data" / "labelsxml" / "test" / (image_stem + ".xml"),
        work / "eval_data" / "labelsxml" / "val" / (image_stem + ".xml"),
    )
    txt_path = next(
        (path for path in txt_candidates if path.is_file()), txt_candidates[0]
    )
    xml_path = next(
        (path for path in xml_candidates if path.is_file()), xml_candidates[0]
    )
    return txt_path, xml_path


def _plate_detection_precision_evaluation(work, image_dir, out_dir, contract_path, cfg):
    """Evaluate specialized plate detections against the packed bbox labels."""
    result_candidates = (
        out_dir / "results.json",
        out_dir / "detections.json",
    )
    detections_path = next(
        (path for path in result_candidates if path.is_file()), result_candidates[0]
    )
    if not detections_path.is_file():
        return {
            "precision_eval_status": "not_computed",
            "precision_eval_reason": "plate_detection_results_missing",
        }

    sop_eval_dir = work / "scripts" / "sop_eval"
    if not (sop_eval_dir / "evaluation_utils.py").is_file():
        sop_eval_dir = Path(__file__).resolve().parent / "sop_eval"
    if not (sop_eval_dir / "evaluation_utils.py").is_file():
        return {
            "precision_eval_status": "not_computed",
            "precision_eval_reason": "evaluation_utils_missing",
        }
    sop_eval_text = str(sop_eval_dir)
    if sop_eval_text not in sys.path:
        sys.path.insert(0, sop_eval_text)

    import cv2
    from evaluation_utils import (
        Detection,
        _empty_stats,
        load_yolo_labels,
        match_detections,
        summarize_metrics,
    )

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    network = (
        contract.get("network") if isinstance(contract.get("network"), dict) else {}
    )
    class_names = [
        str(value)
        for value in (network.get("class_names") or ["doubleEP"])
        if str(value).strip()
    ] or ["doubleEP"]
    class_index = {name: index for index, name in enumerate(class_names)}
    payload = json.loads(detections_path.read_text(encoding="utf-8"))
    rows = payload.get("images") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {
            "precision_eval_status": "not_computed",
            "precision_eval_reason": "plate_detection_results_invalid",
        }

    class_count = len(class_names)
    per_class = {cid: _empty_stats() for cid in range(class_count)}
    label_counts = [0] * class_count
    evaluation_samples = []
    evaluated_images = 0
    match_iou = _cfg_match_iou_thres(cfg)

    for row in rows:
        if not isinstance(row, dict):
            continue
        image_value = str(row.get("image") or "")
        image_path = Path(image_value)
        if not image_path.is_file():
            image_path = image_dir / image_path.name
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        image_stem = image_path.stem
        txt_path, xml_path = _plate_detection_label_paths(work, image_stem)
        if not txt_path.is_file() and not xml_path.is_file():
            continue
        img_h, img_w = image.shape[:2]
        targets = load_yolo_labels(
            str(txt_path),
            img_w,
            img_h,
            class_names=class_names,
            xml_label_path=str(xml_path),
        )
        predictions = []
        for item in row.get("detections") or []:
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox_xyxy") or item.get("box")
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            label = str(item.get("label") or "")
            class_id = class_index.get(label, 0 if class_count == 1 else -1)
            if class_id < 0:
                continue
            predictions.append(
                Detection(
                    bbox[0],
                    bbox[1],
                    bbox[2],
                    bbox[3],
                    class_id,
                    item.get("score") if item.get("score") is not None else 1.0,
                )
            )
        matched = match_detections(
            predictions,
            targets,
            class_count,
            iou_threshold=match_iou,
        )
        for cid in range(class_count):
            label_counts[cid] += sum(1 for target in targets if target.class_id == cid)
            for key in ("tp", "fp", "fn"):
                per_class[cid][key] += int(matched[cid][key])
            per_class[cid]["scores"].extend(matched[cid]["scores"])
            per_class[cid]["matches"].extend(matched[cid]["matches"])
        evaluation_samples.append((predictions, targets))
        evaluated_images += 1

    if evaluated_images <= 0 or not any(label_counts):
        return {
            "precision_eval_status": "not_computed",
            "precision_eval_reason": "labeled_images_missing",
            "images": evaluated_images,
        }

    inference = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    inference_seconds = float(
        (inference.get("inference") or {}).get("elapsed_seconds") or 0.0
    )
    summary = summarize_metrics(
        class_names,
        per_class,
        label_counts,
        evaluated_images,
        inference_seconds,
        evaluation_samples=evaluation_samples,
    )
    metrics = {
        "precision_eval_status": "computed",
        "precision_eval_type": "bbox",
        "precision_eval_iou_threshold": match_iou,
        "map50": summary.get("map50"),
        "map50_95": summary.get("map5095"),
        "map50_95_status": summary.get("map5095_status") or "computed",
        "precision": summary.get("overall_precision"),
        "recall": summary.get("overall_recall"),
        "f1": summary.get("overall_f1"),
        "speed_avg_ms": summary.get("speed_avg_ms"),
        "images": summary.get("images"),
        "class_metrics": summary.get("per_class_rows"),
        "keypoint_metrics_status": "not_computed",
        "keypoint_metrics_reason": "bbox_release_gate_only",
    }
    (out_dir / "eval_summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_lines = [
        "# Plate Detection BM1688 Evaluation",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        "| Precision | {} |".format(metrics["precision"]),
        "| Recall | {} |".format(metrics["recall"]),
        "| F1 | {} |".format(metrics["f1"]),
        "| mAP50 | {} |".format(metrics["map50"]),
        "| mAP50-95 | {} |".format(metrics["map50_95"]),
        "| Images | {} |".format(metrics["images"]),
        "",
        "Keypoint metrics are not part of the current bbox release gate.",
    ]
    (out_dir / "evaluation_report.md").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8"
    )
    return metrics


def _run_plate_evaluation(
    work,
    cfg,
    *,
    contract_name,
    model_name,
    model_type,
):
    model_dir = work / "models" / "BM1688"
    contract_path = model_dir / contract_name
    model_path = model_dir / model_name
    validator = work / "scripts" / "plate_validation" / "validate.py"
    image_dir = work / "eval_data" / "images" / "test"
    if not image_dir.is_dir():
        image_dir = work / "eval_data" / "images" / "val"
    out_dir = work / "output" / "results_bmodel"
    out_dir.mkdir(parents=True, exist_ok=True)

    missing = [
        str(path)
        for path in (contract_path, model_path, validator, image_dir)
        if not path.exists()
    ]
    if missing:
        return {
            "status": "fail",
            "model_tag": "fp16",
            "model_filename": model_path.name,
            "backend": "{}_bm1688".format(model_type),
            "detail": "missing plate validation assets: {}".format(", ".join(missing)),
            "required_models": ["fp16"],
            "completed_models": [],
            "missing_or_failed_models": ["fp16"],
        }

    cmd = [
        sys.executable,
        str(validator),
        "--model",
        str(model_dir),
        "--images",
        str(image_dir),
        "--output",
        str(out_dir),
        "--conf",
        str(_cfg_conf_thres(cfg)),
        "--nms",
        str(_cfg_nms_thres(cfg)),
        "--iou",
        str(_cfg_match_iou_thres(cfg)),
        "--dev-id",
        str(int(cfg.get("device_id") or 0)),
    ]
    proc = _subprocess_run_compat(
        cmd,
        cwd=str(validator.parent),
        env=os.environ.copy(),
        timeout_sec=int(cfg.get("timeout_sec", 7200)),
    )
    summary_path = out_dir / "summary.json"
    metrics = None
    if summary_path.is_file():
        try:
            metrics = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if (
        isinstance(metrics, dict)
        and metrics.get("runtime_ok") is True
        and model_type == "plate_detection"
    ):
        try:
            precision_metrics = _plate_detection_precision_evaluation(
                work, image_dir, out_dir, contract_path, cfg
            )
        except Exception as exc:
            precision_metrics = {
                "precision_eval_status": "failed",
                "precision_eval_reason": str(exc),
            }
        metrics.update(precision_metrics)
        summary_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    succeeded = bool(
        proc.returncode == 0
        and isinstance(metrics, dict)
        and metrics.get("runtime_ok") is True
        and metrics.get("model_type") == model_type
    )
    model_result = {
        "model_tag": "fp16",
        "model_filename": model_path.name,
        "status": "ok" if succeeded else "fail",
        "backend": "{}_bm1688".format(model_type),
        "returncode": proc.returncode,
        "results_dir": str(out_dir),
        "metrics": metrics,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "report_md": (
            str(out_dir / "evaluation_report.md")
            if (out_dir / "evaluation_report.md").is_file()
            else None
        ),
        "eval_summary_path": (
            str(out_dir / "eval_summary.json")
            if (out_dir / "eval_summary.json").is_file()
            else None
        ),
    }
    result = dict(model_result)
    result["models"] = {"fp16": model_result}
    result["required_models"] = ["fp16"]
    result["completed_models"] = ["fp16"] if succeeded else []
    result["missing_or_failed_models"] = [] if succeeded else ["fp16"]
    return result


def _run_plate_recognition_evaluation(work, cfg):
    return _run_plate_evaluation(
        work,
        cfg,
        contract_name="plate_recognition_contract.json",
        model_name="plate_recognition_fp16_1b.bmodel",
        model_type="plate_recognition",
    )


def _run_plate_detection_evaluation(work, cfg):
    return _run_plate_evaluation(
        work,
        cfg,
        contract_name="plate_detection_contract.json",
        model_name="plate_detection_fp16_1b.bmodel",
        model_type="plate_detection",
    )


def _metric_float(model_result, key):
    metrics = (model_result or {}).get("metrics")
    if not isinstance(metrics, dict):
        return None
    try:
        value = float(metrics.get(key))
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _generic_variant_usability_gate(results, cfg):
    """Require all three artifacts and apply the frozen versioned edge gate.

    Older bundles that only carry ``max_int8_map50_drop`` are no longer
    accepted as a quality contract. They must be repacked with a versioned
    ``quality_rule`` snapshot so a historical loose threshold cannot leak into
    a new release decision.
    """
    required = ("fp32", "fp16", "int8")
    failed = [tag for tag in required if (results.get(tag) or {}).get("status") != "ok"]
    quality_rule = cfg.get("quality_rule") if isinstance(cfg, dict) else None
    legacy_threshold_present = isinstance(cfg, dict) and "max_int8_map50_drop" in cfg
    edge_rule = quality_rule.get("edge") if isinstance(quality_rule, dict) else {}
    if not isinstance(edge_rule, dict):
        edge_rule = {}
    limits = edge_rule.get("variant_max_drop")
    if not isinstance(limits, dict):
        limits = {"fp16": 0.010, "int8": 0.020}
    try:
        int8_limit = float(limits.get("int8", 0.020))
    except (TypeError, ValueError):
        int8_limit = 0.020
    gate = {
        "status": "pass",
        "required_models": list(required),
        "missing_or_failed_models": failed,
        "quality_rule_version": (quality_rule or {}).get("version") or "bm1688-yolo11-detection-v1",
        "quality_rule_source": "snapshot" if isinstance(quality_rule, dict) else "missing",
        "max_int8_map50_drop": int8_limit,
        "map50": {tag: _metric_float(results.get(tag), "map50") for tag in required},
        "reasons": [],
    }
    if legacy_threshold_present and not isinstance(quality_rule, dict):
        gate["status"] = "fail"
        gate["reasons"].append("legacy_quality_rule_field")
        return gate
    if failed:
        gate["status"] = "fail"
        gate["reasons"].append("required_model_missing_or_failed")
        return gate

    if not isinstance(quality_rule, dict):
        gate["status"] = "fail"
        gate["reasons"].append("quality_rule_missing")
        return gate

    rule_reasons = _quality_rule_reasons(quality_rule)
    if rule_reasons:
        gate["status"] = "fail"
        gate["reasons"].extend(rule_reasons)
        return gate
    fixed_eval = _fixed_eval_binding(cfg.get("_work_dir") or Path.cwd(), cfg)
    gate["fixed_eval"] = fixed_eval
    if not fixed_eval.get("passed"):
        gate["status"] = "fail"
        gate["reasons"].extend(fixed_eval.get("reasons") or [])
        return gate

    labeled_images = int(cfg.get("labeled_image_count") or 0)
    if labeled_images <= 0:
        # A run without ground truth can prove that the runtime loads and
        # executes, but it cannot prove conversion accuracy or release safety.
        # Keep the diagnostic reason while making the usability gate fail
        # closed; callers must not turn ``not_applicable`` into ``ok``.
        gate["status"] = "fail"
        gate["reasons"].append("no_labeled_images")
        return gate

    fp32_map50 = gate["map50"]["fp32"]
    fp16_map50 = gate["map50"]["fp16"]
    int8_map50 = gate["map50"]["int8"]
    if fp32_map50 is None or int8_map50 is None:
        gate["status"] = "fail"
        gate["reasons"].append("variant_metrics_missing")
        return gate

    # FP32 is the stable edge baseline.  FP16 and INT8 are checked separately
    # so a bad FP16 result cannot be hidden by taking max(FP32, FP16).
    baseline = fp32_map50
    fp16_drop = max(0.0, baseline - fp16_map50) if fp16_map50 is not None else None
    int8_drop = max(0.0, baseline - int8_map50)
    gate["baseline_variant"] = "fp32"
    gate["baseline_map50"] = baseline
    gate["variant_map50_drop"] = {"fp16": fp16_drop, "int8": int8_drop}
    gate["fp16_map50_drop"] = fp16_drop
    gate["int8_map50_drop"] = int8_drop
    if baseline > 0.0 and int8_map50 <= 0.0:
        gate["status"] = "fail"
        gate["reasons"].append("int8_no_usable_detections")
    elif fp16_drop is not None and fp16_drop > float(limits.get("fp16", 0.010)) + 1e-9:
        gate["status"] = "fail"
        gate["reasons"].append("fp16_map50_drop_exceeded")
    elif int8_drop > gate["max_int8_map50_drop"]:
        gate["status"] = "fail"
        gate["reasons"].append("int8_map50_drop_exceeded")
    return gate


def _run_all_bmodel_evaluations(work, cfg, demo_root=None):
    if (work / "models" / "BM1688" / "plate_recognition_contract.json").is_file():
        return _run_plate_recognition_evaluation(work, cfg)
    if (work / "models" / "BM1688" / "plate_detection_contract.json").is_file():
        return _run_plate_detection_evaluation(work, cfg)

    variants = _discover_bmodel_variants(work)
    if not variants:
        return {
            "status": "fail",
            "detail": "models/BM1688 鐩綍涓湭鎵惧埌 bmodel",
            "models": {},
        }

    results = {}
    root_out = work / "output" / "results_bmodel"
    configured_performance_tags = cfg.get("performance_model_tags")
    performance_tags = (
        set(configured_performance_tags)
        if isinstance(configured_performance_tags, (list, tuple, set))
        else {"fp32", "fp16", "int8"}
    )
    performance_enabled = bool(cfg.get("performance_enabled", True))
    for tag in ("fp32", "fp16", "int8"):
        model_file = variants.get(tag)
        if model_file is None:
            results[tag] = {
                "model_tag": tag,
                "status": "missing",
                "detail": "缂哄皯 {} bmodel".format(tag.upper()),
            }
            continue
        variant_out = root_out if tag == "int8" else root_out / "variants" / tag
        results[tag] = _run_sophon_bmodel_eval(
            work,
            cfg,
            demo_root=demo_root,
            model_file=model_file,
            model_tag=tag,
            out_dir=variant_out,
            performance_enabled=(performance_enabled and tag in performance_tags),
        )

    int8_result = dict(results.get("int8") or {})
    if int8_result.get("status") != "ok":
        fallback = _run_python_test(work, cfg)
        if fallback.get("status") == "ok":
            fallback["model_tag"] = "int8"
            fallback["model_filename"] = (
                variants.get("int8").name if variants.get("int8") else None
            )
            results["int8"] = fallback
            int8_result = dict(fallback)

    required = ("fp32", "fp16", "int8")
    gate_cfg = dict(cfg or {})
    gate_cfg["_work_dir"] = str(work)
    usability_gate = _generic_variant_usability_gate(results, gate_cfg)
    complete = usability_gate.get("status") == "pass"
    int8_result["status"] = "ok" if complete else "fail"
    int8_result["models"] = results
    int8_result["usability_gate"] = usability_gate
    int8_result["required_models"] = list(required)
    int8_result["completed_models"] = [
        tag for tag in required if (results.get(tag) or {}).get("status") == "ok"
    ]
    int8_result["missing_or_failed_models"] = [
        tag for tag in required if (results.get(tag) or {}).get("status") != "ok"
    ]
    return int8_result


def _normalize_bmodel_visualizations(work):
    """将 INT8 画框图缩成轻量预览，避免复制一份全尺寸评估图片。"""
    out = work / "output"
    result_root = out / "results_bmodel"
    images = result_root / "images"
    metadata_path = result_root / "visualization_summary.json"
    if not images.is_dir():
        return None
    preview = out / "bmcv_preview"
    if preview.exists():
        shutil.rmtree(str(preview))
    preview.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
    except ImportError:
        cv2 = None
    for source in sorted(images.iterdir()):
        if not source.is_file() or source.suffix.lower() not in (
            ".jpg",
            ".jpeg",
            ".png",
            ".bmp",
        ):
            continue
        destination = preview / (source.stem + ".jpg")
        image = cv2.imread(str(source)) if cv2 is not None else None
        if image is None:
            continue
        height, width = image.shape[:2]
        max_side = max(height, width)
        if max_side > 960:
            scale = 960.0 / float(max_side)
            image = cv2.resize(
                image,
                (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        cv2.imwrite(str(destination), image, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    preview_files = sorted(
        path.name
        for path in preview.iterdir()
        if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")
    )
    metadata = {}
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            metadata = {}
    return {
        "detection_summary": {
            "image_count": int(metadata.get("image_count") or len(preview_files)),
            "with_boxes": int(metadata.get("with_boxes") or 0),
            "total_boxes": int(metadata.get("total_boxes") or 0),
            "images": metadata.get("images") or [],
            "visualization_summary": str(metadata_path),
        },
        "preview": {
            "viz_image_count": len(preview_files),
            "preview_files": preview_files,
        },
    }


def _specialized_validation_profile(work):
    model_dir = work / "models" / "BM1688"
    if (model_dir / "plate_recognition_contract.json").is_file():
        return "plate_recognition"
    if (model_dir / "plate_detection_contract.json").is_file():
        return "plate_detection"
    return None


def _validation_outcome(work, *, mode, report, bmodel_eval, dependency_setup):
    profile = _specialized_validation_profile(work)
    sophon_skipped = bool(report.get("skipped"))
    generic_passed = (not sophon_skipped) and bool(report.get("passed"))
    bmodel_status = str(bmodel_eval.get("status") or "unknown").lower()
    dependency_ok = dependency_setup.get("status") == "ok"

    specialized_authoritative = bool(profile) and mode != "run_only"
    if specialized_authoritative:
        overall_ok = dependency_ok and bmodel_status == "ok"
        generic_status = "not_applicable"
        generic_reason = "专用车牌模型使用独立输出契约和 BM1688 推理器"
    else:
        overall_ok = dependency_ok and (sophon_skipped or generic_passed)
        if mode != "run_only" and bmodel_status != "ok":
            overall_ok = False
        generic_status = (
            "skipped" if sophon_skipped else ("pass" if generic_passed else "fail")
        )
        generic_reason = None

    checks = {
        "generic_sophon_demo": {
            "status": generic_status,
            "raw_passed": report.get("passed"),
            "reason": generic_reason,
        },
        "specialized_runtime": {
            "status": bmodel_status if specialized_authoritative else "not_applicable",
            "profile": profile,
            "authoritative": specialized_authoritative,
        },
    }
    return {
        "passed": overall_ok,
        "validation_profile": profile or "generic_yolo",
        "board_passed": overall_ok,
        "generic_board_passed": report.get("passed"),
        "board_skipped": sophon_skipped,
        "bmodel_sop_status": bmodel_status,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()
    work = Path(args.work_dir).resolve()
    if not work.is_dir():
        print("work-dir 不存在: {}".format(work), file=sys.stderr)
        return 2

    cfg = _load_run_config(work)
    integrity = _verify_edge_run_config(work, cfg)
    export_work = work
    out = work / "output"
    out.mkdir(parents=True, exist_ok=True)
    dependency_setup = _ensure_edge_runtime_dependencies(work)

    demo_root, tried = _resolve_sophon_demo_root()
    if demo_root is not None:
        ops = _repo_ops_dir(work)
        sys.path.insert(0, str(ops))
        from labelu_board_agent import finalize_board_report, run_validation  # type: ignore
        from labelu_board_agent import package_bmcv_preview_images  # type: ignore

        conf = _cfg_conf_thres(cfg)
        nms = _cfg_nms_thres(cfg)
        match_iou = _cfg_match_iou_thres(cfg)
        report = run_validation(
            demo_root,
            export_work,
            run_coco=bool(cfg.get("run_coco_eval")),
            on_progress=None,
            conf_thresh=conf,
            nms_thresh=nms,
            match_iou_thres=match_iou,
            threshold_unit=_cfg_threshold_unit(cfg),
            threshold_source=_cfg_threshold_source(cfg),
        )
        prev = package_bmcv_preview_images(
            demo_root,
            out / "bmcv_preview",
            max_images=int(
                cfg.get("bmcv_preview_max") or cfg.get("eval_image_count") or 12
            ),
        )
        if report.get("bmcv_infer"):
            report["bmcv_infer"]["preview"] = prev
        report = finalize_board_report(report, export_work)
        print("sophon-demo 板端验证: {}".format(report.get("summary", "")), flush=True)
    else:
        reason = "SOPHON_DEMO_ROOT 无效，已尝试: {}".format(tried)
        print(reason, file=sys.stderr, flush=True)
        report = _skipped_board_report(reason, tried)

    report["class_contract"] = (
        cfg.get("class_contract") if isinstance(cfg.get("class_contract"), dict) else {}
    )
    report["execution_generation_state"] = (
        integrity.get("execution_generation_state")
        if isinstance(integrity, dict)
        else None
    )
    report["artifact_execution_signature"] = (
        cfg.get("artifact_execution_signature")
        or cfg.get("execution_generation")
    )
    report["execution_generation"] = cfg.get("execution_generation")
    if cfg.get("execution_fencing"):
        report["execution_fencing"] = cfg.get("execution_fencing")
    report["profile_digest"] = cfg.get("profile_digest")
    report["bm1688_profile"] = (
        cfg.get("bm1688_profile") if isinstance(cfg.get("bm1688_profile"), dict) else None
    )
    fixed_eval = _fixed_eval_binding(work, cfg)
    report["fixed_eval_dataset"] = {
        "fixed_eval_signature": fixed_eval.get("actual_signature")
        or fixed_eval.get("expected_signature"),
        "image_count": fixed_eval.get("image_count"),
        "binding_status": "pass" if fixed_eval.get("passed") else "fail",
        "binding_reasons": fixed_eval.get("reasons") or [],
    }
    # Keep the immutable threshold snapshot next to the board evidence so the
    # server can audit which rule was actually used, rather than infer it from
    # a current default.
    if isinstance(cfg.get("quality_rule"), dict):
        report["quality_rule"] = cfg.get("quality_rule")

    (out / "board_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if report.get("bmcv_infer"):
        (out / "bmcv_infer_summary.json").write_text(
            json.dumps(report.get("bmcv_infer"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    mode = str(cfg.get("validation_mode") or "run_and_eval").strip().lower()
    bmodel_eval = {"status": "skip", "detail": "validation_mode=run_only"}
    if dependency_setup.get("status") != "ok":
        bmodel_eval = {
            "status": "fail",
            "error": dependency_setup.get("error") or "边端依赖安装失败",
        }
    elif mode != "run_only":
        bmodel_eval = _run_all_bmodel_evaluations(work, cfg, demo_root=demo_root)
        normalized_visualization = _normalize_bmodel_visualizations(work)
        if normalized_visualization:
            (out / "bmcv_infer_summary.json").write_text(
                json.dumps(normalized_visualization, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            report["bmcv_infer"] = normalized_visualization
            (out / "board_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    (out / "bmodel_sop_eval.json").write_text(
        json.dumps(bmodel_eval, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        **_validation_outcome(
            work,
            mode=mode,
            report=report,
            bmodel_eval=bmodel_eval,
            dependency_setup=dependency_setup,
        ),
        "dependency_setup": dependency_setup,
        "summary": report.get("summary"),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
