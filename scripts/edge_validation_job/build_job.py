#!/usr/bin/env python3
"""Build a data-only BModel validation job archive."""

import argparse
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def _load_contract(path, model_name):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Invalid plate contract: {}".format(exc))
    contract_format = str(payload.get("format") or "").strip()
    if contract_format == "labelu_plate_detection_bm1688_v1":
        model_type = "plate_detection"
        declared = str(payload.get("bmodel") or "").strip()
        if payload.get("job_type") != model_type:
            raise RuntimeError("Plate-detection contract has an invalid job_type")
        network = payload.get("network")
        if not isinstance(network, dict) or network.get("name") != "plate_detection":
            raise RuntimeError("Plate-detection contract has no valid network")
    elif contract_format == "labelu_plate_recognition_bm1688_v1":
        model_type = "plate_recognition"
        declared = str(payload.get("combined_bmodel") or "").strip()
        if payload.get("job_type") != model_type:
            raise RuntimeError("Plate-recognition contract has an invalid job_type")
    else:
        raise RuntimeError("Unsupported plate contract format")
    if declared and declared != model_name:
        raise RuntimeError(
            "Contract expects {}, but the selected model is {}".format(
                declared, model_name
            )
        )
    return payload, model_type


def _add_json(archive, arcname, payload):
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    archive.addfile(info, io.BytesIO(data))


def build_job(model, images_dir, output, job_id, classes=None, contract=None):
    model = Path(model).expanduser().resolve()
    images_dir = Path(images_dir).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if not model.is_file() or model.suffix.lower() != ".bmodel":
        raise RuntimeError("Model must be an existing .bmodel file: {}".format(model))
    if not images_dir.is_dir():
        raise RuntimeError("Image directory does not exist: {}".format(images_dir))
    images = sorted(
        path
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise RuntimeError("No supported images found in {}".format(images_dir))
    if output.exists():
        raise RuntimeError("Refusing to overwrite existing package: {}".format(output))

    classes_path = Path(classes).expanduser().resolve() if classes else None
    contract_path = Path(contract).expanduser().resolve() if contract else None
    if classes_path and not classes_path.is_file():
        raise RuntimeError("Classes file does not exist: {}".format(classes_path))
    if contract_path and not contract_path.is_file():
        raise RuntimeError("Contract file does not exist: {}".format(contract_path))
    if classes_path and contract_path:
        raise RuntimeError(
            "Use classes for YOLO or contract for a plate model, not both"
        )

    model_type = "yolo_detection"
    if contract_path:
        _contract_payload, model_type = _load_contract(contract_path, model.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "labelu_bmodel_validation_job_v1",
        "job_id": str(job_id).strip(),
        "model_type": model_type,
        "model_filename": model.name,
        "image_count": len(images),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    with tarfile.open(str(output), "w:gz") as archive:
        _add_json(archive, "validation_job/manifest.json", manifest)
        archive.add(str(model), arcname="validation_job/model/{}".format(model.name))
        if classes_path:
            archive.add(str(classes_path), arcname="validation_job/model/classes.txt")
        if contract_path:
            contract_name = (
                "plate_detection_contract.json"
                if model_type == "plate_detection"
                else "plate_recognition_contract.json"
            )
            archive.add(
                str(contract_path),
                arcname="validation_job/model/{}".format(contract_name),
            )
        for image in images:
            archive.add(
                str(image), arcname="validation_job/images/{}".format(image.name)
            )
    return output


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--images", required=True)
    parser.add_argument("--classes")
    parser.add_argument("--contract")
    parser.add_argument("--job-id", default="manual-validation")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(
        build_job(
            args.model,
            args.images,
            args.output,
            args.job_id,
            classes=args.classes,
            contract=args.contract,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
