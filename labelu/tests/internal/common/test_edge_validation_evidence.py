from labelu.internal.common.edge_validation_evidence import (
    EDGE_EVIDENCE_CONTRACT_VERSION,
    edge_validation_evidence_ready,
    evaluate_edge_validation_evidence,
)
from labelu.internal.common.bm1688_compile_contract import profile_snapshot_digest
from labelu.internal.common.bm1688_quality_rules import get_bm1688_quality_rule


def _complete_report():
    return {
        "status": "ok",
        "execution_device": {"device_id": "bm1688-a", "host": "192.168.54.99", "serial": "BM1688-A"},
        "model_identity": {
            "model_id": "export-42",
            "export_id": 42,
            "aicm_model_id": "534",
        },
        "artifact_execution_signature": "artifact-signature-42",
        "dataset_evidence": {
            "fixed_eval_signature": "eval-signature-42",
            "image_count": 20,
        },
        "metrics": {
            "infer_runs": [
                {"precision": "fp32", "bmodel": "model_fp32_1b.bmodel", "ok": True, "input_image_count": 20, "map50": 0.91, "latency_p95_ms": 19.2, "error_rate": 0.0},
                {"precision": "fp16", "bmodel": "model_fp16_1b.bmodel", "ok": True, "input_image_count": 20, "map50": 0.90, "latency_p95_ms": 12.2, "error_rate": 0.0},
                {"precision": "int8", "bmodel": "model_int8_1b.bmodel", "ok": True, "input_image_count": 20, "map50": 0.89, "latency_p95_ms": 8.2, "error_rate": 0.0},
            ],
        },
        "stability": {"status": "pass", "enabled": True, "duration_seconds": 60},
        "execution_fencing": {"token": 7, "status": "matched"},
    }


def test_complete_bm1688_evidence_passes_without_inventing_aicm_id():
    result = evaluate_edge_validation_evidence(
        _complete_report(),
        expected={
            "execution_signature": "artifact-signature-42",
            "fixed_eval_signature": "eval-signature-42",
            "eval_image_count": 20,
        },
    )

    assert result["contract_version"] == EDGE_EVIDENCE_CONTRACT_VERSION
    assert result["status"] == "pass"
    assert result["publishable"] is True
    assert result["available_precisions"] == ["fp16", "fp32", "int8"]
    assert result["model_identity"]["aicm_model_id"] == "534"


def test_missing_device_and_runtime_evidence_is_pending():
    report = _complete_report()
    report.pop("execution_device")
    report["metrics"]["infer_runs"][0].pop("latency_p95_ms")
    report["metrics"]["infer_runs"][1].pop("latency_p95_ms")
    report["metrics"]["infer_runs"][2].pop("latency_p95_ms")
    report.pop("stability")
    report.pop("execution_fencing")

    result = evaluate_edge_validation_evidence(report)

    assert result["status"] == "pending"
    assert result["publishable"] is False
    assert {
        "device_identity_missing",
        "p95_latency_missing",
        "short_stability_missing",
        "execution_fencing_missing",
    } <= set(result["reason_codes"])
    assert edge_validation_evidence_ready(report) is False


def test_failed_execution_and_signature_mismatch_fail_closed():
    report = _complete_report()
    report["status"] = "FAILED"

    result = evaluate_edge_validation_evidence(
        report,
        expected={"execution_signature": "another-signature"},
    )

    assert result["status"] == "fail"
    assert "edge_execution_failed" in result["reason_codes"]
    assert "artifact_signature_mismatch" in result["reason_codes"]


def test_aicm_id_is_required_only_when_release_gate_declares_it():
    report = _complete_report()
    report["model_identity"].pop("aicm_model_id")

    assert evaluate_edge_validation_evidence(report)["status"] == "pass"
    gated = evaluate_edge_validation_evidence(report, require_aicm_id=True)
    assert gated["status"] == "pending"
    assert "aicm_model_id_missing" in gated["reason_codes"]


def test_profile_digest_is_required_and_must_match_when_release_declares_it():
    report = _complete_report()
    profile = {"profile_id": "bm1688-yolov11-detection-v1", "input_size": [640, 640]}
    digest = profile_snapshot_digest(profile)
    report["profile_digest"] = digest

    passed = evaluate_edge_validation_evidence(
        report, expected={"profile_digest": digest}
    )
    assert passed["status"] == "pass"
    assert passed["profile_digest"] == digest

    missing = evaluate_edge_validation_evidence(
        {**report, "profile_digest": None}, expected={"profile_digest": digest}
    )
    assert missing["status"] == "pending"
    assert "profile_digest_missing" in missing["reason_codes"]

    mismatch = evaluate_edge_validation_evidence(
        {**report, "profile_digest": "other"}, expected={"profile_digest": digest}
    )
    assert mismatch["status"] == "fail"
    assert "profile_digest_mismatch" in mismatch["reason_codes"]


def test_execution_generation_state_is_required_for_modern_signed_artifact():
    report = _complete_report()
    expected = {
        "execution_generation": "artifact-signature-42",
        "require_execution_generation_state": True,
    }

    missing = evaluate_edge_validation_evidence(report, expected=expected)
    assert missing["status"] == "pending"
    assert "execution_generation_state_missing" in missing["reason_codes"]

    report["execution_generation_state"] = {
        "status": "accepted_new",
        "execution_generation": "artifact-signature-42",
    }
    passed = evaluate_edge_validation_evidence(report, expected=expected)
    assert passed["status"] == "pass"
    assert passed["publishable"] is True


def test_execution_generation_state_rejects_old_or_conflicting_report():
    report = _complete_report()
    report["execution_generation_state"] = {
        "status": "rejected",
        "execution_generation": "artifact-signature-42",
    }
    expected = {
        "execution_generation": "artifact-signature-42",
        "require_execution_generation_state": True,
    }
    rejected = evaluate_edge_validation_evidence(report, expected=expected)
    assert rejected["status"] == "fail"
    assert "execution_generation_not_consumed" in rejected["reason_codes"]

    report["execution_generation_state"] = {
        "status": "idempotent",
        "execution_generation": "old-generation",
    }
    mismatch = evaluate_edge_validation_evidence(report, expected=expected)
    assert mismatch["status"] == "fail"
    assert "execution_generation_mismatch" in mismatch["reason_codes"]


def test_precision_runs_must_share_fixed_eval_signature_and_threshold_snapshot():
    report = _complete_report()
    rule = get_bm1688_quality_rule()
    for row in report["metrics"]["infer_runs"]:
        row["fixed_eval_signature"] = "eval-signature-42"
        row["metrics"] = {"map50": row["map50"]}

    result = evaluate_edge_validation_evidence(
        report,
        expected={
            "fixed_eval_signature": "eval-signature-42",
            "eval_image_count": 20,
            "quality_rule": rule,
        },
    )

    assert result["status"] == "pass"
    assert result["quality_rule"]["validated"] is True

    report["metrics"]["infer_runs"][2]["fixed_eval_signature"] = "other-pack"
    mismatch = evaluate_edge_validation_evidence(
        report,
        expected={"fixed_eval_signature": "eval-signature-42", "quality_rule": rule},
    )
    assert mismatch["status"] == "fail"
    assert "fixed_eval_precision_signature_mismatch" in mismatch["reason_codes"]
