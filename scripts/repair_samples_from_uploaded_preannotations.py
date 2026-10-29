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
)


@dataclass
class RepairOutcome:
    sample_id: int
    sample_name: str
    before_bad_rects: int
    after_bad_rects: int
    patched: bool
    pre_annotation_id: int | None
    updated_at: str | None


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
        params={"page": 0, "sample_name": sample_name, "canvas_only": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]
    if not rows:
        return None
    row = rows[0]
    if not row.get("file"):
        return None
    return row


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


def repair_one(
    base_url: str,
    token: str,
    task_id: int,
    sample_id: int,
    apply_fix: bool,
) -> RepairOutcome:
    sample = get_sample(base_url, token, task_id, sample_id)
    sample_name = sample["data"]["file"]["filename"]
    current_result = sample["data"].get("result")
    before_bad = count_out_of_bounds_rects(current_result)

    pre_annotation = get_uploaded_pre_annotation(base_url, token, task_id, sample_name)
    if not pre_annotation:
        raise RuntimeError(f"sample {sample_id} missing uploaded pre-annotation")

    restored_result = convert_uploaded_pre_annotation_to_sample_result(pre_annotation["data"])
    after_bad = count_out_of_bounds_rects(restored_result)
    annotated_count = count_annotation_items(restored_result)

    updated_at = sample.get("updated_at")
    patched = False
    if apply_fix:
        patched_sample = patch_sample(
            base_url=base_url,
            token=token,
            task_id=task_id,
            sample_id=sample_id,
            result_payload=restored_result,
            annotated_count=annotated_count,
        )
        updated_at = patched_sample.get("updated_at")
        patched = True

    return RepairOutcome(
        sample_id=sample_id,
        sample_name=sample_name,
        before_bad_rects=before_bad,
        after_bad_rects=after_bad,
        patched=patched,
        pre_annotation_id=pre_annotation.get("id"),
        updated_at=updated_at,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair samples from uploaded pre-annotations")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--task-id", required=True, type=int)
    parser.add_argument("--sample-id", dest="sample_ids", action="append", type=int, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually patch samples")
    args = parser.parse_args()

    token = login(args.base_url.rstrip("/"), args.username, args.password)

    outcomes = [
        repair_one(
            base_url=args.base_url.rstrip("/"),
            token=token,
            task_id=args.task_id,
            sample_id=sample_id,
            apply_fix=args.apply,
        )
        for sample_id in args.sample_ids
    ]

    print(
        json.dumps(
            [
                {
                    "sample_id": item.sample_id,
                    "sample_name": item.sample_name,
                    "before_bad_rects": item.before_bad_rects,
                    "after_bad_rects": item.after_bad_rects,
                    "patched": item.patched,
                    "pre_annotation_id": item.pre_annotation_id,
                    "updated_at": item.updated_at,
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
