import io
import stat
import tarfile
import zipfile

import pytest

from labelu.internal.common.archive import (
    UnsafeArchiveMember,
    safe_extract_tar,
    safe_extract_zip,
)


@pytest.mark.parametrize(
    "escape_name", ["../outside.txt", ".. /outside.txt", "models/C:evil.txt"]
)
def test_safe_extract_zip_rejects_traversal_before_writing(tmp_path, escape_name):
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("safe.txt", b"must not be partially written")
        archive.writestr(escape_name, b"escape")

    destination = tmp_path / "work"
    with pytest.raises(UnsafeArchiveMember, match="unsafe archive member path"):
        safe_extract_zip(archive_path, destination)

    assert not (tmp_path / "outside.txt").exists()
    assert not (destination / "safe.txt").exists()


def test_safe_extract_zip_rejects_symlink_member(tmp_path):
    archive_path = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("models/link.bmodel")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(link, "../../outside.bmodel")

    with pytest.raises(UnsafeArchiveMember, match="special member"):
        safe_extract_zip(archive_path, tmp_path / "work")


def test_safe_extract_zip_accepts_normal_package(tmp_path):
    archive_path = tmp_path / "models.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("models/BM1688/model_int8.bmodel", b"bmodel")
        archive.writestr("run_config.json", b"{}")

    destination = tmp_path / "work"
    result = safe_extract_zip(archive_path, destination)
    assert result == destination.resolve()
    assert (destination / "models/BM1688/model_int8.bmodel").read_bytes() == b"bmodel"


def _tar_member(name, payload=b"payload"):
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    return info, io.BytesIO(payload)


def test_safe_extract_tar_rejects_traversal_before_writing(tmp_path):
    archive_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        safe_info, safe_data = _tar_member("safe.txt")
        archive.addfile(safe_info, safe_data)
        outside_info, outside_data = _tar_member("../outside.txt", b"escape")
        archive.addfile(outside_info, outside_data)

    destination = tmp_path / "work"
    with pytest.raises(UnsafeArchiveMember, match="unsafe archive member path"):
        safe_extract_tar(archive_path, destination, mode="r:gz")

    assert not (tmp_path / "outside.txt").exists()
    assert not (destination / "safe.txt").exists()


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_safe_extract_tar_rejects_links(tmp_path, member_type):
    archive_path = tmp_path / "link.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("models/link")
        info.type = member_type
        info.linkname = "../../outside"
        archive.addfile(info)

    with pytest.raises(UnsafeArchiveMember, match="link or special"):
        safe_extract_tar(archive_path, tmp_path / "work", mode="r")


def test_safe_extract_tar_accepts_normal_package(tmp_path):
    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info, data = _tar_member("dataset/data.yaml", b"names: []\n")
        archive.addfile(info, data)

    destination = tmp_path / "work"
    safe_extract_tar(archive_path, destination, mode="r:gz")
    assert (destination / "dataset/data.yaml").read_text(encoding="utf-8") == "names: []\n"
