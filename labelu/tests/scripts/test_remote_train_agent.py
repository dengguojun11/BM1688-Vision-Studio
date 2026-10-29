import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


def _load_agent_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "remote_train" / "agent_worker.py"
    spec = importlib.util.spec_from_file_location("agent_worker", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_normalize_hyperparams_filters_unknown_keys():
    mod = _load_agent_module()
    out = mod.normalize_hyperparams({"batch": 8, "foo": 1, "amp": True})
    assert out == {"batch": 8, "amp": True}


def test_write_phase_contains_operation_generation(tmp_path):
    mod = _load_agent_module()
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()

    mod.write_phase(job_dir, "running", operation_id="worker-a:7")

    phase = json.loads((job_dir / "phase.json").read_text(encoding="utf-8"))
    assert phase["operation_id"] == "worker-a:7"


def test_write_phase_contains_spec_digest(tmp_path):
    mod = _load_agent_module()
    job_dir = tmp_path / "job-2"
    job_dir.mkdir()
    mod._CURRENT_OPERATION_ID = "worker-a:8"
    mod._CURRENT_SPEC_SHA256 = "abc123"

    mod.write_phase(job_dir, "running")

    phase = json.loads((job_dir / "phase.json").read_text(encoding="utf-8"))
    assert phase["operation_id"] == "worker-a:8"
    assert phase["spec_sha256"] == "abc123"


def test_resolve_weights_prefers_job_dir_weights(tmp_path):
    mod = _load_agent_module()
    job_dir = tmp_path / "job-1"
    wdir = job_dir / "weights"
    wdir.mkdir(parents=True)
    (wdir / "best.pt").write_bytes(b"x" * 2048)
    spec = {"model_weights": "yolo11n.pt"}
    p = mod.resolve_weights_path(job_dir, spec)
    assert p.endswith("best.pt")


def test_download_payload_with_resume_writes_progress(tmp_path, monkeypatch):
    mod = _load_agent_module()
    job_dir = tmp_path / "job-118"
    job_dir.mkdir()
    dest = job_dir / "job_payload.tar.gz"
    payload = b"hello-remote-payload"

    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

        def read(self, n=-1):
            if not self._data:
                return b""
            chunk = self._data[:n] if n > 0 else self._data
            self._data = self._data[len(chunk) :]
            return chunk

    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda req, timeout=300: FakeResp(payload),
    )
    monkeypatch.setattr(mod, "_probe_payload_total_bytes", lambda url: len(payload))

    log_path = job_dir / "train.log"
    with log_path.open("w", encoding="utf-8") as log_fp:
        mod.download_payload_with_resume(
            "http://example/payload?token=x",
            dest,
            job_dir,
            log_fp,
            total_hint=len(payload),
        )

    assert dest.read_bytes() == payload
    phase = json.loads((job_dir / "phase.json").read_text(encoding="utf-8"))
    assert phase["phase"] == "downloading"
    assert phase["download_pct"] == 100


def test_payload_tar_rejects_path_traversal(tmp_path):
    mod = _load_agent_module()
    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("../outside.txt")
        payload = b"escape"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    destination = tmp_path / "job-1"
    with pytest.raises(RuntimeError, match="unsafe payload member path"):
        mod._safe_extract_payload_tar(archive_path, destination)
    assert not (tmp_path / "outside.txt").exists()


def test_payload_tar_rejects_symlink(tmp_path):
    mod = _load_agent_module()
    archive_path = tmp_path / "payload.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        archive.addfile(info)

    with pytest.raises(RuntimeError, match="link or special"):
        mod._safe_extract_payload_tar(archive_path, tmp_path / "job-1")
