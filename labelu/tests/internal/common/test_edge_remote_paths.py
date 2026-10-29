import pytest

from labelu.internal.common.edge_remote_paths import (
    edge_remote_paths,
    normalize_edge_root,
    require_edge_path,
)


def test_edge_remote_layout_stays_under_single_root():
    paths = edge_remote_paths("/data/labelu_edge/")

    assert paths.root == "/data/labelu_edge"
    assert paths.jobs == "/data/labelu_edge/jobs"
    assert paths.runtime == "/data/labelu_edge/runtime"
    assert paths.sophon_demo_root.startswith(paths.root + "/")
    assert paths.validator.startswith(paths.root + "/")
    assert paths.python_packages.startswith(paths.root + "/")
    assert paths.job("manual-job-42") == "/data/labelu_edge/jobs/manual-job-42"


@pytest.mark.parametrize(
    "path",
    (
        "/data/labelu_edge/../outside",
        "/data/labelu_edge-other/job-1",
        "/tmp/labelu",
        "/usr/local/bin/bmodel-validate",
    ),
)
def test_require_edge_path_rejects_escape(path):
    with pytest.raises(ValueError, match="escapes edge root"):
        require_edge_path(path, root="/data/labelu_edge")


@pytest.mark.parametrize("root", ("/", "/data", "relative/path"))
def test_normalize_edge_root_rejects_unsafe_root(root):
    with pytest.raises(ValueError, match="safe absolute"):
        normalize_edge_root(root)


def test_job_directory_rejects_nested_or_parent_name():
    paths = edge_remote_paths("/data/labelu_edge")
    for name in ("../job", "nested/job", ""):
        with pytest.raises(ValueError, match="invalid edge job"):
            paths.job(name)
