#!/usr/bin/env python3
"""Build the BM1688 offline environment package."""

import argparse
import hashlib
import io
import tarfile
from pathlib import Path


def _add_file(archive, source, arcname, mode=None):
    if not source.is_file():
        raise RuntimeError("Required package file is missing: {}".format(source))

    def apply_mode(info):
        if mode is not None:
            info.mode = mode
        return info

    archive.add(str(source), arcname=arcname, filter=apply_mode)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_bytes(archive, data, arcname):
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def build_package(docker_image, sophon_demo, output):
    source_dir = Path(__file__).resolve().parent
    repo_root = source_dir.parents[1]
    validator_dir = repo_root / "scripts" / "edge_bmodel_validator"
    plate_runner = (
        repo_root / "labelu" / "scripts" / "plate_recognition_bm1688_infer.py"
    )
    plate_detection_runner = (
        repo_root / "labelu" / "scripts" / "plate_detection_bm1688_infer.py"
    )
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise RuntimeError("Refusing to overwrite existing package: {}".format(output))
    output.parent.mkdir(parents=True, exist_ok=True)

    docker_image = Path(docker_image).expanduser().resolve()
    sophon_demo = Path(sophon_demo).expanduser().resolve()
    root = "bm1688_edge_runtime"
    with tarfile.open(str(output), "w") as archive:
        for name in (
            "README.md",
            "runtime_manifest.json",
            "check_env.sh",
            "setup.sh",
            "bmodel-validate",
        ):
            mode = (
                0o755
                if name in {"check_env.sh", "setup.sh", "bmodel-validate"}
                else 0o644
            )
            _add_file(
                archive,
                source_dir / name,
                "{}/{}".format(root, name),
                mode=mode,
            )
        _add_file(
            archive,
            validator_dir / "validate.py",
            "{}/app/validate.py".format(root),
        )
        _add_file(
            archive,
            validator_dir / "config.json",
            "{}/app/config.json".format(root),
        )
        _add_file(
            archive,
            plate_runner,
            "{}/app/plate_recognition_bm1688_infer.py".format(root),
        )
        _add_file(
            archive,
            plate_detection_runner,
            "{}/app/plate_detection_bm1688_infer.py".format(root),
        )
        _add_file(
            archive,
            docker_image,
            "{}/payload/bm1688-7g-runtime-image.tar.gz".format(root),
        )
        _add_file(
            archive,
            sophon_demo,
            "{}/payload/sophon-demo-yolov8-plus-det.tar.gz".format(root),
        )
        checksums = (
            "{}  bm1688-7g-runtime-image.tar.gz\n"
            "{}  sophon-demo-yolov8-plus-det.tar.gz\n"
        ).format(_sha256(docker_image), _sha256(sophon_demo))
        _add_bytes(
            archive,
            checksums.encode("ascii"),
            "{}/payload/SHA256SUMS".format(root),
        )
    return output


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker-image", required=True)
    parser.add_argument("--sophon-demo", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(build_package(args.docker_image, args.sophon_demo, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
