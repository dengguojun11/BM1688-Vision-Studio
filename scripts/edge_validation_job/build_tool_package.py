#!/usr/bin/env python3
"""Package the reusable Windows-side validation job builder."""

import argparse
import tarfile
from pathlib import Path


def build_tool_package(output):
    source_dir = Path(__file__).resolve().parent
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise RuntimeError("Refusing to overwrite existing package: {}".format(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    root = "bmodel_validation_job_builder"
    with tarfile.open(str(output), "w:gz") as archive:
        for name in ("README.md", "build_job.py"):
            archive.add(str(source_dir / name), arcname="{}/{}".format(root, name))
    return output


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(build_tool_package(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
