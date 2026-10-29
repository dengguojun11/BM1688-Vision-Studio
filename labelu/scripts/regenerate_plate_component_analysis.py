from __future__ import annotations

import argparse
import json

from labelu.internal.application.service.plate_recognition_train import (
    regenerate_plate_component_analysis,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate Pose/OCR/color/type analysis assets for a completed plate job."
    )
    parser.add_argument("job_id", type=int)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    result = regenerate_plate_component_analysis(args.job_id, device=args.device)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
