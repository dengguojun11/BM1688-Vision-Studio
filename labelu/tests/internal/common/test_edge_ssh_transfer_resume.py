from types import SimpleNamespace

import paramiko
import pytest

from labelu.internal.common import edge_ssh_client as ssh_client


class _RemoteFile:
    def __init__(self, sftp, *, fail_write=False):
        self.sftp = sftp
        self.offset = 0
        self.fail_write = fail_write
        self.pipelined = False

    def seek(self, offset):
        self.offset = int(offset)

    def set_pipelined(self, enabled):
        self.pipelined = bool(enabled)

    def write(self, chunk):
        if self.fail_write:
            raise paramiko.SSHException("Server connection dropped")
        end = self.offset + len(chunk)
        if end > len(self.sftp.data):
            self.sftp.data.extend(b"\0" * (end - len(self.sftp.data)))
        self.sftp.data[self.offset : end] = chunk
        self.offset = end

    def flush(self):
        return None

    def close(self):
        return None


class _Sftp:
    def __init__(self, data=b"", *, fail_write=False):
        self.data = bytearray(data)
        self.fail_write = fail_write
        self.open_calls = []

    def stat(self, _path):
        return SimpleNamespace(st_size=len(self.data))

    def open(self, path, mode):
        self.open_calls.append((path, mode))
        if mode == "wb":
            self.data.clear()
        return _RemoteFile(self, fail_write=self.fail_write)

    def close(self):
        return None


class _Client:
    def __init__(self, sftp):
        self.sftp = sftp

    def open_sftp(self):
        return self.sftp


def test_sftp_upload_resumable_continues_from_remote_size(tmp_path, monkeypatch):
    local = tmp_path / "bundle.tar.gz"
    local.write_bytes(b"abcdefghij")
    sftp = _Sftp(b"abcd")
    progress = []
    monkeypatch.setattr(ssh_client, "_sftp_mkdirs", lambda *_args: None)

    uploaded = ssh_client.sftp_upload_resumable(
        _Client(sftp),
        local,
        "/data/job/bundle.tar.gz.part",
        progress_callback=lambda sent, total: progress.append((sent, total)),
        chunk_bytes=3,
    )

    assert uploaded == 10
    assert bytes(sftp.data) == b"abcdefghij"
    assert sftp.open_calls == [("/data/job/bundle.tar.gz.part", "r+b")]
    assert progress[0] == (4, 10)
    assert progress[-1] == (10, 10)


def test_sftp_upload_resumable_reuses_complete_part(tmp_path, monkeypatch):
    local = tmp_path / "bundle.tar.gz"
    local.write_bytes(b"complete")
    sftp = _Sftp(b"complete")
    progress = []
    monkeypatch.setattr(ssh_client, "_sftp_mkdirs", lambda *_args: None)

    uploaded = ssh_client.sftp_upload_resumable(
        _Client(sftp),
        local,
        "/data/job/bundle.tar.gz.part",
        progress_callback=lambda sent, total: progress.append((sent, total)),
    )

    assert uploaded == len(b"complete")
    assert sftp.open_calls == []
    assert progress == [(len(b"complete"), len(b"complete"))]


def test_sftp_upload_resumable_classifies_connection_drop(tmp_path, monkeypatch):
    local = tmp_path / "bundle.tar.gz"
    local.write_bytes(b"abcdefghij")
    sftp = _Sftp(b"abcd", fail_write=True)
    monkeypatch.setattr(ssh_client, "_sftp_mkdirs", lambda *_args: None)

    with pytest.raises(ssh_client.EdgeSshTransferInterrupted) as exc_info:
        ssh_client.sftp_upload_resumable(
            _Client(sftp),
            local,
            "/data/job/bundle.tar.gz.part",
        )

    assert "连接中断" in str(exc_info.value)
