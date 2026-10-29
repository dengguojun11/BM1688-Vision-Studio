from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

import requests

from labelu.internal.common.pre_annotation_sample_repair import (
    convert_uploaded_pre_annotation_to_sample_result,
    count_annotation_items,
    count_out_of_bounds_rects,
    detect_systematic_rect_scaling_issue,
)


@dataclass
class ScanOutcome:
    sample_id: int
    sample_name: str
    updated_at: str | None
    updated_by: int | None
    annotated_count: int | None
    pre_annotation_id: int | None
    before_bad_rects: int
    suspicious: bool
    reasons: list[str]
    current_rect_count: int
    expected_rect_count: int
    shared_rect_count: int
    ids_equal: bool
    scale_x: float
    scale_y: float
    direct_match_ratio: float
    unscaled_match_ratio: float
    patched: bool = False


def login(base_url: str, username: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/users/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]["token"]


def get_sample(base_url: str, token: str, task_id: int, sample_id: int) -> dict[str, Any]:
    resp = requests.get(
        f"{base_url}/api/v1/tasks/{task_id}/samples/{sample_id}",
        headers={"Authorization": token},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def get_uploaded_pre_annotation(
    base_url: str,
    token: str,
    task_id: int,
    sample_name: str,
) -> dict[str, Any] | None:
    resp = requests.get(
        f"{base_url}/api/v1/tasks/{task_id}/pre_annotations",
        headers={"Authorization": token},
        params={"page": 0, "sample_name": sample_name},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]
    for row in rows:
        if row.get("file"):
            return row
    return None


def patch_sample(
    base_url: str,
    token: str,
    task_id: int,
    sample_id: int,
    result_payload: dict[str, Any],
    annotated_count: int,
) -> dict[str, Any]:
    resp = requests.patch(
        f"{base_url}/api/v1/tasks/{task_id}/samples/{sample_id}",
        headers={"Authorization": token},
        json={
            "data": {"result": json.dumps(result_payload, ensure_ascii=False)},
            "annotated_count": annotated_count,
            "state": "DONE" if annotated_count > 0 else "NEW",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def analyze_one(
    base_url: str,
    token: str,
    task_id: int,
    sample_id: int,
) -> tuple[ScanOutcome, dict[str, Any]]:
    sample = get_sample(base_url, token, task_id, sample_id)
    sample_name = sample["data"]["file"]["filename"]
    current_result = sample["data"].get("result")
    before_bad = count_out_of_bounds_rects(current_result)

    pre_annotation = get_uploaded_pre_annotation(base_url, token, task_id, sample_name)
    if not pre_annotation:
        raise RuntimeError(f"sample {sample_id} missing uploaded pre-annotation")

    expected_result = convert_uploaded_pre_annotation_to_sample_result(pre_annotation["data"])
    detection = detect_systematic_rect_scaling_issue(current_result, expected_result)
    outcome = ScanOutcome(
        sample_id=sample_id,
        sample_name=sample_name,
        updated_at=sample.get("updated_at"),
        updated_by=(sample.get("updaters") or [{}])[-1].get("id") if sample.get("updaters") else None,
        annotated_count=sample.get("annotated_count"),
        pre_annotation_id=pre_annotation.get("id"),
        before_bad_rects=before_bad,
        suspicious=bool(detection["suspicious"]),
        reasons=list(detection["reasons"]),
        current_rect_count=int(detection["current_rect_count"]),
        expected_rect_count=int(detection["expected_rect_count"]),
        shared_rect_count=int(detection["shared_rect_count"]),
        ids_equal=bool(detection["ids_equal"]),
        scale_x=float(detection["scale_x"]),
        scale_y=float(detection["scale_y"]),
        direct_match_ratio=float(detection["direct_match_ratio"]),
        unscaled_match_ratio=float(detection["unscaled_match_ratio"]),
    )
    return outcome, expected_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan and optionally repair scaled samples from uploaded pre-annotations")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--task-id", required=True, type=int)
    parser.add_argument("--sample-id", dest="sample_ids", action="append", type=int, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually patch suspicious samples")
    args = parser.parse_args()

    token = login(args.base_url.rstrip("/"), args.username, args.password)
    outcomes: list[ScanOutcome] = []

    for sample_id in args.sample_ids:
        outcome, expected_result = analyze_one(
            base_url=args.base_url.rstrip("/"),
            token=token,
            task_id=args.task_id,
            sample_id=sample_id,
        )
        if args.apply and outcome.suspicious:
            patched_sample = patch_sample(
                base_url=args.base_url.rstrip("/"),
                token=token,
                task_id=args.task_id,
                sample_id=sample_id,
                result_payload=expected_result,
                annotated_count=count_annotation_items(expected_result),
            )
            outcome.updated_at = patched_sample.get("updated_at")
            outcome.patched = True
        outcomes.append(outcome)

    print(
        json.dumps(
            [
                {
                    "sample_id": item.sample_id,
                    "sample_name": item.sample_name,
                    "updated_at": item.updated_at,
                    "updated_by": item.updated_by,
                    "annotated_count": item.annotated_count,
                    "pre_annotation_id": item.pre_annotation_id,
                    "before_bad_rects": item.before_bad_rects,
                    "suspicious": item.suspicious,
                    "reasons": item.reasons,
                    "current_rect_count": item.current_rect_count,
                    "expected_rect_count": item.expected_rect_count,
                    "shared_rect_count": item.shared_rect_count,
                    "ids_equal": item.ids_equal,
                    "scale_x": item.scale_x,
                    "scale_y": item.scale_y,
                    "direct_match_ratio": item.direct_match_ratio,
                    "unscaled_match_ratio": item.unscaled_match_ratio,
                    "patched": item.patched,
                }
                for item in outcomes
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
