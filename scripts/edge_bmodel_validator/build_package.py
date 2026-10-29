#!/usr/bin/env python3
"""Build the standalone edge validator archive from canonical sources."""

import argparse
import tarfile
from pathlib import Path

PLATE_RUNNER = "plate_recognition_bm1688_infer.py"
PLATE_DETECTION_RUNNER = "plate_detection_bm1688_infer.py"
BUILD_SCRIPT = "build_package.py"


def _should_include(relative_path):
    parts = relative_path.parts
    if "__pycache__" in parts or relative_path.suffix == ".pyc":
        return False
    if parts and parts[0] == "output":
        return False
    if parts and parts[0] == "model" and relative_path.name != "README.txt":
        return False
    if parts and parts[0] == "images" and relative_path.name != "README.txt":
        return False
    if relative_path.name in {PLATE_RUNNER, PLATE_DETECTION_RUNNER, BUILD_SCRIPT}:
        return False
    return True


def build_archive(output_path):
    bundle_dir = Path(__file__).resolve().parent
    repo_root = bundle_dir.parents[1]
    plate_runner = repo_root / "labelu" / "scripts" / PLATE_RUNNER
    if not plate_runner.is_file():
        raise RuntimeError("Canonical plate runner is missing: {}".format(plate_runner))
    detection_runner = repo_root / "labelu" / "scripts" / PLATE_DETECTION_RUNNER
    if not detection_runner.is_file():
        raise RuntimeError(
            "Canonical plate detection runner is missing: {}".format(detection_runner)
        )

    output_path = output_path.expanduser().resolve()
    if output_path.exists():
        raise RuntimeError(
            "Refusing to overwrite existing archive: {}".format(output_path)
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    archive_root = "edge_bmodel_validator"
    with tarfile.open(str(output_path), "w:gz") as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(bundle_dir)
            if not _should_include(relative_path):
                continue
            archive.add(
                str(path),
                arcname="{}/{}".format(archive_root, relative_path.as_posix()),
            )
        archive.add(
            str(plate_runner),
            arcname="{}/{}".format(archive_root, PLATE_RUNNER),
        )
        archive.add(
            str(detection_runner),
            arcname="{}/{}".format(archive_root, PLATE_DETECTION_RUNNER),
        )
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    archive = build_archive(Path(args.output))
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
