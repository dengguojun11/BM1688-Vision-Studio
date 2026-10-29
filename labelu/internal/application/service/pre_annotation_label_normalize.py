import copy
import json
from typing import Any, Dict, List, Union


def lowercase_first_letter(label: str) -> str:
    if len(label) >= 1 and label[0].isupper():
        return label[0].lower() + label[1:]
    return label


def _normalize_rect_boxes_in_annotation_list(annotations: List[Any]) -> None:
    for ann in annotations:
        if not isinstance(ann, dict) or ann.get("toolName") != "rectTool":
            continue
        for box in ann.get("result") or []:
            if isinstance(box, dict) and isinstance(box.get("label"), str) and box["label"]:
                box["label"] = lowercase_first_letter(box["label"])


def _normalize_rect_labels_in_result_dict(result: Dict[str, Any]) -> None:
    annotations = result.get("annotations")
    if isinstance(annotations, list):
        _normalize_rect_boxes_in_annotation_list(annotations)


def normalize_pre_annotation_payload_labels(payload: Union[dict, Any]) -> Union[dict, Any]:
    """将预标注 rect 的 label 首字母改为小写，与任务预设 value 一致。"""
    if not isinstance(payload, dict):
        return payload

    payload = copy.deepcopy(payload)
    result = payload.get("result")

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return payload
        payload["result"] = result

    if isinstance(result, dict):
        _normalize_rect_labels_in_result_dict(result)

    top_annotations = payload.get("annotations")
    if isinstance(top_annotations, list):
        _normalize_rect_boxes_in_annotation_list(top_annotations)

    return payload
